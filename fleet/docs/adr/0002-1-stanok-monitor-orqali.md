# ADR-0002: 1-stanok sex monitoriga hozirgi monitor orqali ulanadi

**Sana**: 2026-11-18
**Holati**: qabul qilingan
**Qaror qabul qilganlar**: zavod muhandisi, Claude

## Kontekst
1-stanokni `stanok_web.py` DR134 (192.168.0.7:8234) orqali har 3 soniyada o'qiydi. DR134 PLK
javobini barcha ulangan mijozlarga tarqatadi. Javoblar faqat bayt soni bo'yicha ajratiladi, shuning
uchun bloklar ataylab har xil uzunlikda tanlangan. Shinaga yana bitta o'quvchi qo'shilsa,
to'qnashuvlar va adashgan javoblar ko'payadi.

## Qaror
Sex monitorida `"source": "monitor"` turi bor: ma'lumot `http://127.0.0.1:8080/api/data` dan
olinadi. Ma'lumot 30 soniyadan eski bo'lsa, stanok "Aloqa yo'q" deb ko'rsatiladi.

## Ko'rib chiqilgan variantlar
### Sex monitori ham DR134'ni to'g'ridan-to'g'ri o'qiydi
- **Afzalliklari**: hamma stanok bir xil usulda ulanadi.
- **Kamchiliklari**: RS485 yuklamasi ikki barobar, javob adashish xavfi.
- **Nega emas**: ishlab turgan, tasdiqlangan o'qishni buzish xavfi.
### stanok_web.py'ni o'chirib, hammasini fleet.py'ga o'tkazish
- **Nega emas**: boshqaruv faqat stanok_web.py'da (ADR-0001).

## Oqibatlar
### Ijobiy
- PLK'ga qo'shimcha so'rov yo'q; hozirgi monitor o'zgarmaydi.
### Salbiy
- 1-stanok ikki dasturga bog'liq: stanok_web.py to'xtasa, sex monitorida ham ko'rinmaydi.
### Xavflar
- stanok_web.py qayta ishga tushganda ~20 s "Aloqa yo'q". Bu qabul qilinadi.
