from aiogram import Bot, Dispatcher
from aiogram.types import Message, CallbackQuery, FSInputFile
from aiogram.filters import CommandStart, Command, StateFilter
import asyncio
import sqlite3
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime 
import dateparser
from openpyxl import Workbook
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext

import os
TOKEN = os.getenv("TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
BOSS_ID = int(os.getenv("BOSS_ID", "0"))

bot = Bot(token=TOKEN)
dp = Dispatcher()
scheduler = AsyncIOScheduler(timezone="Asia/Almaty")
class TaskForm(StatesGroup):
    employee = State()
    task = State()
    deadline = State()

class DoneForm(StatesGroup):
    proof = State()

class CancelTaskForm(StatesGroup):
    reason = State()

class ReassignTaskForm(StatesGroup):
    employee = State()

class RenameEmployeeForm(StatesGroup):
    new_name = State()

class ChangeDeadlineForm(StatesGroup):
    new_deadline = State()

class ManualReminderForm(StatesGroup):
    comment = State()
    
boss_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="➕ Новая задача")],
        [KeyboardButton(text="📋 Активные задачи"), KeyboardButton(text="👤 Сотрудник")],
        [KeyboardButton(text="📌 Мои задачи"), KeyboardButton(text="✔ Выполнено")],
        [KeyboardButton(text="🔁 Переназначить задачу"), KeyboardButton(text="📅 Дедлайн")],
        [KeyboardButton(text="❌ Отменить задачу"), KeyboardButton(text="📣 Напомнить")],
        [KeyboardButton(text="🗂 Архив")]
    ],
    resize_keyboard=True
)

worker_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📌 Мои задачи")],
        [KeyboardButton(text="🗂 Мой архив")],
        [KeyboardButton(text="✔ Выполнено")]
    ],
    resize_keyboard=True
)

admin_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="👥 Сотрудники")],
        [KeyboardButton(text="📊 Отчет")],
        [KeyboardButton(text="🧹 Очистить архив")],
        [KeyboardButton(text="📌 Мои задачи"), KeyboardButton(text="🗂 Мой архив")],
        [KeyboardButton(text="✔ Выполнено")]
    ],
    resize_keyboard=True
)

admin_employees_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📋 Список сотрудников")],
        [KeyboardButton(text="✏️ Переименовать")],
        [KeyboardButton(text="🚫 Уволить"), KeyboardButton(text="✅ Восстановить")],
        [KeyboardButton(text="⬅️ Назад")]
    ],
    resize_keyboard=True
)

admin_last_report = {}
admin_last_report_excel = {}

def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID


def is_boss(user_id: int) -> bool:
    return user_id == BOSS_ID

def save_employee(user_id: int, name: str) -> bool:
    cursor.execute(
        "SELECT display_name FROM employees WHERE telegram_id = ?",
        (user_id,)
    )

    existing_employee = cursor.fetchone()

    if existing_employee:
        cursor.execute(
            """
            UPDATE employees
            SET name = ?
            WHERE telegram_id = ?
            """,
            (name, user_id)
        )

        conn.commit()
        return False

    cursor.execute(
        """
        INSERT INTO employees (telegram_id, name, display_name, is_active)
        VALUES (?, ?, ?, 1)
        """,
        (user_id, name, name)
    )

    conn.commit()
    return True

def get_employee_name(user_id: int) -> str:
    cursor.execute(
        """
        SELECT display_name, name
        FROM employees
        WHERE telegram_id = ?
        """,
        (user_id,)
    )

    employee = cursor.fetchone()

    if employee:
        display_name, telegram_name = employee

        if display_name:
            return display_name

        if telegram_name:
            return telegram_name

    return "Неизвестный сотрудник"


conn = sqlite3.connect("tasks.db")
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id INTEGER,
    task TEXT,
    deadline TEXT,
    status TEXT,
    reminded TEXT DEFAULT ''
)
""")

conn.commit()

cursor.execute("""
CREATE TABLE IF NOT EXISTS employees (
    telegram_id INTEGER PRIMARY KEY,
    name TEXT,
    is_active INTEGER DEFAULT 1
)
""")

conn.commit()

async def notify_admin_delivery_failed(employee_id: int, action: str, task_text: str):
    if not ADMIN_ID:
        return

    employee_name = get_employee_name(employee_id)

    try:
        await bot.send_message(
            ADMIN_ID,
            (
                f"⚠️ Не удалось доставить сообщение сотруднику\n\n"
                f"👤 Сотрудник: {employee_name}\n"
                f"📌 Действие: {action}\n"
                f"📝 Задача: {task_text}"
            )
        )
    except Exception:
        pass

def add_column_if_not_exists(column_name, column_sql):
    cursor.execute("PRAGMA table_info(tasks)")
    columns = [col[1] for col in cursor.fetchall()]

    if column_name not in columns:
        cursor.execute(f"ALTER TABLE tasks ADD COLUMN {column_sql}")
        conn.commit()


add_column_if_not_exists("proof_file_id", "proof_file_id TEXT")
add_column_if_not_exists("proof_file_name", "proof_file_name TEXT")
add_column_if_not_exists("completed_at", "completed_at TEXT")
add_column_if_not_exists("overdue_notified", "overdue_notified INTEGER DEFAULT 0")
add_column_if_not_exists("cancelled_at", "cancelled_at TEXT")

add_column_if_not_exists(
    "last_employee_overdue_reminder",
    "last_employee_overdue_reminder TEXT"
)

add_column_if_not_exists(
    "last_boss_overdue_reminder",
    "last_boss_overdue_reminder TEXT"
)

def add_employee_column_if_not_exists(column_name, column_sql):
    cursor.execute("PRAGMA table_info(employees)")
    columns = [col[1] for col in cursor.fetchall()]

    if column_name not in columns:
        cursor.execute(f"ALTER TABLE employees ADD COLUMN {column_sql}")
        conn.commit()


add_employee_column_if_not_exists("display_name", "display_name TEXT")

@dp.message(CommandStart())
async def start(message: Message):
    user_id = message.from_user.id
    full_name = message.from_user.full_name

    is_new_employee = False

    if not is_boss(user_id):
        is_new_employee = save_employee(user_id, full_name)

        if is_new_employee and not is_admin(user_id):
            try:
                await bot.send_message(
                    ADMIN_ID,
                    (
                        f"👤 Новый сотрудник зарегистрировался\n\n"
                        f"Имя из Telegram: {full_name}"
                    )
                )
            except Exception:
                pass

    if is_admin(user_id):
        await message.answer(
            "Привет 😄 Ты вошла как админ",
            reply_markup=admin_menu
        )
    elif is_boss(user_id):
        await message.answer(
            "Привет 😄 Ты вошёл как шеф",
            reply_markup=boss_menu
        )
    else:
        await message.answer(
            "Привет 😄 Ты вошёл как сотрудник",
            reply_markup=worker_menu
        )

@dp.message(Command("myid"))
async def myid(message: Message):
    await message.answer(str(message.from_user.id))




@dp.message(Command("cancel"), StateFilter("*"))
async def cancel(message: Message, state: FSMContext):
    current_state = await state.get_state()

    if current_state is None:
        await message.answer("Сейчас нечего отменять 😄")
        return

    await state.clear()

    if is_admin(message.from_user.id):
        await message.answer(
            "Действие отменено",
            reply_markup=admin_menu
        )
    elif is_boss(message.from_user.id):
        await message.answer(
            "Действие отменено",
            reply_markup=boss_menu
        )
    else:
        await message.answer(
            "Действие отменено",
            reply_markup=worker_menu
        )

@dp.message(lambda message: message.text and message.text == "👥 Сотрудники")
async def admin_employees_section(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("Этот раздел доступен только админу")
        return

    await message.answer(
        "Раздел сотрудников:",
        reply_markup=admin_employees_menu
    )

@dp.message(lambda message: message.text and message.text == "⬅️ Назад")
async def back_to_main_menu(message: Message):
    user_id = message.from_user.id

    if is_admin(user_id):
        await message.answer(
            "Главное меню админа",
            reply_markup=admin_menu
        )
    elif is_boss(user_id):
        await message.answer(
            "Главное меню шефа",
            reply_markup=boss_menu
        )
    else:
        await message.answer(
            "Главное меню сотрудника",
            reply_markup=worker_menu
        )

@dp.message(lambda message: message.text and message.text == "📊 Отчет")
async def admin_report(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("Отчет доступен только админу")
        return

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Текущий месяц",
                    callback_data="report_period:month"
                )
            ],
            [
                InlineKeyboardButton(
                    text="Текущий год",
                    callback_data="report_period:year"
                )
            ],
            [
                InlineKeyboardButton(
                    text="Все время",
                    callback_data="report_period:all"
                )
            ]
        ]
    )

    await message.answer(
        "Выбери период отчета:",
        reply_markup=keyboard
    )

@dp.message(lambda message: message.text and message.text == "🧹 Очистить архив")
async def admin_clear_archive_start(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("Очищать архив может только админ")
        return

    cursor.execute("""
        SELECT COUNT(*)
        FROM tasks
        WHERE status IN ('Выполнено', 'Отменено')
    """)

    archive_count = cursor.fetchone()[0]

    if archive_count == 0:
        await message.answer(
            "Архив уже пуст",
            reply_markup=admin_menu
        )
        return

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Да, очистить архив",
                    callback_data="confirm_clear_archive"
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Нет, отменить",
                    callback_data="cancel_clear_archive"
                )
            ]
        ]
    )

    await message.answer(
        (
            f"Ты уверена, что хочешь очистить архив?\n\n"
            f"Будет удалено записей: <b>{archive_count}</b>\n\n"
            f"Активные задачи останутся."
        ),
        parse_mode="HTML",
        reply_markup=keyboard
    )

@dp.callback_query(lambda callback: callback.data == "confirm_clear_archive")
async def admin_confirm_clear_archive(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Очищать архив может только админ", show_alert=True)
        return

    cursor.execute("""
        DELETE FROM tasks
        WHERE status IN ('Выполнено', 'Отменено')
    """)

    deleted_count = cursor.rowcount
    conn.commit()

    await callback.message.answer(
        f"Архив очищен ✅\n\nУдалено записей: {deleted_count}",
        reply_markup=admin_menu
    )

    await callback.answer()

@dp.callback_query(lambda callback: callback.data == "cancel_clear_archive")
async def admin_cancel_clear_archive(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Очищать архив может только админ", show_alert=True)
        return

    await callback.message.answer(
        "Очистка архива отменена",
        reply_markup=admin_menu
    )

    await callback.answer()

def build_admin_report(period: str, employee_filter: str = "all") -> str:
    now = datetime.now()
    today = now.date()

    if period == "month":
        period_title = "текущий месяц"
    elif period == "year":
        period_title = "текущий год"
    else:
        period_title = "все время"

    if employee_filter == "all":
        employee_title = "все сотрудники"
    else:
        employee_title = get_employee_name(int(employee_filter))

    cursor.execute("""
        SELECT employee_id, task, deadline, status, completed_at, cancelled_at
        FROM tasks
        ORDER BY id DESC
    """)

    tasks = cursor.fetchall()

    completed_count = 0
    cancelled_count = 0
    active_tasks = []
    overdue_tasks = []

    def in_period(date_text):
        if period == "all":
            return True

        if not date_text:
            return False

        try:
            dt = datetime.strptime(date_text, "%d/%m/%Y %H:%M")
        except:
            return False

        if period == "month":
            return dt.month == now.month and dt.year == now.year

        if period == "year":
            return dt.year == now.year

        return False

    for employee_id, task_text, deadline, status, completed_at, cancelled_at in tasks:
        if employee_filter != "all" and employee_id != int(employee_filter):
            continue

        employee_name = get_employee_name(employee_id)

        if status == "Выполнено" and in_period(completed_at):
            completed_count += 1

        elif status == "Отменено" and in_period(cancelled_at):
            cancelled_count += 1

        elif status == "В процессе":
            active_tasks.append((employee_name, task_text, deadline))

            try:
                deadline_date = datetime.strptime(deadline, "%d/%m/%Y").date()

                if deadline_date < today:
                    overdue_tasks.append((employee_name, task_text, deadline))
            except:
                pass

    text = (
        f"<b>📊 Отчет</b>\n\n"
        f"📅 Период: <b>{period_title}</b>\n"
        f"👤 Сотрудник: <b>{employee_title}</b>\n\n"
        f"✅ Выполнено: <b>{completed_count}</b>\n"
        f"❌ Отменено: <b>{cancelled_count}</b>\n"
        f"📌 Активных задач сейчас: <b>{len(active_tasks)}</b>\n"
        f"⚠️ Просроченных задач сейчас: <b>{len(overdue_tasks)}</b>\n\n"
    )

    if active_tasks:
        text += "<b>📌 Невыполненные задачи:</b>\n\n"

        for employee_name, task_text, deadline in active_tasks:
            text += (
                f"👤 {employee_name}\n"
                f"📝 {task_text}\n"
                f"📅 Дедлайн: {deadline}\n\n"
            )
    else:
        text += "Невыполненных задач сейчас нет ✅"

    return text


def build_admin_report_excel(period: str, employee_filter: str = "all") -> str:
    now = datetime.now()

    if period == "month":
        period_title = "Текущий месяц"
    elif period == "year":
        period_title = "Текущий год"
    else:
        period_title = "Все время"

    if employee_filter == "all":
        employee_title = "Все сотрудники"
    else:
        employee_title = get_employee_name(int(employee_filter))

    def in_period(date_text):
        if period == "all":
            return True

        if not date_text:
            return False

        try:
            dt = datetime.strptime(date_text, "%d/%m/%Y %H:%M")
        except:
            return False

        if period == "month":
            return dt.month == now.month and dt.year == now.year

        if period == "year":
            return dt.year == now.year

        return False

    cursor.execute("""
        SELECT employee_id, task, deadline, status, completed_at, cancelled_at
        FROM tasks
        ORDER BY id DESC
    """)

    tasks = cursor.fetchall()

    wb = Workbook()
    ws = wb.active
    ws.title = "Отчет"

    ws.append(["Отчет"])
    ws.append(["Период", period_title])
    ws.append(["Сотрудник", employee_title])
    ws.append([])

    ws.append([
        "Сотрудник",
        "Задача",
        "Дедлайн",
        "Статус",
        "Дата выполнения",
        "Дата отмены"
    ])

    for employee_id, task_text, deadline, status, completed_at, cancelled_at in tasks:
        if employee_filter != "all" and employee_id != int(employee_filter):
            continue

        if status == "Выполнено" and not in_period(completed_at):
            continue

        if status == "Отменено" and not in_period(cancelled_at):
            continue

        employee_name = get_employee_name(employee_id)

        ws.append([
            employee_name,
            task_text,
            deadline,
            status,
            completed_at or "",
            cancelled_at or ""
        ])

    file_name = f"admin_report_{period}_{employee_filter}.xlsx"
    file_path = file_name

    wb.save(file_path)

    return file_path

@dp.callback_query(lambda callback: callback.data and callback.data.startswith("report_employee:"))
async def admin_report_employee(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Отчет доступен только админу", show_alert=True)
        return

    _, period, employee_filter = callback.data.split(":")

    report_text = build_admin_report(period, employee_filter)
    excel_path = build_admin_report_excel(period, employee_filter)

    admin_last_report[callback.from_user.id] = report_text
    admin_last_report_excel[callback.from_user.id] = excel_path

    await callback.message.answer(
        report_text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="📤 Отправить текст шефу",
                        callback_data="send_report_to_boss"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="📎 Скачать Excel",
                        callback_data="download_report_excel"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="📎 Отправить Excel шефу",
                        callback_data="send_report_excel_to_boss"
                    )
                ]
            ]
        )
    )

    await callback.answer()

@dp.callback_query(lambda callback: callback.data and callback.data.startswith("report_period:"))
async def admin_report_period(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Отчет доступен только админу", show_alert=True)
        return

    period = callback.data.split(":")[1]

    keyboard = [
        [
            InlineKeyboardButton(
                text="Все сотрудники",
                callback_data=f"report_employee:{period}:all"
            )
        ]
    ]

    cursor.execute("""
        SELECT telegram_id, display_name, name
        FROM employees
        ORDER BY display_name
    """)

    employees = cursor.fetchall()

    for telegram_id, display_name, telegram_name in employees:
        employee_name = display_name or telegram_name or "Без имени"

        keyboard.append([
            InlineKeyboardButton(
                text=employee_name,
                callback_data=f"report_employee:{period}:{telegram_id}"
            )
        ])

    await callback.message.answer(
        "Выбери сотрудника для отчета:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
    )

    await callback.answer()

@dp.callback_query(lambda callback: callback.data == "send_report_to_boss")
async def send_report_to_boss(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Отправлять отчет может только админ", show_alert=True)
        return

    report_text = admin_last_report.get(callback.from_user.id)

    if not report_text:
        await callback.message.answer(
            "Сначала сформируй отчет через кнопку 📊 Отчет",
            reply_markup=admin_menu
        )
        await callback.answer()
        return

    try:
        await bot.send_message(
            BOSS_ID,
            report_text,
            parse_mode="HTML"
        )

        await callback.message.answer(
            "Отчет отправлен шефу ✅",
            reply_markup=admin_menu
        )

    except Exception:
        await callback.message.answer(
            "Не смогла отправить отчет шефу. Проверь, что шеф открыл бота и нажал /start.",
            reply_markup=admin_menu
        )

    await callback.answer()

@dp.callback_query(lambda callback: callback.data == "download_report_excel")
async def download_report_excel(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Скачивать отчет может только админ", show_alert=True)
        return

    excel_path = admin_last_report_excel.get(callback.from_user.id)

    if not excel_path:
        await callback.message.answer(
            "Сначала сформируй отчет через кнопку 📊 Отчет",
            reply_markup=admin_menu
        )
        await callback.answer()
        return

    try:
        await bot.send_document(
            callback.from_user.id,
            document=FSInputFile(excel_path),
            caption="📎 Excel-отчет"
        )
    except Exception:
        await callback.message.answer(
            "Не смогла отправить Excel-файл. Попробуй сформировать отчет заново.",
            reply_markup=admin_menu
        )

    await callback.answer()

@dp.callback_query(lambda callback: callback.data == "send_report_excel_to_boss")
async def send_report_excel_to_boss(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Отправлять Excel-отчет может только админ", show_alert=True)
        return

    excel_path = admin_last_report_excel.get(callback.from_user.id)

    if not excel_path:
        await callback.message.answer(
            "Сначала сформируй отчет через кнопку 📊 Отчет",
            reply_markup=admin_menu
        )
        await callback.answer()
        return

    try:
        await bot.send_document(
            BOSS_ID,
            document=FSInputFile(excel_path),
            caption="📎 Excel-отчет"
        )

        await callback.message.answer(
            "Excel-отчет отправлен шефу ✅",
            reply_markup=admin_menu
        )

    except Exception:
        await callback.message.answer(
            "Не смогла отправить Excel-отчет шефу. Проверь, что шеф открыл бота и нажал /start.",
            reply_markup=admin_menu
        )

    await callback.answer()

        
@dp.message(lambda message: message.text and message.text == "📋 Список сотрудников")
async def admin_employee_list(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("Этот раздел доступен только админу")
        return

    cursor.execute("""
        SELECT display_name, name, is_active
        FROM employees
        WHERE telegram_id != ?
        ORDER BY is_active DESC, COALESCE(display_name, name)
    """, (BOSS_ID,))

    employees = cursor.fetchall()

    if not employees:
        await message.answer("Сотрудников пока нет")
        return

    text = "<b>📋 Список сотрудников:</b>\n\n"

    for display_name, telegram_name, is_active in employees:
        status = "активен" if is_active == 1 else "скрыт/уволен"

        employee_name = display_name or telegram_name or "Без имени"

        text += (
            f"👤 <b>{employee_name}</b>\n"
            f"📌 Статус: {status}\n"
        )

        if telegram_name and telegram_name != employee_name:
            text += f"💬 Telegram: {telegram_name}\n"

        text += "\n"

    await message.answer(
        text,
        parse_mode="HTML",
        reply_markup=admin_employees_menu
    )

@dp.message(lambda message: message.text and message.text == "✏️ Переименовать")
async def admin_rename_employee_start(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("Этот раздел доступен только админу")
        return

    cursor.execute("""
        SELECT telegram_id, display_name, name
        FROM employees
        WHERE telegram_id != ?
        ORDER BY COALESCE(display_name, name)
    """, (BOSS_ID,))

    employees = cursor.fetchall()

    if not employees:
        await message.answer("Сотрудников пока нет")
        return

    keyboard = []

    for telegram_id, display_name, telegram_name in employees:
        employee_name = display_name or telegram_name or "Без имени"

        keyboard.append([
            InlineKeyboardButton(
                text=employee_name,
                callback_data=f"rename_employee:{telegram_id}"
            )
        ])

    await message.answer(
        "Кого переименовать?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
    )

@dp.callback_query(lambda callback: callback.data and callback.data.startswith("rename_employee:"))
async def admin_choose_employee_to_rename(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Этот раздел доступен только админу", show_alert=True)
        return

    employee_id = int(callback.data.split(":")[1])

    cursor.execute("""
        SELECT display_name, name
        FROM employees
        WHERE telegram_id = ?
    """, (employee_id,))

    employee = cursor.fetchone()

    if not employee:
        await callback.answer("Сотрудник не найден", show_alert=True)
        return

    display_name, telegram_name = employee
    employee_name = display_name or telegram_name or "Без имени"

    await state.update_data(
        rename_employee_id=employee_id,
        old_employee_name=employee_name
    )

    await state.set_state(RenameEmployeeForm.new_name)

    await callback.message.answer(
        f"Текущее имя: {employee_name}\n\n"
        f"Напиши новое рабочее имя сотрудника.\n"
        f"Если передумала — напиши /cancel"
    )

    await callback.answer()

@dp.message(RenameEmployeeForm.new_name)
async def admin_get_new_employee_name(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("Этот раздел доступен только админу")
        await state.clear()
        return

    if not message.text or len(message.text.strip()) < 2:
        await message.answer("Имя слишком короткое. Напиши нормальное рабочее имя.")
        return

    new_name = message.text.strip()
    data = await state.get_data()

    old_name = data["old_employee_name"]

    await state.update_data(new_employee_name=new_name)

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Да, переименовать",
                    callback_data="confirm_rename_employee"
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Нет, отменить",
                    callback_data="cancel_rename_employee"
                )
            ]
        ]
    )

    await message.answer(
        (
            f"Ты уверена, что хочешь переименовать сотрудника?\n\n"
            f"Было: {old_name}\n"
            f"Будет: {new_name}"
        ),
        reply_markup=keyboard
    )

@dp.callback_query(lambda callback: callback.data == "confirm_rename_employee")
async def admin_confirm_rename_employee(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Этот раздел доступен только админу", show_alert=True)
        return

    data = await state.get_data()

    employee_id = data["rename_employee_id"]
    old_name = data["old_employee_name"]
    new_name = data["new_employee_name"]

    cursor.execute(
        """
        UPDATE employees
        SET display_name = ?
        WHERE telegram_id = ?
        """,
        (new_name, employee_id)
    )

    conn.commit()

    await callback.message.answer(
        (
            f"Готово ✅\n\n"
            f"Сотрудник переименован:\n"
            f"{old_name} → {new_name}"
        ),
        reply_markup=admin_employees_menu
    )

    await state.clear()
    await callback.answer()

@dp.callback_query(lambda callback: callback.data == "cancel_rename_employee")
async def admin_cancel_rename_employee(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Этот раздел доступен только админу", show_alert=True)
        return

    await state.clear()

    await callback.message.answer(
        "Переименование отменено",
        reply_markup=admin_employees_menu
    )

    await callback.answer()

@dp.message(lambda message: message.text and message.text == "🚫 Уволить")
async def admin_fire_employee_start(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("Этот раздел доступен только админу")
        return

    cursor.execute("""
        SELECT telegram_id, display_name, name
        FROM employees
        WHERE is_active = 1
        ORDER BY display_name
    """)

    employees = cursor.fetchall()

    if not employees:
        await message.answer("Активных сотрудников пока нет")
        return

    keyboard = []

    for telegram_id, display_name, telegram_name in employees:
        employee_name = display_name or telegram_name or "Без имени"

        keyboard.append([
            InlineKeyboardButton(
                text=employee_name,
                callback_data=f"fire_employee:{telegram_id}"
            )
        ])

    await message.answer(
        "Кого скрыть/уволить?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
    )

@dp.callback_query(lambda callback: callback.data and callback.data.startswith("fire_employee:"))
async def admin_choose_employee_to_fire(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Этот раздел доступен только админу", show_alert=True)
        return

    employee_id = int(callback.data.split(":")[1])

    if employee_id == ADMIN_ID:
        await callback.answer("Админа нельзя уволить 😄", show_alert=True)
        return

    cursor.execute("""
        SELECT display_name, name
        FROM employees
        WHERE telegram_id = ? AND is_active = 1
    """, (employee_id,))

    employee = cursor.fetchone()

    if not employee:
        await callback.answer("Сотрудник не найден или уже неактивен", show_alert=True)
        return

    display_name, telegram_name = employee
    employee_name = display_name or telegram_name or "Без имени"

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Да, скрыть",
                    callback_data=f"confirm_fire_employee:{employee_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Нет, отменить",
                    callback_data="cancel_fire_employee"
                )
            ]
        ]
    )

    await callback.message.answer(
        (
            f"Ты уверена, что хочешь скрыть сотрудника?\n\n"
            f"👤 {employee_name}\n\n"
            f"Он больше не будет появляться в списке для новых задач."
        ),
        reply_markup=keyboard
    )

    await callback.answer()

@dp.callback_query(lambda callback: callback.data and callback.data.startswith("confirm_fire_employee:"))
async def admin_confirm_fire_employee(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Этот раздел доступен только админу", show_alert=True)
        return

    employee_id = int(callback.data.split(":")[1])

    cursor.execute("""
        SELECT display_name, name
        FROM employees
        WHERE telegram_id = ?
    """, (employee_id,))

    employee = cursor.fetchone()

    if not employee:
        await callback.answer("Сотрудник не найден", show_alert=True)
        return

    display_name, telegram_name = employee
    employee_name = display_name or telegram_name or "Без имени"

    cursor.execute(
        "UPDATE employees SET is_active = 0 WHERE telegram_id = ?",
        (employee_id,)
    )

    conn.commit()

    await callback.message.answer(
        f"Готово ✅\n\nСотрудник {employee_name} скрыт из списка активных.",
        reply_markup=admin_employees_menu
    )

    await callback.answer()

@dp.callback_query(lambda callback: callback.data == "cancel_fire_employee")
async def admin_cancel_fire_employee(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Этот раздел доступен только админу", show_alert=True)
        return

    await callback.message.answer(
        "Увольнение отменено",
        reply_markup=admin_employees_menu
    )

    await callback.answer()

@dp.message(lambda message: message.text and message.text == "✅ Восстановить")
async def admin_restore_employee_start(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("Этот раздел доступен только админу")
        return

    cursor.execute("""
        SELECT telegram_id, display_name, name
        FROM employees
        WHERE is_active = 0
        ORDER BY display_name
    """)

    employees = cursor.fetchall()

    if not employees:
        await message.answer("Скрытых/уволенных сотрудников пока нет")
        return

    keyboard = []

    for telegram_id, display_name, telegram_name in employees:
        employee_name = display_name or telegram_name or "Без имени"

        keyboard.append([
            InlineKeyboardButton(
                text=employee_name,
                callback_data=f"restore_employee:{telegram_id}"
            )
        ])

    await message.answer(
        "Кого восстановить?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
    )

@dp.callback_query(lambda callback: callback.data and callback.data.startswith("restore_employee:"))
async def admin_choose_employee_to_restore(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Этот раздел доступен только админу", show_alert=True)
        return

    employee_id = int(callback.data.split(":")[1])

    cursor.execute("""
        SELECT display_name, name
        FROM employees
        WHERE telegram_id = ? AND is_active = 0
    """, (employee_id,))

    employee = cursor.fetchone()

    if not employee:
        await callback.answer("Сотрудник не найден или уже активен", show_alert=True)
        return

    display_name, telegram_name = employee
    employee_name = display_name or telegram_name or "Без имени"

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Да, восстановить",
                    callback_data=f"confirm_restore_employee:{employee_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Нет, отменить",
                    callback_data="cancel_restore_employee"
                )
            ]
        ]
    )

    await callback.message.answer(
        (
            f"Ты уверена, что хочешь восстановить сотрудника?\n\n"
            f"👤 {employee_name}\n\n"
            f"Он снова появится в списке для новых задач."
        ),
        reply_markup=keyboard
    )

    await callback.answer()

@dp.callback_query(lambda callback: callback.data and callback.data.startswith("confirm_restore_employee:"))
async def admin_confirm_restore_employee(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Этот раздел доступен только админу", show_alert=True)
        return

    employee_id = int(callback.data.split(":")[1])

    cursor.execute("""
        SELECT display_name, name
        FROM employees
        WHERE telegram_id = ?
    """, (employee_id,))

    employee = cursor.fetchone()

    if not employee:
        await callback.answer("Сотрудник не найден", show_alert=True)
        return

    display_name, telegram_name = employee
    employee_name = display_name or telegram_name or "Без имени"

    cursor.execute(
        "UPDATE employees SET is_active = 1 WHERE telegram_id = ?",
        (employee_id,)
    )

    conn.commit()

    await callback.message.answer(
        f"Готово ✅\n\nСотрудник {employee_name} снова активен.",
        reply_markup=admin_employees_menu
    )

    await callback.answer()

@dp.callback_query(lambda callback: callback.data == "cancel_restore_employee")
async def admin_cancel_restore_employee(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Этот раздел доступен только админу", show_alert=True)
        return

    await callback.message.answer(
        "Восстановление отменено",
        reply_markup=admin_employees_menu
    )

    await callback.answer()
async def reminder():
    now = datetime.now()
    today = now.date()

    cursor.execute("""
        SELECT 
            id,
            task,
            deadline,
            employee_id,
            reminded,
            last_employee_overdue_reminder,
            last_boss_overdue_reminder
        FROM tasks
        WHERE status = 'В процессе'
    """)

    tasks = cursor.fetchall()

    for (
        task_id,
        task_text,
        deadline_text,
        employee_id,
        reminded,
        last_employee_overdue_reminder,
        last_boss_overdue_reminder
    ) in tasks:

        if reminded is None:
            reminded = ""

        try:
            deadline_date = datetime.strptime(deadline_text, "%d/%m/%Y").date()
        except:
            continue

        days_left = (deadline_date - today).days

        # Напоминания до дедлайна: за 7 / 2 / 1 / 0 дней
        needed = [7, 2, 1, 0]

        for d in needed:
            tag = f"{task_id}_{d}"

            if days_left == d and tag not in reminded:
                try:
                    await bot.send_message(
                        employee_id,
                        (
                            f"⏰ Напоминание\n\n"
                            f"📝 {task_text}\n"
                            f"📅 Дедлайн: {deadline_text}\n"
                            f"Осталось дней: {days_left}"
                        )
                    )
                except Exception:
                    if ADMIN_ID:
                        try:
                            await bot.send_message(
                                ADMIN_ID,
                                (
                                    f"⚠️ Не удалось отправить напоминание сотруднику\n\n"
                                    f"👤 Сотрудник: {get_employee_name(employee_id)}\n"
                                    f"📝 Задача: {task_text}"
                                )
                            )
                        except Exception:
                            pass

                reminded += tag + ","

                cursor.execute(
                    "UPDATE tasks SET reminded = ? WHERE id = ?",
                    (reminded, task_id)
                )

                conn.commit()

        # Просрочка
        if days_left < 0:
            now_text = now.strftime("%Y-%m-%d %H:%M")
            employee_name = get_employee_name(employee_id)

            # Сотруднику: 2 раза в день.
            # Так как reminder() запускается в 09:00 и 16:00,
            # отправляем при каждом запуске, но защищаемся от дубля в тот же час.
            should_send_employee = True

            if last_employee_overdue_reminder:
                try:
                    last_employee_dt = datetime.strptime(
                        last_employee_overdue_reminder,
                        "%Y-%m-%d %H:%M"
                    )

                    if (
                        last_employee_dt.date() == today
                        and last_employee_dt.hour == now.hour
                    ):
                        should_send_employee = False
                except:
                    pass

            if should_send_employee:
                try:
                    await bot.send_message(
                        employee_id,
                        (
                            f"<b>⚠️ Дедлайн просрочен</b>\n\n"
                            f"📝 {task_text}\n"
                            f"📅 Дедлайн был: <b>{deadline_text}</b>\n\n"
                            f"Пожалуйста, выполните задачу как можно скорее."
                        ),
                        parse_mode="HTML"
                    )
                except Exception:
                    if ADMIN_ID:
                        try:
                            await bot.send_message(
                                ADMIN_ID,
                                (
                                    f"⚠️ Не удалось отправить просроченное напоминание сотруднику\n\n"
                                    f"👤 Сотрудник: {employee_name}\n"
                                    f"📝 Задача: {task_text}\n"
                                    f"📅 Дедлайн был: {deadline_text}"
                                )
                            )
                        except Exception:
                            pass

                cursor.execute(
                    """
                    UPDATE tasks
                    SET last_employee_overdue_reminder = ?
                    WHERE id = ?
                    """,
                    (now_text, task_id)
                )

                conn.commit()

            # Шефу: каждые 3 дня после просрочки
            should_send_boss = False

            if not last_boss_overdue_reminder:
                should_send_boss = True
            else:
                try:
                    last_boss_dt = datetime.strptime(
                        last_boss_overdue_reminder,
                        "%Y-%m-%d %H:%M"
                    )

                    if (today - last_boss_dt.date()).days >= 3:
                        should_send_boss = True
                except:
                    should_send_boss = True

            if should_send_boss:
                try:
                    await bot.send_message(
                        BOSS_ID,
                        (
                            f"<b>⚠️ Сотрудник просрочил дедлайн</b>\n\n"
                            f"👤 Сотрудник: {employee_name}\n"
                            f"📝 Задача: {task_text}\n"
                            f"📅 Дедлайн был: <b>{deadline_text}</b>"
                        ),
                        parse_mode="HTML"
                    )
                except Exception:
                    if ADMIN_ID:
                        try:
                            await bot.send_message(
                                ADMIN_ID,
                                (
                                    f"⚠️ Не удалось отправить уведомление шефу о просрочке\n\n"
                                    f"👤 Сотрудник: {employee_name}\n"
                                    f"📝 Задача: {task_text}"
                                )
                            )
                        except Exception:
                            pass

                cursor.execute(
                    """
                    UPDATE tasks
                    SET last_boss_overdue_reminder = ?
                    WHERE id = ?
                    """,
                    (now_text, task_id)
                )

                conn.commit()

@dp.message(lambda message: message.text and message.text == "📋 Активные задачи")
async def btn_all_tasks(message: Message):
    if not is_boss(message.from_user.id):
        await message.answer("У тебя нет доступа к списку всех задач")
        return

    cursor.execute("""
        SELECT id, employee_id, task, deadline, status
        FROM tasks
        WHERE status = 'В процессе'
        ORDER BY id DESC
    """)
    tasks = cursor.fetchall()

    if not tasks:
        await message.answer("Активных задач пока нет")
        return

    text = "<b>📋 Активные задачи:</b>\n\n"

    for task_id, employee_id, task_text, deadline, status in tasks:
        employee_name = get_employee_name(employee_id)

        text += (
            f"👤 Сотрудник: {employee_name}\n"
            f"📝 {task_text}\n"
            f"📅 Дедлайн: {deadline}\n"
            f"📌 Статус: <b>{status}</b>\n\n"
        )

    await message.answer(text, parse_mode="HTML")

@dp.message(lambda message: message.text and message.text == "🗂 Архив")
async def archive_start(message: Message):
    if not is_boss(message.from_user.id):
        await message.answer("Архив доступен только шефу")
        return

    cursor.execute("""
        SELECT completed_at, cancelled_at
        FROM tasks
        WHERE status IN ('Выполнено', 'Отменено')
    """)

    rows = cursor.fetchall()

    years = set()

    for completed_at, cancelled_at in rows:
        date_text = completed_at or cancelled_at

        if not date_text:
            continue

        try:
            dt = datetime.strptime(date_text, "%d/%m/%Y %H:%M")
            years.add(dt.year)
        except:
            continue

    if not years:
        await message.answer("Архив пока пуст")
        return

    keyboard = []

    for year in sorted(years, reverse=True):
        keyboard.append([
            InlineKeyboardButton(
                text=str(year),
                callback_data=f"archive_year:{year}"
            )
        ])

    await message.answer(
        "Выбери год архива:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
    )

@dp.callback_query(lambda callback: callback.data and callback.data.startswith("archive_year:"))
async def archive_choose_month(callback: CallbackQuery):
    if not is_boss(callback.from_user.id):
        await callback.answer("Архив доступен только шефу", show_alert=True)
        return

    year = int(callback.data.split(":")[1])

    cursor.execute("""
        SELECT completed_at, cancelled_at
        FROM tasks
        WHERE status IN ('Выполнено', 'Отменено')
    """)

    rows = cursor.fetchall()

    months = set()

    for completed_at, cancelled_at in rows:
        date_text = completed_at or cancelled_at

        if not date_text:
            continue

        try:
            dt = datetime.strptime(date_text, "%d/%m/%Y %H:%M")

            if dt.year == year:
                months.add(dt.month)
        except:
            continue

    if not months:
        await callback.message.answer("За этот год архив пуст")
        await callback.answer()
        return

    month_names = {
        1: "Январь",
        2: "Февраль",
        3: "Март",
        4: "Апрель",
        5: "Май",
        6: "Июнь",
        7: "Июль",
        8: "Август",
        9: "Сентябрь",
        10: "Октябрь",
        11: "Ноябрь",
        12: "Декабрь"
    }

    keyboard = []

    for month in sorted(months):
        keyboard.append([
            InlineKeyboardButton(
                text=month_names[month],
                callback_data=f"archive_month:{year}:{month}"
            )
        ])

    await callback.message.answer(
        "Выбери месяц архива:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
    )

    await callback.answer()

@dp.callback_query(lambda callback: callback.data and callback.data.startswith("archive_month:"))
async def archive_choose_employee(callback: CallbackQuery):
    if not is_boss(callback.from_user.id):
        await callback.answer("Архив доступен только шефу", show_alert=True)
        return

    _, year_text, month_text = callback.data.split(":")
    year = int(year_text)
    month = int(month_text)

    cursor.execute("""
        SELECT employee_id, completed_at, cancelled_at
        FROM tasks
        WHERE status IN ('Выполнено', 'Отменено')
    """)

    rows = cursor.fetchall()

    employee_ids = set()

    for employee_id, completed_at, cancelled_at in rows:
        date_text = completed_at or cancelled_at

        if not date_text:
            continue

        try:
            dt = datetime.strptime(date_text, "%d/%m/%Y %H:%M")

            if dt.year == year and dt.month == month:
                employee_ids.add(employee_id)
        except:
            continue

    if not employee_ids:
        await callback.message.answer("За этот месяц архив пуст")
        await callback.answer()
        return

    keyboard = [
        [
            InlineKeyboardButton(
                text="Все сотрудники",
                callback_data=f"archive_show:{year}:{month}:all"
            )
        ]
    ]

    for employee_id in sorted(employee_ids, key=lambda user_id: get_employee_name(user_id)):
        employee_name = get_employee_name(employee_id)

        keyboard.append([
            InlineKeyboardButton(
                text=employee_name,
                callback_data=f"archive_show:{year}:{month}:{employee_id}"
            )
        ])

    await callback.message.answer(
        "Выбери сотрудника:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
    )

    await callback.answer()

@dp.callback_query(lambda callback: callback.data and callback.data.startswith("archive_show:"))
async def archive_show(callback: CallbackQuery):
    if not is_boss(callback.from_user.id):
        await callback.answer("Архив доступен только шефу", show_alert=True)
        return

    _, year_text, month_text, employee_text = callback.data.split(":")
    year = int(year_text)
    month = int(month_text)

    cursor.execute("""
        SELECT employee_id, task, deadline, status, completed_at, cancelled_at
        FROM tasks
        WHERE status IN ('Выполнено', 'Отменено')
        ORDER BY id DESC
    """)

    rows = cursor.fetchall()

    archive_tasks = []

    for employee_id, task_text, deadline, status, completed_at, cancelled_at in rows:
        date_text = completed_at or cancelled_at

        if not date_text:
            continue

        try:
            dt = datetime.strptime(date_text, "%d/%m/%Y %H:%M")
        except:
            continue

        if dt.year != year or dt.month != month:
            continue

        if employee_text != "all" and employee_id != int(employee_text):
            continue

        archive_tasks.append(
            (employee_id, task_text, deadline, status, date_text)
        )

    if not archive_tasks:
        await callback.message.answer("Архив по выбранным условиям пуст")
        await callback.answer()
        return

    month_names = {
        1: "Январь",
        2: "Февраль",
        3: "Март",
        4: "Апрель",
        5: "Май",
        6: "Июнь",
        7: "Июль",
        8: "Август",
        9: "Сентябрь",
        10: "Октябрь",
        11: "Ноябрь",
        12: "Декабрь"
    }

    if employee_text == "all":
        title_employee = "Все сотрудники"
    else:
        title_employee = get_employee_name(int(employee_text))

    text = (
        f"<b>🗂 Архив</b>\n\n"
        f"📅 {month_names[month]} {year}\n"
        f"👤 {title_employee}\n\n"
    )

    for employee_id, task_text, deadline, status, date_text in archive_tasks:
        employee_name = get_employee_name(employee_id)

        if status == "Выполнено":
            date_label = "Выполнено"
        else:
            date_label = "Отменено"

        text += (
            f"👤 <b>{employee_name}</b>\n"
            f"📝 {task_text}\n"
            f"📅 Дедлайн: {deadline}\n"
            f"📌 Статус: <b>{status}</b>\n"
            f"🕒 {date_label}: {date_text}\n\n"
        )

    await callback.message.answer(
        text,
        parse_mode="HTML",
        reply_markup=boss_menu
    )

    await callback.answer()



@dp.message(lambda message: message.text and message.text == "👤 Сотрудник")
async def ask_employee_tasks(message: Message):
    if not is_boss(message.from_user.id):
        await message.answer("Этот раздел доступен только шефу")
        return

    cursor.execute("""
        SELECT telegram_id, display_name, name
        FROM employees
        WHERE is_active = 1
        ORDER BY display_name
    """)

    employees = cursor.fetchall()

    if not employees:
        await message.answer("Активных сотрудников пока нет")
        return

    keyboard = []

    for telegram_id, display_name, telegram_name in employees:
        employee_name = display_name or telegram_name or "Без имени"

        keyboard.append([
            InlineKeyboardButton(
                text=employee_name,
                callback_data=f"show_employee_tasks:{telegram_id}"
            )
        ])

    await message.answer(
        "Выбери сотрудника:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
    )



@dp.callback_query(lambda callback: callback.data and callback.data.startswith("show_employee_tasks:"))
async def show_employee_tasks_by_button(callback: CallbackQuery):
    if not is_boss(callback.from_user.id):
        await callback.answer("Этот раздел доступен только шефу", show_alert=True)
        return

    employee_id = int(callback.data.split(":")[1])
    employee_name = get_employee_name(employee_id)

    cursor.execute("""
        SELECT id, task, deadline, status
        FROM tasks
        WHERE employee_id = ?
        ORDER BY id DESC
    """, (employee_id,))

    tasks = cursor.fetchall()

    if not tasks:
        await callback.message.answer(
            f"У сотрудника {employee_name} пока нет задач",
            reply_markup=boss_menu
        )
        await callback.answer()
        return

    text = f"<b>👤 Задачи сотрудника {employee_name}:</b>\n\n"

    for task_id, task_text, deadline, status in tasks:
        text += (
            f"📝 {task_text}\n"
            f"📅 Дедлайн: {deadline}\n"
            f"📌 Статус: <b>{status}</b>\n\n"
        )

    await callback.message.answer(
        text,
        reply_markup=boss_menu,
        parse_mode="HTML"
    )

    await callback.answer()

@dp.message(lambda message: message.text and message.text == "📌 Мои задачи")
async def btn_my_tasks(message: Message):
    user_id = message.from_user.id

    cursor.execute("""
        SELECT id, task, deadline, status
        FROM tasks
        WHERE employee_id = ? AND status = 'В процессе'
        ORDER BY id DESC
    """, (user_id,))

    tasks = cursor.fetchall()

    if not tasks:
        await message.answer("У тебя пока нет активных задач")
        return

    text = "<b>📌 Мои задачи:</b>\n\n"

    for task_id, task_text, deadline, status in tasks:
        text += (
            f"📝 {task_text}\n"
            f"📅 Дедлайн: {deadline}\n"
            f"📌 Статус: <b>{status}</b>\n\n"
        )

    await message.answer(text, parse_mode="HTML")

@dp.message(lambda message: message.text and message.text == "🗂 Мой архив")
async def my_archive(message: Message):
    user_id = message.from_user.id

    cursor.execute("""
        SELECT task, deadline, status, completed_at, cancelled_at
        FROM tasks
        WHERE employee_id = ? AND status IN ('Выполнено', 'Отменено')
        ORDER BY id DESC
    """, (user_id,))

    tasks = cursor.fetchall()

    if not tasks:
        await message.answer("Твой архив пока пуст")
        return

    text = "<b>🗂 Мой архив:</b>\n\n"

    for task_text, deadline, status, completed_at, cancelled_at in tasks:
        date_text = completed_at or cancelled_at or "Дата не указана"

        if status == "Выполнено":
            date_label = "Выполнено"
        else:
            date_label = "Отменено"

        text += (
            f"📝 {task_text}\n"
            f"📅 Дедлайн: {deadline}\n"
            f"📌 Статус: <b>{status}</b>\n"
            f"🕒 {date_label}: {date_text}\n\n"
        )

    if is_admin(user_id):
        reply_markup = admin_menu
    elif is_boss(user_id):
        reply_markup = boss_menu
    else:
        reply_markup = worker_menu

    await message.answer(
        text,
        parse_mode="HTML",
        reply_markup=reply_markup
    )

@dp.message(lambda message: message.text and message.text == "❌ Отменить задачу")
async def btn_cancel_task(message: Message):
    if not is_boss(message.from_user.id):
        await message.answer("Отменять задачи может только шеф")
        return

    cursor.execute("""
        SELECT id, employee_id, task, deadline
        FROM tasks
        WHERE status = 'В процессе'
        ORDER BY id DESC
    """)

    tasks = cursor.fetchall()

    if not tasks:
        await message.answer("Нет активных задач для отмены")
        return

    keyboard = []

    for task_id, employee_id, task_text, deadline in tasks:
        employee_name = get_employee_name(employee_id)
        button_text = f"{task_text[:30]} | {employee_name}"

        keyboard.append([
            InlineKeyboardButton(
                text=button_text,
                callback_data=f"cancel_task:{task_id}"
            )
        ])

    await message.answer(
        "Выбери задачу, которую нужно отменить:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
    )

@dp.callback_query(lambda callback: callback.data and callback.data.startswith("cancel_task:"))
async def choose_cancel_task(callback: CallbackQuery, state: FSMContext):
    if not is_boss(callback.from_user.id):
        await callback.answer("Отменять задачи может только шеф", show_alert=True)
        return

    task_id = int(callback.data.split(":")[1])

    cursor.execute("""
        SELECT id, employee_id, task, deadline, status
        FROM tasks
        WHERE id = ? AND status = 'В процессе'
    """, (task_id,))

    task = cursor.fetchone()

    if not task:
        await callback.answer("Задача не найдена или уже не активна", show_alert=True)
        return

    await state.update_data(cancel_task_id=task_id)
    await state.set_state(CancelTaskForm.reason)

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Без причины",
                    callback_data="cancel_without_reason"
                )
            ]
        ]
    )

    await callback.message.answer(
        "Напиши причину отмены или нажми кнопку:\n\n"
        "Если передумал(а), напиши /cancel",
        reply_markup=keyboard
    )

    await callback.answer()

@dp.callback_query(lambda callback: callback.data == "cancel_without_reason")
async def cancel_without_reason_button(callback: CallbackQuery, state: FSMContext):
    current_state = await state.get_state()

    if current_state != CancelTaskForm.reason.state:
        await callback.answer("Сейчас отмена задачи не ожидается", show_alert=True)
        return

    data = await state.get_data()
    task_id = data["cancel_task_id"]

    cursor.execute("""
        SELECT employee_id, task, deadline
        FROM tasks
        WHERE id = ? AND status = 'В процессе'
    """, (task_id,))

    task = cursor.fetchone()

    if not task:
        await callback.message.answer("Задача не найдена или уже не активна")
        await state.clear()
        await callback.answer()
        return

    employee_id, task_text, deadline = task
    employee_name = get_employee_name(employee_id)
    reason = "Без причины"
    cancelled_at = datetime.now().strftime("%d/%m/%Y %H:%M")

    cursor.execute(
        """
        UPDATE tasks
        SET status = ?, cancelled_at = ?
        WHERE id = ?
        """,
        ("Отменено", cancelled_at, task_id)
    )

    conn.commit()

    await callback.message.answer(
        (
            f"<b>❌ Задача отменена</b>\n\n"
            f"👤 Сотрудник: {employee_name}\n"
            f"📝 {task_text}\n"
            f"📅 Дедлайн был: <b>{deadline}</b>\n"
            f"💬 Причина: {reason}"
        ),
        parse_mode="HTML",
        reply_markup=boss_menu
    )

    try:
        await bot.send_message(
            employee_id,
            (
                f"<b>❌ Задача отменена</b>\n\n"
                f"📝 {task_text}\n"
                f"📅 Дедлайн был: <b>{deadline}</b>\n"
                f"💬 Причина: {reason}"
            ),
            parse_mode="HTML"
        )
    except Exception:
        await callback.message.answer(
            "Я отменил задачу, но не смог отправить уведомление сотруднику."
        )

        await notify_admin_delivery_failed(
            employee_id,
            "Отмена задачи",
            task_text
        )

    await state.clear()
    await callback.answer()

@dp.message(CancelTaskForm.reason)
async def cancel_task_reason(message: Message, state: FSMContext):
    if not is_boss(message.from_user.id):
        await message.answer("Отменять задачи может только шеф")
        await state.clear()
        return

    data = await state.get_data()
    task_id = data["cancel_task_id"]

    reason = message.text.strip() if message.text else "Без причины"

    if reason.lower() == "без причины":
        reason = "Без причины"

    cursor.execute("""
        SELECT employee_id, task, deadline
        FROM tasks
        WHERE id = ? AND status = 'В процессе'
    """, (task_id,))

    task = cursor.fetchone()

    if not task:
        await message.answer("Задача не найдена или уже не активна")
        await state.clear()
        return

    employee_id, task_text, deadline = task

    employee_name = get_employee_name(employee_id)

    cancelled_at = datetime.now().strftime("%d/%m/%Y %H:%M")

    cursor.execute(
        """
        UPDATE tasks
        SET status = ?, cancelled_at = ?
        WHERE id = ?
        """,
        ("Отменено", cancelled_at, task_id)
    )

    conn.commit()

    await message.answer(
        (
            f"<b>❌ Задача отменена</b>\n\n"
            f"👤 Сотрудник: {employee_name}\n"
            f"📝 {task_text}\n"
            f"📅 Дедлайн был: <b>{deadline}</b>\n"
            f"💬 Причина: {reason}"
        ),
        parse_mode="HTML",
        reply_markup=boss_menu
    )

    try:
        await bot.send_message(
            employee_id,
            (
                f"<b>❌ Задача отменена</b>\n\n"
                f"📝 {task_text}\n"
                f"📅 Дедлайн был: <b>{deadline}</b>\n"
                f"💬 Причина: {reason}"
            ),
            parse_mode="HTML"
        )
    except Exception:
        await message.answer(
            "Я отменил задачу, но не смог отправить уведомление сотруднику."
        )

        await notify_admin_delivery_failed(
            employee_id,
            "Отмена задачи",
            task_text
        )
    await state.clear()

@dp.message(lambda message: message.text and message.text == "📣 Напомнить")
async def manual_reminder_start(message: Message):
    if not is_boss(message.from_user.id):
        await message.answer("Напоминать сотрудникам может только шеф")
        return

    cursor.execute("""
        SELECT id, employee_id, task, deadline
        FROM tasks
        WHERE status = 'В процессе'
        ORDER BY id DESC
    """)

    tasks = cursor.fetchall()

    if not tasks:
        await message.answer("Активных задач пока нет")
        return

    keyboard = []

    for task_id, employee_id, task_text, deadline in tasks:
        employee_name = get_employee_name(employee_id)

        keyboard.append([
            InlineKeyboardButton(
                text=f"{task_text[:30]} | {employee_name} | {deadline}",
                callback_data=f"manual_reminder:{task_id}"
            )
        ])

    await message.answer(
        "Выбери задачу, по которой нужно напомнить сотруднику:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
    )

@dp.callback_query(lambda callback: callback.data and callback.data.startswith("manual_reminder:"))
async def choose_manual_reminder_task(callback: CallbackQuery, state: FSMContext):
    if not is_boss(callback.from_user.id):
        await callback.answer("Напоминать сотрудникам может только шеф", show_alert=True)
        return

    task_id = int(callback.data.split(":")[1])

    cursor.execute("""
        SELECT employee_id, task, deadline
        FROM tasks
        WHERE id = ? AND status = 'В процессе'
    """, (task_id,))

    task = cursor.fetchone()

    if not task:
        await callback.answer("Задача не найдена или уже не активна", show_alert=True)
        return

    employee_id, task_text, deadline = task
    employee_name = get_employee_name(employee_id)

    await state.update_data(
        reminder_task_id=task_id,
        reminder_employee_id=employee_id,
        reminder_employee_name=employee_name,
        reminder_task_text=task_text,
        reminder_deadline=deadline
    )

    await state.set_state(ManualReminderForm.comment)

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Без комментария",
                    callback_data="manual_reminder_no_comment"
                )
            ]
        ]
    )

    await callback.message.answer(
        (
            f"Напиши комментарий для сотрудника или нажми кнопку.\n\n"
            f"👤 Сотрудник: {employee_name}\n"
            f"📝 {task_text}\n"
            f"📅 Дедлайн: {deadline}\n\n"
            f"Например: Срочно, пожалуйста, до 17:00\n"
            f"Если передумал(а), напиши /cancel"
        ),
        reply_markup=keyboard
    )

    await callback.answer()

@dp.callback_query(lambda callback: callback.data == "manual_reminder_no_comment")
async def manual_reminder_no_comment(callback: CallbackQuery, state: FSMContext):
    current_state = await state.get_state()

    if current_state != ManualReminderForm.comment.state:
        await callback.answer("Сейчас комментарий не ожидается", show_alert=True)
        return

    await state.update_data(reminder_comment="Без комментария")

    data = await state.get_data()

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Да, отправить",
                    callback_data="confirm_manual_reminder"
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Нет, отменить",
                    callback_data="cancel_manual_reminder"
                )
            ]
        ]
    )

    await callback.message.answer(
        (
            f"Отправить напоминание сотруднику?\n\n"
            f"👤 Сотрудник: {data['reminder_employee_name']}\n"
            f"📝 {data['reminder_task_text']}\n"
            f"📅 Дедлайн: {data['reminder_deadline']}\n"
            f"💬 Комментарий: Без комментария"
        ),
        reply_markup=keyboard
    )

    await callback.answer()

@dp.message(ManualReminderForm.comment)
async def manual_reminder_get_comment(message: Message, state: FSMContext):
    if not is_boss(message.from_user.id):
        await message.answer("Напоминать сотрудникам может только шеф")
        await state.clear()
        return

    if not message.text or len(message.text.strip()) < 2:
        await message.answer("Комментарий слишком короткий. Напиши текст или нажми кнопку Без комментария.")
        return

    comment = message.text.strip()

    await state.update_data(reminder_comment=comment)

    data = await state.get_data()

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Да, отправить",
                    callback_data="confirm_manual_reminder"
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Нет, отменить",
                    callback_data="cancel_manual_reminder"
                )
            ]
        ]
    )

    await message.answer(
        (
            f"Отправить напоминание сотруднику?\n\n"
            f"👤 Сотрудник: {data['reminder_employee_name']}\n"
            f"📝 {data['reminder_task_text']}\n"
            f"📅 Дедлайн: {data['reminder_deadline']}\n"
            f"💬 Комментарий: {comment}"
        ),
        reply_markup=keyboard
    )

@dp.callback_query(lambda callback: callback.data == "confirm_manual_reminder")
async def confirm_manual_reminder(callback: CallbackQuery, state: FSMContext):
    if not is_boss(callback.from_user.id):
        await callback.answer("Напоминать сотрудникам может только шеф", show_alert=True)
        return

    data = await state.get_data()

    employee_id = data["reminder_employee_id"]
    employee_name = data["reminder_employee_name"]
    task_text = data["reminder_task_text"]
    deadline = data["reminder_deadline"]
    comment = data["reminder_comment"]

    try:
        await bot.send_message(
            employee_id,
            (
                f"<b>📣 Срочное напоминание от шефа</b>\n\n"
                f"📝 {task_text}\n"
                f"📅 Дедлайн: <b>{deadline}</b>\n"
                f"💬 Комментарий: {comment}"
            ),
            parse_mode="HTML"
        )

        await callback.message.answer(
            (
                f"Напоминание отправлено ✅\n\n"
                f"👤 Сотрудник: {employee_name}\n"
                f"📝 {task_text}"
            ),
            reply_markup=boss_menu
        )

    except Exception:
        await callback.message.answer(
            (
                f"Не смогла отправить напоминание сотруднику.\n\n"
                f"👤 Сотрудник: {employee_name}\n"
                f"📝 {task_text}"
            ),
            reply_markup=boss_menu
        )

        await notify_admin_delivery_failed(
            employee_id,
            "Ручное напоминание",
            task_text
        )

    await state.clear()
    await callback.answer()


@dp.callback_query(lambda callback: callback.data == "cancel_manual_reminder")
async def cancel_manual_reminder(callback: CallbackQuery, state: FSMContext):
    if not is_boss(callback.from_user.id):
        await callback.answer("Отправлять напоминания может только шеф", show_alert=True)
        return

    await state.clear()

    await callback.message.answer(
        "Напоминание отменено",
        reply_markup=boss_menu
    )

    await callback.answer()

@dp.message(lambda message: message.text and message.text == "🔁 Переназначить задачу")
async def btn_reassign_task(message: Message):
    if not is_boss(message.from_user.id):
        await message.answer("Переназначать задачи может только шеф")
        return

    cursor.execute("""
        SELECT id, employee_id, task, deadline
        FROM tasks
        WHERE status = 'В процессе'
        ORDER BY id DESC
    """)

    tasks = cursor.fetchall()

    if not tasks:
        await message.answer("Нет активных задач для переназначения")
        return

    keyboard = []

    for task_id, employee_id, task_text, deadline in tasks:
        employee_name = get_employee_name(employee_id)
        button_text = f"{task_text[:30]} | {employee_name}"

        keyboard.append([
            InlineKeyboardButton(
                text=button_text,
                callback_data=f"reassign_task:{task_id}"
            )
        ])

    await message.answer(
        "Выбери задачу, которую нужно переназначить:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
    )

@dp.message(lambda message: message.text and message.text == "📅 Дедлайн")
async def change_deadline_start(message: Message):
    if not is_boss(message.from_user.id):
        await message.answer("Менять дедлайн может только шеф")
        return

    cursor.execute("""
        SELECT id, employee_id, task, deadline
        FROM tasks
        WHERE status = 'В процессе'
        ORDER BY id DESC
    """)

    tasks = cursor.fetchall()

    if not tasks:
        await message.answer("Активных задач пока нет")
        return

    keyboard = []

    for task_id, employee_id, task_text, deadline in tasks:
        employee_name = get_employee_name(employee_id)

        keyboard.append([
            InlineKeyboardButton(
                text=f"{task_text[:30]} | {employee_name} | {deadline}",
                callback_data=f"change_deadline:{task_id}"
            )
        ])

    await message.answer(
        "Выбери задачу, у которой нужно изменить дедлайн:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
    )

@dp.callback_query(lambda callback: callback.data and callback.data.startswith("change_deadline:"))
async def choose_task_for_deadline(callback: CallbackQuery, state: FSMContext):
    if not is_boss(callback.from_user.id):
        await callback.answer("Менять дедлайн может только шеф", show_alert=True)
        return

    task_id = int(callback.data.split(":")[1])

    cursor.execute("""
        SELECT employee_id, task, deadline
        FROM tasks
        WHERE id = ? AND status = 'В процессе'
    """, (task_id,))

    task = cursor.fetchone()

    if not task:
        await callback.answer("Задача не найдена или уже не активна", show_alert=True)
        return

    employee_id, task_text, old_deadline = task
    employee_name = get_employee_name(employee_id)

    await state.update_data(
        deadline_task_id=task_id,
        deadline_employee_id=employee_id,
        deadline_employee_name=employee_name,
        deadline_task_text=task_text,
        old_deadline=old_deadline
    )

    await state.set_state(ChangeDeadlineForm.new_deadline)

    await callback.message.answer(
        (
            f"Напиши новый дедлайн.\n\n"
            f"👤 Сотрудник: {employee_name}\n"
            f"📝 {task_text}\n"
            f"📅 Сейчас: {old_deadline}\n\n"
            f"Можно написать: 30/06/2026 или завтра\n"
            f"Если передумала — напиши /cancel"
        )
    )

    await callback.answer()

@dp.message(ChangeDeadlineForm.new_deadline)
async def get_new_deadline(message: Message, state: FSMContext):
    if not is_boss(message.from_user.id):
        await message.answer("Менять дедлайн может только шеф")
        await state.clear()
        return

    deadline_date = dateparser.parse(
        message.text,
        languages=["ru"],
        settings={"PREFER_DATES_FROM": "future"}
    )

    if not deadline_date:
        await message.answer(
            "Не смогла понять дату. Напиши, например: 30/06/2026 или завтра"
        )
        return

    new_deadline = deadline_date.strftime("%d/%m/%Y")

    data = await state.get_data()

    await state.update_data(new_deadline=new_deadline)

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Да, изменить",
                    callback_data="confirm_change_deadline"
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Нет, отменить",
                    callback_data="cancel_change_deadline"
                )
            ]
        ]
    )

    await message.answer(
        (
            f"Ты уверена, что хочешь изменить дедлайн?\n\n"
            f"👤 Сотрудник: {data['deadline_employee_name']}\n"
            f"📝 {data['deadline_task_text']}\n"
            f"📅 Было: {data['old_deadline']}\n"
            f"📅 Будет: {new_deadline}"
        ),
        reply_markup=keyboard
    )

@dp.callback_query(lambda callback: callback.data == "confirm_change_deadline")
async def confirm_change_deadline(callback: CallbackQuery, state: FSMContext):
    if not is_boss(callback.from_user.id):
        await callback.answer("Менять дедлайн может только шеф", show_alert=True)
        return

    data = await state.get_data()

    task_id = data["deadline_task_id"]
    employee_id = data["deadline_employee_id"]
    employee_name = data["deadline_employee_name"]
    task_text = data["deadline_task_text"]
    old_deadline = data["old_deadline"]
    new_deadline = data["new_deadline"]

    cursor.execute(
        """
        UPDATE tasks
        SET deadline = ?,
            reminded = '',
            overdue_notified = 0,
            last_employee_overdue_reminder = NULL,
            last_boss_overdue_reminder = NULL
        WHERE id = ? AND status = 'В процессе'
        """,
        (new_deadline, task_id)
    )

    conn.commit()

    if cursor.rowcount == 0:
        await callback.message.answer("Задача не найдена или уже не активна")
        await state.clear()
        await callback.answer()
        return

    await callback.message.answer(
        (
            f"<b>📅 Дедлайн изменён</b>\n\n"
            f"👤 Сотрудник: {employee_name}\n"
            f"📝 {task_text}\n"
            f"📅 Было: {old_deadline}\n"
            f"📅 Стало: <b>{new_deadline}</b>"
        ),
        parse_mode="HTML",
        reply_markup=boss_menu
    )

    try:
        await bot.send_message(
            employee_id,
            (
                f"<b>📅 Дедлайн задачи изменён</b>\n\n"
                f"📝 {task_text}\n"
                f"📅 Было: {old_deadline}\n"
                f"📅 Новый дедлайн: <b>{new_deadline}</b>"
            ),
            parse_mode="HTML"
        )
    except Exception:
        await callback.message.answer(
            "Дедлайн изменён, но я не смог отправить уведомление сотруднику."
        )

        await notify_admin_delivery_failed(
            employee_id,
            "Изменение дедлайна",
            task_text
        )

    await state.clear()
    await callback.answer()

@dp.callback_query(lambda callback: callback.data == "cancel_change_deadline")
async def cancel_change_deadline(callback: CallbackQuery, state: FSMContext):
    if not is_boss(callback.from_user.id):
        await callback.answer("Менять дедлайн может только шеф", show_alert=True)
        return

    await state.clear()

    await callback.message.answer(
        "Изменение дедлайна отменено",
        reply_markup=boss_menu
    )

    await callback.answer()

@dp.callback_query(lambda callback: callback.data and callback.data.startswith("reassign_task:"))
async def choose_reassign_task(callback: CallbackQuery, state: FSMContext):
    if not is_boss(callback.from_user.id):
        await callback.answer("Переназначать задачи может только шеф", show_alert=True)
        return

    task_id = int(callback.data.split(":")[1])

    cursor.execute("""
        SELECT id, employee_id, task, deadline
        FROM tasks
        WHERE id = ? AND status = 'В процессе'
    """, (task_id,))

    task = cursor.fetchone()

    if not task:
        await callback.answer("Задача не найдена или уже не активна", show_alert=True)
        return

    _, old_employee_id, task_text, deadline = task

    cursor.execute("""
        SELECT telegram_id, display_name, name
        FROM employees
        WHERE is_active = 1 AND telegram_id != ?
        ORDER BY display_name
    """, (old_employee_id,))

    employees = cursor.fetchall()

    if not employees:
        await callback.message.answer("Нет других активных сотрудников для переназначения")
        await callback.answer()
        return

    keyboard = []

    for employee_id, display_name, telegram_name in employees:
        employee_name = display_name or telegram_name or "Без имени"

        keyboard.append([
            InlineKeyboardButton(
                text=employee_name,
                callback_data=f"reassign_to:{employee_id}"
            )
        ])

    await state.update_data(
        reassign_task_id=task_id,
        old_employee_id=old_employee_id,
        task_text=task_text,
        deadline=deadline
    )
    await state.set_state(ReassignTaskForm.employee)

    old_employee_name = get_employee_name(old_employee_id)

    await callback.message.answer(
        (
            f"<b>🔁 Переназначение задачи</b>\n\n"
            f"👤 Сейчас назначена: {old_employee_name}\n"
            f"📝 {task_text}\n"
            f"📅 Дедлайн: <b>{deadline}</b>\n\n"
            f"Выбери нового сотрудника:"
        ),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        parse_mode="HTML"
    )

    await callback.answer()

@dp.callback_query(lambda callback: callback.data and callback.data.startswith("reassign_to:"))
async def choose_reassign_employee(callback: CallbackQuery, state: FSMContext):
    if not is_boss(callback.from_user.id):
        await callback.answer("Переназначать задачи может только шеф", show_alert=True)
        return

    current_state = await state.get_state()

    if current_state != ReassignTaskForm.employee.state:
        await callback.answer("Сейчас переназначение не ожидается", show_alert=True)
        return

    new_employee_id = int(callback.data.split(":")[1])

    cursor.execute(
        """
        SELECT display_name, name
        FROM employees
        WHERE telegram_id = ? AND is_active = 1
        """,
        (new_employee_id,)
    )

    employee = cursor.fetchone()

    if not employee:
        await callback.answer("Сотрудник не найден или неактивен", show_alert=True)
        return

    display_name, telegram_name = employee
    new_employee_name = display_name or telegram_name or "Без имени"

    data = await state.get_data()

    task_id = data["reassign_task_id"]
    old_employee_id = data["old_employee_id"]
    task_text = data["task_text"]
    deadline = data["deadline"]
    old_employee_name = get_employee_name(old_employee_id)

    if new_employee_id == old_employee_id:
        await callback.answer("Задача уже назначена этому сотруднику", show_alert=True)
        return

    cursor.execute(
        """
        UPDATE tasks
        SET employee_id = ?
        WHERE id = ? AND status = 'В процессе'
        """,
        (new_employee_id, task_id)
    )

    conn.commit()

    if cursor.rowcount == 0:
        await callback.message.answer("Задача не найдена или уже не активна")
        await state.clear()
        await callback.answer()
        return

    await callback.message.answer(
        (
            f"<b>✅ Задача переназначена</b>\n\n"
            f"👤 Было: {old_employee_name}\n"
            f"👤 Теперь: {new_employee_name}\n"
            f"📝 {task_text}\n"
            f"📅 Дедлайн: <b>{deadline}</b>"
        ),
        parse_mode="HTML",
        reply_markup=boss_menu
    )

    try:
        await bot.send_message(
            old_employee_id,
            (
                f"<b>🔁 Задача переназначена</b>\n\n"
                f"📝 {task_text}\n"
                f"Эта задача больше не назначена вам."
            ),
            parse_mode="HTML"
        )
    except Exception:
        await notify_admin_delivery_failed(
            old_employee_id,
            "Задача снята с сотрудника при переназначении",
            task_text
        )

    try:
        await bot.send_message(
            new_employee_id,
            (
                f"<b>📌 Вам переназначена задача</b>\n\n"
                f"📝 {task_text}\n"
                f"📅 Дедлайн: <b>{deadline}</b>"
            ),
            parse_mode="HTML"
        )
    except Exception:
        await callback.message.answer(
            "Задача переназначена, но я не смог отправить уведомление новому сотруднику."
        )

        await notify_admin_delivery_failed(
            new_employee_id,
            "Переназначение задачи",
            task_text
        )

    await state.clear()
    await callback.answer()
    
async def main():
    scheduler.add_job(reminder, "cron", hour=9, minute=0)
    scheduler.add_job(reminder, "cron", hour=16, minute=0)
    scheduler.start()

    await asyncio.gather(
        start_web_server(),
        dp.start_polling(bot)
    )

@dp.message(lambda message: message.text and "Новая задача" in message.text)
async def btn_new(message: Message, state: FSMContext):
    if not is_boss(message.from_user.id):
        await message.answer("Создавать задачи может только шеф")
        return

    cursor.execute("""
        SELECT telegram_id, display_name, name
        FROM employees
        WHERE is_active = 1
        ORDER BY display_name
    """)

    employees = cursor.fetchall()

    if not employees:
        await message.answer(
            "Пока нет активных сотрудников.\n"
            "Попроси сотрудника открыть бота и нажать /start."
        )
        return

    keyboard = []

    for telegram_id, display_name, telegram_name in employees:
        employee_name = display_name or telegram_name or "Без имени"

        keyboard.append([
            InlineKeyboardButton(
                text=employee_name,
                callback_data=f"choose_employee:{telegram_id}"
            )
        ])

    await state.set_state(TaskForm.employee)

    await message.answer(
        "Кому назначить задачу?\n\n"
        "Если передумал(а), напиши /cancel",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
    )
@dp.callback_query(lambda callback: callback.data and callback.data.startswith("choose_employee:"))
async def choose_employee(callback: CallbackQuery, state: FSMContext):
    current_state = await state.get_state()

    if current_state != TaskForm.employee.state:
        await callback.answer("Сейчас выбор сотрудника не ожидается", show_alert=True)
        return

    employee_id = int(callback.data.split(":")[1])

    cursor.execute(
        """
        SELECT display_name, name
        FROM employees
        WHERE telegram_id = ? AND is_active = 1
        """,
        (employee_id,)
    )

    employee = cursor.fetchone()

    if not employee:
        await callback.answer("Сотрудник не найден или уже неактивен", show_alert=True)
        return

    display_name, telegram_name = employee
    employee_name = display_name or telegram_name or "Без имени"

    await state.update_data(employee_id=employee_id, employee_name=employee_name)
    await state.set_state(TaskForm.task)

    await callback.message.answer(
        f"Выбран сотрудник: {employee_name}\n\n"
        f"Опиши задачу"
    )

    await callback.answer()
@dp.message(TaskForm.task)
async def get_task(message: Message, state: FSMContext):
    await state.update_data(task=message.text)
    await state.set_state(TaskForm.deadline)

    await message.answer("Когда дедлайн? (30/05/2026 или завтра)")
@dp.message(TaskForm.deadline)
async def get_deadline(message: Message, state: FSMContext):
    data = await state.get_data()

    employee_id = data["employee_id"]
    employee_name = data.get("employee_name", str(employee_id))
    task_text = data["task"]

    deadline_date = dateparser.parse(
        message.text,
        languages=["ru"],
        settings={"PREFER_DATES_FROM": "future"}
    )

    if deadline_date:
        deadline = deadline_date.strftime("%d/%m/%Y")
    else:
        deadline = "Без дедлайна"

    cursor.execute(
        "INSERT INTO tasks (employee_id, task, deadline, status) VALUES (?, ?, ?, ?)",
        (employee_id, task_text, deadline, "В процессе")
    )

    conn.commit()

    try:
        await bot.send_message(
            employee_id,
            (
                f"<b>📌 Новая задача</b>\n\n"
                f"📝 {task_text}\n"
                f"📅 Дедлайн: <b>{deadline}</b>"
            ),
            parse_mode="HTML"
        )

        await message.answer(
            f"Задача создана 😄\n"
            f"👤 Сотрудник: {employee_name}"
        )

    except Exception:
        await message.answer(
            "Задача создана, но я не смог отправить уведомление сотруднику.\n"
            "Попроси сотрудника открыть бота и нажать /start."
        )

        await notify_admin_delivery_failed(
            employee_id,
            "Новая задача",
            task_text
        )

    await state.clear()


@dp.message(lambda message: message.text and "Выполнено" in message.text)
async def btn_done(message: Message):
    user_id = message.from_user.id

    cursor.execute("""
        SELECT id, task, deadline
        FROM tasks
        WHERE employee_id = ? AND status = 'В процессе'
        ORDER BY id DESC
    """, (user_id,))

    tasks = cursor.fetchall()

    if not tasks:
        await message.answer("У тебя нет активных задач 😄")
        return

    keyboard = []

    for task_id, task_text, deadline in tasks:
        button_text = f"{task_text[:30]}"

        if deadline:
            button_text += f" | {deadline}"

        keyboard.append([
            InlineKeyboardButton(
                text=button_text,
                callback_data=f"done_task:{task_id}"
            )
        ])

    await message.answer(
        "Выбери задачу, которую хочешь отметить выполненной:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
    )


@dp.callback_query(lambda callback: callback.data and callback.data.startswith("done_task:"))
async def choose_done_task(callback: CallbackQuery, state: FSMContext):
    task_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id

    cursor.execute("""
        SELECT id, task, deadline, status, employee_id
        FROM tasks
        WHERE id = ? AND employee_id = ? AND status = 'В процессе'
    """, (task_id, user_id))

    task = cursor.fetchone()

    if not task:
        await callback.answer("Задача не найдена или уже не активна", show_alert=True)
        return

    await state.update_data(task_id=task_id)
    await state.set_state(DoneForm.proof)

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Без файла",
                    callback_data="done_without_file"
                )
            ]
        ]
    )

    await callback.message.answer(
        "Если нужно приложить документ — отправь его сюда файлом.\n"
        "Если документа нет — нажми кнопку:",
        reply_markup=keyboard
    )

    await callback.answer()

@dp.callback_query(lambda callback: callback.data == "done_without_file")
async def done_without_file_button(callback: CallbackQuery, state: FSMContext):
    current_state = await state.get_state()

    if current_state != DoneForm.proof.state:
        await callback.answer("Сейчас выполнение задачи не ожидается", show_alert=True)
        return

    data = await state.get_data()
    task_id = data["task_id"]
    user_id = callback.from_user.id

    cursor.execute("""
        SELECT task, deadline, employee_id
        FROM tasks
        WHERE id = ? AND employee_id = ? AND status = 'В процессе'
    """, (task_id, user_id))

    task = cursor.fetchone()

    if not task:
        await callback.message.answer("Задача не найдена или уже не активна")
        await state.clear()
        await callback.answer()
        return

    task_text, deadline, employee_id = task
    employee_name = get_employee_name(employee_id)
    completed_at = datetime.now().strftime("%d/%m/%Y %H:%M")

    cursor.execute(
        """
        UPDATE tasks
        SET status = ?, completed_at = ?
        WHERE id = ? AND employee_id = ?
        """,
        ("Выполнено", completed_at, task_id, user_id)
    )

    conn.commit()

    await callback.message.answer("Задача отмечена как выполненная ✅")

    await bot.send_message(
        BOSS_ID,
        (
            f"<b>✅ Задача выполнена</b>\n\n"
            f"👤 Сотрудник: {employee_name}\n"
            f"📝 {task_text}\n"
            f"📅 Дедлайн: <b>{deadline}</b>\n"
            f"📌 Без прикреплённого файла"
        ),
        parse_mode="HTML"
    )

    await state.clear()
    await callback.answer()



@dp.message(DoneForm.proof, lambda message: message.text and message.text.lower() == "без файла")
async def done_without_file(message: Message, state: FSMContext):
    data = await state.get_data()
    task_id = data["task_id"]
    user_id = message.from_user.id

    cursor.execute("""
        SELECT task, deadline, employee_id
        FROM tasks
        WHERE id = ? AND employee_id = ?
    """, (task_id, user_id))

    task = cursor.fetchone()

    if not task:
        await message.answer("Задача не найдена")
        await state.clear()
        return

    task_text, deadline, employee_id = task
    employee_name = get_employee_name(employee_id)
    completed_at = datetime.now().strftime("%d/%m/%Y %H:%M")

    cursor.execute(
        """
        UPDATE tasks
        SET status = ?, completed_at = ?
        WHERE id = ? AND employee_id = ?
        """,
        ("Выполнено", completed_at, task_id, user_id)
    )

    conn.commit()

    await message.answer("Задача отмечена как выполненная ✅")

    await bot.send_message(
        BOSS_ID,
        (
            f"<b>✅ Задача выполнена</b>\n\n"
            f"👤 Сотрудник: {employee_name}\n"
            f"📝 {task_text}\n"
            f"📅 Дедлайн: <b>{deadline}</b>\n"
            f"📌 Без прикреплённого файла"
        ),
        parse_mode="HTML"
    )
    await state.clear()


@dp.message(DoneForm.proof, lambda message: message.document is not None or message.photo is not None)
async def done_with_file(message: Message, state: FSMContext):
    data = await state.get_data()
    task_id = data["task_id"]
    user_id = message.from_user.id

    if message.document:
        document = message.document
        file_id = document.file_id
        file_name = document.file_name or "Документ"
        file_type = "document"
    else:
        photo = message.photo[-1]
        file_id = photo.file_id
        file_name = "Фото"
        file_type = "photo"

    cursor.execute("""
        SELECT task, deadline, employee_id
        FROM tasks
        WHERE id = ? AND employee_id = ?
    """, (task_id, user_id))

    task = cursor.fetchone()

    if not task:
        await message.answer("Задача не найдена")
        await state.clear()
        return

    task_text, deadline, employee_id = task
    employee_name = get_employee_name(employee_id)
    completed_at = datetime.now().strftime("%d/%m/%Y %H:%M")

    cursor.execute(
        """
        UPDATE tasks
        SET status = ?, proof_file_id = ?, proof_file_name = ?, completed_at = ?
        WHERE id = ? AND employee_id = ?
        """,
        ("Выполнено", file_id, file_name, completed_at, task_id, user_id)
    )

    conn.commit()

    await message.answer("Задача выполнена, файл прикреплён ✅")

    await bot.send_message(
        BOSS_ID,
        (
            f"<b>✅ Задача выполнена с файлом</b>\n\n"
            f"👤 Сотрудник: {employee_name}\n"
            f"📝 {task_text}\n"
            f"📅 Дедлайн: <b>{deadline}</b>\n"
            f"📎 Файл: {file_name}"
        ),
        parse_mode="HTML"
    )

    if file_type == "document":
        await bot.send_document(
            BOSS_ID,
            document=file_id,
            caption=f"📎 Файл по задаче от сотрудника {employee_name}"
        )
    else:
        await bot.send_photo(
            BOSS_ID,
            photo=file_id,
            caption=f"📎 Фото по задаче от сотрудника {employee_name}"
        )

    await state.clear()


@dp.message(DoneForm.proof)
async def wrong_proof(message: Message):
    await message.answer(
        "Отправь документ или фото, либо нажми кнопку: Без файла"
    )

@dp.message(StateFilter(None))
async def unknown_message(message: Message):
    user_id = message.from_user.id

    if is_admin(user_id):
        await message.answer(
            "Я не понял сообщение 😅 Выбери действие в меню.",
            reply_markup=admin_menu
        )
    elif is_boss(user_id):
        await message.answer(
            "Я не понял сообщение 😅 Выбери действие в меню.",
            reply_markup=boss_menu
        )
    else:
        await message.answer(
            "Я не понял сообщение 😅 Выбери действие в меню.",
            reply_markup=worker_menu
        )

from aiohttp import web
import os

async def handle(request):
    return web.Response(text="Bot is running")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle)

    port = int(os.getenv("PORT", 10000))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

if __name__ == "__main__":
    asyncio.run(main())
