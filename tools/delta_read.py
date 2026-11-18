#!/usr/bin/env python3
# Delta DVP-ES2 PLK registrlarini DR134 (TCP Server, transparent) orqali o'qish.
# Hech qanday tashqi kutubxona kerak emas.
#
# Ishlatish misollari:
#   python3 delta_read.py --scan                 # stansiya (slave ID) 1..16 ni qidiradi
#   python3 delta_read.py --d 0 --count 10       # D0..D9 ni o'qiydi (stansiya 1)
#   python3 delta_read.py --d 100 --count 5 --station 2 --mode ascii
#
# Delta manzillash: D registr n  ->  Modbus manzil 0x1000 + n (FC03 bilan).

import socket, struct, argparse, sys, time

HOST_DEFAULT = "192.168.0.7"
PORT_DEFAULT = 8234

def crc16(data: bytes) -> bytes:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return struct.pack("<H", crc)  # low, high

def build_rtu(station, func, addr, count):
    f = bytes([station, func]) + struct.pack(">HH", addr, count)
    return f + crc16(f)

def lrc(data: bytes) -> int:
    return ((-sum(data)) & 0xFF)

def build_ascii(station, func, addr, count):
    body = bytes([station, func]) + struct.pack(">HH", addr, count)
    frame = b":" + body.hex().upper().encode() + \
            bytes([ord(c) for c in f"{lrc(body):02X}"]) + b"\r\n"
    return frame

def txn(sock, frame, mode, timeout=1.5):
    sock.settimeout(timeout)
    sock.sendall(frame)
    resp = b""
    try:
        while True:
            c = sock.recv(256)
            if not c:
                break
            resp += c
            if mode == "ascii" and resp.endswith(b"\r\n"):
                break
            if mode == "rtu" and len(resp) >= 5:
                # yetarli kutamiz
                time.sleep(0.05)
                sock.setblocking(False)
                try:
                    while True:
                        extra = sock.recv(256)
                        if not extra: break
                        resp += extra
                except BlockingIOError:
                    pass
                sock.setblocking(True)
                break
    except socket.timeout:
        pass
    return resp

def parse_rtu(resp):
    if len(resp) < 5: return None
    station, func = resp[0], resp[1]
    if func & 0x80:  # exception
        return ("EXC", resp[2])
    bc = resp[2]
    data = resp[3:3+bc]
    regs = [struct.unpack(">H", data[i:i+2])[0] for i in range(0, len(data)-1, 2)]
    return ("OK", regs)

def parse_ascii(resp):
    s = resp.strip()
    if not s.startswith(b":"): return None
    hexpart = s[1:-2] if s.endswith(b"\r\n") else s[1:]
    try:
        raw = bytes.fromhex(hexpart.decode())
    except Exception:
        return None
    if len(raw) < 4: return None
    station, func = raw[0], raw[1]
    if func & 0x80:
        return ("EXC", raw[2])
    bc = raw[2]
    data = raw[3:3+bc]
    regs = [struct.unpack(">H", data[i:i+2])[0] for i in range(0, len(data)-1, 2)]
    return ("OK", regs)

def read_d(host, port, station, d_start, count, mode, func=0x03, base=0x1000):
    addr = base + d_start
    frame = build_rtu(station, func, addr, count) if mode == "rtu" \
            else build_ascii(station, func, addr, count)
    s = socket.create_connection((host, port), timeout=3)
    resp = txn(s, frame, mode)
    s.close()
    if not resp:
        return None, b""
    parsed = parse_rtu(resp) if mode == "rtu" else parse_ascii(resp)
    return parsed, resp

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=HOST_DEFAULT)
    ap.add_argument("--port", type=int, default=PORT_DEFAULT)
    ap.add_argument("--station", type=int, default=1)
    ap.add_argument("--d", type=int, default=0, help="boshlang'ich D registr raqami")
    ap.add_argument("--count", type=int, default=10)
    ap.add_argument("--mode", choices=["rtu", "ascii"], default="rtu")
    ap.add_argument("--func", type=lambda x: int(x, 0), default=0x03)
    ap.add_argument("--base", type=lambda x: int(x, 0), default=0x1000,
                    help="D registr uchun Modbus bazaviy manzil (Delta=0x1000)")
    ap.add_argument("--scan", action="store_true", help="stansiya 1..16 ni qidirish")
    args = ap.parse_args()

    if args.scan:
        print("Stansiya (slave ID) qidirilmoqda, rejim:", args.mode)
        found = False
        for st in range(1, 17):
            try:
                parsed, resp = read_d(args.host, args.port, st, 0, 1,
                                      args.mode, args.func, args.base)
            except Exception as e:
                print("  ulanish xatosi:", e); return
            if parsed:
                print(f"  >>> stansiya {st}: JAVOB {parsed}  (xom: {resp.hex(' ')})")
                found = True
        if not found:
            print("  Hech qanday javob yo'q. (Rejim/baud/sim yoki COM3 faol emas)")
        return

    parsed, resp = read_d(args.host, args.port, args.station, args.d,
                          args.count, args.mode, args.func, args.base)
    if not parsed:
        print(f"Javob yo'q. Xom qabul: {resp.hex(' ') if resp else '(bo‘sh)'}")
        print("Tekshiring: COM3 faolmi, baud/parity to'g'rimi, rejim RTU/ASCII, stansiya ID.")
        return
    tag, val = parsed
    if tag == "EXC":
        print(f"Modbus EXCEPTION kodi {val} — qurilma javob berdi, lekin manzil/funksiya mos emas.")
        print("Bu YAXSHI belgi: aloqa ISHLAYAPTI, faqat registr manzilini to'g'rilash kerak.")
    else:
        for i, v in enumerate(val):
            print(f"  D{args.d + i} = {v}  (0x{v:04X})")

if __name__ == "__main__":
    main()
