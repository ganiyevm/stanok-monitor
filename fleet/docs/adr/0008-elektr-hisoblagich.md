# ADR-0008: Elektr sarfi alohida Modbus hisoblagich bilan o'lchanadi

**Sana**: 2026-11-18
**Holati**: taklif
**Qaror qabul qilganlar**: zavod muhandisi, Claude

## Kontekst
Elektr sarfini ko'rish so'raldi. PLK faqat asosiy dvigatel tokini beradi (D66/10, 15 Hz da 3,7 A,
tasdiqlangan). Dvigatel invertor orqali ishlaydi: kuchlanish va cosφ chastotaga qarab o'zgaradi va
noma'lum. Tortish, o'rash va moylash motorlari, ventilyator va chiroqning toki o'qilmaydi. Tokdan
hisoblangan quvvat 0,5–2 kW oralig'ida chiqadi, ya'ni ishonchsiz.

## Qaror
Har stanok kirishiga RS485 (Modbus RTU) chiqishli 3 fazali hisoblagich qo'yiladi (masalan Eastron
SDM630, CHINT DTSU666). U alohida DR134 orqali o'qiladi, chunki PLK shinasi Modbus ASCII ishlatadi.
Tokdan kW taxmini dasturda "aniq" deb ko'rsatilmaydi.

## Ko'rib chiqilgan variantlar
### Dvigatel tokidan kW hisoblash
- **Afzalliklari**: qo'shimcha xarajat yo'q.
- **Kamchiliklari**: 3–4 barobar xato, faqat bitta motor.
- **Nega emas**: noto'g'ri raqam noto'g'ri qaror chiqaradi.
### Invertorning o'zidan quvvatni o'qish
- **Afzalliklari**: asosiy motor uchun aniq.
- **Kamchiliklari**: invertor modeli noma'lum, faqat bitta motor.
- **Nega emas**: hozircha; invertor yorlig'i ma'lum bo'lsa qayta ko'rib chiqiladi.

## Oqibatlar
### Ijobiy
- Aniq kW, kWh, kuchlanish va cosφ; "1 metr matoga necha kWh" ko'rsatkichi.
### Salbiy
- Har stanokka hisoblagich va shlyuz xarajati; o'rnatishni elektrik qiladi.
### Xavflar
- Hisoblagich bilan PLK bitta RS485 simiga ulanmasligi kerak (ASCII va RTU aralashmaydi).
