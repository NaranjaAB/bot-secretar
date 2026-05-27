from aiogram import Bot, Dispatcher
from aiogram.types import Message, Document
from aiogram.filters import CommandStart, Command
import asyncio
import sqlite3
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime
import dateparser
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext

import os
TOKEN = os.getenv("TOKEN")

bot = Bot(token=TOKEN)
dp = Dispatcher()
scheduler = AsyncIOScheduler()
class TaskForm(StatesGroup):
    employee = State()
    task = State()
    deadline = State()
menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📋 Задачи"), KeyboardButton(text="➕ Новая задача")],
        [KeyboardButton(text="✔ Выполнено")]
    ],
    resize_keyboard=True
)

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


@dp.message(CommandStart())
async def start(message: Message):
    await message.answer(
        "Привет 😄 Я секретарь-бот",
        reply_markup=menu
    )


@dp.message(Command("task"))
async def create_task(message: Message):
    raw = message.text.replace("/task", "").strip()

    parts = raw.split(".")

    if len(parts) < 3:
        await message.answer("Формат: /task ID. задача. ДД/ММ/ГГГГ")
        return

    employee_id = int(parts[0].strip())
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

    await bot.send_message(
        employee_id,
        f"📌 Новая задача\n\n{task_text}\nДедлайн: {deadline}"
    )

    await message.answer(
        f"Задача создана 😄\n\n{task_text}\nДедлайн: {deadline}"
    )
@dp.message(Command("tasks"))
async def show_tasks(message: Message):
    cursor.execute("SELECT id, task, deadline, status FROM tasks")
    tasks = cursor.fetchall()

    text = "Список задач:\n\n"

    for t in tasks:
        text += f"{t[0]}. {t[1]} | {t[2]} | {t[3]}\n"

    await message.answer(text)


@dp.message(Command("myid"))
async def myid(message: Message):
    await message.answer(str(message.from_user.id))


async def reminder():
    today = datetime.now().date()

    cursor.execute("""
        SELECT id, task, deadline, employee_id, reminded
        FROM tasks
        WHERE status != 'Выполнено'
        FROM tasks
        WHERE status != 'Выполнено'
    """)

    tasks = cursor.fetchall()

    for task_id, task_text, deadline_text, employee_id, reminded in tasks:

        try:
            deadline_date = datetime.strptime(deadline_text, "%d/%m/%Y").date()
        except:
            continue

        days_left = (deadline_date - today).days

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

@dp.message(lambda message: message.text == "📋 Задачи")
async def btn_tasks(message: Message):
    cursor.execute("SELECT id, task, deadline, status FROM tasks")
    tasks = cursor.fetchall()

    if not tasks:
        await message.answer("Пока задач нет")
        return

    text = "📋 Задачи:\n\n"
    for t in tasks:
        text += f"{t[0]}. {t[1]} | {t[2]} | {t[3]}\n"

    await message.answer(text)
async def main():
    scheduler.start()
    scheduler.add_job(reminder, "cron", hour=9, minute=0)
    await dp.start_polling(bot)

@dp.message(lambda message: "Новая задача" in message.text)
async def btn_new(message: Message, state: FSMContext):
    await state.set_state(TaskForm.employee)
    await message.answer("Кому назначить задачу? (впиши ID)")
@dp.message(TaskForm.employee)
async def get_employee(message: Message, state: FSMContext):
    await state.update_data(employee_id=int(message.text))
    await state.set_state(TaskForm.task)

    await message.answer("Опиши задачу")
@dp.message(TaskForm.task)
async def get_task(message: Message, state: FSMContext):
    await state.update_data(task=message.text)
    await state.set_state(TaskForm.deadline)

    await message.answer("Когда дедлайн? (30/05/2026 или завтра)")
@dp.message(TaskForm.deadline)
async def get_deadline(message: Message, state: FSMContext):
    data = await state.get_data()

    employee_id = data["employee_id"]
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

    await bot.send_message(
        employee_id,
        f"📌 Новая задача\n\n{task_text}\nДедлайн: {deadline}"
    )

    await message.answer("Задача создана 😄")

    await state.clear()

@dp.message(lambda message: "Выполнено" in message.text)
async def btn_done(message: Message):
    await message.answer("Напишите номер задачи, которую выполнили 😄")

@dp.message(lambda message: message.text and message.text.isdigit())
async def mark_done(message: Message):
    task_id = int(message.text)

    cursor.execute(
        "UPDATE tasks SET status=? WHERE id=?",
        ("Выполнено", task_id)
    )

    conn.commit()

    await message.answer(f"Задача №{task_id} отмечена как выполнена ✅")

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
