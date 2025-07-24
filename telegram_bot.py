import os
import re
import logging
import base64
import aiohttp
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler
)
from openai import AsyncOpenAI
from dotenv import load_dotenv
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import date

# Загрузка .env
load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

TIMEZONE = "Asia/Almaty"
BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:5000")

# Инициализация Async OpenAI и токен бота
client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
app_token = os.getenv("TELEGRAM_BOT_TOKEN")

# Папка для временных фото
os.makedirs("temp_photos", exist_ok=True)

# Состояния ConversationHandler
ASK_CODE, SELECT_MENU, ASK_PHOTO, ASK_COMMENT, ANALYZE_FOOD, HANDLE_SAVE, OVERWRITE_CONFIRM = range(7)


# ——— HANDLERS ——————————————————————————————————————————————

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    chat_id = update.effective_chat.id
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{BACKEND_URL}/api/is_registered/{chat_id}") as resp:
            if resp.status == 200:
                keyboard = [
                    [InlineKeyboardButton("📷 Оценка порций", callback_data="estimate")],
                    [InlineKeyboardButton("➕ Добавить приём пищи", callback_data="add")],
                    [InlineKeyboardButton("🥗 Текущая диета", callback_data="current")]
                ]
                await update.message.reply_text(
                    "👋 Что хотите сделать?",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                return SELECT_MENU
            else:
                await update.message.reply_text("🔐 Введите 6-значный код из личного кабинета:")
                return ASK_CODE

async def verify_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code = update.message.text.strip()
    chat_id = update.effective_chat.id
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{BACKEND_URL}/api/link_telegram",
            json={"code": code, "chat_id": chat_id}
        ) as resp:
            if resp.status == 200:
                await update.message.reply_text("✅ Telegram привязан! Введите /start.")
                return ConversationHandler.END
            else:
                await update.message.reply_text("❌ Неверный код. Попробуйте снова:")
                return ASK_CODE

async def handle_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "estimate":
        context.user_data["mode"] = "estimate"
        await query.edit_message_text("📸 Пожалуйста, отправьте фото для оценки порций:")
        return ASK_COMMENT

    if data == "add":
        context.user_data["mode"] = "add"
        keyboard = [
            [InlineKeyboardButton("🍳 Завтрак", callback_data="breakfast")],
            [InlineKeyboardButton("🍛 Обед", callback_data="lunch")],
            [InlineKeyboardButton("🍲 Ужин", callback_data="dinner")],
            [InlineKeyboardButton("🥜 Перекус", callback_data="snack")],
            [InlineKeyboardButton("🔙 Назад", callback_data="back")]
        ]
        await query.edit_message_text(
            "Выберите тип приёма пищи:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return ASK_PHOTO

    if data == "current":
        await query.edit_message_text("⏳ Загружаю текущую диету...")
        chat_id = query.message.chat.id
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{BACKEND_URL}/api/current_diet/{chat_id}") as resp:
                if resp.status != 200:
                    await query.message.reply_text("⚠️ Диета не найдена.")
                    return SELECT_MENU
                diet = await resp.json()

        def fmt(meal, items):
            lines = [f"🍱 {meal}:"]
            for i in items:
                lines.append(f"- {i['name']} ({i['grams']} г, {i['kcal']} ккал)")
            return "\n".join(lines)

        msg = (
            f"📅 Дата: {diet['date']}\n\n"
            f"{fmt('Завтрак',  diet['breakfast'])}\n\n"
            f"{fmt('Обед',     diet['lunch'])}\n\n"
            f"{fmt('Ужин',     diet['dinner'])}\n\n"
            f"{fmt('Перекус',  diet['snack'])}\n\n"
            f"🔥 Калории: {diet['total_kcal']} ккал\n"
            f"🍗 Белки: {diet['protein']} г\n"
            f"🥑 Жиры: {diet['fat']} г\n"
            f"🥔 Углеводы: {diet['carbs']} г"
        )
        await query.message.reply_text(msg)
        return SELECT_MENU

    return await start(update, context)

async def ask_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "back":
        return await start(update, context)

    context.user_data["meal_type"] = query.data
    await query.edit_message_text("📸 Пожалуйста, отправьте фото еды:")
    return ASK_COMMENT

async def ask_comment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        await update.message.reply_text("Пожалуйста, отправьте фотографию.")
        return ASK_COMMENT

    file_id = update.message.photo[-1].file_id
    try:
        photo_file = await context.bot.get_file(file_id)
        path = f"temp_photos/{update.effective_user.id}_{file_id}.jpg"
        await photo_file.download_to_drive(path)
        context.user_data["photo_path"] = path
    except Exception:
        await update.message.reply_text("⚠️ Не удалось получить или сохранить фото. Попробуйте ещё раз.")
        return ASK_COMMENT

    await update.message.reply_text("✍️ Опишите, что на фото (название блюда, порции):")
    return ANALYZE_FOOD

async def analyze_food(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_comment = update.message.text.strip()
    photo_path = context.user_data.get("photo_path")
    meal_type = context.user_data.get("meal_type", "приём пищи")
    mode = context.user_data.get("mode")

    messages = [{
        "role": "system",
        "content": (
            "Ты — профессиональный диетолог. Проанализируй фото и описание. "
            "Цифры должны быть точными, без приблизительных оценок через дифис вроде 45-50. Верни в формате:\n"
            "Калории: X ккал\nБелки: X г\nЖиры: X г\nУглеводы: X г\nКомментарий: ..."
        )
    }]

    if photo_path:
        with open(photo_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        messages.append({
            "role": "user",
            "content": [
                {"type": "text", "text": f"{'Добавление' if mode == 'add' else 'Оценка'}: {user_comment}"},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}
            ]
        })
    else:
        messages.append({"role": "user", "content": user_comment})

    try:
        resp = await client.chat.completions.create(model="gpt-4o", messages=messages, max_tokens=500)
        result = resp.choices[0].message.content.strip()
    except Exception:
        await update.message.reply_text("⚠️ Ошибка анализа, попробуйте снова.")
        return await start(update, context)

    if mode == "estimate":
        await update.message.reply_text(f"📊 Результат оценки:\n\n{result}")
        return await start(update, context)

    context.user_data["analysis_result"] = result
    kb = [[
        InlineKeyboardButton("✅ Сохранить", callback_data="save_yes"),
        InlineKeyboardButton("❌ Отмена", callback_data="save_no")
    ]]
    await update.message.reply_text(
        f"📊 Результат анализа ({meal_type}):\n\n{result}",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return HANDLE_SAVE

async def handle_save_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    mode = context.user_data.get("mode")
    chat_id = query.message.chat.id

    if "photo_path" in context.user_data:
        try:
            os.remove(context.user_data["photo_path"])
        except:
            pass

    if query.data == "save_no":
        await query.edit_message_text("❌ Данные не сохранены.")
        return await start(update, context)

    if mode == "add":
        analysis = context.user_data["analysis_result"]
        meal_type = context.user_data["meal_type"]

        calm = re.search(r'Калории[:\s]+(\d+)', analysis)
        prot = re.search(r'Белки[:\s]+([\d.]+)', analysis)
        fatm = re.search(r'Жиры[:\s]+([\d.]+)', analysis)
        carb = re.search(r'Углеводы[:\s]+([\d.]+)', analysis)

        if not (calm and prot and fatm and carb):
            await query.edit_message_text("⚠️ Не удалось извлечь калории или БЖУ. Попробуйте ещё раз.")
            return await start(update, context)

        payload = {
            "chat_id": chat_id,
            "meal_type": meal_type,
            "analysis": analysis,
            "calories": int(calm.group(1)),
            "protein": float(prot.group(1)),
            "fat": float(fatm.group(1)),
            "carbs": float(carb.group(1)),
        }

        async with aiohttp.ClientSession() as session:
            resp = await session.post(f"{BACKEND_URL}/api/log_meal", json=payload)
            if resp.status == 409:
                kb = [[
                    InlineKeyboardButton("🔄 Перезаписать", callback_data="overwrite_yes"),
                    InlineKeyboardButton("❌ Отмена", callback_data="overwrite_no")
                ]]
                await query.edit_message_text(
                    "⚠️ Приём пищи уже сохранён сегодня. Перезаписать?",
                    reply_markup=InlineKeyboardMarkup(kb)
                )
                return OVERWRITE_CONFIRM
            if resp.status == 200:
                await query.edit_message_text("✅ Приём пищи сохранён.")
            else:
                await query.edit_message_text("⚠️ Ошибка сохранения. Попробуйте позже.")
        return await start(update, context)

    await query.edit_message_text("✅ Действие выполнено.")
    return await start(update, context)

async def handle_overwrite(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "overwrite_no":
        await query.edit_message_text("❌ Операция отменена.")
        return await start(update, context)

    chat_id = query.message.chat.id
    analysis = context.user_data["analysis_result"]
    meal_type = context.user_data["meal_type"]

    calm = re.search(r'Калории[:\s]+(\d+)', analysis)
    prot = re.search(r'Белки[:\s]+([\d.]+)', analysis)
    fatm = re.search(r'Жиры[:\s]+([\d.]+)', analysis)
    carb = re.search(r'Углеводы[:\s]+([\d.]+)', analysis)

    async with aiohttp.ClientSession() as session:
        await session.delete(
            f"{BACKEND_URL}/api/log_meal",
            json={"chat_id": chat_id, "meal_type": meal_type}
        )
        await session.post(
            f"{BACKEND_URL}/api/log_meal",
            json={
                "chat_id": chat_id,
                "meal_type": meal_type,
                "analysis": analysis,
                "calories": int(calm.group(1)),
                "protein": float(prot.group(1)),
                "fat": float(fatm.group(1)),
                "carbs": float(carb.group(1)),
            }
        )
    await query.edit_message_text("🔄 Приём пищи перезаписан.")
    return await start(update, context)

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🚫 Операция отменена. Введите /start.")
    return ConversationHandler.END

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logging.error("Exception in handler", exc_info=context.error)
    if update.effective_message:
        await update.effective_message.reply_text("⚠️ Произошла ошибка. Попробуйте снова.")

# ——— REMINDER & SCHEDULER ——————————————————————————————————

async def remind_missing_meals(app):
    """Проверка и напоминание о незаполненных приёмах пищи в 21:00."""
    bot = app.bot
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{BACKEND_URL}/api/registered_chats") as resp:
            if resp.status != 200:
                return
            data = await resp.json()
            chat_ids = data.get("chat_ids", [])

        for chat_id in chat_ids:
            async with session.get(f"{BACKEND_URL}/api/meals/today/{chat_id}") as r2:
                if r2.status != 200:
                    continue
                meals = await r2.json()
                logged = {m['meal_type'] for m in meals}

            missing = [m for m in ("breakfast", "lunch", "dinner") if m not in logged]
            if not missing:
                continue

            names = {"breakfast": "Завтрак", "lunch": "Обед", "dinner": "Ужин"}
            text = "🔔 *Напоминание о приёмах пищи*\nВы не заполнили сегодня:\n"
            for m in missing:
                text += f"– {names[m]}\n"
            text += "\nПожалуйста, откройте /start и добавьте их."

            try:
                await bot.send_message(chat_id=int(chat_id), text=text, parse_mode="Markdown")
            except:
                pass

async def on_startup(app):
    scheduler = AsyncIOScheduler(timezone=TIMEZONE)
    scheduler.add_job(remind_missing_meals, trigger='cron', hour=21, minute=0, args=[app])
    scheduler.start()
    logging.info("🕘 Планировщик напоминаний запущен")
    # при необходимости сохранить: app.bot_data["scheduler"] = scheduler

# ——— NEW CHECK COMMAND HANDLER —————————————————————————————

async def check_meals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manually trigger the meal reminder check."""
    chat_id = update.message.chat.id
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{BACKEND_URL}/api/meals/today/{chat_id}") as r2:
            if r2.status != 200:
                await update.message.reply_text("⚠️ Не удалось получить данные о приёмах пищи.")
                return
            meals = await r2.json()
            logged = {m['meal_type'] for m in meals}

        missing = [m for m in ("breakfast", "lunch", "dinner") if m not in logged]
        if not missing:
            await update.message.reply_text("✅ Все приёмы пищи за сегодня уже добавлены.")
            return

        names = {"breakfast": "Завтрак", "lunch": "Обед", "dinner": "Ужин"}
        text = "🔔 *Напоминание о приёмах пищи*\nВы не заполнили сегодня:\n"
        for m in missing:
            text += f"– {names[m]}\n"
        text += "\nПожалуйста, откройте /start и добавьте их."

        try:
            await update.message.reply_text(text, parse_mode="Markdown")
        except Exception as e:
            logging.error(f"Error sending check reminder: {e}")
            await update.message.reply_text("⚠️ Ошибка при отправке напоминания.")

# ——— RUN BOT ——————————————————————————————————————————————

def run_bot():
    application = Application.builder() \
        .token(app_token) \
        .post_init(on_startup) \
        .build()

    # Добавляем обработчик команды /check для немедленного запуска проверки
    application.add_handler(CommandHandler("check", check_meals))

    # Создаем ConversationHandler
    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            ASK_CODE:          [MessageHandler(filters.TEXT & ~filters.COMMAND, verify_code)],
            SELECT_MENU:       [CallbackQueryHandler(handle_menu)],
            ASK_PHOTO:         [CallbackQueryHandler(ask_photo)],
            ASK_COMMENT:       [MessageHandler(filters.PHOTO, ask_comment)],
            ANALYZE_FOOD:      [MessageHandler(filters.TEXT & ~filters.COMMAND, analyze_food)],
            HANDLE_SAVE:       [CallbackQueryHandler(handle_save_confirmation)],
            OVERWRITE_CONFIRM: [CallbackQueryHandler(handle_overwrite)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True
    )

    # Добавляем ConversationHandler
    application.add_handler(conv)
    # Добавляем обработчик ошибок
    application.add_error_handler(error_handler)

    logging.info("✅ Бот запущен")
    application.run_polling()

if __name__ == "__main__":
    run_bot()
