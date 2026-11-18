#!/usr/bin/env python3
# X kirishlar va M relelarni jonli kuzatadi, O'ZGARISHLARNI ko'rsatadi. FAQAT O'QIYDI.
import socket, struct, time, sys

HOST, PORT, STATION = "192.168.0.7", 8234, 1
DUR = int(sys.argv[1]) if len(sys.argv) > 1 else 45

def lrc(b): return (-sum(b)) & 0xFF
def frame(fn, addr, cnt):
    body = bytes([STATION, fn]) + struct.pack(">HH", addr, cnt)
    return b":" + body.hex().upper().encode() + f"{lrc(body):02X}".encode() + b"\r\n"

def read_bits(fn, addr, count):
    try:
        s = socket.create_connection((HOST, PORT), timeout=2); s.settimeout(1.0)
        s.sendall(frame(fn, addr, count)); resp=b""
        while not resp.endswith(b"\r\n"):
            c = s.recv(256)
            if not c: break
            resp += c
        s.close()
        raw = bytes.fromhex(resp.strip()[1:-2].decode())
        if raw[1] & 0x80: return None
        data = raw[3:3+raw[2]]; bits=[]
        for byte in data:
            for i in range(8): bits.append((byte>>i)&1)
        return bits[:count]
    except Exception:
        return None

def snapshot():
    x = read_bits(0x02, 0x0400, 32) or []
    m = read_bits(0x01, 0x0800, 64) or []
    return x, m

print(f"Jonli kuzatuv {DUR}s. HOZIR o'rash ipi datchigini ishga tushiring (ipni ko'taring/bo'shating).")
x0, m0 = snapshot()
print("Boshlang'ich X yoniq:", [f"X{i}" for i,b in enumerate(x0) if b])
print("Boshlang'ich M yoniq:", [f"M{i}" for i,b in enumerate(m0) if b])
print("--- o'zgarishlar ---")
prevx, prevm = x0[:], m0[:]
t0 = time.time(); any_change=False
while time.time()-t0 < DUR:
    x, m = snapshot()
    if not x: continue
    for i in range(min(len(x),len(prevx))):
        if x[i]!=prevx[i]:
            st = "YONDI" if x[i] else "OCHDI"
            print(f"  [{time.time()-t0:4.1f}s] X{i}: {prevx[i]} -> {x[i]} ({st})", flush=True)
            any_change=True
    for i in range(min(len(m),len(prevm))):
        if m[i]!=prevm[i]:
            print(f"  [{time.time()-t0:4.1f}s] M{i}: {prevm[i]} -> {m[i]}", flush=True)
            any_change=True
    prevx, prevm = x, m
if not any_change:
    print("  (hech qanday o'zgarish bo'lmadi)")
print("--- tugadi ---")
