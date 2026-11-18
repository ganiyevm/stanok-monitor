#!/usr/bin/env python3
# Delta DVP32ES2 — kirish (X), chiqish (Y), yordamchi rele (M) bit holatlarini
# va D registrlarni FAQAT O'QIYDI. Hech narsa yozmaydi.
# Delta Modbus: X=0x0400(FC02), Y=0x0500(FC01), M=0x0800(FC01), D=0x1000(FC03)

import socket, struct, sys, argparse

HOST, PORT, STATION = "192.168.0.7", 8234, 1

def lrc(b): return (-sum(b)) & 0xFF
def frame(fn, addr, cnt):
    body = bytes([STATION, fn]) + struct.pack(">HH", addr, cnt)
    return b":" + body.hex().upper().encode() + f"{lrc(body):02X}".encode() + b"\r\n"

def txn(fr):
    s = socket.create_connection((HOST, PORT), timeout=3); s.settimeout(1.5)
    s.sendall(fr); resp = b""
    try:
        while not resp.endswith(b"\r\n"):
            c = s.recv(256)
            if not c: break
            resp += c
    except socket.timeout: pass
    s.close()
    if not resp.startswith(b":"): return None
    raw = bytes.fromhex(resp.strip()[1:-2].decode())
    if raw[1] & 0x80: return ("EXC", raw[2])
    return ("OK", raw)

def read_bits(fn, addr, count):
    r = txn(frame(fn, addr, count))
    if not r or r[0]=="EXC": return None
    raw = r[1]; bc = raw[2]; data = raw[3:3+bc]
    bits = []
    for byte in data:
        for i in range(8):
            bits.append((byte >> i) & 1)
    return bits[:count]

def read_words(addr, count):
    r = txn(frame(0x03, addr, count))
    if not r or r[0]=="EXC": return None
    raw = r[1]; bc = raw[2]; data = raw[3:3+bc]
    return [struct.unpack(">H", data[i:i+2])[0] for i in range(0, bc, 2)]

def show_bits(name, fn, base, count, label):
    bits = read_bits(fn, base, count)
    if bits is None:
        print(f"  {name}: (o'qilmadi)"); return {}
    on = [f"{label}{i}" for i,b in enumerate(bits) if b]
    print(f"  {name} (yoniq=1): " + (" ".join(on) if on else "(hammasi 0)"))
    return {f"{label}{i}": b for i,b in enumerate(bits)}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--xcount", type=int, default=32)
    ap.add_argument("--ycount", type=int, default=32)
    ap.add_argument("--mcount", type=int, default=0, help="M rele soni (0=o'qimaydi)")
    ap.add_argument("--dump-d", type=int, default=0, help="D0..D(N-1) ni ko'rsatadi")
    args = ap.parse_args()

    print("===== KIRISH / CHIQISH (FAQAT O'QILDI) =====")
    show_bits("X (kirishlar/datchiklar)", 0x02, 0x0400, args.xcount, "X")
    show_bits("Y (chiqishlar)",           0x01, 0x0500, args.ycount, "Y")
    if args.mcount:
        show_bits("M (yordamchi rele)",   0x01, 0x0800, args.mcount, "M")
    if args.dump_d:
        print("\n===== D registrlar (sozlamalar) =====")
        for start in range(0, args.dump_d, 50):
            cnt = min(50, args.dump_d-start)
            ws = read_words(0x1000+start, cnt)
            if ws:
                for i,v in enumerate(ws):
                    if v: print(f"  D{start+i} = {v}  (0x{v:04X})")

if __name__ == "__main__":
    main()
