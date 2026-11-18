#!/usr/bin/env python3
# Modbus TCP -> Modbus ASCII ko'prik.
# QModMaster (yoki boshqa Modbus TCP dastur) 127.0.0.1:5020 ga ulanadi,
# ko'prik so'rovni ASCII kadrga aylantirib DR134 (192.168.0.7:8234) orqali PLK ga yuboradi.
# Hech qanday tashqi kutubxona kerak emas.
#
# Ishlatish:  python3 modbus_bridge.py              (faqat O'QISH: FC01-04)
#             python3 modbus_bridge.py --allow-write (FC05/06/15/16 yozish ham o'tadi)

import socket, struct, threading, argparse, time

GW_HOST, GW_PORT = "192.168.0.7", 8234
READ_FCS = {0x01, 0x02, 0x03, 0x04}
WRITE_FCS = {0x05, 0x06, 0x0F, 0x10}

gw_lock = threading.Lock()   # DR134 bir vaqtda bitta so'rovga xizmat qiladi

def lrc(b): return (-sum(b)) & 0xFF

def matches(req, raw):
    # DR134 PLK javobini barcha ulangan mijozlarga tarqatadi (masalan stanok_web.py),
    # shuning uchun faqat aynan bizning so'rovimizga mos javobni qabul qilamiz.
    unit, fc = req[0], req[1]
    if raw[0] != unit or (raw[1] & 0x7F) != fc:
        return False
    if raw[1] & 0x80:
        return len(raw) == 3
    if fc in (0x01, 0x02):
        return raw[2] == (struct.unpack(">H", req[4:6])[0] + 7) // 8
    if fc in (0x03, 0x04):
        return raw[2] == struct.unpack(">H", req[4:6])[0] * 2
    if fc in (0x05, 0x06):
        return raw == req
    return raw[:6] == req[:6]          # FC0F/FC10: manzil + soni qaytadi

def ascii_txn(req, tries=3):
    fr = b":" + req.hex().upper().encode() + f"{lrc(req):02X}".encode() + b"\r\n"
    with gw_lock:
        for _ in range(tries):
            s = socket.create_connection((GW_HOST, GW_PORT), timeout=3)
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
                        if len(raw) >= 4 and lrc(raw[:-1]) == raw[-1] and matches(req, raw[:-1]):
                            return raw[:-1]   # unit + PDU, LRC olib tashlangan
            finally:
                s.close()
            time.sleep(0.2)
    return None

def handle(conn, addr, allow_write):
    print(f"[+] ulandi {addr}")
    try:
        while True:
            hdr = conn.recv(7, socket.MSG_WAITALL)
            if len(hdr) < 7: break
            tid, pid, length, unit = struct.unpack(">HHHB", hdr)
            pdu = conn.recv(length - 1, socket.MSG_WAITALL)
            fc = pdu[0]
            if fc in READ_FCS or (allow_write and fc in WRITE_FCS):
                resp = ascii_txn(bytes([unit]) + pdu)
                if resp is None:
                    out = bytes([unit, fc | 0x80, 0x0B])      # gateway target failed
                else:
                    out = resp
                    if fc in WRITE_FCS:
                        print(f"    YOZILDI: {(bytes([unit]) + pdu).hex(' ')}")
            else:
                out = bytes([unit, fc | 0x80, 0x01])          # illegal function
                if fc in WRITE_FCS:
                    print(f"    yozish rad etildi (FC{fc:02X}); --allow-write kerak")
            conn.sendall(struct.pack(">HHH", tid, pid, len(out)) + out)
    except (ConnectionError, OSError):
        pass
    finally:
        conn.close()
        print(f"[-] uzildi {addr}")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--listen", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5020)
    ap.add_argument("--allow-write", action="store_true")
    a = ap.parse_args()
    srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((a.listen, a.port)); srv.listen(4)
    print(f"Ko'prik: {a.listen}:{a.port} (Modbus TCP) -> {GW_HOST}:{GW_PORT} (Modbus ASCII)")
    print("Rejim:", "O'QISH + YOZISH" if a.allow_write else "faqat O'QISH")
    while True:
        c, ad = srv.accept()
        threading.Thread(target=handle, args=(c, ad, a.allow_write), daemon=True).start()

if __name__ == "__main__":
    main()
