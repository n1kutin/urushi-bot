import os
import json
import logging
from pathlib import Path

from groq import Groq
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from telegram.error import TelegramError

# ====== НАСТРОЙКИ ======
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "ВСТАВЬ_СЮДА_ТОКЕН_БОТА")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "ВСТАВЬ_СЮДА_GROQ_КЛЮЧ")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

SYSTEM_PROMPT = (
    "Ты отвечаешь в личных сообщениях от имени владельца аккаунта в обычной переписке с друзьями/знакомыми. "
    "Правила:\n"
    "1. Отвечай только по смыслу того, что написал собеседник. Никогда не придумывай тему, "
    "факты или подробности, которых не было в переписке.\n"
    "2. Если сообщение собеседника короткое, нейтральное или непонятное (например 'ку', 'привет', 'норм') — "
    "отвечай в том же духе, коротко и непринуждённо, не выдумывая контекст.\n"
    "3. Пиши в разговорном стиле, как обычный человек в чате: коротко, без формальностей, без канцелярита.\n"
    "4. Никогда не упоминай, что ты бот или ассистент.\n"
    "5. Не задавай лишних вопросов, если в этом нет смысла по контексту.\n"
    "6. Не повторяй и не пересказывай то, что уже написал собеседник."
)

HISTORY_FILE = Path("chat_history.json")
MAX_HISTORY_MESSAGES = 20  # сколько последних сообщений хранить на чат

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

groq_client = Groq(api_key=GROQ_API_KEY)


# ====== ХРАНЕНИЕ ИСТОРИИ ПЕРЕПИСКИ ПО ЧАТАМ ======
def load_history() -> dict:
    if HISTORY_FILE.exists():
        return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    return {}


def save_history(history: dict) -> None:
    HISTORY_FILE.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")


def get_chat_history(history: dict, chat_id: str) -> list:
    return history.get(chat_id, [])


def append_to_history(history: dict, chat_id: str, role: str, text: str) -> None:
    chat_log = history.setdefault(chat_id, [])
    chat_log.append({"role": role, "text": text})
    if len(chat_log) > MAX_HISTORY_MESSAGES:
        del chat_log[: len(chat_log) - MAX_HISTORY_MESSAGES]


# ====== ГЕНЕРАЦИЯ ОТВЕТА ЧЕРЕЗ GROQ ======
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


# ====== ОБРАБОТЧИК BUSINESS-СООБЩЕНИЙ ======
async def handle_business_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.business_message
    if msg is None or not msg.text:
        return

    chat_id = str(msg.chat.id)
    business_connection_id = msg.business_connection_id
    text = msg.text

    history = load_history()
    chat_log = get_chat_history(history, chat_id)

    try:
        reply_text = generate_reply(chat_log, text)
    except Exception as e:
        logger.error(f"Ошибка генерации ответа: {e}")
        reply_text = "Извини, сейчас не могу ответить, скоро вернусь к переписке."

    if not reply_text:
        reply_text = "Хорошо, понял."

    # Сохраняем сообщение пользователя и ответ бота в историю
    append_to_history(history, chat_id, "user", text)
    append_to_history(history, chat_id, "bot", reply_text)
    save_history(history)

    # Отправляем ответ от имени владельца аккаунта
    try:
        await context.bot.send_message(
            business_connection_id=business_connection_id,
            chat_id=msg.chat.id,
            text=reply_text,
        )
        logger.info(f"Ответил в чат {chat_id}: {reply_text}")
    except TelegramError as e:
        logger.error(f"Не удалось отправить сообщение в чат {chat_id}: {e}")


def main() -> None:
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Telegram Business сообщения приходят отдельным типом обновления
    app.add_handler(MessageHandler(filters.ALL, handle_business_message, block=False))

    logger.info("Бот запущен, слушает Business-сообщения...")
    app.run_polling(allowed_updates=["business_message", "edited_business_message"])


if __name__ == "__main__":
    main()
