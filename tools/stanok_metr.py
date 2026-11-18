#!/usr/bin/env python3
# SBY-850X6G stanogining umumiy tikilgan uzunligini o'qiydi.
# Delta DVP32ES2 PLK -> DR134 (192.168.0.7:8234, TCP Server, 9600 7E1 ASCII).
# Ishlatish:  python3 stanok_metr.py

import socket, struct, time

HOST, PORT, STATION = "192.168.0.7", 8234, 1
BASE = 0x1000  # Delta: D(n) -> Modbus 0x1000+n

def lrc(b): return (-sum(b)) & 0xFF
def ascii_frame(st, fn, addr, cnt):
    body = bytes([st, fn]) + struct.pack(">HH", addr, cnt)
    return b":" + body.hex().upper().encode() + f"{lrc(body):02X}".encode() + b"\r\n"

def read_regs(d_start, count):
    s = socket.create_connection((HOST, PORT), timeout=3); s.settimeout(1.5)
    s.sendall(ascii_frame(STATION, 0x03, BASE + d_start, count))
    resp = b""
    try:
        while not resp.endswith(b"\r\n"):
            c = s.recv(256)
            if not c: break
            resp += c
    except socket.timeout:
        pass
    s.close()
    raw = bytes.fromhex(resp.strip()[1:-2].decode())
    bc = raw[2]; data = raw[3:3+bc]
    return [struct.unpack(">H", data[i:i+2])[0] for i in range(0, bc, 2)]

def main():
    r = read_regs(526, 4)          # D526, D527, D528, D529
    d526, d527, d528, d529 = r
    total_cm = (d529 << 16) | d528
    print("===== STANOK — tikilgan uzunlik =====")
    print(f"  Umumiy (umri davomida): {total_cm} cm = {total_cm/100:,.2f} m = {total_cm/100000:,.3f} km")
    print(f"  (D528={d528}, D529={d529}, D526={d526})")

if __name__ == "__main__":
    main()
