#!/usr/bin/env python3
# Stanok (Yongming SBY-850x6G / Delta DVP32ES2) real vaqt monitori.
# Asosan O'QIYDI. Yagona yozish: PIN bilan himoyalangan, CONTROLS ro'yxatidagi panel tugmalari
# (LIGHT, FAN yoqish/o'chirish; bypass'ni faqat O'CHIRISH). Boshqa hech qayerga yozmaydi.
# Hech qanday tashqi kutubxona kerak emas (faqat Python stdlib).
# Ishga tushirish:  python3 stanok_web.py
# Ko'rish:  shu kompyuterda  http://localhost:8080
#          boshqa qurilmadan (bir tarmoqda)  http://192.168.0.253:8080
#
# PLK'ni fonda bitta oqim o'qiydi (POLL_SEC), brauzerlar faqat keshlangan ma'lumotni oladi.
# Har daqiqa ~/stanok_log/YYYY-MM-DD.csv ga yoziladi, to'xtash/ishga tushish voqealari
# ~/stanok_log/events.csv ga yoziladi.

import socket, struct, json, time, threading, os, csv, hmac, base64
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PLC_HOST, PLC_PORT, STATION = "192.168.0.7", 8234, 1
WEB_PORT = int(os.environ.get("STANOK_PORT", "8080"))
# Tashqi (internet) kirish: faqat 127.0.0.1 da tinglaydi, Cloudflare tunnel shu portga ulanadi.
# Bu port orqali: login-parol SHART, boshqaruv (to'xtatish/tezlik/reset/LIGHT/FAN) TAQIQLANGAN.
REMOTE_PORT = int(os.environ.get("STANOK_REMOTE_PORT", "0"))
AUTH_FILE = os.path.expanduser(os.environ.get("STANOK_AUTH_FILE", "~/stanok/web_auth"))  # "login:parol"
AUTH_MAX_FAILS, AUTH_BLOCK_SEC = 10, 900
BASE = 0x1000          # Delta: D(n) -> Modbus 0x1000+n
POLL_SEC = 3
SPEED_WINDOW = 180     # m/daq shu soniyalar oralig'ida hisoblanadi (D528 sekin yangilanadi)
STOP_AFTER = 150       # chastota o'qilmasa: shuncha soniya uzunlik o'smasa -> "To'xtagan"
RUN_FREQ = 100         # D89..D99 o'rtachasi (Hz x100) shundan katta -> "Ishlayapti"
LOG_DIR = os.path.expanduser("~/stanok_log")
# DR134 javoblarni barcha mijozlarga tarqatadi, boshqa kompyuterdagi monitor esa 50 talik
# bloklar so'raydi. Javob faqat bayt soni bo'yicha ajratiladi, shuning uchun har blok
# boshqalar ishlatmaydigan, o'zaro farqli uzunlikda.
BLOCKS = [(0, 43), (43, 61), (104, 53), (157, 57), (214, 37), (480, 31), (511, 39), (2000, 29)]

# ---------- Boshqaruv (PIN bilan) ----------
# PIN kodda saqlanmaydi: STANOK_PIN muhit o'zgaruvchisi yoki ~/stanok/control_pin fayli.
# Ikkalasi ham bo'lmasa, boshqaruv butunlay o'chiq (har qanday PIN rad etiladi).
def _load_pin():
    pin = os.environ.get("STANOK_PIN", "").strip()
    if not pin:
        try:
            pin = open(os.path.expanduser("~/stanok/control_pin")).read().strip()
        except OSError:
            pin = ""
    return pin
CONTROL_PIN = _load_pin()
# nom -> (M bit, ruxsat etilgan qiymatlar, tavsif). Bitlar joyida test bilan tasdiqlangan.
CONTROLS = {
    "light":   (531, (0, 1), "LIGHT [5]"),
    "fan":     (16,  (0, 1), "FAN [6]"),
    # Bypass'ni masofadan faqat O'CHIRISH (himoyani qayta yoqish) mumkin, yoqish emas.
    "warp":    (518, (0,),   "WARP STOP [7] bypass"),
    "weftbre": (519, (0,),   "WEFT BRE STOP [8] bypass"),
    "weftend": (520, (0,),   "WEFT END STOP [9] bypass"),
}
# Tezlik (Hz). Joyida sinov bilan aniqlangan (2026-11-14): D481 (Hz x100) haqiqiy sozlama,
# PLK uni D86 ga, u orqali rampa bilan D88 (dvigatel buyrug'i) ga beradi. D41, D222 (Hz x100)
# va D480 (butun Hz) panel ekrani nusxalari -- panel kabi ularni ham yangilaymiz.
SPEED_REG = 481
SPEED_COPIES = (41, 222)   # x100
SPEED_DISPLAY = 480        # butun Hz
SPEED_MIN, SPEED_MAX = 5, 40
# Masofadan faqat TO'XTATISH (yoqish ataylab yo'q -- yoqish faqat stanokdagi tugma bilan).
# M1 = ishlash belgisi; unga 0 yozish stanokni to'xtatadi (joyida sinalgan 2026-11-14 18:07:
# Y3/Y4 o'chdi, D88 rampa bilan 0 ga tushdi).
STOP_BIT = 1
# Smenani tiklash. Panel (menyu 3 -> tugma 6) tiklaganda D516 (metr), D514 (impulslar) va D80
# (D516 nusxasi) 0 bo'ladi, alohida PLK biti topilmadi (M1127, M101 sinab ko'rildi -- emas).
# Shuning uchun panel natijasini takrorlaymiz: shu registrlarga 0 yozamiz (impulslar birinchi).
# Panel resetida (21:44:06 kuzatuv) bir vaqtda 0 bo'lganlar: D514, D516, D2000..D2022 (juft).
# D80 ni PLK D516 dan o'zi ko'chiradi -- unga yozmaymiz.
RESET_REGS = (514, 516) + tuple(range(2000, 2023, 2))
RESET_BIT = RESET_REGS
MAX_PIN_FAILS, PIN_BLOCK_SEC = 5, 300
pin_fails = {}             # ip -> (soni, oxirgi vaqt)
plc_lock = threading.Lock()  # RS485 shinasida bir vaqtda bitta so'rov

# ---------- Modbus (ASCII) o'qish ----------
def lrc(b): return (-sum(b)) & 0xFF

def read_block(d_start, count, fn=0x03, base=BASE):
    # DR134 PLK javobini barcha ulangan mijozlarga tarqatadi, shuning uchun
    # faqat bizning so'rovga mos (stansiya, funksiya, bayt soni) javobni olamiz.
    need = count * 2 if fn == 0x03 else (count + 7) // 8
    req = bytes([STATION, fn]) + struct.pack(">HH", base + d_start, count)
    fr = b":" + req.hex().upper().encode() + f"{lrc(req):02X}".encode() + b"\r\n"
    with plc_lock:
        return _txn_read(fr, d_start, count, fn, need)

def _txn_read(fr, d_start, count, fn, need):
    s = socket.create_connection((PLC_HOST, PLC_PORT), timeout=3)
    try:
        s.sendall(fr)
        buf, deadline = b"", time.time() + 2.0
        while time.time() < deadline:
            s.settimeout(max(0.05, deadline - time.time()))
            try:
                c = s.recv(512)
            except socket.timeout:
                break
            if not c: break
            buf += c
            while b"\r\n" in buf:
                line, buf = buf.split(b"\r\n", 1)
                if b":" not in line: continue
                try:
                    raw = bytes.fromhex(line[line.rfind(b":") + 1:].decode())
                except ValueError:
                    continue
                if len(raw) < 4 or lrc(raw[:-1]) != raw[-1] or raw[0] != STATION:
                    continue
                if raw[1] == fn | 0x80:
                    raise RuntimeError(f"Modbus exception {raw[2]}")
                if raw[1] == fn and raw[2] == need:
                    data = raw[3:3 + need]
                    if fn != 0x03:
                        return {d_start + i: (data[i // 8] >> (i % 8)) & 1 for i in range(count)}
                    return {d_start + i: struct.unpack(">H", data[2*i:2*i+2])[0] for i in range(count)}
    finally:
        s.close()
    raise TimeoutError(f"D{d_start}: javob yo'q")

def read_all():
    regs, errors = {}, 0
    for start, cnt in BLOCKS:
        for _ in range(2):
            try:
                regs.update(read_block(start, cnt)); break
            except Exception:
                time.sleep(0.2)
        else:
            errors += 1
    if errors == len(BLOCKS):
        raise ConnectionError("PLK javob bermayapti")
    # D480 = D41/100 (panel "Hz"). Mos kelmasa, javoblardan biri adashgan -> bu o'qish tashlanadi.
    # D41 ba'zan 0 bo'ladi (panel holatiga qarab) -- faqat noldan farqli bo'lsa solishtiramiz
    if regs.get(41) and 480 in regs and regs[41] // 100 != regs[480]:
        raise ValueError(f"nomuvofiq o'qish (D41={regs[41]}, D480={regs[480]})")
    return regs

def write_coil(m, value):
    # FC05 bitta M bitni yozadi. Javob so'rovning aynan nusxasi bo'lishi kerak.
    req = bytes([STATION, 0x05]) + struct.pack(">HH", 0x0800 + m, 0xFF00 if value else 0x0000)
    fr = b":" + req.hex().upper().encode() + f"{lrc(req):02X}".encode() + b"\r\n"
    with plc_lock:
        s = socket.create_connection((PLC_HOST, PLC_PORT), timeout=3)
        try:
            s.sendall(fr)
            buf, deadline = b"", time.time() + 2.0
            while time.time() < deadline:
                s.settimeout(max(0.05, deadline - time.time()))
                try:
                    c = s.recv(512)
                except socket.timeout:
                    break
                if not c: break
                buf += c
                while b"\r\n" in buf:
                    line, buf = buf.split(b"\r\n", 1)
                    try:
                        raw = bytes.fromhex(line[line.rfind(b":") + 1:].decode())
                    except ValueError:
                        continue
                    if len(raw) >= 4 and lrc(raw[:-1]) == raw[-1]:
                        if raw[:-1] == req:
                            return True
                        if raw[0] == STATION and raw[1] == 0x85:
                            raise RuntimeError(f"Modbus exception {raw[2]}")
        finally:
            s.close()
    return False

def write_register(d, value):
    # FC06 bitta D registrni yozadi. Javob so'rovning aynan nusxasi bo'lishi kerak.
    req = bytes([STATION, 0x06]) + struct.pack(">HH", BASE + d, value)
    fr = b":" + req.hex().upper().encode() + f"{lrc(req):02X}".encode() + b"\r\n"
    with plc_lock:
        s = socket.create_connection((PLC_HOST, PLC_PORT), timeout=3)
        try:
            s.sendall(fr)
            buf, deadline = b"", time.time() + 2.0
            while time.time() < deadline:
                s.settimeout(max(0.05, deadline - time.time()))
                try:
                    c = s.recv(512)
                except socket.timeout:
                    break
                if not c: break
                buf += c
                while b"\r\n" in buf:
                    line, buf = buf.split(b"\r\n", 1)
                    try:
                        raw = bytes.fromhex(line[line.rfind(b":") + 1:].decode())
                    except ValueError:
                        continue
                    if len(raw) >= 4 and lrc(raw[:-1]) == raw[-1]:
                        if raw[:-1] == req:
                            return True
                        if raw[0] == STATION and raw[1] == 0x86:
                            raise RuntimeError(f"Modbus exception {raw[2]}")
        finally:
            s.close()
    return False

def set_speed(hz, ip):
    if SPEED_REG is None:
        return 503, "Tezlikni o'zgartirish hali yoqilmagan (panel registri aniqlanmagan)"
    if not (SPEED_MIN <= hz <= SPEED_MAX):
        return 400, f"Tezlik {SPEED_MIN}–{SPEED_MAX} Hz oralig'ida bo'lishi kerak"
    old = state.get("hz")
    # Avval ekran nusxalari, oxirida haqiqiy sozlama (D481)
    ok = all([write_register(r, hz * 100) for r in SPEED_COPIES]
             + [write_register(SPEED_DISPLAY, hz), write_register(SPEED_REG, hz * 100)])
    now = None
    for _ in range(5):          # PLK D481 ni D86 ga ko'chirganini tekshiramiz
        time.sleep(0.5)
        try:
            now = read_block(86, 1)[86] / 100
            break
        except Exception:
            pass
    if now == hz:
        log_event(f"Masofadan: tezlik {old} -> {hz} Hz ({ip})", state.get("total_m"))
        return 200, f"Tezlik {hz} Hz qilindi"
    log_event(f"Masofadan: tezlik {old} -> {hz} Hz — TASDIQLANMADI ({ip})", state.get("total_m"))
    return 502, f"PLK tasdiqlamadi (javob={ok}, D86={now})"

def stop_machine(ip):
    if STOP_BIT is None:
        return 503, "To'xtatish hali yoqilmagan (sinalmagan)"
    if not state.get("running"):
        return 400, "Stanok allaqachon to'xtagan"
    ok = write_coil(STOP_BIT, 0)
    stopped = False
    for _ in range(10):         # dvigatel buyrug'i (D88) pasaya boshlashini kutamiz
        time.sleep(0.5)
        try:
            if read_block(STOP_BIT, 1, fn=0x01, base=0x0800).get(STOP_BIT) == 0:
                stopped = True; break
        except Exception:
            pass
    # DR134 dagi to'qnashuvlar tufayli yozish javobi yo'qolishi mumkin, buyruq esa bajariladi --
    # shuning uchun natijani PLK holatini qayta o'qib baholaymiz.
    if stopped:
        log_event(f"Masofadan: TO'XTATISH buyrug'i ({ip})", state.get("total_m"))
        return 200, "To'xtatish buyrug'i yuborildi"
    log_event(f"Masofadan: TO'XTATISH — TASDIQLANMADI ({ip})", state.get("total_m"))
    return 502, f"PLK tasdiqlamadi (javob={ok})"

def reset_shift(ip):
    if not RESET_REGS:
        return 503, "Smenani tiklash yoqilmagan"
    try:
        before = read_block(516, 1)[516] / 100
    except Exception:
        return 503, "Smena metrini o'qib bo'lmadi, keyinroq urinib ko'ring"
    oks = [write_register(r, 0) for r in RESET_REGS]
    after = eff_t = None
    for _ in range(6):          # yozish javobi yo'qolishi mumkin -- qayta o'qib tekshiramiz
        time.sleep(0.5)
        try:
            after = read_block(516, 1)[516] / 100
            eff_t = read_block(2000, 29)[2000]
            if after < 0.5 and eff_t <= 1:
                break
        except Exception:
            pass
    if after is not None and after < 0.5 and eff_t is not None and eff_t <= 1:
        log_event(f"Masofadan: SMENA TIKLANDI ({before} m edi) ({ip})", state.get("total_m"))
        return 200, f"Smena tiklandi ({before} m \"Oxirgi smenalar\"ga yoziladi)"
    log_event(f"Masofadan: smenani tiklash — TASDIQLANMADI ({ip})", state.get("total_m"))
    return 502, f"PLK tasdiqlamadi (javoblar={sum(map(bool, oks))}/{len(oks)}, D516={after}, D2000={eff_t})"

def control(action, value, pin, ip):
    n, t = pin_fails.get(ip, (0, 0))
    if n >= MAX_PIN_FAILS and time.time() - t < PIN_BLOCK_SEC:
        return 429, f"Ko'p noto'g'ri PIN. {int(PIN_BLOCK_SEC - (time.time() - t))} s kuting."
    if not CONTROL_PIN:
        return 403, "Boshqaruv o'chirilgan (PIN sozlanmagan)"
    if not hmac.compare_digest(str(pin), CONTROL_PIN):
        pin_fails[ip] = (n + 1 if time.time() - t < PIN_BLOCK_SEC else 1, time.time())
        return 403, "PIN noto'g'ri"
    pin_fails.pop(ip, None)
    if action == "speed":
        return set_speed(value, ip)
    if action == "stop":
        return stop_machine(ip)
    if action == "reset_shift":
        return reset_shift(ip)
    if action not in CONTROLS:
        return 400, "Noma'lum buyruq"
    m, allowed, label = CONTROLS[action]
    if value not in allowed:
        return 400, f"{label}: bu qiymatga ruxsat yo'q"
    ok = write_coil(m, value)
    time.sleep(0.3)
    try:
        now = read_block(m, 1, fn=0x01, base=0x0800).get(m)
    except Exception:
        now = None
    verb = "yoqildi" if value else "o'chirildi"
    if now == value:
        log_event(f"Masofadan: {label} {verb} ({ip})", state.get("total_m"))
        return 200, f"{label} {verb}"
    log_event(f"Masofadan: {label} {verb} — TASDIQLANMADI ({ip})", state.get("total_m"))
    return 502, f"{label}: PLK tasdiqlamadi (javob={ok}, holat={now})"

def read_bits():
    # Panel tugmalari (joyida test bilan tasdiqlangan): M16=FAN[6], M531=LIGHT[5],
    # M518/M519/M520 = WARP STOP[7] / WEFT BRE STOP[8] / WEFT END STOP[9] bypass.
    # Bloklar uzunligi har xil (4 va 3 bayt), shunda javoblar adashmaydi.
    # M29 = WEFT END (barabanda ip tugadi) belgisi, Y0/Y2/Y16 = ogohlantirish chiqishlari --
    # 2026-11-14 dagi bitta ip tugashi kuzatuvidan, hali tasdiqlanmagan.
    bits = {}
    for start, cnt in ((0, 96), (512, 24)):   # 12 va 3 bayt
        try:
            bits.update(read_block(start, cnt, fn=0x01, base=0x0800))
        except Exception:
            pass
    try:   # Y0..Y17 (8lik): 16 bit = 2 bayt, M bloklaridan farqli uzunlik
        y = read_block(0, 16, fn=0x01, base=0x0500)
        bits.update({f"Y{oct(k)[2:]}": v for k, v in y.items()})
    except Exception:
        pass
    try:   # X0..X7: asos ipi uzilganda X3 = 1 (2026-11-14 18:14 kuzatuvi)
        x = read_block(0, 8, fn=0x02, base=0x0400)
        bits.update({f"X{k}": v for k, v in x.items()})
    except Exception:
        pass
    return bits

def stop_reason(bits):
    if bits.get(29) == 1:
        return "Barabanda ip tugadi (WEFT END) ?"
    if bits.get("X3") == 1 or bits.get(20) == 1:
        return "Asos ipi uzildi (WARP BREAK) ?"
    # M40: 21:12 (panelda boshqa tugma -> avariya) va 21:47 (operator qo'lda to'xtatdi) -- ikkalasida yoniq
    if bits.get(40) == 1:
        return "Qo'lda to'xtatilgan yoki avariya (M40)"
    alarm = [y for y in ("Y0", "Y2", "Y16") if bits.get(y) == 1]
    if alarm:
        return f"Noma'lum ogohlantirish ({', '.join(alarm)})"
    return None

def u32(regs, lo):
    return (regs[lo + 1] << 16) | regs[lo] if lo in regs and lo + 1 in regs else None

# ---------- Fonda o'qish va holat ----------
state = {"ok": False, "error": "hali o'qilmadi", "time": "-"}
lock = threading.Lock()
history = deque()          # (vaqt, umumiy_sm)
chart = deque(maxlen=720)  # oxirgi ~1 soat: (vaqt, m/daq)
events = deque(maxlen=50)

def log_event(kind, total_m):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    events.appendleft({"time": ts, "event": kind})
    os.makedirs(LOG_DIR, exist_ok=True)
    path = os.path.join(LOG_DIR, "events.csv")
    new = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.writer(f)
        if new: w.writerow(["vaqt", "voqea", "umumiy_m"])
        w.writerow([ts, kind, total_m])

def append_csv(path, header, row):
    # Sarlavha o'zgargan bo'lsa (yangi ustunlar), eski faylni .vN.csv qilib chetga olamiz --
    # bitta faylda har xil tuzilishdagi qatorlar aralashmasin.
    os.makedirs(LOG_DIR, exist_ok=True)
    if os.path.exists(path):
        with open(path, newline="") as f:
            old = next(csv.reader(f), None)
        if old != header:
            n = 1
            while os.path.exists(f"{path[:-4]}.v{n}.csv"): n += 1
            os.rename(path, f"{path[:-4]}.v{n}.csv")
    new = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.writer(f)
        if new: w.writerow(header)
        w.writerow(row)

def log_minute(d):
    append_csv(os.path.join(LOG_DIR, time.strftime("%Y-%m-%d") + ".csv"),
               ["vaqt", "holat", "hz", "hz_haqiqiy", "ppm", "m_daq", "m_daq_panel",
                "samaradorlik", "smena", "smena_m", "umumiy_m", "arqoq_zichligi", "warp_a_kg"],
               [d["time"], d["status"], d["hz"], d["hz_actual"], d["ppm"], d["m_min"],
                d["m_min_panel"], d["efficiency"], d["shift"], d["shift_m"], d["total_m"],
                d["weft_den"], d["warp_a"]])

# ---------- Smena hisoboti ----------
# Nazoratchi paneldan smenani tiklaganda D516 (PRODUT) va D514 nolga tushadi. Shuni ushlab,
# tugagan smenani smenalar.csv ga yozamiz. Holat qayta ishga tushishlarda yo'qolmasligi uchun
# shift_state.json da saqlanadi.
SHIFT_CSV = os.path.join(LOG_DIR, "smenalar.csv")
SHIFT_STATE = os.path.join(LOG_DIR, "shift_state.json")
SHIFT_HEADER = ["tugadi", "boshlandi", "smena", "toqildi_m", "ishladi_daq", "toxtadi_daq",
                "toxtashlar_soni", "ortacha_m_soat"]
RESET_CONFIRM = 2          # ketma-ket shuncha keskin tushish -- haqiqiy tiklash

def load_shift_state():
    try:
        with open(SHIFT_STATE) as f:
            return json.load(f)
    except Exception:
        return {"start": None, "run_s": 0.0, "stop_s": 0.0, "stops": 0, "last_m": None, "shift": None}

def save_shift_state(s):
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(SHIFT_STATE + ".tmp", "w") as f:
            json.dump(s, f)
        os.replace(SHIFT_STATE + ".tmp", SHIFT_STATE)
    except Exception:
        pass

def recent_shifts(n=10):
    try:
        with open(SHIFT_CSV, newline="") as f:
            return list(csv.DictReader(f))[-n:][::-1]
    except Exception:
        return []

def poller():
    running, last_change, last_total, last_min = None, time.time(), None, None
    last_bypass = last_reason = None
    sh = load_shift_state(); zero_count = 0; last_t = time.time(); last_save = 0
    while True:
        t = time.time()
        try:
            regs = read_all()
            total = u32(regs, 528)
            if total is not None:
                history.append((t, total))
                while history and history[0][0] < t - SPEED_WINDOW:
                    history.popleft()
                if last_total is None or total != last_total:
                    last_change = t
                last_total = total
            m_min = None
            if len(history) >= 2 and history[-1][0] - history[0][0] >= 60:
                (t0, l0), (t1, l1) = history[0], history[-1]
                m_min = round((l1 - l0) / 100 / ((t1 - t0) / 60), 2)
            bits = read_bits()
            bypass = [n for m, n in ((518, "WARP STOP [7]"), (519, "WEFT BRE STOP [8]"),
                                     (520, "WEFT END STOP [9]")) if bits.get(m) == 1]
            fr = [regs[k] for k in range(89, 100) if k in regs]
            freq = sum(fr) / len(fr) if fr else None
            now_running = freq > RUN_FREQ if freq is not None else (t - last_change) < STOP_AFTER
            weft = regs.get(491)   # panel 2-ekran: WEFT DEN 39.0 = D491 (21:31 surat)
            d = {
                "ok": True, "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "status": "Ishlayapti" if now_running else "To'xtagan",
                "running": now_running,
                "hz": regs[481] / 100 if 481 in regs else None,   # haqiqiy sozlama (D481)
                "hz_actual": round(freq / 100, 2) if freq is not None else None,
                "m_min": m_min if now_running else 0.0,
                "m_hour": round(m_min * 60, 1) if (m_min and now_running) else 0.0,
                "reason": None if now_running else stop_reason(bits),
                "bypass": bypass if 518 in bits else None,
                "light": bits.get(531),
                "fan": bits.get(16),
                "ppm": regs.get(218),
                "m_min_panel": regs[228] / 10 if 228 in regs else None,
                "efficiency": regs[229] / 10 if 229 in regs else None,
                # panel EFFICIENCY = D2018/10 (21:22 panel 53,8 / D2018=538; D2008/D2000 = ishlagan/umumiy daqiqa)
                "eff_panel": regs[2018] / 10 if 2018 in regs else None,
                "eff_min": [regs.get(2008), regs.get(2000)],
                "shift": regs.get(536),
                "shift_m": round(regs[516] / 100, 2) if 516 in regs else None,
                "shift_pulses": regs.get(514),
                "total_m": round(total / 100, 2) if total is not None else None,
                "weft_den": round(weft / 10, 1) if weft is not None else None,
                "warp_a": int(regs[485] / 10) / 100 if 485 in regs else None,   # 4058 -> 4.05 kg (panel kesadi)
                "main_load": regs[66] / 10 if 66 in regs else None,          # MAIN LOAD = D66/10 (15 Hz da 3.7 A -- tasdiqlangan)
                "pulses": u32(regs, 526),
                "nonzero": {f"D{k}": v for k, v in sorted(regs.items()) if v},
            }
            # --- smena hisobi ---
            dt = min(t - last_t, 30); last_t = t
            sh["run_s" if now_running else "stop_s"] += dt
            if running and not now_running:
                sh["stops"] += 1
            sm = d["shift_m"]
            if sh["start"] is None:
                sh["start"] = d["time"]
            if sm is not None:
                # tiklash = keskin tushish (stanok ishlab tursa metr darrov o'sa boshlaydi,
                # shuning uchun aynan nolni kutmaymiz); last_m tasdiqlanguncha o'zgarmaydi
                prev = sh["last_m"] or 0
                if prev >= 1.0 and sm < prev - 1.0 and sm < 5:
                    zero_count += 1
                    if zero_count >= RESET_CONFIRM:
                        hours = sh["run_s"] / 3600
                        append_csv(SHIFT_CSV, SHIFT_HEADER, [
                            d["time"], sh["start"], sh["shift"] if sh["shift"] is not None else d["shift"],
                            sh["last_m"], round(sh["run_s"] / 60), round(sh["stop_s"] / 60), sh["stops"],
                            round(sh["last_m"] / hours, 1) if hours > 0.05 else ""])
                        log_event(f"Smena tiklandi: {sh['last_m']} m to'qilgan", d["total_m"])
                        sh = {"start": d["time"], "run_s": 0.0, "stop_s": 0.0, "stops": 0,
                              "last_m": sm, "shift": d["shift"]}
                        zero_count = 0
                        last_save = 0      # yangi smena holatini darhol saqlaymiz
                else:
                    zero_count = 0
                    sh["last_m"] = sm
                    sh["shift"] = d["shift"]
            if t - last_save > 30:
                save_shift_state(sh); last_save = t
            tot = sh["run_s"] + sh["stop_s"]
            d["eff_calc"] = round(sh["run_s"] / tot * 100, 1) if tot >= 60 else (100.0 if now_running else None)
            d["shift_info"] = {"start": sh["start"], "run_min": round(sh["run_s"] / 60),
                               "stop_min": round(sh["stop_s"] / 60), "stops": sh["stops"]}
            if running is not None and now_running != running:
                log_event("Ishga tushdi" if now_running else "To'xtadi", d["total_m"])
            if not now_running and d["reason"] and d["reason"] != last_reason:
                log_event(f"To'xtash sababi: {d['reason']}", d["total_m"])
            last_reason = d["reason"]
            running = now_running
            if d["bypass"] is not None:
                if last_bypass is not None and set(d["bypass"]) != set(last_bypass):
                    for n in set(d["bypass"]) - set(last_bypass):
                        log_event(f"Himoya O'CHIRILDI: {n}", d["total_m"])
                    for n in set(last_bypass) - set(d["bypass"]):
                        log_event(f"Himoya yoqildi: {n}", d["total_m"])
                last_bypass = d["bypass"]
            chart.append((int(t), d["m_min"] or 0))
            minute = time.strftime("%Y-%m-%d %H:%M")
            if minute != last_min:
                log_minute(d); last_min = minute
        except Exception as e:
            d = {"ok": False, "error": str(e), "time": time.strftime("%Y-%m-%d %H:%M:%S")}
        d["shifts"] = recent_shifts()
        d["stop_enabled"] = STOP_BIT is not None
        d["reset_enabled"] = RESET_BIT is not None
        d["speed_enabled"] = SPEED_REG is not None
        d["speed_min"], d["speed_max"] = SPEED_MIN, SPEED_MAX
        d["events"] = list(events)
        d["chart"] = list(chart)
        with lock:
            state.clear(); state.update(d)
        alive["poll"] = time.time()
        time.sleep(max(0.5, POLL_SEC - (time.time() - t)))

PAGE = r"""<!doctype html><html lang="uz"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#0e1116">
<title>Stanok · SBY-850×6G</title>
<style>
:root{
 --bg:#eef1f5;--panel:#ffffff;--panel2:#f6f8fb;--fg:#141a23;--mut:#5b6577;--line:#dfe4ec;
 --acc:#f26b1d;--accbtn:#c2410c;--acc2:#ffe3d1;--ok:#166534;--okbg:#e3f6ea;--err:#b42318;--errbg:#fde8e8;--warn:#92400e;--warnbg:#fff3d6;
 --idle:#7a8496;--shadow:0 1px 2px rgba(16,24,40,.06),0 4px 16px rgba(16,24,40,.05);
 color-scheme:light}
@media(prefers-color-scheme:dark){:root{
 --bg:#0e1116;--panel:#161b23;--panel2:#1c222c;--fg:#e9eef5;--mut:#8f9bad;--line:#262e3a;
 --acc:#ff7a2e;--accbtn:#c2410c;--acc2:#3a2416;--ok:#2fc46e;--okbg:#10291b;--err:#ff5a5f;--errbg:#311517;--warn:#f0a72a;--warnbg:#2f2410;
 --idle:#5d6779;--shadow:none;color-scheme:dark}}
*{box-sizing:border-box}
[hidden]{display:none!important}
html,body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.4 system-ui,-apple-system,"Segoe UI",Roboto,Arial,sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:16px 16px calc(28px + env(safe-area-inset-bottom))}
.num{font-variant-numeric:tabular-nums}
button{font:inherit;color:inherit}

/* header */
header{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;margin:4px 0 16px}
.brand{display:flex;align-items:center;gap:12px}
.logo{width:40px;height:40px;border-radius:11px;background:var(--acc);display:grid;place-items:center;color:#fff;font-weight:800;font-size:15px;letter-spacing:.5px}
.brand h1{font-size:18px;margin:0;font-weight:700}
.brand p{margin:0;color:var(--mut);font-size:12.5px}
.live{display:flex;align-items:center;gap:8px;color:var(--mut);font-size:13px;background:var(--panel);border:1px solid var(--line);border-radius:999px;padding:6px 12px}
.dot{width:8px;height:8px;border-radius:50%;background:var(--idle)}
.dot.on{background:var(--ok);box-shadow:0 0 0 0 var(--ok);animation:pulse 2s infinite}
@keyframes pulse{0%{box-shadow:0 0 0 0 rgba(47,196,110,.55)}70%{box-shadow:0 0 0 8px rgba(47,196,110,0)}100%{box-shadow:0 0 0 0 rgba(47,196,110,0)}}

/* layout */
.grid{display:grid;gap:14px}
.top{grid-template-columns:1.35fr 1fr}
.cols{grid-template-columns:1fr 1fr}
@media(max-width:860px){.top,.cols{grid-template-columns:1fr}}
.card{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:16px;box-shadow:var(--shadow);min-width:0}
.card h2{margin:0 0 12px;font-size:13px;font-weight:650;color:var(--mut);text-transform:uppercase;letter-spacing:.06em;display:flex;justify-content:space-between;align-items:center;gap:8px}
.card h2 small{text-transform:none;letter-spacing:0;font-weight:500}
section{margin-bottom:14px}

/* hero */
.hero{position:relative;overflow:hidden;border-radius:18px;padding:20px;color:#fff;background:var(--idle);display:flex;flex-direction:column;gap:16px;min-height:220px}
.hero.run{background:linear-gradient(135deg,#0f7a3d,#15803d)}
.hero.stop{background:linear-gradient(135deg,#b42318,#c62f35)}
.hero.off{background:linear-gradient(135deg,#4b5563,#5b6577)}
.hero .row{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;flex-wrap:wrap}
.hero .state{font-size:34px;font-weight:800;line-height:1.1}
.hero .reason{font-size:16px;font-weight:600;margin-top:6px;max-width:46ch}
.hero .todo{margin-top:10px;background:rgba(0,0,0,.22);border-radius:10px;padding:8px 12px;font-size:15px;max-width:52ch}
.hero .todo b{display:block;font-size:11.5px;letter-spacing:.08em;text-transform:uppercase}
.hero .since{font-size:14px;margin-top:4px}
.hero .big{display:flex;gap:26px;flex-wrap:wrap;margin-top:auto}
.hero .big div b{display:block;font-size:30px;font-weight:750;line-height:1.1}
.hero .big div span{font-size:13px}
.hero .big div.main b{font-size:40px}
.stopbtn{background:#fff;color:#c62f35;border:0;border-radius:12px;padding:12px 18px;font-weight:800;font-size:16px;cursor:pointer;box-shadow:0 2px 10px rgba(0,0,0,.18);display:flex;align-items:center;gap:8px}
.stopbtn:disabled{opacity:.6;cursor:default}
.stopbtn i{width:12px;height:12px;background:#c62f35;border-radius:2px;display:inline-block}

/* kpis */
.kpis{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.kpi{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:14px 16px;box-shadow:var(--shadow);display:flex;flex-direction:column;justify-content:space-between;min-height:103px}
.kpi .k{font-size:12.5px;color:var(--mut)}
.kpi .v{font-size:28px;font-weight:750;margin-top:4px}
.kpi .v small{font-size:14px;color:var(--mut);font-weight:500;margin-left:3px}
.kpi .s{font-size:13px;color:var(--mut);margin-top:2px}
.bar{height:6px;border-radius:9px;background:var(--panel2);overflow:hidden;margin-top:8px;border:1px solid var(--line)}
.bar i{display:block;height:100%;background:var(--ok);border-radius:9px;transition:width .6s}
.bar i.lo{background:var(--err)}.bar i.mid{background:var(--warn)}

/* banner */
.banner{display:none;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;background:var(--warnbg);color:var(--fg);border:1px solid var(--warn);border-radius:14px;padding:12px 14px;margin-bottom:14px}
.banner b{color:var(--warn)}
.banner .bb{display:flex;gap:8px;flex-wrap:wrap}

/* controls */
.ctl{display:grid;gap:14px}
.speed{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.speed .cur{font-size:40px;font-weight:800;min-width:92px;text-align:center}
.speed .far{margin:0 10px}
@media(max-width:520px){.speed{gap:6px}.speed .btn{min-width:48px;padding:10px 8px}.speed .far{margin:0 4px}.speed .cur{font-size:30px;min-width:58px}}
.speed .cur small{font-size:15px;color:var(--mut);font-weight:600;margin-left:2px}
.btn{border:1px solid var(--line);background:var(--panel2);border-radius:11px;padding:10px 14px;font-weight:650;cursor:pointer;min-width:56px;min-height:48px;transition:background .15s,border-color .15s}
.btn:hover:not(:disabled){border-color:var(--acc)}
.btn:disabled{opacity:.45;cursor:default}
.btn.pri{background:var(--accbtn);border-color:var(--accbtn);color:#fff}
.btn.warn{background:var(--warnbg);border-color:var(--warn)}
.btn.danger{background:var(--errbg);border-color:var(--err);color:var(--err)}
.inrow{display:flex;gap:8px;margin-top:10px}
.inrow input{flex:1;min-width:0;max-width:150px;min-height:48px;padding:10px 12px;border-radius:11px;border:1px solid var(--line);background:var(--panel2);color:var(--fg);font:inherit;font-weight:600}
.hint{font-size:13px;color:var(--mut);margin-top:8px}
.toggles{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.tg{display:flex;justify-content:space-between;align-items:center;gap:10px;padding:12px 14px;border:1px solid var(--line);border-radius:13px;background:var(--panel2);cursor:pointer;text-align:left;width:100%}
.tg:disabled{opacity:.5;cursor:default}
.tg .l b{display:block;font-size:15px}.tg .l span{font-size:13px;color:var(--mut)}
.sw{width:44px;height:26px;border-radius:99px;background:var(--line);position:relative;flex:none;transition:background .2s}
.sw::after{content:"";position:absolute;top:3px;left:3px;width:20px;height:20px;border-radius:50%;background:#fff;box-shadow:0 1px 3px rgba(0,0,0,.25);transition:left .2s}
.tg.on .sw{background:var(--ok)}.tg.on .sw::after{left:21px}
.prot{display:flex;align-items:center;gap:10px;padding:12px 14px;border-radius:13px;border:1px solid var(--line);background:var(--panel2)}
.pill{display:inline-flex;align-items:center;gap:6px;font-size:12.5px;font-weight:650;padding:4px 10px;border-radius:99px}
.pill.ok{background:var(--okbg);color:var(--ok)}.pill.bad{background:var(--errbg);color:var(--err)}.pill.mut{background:var(--panel2);color:var(--mut);border:1px solid var(--line)}

/* params */
.params{display:grid;grid-template-columns:repeat(3,1fr);gap:1px;background:var(--line);border:1px solid var(--line);border-radius:13px;overflow:hidden}
@media(max-width:520px){.params{grid-template-columns:1fr 1fr}}
.params div{background:var(--panel);padding:12px 14px}
.params span{display:block;font-size:13px;color:var(--mut)}
.params b{display:block;font-size:19px;font-weight:700;margin-top:2px}
.params b small{font-size:12.5px;color:var(--mut);font-weight:500;margin-left:2px}

/* chart */
.chart{position:relative;height:190px}
.chart svg{width:100%;height:100%;display:block;overflow:visible}
.chart .ax{fill:var(--mut);font-size:11px}
.chart .gl{stroke:var(--line);stroke-width:1}
.empty{color:var(--mut);font-size:14px;padding:30px 0;text-align:center}

/* shift */
.shnow{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:12px}
@media(max-width:520px){.shnow{grid-template-columns:1fr 1fr}}
.shnow div{background:var(--panel2);border:1px solid var(--line);border-radius:12px;padding:10px 12px}
.shnow span{display:block;font-size:13px;color:var(--mut)}.shnow b{font-size:18px}
.tbl{overflow-x:auto;border:1px solid var(--line);border-radius:12px}
table{width:100%;border-collapse:collapse;font-size:13.5px}
th,td{padding:9px 12px;text-align:left;white-space:nowrap;border-bottom:1px solid var(--line)}
th{font-size:12px;color:var(--mut);font-weight:600;background:var(--panel2)}
tr:last-child td{border-bottom:0}
td.r,th.r{text-align:right}

/* events */
.ev{list-style:none;margin:0;padding:0;max-height:340px;overflow:auto}
.ev li{display:flex;gap:12px;padding:9px 2px;border-bottom:1px solid var(--line);font-size:14px;align-items:flex-start}
.ev li:last-child{border-bottom:0}
.ev time{color:var(--mut);font-size:12.5px;min-width:64px;padding-top:1px}
.ev i{width:9px;height:9px;border-radius:50%;margin-top:6px;flex:none;background:var(--idle)}
.ev i.g{background:var(--ok)}.ev i.r{background:var(--err)}.ev i.o{background:var(--acc)}.ev i.y{background:var(--warn)}

details.card summary{cursor:pointer;color:var(--mut);font-size:13px;font-weight:650;text-transform:uppercase;letter-spacing:.06em}
details.card[open] summary{margin-bottom:12px}
.raw{display:grid;grid-template-columns:repeat(auto-fill,minmax(120px,1fr));gap:6px;font-size:12.5px}
.raw div{background:var(--panel2);border-radius:8px;padding:5px 8px;display:flex;justify-content:space-between}
.raw span{color:var(--mut)}
.resetrow{display:flex;justify-content:flex-end;margin-top:14px;padding-top:14px;border-top:1px dashed var(--line)}
footer{color:var(--mut);font-size:12px;text-align:center;margin-top:8px}

/* modal + toast */
.ov{position:fixed;inset:0;background:rgba(5,8,12,.55);display:none;align-items:center;justify-content:center;padding:16px;z-index:20}
.ov.show{display:flex}
.modal{background:var(--panel);border:1px solid var(--line);border-radius:18px;padding:20px;width:100%;max-width:380px;box-shadow:0 20px 60px rgba(0,0,0,.35)}
.modal h3{margin:0 0 6px;font-size:18px}.modal p{margin:0 0 14px;color:var(--mut);font-size:14px;white-space:pre-line}
.modal input{width:100%;padding:12px 14px;border-radius:12px;border:1px solid var(--line);background:var(--panel2);color:var(--fg);font:inherit;font-size:20px;letter-spacing:.3em;text-align:center;margin-bottom:14px}
.modal .acts{display:flex;gap:8px;justify-content:flex-end}
.toast{position:fixed;left:50%;bottom:calc(20px + env(safe-area-inset-bottom));transform:translateX(-50%) translateY(20px);opacity:0;background:var(--fg);color:var(--bg);padding:11px 16px;border-radius:12px;font-size:14px;font-weight:600;transition:all .25s;z-index:30;max-width:calc(100% - 32px)}
.toast.show{opacity:1;transform:translateX(-50%) translateY(0)}
.toast.bad{background:var(--err);color:#fff}.toast.good{background:var(--ok);color:#fff}
</style></head><body>
<div class="wrap">
<header>
 <div class="brand"><div class="logo">SBY</div><div><h1>Stanok monitori</h1><p>Yongming SBY-850×6G to'quv stanogi</p></div></div>
 <div class="live"><span class="dot" id="dot"></span><span id="upd">ulanmoqda…</span></div>
</header>

<div class="banner" id="byp"><div><b>⚠ Uzilish himoyasi o'chirilgan:</b> <span id="bypt"></span></div><div class="bb" id="bypb"></div></div>

<section class="grid top">
 <div class="hero off" id="hero">
  <div class="row">
   <div><div class="state" id="sts">—</div><div class="since" id="since"></div><div class="reason" id="rsn"></div><div class="todo" id="todo" hidden><b>Nima qilish kerak</b><span id="todot"></span></div></div>
   <button class="stopbtn" id="bstop" hidden><i></i>To'xtatish</button>
  </div>
  <div class="big">
   <div class="main"><b class="num" id="h_mh">—</b><span>metr / soat</span></div>
   <div><b class="num" id="h_mmin">—</b><span>metr / daqiqa</span></div>
   <div><b class="num" id="h_hz">—</b><span>Hz (sozlama)</span></div>
  </div>
 </div>
 <div class="kpis">
  <div class="kpi"><div class="k">Smenada to'qildi</div><div class="v num" id="k_shm">—</div><div class="s" id="k_shp"></div></div>
  <div class="kpi"><div class="k">Panel samaradorligi</div><div class="v num" id="k_eff">—</div><div class="bar"><i id="k_effb" style="width:0"></i></div><div class="s">stanok panelidagi EFFICIENCY</div></div>
  <div class="kpi"><div class="k">Tezlik (PPM)</div><div class="v num" id="k_ppm">—</div><div class="s" id="k_ppms"></div></div>
  <div class="kpi"><div class="k">Smena</div><div class="v num" id="k_shift">—</div><div class="s" id="k_shs"></div></div>
 </div>
</section>

<section class="grid cols">
 <div class="card" id="ctlcard">
  <h2>Boshqaruv <small id="pinst"></small></h2>
  <div class="ctl">
   <div id="spd">
    <div class="speed">
     <button class="btn far" id="sdn10" aria-label="10 Hz kamaytirish">−10</button><button class="btn" id="sdn" aria-label="1 Hz kamaytirish">−1</button>
     <div class="cur num" id="hz">—<small>Hz</small></div>
     <button class="btn" id="sup" aria-label="1 Hz oshirish">+1</button><button class="btn far" id="sup10" aria-label="10 Hz oshirish">+10</button>
    </div>
    <div class="inrow"><input id="sval" type="number" inputmode="numeric" placeholder="Hz"><button class="btn pri" id="sset">O'rnatish</button></div>
    <div class="hint" id="shint"></div>
   </div>
   <div class="toggles">
    <button class="tg" id="b_light"><div class="l"><b>Chiroq</b><span>panel tugmasi [5]</span></div><div class="sw"></div></button>
    <button class="tg" id="b_fan"><div class="l"><b>Ventilyator</b><span>panel tugmasi [6]</span></div><div class="sw"></div></button>
   </div>
   <div class="prot"><span style="flex:1">Uzilish himoyasi [7–9]</span><span id="prot" class="pill mut">—</span></div>
  </div>
 </div>
 <div class="card">
  <h2>Stanok ko'rsatkichlari</h2>
  <div class="params">
   <div><span>Arqoq zichligi</span><b class="num" id="p_weft">—</b></div>
   <div><span>Dvigatel toki</span><b class="num" id="p_load">—</b></div>
   <div><span>Sozlangan chastota</span><b class="num" id="p_hz">—</b></div>
   <div><span>Haqiqiy chastota</span><b class="num" id="p_hza">—</b></div>
   <div><span>Umumiy uzunlik</span><b class="num" id="p_total">—</b></div>
   <div><span>Ishlash ulushi (smena)</span><b class="num" id="p_share">—</b></div>
  </div>
  <div class="hint">Ishlash ulushi = smenada ishlagan vaqt ÷ umumiy vaqt. Panel samaradorligidan boshqa hisob.</div>
 </div>
</section>

<section class="card">
 <h2>Tezlik, oxirgi 1 soat <small>m/daq</small></h2>
 <div class="chart" id="chart"></div>
</section>

<section class="card">
 <h2>Smena</h2>
 <div class="shnow">
  <div><span>Boshlangan</span><b class="num" id="c_start">—</b></div>
  <div><span>Ishladi</span><b class="num" id="c_run">—</b></div>
  <div><span>To'xtab turdi</span><b class="num" id="c_stop">—</b></div>
  <div><span>To'xtashlar</span><b class="num" id="c_stops">—</b></div>
 </div>
 <div class="tbl"><table id="sh"><thead><tr><th>Tugadi</th><th>Boshlandi</th><th class="r">To'qildi, m</th><th class="r">Ishladi</th><th class="r">To'xtadi</th><th class="r">To'xtash</th><th class="r">m/soat</th></tr></thead><tbody></tbody></table></div>
 <div class="resetrow"><button class="btn danger" id="breset" hidden>↺ Smenani tiklash</button></div>
</section>

<section class="card">
 <h2>Voqealar</h2>
 <ul class="ev" id="ev"></ul>
</section>

<details class="card"><summary>Texnik ma'lumot (mexanik uchun)</summary>
 <div class="params" style="margin-bottom:12px">
  <div><span>WARP-A</span><b class="num" id="p_warpa">—</b></div>
  <div><span>Panel m/min (tasdiqlanmagan)</span><b class="num" id="p_mminp">—</b></div>
  <div><span>Smena impulslari</span><b class="num" id="p_pulses">—</b></div>
  <div><span>PLK / shlyuz</span><b style="font-size:14px">Delta DVP32ES2 · DR134 192.168.0.7</b></div>
  <div><span>Panel ishladi / jami</span><b class="num" id="p_effmin">—</b></div>
  <div><span>Jurnal papkasi</span><b style="font-size:14px">~/stanok_log/</b></div>
 </div>
 <div class="hint" style="margin:0 0 8px">Barcha noldan farqli D registrlar:</div>
 <div class="raw num" id="raw"></div></details>
<footer>Har 3 soniyada yangilanadi · Stanokni yoqish faqat stanokdagi tugma bilan</footer>
</div>

<div class="ov" id="ov"><div class="modal">
 <h3 id="m_t"></h3><p id="m_p"></p>
 <input id="m_pin" type="password" inputmode="numeric" autocomplete="off" placeholder="PIN">
 <div class="acts"><button class="btn" id="m_no">Bekor</button><button class="btn pri" id="m_yes">Tasdiqlash</button></div>
</div></div>
<div class="toast" id="toast"></div>

<script>
const $=id=>document.getElementById(id);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt=(v,d=2)=>v==null?'—':Number(v).toLocaleString('ru-RU',{minimumFractionDigits:d,maximumFractionDigits:d});
const mins=m=>m==null?'—':(m>=60?Math.floor(m/60)+' soat '+(m%60)+' daq':m+' daq');
const ts=s=>{const m=/^(\d+)-(\d+)-(\d+) (\d+):(\d+):(\d+)/.exec(s||'');return m?new Date(+m[1],m[2]-1,+m[3],+m[4],+m[5],+m[6]).getTime()/1000:null;};
function todo(r){if(!r)return "Sababni stanok panelida tekshiring. Yoqish faqat stanokdagi tugma bilan.";
 if(/WEFT END/.test(r))return "Arqoq g'altagini almashtiring, keyin stanokdagi tugma bilan yoqing.";
 if(/WARP BREAK/.test(r))return "Uzilgan asos ipini ulang, keyin stanokdagi tugma bilan yoqing.";
 if(/M40/.test(r))return "Avariya tugmasi va panelni tekshiring. Muammo bo'lmasa, stanokdagi tugma bilan yoqing.";
 return "Panel ogohlantirishini tekshiring, kerak bo'lsa mexanikni chaqiring.";}
let busy=false,last=null;
const store={get(){try{return sessionStorage.getItem('pin')}catch(e){return null}},set(v){try{v?sessionStorage.setItem('pin',v):sessionStorage.removeItem('pin')}catch(e){}}};

let tt;function toast(msg,kind){const t=$('toast');t.textContent=msg;t.className='toast show '+(kind||'');clearTimeout(tt);tt=setTimeout(()=>t.className='toast',3800);}

function ask(title,text,danger){return new Promise(res=>{
 $('m_t').textContent=title;$('m_p').textContent=text||'';
 const pin=store.get(),pi=$('m_pin');pi.hidden=!!pin;pi.value='';
 $('m_yes').className='btn '+(danger?'danger':'pri');
 $('ov').classList.add('show');if(!pin)setTimeout(()=>pi.focus(),50);
 const done=v=>{$('ov').classList.remove('show');$('m_yes').onclick=$('m_no').onclick=pi.onkeydown=null;res(v);};
 $('m_no').onclick=()=>done(null);
 $('m_yes').onclick=()=>{const p=pin||pi.value.trim();if(!p){pi.focus();return;}done(p);};
 pi.onkeydown=e=>{if(e.key==='Enter')$('m_yes').click();if(e.key==='Escape')done(null);};
});}

async function send(action,value,title,text,danger){
 const pin=await ask(title,text,danger);if(!pin)return;
 busy=true;render();toast('Yuborilmoqda…');
 try{const r=await fetch('/api/control',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action,value,pin})});
  const j=await r.json();toast(j.message,r.ok?'good':'bad');
  if(r.status===403||r.status===429)store.set(null);else if(r.ok)store.set(pin);
 }catch(e){toast("Server bilan aloqa yo'q",'bad');}
 busy=false;tick();}

function chart(pts){
 const el=$('chart');if(!pts||pts.length<2){el.innerHTML='<div class="empty">Hali ma\'lumot yo\'q</div>';return;}
 const W=el.clientWidth||600,H=el.clientHeight||190,L=34,B=20,T=8;
 const t1=pts[pts.length-1][0],t0=t1-3600,mx=Math.max(0.5,...pts.map(p=>p[1]))*1.15;
 const X=t=>L+(t-t0)/3600*(W-L-4),Y=v=>T+(H-T-B)*(1-v/mx);
 let g='';for(let i=0;i<=3;i++){const v=mx/1.15*i/3,y=Y(v);g+=`<line class="gl" x1="${L}" x2="${W}" y1="${y}" y2="${y}"/><text class="ax" x="${L-6}" y="${y+4}" text-anchor="end">${v.toFixed(v<10?1:0)}</text>`;}
 for(let m=0;m<=60;m+=15){const t=t0+m*60,d=new Date(t*1000);g+=`<text class="ax" x="${X(t)}" y="${H-4}" text-anchor="middle">${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}</text>`;}
 const pp=pts.filter(p=>p[0]>=t0).map(p=>[X(p[0]),Y(p[1])]);
 const line=pp.map(p=>p.map(n=>n.toFixed(1)).join(',')).join(' ');
 const area=`M${pp[0][0]},${Y(0)} L`+line.replace(/ /g,' L')+` L${pp[pp.length-1][0]},${Y(0)} Z`;
 el.innerHTML=`<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none"><defs><linearGradient id="ga" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="var(--acc)" stop-opacity=".28"/><stop offset="1" stop-color="var(--acc)" stop-opacity="0"/></linearGradient></defs>${g}<path d="${area}" fill="url(#ga)"/><polyline points="${line}" fill="none" stroke="var(--acc)" stroke-width="2.2" stroke-linejoin="round"/></svg>`;
}

function evColor(e){e=e.toLowerCase();if(e.includes('ishga tushdi')||e.includes('himoya yoqildi'))return 'g';
 if(e.includes('sabab')||e.includes("o'chirildi")||e.includes('tasdiqlanmadi'))return 'r';
 if(e.includes("to'xtadi"))return 'r';if(e.includes('masofadan')||e.includes('smena'))return 'o';return '';}

function render(){
 if(last&&last.readonly){$('ctlcard').hidden=true;$('bypb').hidden=true;$('bstop').hidden=true;$('breset').hidden=true;}
 const d=last;if(!d)return;
 $('dot').className='dot'+(d.ok?' on':'');$('upd').textContent=d.ok?d.time.slice(11):'aloqa yo\'q';
 const hero=$('hero');
 if(!d.ok){hero.className='hero off';$('sts').textContent="Aloqa yo'q";$('rsn').textContent=d.error||'';$('since').textContent='';$('todo').hidden=true;$('bstop').hidden=true;return;}
 hero.className='hero '+(d.running?'run':'stop');
 $('sts').textContent=d.running?'Ishlayapti':"To'xtagan";
 $('rsn').textContent=d.running?'':(d.reason?'Sabab: '+d.reason:'Sabab aniqlanmadi');
 $('todo').hidden=d.running;$('todot').textContent=d.running?'':todo(d.reason);
 const ev0=d.events&&d.events.find(e=>/^(Ishga tushdi|To'xtadi)$/.test(e.event));
 const ago=ev0?ts(d.time)-ts(ev0.time):null;
 const am=ago==null?null:Math.floor(ago/60),agoT=am==null?'':(am>=60?Math.floor(am/60)+' soat '+(am%60)+' daqiqadan':am+' daqiqadan');
 $('since').textContent=ev0&&ago!=null&&ago>=0?(agoT+' beri ('+ev0.time.slice(11,16)+' dan)'):'';
 const bs=$('bstop');bs.hidden=!(d.stop_enabled&&d.running);bs.disabled=busy;
 bs.onclick=()=>send('stop',0,"Stanokni to'xtataymi?","Qayta yoqish faqat stanokdagi tugma bilan mumkin.",true);
 $('h_mmin').textContent=fmt(d.m_min,2);$('h_mh').textContent=fmt(d.m_hour,1);$('h_hz').textContent=fmt(d.hz,0);

 const sinf=d.shift_info;
 $('k_shm').innerHTML=fmt(d.shift_m)+'<small>m</small>';
 $('k_shp').textContent=sinf&&sinf.run_min>=5&&d.shift_m!=null?('o\'rtacha '+fmt(d.shift_m/(sinf.run_min/60),1)+' m/soat'):'';
 const ef=d.eff_panel;$('k_eff').innerHTML=fmt(ef,1)+'<small>%</small>';
 const eb=$('k_effb');eb.style.width=Math.min(100,ef||0)+'%';eb.className=ef==null?'':ef<60?'lo':ef<85?'mid':'';
 $('k_ppm').innerHTML=fmt(d.ppm,0)+'<small>PPM</small>';$('k_ppms').textContent='arqoq urishi / daqiqa';
 $('k_shift').textContent=d.shift??'—';$('k_shs').textContent=d.shift_info?('boshlandi '+d.shift_info.start.slice(11,16)):'';

 // speed
 $('spd').hidden=!d.speed_enabled;
 $('hz').innerHTML=fmt(d.hz,0)+'<small>Hz</small>';
 if(d.speed_enabled&&d.hz!=null){const h=Math.round(d.hz),lo=d.speed_min,hi=d.speed_max,cl=v=>Math.min(hi,Math.max(lo,v));
  for(const [id,st] of [['sdn10',-10],['sdn',-1],['sup',1],['sup10',10]]){const b=$(id),to=cl(h+st);b.disabled=busy||to===h;b.onclick=()=>send('speed',to,'Tezlik: '+h+' → '+to+' Hz','Stanok yangi tezlikka asta-sekin o\'tadi.');}
  const si=$('sval');si.min=lo;si.max=hi;si.placeholder=lo+'–'+hi+' Hz';$('sset').disabled=busy;
  $('shint').textContent='Ruxsat etilgan oraliq: '+lo+'–'+hi+' Hz';
  $('sset').onclick=()=>{const v=parseInt(si.value,10);
   if(isNaN(v)||v<lo||v>hi){toast('Tezlik '+lo+'–'+hi+' Hz oralig\'ida bo\'lishi kerak','bad');return;}
   if(v===h){toast('Tezlik allaqachon '+h+' Hz');return;}si.value='';send('speed',v,'Tezlik: '+h+' → '+v+' Hz','Stanok yangi tezlikka asta-sekin o\'tadi.');};}
 // toggles
 for(const [k,name] of [['light','Chiroq'],['fan','Ventilyator']]){const b=$('b_'+k),v=d[k];
  b.classList.toggle('on',v===1);b.disabled=v==null||busy;b.querySelector('span').textContent=(v==null?'—':v?'yoniq':"o'chiq")+' · panel tugmasi '+(k==='light'?'[5]':'[6]');
  b.onclick=()=>send(k,v?0:1,name+(v?"ni o'chiraymi?":'ni yoqaymi?'),'');}
 // protection
 const pr=$('prot'),byp=$('byp');
 if(d.bypass==null){pr.className='pill mut';pr.textContent='—';byp.style.display='none';}
 else if(d.bypass.length){pr.className='pill bad';pr.textContent="O'chirilgan";byp.style.display='flex';
  $('bypt').textContent=d.bypass.join(', ')+" — ip uzilsa stanok to'xtamaydi";
  const keys={'WARP STOP [7]':'warp','WEFT BRE STOP [8]':'weftbre','WEFT END STOP [9]':'weftend'},bb=$('bypb'),want=d.bypass.join('|');
  if(bb.dataset.k!==want){bb.dataset.k=want;bb.innerHTML='';d.bypass.forEach(n=>{const b=document.createElement('button');b.className='btn warn';b.textContent='Yoqish: '+n;
   b.onclick=()=>send(keys[n],0,'Himoyani yoqaymi?',n);bb.appendChild(b);});}}
 else{pr.className='pill ok';pr.textContent='Ishlayapti';byp.style.display='none';}
 $('pinst').textContent=store.get()?'PIN saqlangan':'PIN bilan';

 // params
 $('p_weft').innerHTML=fmt(d.weft_den,1)+'<small>t/10sm</small>';
 $('p_load').innerHTML=fmt(d.main_load,1)+'<small>A</small>';
 $('p_warpa').innerHTML=fmt(d.warp_a)+'<small>kg</small>';
 $('p_mminp').innerHTML=fmt(d.m_min_panel,1)+'<small>m/min</small>';
 $('p_hz').innerHTML=fmt(d.hz,0)+'<small>Hz</small>';
 $('p_hza').innerHTML=fmt(d.hz_actual,1)+'<small>Hz</small>';
 $('p_share').innerHTML=d.eff_calc==null?'—':fmt(d.eff_calc,1)+'<small>%</small>';
 $('p_pulses').textContent=d.shift_pulses??'—';
 $('p_effmin').textContent=d.eff_min&&d.eff_min[1]!=null?(d.eff_min[0]+' / '+d.eff_min[1]+' daq'):'—';
 $('p_total').innerHTML=fmt(d.total_m,0)+'<small>m</small>';
 chart(d.chart);

 // shift
 const si=d.shift_info;$('c_start').textContent=si?si.start.slice(11,16):'—';
 $('c_run').textContent=si?mins(si.run_min):'—';$('c_stop').textContent=si?mins(si.stop_min):'—';$('c_stops').textContent=si?si.stops:'—';
 const br=$('breset');br.hidden=!d.reset_enabled;br.disabled=busy||d.shift_m==null;
 br.onclick=()=>send('reset_shift',0,'Smenani tiklaymi?','Hozirgi '+fmt(d.shift_m)+' m "Oxirgi smenalar"ga yoziladi.\nPaneldagi PRODUT va EFFICIENCY 0 dan boshlanadi.',true);
 const sv=v=>v===''||v==null?'—':esc(v);
 $('sh').querySelector('tbody').innerHTML=d.shifts&&d.shifts.length?d.shifts.map(s=>`<tr><td>${esc(s.tugadi.slice(5,16))}</td><td>${s.boshlandi?esc(s.boshlandi.slice(11,16)):'—'}</td><td class="r"><b>${sv(s.toqildi_m)}</b></td><td class="r">${s.ishladi_daq?esc(s.ishladi_daq)+' daq':'—'}</td><td class="r">${s.toxtadi_daq?esc(s.toxtadi_daq)+' daq':'—'}</td><td class="r">${sv(s.toxtashlar_soni)}</td><td class="r">${sv(s.ortacha_m_soat)}</td></tr>`).join(''):'<tr><td colspan="7" style="color:var(--mut)">Hali yozuv yo\'q</td></tr>';

 $('ev').innerHTML=d.events&&d.events.length?d.events.map(e=>`<li><time>${esc(e.time.slice(11,19))}</time><i class="${evColor(e.event)}"></i><span>${esc(e.event)}</span></li>`).join(''):'<li style="color:var(--mut)">Hali voqea yo\'q</li>';
 if(document.querySelector('details').open)$('raw').innerHTML=Object.entries(d.nonzero||{}).map(([k,v])=>`<div><span>${esc(k)}</span><b>${esc(v)}</b></div>`).join('');
}

async function tick(){
 try{last=await (await fetch('/api/data',{cache:'no-store'})).json();}
 catch(e){last={ok:false,error:"Server bilan aloqa yo'q",time:''};}
 render();}
window.addEventListener('resize',()=>last&&last.ok&&chart(last.chart));
tick();setInterval(tick,3000);
</script></body></html>"""

auth_fails = {}

class Handler(BaseHTTPRequestHandler):
    remote = False
    def log_message(self, *a): pass
    def client_id(self):
        return self.headers.get("Cf-Connecting-IP") or self.client_address[0]
    def authed(self):
        """Faqat tashqi port uchun. Login fayli yo'q bo'lsa -- hech kimni kiritmaydi."""
        ip = self.client_id()
        n, t = auth_fails.get(ip, (0, 0))
        if n >= AUTH_MAX_FAILS and time.time() - t < AUTH_BLOCK_SEC:
            return False
        try:
            want = open(AUTH_FILE).read().strip().encode()
        except OSError:
            return False
        if not want or b":" not in want:
            return False
        h = self.headers.get("Authorization", "")
        got = b""
        if h.startswith("Basic "):
            try:
                got = base64.b64decode(h[6:].strip())
            except Exception:
                got = b""
        if got and hmac.compare_digest(got, want):
            auth_fails.pop(ip, None)
            return True
        if got:
            auth_fails[ip] = (n + 1 if time.time() - t < AUTH_BLOCK_SEC else 1, time.time())
        return False
    def deny_auth(self):
        try:
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="Stanok monitor", charset="UTF-8"')
            self.send_header("Content-Length", "0"); self.end_headers()
        except (BrokenPipeError, ConnectionResetError):
            pass
    def send(self, body, ctype):
        try:
            self.send_response(200); self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body))); self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass
    def do_GET(self):
        if self.remote and not self.authed():
            self.deny_auth(); return
        if self.path.startswith("/api/data"):
            with lock:
                d = dict(state)
            if self.remote:
                d.update(readonly=True, stop_enabled=False, speed_enabled=False, reset_enabled=False)
            body = json.dumps(d).encode()
            self.send(body, "application/json")
        else:
            self.send(PAGE.encode(), "text/html; charset=utf-8")
    def do_POST(self):
        if not self.path.startswith("/api/control"):
            self.send_error(404); return
        if self.remote:
            if not self.authed():
                self.deny_auth(); return
            body = json.dumps({"message": "Tashqaridan boshqarish o'chirilgan. Faqat zavod tarmog'idan."}).encode()
            try:
                self.send_response(403); self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body))); self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass
            return
        try:
            n = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(min(n, 1024)) or b"{}")
            code, msg = control(req.get("action"), int(req.get("value")), req.get("pin", ""),
                                self.client_address[0])
        except Exception as e:
            code, msg = 400, f"Xato: {e}"
        body = json.dumps({"message": msg}).encode()
        try:
            self.send_response(code); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body))); self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

class RemoteHandler(Handler):
    remote = True

# ---------- systemd watchdog ----------
# Poller aylanib tursa va veb-server javob bersa, systemd ga "tirikman" deymiz. Biror qism
# qotib qolsa xabar to'xtaydi va systemd (WatchdogSec) dasturni o'zi qayta ishga tushiradi.
alive = {"poll": time.time()}

def sd_notify(msg):
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return
    if addr.startswith("@"):
        addr = "\0" + addr[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
            s.connect(addr); s.sendall(msg.encode())
    except Exception:
        pass

def watchdog():
    import urllib.request
    while True:
        time.sleep(20)
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{WEB_PORT}/api/data", timeout=10).read()
            web_ok = True
        except Exception:
            web_ok = False
        if web_ok and time.time() - alive["poll"] < 120:
            sd_notify("WATCHDOG=1")

if __name__ == "__main__":
    threading.Thread(target=poller, daemon=True).start()
    threading.Thread(target=watchdog, daemon=True).start()
    threading.Timer(2, sd_notify, args=("READY=1",)).start()
    print(f"Veb-dashboard: http://localhost:{WEB_PORT}  (tarmoqdan: http://<server-ip>:{WEB_PORT})")
    if REMOTE_PORT:
        rs = ThreadingHTTPServer(("127.0.0.1", REMOTE_PORT), RemoteHandler)
        threading.Thread(target=rs.serve_forever, daemon=True).start()
        print(f"Tashqi (faqat ko'rish, login bilan): 127.0.0.1:{REMOTE_PORT}")
    ThreadingHTTPServer(("0.0.0.0", WEB_PORT), Handler).serve_forever()
