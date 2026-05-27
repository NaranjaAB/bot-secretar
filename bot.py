from aiogram import Bot, Dispatcher
from aiogram.types import Message, Document, CallbackQuery
from aiogram.filters import CommandStart, Command, StateFilter
import asyncio
import sqlite3
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime
import dateparser
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext

import os
TOKEN = os.getenv("TOKEN")
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
class EmployeeTasksForm(StatesGroup):
    employee_id = State()

class CancelTaskForm(StatesGroup):
    reason = State()

class ReassignTaskForm(StatesGroup):
    employee = State()
    
boss_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="➕ Новая задача")],
        [KeyboardButton(text="📋 Все задачи"), KeyboardButton(text="👤 Задачи сотрудника")],
        [KeyboardButton(text="📌 Мои задачи"), KeyboardButton(text="✔ Выполнено")],
        [KeyboardButton(text="❌ Отменить задачу"), KeyboardButton(text="🔁 Переназначить задачу")]
    ],
    resize_keyboard=True
)

worker_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📌 Мои задачи")],
        [KeyboardButton(text="✔ Выполнено")]
    ],
    resize_keyboard=True
)


def is_boss(user_id: int) -> bool:
    return user_id == BOSS_ID

def save_employee(user_id: int, name: str):
    cursor.execute(
        """
        INSERT INTO employees (telegram_id, name, is_active)
        VALUES (?, ?, 1)
        ON CONFLICT(telegram_id)
        DO UPDATE SET
            name = excluded.name
        """,
        (user_id, name)
    )

    conn.commit()

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

@dp.message(CommandStart())
async def start(message: Message):
    user_id = message.from_user.id
    full_name = message.from_user.full_name

    save_employee(user_id, full_name)

    if is_boss(user_id):
        await message.answer(
            "Привет 😄 Ты вошёл как шеф",
            reply_markup=boss_menu
        )
    else:
        await message.answer(
            "Привет 😄 Ты вошёл как сотрудник",
            reply_markup=worker_menu
        )

@dp.message(Command("task"))
async def create_task(message: Message):
    if not is_boss(message.from_user.id):
        await message.answer("Создавать задачи может только шеф")
        return

    raw = message.text.replace("/task", "").strip()

    parts = raw.split(".")

    if len(parts) < 3:
        await message.answer("Формат: /task ID. задача. ДД/ММ/ГГГГ")
        return

    employee_id_text = parts[0].strip()

    if not employee_id_text.isdigit():
        await message.answer("ID сотрудника должен быть числом")
        return

    employee_id = int(employee_id_text)
    task_text = parts[1].strip()
    date_text = parts[2].strip()

    # 💡 парсим ДД/ММ/ГГГГ
    try:
        day, month, year = date_text.split("/")

        deadline = f"{day.zfill(2)}/{month.zfill(2)}/{year}"
    except:
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
            f"Задача создана 😄\n\n{task_text}\nДедлайн: {deadline}"
        )

    except Exception:
        await message.answer(
            f"Задача создана, но я не смог отправить уведомление сотруднику.\n\n"
            f"Попроси сотрудника открыть бота и нажать /start."
        )
@dp.message(Command("tasks"))
async def show_tasks(message: Message):
    if not is_boss(message.from_user.id):
        await message.answer("Эта команда доступна только шефу")
        return

    cursor.execute("""
        SELECT id, employee_id, task, deadline, status
        FROM tasks
        ORDER BY id DESC
    """)
    tasks = cursor.fetchall()

    if not tasks:
        await message.answer("Пока задач нет")
        return

    text = "<b>📋 Все задачи:</b>\n\n"

    for task_id, employee_id, task_text, deadline, status in tasks:
        text += (
            f"<b>#{task_id}</b>\n"
            f"👤 Сотрудник: {employee_id}\n"
            f"📝 {task_text}\n"
            f"📅 Дедлайн: {deadline}\n"
            f"📌 Статус: <b>{status}</b>\n\n"
        )

    await message.answer(text, parse_mode="HTML")


@dp.message(Command("myid"))
async def myid(message: Message):
    await message.answer(str(message.from_user.id))

@dp.message(Command("fire"))
async def fire_employee(message: Message):
    if not is_boss(message.from_user.id):
        await message.answer("Эта команда доступна только шефу")
        return

    parts = message.text.split(maxsplit=1)

    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Формат: /fire Telegram_ID")
        return

    employee_id = int(parts[1])

    if employee_id == BOSS_ID:
        await message.answer("Шефа нельзя уволить 😄")
        return

    cursor.execute(
        "UPDATE employees SET is_active = 0 WHERE telegram_id = ?",
        (employee_id,)
    )

    conn.commit()

    if cursor.rowcount == 0:
        await message.answer("Я не нашёл такого сотрудника в базе")
        return

    await message.answer(f"Сотрудник {employee_id} скрыт из списка активных")

@dp.message(Command("restore"))
async def restore_employee(message: Message):
    if not is_boss(message.from_user.id):
        await message.answer("Эта команда доступна только шефу")
        return

    parts = message.text.split(maxsplit=1)

    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Формат: /restore Telegram_ID")
        return

    employee_id = int(parts[1])

    cursor.execute(
        "UPDATE employees SET is_active = 1 WHERE telegram_id = ?",
        (employee_id,)
    )

    conn.commit()

    if cursor.rowcount == 0:
        await message.answer("Я не нашёл такого сотрудника в базе")
        return

    await message.answer(f"Сотрудник {employee_id} снова активен")

@dp.message(Command("cancel"), StateFilter("*"))
async def cancel(message: Message, state: FSMContext):
    current_state = await state.get_state()

    if current_state is None:
        await message.answer("Сейчас нечего отменять 😄")
        return

    await state.clear()

    if is_boss(message.from_user.id):
        await message.answer(
            "Действие отменено",
            reply_markup=boss_menu
        )
    else:
        await message.answer(
            "Действие отменено",
            reply_markup=worker_menu
        )
async def reminder():
    today = datetime.now().date()

    cursor.execute("""
        SELECT id, task, deadline, employee_id, reminded, overdue_notified
        FROM tasks
        WHERE status = 'В процессе'
    """)

    tasks = cursor.fetchall()

    for task_id, task_text, deadline_text, employee_id, reminded, overdue_notified in tasks:

        try:
            deadline_date = datetime.strptime(deadline_text, "%d/%m/%Y").date()
        except:
            continue

        days_left = (deadline_date - today).days
        if days_left < 0 and overdue_notified == 0:
            await bot.send_message(
                BOSS_ID,
                (
                    f"⚠️ Дедлайн просрочен\n\n"
                    f"👤 Сотрудник: {employee_id}\n"
                    f"📝 Задача: {task_text}\n"
                    f"📅 Дедлайн был: {deadline_text}"
                )
            )

            cursor.execute(
                "UPDATE tasks SET overdue_notified = 1 WHERE id = ?",
                (task_id,)
            )

            conn.commit()

        # какие напоминания нужны
        needed = [7, 2, 1, 0]

        for d in needed:

            tag = f"{task_id}_{d}"

            if days_left == d and tag not in reminded:

                await bot.send_message(
                    employee_id,
                    f"⏰ Напоминание\n{task_text}\nДедлайн: {deadline_text}\nОсталось: {days_left} дней"
                )

                # записываем что уже отправили
                reminded += tag + ","

                cursor.execute(
                    "UPDATE tasks SET reminded = ? WHERE id = ?",
                    (reminded, task_id)
                )

                conn.commit()

@dp.message(lambda message: message.text and message.text == "📋 Все задачи")
async def btn_all_tasks(message: Message):
    if not is_boss(message.from_user.id):
        await message.answer("У тебя нет доступа к списку всех задач")
        return

    cursor.execute("""
        SELECT id, employee_id, task, deadline, status
        FROM tasks
        ORDER BY id DESC
    """)
    tasks = cursor.fetchall()

    if not tasks:
        await message.answer("Пока задач нет")
        return

    text = "<b>📋 Все задачи:</b>\n\n"

    for task_id, employee_id, task_text, deadline, status in tasks:
        text += (
            f"<b>#{task_id}</b>\n"
            f"👤 Сотрудник: {employee_id}\n"
            f"📝 {task_text}\n"
            f"📅 Дедлайн: {deadline}\n"
            f"📌 Статус: <b>{status}</b>\n\n"
        )

    await message.answer(text, parse_mode="HTML")

@dp.message(lambda message: message.text and message.text == "👤 Задачи сотрудника")
async def ask_employee_tasks(message: Message, state: FSMContext):
    if not is_boss(message.from_user.id):
        await message.answer("Этот раздел доступен только шефу")
        return

    await state.set_state(EmployeeTasksForm.employee_id)
    await message.answer(
        "Впиши Telegram ID сотрудника, чьи задачи хочешь посмотреть.\n\n"
        "Если передумал(а), напиши /cancel"
    )

@dp.message(EmployeeTasksForm.employee_id)
async def show_employee_tasks(message: Message, state: FSMContext):
    if not is_boss(message.from_user.id):
        await message.answer("Этот раздел доступен только шефу")
        await state.clear()
        return

    if not message.text or not message.text.isdigit():
        await message.answer(
            "Впиши только Telegram ID цифрами.\n"
            "Например: 123456789\n\n"
            "Если хочешь отменить создание задачи — напиши /cancel"
        )
        return

    employee_id = int(message.text)

    cursor.execute("""
        SELECT id, task, deadline, status
        FROM tasks
        WHERE employee_id = ?
        ORDER BY id DESC
    """, (employee_id,))

    tasks = cursor.fetchall()

    if not tasks:
        await message.answer(
            f"У сотрудника {employee_id} пока нет задач",
            reply_markup=boss_menu
        )
        await state.clear()
        return

    text = f"<b>👤 Задачи сотрудника {employee_id}:</b>\n\n"

    for task_id, task_text, deadline, status in tasks:
        text += (
            f"<b>#{task_id}</b>\n"
            f"📝 {task_text}\n"
            f"📅 Дедлайн: {deadline}\n"
            f"📌 Статус: <b>{status}</b>\n\n"
        )

    await message.answer(text, reply_markup=boss_menu, parse_mode="HTML")
    await state.clear()

@dp.message(lambda message: message.text and message.text == "📌 Мои задачи")
async def btn_my_tasks(message: Message):
    user_id = message.from_user.id

    cursor.execute("""
        SELECT id, task, deadline, status
        FROM tasks
        WHERE employee_id = ?
        ORDER BY id DESC
    """, (user_id,))

    tasks = cursor.fetchall()

    if not tasks:
        await message.answer("У тебя пока нет задач")
        return

    text = "<b>📌 Мои задачи:</b>\n\n"

    for task_id, task_text, deadline, status in tasks:
        text += (
            f"<b>#{task_id}</b>\n"
            f"📝 {task_text}\n"
            f"📅 Дедлайн: {deadline}\n"
            f"📌 Статус: <b>{status}</b>\n\n"
        )

    await message.answer(text, parse_mode="HTML")

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
        button_text = f"#{task_id} — {task_text[:30]} | {employee_id}"

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

    await callback.message.answer(
        "Напиши причину отмены.\n\n"
        "Если причины нет — напиши: без причины\n"
        "Если передумал(а), напиши /cancel"
    )

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

    cursor.execute(
        """
        UPDATE tasks
        SET status = ?
        WHERE id = ?
        """,
        ("Отменено", task_id)
    )

    conn.commit()

    await message.answer(
        (
            f"<b>❌ Задача отменена</b>\n\n"
            f"<b>#{task_id}</b>\n"
            f"👤 Сотрудник: {employee_id}\n"
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
                f"<b>#{task_id}</b>\n"
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

    await state.clear()

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
        button_text = f"#{task_id} — {task_text[:30]} | {employee_id}"

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
        SELECT telegram_id, name
        FROM employees
        WHERE is_active = 1 AND telegram_id != ?
        ORDER BY name
    """, (old_employee_id,))

    employees = cursor.fetchall()

    if not employees:
        await callback.message.answer("Нет других активных сотрудников для переназначения")
        await callback.answer()
        return

    keyboard = []

    for employee_id, name in employees:
        keyboard.append([
            InlineKeyboardButton(
                text=name,
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

    await callback.message.answer(
        (
            f"<b>🔁 Переназначение задачи</b>\n\n"
            f"<b>#{task_id}</b>\n"
            f"👤 Сейчас назначена: {old_employee_id}\n"
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
        "SELECT name FROM employees WHERE telegram_id = ? AND is_active = 1",
        (new_employee_id,)
    )

    employee = cursor.fetchone()

    if not employee:
        await callback.answer("Сотрудник не найден или неактивен", show_alert=True)
        return

    new_employee_name = employee[0]

    data = await state.get_data()

    task_id = data["reassign_task_id"]
    old_employee_id = data["old_employee_id"]
    task_text = data["task_text"]
    deadline = data["deadline"]

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
            f"<b>#{task_id}</b>\n"
            f"👤 Было: {old_employee_id}\n"
            f"👤 Теперь: {new_employee_name} ({new_employee_id})\n"
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
                f"<b>#{task_id}</b>\n"
                f"📝 {task_text}\n"
                f"Эта задача больше не назначена вам."
            ),
            parse_mode="HTML"
        )
    except Exception:
        pass

    try:
        await bot.send_message(
            new_employee_id,
            (
                f"<b>📌 Вам переназначена задача</b>\n\n"
                f"<b>#{task_id}</b>\n"
                f"📝 {task_text}\n"
                f"📅 Дедлайн: <b>{deadline}</b>"
            ),
            parse_mode="HTML"
        )
    except Exception:
        await callback.message.answer(
            "Задача переназначена, но я не смог отправить уведомление новому сотруднику."
        )

    await state.clear()
    await callback.answer()
    
async def main():
    scheduler.add_job(reminder, "cron", hour=9, minute=0)
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
        SELECT telegram_id, name
        FROM employees
        WHERE is_active = 1
        ORDER BY name
    """)

    employees = cursor.fetchall()

    if not employees:
        await message.answer(
            "Пока нет активных сотрудников.\n"
            "Попроси сотрудника открыть бота и нажать /start."
        )
        return

    keyboard = []

    for telegram_id, name in employees:
        keyboard.append([
            InlineKeyboardButton(
                text=name,
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
        "SELECT name FROM employees WHERE telegram_id = ? AND is_active = 1",
        (employee_id,)
    )

    employee = cursor.fetchone()

    if not employee:
        await callback.answer("Сотрудник не найден или уже неактивен", show_alert=True)
        return

    employee_name = employee[0]

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
        button_text = f"#{task_id} — {task_text[:30]}"

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

    await callback.message.answer(
        "Если нужно приложить документ — отправь его сюда файлом.\n"
        "Если документа нет — напиши: без файла"
    )

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

    await message.answer(f"Задача №{task_id} отмечена как выполненная ✅")

    await bot.send_message(
        BOSS_ID,
        (
            f"<b>✅ Задача выполнена</b>\n\n"
            f"<b>#{task_id}</b>\n"
            f"👤 Сотрудник: {employee_id}\n"
            f"📝 {task_text}\n"
            f"📅 Дедлайн: <b>{deadline}</b>\n"
            f"📌 Без прикреплённого файла"
        ),
        parse_mode="HTML"
    )
    await state.clear()


@dp.message(DoneForm.proof, lambda message: message.document is not None)
async def done_with_file(message: Message, state: FSMContext):
    data = await state.get_data()
    task_id = data["task_id"]
    user_id = message.from_user.id

    document = message.document
    file_id = document.file_id
    file_name = document.file_name

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

    await message.answer(f"Задача №{task_id} выполнена, файл прикреплён ✅")

    await bot.send_message(
        BOSS_ID,
        (
            f"<b>✅ Задача выполнена с файлом</b>\n\n"
            f"<b>#{task_id}</b>\n"
            f"👤 Сотрудник: {employee_id}\n"
            f"📝 {task_text}\n"
            f"📅 Дедлайн: <b>{deadline}</b>\n"
            f"📎 Файл: {file_name}"
        ),
        parse_mode="HTML"
    )

    await bot.send_document(
        BOSS_ID,
        document=file_id,
        caption=f"📎 Файл по задаче #{task_id} от сотрудника {employee_id}"
    )

    await state.clear()


@dp.message(DoneForm.proof)
async def wrong_proof(message: Message):
    await message.answer(
        "Отправь документ файлом или напиши: без файла"
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
