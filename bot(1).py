import os
import json
import logging
from pathlib import Path

import google.generativeai as genai
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

# ====== НАСТРОЙКИ ======
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "ВСТАВЬ_СЮДА_ТОКЕН_БОТА")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "ВСТАВЬ_СЮДА_GEMINI_КЛЮЧ")

SYSTEM_PROMPT = (
    "Ты дружелюбный ассистент, отвечающий от имени владельца аккаунта. "
    "Отвечай кратко, вежливо и по-человечески, без упоминания, что ты бот."
)

HISTORY_FILE = Path("chat_history.json")
MAX_HISTORY_MESSAGES = 20  # сколько последних сообщений хранить на чат

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-2.0-flash")  # быстрая и почти бесплатная модель


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


# ====== ГЕНЕРАЦИЯ ОТВЕТА ======
def generate_reply(chat_log: list, new_message: str) -> str:
    # Собираем контекст переписки в один промпт
    convo = "\n".join(
        f"{'Собеседник' if m['role'] == 'user' else 'Ты'}: {m['text']}" for m in chat_log
    )
    prompt = (
        f"{SYSTEM_PROMPT}\n\n"
        f"История переписки:\n{convo}\n\n"
        f"Новое сообщение от собеседника: {new_message}\n\n"
        f"Твой ответ:"
    )
    response = model.generate_content(prompt)
    return response.text.strip()


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

    # Сохраняем сообщение пользователя и ответ бота в историю
    append_to_history(history, chat_id, "user", text)
    append_to_history(history, chat_id, "bot", reply_text)
    save_history(history)

    # Отправляем ответ от имени владельца аккаунта
    await context.bot.send_message(
        business_connection_id=business_connection_id,
        chat_id=msg.chat.id,
        text=reply_text,
    )
    logger.info(f"Ответил в чат {chat_id}: {reply_text}")


def main() -> None:
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Telegram Business сообщения приходят отдельным типом обновления
    app.add_handler(MessageHandler(filters.ALL, handle_business_message, block=False))

    logger.info("Бот запущен, слушает Business-сообщения...")
    app.run_polling(allowed_updates=["business_message", "edited_business_message"])


if __name__ == "__main__":
    main()
