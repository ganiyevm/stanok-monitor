#!/usr/bin/env python3
# HMI tugmalarini PLK bitlariga moslash uchun kuzatuvchi. FAQAT O'QIYDI.
# X, Y, M0-M4095 bitlari va D0-D599 registrlarini aylanib o'qiydi, har o'zgarishni
# vaqti bilan ~/stanok_watch.log ga yozadi.
# Birinchi WARMUP soniyada o'zi o'zgaradigan (shovqinli) elementlarni aniqlab,
# keyin ularni yozmaydi -- shunda faqat tugma bosilgandagi o'zgarishlar qoladi.
#
# Ishlatish:  python3 stanok_watch.py            (Ctrl+C bilan to'xtatiladi)

import socket, struct, time, os

PLC_HOST, PLC_PORT, STATION = "192.168.0.7", 8234, 1
LOG = os.path.expanduser("~/stanok_watch.log")
WARMUP = 90

# (nom, Modbus manzil, birinchi raqam, soni, funksiya). FC01/02 ni boshqa monitor ishlatmaydi;
# FC03 bloklari noyob uzunlikda, chunki DR134 javoblarni barcha mijozlarga tarqatadi.
BIT_AREAS = [("X", 0x0400, 0, 64, 0x02), ("Y", 0x0500, 0, 64, 0x01)] + \
            [("M", 0x0800 + i, i, 256, 0x01) for i in range(0, 1536, 256)] + \
            [("M", 0xB000 + i - 1536, i, 256, 0x01) for i in range(1536, 4096, 256)]
REG_AREAS = [(0, 43), (43, 61), (104, 53), (157, 57), (214, 37), (251, 59), (310, 67),
             (377, 71), (448, 73), (521, 79)]

def lrc(b): return (-sum(b)) & 0xFF

def txn(fn, addr, cnt):
    req = bytes([STATION, fn]) + struct.pack(">HH", addr, cnt)
    need = (cnt + 7) // 8 if fn in (1, 2) else cnt * 2
    fr = b":" + req.hex().upper().encode() + f"{lrc(req):02X}".encode() + b"\r\n"
    s = socket.create_connection((PLC_HOST, PLC_PORT), timeout=3)
    try:
        s.sendall(fr)
        buf, deadline = b"", time.time() + 0.8
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
                if len(raw) >= 4 and lrc(raw[:-1]) == raw[-1] and raw[0] == STATION \
                        and raw[1] == fn and raw[2] == need:
                    return raw[3:3 + need]
    finally:
        s.close()
    return None

def name(area, n):
    return f"{area}{oct(n)[2:]}" if area in ("X", "Y") else f"{area}{n}"

def snapshot():
    st = {}
    for area, addr, first, cnt, fn in BIT_AREAS:
        data = txn(fn, addr, cnt)
        if data:
            for i in range(cnt):
                st[name(area, first + i)] = (data[i // 8] >> (i % 8)) & 1
    for first, cnt in REG_AREAS:
        data = txn(3, 0x1000 + first, cnt)
        if data:
            for i in range(cnt):
                st[f"D{first + i}"] = struct.unpack(">H", data[2*i:2*i+2])[0]
    return st

def main():
    out = open(LOG, "a", buffering=1)
    def say(msg):
        line = f"{time.strftime('%H:%M:%S')}  {msg}"
        print(line, flush=True); out.write(line + "\n")
    say(f"Boshlandi. {WARMUP} s shovqinli elementlar aniqlanadi -- hech narsa bosmang.")
    prev, noisy, t0 = snapshot(), set(), time.time()
    while True:
        cur = snapshot()
        ch = {k: (prev[k], cur[k]) for k in cur if k in prev and cur[k] != prev[k]}
        if time.time() - t0 < WARMUP:
            noisy |= ch.keys()
        else:
            if not getattr(main, "ready", False):
                main.ready = True
                say(f"Tayyor. Shovqinli {len(noisy)} ta element e'tiborga olinmaydi. Tugmalarni bosing.")
            real = {k: v for k, v in ch.items() if k not in noisy}
            if real:
                say("  ".join(f"{k}: {a}->{b}" for k, (a, b) in sorted(real.items())))
        prev.update(cur)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
