# ADR-0004: Hozir SQLite, markaziy server bo'lganda PostgreSQL

**Sana**: 2026-11-18
**Holati**: qabul qilingan
**Qaror qabul qilganlar**: zavod muhandisi, Claude

## Kontekst
Bitta stanok monitori CSV fayllarga yozadi. 40 stanok uchun kunlik hisobot, to'xtashlar va smenalar
bo'yicha so'rovlar kerak. Yozuvlar: 40 stanok × 1440 daqiqa ≈ 58 ming qator/kun. Zavod kompyuteri
bitta, internet ishonchsiz. Kelajakda Toshkentdagi markaziy serverga ma'lumot yuborish rejalashtirilgan.

## Qaror
Zavoddagi sex monitori SQLite (`data/fleet.db`, WAL rejimi) ishlatadi. Markaziy server qurilganda u
yerda PostgreSQL bo'ladi, zavod esa unga buferlab yuboradi.

## Ko'rib chiqilgan variantlar
### Darhol PostgreSQL
- **Afzalliklari**: keyin ko'chirish kerak emas.
- **Kamchiliklari**: o'rnatish, parol, zaxira va yangilash xizmati kerak; bitta mashina uchun ortiqcha.
- **Nega emas**: hozirgi hajmga SQLite yetarli.
### CSV'da qolish
- **Nega emas**: 40 stanok bo'yicha hisobot so'rovlari sekin va murakkab.

## Oqibatlar
### Ijobiy
- Qo'shimcha o'rnatish yo'q (Python ichida), bitta faylni nusxalash zaxira hisoblanadi.
### Salbiy
- Bir vaqtda faqat bitta yozuvchi; markaziy serverga o'tishda ko'chirish skripti kerak bo'ladi.
### Xavflar
- Disk to'lishi: yiliga ~21 mln qator (bir necha GB). Eski yozuvlarni arxivlash keyinroq kerak.
