# ADR-0001: Sex monitori PLK'ga hech narsa yozmaydi

**Sana**: 2026-11-18
**Holati**: qabul qilingan
**Qaror qabul qilganlar**: zavod muhandisi, Claude

## Kontekst
40 tagacha stanok bitta sex monitorida (`fleet.py`) ko'rsatiladi. Har stanokning o'z sahifasi
(`stanok_web.py`) PIN bilan cheklangan boshqaruvga ega: to'xtatish, tezlik, LIGHT/FAN, himoyani yoqish.
Bitta joydan 40 stanokni boshqarish xatoning ta'sir doirasini 40 barobar oshiradi: noto'g'ri stanok
tanlash, xatolik yoki ruxsatsiz kirish.

## Qaror
Sex monitori faqat o'qiydi. `Gateway` sinfida faqat FC01/02/03 bor, yozish funksiyasi umuman yo'q.
POST so'rovlar 405 qaytaradi. Boshqaruv uchun batafsil oynada stanokning o'z sahifasiga havola
beriladi (faqat zavod tarmog'ida).

## Ko'rib chiqilgan variantlar
### Sex ekranidan boshqarish (PIN bilan)
- **Afzalliklari**: master bitta ekrandan hammasini boshqaradi.
- **Kamchiliklari**: noto'g'ri katakni bosish xavfi; 40 stanokka yozuvchi bitta nuqta.
- **Nega emas**: har stanokda boshqaruv alohida tasdiqlanishi kerak (ADR-0005).

## Oqibatlar
### Ijobiy
- Sex monitoridagi xato hech qachon stanokni to'xtatmaydi yoki tezligini o'zgartirmaydi.
- Tashqi kirishni ochish xavfsizroq: ko'rish huquqidan boshqa narsa yo'q.
### Salbiy
- Boshqarish uchun stanok sahifasiga o'tish kerak (bitta qo'shimcha bosish).
### Xavflar
- Kelajakda boshqaruv qo'shish istagi paydo bo'ladi. U yangi ADR bilan, har stanok alohida
  tasdiqlangandan keyin qilinishi kerak.
