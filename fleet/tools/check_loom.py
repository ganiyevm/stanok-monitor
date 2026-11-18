#!/usr/bin/env python3
# Yangi stanokni sex monitoriga qo'shishdan oldin tekshirish. FAQAT O'QIYDI.
# Ishlatish:  python3 tools/check_loom.py 10.10.1.102 [8234]
# Chiqqan qiymatlarni stanok panelidagi raqamlar bilan solishtiring (README, 3-qadam).
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import fleet

host = sys.argv[1]; port = int(sys.argv[2]) if len(sys.argv) > 2 else 8234
src = fleet.ModbusSource({"host": host, "port": port})
print(f"{host}:{port} o'qilmoqda (faqat o'qish)...")
d = src.poll()
rows = [("Holat", "Ishlayapti" if d["running"] else "To'xtagan", "panel: motor aylanyaptimi"),
        ("Hz sozlama", d["hz"], "panel: Hz"),
        ("Hz haqiqiy", d["hz_actual"], ""),
        ("PPM", d["ppm"], "panel: PPM"),
        ("Smena metri", d["shift_m"], "panel: PRODUT"),
        ("Samaradorlik %", d["eff_panel"], "panel: EFFICIENCY"),
        ("Smena raqami", d["shift"], "panel: SHIFT"),
        ("Arqoq zichligi", d["weft_den"], "panel 2-ekran: WEFT DEN"),
        ("Dvigatel toki A", d["main_load"], "panel: MAIN LOAD"),
        ("Umumiy metr", d["total_m"], "panel: TOTAL"),
        ("LIGHT / FAN", f"{d['light']} / {d['fan']}", "panel: [5] / [6] chiroqlari"),
        ("Himoya o'chiq", d["bypass"], "panel: [7] [8] [9]"),
        ("To'xtash sababi", d["reason"], "")]
for name, val, hint in rows:
    print(f"  {name:<18} {str(val):<22} {hint}")
print("Hammasi panel bilan mos kelsa, looms.json da shu stanokka \"enabled\": true qiling.")
