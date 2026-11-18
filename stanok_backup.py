#!/usr/bin/env python3
# Delta DVP32ES2 PLK xotirasining (D, T, C registrlar va X, Y, M, S, T, C bitlar)
# to'liq zaxira nusxasini JSON faylga yozadi. FAQAT O'QIYDI, hech narsa yozmaydi.
# Eslatma: bu LADDER DASTURNI emas, PLK ma'lumotlarini (sozlamalar, hisoblagichlar) saqlaydi.
#
# Ishlatish:  python3 stanok_backup.py            -> ~/stanok_backup/plc_data_YYYYmmdd_HHMMSS.json
#             python3 stanok_backup.py --diff A.json B.json   -> ikki zaxirani solishtiradi

import socket, struct, json, os, sys, time, argparse
from datetime import datetime

HOST, PORT, STATION = "192.168.0.7", 8234, 1

# (nom, Modbus bazaviy manzil, birinchi element raqami, soni, funksiya, turi)
AREAS = [
    ("X", 0x0400, 0,    256,  0x02, "bit"),
    ("Y", 0x0500, 0,    256,  0x01, "bit"),
    ("S", 0x0000, 0,    1024, 0x01, "bit"),
    ("M", 0x0800, 0,    1536, 0x01, "bit"),
    ("M", 0xB000, 1536, 2560, 0x01, "bit"),
    ("T", 0x0600, 0,    256,  0x01, "bit"),
    ("C", 0x0E00, 0,    256,  0x01, "bit"),
    ("TV", 0x0600, 0,   256,  0x03, "reg"),
    ("CV", 0x0E00, 0,   200,  0x03, "reg"),
    ("D", 0x1000, 0,    4096, 0x03, "reg"),
    ("D", 0x9000, 4096, 5904, 0x03, "reg"),
]
BIT_CHUNK, REG_CHUNK = 128, 32

def lrc(b): return (-sum(b)) & 0xFF

def txn(sock, fn, addr, cnt):
    body = bytes([STATION, fn]) + struct.pack(">HH", addr, cnt)
    fr = b":" + body.hex().upper().encode() + f"{lrc(body):02X}".encode() + b"\r\n"
    sock.sendall(fr)
    resp = b""
    try:
        while not resp.endswith(b"\r\n"):
            c = sock.recv(512)
            if not c: break
            resp += c
    except socket.timeout:
        pass
    # oxirgi to'liq ":...\r\n" kadrni olamiz (kechikkan eski javoblar tashlanadi)
    resp = resp.strip()
    if b":" not in resp:
        return None
    resp = resp[resp.rfind(b":") + 1:]
    try:
        raw = bytes.fromhex(resp.decode())
    except ValueError:
        return None
    if len(raw) < 4 or raw[0] != STATION or (raw[1] & 0x7F) != fn or lrc(raw[:-1]) != raw[-1]:
        return None
    if raw[1] & 0x80:
        return ("EXC", raw[2])
    data = raw[3:3 + raw[2]]
    need = (cnt + 7) // 8 if fn in (0x01, 0x02) else cnt * 2
    return ("OK", data) if len(data) == need else None

def read_area(sock, base, first, count, fn, kind):
    out = {}
    chunk = BIT_CHUNK if kind == "bit" else REG_CHUNK
    for off in range(0, count, chunk):
        n = min(chunk, count - off)
        r = None
        for _ in range(3):
            r = txn(sock, fn, base + off, n)
            if r: break
            time.sleep(0.5)
            sock.settimeout(0.3)          # qolib ketgan baytlarni tozalash
            try:
                while sock.recv(512): pass
            except socket.timeout:
                pass
            sock.settimeout(2.5)
        if not r or r[0] == "EXC":
            continue
        data = r[1]
        if kind == "bit":
            for i in range(n):
                out[first + off + i] = (data[i // 8] >> (i % 8)) & 1
        else:
            for i in range(n):
                out[first + off + i] = struct.unpack(">H", data[2*i:2*i+2])[0]
    return out

def backup():
    s = socket.create_connection((HOST, PORT), timeout=3); s.settimeout(2.5)
    result = {"time": datetime.now().isoformat(timespec="seconds"),
              "host": HOST, "station": STATION, "areas": {}}
    for name, base, first, count, fn, kind in AREAS:
        print(f"  {name}{first}..{name}{first+count-1} o'qilmoqda...", flush=True)
        vals = read_area(s, base, first, count, fn, kind)
        result["areas"].setdefault(name, {}).update({str(k): v for k, v in vals.items()})
        print(f"    {len(vals)}/{count} o'qildi")
    s.close()
    d = os.path.expanduser("~/stanok_backup"); os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"plc_data_{datetime.now():%Y%m%d_%H%M%S}.json")
    with open(path, "w") as f:
        json.dump(result, f, indent=1)
    print("Saqlandi:", path)

def diff(a, b):
    A, B = json.load(open(a))["areas"], json.load(open(b))["areas"]
    for name in A:
        for k in sorted(A[name], key=int):
            if B.get(name, {}).get(k) != A[name][k]:
                print(f"  {name}{k}: {A[name][k]} -> {B.get(name, {}).get(k)}")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--diff", nargs=2, metavar=("A", "B"))
    args = ap.parse_args()
    diff(*args.diff) if args.diff else backup()
