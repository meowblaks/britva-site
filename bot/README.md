# Telegram-бот для записи в барбершоп BRITVA

Клиент записывается к мастеру прямо в Telegram за минуту, без звонков.

Что умеет:
- выбор услуги, даты и времени
- сбор имени и телефона
- занятое время нельзя выбрать повторно
- отмена записи на любом шаге

Как запустить:
1. Нужен Python 3.10+. Установите библиотеки: `pip install -r requirements.txt`
2. Получите токен у @BotFather и вставьте его в файл `.env` (образец — `.env.example`).
3. Запустите бота: `python bot.py`

Стек: Python · aiogram 3 · SQLite

---

# BRITVA Barbershop Booking Telegram Bot

Clients can book an appointment with a barber right in Telegram in under a minute — no phone calls needed.

Features:
- choose a service, date and time
- collect the client's name and phone number
- booked time slots can't be selected twice
- cancel the booking at any step

How to run:
1. Requires Python 3.10+. Install dependencies: `pip install -r requirements.txt`
2. Get a token from @BotFather and put it in the `.env` file (see `.env.example`).
3. Start the bot: `python bot.py`

Stack: Python · aiogram 3 · SQLite
