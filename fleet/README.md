# Sex monitori

40 tagacha plastik lenta to'quv stanogini (Yongming SBY-850×6G, Delta DVP32ES2 PLK) bitta ekranda
kuzatish. **Faqat o'qiydi**: PLK'ga hech narsa yozmaydi. Boshqaruv har stanokning o'z sahifasida
(`stanok_web.py`) qoladi. Faqat Python standart kutubxonasi ishlatiladi.

## Nima ko'rsatadi

- **Sex ekrani**: har stanok bitta katak.
  - Yashil: ishlayapti, katakda m/soat.
  - Qizil: to'xtagan, katakda sabab va qancha vaqtdan beri.
  - Sariq: ishlayapti, lekin himoya o'chiq.
  - Kulrang: aloqa yo'q.
  - Punktir chiziq: hali ulanmagan.
- Yuqorida jami ko'rsatkichlar: nechta ishlayapti yoki to'xtagan, umumiy tezlik, bugun to'qilgan metr.
- Katak bosilsa, batafsil oyna ochiladi: barcha parametrlar, 1 soatlik grafik, voqealar, smenalar.
- **Hisobot**: kun bo'yicha har stanok uchun to'qilgan metr, ishlagan va to'xtagan vaqt, to'xtashlar
  soni, to'xtash sabablari. Excel'ga (CSV) yuklab olish mumkin.

## Fayllar

| Fayl | Vazifasi |
| --- | --- |
| `fleet.py` | Server: stanoklarni o'qish, ma'lumotlar bazasi, veb |
| `fleet.html` | Sex ekrani va hisobot sahifasi |
| `looms.json` | Stanoklar ro'yxati |
| `tools/check_loom.py` | Yangi stanokni bir marta o'qib, panel bilan solishtirish uchun chiqaradi |
| `tools/stanok_backup.py` | PLK xotirasini to'liq zaxiralash va solishtirish (`--host` bilan) |
| `systemd/stanok-fleet.service` | Doim ishlab turishi uchun xizmat |
| `data/` | `fleet.db` (SQLite) va har stanokning smena holati |

## Stanok ulanish usullari (`looms.json`)

```json
{"id": 1, "source": "monitor", "url": "http://127.0.0.1:8080", "panel_port": 8080}
{"id": 2, "source": "modbus", "host": "10.10.1.102", "port": 8234, "station": 1, "enabled": true}
```

- `monitor`: stanokni allaqachon `stanok_web.py` o'qiyapti, sex monitori undan oladi. RS485 shinasiga
  ikkinchi o'quvchi qo'shilmaydi. 1-stanok shu usulda ulangan.
- `modbus`: stanok o'zining DR134 shlyuzi orqali to'g'ridan-to'g'ri o'qiladi.
- `"enabled": false`: stanok ekranda "Ulanmagan" bo'lib turadi va o'qilmaydi.
- `panel_port`: shu stanokning boshqaruv sahifasiga havola (faqat zavod tarmog'ida ko'rinadi).

## Yangi stanok qo'shish tartibi

1. **DR134'ni sozlash** (stanokka ulashdan oldin, stolda):
   - IP manzil: `10.10.1.1NN`, bu yerda NN stanok raqami;
   - TCP Server rejimi, port 8234;
   - RS485 sozlamalari 1-stanokdagi DR134 bilan aynan bir xil.
2. **DR134'ni stanok PLK'sining RS485 portiga ulash.**
3. **Faqat o'qib tekshirish:** `python3 tools/check_loom.py 10.10.1.1NN`. Har qiymatni stanok paneli bilan
   solishtiring: Hz, PPM, PRODUT, EFFICIENCY, MAIN LOAD, TOTAL.
4. **PLK dasturi bir xilligini tekshirish:**
   - `python3 tools/stanok_backup.py --host 10.10.1.1NN` bilan zaxira oling;
   - `--diff` bilan 1-stanok zaxirasi bilan solishtiring.
   - Sozlamalar farq qilishi normal. Registrlar xaritasi butunlay boshqacha bo'lsa, stanokni qo'shmang.
5. Hammasi mos kelsa, `looms.json`da shu stanokka `"enabled": true` qo'ying va xizmatni qayta ishga
   tushiring: `systemctl --user restart stanok-fleet`.

## O'rnatish

```bash
cp systemd/stanok-fleet.service ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now stanok-fleet
```

Sahifa manzili: `http://<kompyuter-ip>:8090`

Sozlamalar muhit o'zgaruvchilari orqali beriladi:

| O'zgaruvchi | Vazifasi | Odatiy qiymat |
| --- | --- | --- |
| `FLEET_PORT` | Veb port | 8090 |
| `FLEET_CONFIG` | Stanoklar ro'yxati fayli | `looms.json` |
| `FLEET_DATA` | Ma'lumotlar papkasi | `data/` |
| `FLEET_REMOTE_PORT` | Tashqi kirish porti. Faqat 127.0.0.1 da ochiladi, `FLEET_AUTH_FILE` dagi `login:parol` talab qilinadi | o'chiq |

Band portlar: 8080 va 8091 ni `stanok_web.py` ishlatadi.

## Smenalar va super admin

- Smenalar `data/settings.json` faylida saqlanadi. Odatiy qiymat: 1-smena 07:00, 2-smena 19:00.
- Har smena nomi va boshlanish vaqti bilan beriladi. Tugash vaqti = keyingi smenaning boshlanishi, shuning
  uchun smenalar 24 soatni to'liq qoplaydi. Birinchi smena ishlab chiqarish kunini boshlaydi, tungi smena
  boshlangan kunga yoziladi.
- O'zgartirish: sahifadagi **Sozlamalar** bo'limi, faqat zavod tarmog'idan va super admin paroli bilan.
  Tashqi (tunnel) port bu bo'limni rad etadi. Har o'zgarish `settings_log` jadvaliga vaqt va IP bilan yoziladi.
- Super admin paroli `~/stanok/fleet_admin` faylida PBKDF2 xeshi ko'rinishida turadi. Parolni yangilash:
  `python3 ~/stanok-fleet/fleet.py --set-admin-password`. Sessiya 8 soat, 5 ta noto'g'ri urinishdan keyin
  15 daqiqa blok.

## Hisobot qanday hisoblanadi

- Rejali vaqt = ishlab chiqarish kuni yoki smena oralig'i (stanok monitoringga ulangandan beri, hozirgacha).
- Stanokdan ma'lumot kelmagan daqiqalar "Aloqa yo'q / elektr" deb hisoblanadi. Elektr o'chganda PLK va DR134
  ham o'chadi, shuning uchun u shunday ko'rinadi. Bu vaqt yo'qotish sifatida ishlash ulushini kamaytiradi.

## Cheklovlar

- To'xtash sabablari PLK bitlaridan aniqlanadi va hali to'liq tasdiqlanmagan ("?" belgisi bilan).
- Hisobot daqiqalik o'qishlardan tuziladi. Aloqa uzilgan vaqt hisobga kirmaydi.

## Arxitektura qarorlari

Nega shunday qilingani `docs/adr/` papkasida yozilgan (ADR-0001 … 0008). Ro'yxat: [docs/adr/README.md](docs/adr/README.md).
