import os
import re
import time
import random
import logging
import sqlite3
import asyncio
from pathlib import Path

from groq import Groq
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    MessageHandler,
    CommandHandler,
    CallbackQueryHandler,
    PreCheckoutQueryHandler,
    filters,
    ContextTypes,
)
from telegram.error import TelegramError

# ====== НАСТРОЙКИ ======
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "ВСТАВЬ_СЮДА_ТОКЕН_БОТА")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "ВСТАВЬ_СЮДА_GROQ_КЛЮЧ")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))  # Telegram user_id владельца аккаунта

DISCLAIMER_TEXT = (
    "(автоответ) привет, я автоответчик, владелец сейчас не может ответить сам, "
    "но я постараюсь помочь по переписке 🙂"
)

SYSTEM_PROMPT = (
    "Ты пишешь в личных сообщениях от имени владельца аккаунта, обычному собеседнику (другу/знакомому). "
    "Собеседнику уже известно, что это автоответчик — в начале разговора ему показали дисклеймер об этом. "
    "Твоя задача — отвечать максимально похоже на живого человека в реальной переписке, а не как формальный бот-ассистент.\n\n"
    "Стиль:\n"
    "- Пиши как обычный человек в чате: естественно, без формальностей и канцелярита.\n"
    "- Используй разговорный сленг и сокращения там, где это естественно ('норм', 'чё', 'ладно', 'ок', 'хз', 'ну' и т.п.), "
    "но не используй слово 'кек' и подобные заезженные интернет-мемные словечки.\n"
    "- Мат и грубые слова допустимы, если это соответствует тону переписки собеседника, но не вставляй их искусственно, "
    "где это не в тему.\n"
    "- Не пиши длинными правильными предложениями как нейросеть-ассистент — звучи расслабленно, как человек, "
    "которому не нужно ничего доказывать.\n\n"
    "Правила по смыслу:\n"
    "1. Отвечай только по смыслу того, что написал собеседник. Не придумывай тему или факты, которых не было в переписке.\n"
    "2. Если сообщение короткое и нейтральное ('привет', 'ку', 'как дела') — отвечай так же коротко и непринуждённо.\n"
    "3. Если тебя прямо спрашивают про то, что ты бот/автоответчик — можешь честно подтвердить это коротко и спокойно, "
    "без формального тона ('ну да, я автоответчик, написал в начале же' и т.п.), не нужно скрывать или отрицать это.\n"
    "4. Если собеседник спрашивает что-то странное, непонятное по контексту, или явно адресованное не тебе/не туда "
    "(например путает с другим человеком, спрашивает про планы/события, о которых в переписке вообще не было речи, "
    "или вопрос не имеет смысла исходя из истории чата) — не придумывай ответ и не пытайся угадать, о чём речь. "
    "Вместо этого отмазывайся естественно, как человек, который не понял вопрос или не в курсе: "
    "'ты о чём вообще', 'не понял о чём ты', 'хз, ты не путаешь меня с кем-то', 'я не в курсе про это, давай потом'. "
    "Можно добавить что-то от себя в тон ситуации (лёгкое недоумение, шутку, нейтральную фразу), "
    "но не выдумывай факты или контекст, которых не было.\n"
    "5. НЕ задавай вопрос собеседнику в каждом своём сообщении. Большинство ответов должны быть утверждениями/реакциями "
    "без вопроса на конце. Задавай вопрос только если это реально нужно для разговора, не чаще чем примерно "
    "в одном из 3-4 сообщений.\n"
    "6. Не повторяй и не пересказывай то, что уже написал собеседник.\n"
    "7. Длина ответа должна сильно варьироваться от сообщения к сообщению, а не быть одинаковой. "
    "Если тема нейтральная и малозначимая — отвечай совсем коротко, иногда буквально одним словом или междометием "
    "('норм', 'ок', 'ну ку', 'хах', 'жиза'). Если тема требует развёрнутого ответа или собеседник написал длинно — "
    "отвечай длиннее, несколькими предложениями. Не зацикливайся на одном среднем размере ответа — "
    "чередуй короткие реакции и более развёрнутые сообщения в зависимости от того, что реально уместно, "
    "как это естественно происходит у живых людей в переписке.\n"
    "8. Учитывай предыдущие свои сообщения в истории переписки: если видишь, что твой прошлый ответ был неудачным, "
    "странным или не в тему (например собеседник переспросил, не понял, или отреагировал негативно) — "
    "не повторяй тот же подход, скорректируй стиль и смысл в следующем ответе, как это сделал бы человек, "
    "осознавший, что сказал что-то не то."
)

DB_PATH = Path("chat_history.db")
MAX_HISTORY_MESSAGES = 20  # сколько последних сообщений хранить на чат

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

groq_client = Groq(api_key=GROQ_API_KEY)


# ====== SQLITE: ИНИЦИАЛИЗАЦИЯ И НИЗКОУРОВНЕВЫЕ ФУНКЦИИ ======
def _get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    return conn


def init_db() -> None:
    conn = _get_connection()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT NOT NULL,
                role TEXT NOT NULL,
                text TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_chat_id ON messages(chat_id, id)"
        )
        conn.commit()
    finally:
        conn.close()


def _get_chat_history_sync(chat_id: str, limit: int) -> list:
    conn = _get_connection()
    try:
        cur = conn.execute(
            "SELECT role, text FROM messages WHERE chat_id = ? ORDER BY id DESC LIMIT ?",
            (chat_id, limit),
        )
        rows = cur.fetchall()
        rows.reverse()
        return [{"role": role, "text": text} for role, text in rows]
    finally:
        conn.close()


def _chat_has_history_sync(chat_id: str) -> bool:
    conn = _get_connection()
    try:
        cur = conn.execute(
            "SELECT 1 FROM messages WHERE chat_id = ? LIMIT 1",
            (chat_id,),
        )
        return cur.fetchone() is not None
    finally:
        conn.close()


def _append_messages_sync(chat_id: str, entries: list, max_history: int) -> None:
    conn = _get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        for role, text in entries:
            conn.execute(
                "INSERT INTO messages (chat_id, role, text) VALUES (?, ?, ?)",
                (chat_id, role, text),
            )
        conn.execute(
            """
            DELETE FROM messages
            WHERE chat_id = ? AND id NOT IN (
                SELECT id FROM messages WHERE chat_id = ? ORDER BY id DESC LIMIT ?
            )
            """,
            (chat_id, chat_id, max_history),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


async def get_chat_history(chat_id: str, limit: int = MAX_HISTORY_MESSAGES) -> list:
    return await asyncio.to_thread(_get_chat_history_sync, chat_id, limit)


async def chat_has_history(chat_id: str) -> bool:
    return await asyncio.to_thread(_chat_has_history_sync, chat_id)


async def append_history(chat_id: str, entries: list) -> None:
    await asyncio.to_thread(_append_messages_sync, chat_id, entries, MAX_HISTORY_MESSAGES)


_chat_locks: dict = {}
_locks_guard = asyncio.Lock()


async def get_chat_lock(chat_id: str) -> asyncio.Lock:
    async with _locks_guard:
        if chat_id not in _chat_locks:
            _chat_locks[chat_id] = asyncio.Lock()
        return _chat_locks[chat_id]


def generate_reply(chat_log: list, new_message: str) -> str:
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for m in chat_log:
        role = "user" if m["role"] == "user" else "assistant"
        messages.append({"role": role, "content": m["text"]})
    messages.append({"role": "user", "content": new_message})

    response = groq_client.chat.completions.create(
        model=GROQ_MODEL,
        messages=messages,
        max_tokens=500,
        temperature=0.7,
    )
    return response.choices[0].message.content.strip()


business_owner_cache: dict = {}


async def get_business_owner_id(context: ContextTypes.DEFAULT_TYPE, business_connection_id: str):
    if business_connection_id in business_owner_cache:
        return business_owner_cache[business_connection_id]
    try:
        connection = await context.bot.get_business_connection(business_connection_id)
        owner_id = connection.user.id
        business_owner_cache[business_connection_id] = owner_id
        return owner_id
    except Exception as e:
        logger.error(f"Не удалось получить владельца business-подключения: {e}")
        return None


# ====== !PING — ПРОВЕРКА ПИНГА (ТОЛЬКО ДЛЯ ВЛАДЕЛЬЦА) ======
async def measure_telegram_ping(context: ContextTypes.DEFAULT_TYPE) -> float:
    start = time.monotonic()
    await context.bot.get_me()
    return (time.monotonic() - start) * 1000  # в миллисекундах


def measure_db_ping() -> float:
    start = time.monotonic()
    conn = _get_connection()
    try:
        conn.execute("SELECT 1").fetchone()
    finally:
        conn.close()
    return (time.monotonic() - start) * 1000


def build_ping_message(tg_ms: float, db_ms: float) -> str:
    def status_icon(ms: float, good: float, ok: float) -> str:
        if ms <= good:
            return "🟢"
        if ms <= ok:
            return "🟡"
        return "🔴"

    tg_icon = status_icon(tg_ms, 150, 400)
    db_icon = status_icon(db_ms, 10, 50)

    return (
        "<b>📡 Статус сервера</b>\n\n"
        f"{tg_icon} <b>Telegram API:</b> <code>{tg_ms:.1f} ms</code>\n"
        f"{db_icon} <b>SQLite:</b> <code>{db_ms:.1f} ms</code>\n"
    )


async def handle_ping_command(
    context: ContextTypes.DEFAULT_TYPE,
    business_connection_id: str | None,
    chat_id: int,
) -> None:
    try:
        tg_ms = await measure_telegram_ping(context)
        db_ms = await asyncio.to_thread(measure_db_ping)
        text = build_ping_message(tg_ms, db_ms)
    except Exception as e:
        logger.error(f"Не удалось измерить пинг: {e}")
        text = "⚠️ Не получилось измерить пинг."

    try:
        await context.bot.send_message(
            business_connection_id=business_connection_id,
            chat_id=chat_id,
            text=text,
            parse_mode=ParseMode.HTML,
        )
    except TelegramError as e:
        logger.error(f"Не удалось отправить пинг в чат {chat_id}: {e}")


# ====== ОТПРАВКА ОТВЕТА (С ЛЕСЕНКОЙ, БЕЗ ЗАДЕРЖКИ) ======
def split_into_chunks(text: str) -> list:
    sentences = re.split(r'(?<=[.!?…])\s+', text.strip())
    sentences = [s for s in sentences if s]

    if len(sentences) < 2:
        return [text]

    parts_count = min(random.choice([2, 2, 3]), len(sentences))

    chunks = []
    avg = len(sentences) / parts_count
    idx = 0
    for i in range(parts_count):
        end = round(avg * (i + 1))
        part = " ".join(sentences[idx:end]).strip()
        if part:
            chunks.append(part)
        idx = end

    return chunks if chunks else [text]


async def send_reply(
    context: ContextTypes.DEFAULT_TYPE,
    business_connection_id: str,
    chat_id: int,
    text: str,
) -> None:
    if len(text) > 60 and random.random() < 0.35:
        chunks = split_into_chunks(text)
    else:
        chunks = [text]

    for chunk in chunks:
        try:
            await context.bot.send_message(
                business_connection_id=business_connection_id,
                chat_id=chat_id,
                text=chunk,
            )
        except TelegramError as e:
            logger.error(f"Не удалось отправить часть сообщения в чат {chat_id}: {e}")
            break


# ====== ОБРАБОТЧИК BUSINESS-СООБЩЕНИЙ ======
async def handle_business_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.business_message
    if msg is None or not msg.text:
        return

    business_connection_id = msg.business_connection_id
    is_owner_message = bool(msg.from_user and msg.from_user.id == OWNER_ID)

    # !ping от владельца работает прямо в business-чате, с кем бы он ни переписывался
    if is_owner_message and msg.text.strip() == "!ping":
        await handle_ping_command(context, business_connection_id, msg.chat.id)
        return

    # Игнорируем остальные сообщения, которые отправил сам владелец аккаунта
    owner_id = await get_business_owner_id(context, business_connection_id)
    if owner_id is not None and msg.from_user and msg.from_user.id == owner_id:
        return

    chat_id = str(msg.chat.id)
    text = msg.text

    lock = await get_chat_lock(chat_id)
    async with lock:
        is_new_chat = not await chat_has_history(chat_id)

        if is_new_chat:
            try:
                await context.bot.send_message(
                    business_connection_id=business_connection_id,
                    chat_id=msg.chat.id,
                    text=DISCLAIMER_TEXT,
                )
            except TelegramError as e:
                logger.error(f"Не удалось отправить дисклеймер в чат {chat_id}: {e}")

        chat_log = await get_chat_history(chat_id)

        try:
            reply_text = await asyncio.to_thread(generate_reply, chat_log, text)
        except Exception as e:
            logger.error(f"Ошибка генерации ответа: {e}")
            reply_text = "Извини, сейчас не могу ответить, скоро вернусь к переписке."

        if not reply_text:
            reply_text = "Хорошо, понял."

        await append_history(chat_id, [("user", text), ("bot", reply_text)])

    await send_reply(context, business_connection_id, msg.chat.id, reply_text)
    logger.info(f"Ответил в чат {chat_id}")


# ====== ГЛАВНОЕ МЕНЮ ======
def build_main_menu() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("О боте", callback_data="about")],
        [InlineKeyboardButton("⭐ Задонатить звёздами", callback_data="donate_menu")],
    ]
    return InlineKeyboardMarkup(keyboard)


def build_donate_menu() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("⭐ 25", callback_data="donate_25")],
        [InlineKeyboardButton("⭐ 50", callback_data="donate_50")],
        [InlineKeyboardButton("⭐ 100", callback_data="donate_100")],
        [InlineKeyboardButton("⭐ 250", callback_data="donate_250")],
        [InlineKeyboardButton("« Назад", callback_data="back_main")],
    ]
    return InlineKeyboardMarkup(keyboard)


# ====== ОБРАБОТЧИК ОБЫЧНЫХ СООБЩЕНИЙ (ПРЯМО БОТУ, НЕ ЧЕРЕЗ BUSINESS) ======
async def handle_direct_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    if msg is None or msg.from_user is None or not msg.text:
        return

    raw_text = msg.text.strip()
    text = raw_text.lower()

    # !ping доступен только владельцу аккаунта
    if raw_text == "!ping":
        if OWNER_ID and msg.from_user.id == OWNER_ID:
            await handle_ping_command(context, None, msg.chat.id)
        # если написал не владелец — молча игнорируем
        return

    if text.startswith("/start"):
        await context.bot.send_message(
            chat_id=msg.chat.id,
            text="Привет! Я Urushi — бот для автоответов в личных сообщениях через Telegram Business.",
            reply_markup=build_main_menu(),
        )
    else:
        await context.bot.send_message(
            chat_id=msg.chat.id,
            text="Я работаю только как автоответчик в Business-чатах, тут просто ничего не делаю 🙂\n\nМеню — /start",
        )


# ====== ОБРАБОТКА НАЖАТИЙ НА КНОПКИ МЕНЮ ======
STAR_AMOUNTS = {
    "donate_25": 25,
    "donate_50": 50,
    "donate_100": 100,
    "donate_250": 250,
}


async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    if query.data == "about":
        await query.edit_message_text(
            text="Urushi — личный AI-автоответчик для Telegram Business.\nОтвечает за тебя в личных сообщениях, пока ты занят.",
            reply_markup=build_main_menu(),
        )

    elif query.data == "donate_menu":
        await query.edit_message_text(
            text="Выбери сумму донат в звёздах ⭐",
            reply_markup=build_donate_menu(),
        )

    elif query.data == "back_main":
        await query.edit_message_text(
            text="Привет! Я Urushi — бот для автоответов в личных сообщениях через Telegram Business.",
            reply_markup=build_main_menu(),
        )

    elif query.data in STAR_AMOUNTS:
        amount = STAR_AMOUNTS[query.data]
        await context.bot.send_invoice(
            chat_id=query.message.chat.id,
            title=f"Донат {amount} звёзд",
            description="Спасибо за поддержку бота Urushi ⭐",
            payload=f"donate_{amount}",
            provider_token="",
            currency="XTR",
            prices=[LabeledPrice(label=f"{amount} звёзд", amount=amount)],
        )


# ====== ПОДТВЕРЖДЕНИЕ ОПЛАТЫ ЗВЁЗДАМИ ======
async def handle_pre_checkout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.pre_checkout_query
    await query.answer(ok=True)


async def handle_successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    payment = update.message.successful_payment
    await update.message.reply_text(
        f"Спасибо за донат в {payment.total_amount} ⭐! Очень приятно 🙏"
    )


def main() -> None:
    init_db()

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(MessageHandler(filters.UpdateType.BUSINESS_MESSAGE, handle_business_message, block=False))
    app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.PRIVATE, handle_direct_message, block=False))
    app.add_handler(CallbackQueryHandler(handle_callback_query))
    app.add_handler(PreCheckoutQueryHandler(handle_pre_checkout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, handle_successful_payment))

    logger.info("Бот запущен, слушает Business-сообщения и прямые сообщения...")
    app.run_polling(allowed_updates=["business_message", "edited_business_message", "message", "callback_query", "pre_checkout_query"])


if __name__ == "__main__":
    main()
