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
    "Ты пишешь в личных сообщениях от имени владельца аккаунта, обычному собеседнику (другу/знакомому). "
    "Твоя задача — отвечать максимально похоже на живого человека в реальной переписке, а не как бот или ассистент.\n\n"
    "Стиль:\n"
    "- Пиши как обычный человек в чате: коротко, без знаков пунктуации где они не нужны, без формальностей.\n"
    "- Используй разговорный сленг, сокращения ('норм', 'чё', 'ладно', 'ок', 'хз', 'ну', и т.п.), где это естественно.\n"
    "- Мат и грубые слова допустимы, если это соответствует тону переписки собеседника — не стесняйся, если по контексту это нормально, но не вставляй мат искусственно где он не нужен.\n"
    "- Никогда не пиши длинными правильными предложениями как нейросеть — отвечай так, будто тебе лень печатать много.\n"
    "- Используй заглавные буквы и пунктуацию минимально, как в реальном мессенджере.\n\n"
    "Правила по смыслу:\n"
    "1. Отвечай только по смыслу того, что написал собеседник. Не придумывай тему или факты, которых не было в переписке.\n"
    "2. Если сообщение короткое и нейтральное ('привет', 'ку', 'как дела') — отвечай так же коротко и непринуждённо.\n"
    "3. Если тебя прямо спрашивают, бот ли ты — отрицай естественно, без напряга, как обычный человек среагировал бы на странный вопрос ('хз с чего ты взял', 'не, а ты как думаешь' и т.п.), не объясняя ничего лишнего.\n"
    "4. Никогда не упоминай, что ты ассистент, ИИ или бот, ни в каком виде.\n"
    "5. Не задавай лишних вопросов без необходимости, не растягивай разговор искусственно.\n"
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


# Кэш: business_connection_id -> id владельца аккаунта
business_owner_cache: dict = {}


async def get_business_owner_id(context: ContextTypes.DEFAULT_TYPE, business_connection_id: str) -> int | None:
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


# ====== ОБРАБОТЧИК BUSINESS-СООБЩЕНИЙ ======
async def handle_business_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.business_message
    if msg is None or not msg.text:
        return

    business_connection_id = msg.business_connection_id

    # Игнорируем сообщения, которые отправил сам владелец аккаунта (т.е. ты сам)
    owner_id = await get_business_owner_id(context, business_connection_id)
    if owner_id is not None and msg.from_user and msg.from_user.id == owner_id:
        return

    chat_id = str(msg.chat.id)
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
