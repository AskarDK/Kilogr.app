import logging
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    filters,
    ContextTypes
)
from app import db, User, app
import os
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO)

# Состояние
ASK_EMAIL = 1

# Команда /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Чтобы получать свою диету через Telegram, отправь мне свой email, с которым ты регистрировался."
    )
    return ASK_EMAIL

# Получение email
async def receive_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    email = update.message.text.strip()

    with app.app_context():
        user = db.session.query(User).filter_by(email=email).first()

        if not user:
            await update.message.reply_text("❌ Пользователь с таким email не найден.")
            return ConversationHandler.END

        user.telegram_chat_id = str(update.message.chat_id)
        db.session.commit()

    await update.message.reply_text("✅ Вы успешно подписались на рассылку диет!")
    return ConversationHandler.END

# Команда /cancel
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Операция отменена.")
    return ConversationHandler.END


def run_bot():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    app_telegram = Application.builder().token(token).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            ASK_EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_email)]
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )

    app_telegram.add_handler(conv_handler)
    logging.info("Telegram-бот запущен.")
    app_telegram.run_polling()


if __name__ == '__main__':
    run_bot()
