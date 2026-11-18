# ADR-0005: Yangi stanok faqat "o'qib tekshirish"dan keyin qo'shiladi

**Sana**: 2026-11-18
**Holati**: qabul qilingan
**Qaror qabul qilganlar**: zavod muhandisi, Claude

## Kontekst
Registrlar xaritasi (D66 = tok, D528 = uzunlik, D516 = smena metri, M531 = LIGHT va h.k.) faqat
1-stanokda, panel bilan solishtirib tasdiqlangan. Boshqa stanoklar bir xil model, lekin PLK dasturi
versiyasi farq qilishi mumkin. Foydalanuvchi qoidasi: "aniq bilmasdan taxmin bilan sinab ko'rmaymiz".

## Qaror
Har yangi stanok `looms.json`da `"enabled": false` bilan turadi va quyidagidan keyin yoqiladi:
1. `tools/check_loom.py 10.10.1.1NN` — faqat o'qib, qiymatlarni panel bilan solishtirish.
2. `tools/stanok_backup.py --host ...` va `--diff` — PLK xotirasini 1-stanok bilan solishtirish.

## Ko'rib chiqilgan variantlar
### Hammasini birdaniga yoqish
- **Afzalliklari**: tez.
- **Kamchiliklari**: boshqa xaritali PLK noto'g'ri raqam ko'rsatadi va hech kim sezmaydi.
- **Nega emas**: noto'g'ri hisobot ko'rsatmaslikdan yomonroq.

## Oqibatlar
### Ijobiy
- Ekrandagi har bir raqam tasdiqlangan manzildan keladi.
### Salbiy
- Har stanok uchun ~10 daqiqa qo'shimcha ish (stanok yonida).
### Xavflar
- Xarita farq qilsa, model profili alohida qilinadi (`fleet.py`dagi SBY profili kabi).
