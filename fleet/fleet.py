#!/usr/bin/env python3
# Sex monitori: 40 tagacha stanokni (Yongming SBY-850x6G / Delta DVP32ES2) bitta ekranda kuzatish.
#
# FAQAT O'QIYDI. Bu dastur PLK'ga hech narsa yozmaydi: Gateway sinfida yozish funksiyasi yo'q.
# Boshqaruv (to'xtatish, tezlik, LIGHT/FAN) har stanokning o'z sahifasida (stanok_web.py) qoladi.
#
# Stanoklar ro'yxati: looms.json. Har stanok ikki usulda ulanadi:
#   "source": "modbus"  -- o'zining DR134 shlyuzi orqali to'g'ridan-to'g'ri (host, port, station)
#   "source": "monitor" -- shu stanokni allaqachon o'qiyotgan stanok_web.py dan (url).
#                          RS485 shinasiga ikkinchi o'quvchi qo'shilmaydi.
# Ma'lumotlar: data/fleet.db (SQLite): daqiqalik ko'rsatkichlar, voqealar, smenalar.
# Faqat Python stdlib.  Ishga tushirish:  python3 fleet.py   ->  http://<ip>:8090

import socket, struct, json, time, threading, os, csv, io, hmac, base64, sqlite3, re, hashlib, secrets, sys
import urllib.request
from collections import deque
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.environ.get("FLEET_CONFIG", os.path.join(HERE, "looms.json"))
DATA_DIR = os.path.expanduser(os.environ.get("FLEET_DATA", os.path.join(HERE, "data")))
WEB_PORT = int(os.environ.get("FLEET_PORT", "8090"))
# Tashqi (internet) kirish: faqat 127.0.0.1 da, login-parol bilan (Cloudflare tunnel uchun).
REMOTE_PORT = int(os.environ.get("FLEET_REMOTE_PORT", "0"))
AUTH_FILE = os.path.expanduser(os.environ.get("FLEET_AUTH_FILE", "~/stanok/web_auth"))
AUTH_MAX_FAILS, AUTH_BLOCK_SEC = 10, 900
POLL_SEC = 3
OFFLINE_AFTER = 20      # shuncha soniya javob bo'lmasa -> "Aloqa yo'q"
SPEED_WINDOW = 180      # m/daq shu oraliqda hisoblanadi (D528 sekin yangilanadi)
STOP_AFTER = 150
RUN_FREQ = 100          # D89..D99 o'rtachasi (Hz x100) shundan katta -> ishlayapti
RESET_CONFIRM = 2
# Super admin: parol xeshi (PBKDF2) shu faylda. Fayl yo'q bo'lsa, sozlamalar sahifasi yopiq.
ADMIN_FILE = os.path.expanduser(os.environ.get("FLEET_ADMIN_FILE", "~/stanok/fleet_admin"))
ADMIN_SESSION_SEC = 8 * 3600
ADMIN_MAX_FAILS, ADMIN_BLOCK_SEC = 5, 900
DEFAULT_SHIFTS = [{"name": "1-smena", "start": "07:00"}, {"name": "2-smena", "start": "19:00"}]

# =====================================================================================
# Modbus ASCII (DR134 orqali). FAQAT O'QISH: FC01 (M/Y), FC02 (X), FC03 (D).
# =====================================================================================
def lrc(b): return (-sum(b)) & 0xFF

class Gateway:
    READ_FUNCS = (0x01, 0x02, 0x03)

    def __init__(self, host, port, station=1):
        self.host, self.port, self.station = host, int(port), int(station)
        self.lock = threading.Lock()     # bitta RS485 shinasida bir vaqtda bitta so'rov

    def read(self, start, count, fn=0x03, base=0x1000):
        if fn not in self.READ_FUNCS:
            raise ValueError("faqat o'qish funksiyalari ruxsat etilgan")
        need = count * 2 if fn == 0x03 else (count + 7) // 8
        req = bytes([self.station, fn]) + struct.pack(">HH", base + start, count)
        fr = b":" + req.hex().upper().encode() + f"{lrc(req):02X}".encode() + b"\r\n"
        with self.lock:
            s = socket.create_connection((self.host, self.port), timeout=3)
            try:
                s.sendall(fr)
                buf, deadline = b"", time.time() + 2.0
                while time.time() < deadline:
                    s.settimeout(max(0.05, deadline - time.time()))
                    try:
                        c = s.recv(512)
                    except socket.timeout:
                        break
                    if not c:
                        break
                    buf += c
                    while b"\r\n" in buf:
                        line, buf = buf.split(b"\r\n", 1)
                        if b":" not in line:
                            continue
                        try:
                            raw = bytes.fromhex(line[line.rfind(b":") + 1:].decode())
                        except ValueError:
                            continue
                        # DR134 javobni hamma mijozga tarqatadi -- faqat bizning so'rovga mosini olamiz
                        if len(raw) < 4 or lrc(raw[:-1]) != raw[-1] or raw[0] != self.station:
                            continue
                        if raw[1] == fn | 0x80:
                            raise RuntimeError(f"Modbus exception {raw[2]}")
                        if raw[1] == fn and raw[2] == need:
                            data = raw[3:3 + need]
                            if fn != 0x03:
                                return {start + i: (data[i // 8] >> (i % 8)) & 1 for i in range(count)}
                            return {start + i: struct.unpack(">H", data[2*i:2*i+2])[0]
                                    for i in range(count)}
            finally:
                s.close()
        raise TimeoutError(f"{self.host}: javob yo'q")

# =====================================================================================
# SBY-850x6G profili. Manzillar 1-stanokda joyida tasdiqlangan (stanok_web.py bilan bir xil).
# Yangi stanok qo'shishdan oldin README'dagi tekshiruvni bajaring.
# =====================================================================================
SBY_BLOCKS = [(0, 43), (43, 61), (104, 53), (157, 57), (214, 37), (480, 31), (511, 39), (2000, 29)]

def u32(regs, lo):
    return (regs[lo + 1] << 16) | regs[lo] if lo in regs and lo + 1 in regs else None

def sby_read_regs(gw):
    regs, errors = {}, 0
    for start, cnt in SBY_BLOCKS:
        for _ in range(2):
            try:
                regs.update(gw.read(start, cnt)); break
            except Exception:
                time.sleep(0.2)
        else:
            errors += 1
    if errors == len(SBY_BLOCKS):
        raise ConnectionError("PLK javob bermayapti")
    if regs.get(41) and 480 in regs and regs[41] // 100 != regs[480]:
        raise ValueError(f"nomuvofiq o'qish (D41={regs[41]}, D480={regs[480]})")
    return regs

def sby_read_bits(gw):
    bits = {}
    for start, cnt in ((0, 96), (512, 24)):
        try:
            bits.update(gw.read(start, cnt, fn=0x01, base=0x0800))
        except Exception:
            pass
    try:
        y = gw.read(0, 16, fn=0x01, base=0x0500)
        bits.update({f"Y{oct(k)[2:]}": v for k, v in y.items()})
    except Exception:
        pass
    try:
        x = gw.read(0, 8, fn=0x02, base=0x0400)
        bits.update({f"X{k}": v for k, v in x.items()})
    except Exception:
        pass
    return bits

def sby_stop_reason(bits):
    if bits.get(29) == 1:
        return "Barabanda ip tugadi (WEFT END) ?"
    if bits.get("X3") == 1 or bits.get(20) == 1:
        return "Asos ipi uzildi (WARP BREAK) ?"
    if bits.get(40) == 1:
        return "Qo'lda to'xtatilgan yoki avariya (M40)"
    alarm = [y for y in ("Y0", "Y2", "Y16") if bits.get(y) == 1]
    if alarm:
        return f"Noma'lum ogohlantirish ({', '.join(alarm)})"
    return None

FIELDS = ("running", "hz", "hz_actual", "m_min", "ppm", "eff_panel", "shift", "shift_m",
          "total_m", "weft_den", "main_load", "reason", "bypass", "light", "fan")

class ModbusSource:
    def __init__(self, cfg):
        self.gw = Gateway(cfg["host"], cfg.get("port", 8234), cfg.get("station", 1))
        self.history = deque()
        self.last_total, self.last_change = None, time.time()

    def describe(self):
        return f"DR134 {self.gw.host}:{self.gw.port}"

    def poll(self):
        t = time.time()
        regs = sby_read_regs(self.gw)
        total = u32(regs, 528)
        if total is not None:
            self.history.append((t, total))
            while self.history and self.history[0][0] < t - SPEED_WINDOW:
                self.history.popleft()
            if self.last_total is None or total != self.last_total:
                self.last_change = t
            self.last_total = total
        m_min = None
        if len(self.history) >= 2 and self.history[-1][0] - self.history[0][0] >= 60:
            (t0, l0), (t1, l1) = self.history[0], self.history[-1]
            m_min = round((l1 - l0) / 100 / ((t1 - t0) / 60), 2)
        bits = sby_read_bits(self.gw)
        fr = [regs[k] for k in range(89, 100) if k in regs]
        freq = sum(fr) / len(fr) if fr else None
        running = freq > RUN_FREQ if freq is not None else (t - self.last_change) < STOP_AFTER
        weft = regs.get(491)
        return {
            "running": running,
            "hz": regs[481] / 100 if 481 in regs else None,
            "hz_actual": round(freq / 100, 2) if freq is not None else None,
            "m_min": m_min if running else 0.0,
            "ppm": regs.get(218),
            "eff_panel": regs[2018] / 10 if 2018 in regs else None,
            "shift": regs.get(536),
            "shift_m": round(regs[516] / 100, 2) if 516 in regs else None,
            "total_m": round(total / 100, 2) if total is not None else None,
            "weft_den": round(weft / 10, 1) if weft is not None else None,
            "main_load": regs[66] / 10 if 66 in regs else None,
            "reason": None if running else sby_stop_reason(bits),
            "bypass": [n for m, n in ((518, "WARP STOP [7]"), (519, "WEFT BRE STOP [8]"),
                                      (520, "WEFT END STOP [9]")) if bits.get(m) == 1]
                      if 518 in bits else None,
            "light": bits.get(531),
            "fan": bits.get(16),
        }

class MonitorSource:
    """Stanokni allaqachon o'qiyotgan stanok_web.py'dan oladi (PLK'ga qo'shimcha so'rov yo'q)."""
    def __init__(self, cfg):
        self.url = cfg["url"].rstrip("/") + "/api/data"

    def describe(self):
        return f"monitor {self.url}"

    def poll(self):
        with urllib.request.urlopen(self.url, timeout=5) as r:
            d = json.load(r)
        if not d.get("ok"):
            raise ConnectionError(d.get("error") or "monitor xato qaytardi")
        age = time.time() - time.mktime(time.strptime(d["time"], "%Y-%m-%d %H:%M:%S"))
        if age > 30:
            raise ConnectionError(f"monitor ma'lumoti eski ({int(age)} s)")
        out = {k: d.get(k) for k in FIELDS}
        out["running"] = bool(d.get("running"))
        return out

# =====================================================================================
# Ma'lumotlar bazasi (SQLite)
# =====================================================================================
class DB:
    def __init__(self, path):
        self.c = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.lock = threading.Lock()
        with self.lock:
            self.c.execute("PRAGMA journal_mode=WAL")
            self.c.executescript("""
            CREATE TABLE IF NOT EXISTS readings(
                loom INTEGER, ts TEXT, running INTEGER, hz REAL, hz_actual REAL, m_min REAL,
                shift_m REAL, total_m REAL, eff_panel REAL, ppm INTEGER, main_load REAL);
            CREATE INDEX IF NOT EXISTS readings_ts ON readings(ts, loom);
            CREATE TABLE IF NOT EXISTS events(loom INTEGER, ts TEXT, kind TEXT, event TEXT);
            CREATE INDEX IF NOT EXISTS events_ts ON events(ts, loom);
            CREATE TABLE IF NOT EXISTS shifts(
                loom INTEGER, ended TEXT, started TEXT, shift INTEGER, metres REAL,
                run_min INTEGER, stop_min INTEGER, stops INTEGER);
            CREATE TABLE IF NOT EXISTS settings_log(ts TEXT, ip TEXT, change TEXT);
            """)
            # Rejali vaqt stanok monitoringga ulangan paytdan hisoblanadi (undan oldin ma'lumot yo'q)
            self.first_seen = dict(self.c.execute(
                "SELECT loom, MIN(ts) FROM readings GROUP BY loom").fetchall())

    def q(self, sql, args=()):
        with self.lock:
            return self.c.execute(sql, args).fetchall()

# =====================================================================================
# Bitta stanok: o'qish tsikli, holat, voqealar, smena hisobi
# =====================================================================================
def now_str(t=None):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t))

class Loom:
    def __init__(self, cfg, db):
        self.id = int(cfg["id"])
        self.name = cfg.get("name") or f"{self.id}-stanok"
        self.enabled = bool(cfg.get("enabled", True))
        self.panel_port = cfg.get("panel_port")
        self.db = db
        src = cfg.get("source", "modbus")
        self.source = MonitorSource(cfg) if src == "monitor" else ModbusSource(cfg) if "host" in cfg else None
        if self.source is None:
            self.enabled = False
        self.lock = threading.Lock()
        self.status = "offline" if self.enabled else "disabled"
        self.since = time.time()
        self.data, self.error, self.last_ok = {}, ("hali o'qilmadi" if self.enabled else None), 0
        self.chart = deque(maxlen=1200)
        self.events = deque(maxlen=50)
        for ts, kind, ev in reversed(db.q(
                "SELECT ts, kind, event FROM events WHERE loom=? ORDER BY rowid DESC LIMIT 50", (self.id,))):
            self.events.appendleft({"time": ts, "kind": kind, "event": ev})
        self.shift_file = os.path.join(DATA_DIR, f"shift_{self.id}.json")
        self.sh = self.load_shift()

    # --- smena holati (qayta ishga tushganda yo'qolmasin) ---
    def load_shift(self):
        try:
            with open(self.shift_file) as f:
                return json.load(f)
        except Exception:
            return {"start": None, "run_s": 0.0, "stop_s": 0.0, "stops": 0, "last_m": None, "shift": None}

    def save_shift(self):
        try:
            with open(self.shift_file + ".tmp", "w") as f:
                json.dump(self.sh, f)
            os.replace(self.shift_file + ".tmp", self.shift_file)
        except Exception:
            pass

    def event(self, text, kind):
        ts = now_str()
        self.events.appendleft({"time": ts, "kind": kind, "event": text})
        self.db.q("INSERT INTO events VALUES (?,?,?,?)", (self.id, ts, kind, text))

    def set_state(self, status, data, error):
        with self.lock:
            if status != self.status:
                self.status, self.since = status, time.time()
            if data is not None:
                self.data = data
            self.error = error

    def run(self):
        running = last_reason = last_bypass = last_min = link_ok = None
        last_t, last_save, zero_count = time.time(), 0, 0
        while True:
            t = time.time()
            try:
                d = self.source.poll()
                now_running = bool(d["running"])
                if link_ok is False:
                    self.event("Aloqa tiklandi", "link")
                link_ok, self.last_ok = True, t
                # --- smena hisobi (panel smenani tiklaganda D516 keskin tushadi) ---
                sh = self.sh
                dt = min(t - last_t, 30); last_t = t
                sh["run_s" if now_running else "stop_s"] += dt
                if running and not now_running:
                    sh["stops"] += 1
                if sh["start"] is None:
                    sh["start"] = now_str(t)
                sm = d.get("shift_m")
                if sm is not None:
                    prev = sh["last_m"] or 0
                    if prev >= 1.0 and sm < prev - 1.0 and sm < 5:
                        zero_count += 1
                        if zero_count >= RESET_CONFIRM:
                            self.db.q("INSERT INTO shifts VALUES (?,?,?,?,?,?,?,?)", (
                                self.id, now_str(t), sh["start"], sh["shift"], sh["last_m"],
                                round(sh["run_s"] / 60), round(sh["stop_s"] / 60), sh["stops"]))
                            self.event(f"Smena tugadi: {sh['last_m']} m to'qilgan", "shift")
                            self.sh = sh = {"start": now_str(t), "run_s": 0.0, "stop_s": 0.0, "stops": 0,
                                            "last_m": sm, "shift": d.get("shift")}
                            zero_count, last_save = 0, 0
                    else:
                        zero_count = 0
                        sh["last_m"], sh["shift"] = sm, d.get("shift")
                if t - last_save > 30:
                    self.save_shift(); last_save = t
                tot = sh["run_s"] + sh["stop_s"]
                d["eff_calc"] = round(sh["run_s"] / tot * 100, 1) if tot >= 60 else None
                d["shift_info"] = {"start": sh["start"], "run_min": round(sh["run_s"] / 60),
                                   "stop_min": round(sh["stop_s"] / 60), "stops": sh["stops"]}
                d["m_hour"] = round(d["m_min"] * 60, 1) if (d.get("m_min") and now_running) else 0.0
                # --- voqealar ---
                if running is not None and now_running != running:
                    self.event("Ishga tushdi" if now_running else "To'xtadi",
                               "start" if now_running else "stop")
                if not now_running and d.get("reason") and d["reason"] != last_reason:
                    self.event(d["reason"], "reason")
                last_reason = d.get("reason")
                running = now_running
                bp = d.get("bypass")
                if bp is not None:
                    if last_bypass is not None:
                        for n in set(bp) - set(last_bypass):
                            self.event(f"Himoya O'CHIRILDI: {n}", "bypass")
                        for n in set(last_bypass) - set(bp):
                            self.event(f"Himoya yoqildi: {n}", "bypass")
                    last_bypass = bp
                status = ("warn" if bp else "run") if now_running else "stop"
                self.set_state(status, d, None)
                self.chart.append((int(t), d.get("m_min") or 0))
                minute = time.strftime("%Y-%m-%d %H:%M", time.localtime(t))
                if minute != last_min:
                    last_min = minute
                    self.db.q("INSERT INTO readings VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
                        self.id, now_str(t), int(now_running), d.get("hz"), d.get("hz_actual"),
                        d.get("m_min"), d.get("shift_m"), d.get("total_m"), d.get("eff_panel"),
                        d.get("ppm"), d.get("main_load")))
                    self.db.first_seen.setdefault(self.id, now_str(t))
            except Exception as e:
                err = str(e) or type(e).__name__
                if t - self.last_ok > OFFLINE_AFTER:
                    if link_ok:
                        self.event(f"Aloqa uzildi: {err}", "link")
                    link_ok = False
                    self.set_state("offline", None, err)
                else:
                    with self.lock:
                        self.error = err
            alive[self.id] = time.time()
            time.sleep(max(0.5, POLL_SEC - (time.time() - t)))

    def tile(self):
        with self.lock:
            d, status, since, err = self.data, self.status, self.since, self.error
        live = status in ("run", "warn", "stop")
        return {
            "id": self.id, "name": self.name, "status": status, "since": since,
            "error": err if status == "offline" else None,
            "m_hour": d.get("m_hour") if live else None,
            "hz": d.get("hz") if live else None,
            "shift_m": d.get("shift_m") if live else None,
            "eff": d.get("eff_panel") if live else None,
            "reason": d.get("reason") if status == "stop" else None,
            "bypass": d.get("bypass") if live else None,
            "main_load": d.get("main_load") if live else None,
        }

    def detail(self):
        with self.lock:
            d = dict(self.data)
        out = self.tile()
        out.update(data=d if out["status"] in ("run", "warn", "stop") else None,
                   source=self.source.describe() if self.source else None,
                   panel_port=self.panel_port, last_ok=self.last_ok or None,
                   chart=[c for c in self.chart if c[0] > time.time() - 3600],
                   events=list(self.events),
                   shifts=[dict(zip(("ended", "started", "shift", "metres", "run_min", "stop_min", "stops"), r))
                           for r in self.db.q("SELECT ended, started, shift, metres, run_min, stop_min, stops "
                                              "FROM shifts WHERE loom=? ORDER BY rowid DESC LIMIT 10", (self.id,))])
        return out

# =====================================================================================
# Smena sozlamalari (super admin o'zgartiradi). Har smena nomi va boshlanish vaqti bilan;
# tugashi = keyingi smenaning boshlanishi, shuning uchun smenalar doim 24 soatni to'liq qoplaydi.
# Birinchi smena ishlab chiqarish kunini boshlaydi (tungi smena boshlangan kunga yoziladi).
# =====================================================================================
settings_lock = threading.Lock()
SETTINGS = {"shifts": [dict(x) for x in DEFAULT_SHIFTS]}

def hm(v):
    m = re.fullmatch(r"([01]\d|2[0-3]):([0-5]\d)", str(v or "").strip())
    if not m:
        raise ValueError(f"vaqt noto'g'ri: {v!r} (SS:DD kerak, masalan 07:00)")
    return int(m.group(1)) * 60 + int(m.group(2))

def validate_shifts(shifts):
    if not isinstance(shifts, list) or not 1 <= len(shifts) <= 4:
        raise ValueError("smenalar soni 1 dan 4 gacha bo'lishi kerak")
    out = []
    for x in shifts:
        if not isinstance(x, dict):
            raise ValueError("smena ma'lumoti noto'g'ri")
        name = str(x.get("name", "")).strip()
        if not 1 <= len(name) <= 30:
            raise ValueError("smena nomi 1–30 belgi bo'lishi kerak")
        out.append({"name": name, "start": "%02d:%02d" % divmod(hm(x.get("start")), 60)})
    if len({x["start"] for x in out}) != len(out):
        raise ValueError("ikki smena bir vaqtda boshlana olmaydi")
    if len({x["name"] for x in out}) != len(out):
        raise ValueError("smena nomlari takrorlanmasligi kerak")
    first = hm(out[0]["start"])
    return [out[0]] + sorted(out[1:], key=lambda x: (hm(x["start"]) - first) % 1440)

def shift_table():
    """[(nom, "SS:DD" boshlanish, "SS:DD" tugash, kun boshidan siljish daq, davomiylik daq)]"""
    sh = SETTINGS["shifts"]
    ds = hm(sh[0]["start"])
    offs = [(hm(x["start"]) - ds) % 1440 for x in sh] + [1440]
    return [(x["name"], x["start"], sh[(i + 1) % len(sh)]["start"], offs[i], offs[i + 1] - offs[i])
            for i, x in enumerate(sh)]

def production_day(t=None):
    with settings_lock:
        ds = hm(SETTINGS["shifts"][0]["start"])
    return (datetime.fromtimestamp(t or time.time()) - timedelta(minutes=ds)).date().isoformat()

def current_shift(t=None):
    dt = datetime.fromtimestamp(t or time.time())
    with settings_lock:
        tab = shift_table()
    off = (dt.hour * 60 + dt.minute - hm(tab[0][1])) % 1440
    for name, start, end, o, ln in tab:
        if o <= off < o + ln:
            return {"name": name, "start": start, "end": end}

def settings_path():
    return os.path.join(DATA_DIR, "settings.json")

def load_settings():
    try:
        with open(settings_path(), encoding="utf-8") as f:
            shifts = validate_shifts(json.load(f)["shifts"])
    except FileNotFoundError:
        return
    except Exception as e:
        print(f"settings.json o'qilmadi ({e}), odatiy smenalar ishlatiladi", flush=True)
        return
    with settings_lock:
        SETTINGS["shifts"] = shifts

def save_shifts(shifts, ip):
    shifts = validate_shifts(shifts)
    with settings_lock:
        old = SETTINGS["shifts"]
        tmp = settings_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"shifts": shifts}, f, ensure_ascii=False, indent=1)
        os.replace(tmp, settings_path())
        SETTINGS["shifts"] = shifts
    fmt = lambda xs: ", ".join(f"{x['name']} {x['start']}" for x in xs)
    admin_log(ip, f"Smenalar o'zgartirildi: [{fmt(old)}] -> [{fmt(shifts)}]")
    _today_cache["t"] = 0
    return shifts

def admin_log(ip, text):
    DBH.q("INSERT INTO settings_log VALUES (?,?,?)", (now_str(), ip, text))

# ---------- Super admin paroli va sessiyalar ----------
admin_sessions, admin_fails = {}, {}

def _pw_hash(pw, salt, it=200_000):
    return hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, it).hex()

def set_admin_password(pw):
    if len(pw) < 8:
        raise ValueError("parol kamida 8 belgi bo'lishi kerak")
    salt = secrets.token_bytes(16)
    os.makedirs(os.path.dirname(ADMIN_FILE), exist_ok=True)
    fd = os.open(ADMIN_FILE + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(f"pbkdf2_sha256$200000${salt.hex()}${_pw_hash(pw, salt)}\n")
    os.replace(ADMIN_FILE + ".tmp", ADMIN_FILE)
    admin_sessions.clear()      # parol o'zgarsa, eski kirishlar bekor

def admin_configured():
    return os.path.isfile(ADMIN_FILE)

def check_admin_password(pw):
    try:
        algo, it, salt, want = open(ADMIN_FILE).read().strip().split("$")
        return algo == "pbkdf2_sha256" and hmac.compare_digest(_pw_hash(pw, bytes.fromhex(salt), int(it)), want)
    except (OSError, ValueError):
        return False

# =====================================================================================
# Hisobot: ishlab chiqarish kuni (birinchi smena boshidan 24 soat) va smenalar bo'yicha.
# Rejali vaqt = kun yoki smena oralig'i (hozirgacha, stanok monitoringga ulangandan beri).
# O'qish bo'lmagan daqiqalar "aloqa yo'q" -- elektr o'chishi ham shunday ko'rinadi -- va
# yo'qotish sifatida hisoblanadi (zavod qarori: rejali to'xtash yo'q, elektr o'chishi yo'qotish).
# =====================================================================================
TSF = "%Y-%m-%d %H:%M:%S"

def _minutes(a, b):
    return int((datetime.strptime(b, TSF) - datetime.strptime(a, TSF)).total_seconds() // 60)

def _agg(samples, planned):
    n = len(samples)
    run = sum(r[2] for r in samples)
    tot = [r[3] for r in samples if r[3] is not None]
    mm = [r[4] for r in samples if r[2] and r[4]]
    ld = [r[5] for r in samples if r[2] and r[5]]
    planned = max(planned, n)
    return {"metres": round(max(tot) - min(tot), 1) if tot else None,
            "run_min": run, "stop_min": n - run, "offline_min": planned - n, "planned_min": planned,
            "eff": round(run / planned * 100, 1) if planned else None,
            "m_hour": round(sum(mm) / len(mm) * 60, 1) if mm else None,
            "main_load": round(sum(ld) / len(ld), 1) if ld else None}

def _sum(items):
    t = {"metres": 0.0, "run_min": 0, "stop_min": 0, "offline_min": 0, "planned_min": 0}
    for it in items:
        for k in t:
            t[k] += it.get(k) or 0
    t["metres"] = round(t["metres"], 1)
    t["eff"] = round(t["run_min"] / t["planned_min"] * 100, 1) if t["planned_min"] else None
    return t

def day_report(db, looms, day):
    d0 = datetime.strptime(day, "%Y-%m-%d")
    with settings_lock:
        tab = shift_table()
    a = d0 + timedelta(minutes=hm(tab[0][1]))
    sa, sb = a.strftime(TSF), (a + timedelta(days=1)).strftime(TSF)
    wins = [((a + timedelta(minutes=o)).strftime(TSF), (a + timedelta(minutes=o + ln)).strftime(TSF))
            for _, _, _, o, ln in tab]
    now = now_str()
    by_loom = {}
    for r in db.q("SELECT loom, ts, running, total_m, m_min, main_load FROM readings "
                  "WHERE ts>=? AND ts<? ORDER BY loom, ts", (sa, sb)):
        by_loom.setdefault(r[0], []).append(r)
    stops = dict(db.q("SELECT loom, COUNT(*) FROM events WHERE kind='stop' AND ts>=? AND ts<? "
                      "GROUP BY loom", (sa, sb)))
    reasons = {}
    for loom, ev, n in db.q("SELECT loom, event, COUNT(*) FROM events WHERE kind='reason' "
                            "AND ts>=? AND ts<? GROUP BY loom, event", (sa, sb)):
        reasons[ev] = reasons.get(ev, 0) + n

    def planned(lid, w0, w1):
        first = db.first_seen.get(lid)
        if not first:
            return 0
        start, end = max(w0, first[:16] + ":00"), min(w1, now)   # daqiqa boshidan
        return max(0, _minutes(start, end)) if end > start else 0

    out = []
    for lm in looms:
        samples = by_loom.get(lm.id, [])
        if not lm.enabled and not samples:
            continue
        item = _agg(samples, planned(lm.id, sa, sb))
        item.update(id=lm.id, name=lm.name,
                    stops=stops.get(lm.id, 0) if item["planned_min"] else None,
                    by_shift=[_agg([r for r in samples if w0 <= r[1] < w1], planned(lm.id, w0, w1))
                              for w0, w1 in wins])
        if not item["planned_min"]:
            item.update(metres=None, run_min=None, stop_min=None, offline_min=None, eff=None)
        out.append(item)
    total = _sum(out)
    total["stops"] = sum(x["stops"] or 0 for x in out)
    shifts = []
    for i, (name, start, end, _, _) in enumerate(tab):
        st = _sum([x["by_shift"][i] for x in out])
        st.update(name=name, start=start, end=end)
        shifts.append(st)
    return {"date": day, "window": [sa, sb], "shifts": shifts, "looms": out, "total": total,
            "reasons": sorted(({"reason": k, "count": v} for k, v in reasons.items()),
                              key=lambda x: -x["count"])}

def report_csv(rep):
    f = io.StringIO()
    w = csv.writer(f, delimiter=";")
    w.writerow(["Ishlab chiqarish kuni", rep["date"], "oraliq", f"{rep['window'][0]} — {rep['window'][1]}"])
    sh = [s["name"] for s in rep["shifts"]]
    w.writerow(["Stanok", "To'qildi (m)"] + [f"{n} (m)" for n in sh] +
               ["Ishladi (daq)", "To'xtadi (daq)", "Aloqa yo'q / elektr (daq)", "Rejali vaqt (daq)",
                "To'xtashlar", "Ishlash ulushi (%)", "O'rtacha tezlik (m/soat)", "Dvigatel toki (A)"])
    num = lambda v: "" if v is None else str(v).replace(".", ",")
    for r in rep["looms"]:
        w.writerow([r["name"], num(r["metres"])] + [num(b["metres"]) for b in r["by_shift"]] +
                   [num(r["run_min"]), num(r["stop_min"]), num(r["offline_min"]), num(r["planned_min"]),
                    num(r["stops"]), num(r["eff"]), num(r["m_hour"]), num(r["main_load"])])
    t = rep["total"]
    w.writerow(["JAMI", num(t["metres"])] + [num(s["metres"]) for s in rep["shifts"]] +
               [num(t["run_min"]), num(t["stop_min"]), num(t["offline_min"]), num(t["planned_min"]),
                num(t["stops"]), num(t["eff"]), "", ""])
    return ("\ufeff" + f.getvalue()).encode("utf-8")   # BOM: Excel o'zbekcha harflarni to'g'ri ochadi

# =====================================================================================
# Veb-server
# =====================================================================================
LOOMS, DBH = [], None
alive = {}
_today_cache = {"t": 0, "v": None}

def fleet_summary(remote):
    tiles = [lm.tile() for lm in LOOMS]
    cnt = {s: sum(1 for x in tiles if x["status"] == s) for s in ("run", "warn", "stop", "offline", "disabled")}
    live = [x for x in tiles if x["status"] in ("run", "warn")]
    effs = [x["eff"] for x in tiles if x["eff"] is not None]
    if time.time() - _today_cache["t"] > 60:
        _today_cache.update(t=time.time(), v=day_report(DBH, LOOMS, production_day())["total"]["metres"])
    return {"time": now_str(), "now": time.time(), "remote": remote, "counts": cnt,
            "prod_day": production_day(), "shift": current_shift(),
            "m_hour": round(sum(x["m_hour"] or 0 for x in live), 1),
            "today_m": _today_cache["v"],
            "eff_avg": round(sum(effs) / len(effs), 1) if effs else None,
            "looms": tiles}

auth_fails = {}

class Handler(BaseHTTPRequestHandler):
    remote = False

    def log_message(self, *a):
        pass

    def client_id(self):
        return self.headers.get("Cf-Connecting-IP") or self.client_address[0]

    def authed(self):
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
        h, got = self.headers.get("Authorization", ""), b""
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

    def reply(self, code, body, ctype, extra=()):
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            for k, v in extra:
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def json(self, obj, code=200, extra=()):
        self.reply(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json", extra)

    # --- super admin ---
    def admin_token(self):
        for part in self.headers.get("Cookie", "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == "fleet_admin" and v:
                exp = admin_sessions.get(v)
                if exp and exp > time.time():
                    return v
                admin_sessions.pop(v, None)
        return None

    def admin_state(self, logged=None):
        if logged is None:
            logged = self.admin_token() is not None
        d = {"configured": admin_configured(), "logged_in": logged}
        if logged:
            with settings_lock:
                d["shifts"] = [dict(x) for x in SETTINGS["shifts"]]
            d["log"] = [{"time": t, "ip": ip, "change": c} for t, ip, c in
                        DBH.q("SELECT ts, ip, change FROM settings_log ORDER BY rowid DESC LIMIT 30")]
        return d

    def admin_post(self, p):
        ip = self.client_address[0]
        # CSRF himoyasi: brauzer boshqa saytdan bu sarlavhani so'rovsiz yubora olmaydi
        if self.headers.get("X-Requested-With") != "fleet":
            return self.json({"error": "ruxsat yo'q"}, 403)
        try:
            n = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(min(n, 4096)) or b"{}")
        except ValueError:
            return self.json({"error": "so'rov noto'g'ri"}, 400)
        if p == "/api/admin/login":
            if not admin_configured():
                return self.json({"error": "Super admin paroli o'rnatilmagan"}, 503)
            cnt, t = admin_fails.get(ip, (0, 0))
            if cnt >= ADMIN_MAX_FAILS and time.time() - t < ADMIN_BLOCK_SEC:
                return self.json({"error": f"Ko'p noto'g'ri urinish. {int(ADMIN_BLOCK_SEC - (time.time() - t)) // 60 + 1} daqiqa kuting."}, 429)
            if not check_admin_password(str(req.get("password", ""))):
                admin_fails[ip] = (cnt + 1 if time.time() - t < ADMIN_BLOCK_SEC else 1, time.time())
                return self.json({"error": "Parol noto'g'ri"}, 403)
            admin_fails.pop(ip, None)
            tok = secrets.token_urlsafe(32)
            admin_sessions[tok] = time.time() + ADMIN_SESSION_SEC
            admin_log(ip, "Super admin kirdi")
            return self.json(self.admin_state(logged=True), 200, [("Set-Cookie",
                f"fleet_admin={tok}; Path=/api/admin; HttpOnly; SameSite=Strict; Max-Age={ADMIN_SESSION_SEC}")])
        tok = self.admin_token()
        if tok is None:
            return self.json({"error": "Avval super admin sifatida kiring"}, 401)
        if p == "/api/admin/logout":
            admin_sessions.pop(tok, None)
            return self.json({"ok": True}, 200, [("Set-Cookie", "fleet_admin=; Path=/api/admin; Max-Age=0")])
        try:
            if p == "/api/admin/shifts":
                save_shifts(req.get("shifts"), ip)
                return self.json(self.admin_state())
            if p == "/api/admin/password":
                if not check_admin_password(str(req.get("old", ""))):
                    return self.json({"error": "Joriy parol noto'g'ri"}, 403)
                set_admin_password(str(req.get("new", "")))
                admin_log(ip, "Super admin paroli o'zgartirildi")
                return self.json({"ok": True, "message": "Parol o'zgartirildi. Qayta kiring."})
        except ValueError as e:
            return self.json({"error": str(e)}, 400)
        return self.json({"error": "topilmadi"}, 404)

    def do_GET(self):
        if self.remote and not self.authed():
            self.reply(401, b"", "text/plain",
                       [("WWW-Authenticate", 'Basic realm="Sex monitori", charset="UTF-8"')])
            return
        u = urlparse(self.path)
        p, qs = u.path, parse_qs(u.query)
        try:
            if p.startswith("/api/admin"):
                if self.remote:
                    self.json({"error": "Sozlamalar faqat zavod tarmog'idan"}, 403)
                elif p == "/api/admin/state":
                    self.json(self.admin_state())
                else:
                    self.json({"error": "topilmadi"}, 404)
            elif p == "/api/fleet":
                self.json(fleet_summary(self.remote))
            elif m := re.fullmatch(r"/api/loom/(\d+)", p):
                lm = next((x for x in LOOMS if x.id == int(m.group(1))), None)
                if lm is None:
                    self.json({"error": "bunday stanok yo'q"}, 404)
                else:
                    d = lm.detail()
                    if self.remote:
                        d["panel_port"] = None
                    self.json(d)
            elif p in ("/api/report", "/api/report.csv"):
                day = qs.get("date", [production_day()])[0]
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
                    self.json({"error": "sana noto'g'ri"}, 400); return
                rep = day_report(DBH, LOOMS, day)
                if p.endswith(".csv"):
                    self.reply(200, report_csv(rep), "text/csv; charset=utf-8",
                               [("Content-Disposition", f'attachment; filename="sex_hisobot_{day}.csv"')])
                else:
                    self.json(rep)
            elif p in ("/", "/index.html"):
                self.reply(200, PAGE.encode(), "text/html; charset=utf-8")
            else:
                self.reply(404, b"topilmadi", "text/plain")
        except ValueError as e:
            self.json({"error": str(e)}, 400)

    def do_POST(self):
        p = urlparse(self.path).path
        if p.startswith("/api/admin/"):
            if self.remote:
                return self.json({"error": "Sozlamalar faqat zavod tarmog'idan"}, 403)
            return self.admin_post(p)
        # Stanoklarni boshqarish bu yerda yo'q (ADR-0001)
        self.json({"error": "Sex monitori faqat ko'rish uchun. Boshqaruv stanokning o'z sahifasida."}, 405)

class RemoteHandler(Handler):
    remote = True

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
    while True:
        time.sleep(20)
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{WEB_PORT}/api/fleet", timeout=10).read()
            web_ok = True
        except Exception:
            web_ok = False
        polls_ok = all(time.time() - alive.get(lm.id, 0) < 120 for lm in LOOMS if lm.enabled)
        if web_ok and polls_ok:
            sd_notify("WATCHDOG=1")

def load_config():
    with open(CONFIG) as f:
        cfg = json.load(f)
    items = cfg["looms"] if isinstance(cfg, dict) else cfg
    ids = [int(x["id"]) for x in items]
    if len(ids) != len(set(ids)):
        raise SystemExit("looms.json: stanok raqamlari takrorlangan")
    hosts = [(x["host"], int(x.get("port", 8234))) for x in items if x.get("enabled", True) and "host" in x]
    if len(hosts) != len(set(hosts)):
        raise SystemExit("looms.json: ikki stanokka bitta DR134 manzili yozilgan")
    return sorted(items, key=lambda x: int(x["id"]))

PAGE = open(os.path.join(HERE, "fleet.html"), encoding="utf-8").read()

if __name__ == "__main__":
    if sys.argv[1:] == ["--set-admin-password"]:
        import getpass
        pw = getpass.getpass("Yangi super admin paroli (kamida 8 belgi): ")
        if pw != getpass.getpass("Yana bir marta: "):
            raise SystemExit("Parollar mos kelmadi")
        set_admin_password(pw)
        raise SystemExit(f"Saqlandi: {ADMIN_FILE}")
    os.makedirs(DATA_DIR, exist_ok=True)
    load_settings()
    # Avval portni egallaymiz: band bo'lsa, o'qish oqimlari ishga tushmasdan chiqib ketamiz
    server = ThreadingHTTPServer(("0.0.0.0", WEB_PORT), Handler)
    rs = ThreadingHTTPServer(("127.0.0.1", REMOTE_PORT), RemoteHandler) if REMOTE_PORT else None
    DBH = DB(os.path.join(DATA_DIR, "fleet.db"))
    LOOMS = [Loom(c, DBH) for c in load_config()]
    for lm in LOOMS:
        if lm.enabled:
            threading.Thread(target=lm.run, daemon=True, name=f"loom{lm.id}").start()
    threading.Thread(target=watchdog, daemon=True).start()
    threading.Timer(2, sd_notify, args=("READY=1",)).start()
    on = sum(lm.enabled for lm in LOOMS)
    print(f"Sex monitori: {len(LOOMS)} stanok ({on} ulangan). http://localhost:{WEB_PORT}", flush=True)
    if rs:
        threading.Thread(target=rs.serve_forever, daemon=True).start()
        print(f"Tashqi (login bilan): 127.0.0.1:{REMOTE_PORT}", flush=True)
    server.serve_forever()
