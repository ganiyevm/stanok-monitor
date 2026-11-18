#!/usr/bin/env python3
# Panel smena resetida qaysi D registrlar o'zgarishini topish. FAQAT O'QIYDI.
# Har aylanishda: D0-599 + D2000-2099 (hamma o'zgarish yoziladi, shovqin filtri yo'q,
# faqat ms vaqt bilan). Har aylanishda bitta M bo'lagi (256 bit) ham o'qiladi.
import importlib.util, os, time, struct
spec = importlib.util.spec_from_file_location("w", os.path.expanduser("~/stanok_watch.py"))
w = importlib.util.module_from_spec(spec); spec.loader.exec_module(w)
LOG = os.path.expanduser("~/stanok_reset_watch2.log")
REGS = [(0, 43), (43, 61), (104, 53), (157, 57), (214, 37), (251, 59), (310, 67), (377, 71),
        (448, 63), (511, 39), (550, 49), (2000, 97)]
NOISY = {"D4", "D34", "D61", "D67", "D68", "D69", "D87", "D88", "D218", "D220", "D226"} | \
        {f"D{i}" for i in range(89, 100)} | {f"D{i}" for i in range(104, 111)} | \
        {f"D{i}" for i in range(1050, 1100)}
def main():
    out = open(LOG, "a", buffering=1)
    def say(m):
        t = time.time(); line = f"{time.strftime('%H:%M:%S', time.localtime(t))}.{int(t*1000)%1000:03d}  {m}"
        print(line, flush=True); out.write(line + "\n")
    prev, k = {}, 0
    say("Boshlandi (faqat o'qish).")
    while True:
        cur = {}
        for a, c in REGS:
            d = w.txn(3, 0x1000 + a, c)
            if d:
                for i in range(c): cur[f"D{a+i}"] = struct.unpack(">H", d[2*i:2*i+2])[0]
        area, addr, first, cnt, fn = w.BIT_AREAS[k % len(w.BIT_AREAS)]; k += 1
        d = w.txn(fn, addr, cnt)
        if d:
            for i in range(cnt): cur[w.name(area, first+i)] = (d[i//8] >> (i % 8)) & 1
        ch = {n: (prev[n], v) for n, v in cur.items() if n in prev and prev[n] != v and n not in NOISY}
        prev.update(cur)
        if ch: say("  ".join(f"{n}: {a}->{b}" for n, (a, b) in sorted(ch.items())))
if __name__ == "__main__":
    try: main()
    except KeyboardInterrupt: pass
