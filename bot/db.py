# db.py — работа с базой данных SQLite.
# База хранится в обычном файле barbershop.db — он создастся сам
# рядом с кодом при первом запуске бота.

import sqlite3
from datetime import date, datetime  # date — отбор записей, datetime — момент записи

# Имя файла базы данных
DB_NAME = "barbershop.db"


def init_db():
    """Создаёт таблицы и наполняет их при первом запуске."""
    # connect() создаёт файл базы, если его ещё нет
    connection = sqlite3.connect(DB_NAME)
    cursor = connection.cursor()

    # Таблица услуг: id — номер записи (создаётся сам), name — название услуги.
    # IF NOT EXISTS — чтобы не создавать таблицу повторно при каждом запуске.
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS services (
            id   INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL
        )
        """
    )

    # Таблица записей клиентов.
    # booking_date хранится текстом "2026-11-04", booking_time — текстом "15:00".
    # Для учебного проекта текстовых дат достаточно.
    #
    # remind_day_sent / remind_hour_sent — флажки напоминаний:
    # 0 — напоминание ещё не отправлено, 1 — уже отправлено.
    # Чтобы не прислать одно и то же напоминание дважды.
    #
    # created_at — когда клиент записался ("2026-11-05 14:30:00").
    # Нужно, чтобы понять, насколько заранее он записался:
    # поздняя запись не получает напоминание "за день".
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS bookings (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         INTEGER NOT NULL,
            service_id      INTEGER NOT NULL,
            client_name     TEXT    NOT NULL,
            client_phone    TEXT    NOT NULL,
            booking_date    TEXT    NOT NULL,
            booking_time    TEXT    NOT NULL,
            remind_day_sent INTEGER NOT NULL DEFAULT 0,
            remind_hour_sent INTEGER NOT NULL DEFAULT 0,
            created_at      TEXT
        )
        """
    )

    # Миграция для старой базы: если флажков ещё нет — добавляем их.
    # ALTER TABLE позволяет дополнить таблицу, не удаляя старые записи.
    cursor.execute("PRAGMA table_info(bookings)")
    existing_columns = {row[1] for row in cursor.fetchall()}
    for column in ("remind_day_sent", "remind_hour_sent", "created_at"):
        if column not in existing_columns:
            if column == "created_at":
                # NULL у старых записей значит "записались давно":
                # для них напоминания работают по-старому
                cursor.execute("ALTER TABLE bookings ADD COLUMN created_at TEXT")
            else:
                cursor.execute(
                    f"ALTER TABLE bookings ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0"
                )

    # Если таблица услуг пустая — добавляем три услуги
    cursor.execute("SELECT COUNT(*) FROM services")
    if cursor.fetchone()[0] == 0:
        cursor.executemany(
            "INSERT INTO services (name) VALUES (?)",
            [("Стрижка",), ("Борода",), ("Стрижка + борода",)],
        )

    # commit сохраняет изменения, close закрывает соединение
    connection.commit()
    connection.close()


def get_services():
    """Возвращает список услуг: [(1, 'Стрижка'), (2, 'Борода'), ...]."""
    connection = sqlite3.connect(DB_NAME)
    cursor = connection.cursor()

    cursor.execute("SELECT id, name FROM services ORDER BY id")
    services = cursor.fetchall()  # fetchall() забирает все строки результата

    connection.close()
    return services


def get_service_name(service_id):
    """Возвращает название услуги по её номеру."""
    connection = sqlite3.connect(DB_NAME)
    cursor = connection.cursor()

    cursor.execute("SELECT name FROM services WHERE id = ?", (service_id,))
    row = cursor.fetchone()  # одна строка или None, если услуги нет

    connection.close()
    return row[0] if row else "услуга"


def is_time_taken(booking_date, booking_time):
    """True, если на эту дату и время уже есть запись."""
    connection = sqlite3.connect(DB_NAME)
    cursor = connection.cursor()

    # ? — это подстановки: SQLite сам безопасно подставит значения
    cursor.execute(
        "SELECT COUNT(*) FROM bookings WHERE booking_date = ? AND booking_time = ?",
        (booking_date, booking_time),
    )
    taken = cursor.fetchone()[0] > 0

    connection.close()
    return taken


def get_taken_times(booking_date):
    """Возвращает множество занятых времён на дату: {'15:00', '16:00', ...}.

    Нужно для клавиатуры: занятые слоты помечаются как недоступные.
    """
    connection = sqlite3.connect(DB_NAME)
    cursor = connection.cursor()

    cursor.execute(
        "SELECT booking_time FROM bookings WHERE booking_date = ?",
        (booking_date,),
    )
    times = {row[0] for row in cursor.fetchall()}

    connection.close()
    return times


def add_booking(user_id, service_id, client_name, client_phone,
                booking_date, booking_time):
    """Сохраняет запись клиента в базу и возвращает её номер."""
    connection = sqlite3.connect(DB_NAME)
    cursor = connection.cursor()

    cursor.execute(
        """
        INSERT INTO bookings
            (user_id, service_id, client_name, client_phone,
             booking_date, booking_time, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (user_id, service_id, client_name, client_phone,
         booking_date, booking_time, datetime.now().isoformat(sep=" ", timespec="seconds")),
    )
    booking_id = cursor.lastrowid  # номер (id) добавленной записи

    connection.commit()
    connection.close()
    return booking_id


def get_upcoming_bookings():
    """Все записи начиная с сегодняшнего дня — для проверки напоминаний.

    Возвращает строки-словари (sqlite3.Row): к полям обращаемся по имени,
    например booking["user_id"], а не по номеру.
    """
    connection = sqlite3.connect(DB_NAME)
    connection.row_factory = sqlite3.Row  # строки с доступом по имени поля
    cursor = connection.cursor()

    today = date.today().isoformat()
    cursor.execute(
        """
        SELECT id, user_id, service_id, client_name, client_phone,
               booking_date, booking_time, remind_day_sent, remind_hour_sent,
               created_at
        FROM bookings
        WHERE booking_date >= ?
        """,
        (today,),
    )
    bookings = cursor.fetchall()

    connection.close()
    return bookings


def mark_reminded(booking_id, which):
    """Отмечает, что напоминание ('day' или 'hour') уже отправлено."""
    connection = sqlite3.connect(DB_NAME)
    cursor = connection.cursor()

    column = f"remind_{which}_sent"
    cursor.execute(f"UPDATE bookings SET {column} = 1 WHERE id = ?", (booking_id,))

    connection.commit()
    connection.close()


def get_booking(booking_id):
    """Возвращает одну запись по номеру (sqlite3.Row) или None, если её нет."""
    connection = sqlite3.connect(DB_NAME)
    connection.row_factory = sqlite3.Row  # строки с доступом по имени поля
    cursor = connection.cursor()

    cursor.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,))
    booking = cursor.fetchone()

    connection.close()
    return booking


def delete_booking(booking_id):
    """Удаляет запись клиента. Возвращает True, если запись была и удалена."""
    connection = sqlite3.connect(DB_NAME)
    cursor = connection.cursor()

    cursor.execute("DELETE FROM bookings WHERE id = ?", (booking_id,))
    deleted = cursor.rowcount > 0  # сколько строк затронуто: 1 или 0

    connection.commit()
    connection.close()
    return deleted
