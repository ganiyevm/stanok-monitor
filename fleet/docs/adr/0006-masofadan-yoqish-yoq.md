# ADR-0006: Masofadan yoqish yo'q; tashqaridan faqat ko'rish va login bilan

**Sana**: 2026-11-14 (yozilgan sana: 2026-11-18)
**Holati**: qabul qilingan
**Qaror qabul qilganlar**: zavod muhandisi, Claude

## Kontekst
Stanok yonida odamlar ishlaydi. PLK'da ishga tushirish biti mavjud, lekin stanokni kutilmaganda
yoqish jarohatga olib kelishi mumkin. Telefondan kuzatish Cloudflare tunnel orqali internetga
chiqariladi.

## Qaror
- Masofadan YOQISH funksiyasi umuman yozilmaydi. Masofadan faqat to'xtatish mumkin (PIN bilan).
- Uzilish himoyasini masofadan faqat qayta yoqish mumkin, o'chirish mumkin emas.
- Tashqi port (127.0.0.1:8091, tunnel orqali) login-parol talab qiladi va har qanday boshqaruvni
  rad etadi. Boshqaruv faqat zavod tarmog'idan.

## Ko'rib chiqilgan variantlar
### Telefondan to'liq boshqarish
- **Afzalliklari**: master zavoddan tashqarida ham boshqaradi.
- **Kamchiliklari**: stanok yonidagi odam uchun xavf; parol o'g'irlansa butun boshqaruv ochiladi.
- **Nega emas**: xavfsizlik ishlab chiqarish qulayligidan ustun.

## Oqibatlar
### Ijobiy
- Eng xavfli harakat (yoqish) har doim stanok yonidagi odamning qo'lida.
### Salbiy
- To'xtagan stanokni yoqish uchun odam stanok yoniga borishi kerak.
### Xavflar
- PIN hozircha 3 xonali (123), 6 xonaliga almashtirilishi kerak.
