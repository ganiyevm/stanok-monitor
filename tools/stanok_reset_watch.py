#!/usr/bin/env python3
# Smenani tiklash (panel: menyu 3 -> tugma 6) qaysi bit/registr orqali ishlashini topish uchun.
# FAQAT O'QIYDI. Faqat sinov vaqtida qisqa ishga tushiring (DR134 ni band qiladi).
# - M0..M4095 hammasi (256 lik bo'laklarda), Y, X
# - D511..D549 (smena registrlari) har aylanishda, qolgan D0..D599 har 3-aylanishda
# - D80, D512..D519 hech qachon "shovqinli" deb chiqarilmaydi -- doim yoziladi
# - vaqt millisekundgacha: nima oldin o'zgarganini ko'rish uchun
# Ishlatish: python3 -u stanok_reset_watch.py   (Ctrl+C bilan to'xtatiladi)
import importlib.util, os, time, struct

spec = importlib.util.spec_from_file_location("w", os.path.expanduser("~/stanok_watch.py"))
w = importlib.util.module_from_spec(spec); spec.loader.exec_module(w)

LOG = os.path.expanduser("~/stanok_reset_watch.log")
WARMUP = 60
ALWAYS = {"D80", "D81"} | {f"D{i}" for i in range(512, 520)}
SHIFT_BLOCK = (511, 39)
OTHER_REGS = [(0, 43), (43, 61), (104, 53), (157, 57), (214, 37), (251, 59), (310, 67),
              (377, 71), (448, 63), (550, 49)]

def read_regs(first, cnt, st):
    data = w.txn(3, 0x1000 + first, cnt)
    if data:
        for i in range(cnt):
            st[f"D{first + i}"] = struct.unpack(">H", data[2*i:2*i+2])[0]

def read_bits(area, addr, first, cnt, fn, st):
    data = w.txn(fn, addr, cnt)
    if data:
        for i in range(cnt):
            st[w.name(area, first + i)] = (data[i // 8] >> (i % 8)) & 1

def main():
    out = open(LOG, "a", buffering=1)
    def say(msg):
        t = time.time()
        line = f"{time.strftime('%H:%M:%S', time.localtime(t))}.{int(t*1000)%1000:03d}  {msg}"
        print(line, flush=True); out.write(line + "\n")
    prev, noisy, t0, n, ready = {}, set(), time.time(), 0, False
    say(f"Boshlandi. {WARMUP} s shovqin o'rganiladi -- hech narsa bosmang.")
    while True:
        for area, addr, first, cnt, fn in w.BIT_AREAS:
            cur = {}
            read_regs(*SHIFT_BLOCK, cur)          # smena registrlari tez-tez
            read_bits(area, addr, first, cnt, fn, cur)
            if n % 3 == 0 and area == "X":
                for r in OTHER_REGS:
                    read_regs(*r, cur)
            ch = {k: (prev[k], v) for k, v in cur.items() if k in prev and prev[k] != v}
            prev.update(cur)
            if time.time() - t0 < WARMUP:
                noisy |= set(ch) - ALWAYS
            else:
                if not ready:
                    ready = True
                    say(f"Tayyor. Shovqinli {len(noisy)} ta element chiqarilmaydi. Resetni bosing.")
                real = {k: v for k, v in ch.items() if k not in noisy}
                if real:
                    say("  ".join(f"{k}: {a}->{b}" for k, (a, b) in sorted(real.items())))
        n += 1

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
