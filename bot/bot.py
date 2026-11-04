# bot.py — главный файл бота: сценарий записи клиента.
# Библиотека aiogram 3 асинхронная, поэтому функции помечены async,
# а перед их вызовом ставится await.

import asyncio
import os
from datetime import date, timedelta
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


def times_keyboard(taken_times):
    """Сетка выбора времени: 3 кнопки в ряд + кнопка «Назад».

    Занятые слоты помечаем ❌ и делаем «неактивными»: Telegram не умеет
    по-настоящему блокировать кнопки, поэтому нажатие на занятую кнопку
    просто показывает подсказку и ничего не меняет.

    Важно: Telegram разрешает в callback_data только буквы, цифры,
    "_" и "-". Двоеточие там запрещено, поэтому "15:00" шифруем
    как "time_15-00", а после нажатия возвращаем двоеточие на место.
    """
    rows, row = [], []
    for time_str in WORK_TIMES:
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


# ==================== ТЕКСТЫ ШАГОВ ====================

WELCOME_TEXT = (
    "✂️ <b>Барбершоп BRITVA</b>\n\n"
    "Привет! Мы стрижём без спешки: точная работа, "
    "тёплая атмосфера и чашка кофе в придачу ☕\n\n"
    "🕘 Ежедневно с 9:00 до 19:00\n\n"
    "👇 Жми «Записаться» — подберём удобное время за минуту."
)

STEP_SERVICE = "🪒 <b>Шаг 1 из 5</b> · Услуга\n\nВыбери, что будем делать:"
STEP_NAME = "✍️ <b>Шаг 2 из 5</b> · Имя\n\nКак тебя зовут? Напиши имя сообщением."
STEP_PHONE = "📞 <b>Шаг 3 из 5</b> · Телефон\n\nПриятно познакомиться, <b>{name}</b>! "
STEP_PHONE += "Оставь номер — напомним о записи за день.\n\nНапример: <code>+79991234567</code>"
STEP_DATE = "📅 <b>Шаг 4 из 5</b> · Дата\n\nНа какой день тебя записать?"
STEP_TIME = "⏰ <b>Шаг 5 из 5</b> · Время\n\nВыбери удобное время:"


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
        "Когда захочешь записаться — нажми /start. Будем рады тебя видеть! ✂️"
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
        f"Отлично, выбрал(а) <b>{service_name}</b>.\n\n{STEP_NAME}",
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
            "🤔 Имя должно состоять из букв (2–50 символов).\n\nПопробуй ещё раз:",
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
            "📞 Не похоже на телефон. Введи номер ещё раз:\n\nНапример: <code>+79991234567</code>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[back_button("back_name")]),
        )
        return

    await state.update_data(phone=text.strip())
    await state.set_state(BookingStates.entering_date)

    await message.answer(STEP_DATE, reply_markup=dates_keyboard())


# ==================== ШАГ 4: ДЕНЬ И ВРЕМЯ ====================

@dp.callback_query(BookingStates.entering_date, F.data.startswith("date_"))
async def choose_date(callback: CallbackQuery, state: FSMContext):
    """Клиент выбрал день — показываем кнопки со временем.

    Занятые часы заранее помечаем ❌ на кнопках.
    """
    # "date_2026-11-04" -> "2026-11-04" (дефис в callback_data разрешён)
    date_iso = callback.data[5:]

    await state.update_data(booking_date=date_iso)
    await state.set_state(BookingStates.entering_time)

    taken = db.get_taken_times(date_iso)  # {'15:00', '17:00', ...}
    await callback.message.answer(STEP_TIME, reply_markup=times_keyboard(taken))
    await callback.answer()


@dp.callback_query(F.data == "busy")
async def busy_time(callback: CallbackQuery):
    """Нажали на занятое время — мягко объясняем, что оно недоступно."""
    await callback.answer("Это время уже занято, выбери другое ⏰", show_alert=True)


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
        taken = db.get_taken_times(data["booking_date"])
        await callback.message.answer(STEP_TIME, reply_markup=times_keyboard(taken))
        await callback.answer("Это время уже занято, выбери другое ⏰", show_alert=True)
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
        "📋 <b>Проверь детали записи</b>\n\n"
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
        await state.set_state(BookingStates.entering_time)
        taken = db.get_taken_times(data["booking_date"])
        await callback.message.answer(STEP_TIME, reply_markup=times_keyboard(taken))
        await callback.answer("Увы, это время уже заняли. Выбери другое ⏰", show_alert=True)
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
        "Приходи за 5 минут до начала. Ждём тебя! ✂️☕"
    )
    await callback.message.answer(confirmation)
    await callback.answer()
    await state.clear()  # диалог завершён — сбрасываем шаги


@dp.callback_query(BookingStates.confirming, F.data == "cancel_booking")
async def cancel_booking(callback: CallbackQuery, state: FSMContext):
    """Клиент передумал на шаге подтверждения."""
    await state.clear()
    await callback.message.answer(
        "🚫 Запись отменена.\n\nКогда захочешь записаться — нажми /start. ✂️"
    )
    await callback.answer()


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

    await state.set_state(BookingStates.entering_time)
    taken = db.get_taken_times(date_iso)
    await callback.message.answer(STEP_TIME, reply_markup=times_keyboard(taken))
    await callback.answer()


# ==================== ЗАПУСК ====================

async def main():
    db.init_db()  # создаём базу и таблицы, если их ещё нет
    print("Бот запущен! Открой его в Telegram и нажми /start. Остановка: Ctrl+C")

    # Бот начинает слушать сообщения от Telegram (бесконечный цикл)
    await dp.start_polling(bot)


# Точка входа: запускаем main через asyncio
if __name__ == "__main__":
    asyncio.run(main())
