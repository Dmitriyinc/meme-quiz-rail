import asyncio
import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone

import aiohttp
from aiogram import Bot, Dispatcher, Router, types, F
from aiogram.filters import Command
from aiogram.types import (
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    WebAppInfo,
)
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "MEMEcrypted")
WEBAPP_URL = os.getenv("WEBAPP_URL", "")
SHEETS_WEBHOOK = os.getenv("SHEETS_WEBHOOK", "")
DENCHIK_CHAT_ID = int(os.getenv("DENCHIK_CHAT_ID", "0"))

# ── Sendpulse ─────────────────────────────────────────────────────
SP_CLIENT_ID = os.getenv("SP_CLIENT_ID", "sp_id_4a6ef21f199408a9cb0346c737792ba3")
SP_CLIENT_SECRET = os.getenv("SP_CLIENT_SECRET", "sp_sk_72ab44111b920c50c5dd791c13f33aa6")
SP_BOT_ID = os.getenv("SP_BOT_ID", "69f0a309d8b94020830489fb")
SP_EVENT_URL = os.getenv("SP_EVENT_URL", "https://events.sendpulse.com/events/id/33c8c2ecd9924fcf8e6923b0c1afec21/8135579")

# Кеш токена
_sp_token: str | None = None
_sp_token_expires: float = 0

KYIV = timezone(timedelta(hours=3))


# ── Database ─────────────────────────────────────────────────────

def init_db():
    conn = sqlite3.connect("quiz.db", check_same_thread=False)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            username TEXT,
            first_name TEXT,
            score INTEGER,
            total INTEGER,
            passed INTEGER,
            sections TEXT,
            time_spent INTEGER,
            attempt_num INTEGER DEFAULT 1,
            created_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS feedback (
            user_id INTEGER PRIMARY KEY,
            value TEXT,
            created_at TEXT
        )
    """)
    conn.commit()
    return conn


db = init_db()
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
router = Router()


# ── Helpers ──────────────────────────────────────────────────────

def now_kyiv():
    return datetime.now(KYIV)


def quiz_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Начать квиз", web_app=WebAppInfo(url=WEBAPP_URL))]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


async def is_subscribed(user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(CHANNEL_ID, user_id)
        return member.status in ("member", "administrator", "creator")
    except Exception as e:
        logging.error(f"Subscription check error: {e}")
        return False


async def write_to_sheets(user: types.User, data: dict, attempt_num: int):
    if not SHEETS_WEBHOOK:
        return
    score = data.get("score", 0)
    total = data.get("total", 25)
    payload = {
        "user_id": user.id,
        "username": user.username or "",
        "first_name": user.first_name or "",
        "score": score,
        "total": total,
        "passed": data.get("passed", False),
        "sections": json.dumps(data.get("sections", {}), ensure_ascii=False),
        "time_spent": data.get("time_spent", 0),
        "attempt_num": attempt_num,
        "timestamp": now_kyiv().strftime("%Y-%m-%d %H:%M:%S"),
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                SHEETS_WEBHOOK, json=payload,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                logging.info(f"Sheets: {resp.status}")
    except Exception as e:
        logging.error(f"Sheets error: {e}")


async def notify_denchik(text: str):
    if not DENCHIK_CHAT_ID:
        return
    try:
        await bot.send_message(DENCHIK_CHAT_ID, text)
    except Exception as e:
        logging.error(f"Denchik notify error: {e}")


def get_attempt_count(user_id: int) -> int:
    row = db.execute(
        "SELECT COUNT(*) FROM attempts WHERE user_id = ?", (user_id,)
    ).fetchone()
    return row[0] if row else 0


def has_passed(user_id: int) -> bool:
    row = db.execute(
        "SELECT passed FROM attempts WHERE user_id = ? AND passed = 1 LIMIT 1",
        (user_id,),
    ).fetchone()
    return row is not None


# ── Sendpulse API ─────────────────────────────────────────────────

async def sp_get_token() -> str | None:
    """Отримати OAuth токен, використовуючи кеш."""
    global _sp_token, _sp_token_expires
    if _sp_token and time.time() < _sp_token_expires - 60:
        return _sp_token
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.sendpulse.com/oauth/access_token",
                json={
                    "grant_type": "client_credentials",
                    "client_id": SP_CLIENT_ID,
                    "client_secret": SP_CLIENT_SECRET,
                },
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                data = await resp.json()
                _sp_token = data.get("access_token")
                expires_in = data.get("expires_in", 3600)
                _sp_token_expires = time.time() + expires_in
                logging.info("Sendpulse token refreshed")
                return _sp_token
    except Exception as e:
        logging.error(f"SP token error: {e}")
        return None


async def sp_get_contact_id(telegram_id: int) -> str | None:
    """Знайти contact_id в Sendpulse по telegram_id."""
    token = await sp_get_token()
    if not token:
        return None
    try:
        async with aiohttp.ClientSession() as session:
            url = (
                f"https://api.sendpulse.com/telegram/contacts/getByVariable"
                f"?variable_name=telegram_id&variable_value={telegram_id}&bot_id={SP_BOT_ID}"
            )
            async with session.get(
                url,
                headers={"Authorization": f"Bearer {token}"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                data = await resp.json()
                # Повертає список контактів
                contacts = data if isinstance(data, list) else data.get("data", [])
                if contacts:
                    contact_id = contacts[0].get("id")
                    logging.info(f"SP contact found: {contact_id} for tg:{telegram_id}")
                    return contact_id
                logging.warning(f"SP contact not found for tg:{telegram_id}")
                return None
    except Exception as e:
        logging.error(f"SP get contact error: {e}")
        return None


async def sp_set_variable(contact_id: str, variable_name: str, variable_value):
    """Записати змінну контакту в Sendpulse."""
    token = await sp_get_token()
    if not token:
        return
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.sendpulse.com/telegram/contacts/setVariable",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "contact_id": contact_id,
                    "variable_name": variable_name,
                    "variable_value": variable_value,
                },
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                logging.info(f"SP set variable {variable_name}={variable_value}: {resp.status}")
    except Exception as e:
        logging.error(f"SP set variable error: {e}")


async def sp_set_tag(contact_id: str, tag: str):
    """Поставити тег контакту в Sendpulse."""
    token = await sp_get_token()
    if not token:
        return
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.sendpulse.com/telegram/contacts/setTag",
                headers={"Authorization": f"Bearer {token}"},
                json={"contact_id": contact_id, "tag": tag},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                logging.info(f"SP set tag {tag}: {resp.status}")
    except Exception as e:
        logging.error(f"SP set tag error: {e}")


async def sp_send_event(telegram_id: int, score: int, total: int, passed: bool, attempt_num: int):
    """Відправити подію в Sendpulse — тригер для автоворонки."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                SP_EVENT_URL,
                json={
                    "email": f"{telegram_id}@tg.local",  # фейковий ідентифікатор
                    "phone": "",
                    "user_id": telegram_id,
                    "score": score,
                    "total": total,
                    "passed": "true" if passed else "false",
                    "attempt_num": attempt_num,
                },
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                text = await resp.text()
                logging.info(f"SP event sent: {resp.status} {text}")
    except Exception as e:
        logging.error(f"SP event error: {e}")


async def sp_run_flow(contact_id: str, flow_id: str):
    """Запустити флоу на конкретний контакт через Chatbot API."""
    token = await sp_get_token()
    if not token:
        return
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.sendpulse.com/telegram/flows/run",
                headers={"Authorization": f"Bearer {token}"},
                json={"contact_id": contact_id, "flow_id": flow_id},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                text = await resp.text()
                logging.info(f"SP run flow {flow_id}: {resp.status} {text}")
    except Exception as e:
        logging.error(f"SP run flow error: {e}")


async def sp_sync_contact(user: types.User, score: int, total: int, passed: bool, attempt_num: int):
    """Повний цикл: знайти контакт → записати змінні → поставити теги → запустити флоу."""
    contact_id = await sp_get_contact_id(user.id)
    if not contact_id:
        logging.warning(f"Cannot sync SP contact for {user.id} — contact not found")
        return

    # Змінні
    await sp_set_variable(contact_id, "score", score)
    await sp_set_variable(contact_id, "total", total)
    await sp_set_variable(contact_id, "attempt_num", attempt_num)
    await sp_set_variable(contact_id, "passed_quiz", "yes" if passed else "no")

    # Теги
    await sp_set_tag(contact_id, "quiz_passed" if passed else "quiz_failed")
    await sp_set_tag(contact_id, f"attempt_{attempt_num}")

    # Запуск флоу
    flow_id = os.getenv("SP_FLOW_PASSED_ID") if passed else os.getenv("SP_FLOW_FAILED_ID")
    if flow_id:
        await sp_run_flow(contact_id, flow_id)


# ── /quiz — аліас ─────────────────────────────────────────────────

@router.message(Command("quiz"))
async def cmd_quiz(message: types.Message):
    await cmd_start(message)


# ── /start ───────────────────────────────────────────────────────

@router.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    attempts = get_attempt_count(user_id)

    if attempts == 0:
        await message.answer(
            "<b>Проверь свой уровень в мемкоинах!</b>\n\n"
            "Внутри — 25 вопросов по теме. После прохождения дадим полезные "
            "материалы для закрепления знаний и инструменты для торговли.\n\n"
            "<b>Жми на кнопку и поехали</b>👇",
            parse_mode="HTML",
            reply_markup=quiz_keyboard(),
        )
        return

    if has_passed(user_id):
        await message.answer(
            "Ты уже прошел квиз. Можешь пройти еще раз.",
            reply_markup=quiz_keyboard(),
        )
        return

    sub = await is_subscribed(user_id)
    if sub:
        await message.answer(
            "Попробуй еще раз.",
            reply_markup=quiz_keyboard(),
        )
    else:
        await message.answer(
            "Чтобы перепройти квиз — подпишись на @MEMEcrypted.\n\n"
            "Как подписался — жми «Проверить».",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text="Подписаться на MEMEcrypted",
                    url=f"https://t.me/{CHANNEL_USERNAME}",
                )],
                [InlineKeyboardButton(text="Проверить", callback_data="check_sub")],
            ]),
        )


# ── Quiz result from Web App ─────────────────────────────────────

@router.message(F.web_app_data)
async def handle_quiz_result(message: types.Message):
    user = message.from_user
    try:
        data = json.loads(message.web_app_data.data)
    except json.JSONDecodeError:
        logging.error(f"Bad web_app_data from {user.id}")
        return

    score = data.get("score", 0)
    total = data.get("total", 25)
    passed = data.get("passed", False)
    sections = data.get("sections", {})
    time_spent = data.get("time_spent", 0)

    attempt_num = get_attempt_count(user.id) + 1

    # Зберігаємо в БД
    db.execute(
        "INSERT INTO attempts (user_id, username, first_name, score, total, passed, sections, time_spent, attempt_num, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (user.id, user.username, user.first_name,
         score, total, int(passed),
         json.dumps(sections, ensure_ascii=False),
         time_spent, attempt_num, now_kyiv().isoformat()),
    )
    db.commit()

    # ── Sendpulse: подія (тригер автоворонки) + синхронізація контакту
    asyncio.create_task(sp_send_event(user.id, score, total, passed, attempt_num))
    asyncio.create_task(sp_sync_contact(user, score, total, passed, attempt_num))

    # ── Google Sheets (якщо є)
    asyncio.create_task(write_to_sheets(user, data, attempt_num))

    # ── Повідомлення адміну / Денчику
    name = f"@{user.username}" if user.username else user.first_name or str(user.id)
    denchik_text = f"{name} (ID: {user.id}) {'прошел' if passed else 'не прошел'} квиз — {score}/{total}, попытка {attempt_num}"
    asyncio.create_task(notify_denchik(denchik_text))
    try:
        await bot.send_message(ADMIN_ID, denchik_text)
    except Exception as e:
        logging.error(f"Admin notify error: {e}")

    # ── Відповідь юзеру (мінімальна — основний контент іде через Sendpulse)
    await message.answer(
        "Результат отправлен! Сейчас подготовим материалы для тебя 👇",
        reply_markup=ReplyKeyboardRemove(),
    )

    # ── Фідбек через 3 секунди
    await asyncio.sleep(3)
    has_feedback = db.execute(
        "SELECT 1 FROM feedback WHERE user_id = ?", (user.id,)
    ).fetchone()
    if not has_feedback:
        await message.answer(
            "Как тебе такой формат активностей от нас?\n\nБудем делать еще, если заходит.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="👍 Круто, давайте еще", callback_data="fb_up"),
                    InlineKeyboardButton(text="👎 Не мое", callback_data="fb_down"),
                ],
            ]),
        )


# ── Feedback ─────────────────────────────────────────────────────

@router.callback_query(F.data.in_({"fb_up", "fb_down"}))
async def handle_feedback(callback: types.CallbackQuery):
    value = "👍" if callback.data == "fb_up" else "👎"
    db.execute(
        "INSERT OR REPLACE INTO feedback (user_id, value, created_at) VALUES (?, ?, ?)",
        (callback.from_user.id, value, now_kyiv().isoformat()),
    )
    db.commit()

    await callback.message.edit_text("Спасибо за ответ!")
    await callback.answer()

    name = f"@{callback.from_user.username}" if callback.from_user.username else callback.from_user.first_name
    asyncio.create_task(notify_denchik(f"{name} — {value}"))

    if SHEETS_WEBHOOK:
        try:
            async with aiohttp.ClientSession() as session:
                await session.post(SHEETS_WEBHOOK, json={
                    "type": "event",
                    "event": f"feedback_{value}",
                    "user_id": callback.from_user.id,
                    "username": callback.from_user.username or "",
                    "first_name": callback.from_user.first_name or "",
                })
        except Exception:
            pass


# ── Retry flow ───────────────────────────────────────────────────

@router.callback_query(F.data == "retry_quiz")
async def retry_quiz(callback: types.CallbackQuery):
    user_id = callback.from_user.id

    sub = await is_subscribed(user_id)
    if not sub:
        await callback.message.edit_text(
            "Чтобы перепройти квиз — подпишись на @MEMEcrypted. Это займет 10 секунд.\n\n"
            "Как подписался — жми «Проверить».",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text="Подписаться на MEMEcrypted",
                    url=f"https://t.me/{CHANNEL_USERNAME}",
                )],
                [InlineKeyboardButton(text="Проверить", callback_data="check_sub")],
            ]),
        )
        await callback.answer()
        return

    await callback.message.answer("Поехали!", reply_markup=quiz_keyboard())
    await callback.answer()


@router.callback_query(F.data == "check_sub")
async def check_sub_callback(callback: types.CallbackQuery):
    user_id = callback.from_user.id

    sub = await is_subscribed(user_id)
    if not sub:
        await callback.answer("Подписка не найдена", show_alert=True)
        return

    await callback.message.edit_text("Подписка есть!")
    await callback.answer()
    await callback.message.answer("Поехали!", reply_markup=quiz_keyboard())


# ── /stats ───────────────────────────────────────────────────────

@router.message(Command("stats"))
async def cmd_stats(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return

    total_attempts = db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0]
    if total_attempts == 0:
        await message.answer("Пока нет данных.")
        return

    unique_users = db.execute("SELECT COUNT(DISTINCT user_id) FROM attempts").fetchone()[0]
    passed_count = db.execute("SELECT COUNT(*) FROM attempts WHERE passed = 1").fetchone()[0]
    failed_count = total_attempts - passed_count
    avg_score = db.execute("SELECT AVG(score) FROM attempts").fetchone()[0] or 0
    avg_time = db.execute("SELECT AVG(time_spent) FROM attempts").fetchone()[0] or 0

    today = now_kyiv().strftime("%Y-%m-%d")
    today_attempts = db.execute(
        "SELECT COUNT(*) FROM attempts WHERE created_at LIKE ?", (f"{today}%",)
    ).fetchone()[0]

    pass_rate = round(passed_count / total_attempts * 100)
    avg_min = int(avg_time) // 60
    avg_sec = int(avg_time) % 60

    fb_up = db.execute("SELECT COUNT(*) FROM feedback WHERE value = '👍'").fetchone()[0]
    fb_down = db.execute("SELECT COUNT(*) FROM feedback WHERE value = '👎'").fetchone()[0]

    all_sections = db.execute("SELECT sections FROM attempts").fetchall()
    sec_stats = {}
    for (raw,) in all_sections:
        try:
            secs = json.loads(raw)
            for sec, r in secs.items():
                if sec not in sec_stats:
                    sec_stats[sec] = {"correct": 0, "total": 0}
                sec_stats[sec]["correct"] += r.get("correct", 0)
                sec_stats[sec]["total"] += r.get("total", 0)
        except (json.JSONDecodeError, AttributeError):
            continue

    section_lines = ""
    for sec, r in sorted(sec_stats.items(), key=lambda x: x[1]["correct"] / max(x[1]["total"], 1)):
        pct = round(r["correct"] / max(r["total"], 1) * 100)
        section_lines += f"  {sec}: {pct}%\n"

    text = (
        f"Статистика квиза\n\n"
        f"Попыток: {total_attempts}\n"
        f"Юзеров: {unique_users}\n"
        f"Прошли: {passed_count} ({pass_rate}%)\n"
        f"Не прошли: {failed_count} ({100 - pass_rate}%)\n"
        f"Средний скор: {avg_score:.1f}/25\n"
        f"Среднее время: {avg_min}:{avg_sec:02d}\n"
        f"Сегодня: {today_attempts}\n"
        f"Фидбек: 👍 {fb_up} / 👎 {fb_down}\n\n"
        f"По сложности:\n{section_lines}"
    )
    await message.answer(text)


# ── Run ──────────────────────────────────────────────────────────

dp.include_router(router)


async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    logging.info("Bot starting...")
    await bot.set_my_commands([
        types.BotCommand(command="quiz", description="Пройти квиз"),
        types.BotCommand(command="start", description="Начать"),
    ])
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
