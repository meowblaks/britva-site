# bot.py — главный файл бота: сценарий записи клиента.
# Библиотека aiogram 3 асинхронная, поэтому функции помечены async,
# а перед их вызовом ставится await.

import asyncio
import os
from datetime import date, datetime, timedelta
from html import escape  # экранирует < > & в пользовательском вводе

from dotenv import load_dotenv  # читает файл .env в переменные окружения

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties  # настройки бота по умолчанию
from aiogram.enums import ParseMode                      # режим разметки текста
from aiogram.filters import Command, CommandStart  # фильтры команд /start и /cancel
from aiogram.fsm.context import FSMContext         # хранилище шагов диалога
from aiogram.fsm.state import State, StatesGroup   # описание шагов (состояния)
from aiogram.types import (
    Message,               # обычное сообщение от пользователя
    CallbackQuery,         # нажатие на inline-кнопку
    InlineKeyboardMarkup,  # inline-клавиатура: кнопки прикреплены к сообщению
    InlineKeyboardButton,  # одна кнопка
)

import db  # наш файл db.py с базой данных SQLite


# ==================== НАСТРОЙКИ ====================

# Токен хранится в файле .env (он не попадает на GitHub — см. .gitignore).
# Шаблон для заполнения — в .env.example: скопируй его в .env и вставь токен.
load_dotenv()          # загружает переменные из .env
TOKEN = os.getenv("BOT_TOKEN", "")  # читаем BOT_TOKEN из окружения

if not TOKEN:
    raise SystemExit(
        "Нет токена бота.\n"
        "Создай файл .env рядом с bot.py со строкой:\n"
        "BOT_TOKEN=твой_токен\n"
        "(шаблон — в файле .env.example)"
    )

# Ссылка на сайт в кнопке приветствия. ЗАГЛУШКА — позже вставь настоящую.
SITE_URL = "https://example.com"

# parse_mode="HTML" — разрешает теги <b>, <i>, <code> во всех сообщениях бота
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()       # диспетчер: ловит сообщения и вызывает наши функции

# Рабочие часы: 9:00–19:00. Последняя запись — на 18:00,
# чтобы к закрытию (19:00) клиента успели обслужить.
WORK_TIMES = [f"{hour}:00" for hour in range(9, 19)]

# Telegram ID администратора из .env (0 — уведомления выключены).
ADMIN_ID = int(os.getenv("ADMIN_ID") or 0)

# Как часто фоновый цикл проверяет, кому пора отправить напоминание (в секундах)
REMINDER_CHECK_SECONDS = 60

# Тестовый режим: включается строкой TEST_MODE=1 в файле .env.
# Нужен, чтобы проверить напоминания, не ожидая целый день:
# "за день" превращается в "за 4 минуты", "за 2 часа" — в "за 2 минуты",
# а время для записи предлагается с шагом 5 минут от текущего.
TEST_MODE = os.getenv("TEST_MODE") == "1"

# За сколько до визита присылать напоминания
DAY_REMINDER_BEFORE = timedelta(minutes=4) if TEST_MODE else timedelta(hours=24)
HOUR_REMINDER_BEFORE = timedelta(minutes=2) if TEST_MODE else timedelta(hours=2)

# Записался позже этого порога до визита — напоминаний не будет.
# В тестовом режиме порога нет: там и так всё происходит за минуты.
MIN_LEAD_BEFORE = timedelta(0) if TEST_MODE else timedelta(hours=3)

# Параметры тестовых слотов времени
TEST_SLOT_STEP_MIN = 5      # шаг сетки — 5 минут
TEST_SLOT_WINDOW_HOURS = 2  # на сколько часов вперёд показывать слоты


# ==================== СОСТОЯНИЯ (FSM) ====================
# FSM ("машина состояний") запоминает, на каком шаге записи находится
# клиент: выбрал ли услугу, ввёл ли имя, телефон и т.д.

class BookingStates(StatesGroup):
    entering_name = State()     # шаг: бот ждёт имя
    entering_phone = State()    # шаг: бот ждёт телефон
    entering_date = State()     # шаг: клиент выбирает день
    entering_time = State()     # шаг: клиент выбирает время
    confirming = State()        # шаг: клиент подтверждает запись


# ==================== КЛАВИАТУРЫ ====================

def back_button(callback_data):
    """Ряд с единственной кнопкой «Назад», ведущей на предыдущий шаг."""
    return [InlineKeyboardButton(text="⬅️ Назад", callback_data=callback_data)]


def services_keyboard(services):
    """Клавиатура с услугами. callback_data вида 'service_1', 'service_2'..."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=name, callback_data=f"service_{service_id}")]
            for service_id, name in services
        ]
    )


def dates_keyboard():
    """Кнопки «Сегодня» и «Завтра» + кнопка «Назад».

    В callback_data кладём саму дату (2026-11-04).
    """
    today = date.today()
    tomorrow = today + timedelta(days=1)  # прибавляем один день
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="☀️ Сегодня", callback_data=f"date_{today.isoformat()}")],
            [InlineKeyboardButton(text="🌙 Завтра", callback_data=f"date_{tomorrow.isoformat()}")],
            back_button("back_phone"),
        ]
    )


def times_keyboard(taken_times, available_times=None):
    """Сетка выбора времени: 3 кнопки в ряд + кнопка «Назад».

    taken_times — какие слоты уже заняты (помечаем ❌).
    available_times — какие слоты вообще показывать; на сегодняшний
    день сюда попадают только часы, которые ещё не наступили.
    None значит "показать все рабочие часы".

    Занятые слоты помечаем ❌ и делаем «неактивными»: Telegram не умеет
    по-настоящему блокировать кнопки, поэтому нажатие на занятую кнопку
    просто показывает подсказку и ничего не меняет.

    Важно: Telegram разрешает в callback_data только буквы, цифры,
    "_" и "-". Двоеточие там запрещено, поэтому "15:00" шифруем
    как "time_15-00", а после нажатия возвращаем двоеточие на место.
    """
    slots = available_times if available_times is not None else WORK_TIMES
    rows, row = [], []
    for time_str in slots:
        if time_str in taken_times:
            text, callback = f"❌ {time_str}", "busy"
        else:
            text, callback = time_str, "time_" + time_str.replace(":", "-")
        row.append(InlineKeyboardButton(text=text, callback_data=callback))
        if len(row) == 3:  # набрали 3 кнопки — переносим на новую строку
            rows.append(row)
            row = []
    if row:            # добавляем последний неполный ряд, если он есть
        rows.append(row)

    rows.append(back_button("back_date"))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_keyboard():
    """Кнопки финального подтверждения записи."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Подтвердить", callback_data="confirm_yes"),
                InlineKeyboardButton(text="❌ Отменить", callback_data="cancel_booking"),
            ],
            back_button("back_time"),
        ]
    )


def reminder_keyboard(booking_id):
    """Кнопки под напоминанием: клиент подтверждает визит или отменяет его."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Приду", callback_data=f"remind_yes_{booking_id}"),
                InlineKeyboardButton(text="❌ Отменить запись", callback_data=f"remind_cancel_{booking_id}"),
            ]
        ]
    )


# ==================== УВЕДОМЛЕНИЯ АДМИНИСТРАТОРУ ====================

async def notify_admin(text):
    """Отправляет сообщение администратору, если ADMIN_ID указан в .env.

    Ошибка отправки (бот заблокирован, ID неверный) не должна
    срывать запись клиента — поэтому заворачиваем в try/except.
    """
    if not ADMIN_ID:
        return
    try:
        await bot.send_message(ADMIN_ID, text)
    except Exception as error:  # noqa
        print(f"Не удалось уведомить администратора: {error}")


# ==================== НАПОМИНАНИЯ КЛИЕНТАМ ====================

async def send_reminder(booking, which):
    """Отправляет клиенту напоминание и помечает его в базе как отправленное."""
    service_name = db.get_service_name(booking["service_id"])
    date_str = date.fromisoformat(booking["booking_date"]).strftime("%d.%m.%Y")

    when = "Визит уже завтра" if which == "day" else "Визит уже совсем скоро"
    text = (
        "⏰ <b>Напоминание о визите</b>\n\n"
        f"{when}:\n\n"
        f"💈 Услуга: <b>{service_name}</b>\n"
        f"🗓 Дата: <b>{date_str}</b>\n"
        f"⏰ Время: <b>{booking['booking_time']}</b>\n\n"
        "Приходите за 5 минут до начала. Если планы изменились — "
        "отмените запись, чтобы освободить время другим."
    )

    await bot.send_message(
        booking["user_id"],
        text,
        reply_markup=reminder_keyboard(booking["id"]),
    )
    db.mark_reminded(booking["id"], which)  # запоминаем: напоминание ушло


async def check_reminders():
    """Смотрит будущие записи и шлёт напоминания тем, кому пора.

    Правила:
    - напоминание "за день" — только если клиент записался заранее
      больше чем за 24 часа до визита (в тестовом режиме окно — 4 минуты);
    - напоминание "за 2 часа" — только если записался больше чем за 3 часа
      (в тестовом режиме окно — 2 минуты);
    - записался позже порога MIN_LEAD_BEFORE — напоминаний нет вовсе
      (в тестовом режиме порога нет);
    - визит уже прошёл — просто ставим флажки, ничего не отправляем
      (иначе бот завалит клиента сообщениями о прошлом).
    """
    now = datetime.now()

    for booking in db.get_upcoming_bookings():
        visit_time = slot_datetime(
            booking["booking_date"], booking["booking_time"]
        )
        remaining = visit_time - now  # сколько осталось до визита

        if remaining.total_seconds() <= 0:  # визит уже прошёл
            db.mark_reminded(booking["id"], "day")
            db.mark_reminded(booking["id"], "hour")
            continue

        # Насколько заранее клиент записался.
        # created_at пустой только у записей до введения этого поля —
        # считаем, что записались "бесконечно" заранее.
        if booking["created_at"]:
            booked_at = datetime.fromisoformat(booking["created_at"])
            lead = visit_time - booked_at
        else:
            lead = timedelta(days=36500)  # ~100 лет — заведомо заранее

        # Поздняя запись: напоминаний не будет — ставим оба флажка сразу
        if lead <= MIN_LEAD_BEFORE:
            db.mark_reminded(booking["id"], "day")
            db.mark_reminded(booking["id"], "hour")
            continue

        if remaining <= HOUR_REMINDER_BEFORE and not booking["remind_hour_sent"]:
            await send_reminder(booking, "hour")
        elif remaining <= DAY_REMINDER_BEFORE and lead > DAY_REMINDER_BEFORE \
                and not booking["remind_day_sent"]:
            await send_reminder(booking, "day")


async def reminder_worker():
    """Фоновый бесконечный цикл: раз в минуту проверяет напоминания.

    asyncio.create_task() запускает его параллельно работе бота.
    """
    while True:
        try:
            await check_reminders()
        except Exception as error:
            print(f"Ошибка проверки напоминаний: {error}")
        await asyncio.sleep(REMINDER_CHECK_SECONDS)


# ==================== ТЕКСТЫ ШАГОВ ====================

WELCOME_TEXT = (
    "✂️ <b>Барбершоп BRITVA</b>\n\n"
    "Здравствуйте! Мы стрижём без спешки: точная работа, "
    "тёплая атмосфера и чашка кофе в придачу ☕\n\n"
    "🕘 Ежедневно с 9:00 до 19:00\n\n"
    "👇 Нажмите «Записаться» — подберём удобное время за минуту."
)

STEP_SERVICE = "🪒 <b>Шаг 1 из 5</b> · Услуга\n\nВыберите, что будем делать:"

if TEST_MODE:
    WELCOME_TEXT += (
        "\n\n🧪 <b>Тестовый режим</b>: напоминания приходят "
        "за 4 минуты и за 2 минуты до визита."
    )
STEP_NAME = "✍️ <b>Шаг 2 из 5</b> · Имя\n\nКак вас зовут? Напишите имя сообщением."
STEP_PHONE = "📞 <b>Шаг 3 из 5</b> · Телефон\n\nПриятно познакомиться, <b>{name}</b>! "
STEP_PHONE += "Оставьте номер — напомним о записи за день.\n\nНапример: <code>+79991234567</code>"
STEP_DATE = "📅 <b>Шаг 4 из 5</b> · Дата\n\nНа какой день вас записать?"
STEP_TIME = "⏰ <b>Шаг 5 из 5</b> · Время\n\nВыберите удобное время:"


# ==================== КОМАНДЫ ====================

@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    """Команда /start: красивое приветствие и кнопка «Записаться»."""
    await state.clear()  # начинаем диалог заново с чистого листа
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✂️ Записаться", callback_data="show_services")],
            [InlineKeyboardButton(text="🌐 Наш сайт", url=SITE_URL)],
        ]
    )
    await message.answer(WELCOME_TEXT, reply_markup=keyboard)


@dp.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    """Команда /cancel: отменяет начатую запись в любой момент."""
    await state.clear()
    await message.answer(
        "🚫 Запись отменена.\n\n"
        "Когда захотите записаться — нажмите /start. Будем рады видеть вас! ✂️"
    )


# ==================== ШАГ 1: ВЫБОР УСЛУГИ ====================

@dp.callback_query(F.data == "show_services")
async def show_services(callback: CallbackQuery, state: FSMContext):
    """Нажали «Записаться» — показываем кнопки с услугами."""
    await state.clear()
    services = db.get_services()
    await callback.message.answer(STEP_SERVICE, reply_markup=services_keyboard(services))
    await callback.answer()  # убирает "часики" ожидания на кнопке


@dp.callback_query(F.data.startswith("service_"))
async def choose_service(callback: CallbackQuery, state: FSMContext):
    """Клиент выбрал услугу — сохраняем её и просим имя.

    Здесь нет фильтра состояния: кнопки услуг работают всегда,
    даже если бот перезапускался и "забыл" шаги диалога.
    """
    # "service_2" -> "2" -> 2
    service_id = int(callback.data.split("_")[1])
    service_name = db.get_service_name(service_id)

    await state.update_data(service_id=service_id)  # запоминаем услугу в FSM
    await state.set_state(BookingStates.entering_name)

    await callback.message.answer(
        f"Вы выбрали услугу: <b>{service_name}</b>.\n\n{STEP_NAME}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[back_button("back_services")]),
    )
    await callback.answer()


# ==================== ШАГ 2: ИМЯ ====================

@dp.message(BookingStates.entering_name)
async def process_name(message: Message, state: FSMContext):
    """Проверяем имя и просим телефон."""
    text = (message.text or "").strip()  # фото и стикеры дадут пустой текст

    # Проверка: от 2 до 50 символов, только буквы, пробелы и дефисы
    if not (2 <= len(text) <= 50) or not all(ch.isalpha() or ch in " -" for ch in text):
        await message.answer(
            "🤔 Имя должно состоять из букв (2–50 символов).\n\nПопробуйте ещё раз:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[back_button("back_services")]),
        )
        return  # состояние не меняется — бот снова ждёт имя

    await state.update_data(name=text)
    await state.set_state(BookingStates.entering_phone)

    await message.answer(
        STEP_PHONE.format(name=text),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[back_button("back_name")]),
    )


# ==================== ШАГ 3: ТЕЛЕФОН ====================

@dp.message(BookingStates.entering_phone)
async def process_phone(message: Message, state: FSMContext):
    """Проверяем телефон и показываем выбор дня."""
    text = message.text or ""

    # Считаем только цифры: пробелы, скобки и дефисы игнорируем
    digits = "".join(ch for ch in text if ch.isdigit())

    # В номере обычно 10–12 цифр (10 без кода страны, 11–12 с ним)
    if not 10 <= len(digits) <= 12:
        await message.answer(
            "📞 Не похоже на телефон. Введите номер ещё раз:\n\nНапример: <code>+79991234567</code>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[back_button("back_name")]),
        )
        return

    await state.update_data(phone=text.strip())
    await state.set_state(BookingStates.entering_date)

    await message.answer(STEP_DATE, reply_markup=dates_keyboard())


# ==================== ШАГ 4: ДЕНЬ И ВРЕМЯ ====================

def next_5min_boundary(dt):
    """Округляет время вверх до ближайших 5 минут (строго в будущем).

    Например, 20:46 -> 20:50, а 20:50:01 -> 20:55.
    """
    minutes = dt.hour * 60 + dt.minute
    if dt.second or dt.microsecond:
        minutes += 1  # частично прошедшая минута уже не подходит
    minutes = ((minutes + 4) // 5) * 5  # округляем вверх до кратного 5
    boundary = dt.replace(hour=0, minute=0, second=0, microsecond=0) \
        + timedelta(minutes=minutes)
    if boundary <= dt:  # ровно на границе — берём следующие 5 минут
        boundary += timedelta(minutes=5)
    return boundary


def test_mode_times():
    """Слоты для тестового режима: каждые 5 минут от текущего времени.

    Показываем ближайшие TEST_SLOT_WINDOW_HOURS часов, чтобы можно было
    записаться "на скорую руку" и быстро проверить напоминания.
    Через полночь не пересекаемся: слоты не заходят на следующий день.
    """
    start = next_5min_boundary(datetime.now())
    midnight = (start + timedelta(days=1)).replace(hour=0, minute=0)
    end = min(start + timedelta(hours=TEST_SLOT_WINDOW_HOURS), midnight)

    times, t = [], start
    while t < end:
        times.append(t.strftime("%H:%M"))
        t += timedelta(minutes=TEST_SLOT_STEP_MIN)
    return times


def slot_datetime(date_iso, time_str):
    """Собирает datetime из даты и времени слота.

    Важно: время хранится как "9:00" (без нуля впереди), а строгий
    datetime.fromisoformat("... 9:00") такое не понимает и падает с ошибкой.
    strptime с шаблоном "%H:%M" принимает и "9:00", и "09:00".
    """
    return datetime.strptime(f"{date_iso} {time_str}", "%Y-%m-%d %H:%M")


def future_times(date_iso, times):
    """Оставляет только слоты, которые ещё не наступили.

    Для завтрашнего дня и позже все часы считаются будущими.
    Используется, чтобы на «Сегодня» нельзя было выбрать прошедшее время.
    """
    if date_iso != date.today().isoformat():
        return list(times)  # не сегодня — все часы в будущем

    now = datetime.now()
    return [
        t for t in times
        if slot_datetime(date_iso, t) > now
    ]


async def offer_times(callback: CallbackQuery, state: FSMContext,
                      date_iso, alert=None):
    """Показывает клиенту выбор времени на день date_iso.

    На сегодняшний день прошедшие часы скрываются. Если будущих часов
    не осталось вовсе — бот сам переключает запись на завтра и сразу
    показывает завтрашнее расписание.

    В тестовом режиме (TEST_MODE=1 в .env) вместо обычных рабочих часов
    предлагаются слоты с шагом 5 минут от текущего времени.

    alert — необязательный текст всплывашки (например, "время занято").
    """
    base_times = test_mode_times() if TEST_MODE else WORK_TIMES
    available = future_times(date_iso, base_times)

    await state.set_state(BookingStates.entering_time)

    if not available:
        # Сегодня записаться уже некуда — предлагаем завтра
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        await state.update_data(booking_date=tomorrow)
        taken = db.get_taken_times(tomorrow)
        await callback.message.answer(
            "🌙 На сегодня свободного времени уже не осталось.\n"
            "Вот расписание на завтра:"
        )
        await callback.message.answer(
            STEP_TIME, reply_markup=times_keyboard(taken)
        )
    else:
        await state.update_data(booking_date=date_iso)
        taken = db.get_taken_times(date_iso)
        await callback.message.answer(
            STEP_TIME, reply_markup=times_keyboard(taken, available)
        )

    if alert:  # например, "выбранное время только что заняли"
        await callback.answer(alert, show_alert=True)
    else:
        await callback.answer()


@dp.callback_query(BookingStates.entering_date, F.data.startswith("date_"))
async def choose_date(callback: CallbackQuery, state: FSMContext):
    """Клиент выбрал день — показываем кнопки со временем.

    Прошедшие часы сегодняшнего дня не показываются вовсе;
    если на сегодня ничего не осталось — бот предлагает завтра.
    """
    # "date_2026-11-04" -> "2026-11-04" (дефис в callback_data разрешён)
    await offer_times(callback, state, callback.data[5:])


@dp.callback_query(F.data == "busy")
async def busy_time(callback: CallbackQuery):
    """Нажали на занятое время — мягко объясняем, что оно недоступно."""
    await callback.answer("Это время уже занято, выберите другое ⏰", show_alert=True)


# ==================== ШАГ 5: ПОДТВЕРЖДЕНИЕ ====================

@dp.callback_query(BookingStates.entering_time, F.data.startswith("time_"))
async def choose_time(callback: CallbackQuery, state: FSMContext):
    """Клиент выбрал время — показываем красивое подтверждение."""
    # "time_15-00" -> "15-00" -> "15:00"
    time_str = callback.data[5:].replace("-", ":")

    data = await state.get_data()  # всё, что копили в FSM: услуга, имя, телефон, день

    # Нельзя записать двух клиентов на одно и то же время
    # (двойная проверка: вдруг слот заняли, пока клиент выбирал)
    if db.is_time_taken(data["booking_date"], time_str):
        await offer_times(callback, state, data["booking_date"],
                          alert="Это время уже занято, выберите другое ⏰")
        return

    await state.update_data(booking_time=time_str)
    await state.set_state(BookingStates.confirming)

    # Дата для человека: "2026-11-04" -> "04.11.2026"
    date_str = date.fromisoformat(data["booking_date"]).strftime("%d.%m.%Y")
    service_name = db.get_service_name(data["service_id"])
    # Экранируем пользовательский ввод: символ "<" в телефоне сломал бы HTML
    name_esc = escape(data["name"])
    phone_esc = escape(data["phone"])

    summary = (
        "📋 <b>Проверьте детали записи</b>\n\n"
        f"💈 Услуга: <b>{service_name}</b>\n"
        f"👤 Имя: <b>{name_esc}</b>\n"
        f"📞 Телефон: <b>{phone_esc}</b>\n"
        f"🗓 Дата: <b>{date_str}</b>\n"
        f"⏰ Время: <b>{time_str}</b>\n\n"
        "Всё верно?"
    )
    await callback.message.answer(summary, reply_markup=confirm_keyboard())
    await callback.answer()


@dp.callback_query(BookingStates.confirming, F.data == "confirm_yes")
async def confirm_booking(callback: CallbackQuery, state: FSMContext):
    """Клиент подтвердил — сохраняем запись в базу."""
    data = await state.get_data()

    # Последняя проверка: пока клиент читал подтверждение,
    # слот мог занять кто-то другой
    if db.is_time_taken(data["booking_date"], data["booking_time"]):
        await offer_times(callback, state, data["booking_date"],
                          alert="Увы, это время уже заняли. Выберите другое ⏰")
        return

    # Сохраняем запись в базу данных и получаем её номер
    booking_id = db.add_booking(
        user_id=callback.from_user.id,
        service_id=data["service_id"],
        client_name=data["name"],
        client_phone=data["phone"],
        booking_date=data["booking_date"],
        booking_time=data["booking_time"],
    )

    # Дата для человека: "2026-11-04" -> "04.11.2026"
    date_str = date.fromisoformat(data["booking_date"]).strftime("%d.%m.%Y")
    service_name = db.get_service_name(data["service_id"])

    confirmation = (
        "🎉 <b>Запись оформлена!</b>\n\n"
        "─────────────────\n"
        f"🎫 Номер записи: <b>№ {booking_id}</b>\n"
        f"💈 Услуга: <b>{service_name}</b>\n"
        f"👤 Имя: <b>{escape(data['name'])}</b>\n"
        f"📞 Телефон: <b>{escape(data['phone'])}</b>\n"
        f"🗓 Когда: <b>{date_str} в {data['booking_time']}</b>\n"
        "─────────────────\n\n"
        "Приходите за 5 минут до начала. Ждём вас! ✂️☕"
    )
    await callback.message.answer(confirmation)
    await callback.answer()
    await state.clear()  # диалог завершён — сбрасываем шаги

    # Сообщаем администратору о новой записи
    await notify_admin(
        "🆕 <b>Новая запись!</b>\n\n"
        f"🎫 Номер записи: <b>№ {booking_id}</b>\n"
        f"👤 Имя: <b>{escape(data['name'])}</b>\n"
        f"📞 Телефон: <b>{escape(data['phone'])}</b>\n"
        f"💈 Услуга: <b>{service_name}</b>\n"
        f"🗓 Когда: <b>{date_str} в {data['booking_time']}</b>"
    )


@dp.callback_query(BookingStates.confirming, F.data == "cancel_booking")
async def cancel_booking(callback: CallbackQuery, state: FSMContext):
    """Клиент передумал на шаге подтверждения."""
    await state.clear()
    await callback.message.answer(
        "🚫 Запись отменена.\n\nКогда захотите записаться — нажмите /start. ✂️"
    )
    await callback.answer()


# ==================== КНОПКИ ПОД НАПОМИНАНИЕМ ====================
# Ответы на напоминание: клиент подтверждает визит или отменяет запись.
# Запись тут уже существует в базе, поэтому отмена удаляет её из базы.

@dp.callback_query(F.data.startswith("remind_yes_"))
async def reminder_confirmed(callback: CallbackQuery):
    """Клиент нажал «Приду» — просто убираем кнопки и радуемся."""
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer("Отлично, ждём вас! ✂️☕")
    await callback.answer()


@dp.callback_query(F.data.startswith("remind_cancel_"))
async def reminder_cancelled(callback: CallbackQuery):
    """Клиент отменил запись по напоминанию: удаляем, освобождаем время,
    сообщаем администратору."""
    # "remind_cancel_7" -> 7
    booking_id = int(callback.data.split("_")[2])

    booking = db.get_booking(booking_id)  # берём данные ДО удаления
    if not booking or not db.delete_booking(booking_id):
        # Записи уже нет (кто-то отменил раньше или бот был офлайн)
        await callback.answer("Эта запись уже не действует", show_alert=True)
        return

    date_str = date.fromisoformat(booking["booking_date"]).strftime("%d.%m.%Y")

    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(
        "🚫 Запись отменена, время освободилось.\n\n"
        "Когда захотите записаться снова — просто напишите боту. ✂️"
    )
    await callback.answer()

    await notify_admin(
        "❌ <b>Отмена записи</b>\n\n"
        f"🎫 Клиент отменил запись <b>№ {booking_id}</b> по напоминанию\n"
        f"👤 Имя: <b>{escape(booking['client_name'])}</b>\n"
        f"📞 Телефон: <b>{escape(booking['client_phone'])}</b>\n"
        f"💈 Услуга: <b>{db.get_service_name(booking['service_id'])}</b>\n"
        f"🗓 Когда: <b>{date_str} в {booking['booking_time']}</b>\n\n"
        "Время снова свободно."
    )


# ==================== КНОПКИ «НАЗАД» ====================
# Эти обработчики стоят без фильтра состояния: кнопка «Назад»
# должна работать на любом шаге. Каждый шаг возвращает клиента
# на предыдущий, сохраняя уже введённые данные.

@dp.callback_query(F.data == "back_services")
async def back_to_services(callback: CallbackQuery, state: FSMContext):
    """Назад к выбору услуги — обнуляем всё, что вводили после неё."""
    await state.clear()
    services = db.get_services()
    await callback.message.answer(STEP_SERVICE, reply_markup=services_keyboard(services))
    await callback.answer()


@dp.callback_query(F.data == "back_name")
async def back_to_name(callback: CallbackQuery, state: FSMContext):
    """Назад к вводу имени — имя и телефон забываем, услугу помним."""
    data = await state.get_data()
    await state.set_state(BookingStates.entering_name)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[back_button("back_services")])
    if "service_id" in data:
        service_name = db.get_service_name(data["service_id"])
        await callback.message.answer(
            f"Возвращаемся. Услуга: <b>{service_name}</b>.\n\n{STEP_NAME}",
            reply_markup=keyboard,
        )
    else:
        await callback.message.answer(STEP_NAME, reply_markup=keyboard)
    await callback.answer()


@dp.callback_query(F.data == "back_phone")
async def back_to_phone(callback: CallbackQuery, state: FSMContext):
    """Назад к вводу телефона — телефон забываем, имя помним."""
    data = await state.get_data()
    await state.set_state(BookingStates.entering_phone)

    name = data.get("name", "")
    await callback.message.answer(
        STEP_PHONE.format(name=name) if name else STEP_PHONE.format(name="друг"),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[back_button("back_name")]),
    )
    await callback.answer()


@dp.callback_query(F.data == "back_date")
async def back_to_date(callback: CallbackQuery, state: FSMContext):
    """Назад к выбору дня."""
    await state.set_state(BookingStates.entering_date)
    await callback.message.answer(STEP_DATE, reply_markup=dates_keyboard())
    await callback.answer()


@dp.callback_query(F.data == "back_time")
async def back_to_time(callback: CallbackQuery, state: FSMContext):
    """Назад к выбору времени — слоты обновляем на свежие."""
    data = await state.get_data()
    date_iso = data.get("booking_date")

    if not date_iso:  # данных нет — начинаем с выбора дня
        await state.set_state(BookingStates.entering_date)
        await callback.message.answer(STEP_DATE, reply_markup=dates_keyboard())
        await callback.answer()
        return

    await offer_times(callback, state, date_iso)


# ==================== ЗАПУСК ====================

async def main():
    db.init_db()  # создаём базу и таблицы, если их ещё нет
    print("Бот запущен! Открой его в Telegram и нажми /start. Остановка: Ctrl+C")

    # Фоновый цикл напоминаний: живёт параллельно с приёмом сообщений.
    # Ссылку на задачу сохраняем, чтобы Python её не "собрал" как мусор.
    background_tasks.add(asyncio.create_task(reminder_worker()))

    # Бот начинает слушать сообщения от Telegram (бесконечный цикл)
    await dp.start_polling(bot)


# Точка входа: запускаем main через asyncio
if __name__ == "__main__":
    # Множество фоновых задач, чтобы Python их не удалял во время работы
    background_tasks = set()
    asyncio.run(main())
