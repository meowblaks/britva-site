# Telegram-бот для записи в барбершоп BRITVA

Клиент записывается к мастеру прямо в Telegram за минуту, без звонков.

Что умеет:
- выбор услуги, даты и времени
- сбор имени и телефона, проверка данных перед подтверждением
- занятое и прошедшее время нельзя выбрать
- уведомления мастеру о каждой новой записи и отмене
- напоминания клиенту за день и за 2 часа до визита с кнопками «Приду» и «Отменить запись»
- отмена записи на любом шаге

Как запустить:
1. Нужен Python 3.10+. Установите библиотеки: `pip install -r requirements.txt`
2. Скопируйте `.env.example` в `.env` и заполните:
   - `BOT_TOKEN` — токен бота от @BotFather
   - `ADMIN_ID` — Telegram ID мастера для уведомлений (узнать можно у @userinfobot)
   - `TEST_MODE` — оставьте пустым; `1` включает быстрые напоминания для проверки
3. Запустите бота: `python bot.py`

Стек: Python · aiogram 3 · SQLite

---

# BRITVA Barbershop Booking Telegram Bot

Clients can book an appointment with a barber right in Telegram in under a minute — no phone calls needed.

Features:
- choose a service, date and time
- collect name and phone number, review details before confirming
- booked and past time slots can't be selected
- notifications to the barber about every new booking and cancellation
- reminders to the client a day and 2 hours before the visit, with "I'll come" and "Cancel" buttons
- cancel the booking at any step

How to run:
1. Requires Python 3.10+. Install dependencies: `pip install -r requirements.txt`
2. Copy `.env.example` to `.env` and fill in:
   - `BOT_TOKEN` — bot token from @BotFather
   - `ADMIN_ID` — the barber's Telegram ID for notifications (get it from @userinfobot)
   - `TEST_MODE` — leave empty; `1` enables fast reminders for testing
3. Start the bot: `python bot.py`

Stack: Python · aiogram 3 · SQLite
