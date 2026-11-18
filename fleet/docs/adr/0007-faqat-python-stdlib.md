# ADR-0007: Faqat Python standart kutubxonasi

**Sana**: 2026-11-14 (yozilgan sana: 2026-11-18)
**Holati**: qabul qilingan
**Qaror qabul qilganlar**: zavod muhandisi, Claude

## Kontekst
Dastur zavoddagi virtual mashinada (Debian, Python 3.11) ishlaydi. Internet ishonchsiz, `curl` ham
o'rnatilmagan. Dastur yillab o'zgarishsiz ishlashi va systemd watchdog bilan o'zi tiklanishi kerak.

## Qaror
`stanok_web.py` va `fleet.py` faqat Python standart kutubxonasidan foydalanadi: Modbus ASCII o'zimiz
yozgan, veb-server `http.server`, ma'lumotlar bazasi `sqlite3`. Brauzer qismi CDN'siz, bitta HTML fayl.

## Ko'rib chiqilgan variantlar
### Flask/FastAPI + pymodbus + frontend freymvork
- **Afzalliklari**: kamroq kod, tayyor yechimlar.
- **Kamchiliklari**: `pip install` kerak, versiya yangilanishlari, internet bo'lmasa o'rnatib bo'lmaydi;
  DR134 javob tarqatish xususiyati uchun baribir maxsus kod kerak.
- **Nega emas**: o'rnatish va qo'llab-quvvatlash xavfi foydadan katta.

## Oqibatlar
### Ijobiy
- Fayllarni nusxalash = o'rnatish. Paketlar yangilanishi bilan buzilmaydi.
### Salbiy
- Veb-server oddiy (ThreadingHTTPServer), HTTPS yo'q. Tashqi kirish Cloudflare tunnel orqali shifrlanadi.
### Xavflar
- Foydalanuvchilar ko'paysa (40+ ekran), server yetishmasligi mumkin. Unda markaziy serverda
  boshqa yechim ko'riladi (ADR-0004).
