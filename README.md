# Stanok monitor

Plastik lenta to'quv stanogini (Yongming SBY-850×6G, Delta DVP32ES2 PLK) real vaqtda kuzatish va
cheklangan masofadan boshqarish uchun veb-dastur. Stanok PLK'si USR-DR134 shlyuzi (RS485 → Ethernet,
Modbus ASCII) orqali o'qiladi. Faqat Python standart kutubxonasi ishlatiladi, qo'shimcha paket kerak emas.

## Imkoniyatlar

- Stanok holati (ishlayapti yoki to'xtagan) va to'xtash sababi: arqoq tugadi, ip uzildi, avariya
- Tezlik (Hz, PPM), smenada to'qilgan metr, samaradorlik (stanok paneli bilan bir xil)
- Arqoq zichligi, asosiy dvigatel toki, umumiy metr, oxirgi 1 soat grafigi
- Smenalar tarixi va voqealar jurnali (CSV fayllarda)
- PIN bilan boshqaruv: to'xtatish, tezlik 5–40 Hz, LIGHT/FAN, uzilish himoyasini qayta yoqish,
  smenani nolga qaytarish
- **Masofadan ishga tushirish ataylab yo'q**: stanok yonida odamlar ishlaydi
- Tashqi kirish uchun alohida port: login-parol bilan, faqat ko'rish (boshqaruv rad etiladi)
- systemd watchdog: dastur qotib qolsa yoki yiqilsa, o'zi qayta ishga tushadi

## Fayllar

| Fayl | Vazifasi |
| --- | --- |
| `stanok_web.py` | Asosiy dastur: PLK'ni o'qish, veb-sahifa, boshqaruv, loglar |
| `stanok_backup.py` | PLK xotirasini (D, M, X, Y, T, C, S) JSON'ga zaxiralash va solishtirish |
| `modbus_bridge.py` | Modbus TCP → Modbus ASCII ko'prigi (QModMaster kabi vositalar uchun) |
| `tools/` | Sozlash paytida ishlatilgan o'qish va kuzatish skriptlari (faqat o'qiydi) |
| `systemd/` | Doim ishlab turishi uchun systemd foydalanuvchi xizmatlari |

## O'rnatish

```bash
cp stanok_web.py ~/
mkdir -p ~/stanok && echo "123456" > ~/stanok/control_pin && chmod 600 ~/stanok/control_pin
cp systemd/stanok-web.service ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now stanok-web
loginctl enable-linger $USER     # foydalanuvchi tizimga kirmasa ham ishlashi uchun
```

Sahifa: `http://<kompyuter-ip>:8080`

## Sozlamalar

| Sozlama | Qayerda | Izoh |
| --- | --- | --- |
| PLK manzili | `stanok_web.py`dagi `PLC_HOST`, `PLC_PORT` | Odatiy: `192.168.0.7:8234` |
| Veb port | `STANOK_PORT` muhit o'zgaruvchisi | Odatiy: 8080 |
| Boshqaruv PIN'i | `STANOK_PIN` yoki `~/stanok/control_pin` | Yo'q bo'lsa, boshqaruv butunlay o'chiq |
| Tashqi port | `STANOK_REMOTE_PORT` | Faqat 127.0.0.1 da; Cloudflare tunnel uchun |
| Tashqi login | `~/stanok/web_auth` (`login:parol`) | Yo'q bo'lsa, tashqi port hech kimni kiritmaydi |

Loglar `~/stanok_log/` papkasiga yoziladi: kunlik CSV (har daqiqa), `events.csv`, `smenalar.csv`.

## Ogohlantirish

Dastur PLK'ga faqat oq ro'yxatdagi registr va bitlarga yozadi. Ular shu stanok modeli uchun joyida
sinab tasdiqlangan. Boshqa model yoki boshqa PLK dasturiga ulashdan oldin manzillarni qayta tekshiring.
Boshqaruv portini internetga ochmang.

## Sex monitori (40 stanok)

`fleet/` papkasida ko'p stanokli sex monitori bor. U faqat o'qiydi, 40 tagacha stanokni bitta ekranda ko'rsatadi,
kunlik va smenalik hisobot beradi, smenalarni super admin sozlaydi. Batafsil: [fleet/README.md](fleet/README.md).
Arxitektura qarorlari: [fleet/docs/adr/](fleet/docs/adr/README.md).

## Arxitektura qarorlari

Masofadan yoqish yo'qligi va tashqi kirish faqat ko'rish uchunligi (ADR-0006), faqat standart kutubxona
ishlatilishi (ADR-0007) va boshqa qarorlar sex monitori loyihasida yozilgan: [fleet/docs/adr/](fleet/docs/adr/README.md).
