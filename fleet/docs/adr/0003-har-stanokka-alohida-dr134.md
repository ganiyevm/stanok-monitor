# ADR-0003: Har stanokka alohida DR134, alohida 10.10.1.x tarmog'i

**Sana**: 2026-11-18
**Holati**: qabul qilingan
**Qaror qabul qilganlar**: zavod muhandisi, Claude

## Kontekst
40 ta bir xil stanok (SBY-850x6G, Delta DVP32ES2) ulanadi. Har PLK'da bitta RS485 port bor.
Modbus ASCII 9600 bod atrofida sekin: bitta stanokning to'liq o'qilishi 12 ta so'rov. Ofis tarmog'i
192.168.0.x da, unda boshqa qurilmalar ham bor.

## Qaror
Har stanokka bitta USR-DR134 shlyuzi o'rnatiladi. IP manzili `10.10.1.1NN` (NN = stanok raqami),
port 8234, alohida switch'lar orqali alohida tarmoqda. Sex monitori har stanokni alohida oqimda
o'qiydi.

## Ko'rib chiqilgan variantlar
### 40 stanok bitta umumiy RS485 shinasida (har PLK'ga alohida stansiya raqami)
- **Afzalliklari**: shlyuz kam, arzonroq.
- **Kamchiliklari**: 40 × 12 so'rov bitta sekin shinada, har stanok bir necha daqiqada bir yangilanadi;
  bitta uzilgan sim hammasini to'xtatadi; har PLK'da stansiya raqamini o'zgartirish kerak.
- **Nega emas**: tezlik va ishonchlilik yetarli emas.
### 192.168.0.x ofis tarmog'ida qoldirish
- **Nega emas**: manzillar to'qnashuvi, ofis kompyuterlaridan stanoklarga ochiq yo'l.

## Oqibatlar
### Ijobiy
- Bitta stanok yoki sim buzilsa, faqat o'sha katak kulrang bo'ladi.
- Har stanok har 3 soniyada yangilanadi.
### Salbiy
- 40 ta shlyuz va switch'lar xarajati; har DR134'ni o'rnatishdan oldin sozlash kerak.
### Xavflar
- Ikki DR134'ga bitta IP berilsa, adashib o'qiladi. `looms.json` yuklanayotganda takrorlangan
  manzilni tekshiradi; o'rnatishda IP stanokka yopishtiriladi.
