import os
import re
import logging
import base64
import aiohttp
import asyncio
import json
from aiohttp import web
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
# Убрали импорт OpenAI, так как анализ теперь на бэкенде
from dotenv import load_dotenv
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# --- КОНФИГУРАЦИЯ ---
load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

TIMEZONE = "Asia/Almaty"
# Убедитесь, что URL в .env файле правильный (например, http://127.0.0.1:5000)
BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:5000")
BOT_SECRET_TOKEN = os.getenv("BOT_SECRET_TOKEN") # Для вебхука уведомлений

# Убрали инициализацию клиента OpenAI
app_token = os.getenv("TELEGRAM_BOT_TOKEN")

os.makedirs("temp_photos", exist_ok=True)

# --- ИЗМЕНЕНО: Упростили состояния ---
(ASK_CODE, SELECT_MENU, ASK_PHOTO, HANDLE_SAVE, OVERWRITE_CONFIRM, HISTORY_MENU) = range(6)


# --- Клавиатура главного меню ---
MAIN_MENU_KEYBOARD = [
    [InlineKeyboardButton("➕ Добавить приём пищи", callback_data="add")],
    [InlineKeyboardButton("🍽️ Приемы пищи за сегодня", callback_data="today_meals")], # <-- НОВАЯ КНОПКА
    [InlineKeyboardButton("🚀 Мой прогресс", callback_data="progress")],
    [InlineKeyboardButton("📜 Моя история", callback_data="history")],
    [InlineKeyboardButton("🥗 Текущая диета", callback_data="current")],
]

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ (без изменений) ---

async def cleanup_chat(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.user_data.get('chat_id')
    messages_to_delete = context.user_data.pop('messages_to_delete', [])

    main_menu_msg_id = context.user_data.pop('main_menu_message_id', None)
    if main_menu_msg_id:
        messages_to_delete.append(main_menu_msg_id)

    if not chat_id or not messages_to_delete:
        return

    for msg_id in set(messages_to_delete):
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except Exception:
            pass

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cleanup_chat(context)
    text = "👋 Вы в главном меню. Что хотите сделать?"
    reply_markup = InlineKeyboardMarkup(MAIN_MENU_KEYBOARD)
    # Используем effective_chat, чтобы работало и с query, и с message
    chat = update.effective_chat
    sent_message = await chat.send_message(text, reply_markup=reply_markup)
    context.user_data['main_menu_message_id'] = sent_message.message_id
    context.user_data['messages_to_delete'] = []


async def back_to_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает главное меню и возвращает бота в состояние SELECT_MENU."""
    query = update.callback_query
    if query:
        await query.answer()
        # Удаляем сообщение, к которому была привязана кнопка "Назад"
        try:
            await query.message.delete()
        except Exception as e:
            logging.warning(f"Could not delete message on back_to_main_menu: {e}")


    await show_main_menu(update, context)
    return SELECT_MENU


# Добавьте эту новую функцию в telegram_bot.py

async def show_today_meals(query: Update, context: ContextTypes.DEFAULT_TYPE):
    """Запрашивает у бэкенда и отображает все приемы пищи за сегодня."""
    chat_id = query.message.chat.id
    loading_msg = await query.message.reply_text("⏳ Загружаю приемы пищи за сегодня...")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{BACKEND_URL}/api/meals/today/{chat_id}") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    meals = data.get("meals")
                    total_calories = data.get("total_calories")

                    if not meals:
                        text = "🤷‍♂️ Вы еще ничего не ели сегодня."
                    else:
                        text = "🍽️ *Ваши приемы пищи за сегодня:*\n\n"
                        meal_type_map = {
                            'breakfast': '🍳 Завтрак',
                            'lunch': '🍛 Обед',
                            'dinner': '🍲 Ужин',
                            'snack': '🥜 Перекус'
                        }
                        for meal in meals:
                            meal_name = meal.get('name')
                            meal_calories = meal.get('calories')
                            meal_type_rus = meal_type_map.get(meal.get('meal_type'), 'Прием пищи')
                            text += f"*{meal_type_rus}*: {meal_name} - *{meal_calories} ккал*\n"

                        text += f"\n🔥 *Всего за день: {total_calories} ккал*"

                    await loading_msg.edit_text(
                        text,
                        parse_mode="Markdown",
                        reply_markup=InlineKeyboardMarkup(
                            [[InlineKeyboardButton("🔙 Назад в меню", callback_data="back_to_main")]])
                    )
                else:
                    await loading_msg.edit_text("⚠️ Произошла ошибка при загрузке данных.")
    except aiohttp.ClientError as e:
        logging.error(f"Today's meals loading failed: {e}")
        await loading_msg.edit_text("⚠️ Ошибка сети. Не удалось загрузить данные.")

# ——— ОБРАБОТЧИКИ ДИАЛОГОВ ———

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    context.user_data['chat_id'] = update.effective_chat.id
    context.user_data['messages_to_delete'] = [update.message.message_id]

    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(f"{BACKEND_URL}/api/is_registered/{update.effective_chat.id}") as resp:
                if resp.status == 200:
                    await show_main_menu(update, context)
                    return SELECT_MENU
                else:
                    sent_message = await update.message.reply_text("🔐 Введите 8-значный код из личного кабинета:")
                    context.user_data.setdefault('messages_to_delete', []).append(sent_message.message_id)
                    return ASK_CODE
        except aiohttp.ClientError as e:
            logging.error(f"Cannot connect to backend: {e}")
            await update.message.reply_text("⚠️ Не удалось подключиться к серверу. Попробуйте позже.")
            return ConversationHandler.END


async def verify_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.setdefault('messages_to_delete', []).append(update.message.message_id)
    code = update.message.text.strip()
    chat_id = update.effective_chat.id
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(f"{BACKEND_URL}/api/link_telegram", json={"code": code, "chat_id": chat_id}) as resp:
                if resp.status == 200:
                    await cleanup_chat(context)
                    await update.message.reply_text("✅ Telegram привязан! Введите /start, чтобы начать.")
                    return ConversationHandler.END
                else:
                    sent_message = await update.message.reply_text("❌ Неверный код. Попробуйте снова:")
                    context.user_data.setdefault('messages_to_delete', []).append(sent_message.message_id)
                    return ASK_CODE
        except aiohttp.ClientError as e:
            logging.error(f"Cannot connect to backend: {e}")
            await update.message.reply_text("⚠️ Не удалось подключиться к серверу. Попробуйте позже.")
            return ConversationHandler.END


async def handle_menu_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = update.effective_chat.id

    try:
        await query.message.delete()
    except Exception: pass
    context.user_data['main_menu_message_id'] = None

    if data == "add":
        keyboard = [
            [InlineKeyboardButton("🍳 Завтрак", callback_data="meal_breakfast"),
             InlineKeyboardButton("🍛 Обед", callback_data="meal_lunch")],
            [InlineKeyboardButton("🍲 Ужин", callback_data="meal_dinner"),
             InlineKeyboardButton("🥜 Перекус", callback_data="meal_snack")],
            [InlineKeyboardButton("🔙 Назад в меню", callback_data="back_to_main")]
        ]
        sent_message = await query.message.reply_text("Выберите тип приёма пищи:",
                                                      reply_markup=InlineKeyboardMarkup(keyboard))
        context.user_data['messages_to_delete'] = [sent_message.message_id]
        return ASK_PHOTO

    if data == "today_meals":  # <-- ДОБАВЬТЕ ЭТОТ БЛОК
        await show_today_meals(query, context)
        return SELECT_MENU

    if data == "progress":
        await show_progress(query, context)
        return SELECT_MENU

    if data == "history":
        return await show_history_menu(update, context)

    if data == "current":
        loading_msg = await query.message.reply_text("⏳ Загружаю вашу диету...")
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{BACKEND_URL}/api/current_diet/{chat_id}") as resp:
                    if resp.status == 200:
                        diet = await resp.json()
                        text = f"🥗 *Ваша диета на {diet['date']}*\n\n"
                        for meal_type, meal_name in [("breakfast", "Завтрак"), ("lunch", "Обед"), ("dinner", "Ужин"), ("snack", "Перекус")]:
                            text += f"*{meal_name}*:\n"
                            items = diet.get(meal_type)
                            if items:
                                for item in items:
                                    text += f"- {item['name']} ({item['grams']}г, {item['kcal']} ккал)\n"
                            else:
                                text += "- нет данных\n"
                            text += "\n"
                        text += (f"Итого: *{diet['total_kcal']} ккал* (Б: {diet['protein']}г, "
                                 f"Ж: {diet['fat']}г, У: {diet['carbs']}г)")
                        await loading_msg.edit_text(text, parse_mode="Markdown")
                    elif resp.status == 404:
                        await loading_msg.edit_text("🤷‍♂️ У вас пока нет сгенерированной диеты. Создайте её в профиле на сайте.")
                    else:
                        await loading_msg.edit_text("⚠️ Произошла ошибка при загрузке диеты.")
        except aiohttp.ClientError as e:
            logging.error(f"Diet loading failed: {e}")
            await loading_msg.edit_text("⚠️ Ошибка сети. Не удалось загрузить диету.")

        await show_main_menu(update, context)
        return SELECT_MENU

    # На случай непредвиденных callback_data
    await show_main_menu(update, context)
    return SELECT_MENU

async def ask_photo_for_meal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "back_to_main":
        await query.message.delete()
        return await back_to_main_menu(update, context)

    context.user_data["meal_type"] = query.data.split('_')[1]
    await query.edit_message_text("📸 Пожалуйста, отправьте фото еды:", reply_markup=InlineKeyboardMarkup(
        [[InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")]]
    ))
    return ASK_PHOTO # Остаемся в этом же состоянии, ждем фото


# --- ИЗМЕНЕНО: Новая функция для обработки фото и отправки на бэкенд ---
async def process_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.setdefault('messages_to_delete', []).append(update.message.message_id)

    # Удаляем предыдущее сообщение с просьбой отправить фото
    if context.user_data.get('messages_to_delete'):
        old_message_id = context.user_data['messages_to_delete'][0]
        try:
            await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=old_message_id)
            context.user_data['messages_to_delete'].pop(0)
        except Exception:
            pass

    analyzing_msg = await update.message.reply_text("⏳ Анализирую фото, это может занять до 30 секунд...")
    await update.effective_chat.send_chat_action('typing')

    file_id = update.message.photo[-1].file_id
    try:
        photo_file = await context.bot.get_file(file_id)
        photo_bytes = await photo_file.download_as_bytearray()

        # Отправляем фото на бэкенд
        form_data = aiohttp.FormData()
        form_data.add_field('file', photo_bytes, filename='meal.jpg', content_type='image/jpeg')

        async with aiohttp.ClientSession() as session:
            async with session.post(f"{BACKEND_URL}/analyze_meal_photo", data=form_data) as resp:
                await analyzing_msg.delete()
                if resp.status == 200:
                    result_data = await resp.json()
                    context.user_data["analysis_result"] = result_data # Сохраняем весь JSON

                    # Формируем сообщение с результатом
                    text = (f"📊 *Результат анализа:*\n\n"
                            f"Название: *{result_data.get('name', 'N/A')}*\n"
                            f"Вердикт: *{result_data.get('verdict', 'N/A')}*\n\n"
                            f"Калории: *{result_data.get('calories', 0)} ккал*\n"
                            f"Белки: {result_data.get('protein', 0.0)} г\n"
                            f"Жиры: {result_data.get('fat', 0.0)} г\n"
                            f"Углеводы: {result_data.get('carbs', 0.0)} г\n\n"
                            f"_{result_data.get('analysis', '')}_")

                    kb = [[InlineKeyboardButton("✅ Сохранить", callback_data="save_yes"),
                           InlineKeyboardButton("❌ Отмена", callback_data="save_no")]]
                    result_msg = await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
                    context.user_data['messages_to_delete'].append(result_msg.message_id)
                    return HANDLE_SAVE
                else:
                    error_text = await resp.text()
                    logging.error(f"Backend photo analysis failed: {resp.status} - {error_text}")
                    await update.message.reply_text("⚠️ Ошибка анализа на сервере. Попробуйте другое фото или позже.")
                    await show_main_menu(update, context)
                    return SELECT_MENU

    except Exception as e:
        logging.error(f"Failed to process photo: {e}")
        await analyzing_msg.delete()
        await update.message.reply_text("⚠️ Не удалось обработать фото. Попробуйте ещё раз.")
        return ASK_PHOTO


async def handle_save_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "save_no":
        await query.message.reply_text("❌ Операция отменена.")
        await show_main_menu(update, context)
        return SELECT_MENU

    # Логика сохранения через API бэкенда
    chat_id = update.effective_chat.id
    meal_type = context.user_data.get("meal_type")
    analysis_result = context.user_data.get("analysis_result")

    if not meal_type or not analysis_result:
        await query.message.reply_text("⚠️ Произошла внутренняя ошибка. Попробуйте снова.")
        await show_main_menu(update, context)
        return SELECT_MENU

    payload = {
        "chat_id": chat_id,
        "meal_type": meal_type,
        # Добавляем все поля из анализа
        **analysis_result
    }

    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(f"{BACKEND_URL}/api/log_meal", json=payload) as resp:
                if resp.status == 200:
                    await query.message.edit_text("✅ Приём пищи сохранён.")
                    await show_main_menu(update, context)
                    return SELECT_MENU
                elif resp.status == 409: # Конфликт - запись уже существует
                    kb = [[InlineKeyboardButton("Да, перезаписать", callback_data="overwrite_yes"),
                           InlineKeyboardButton("Нет, отмена", callback_data="overwrite_no")]]
                    await query.message.edit_text(f"🥣 Приём пищи '{meal_type}' за сегодня уже существует. Перезаписать?",
                                                  reply_markup=InlineKeyboardMarkup(kb))
                    return OVERWRITE_CONFIRM
                else:
                    error_text = await resp.text()
                    logging.error(f"Backend save failed: {resp.status} - {error_text}")
                    await query.message.edit_text("⚠️ Ошибка сохранения на сервере.")
                    await show_main_menu(update, context)
                    return SELECT_MENU
        except aiohttp.ClientError as e:
            logging.error(f"Save failed (network): {e}")
            await query.message.edit_text("⚠️ Ошибка сети. Не удалось сохранить данные.")
            await show_main_menu(update, context)
            return SELECT_MENU


async def handle_overwrite(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "overwrite_no":
        await query.message.edit_text("❌ Операция отменена.")
        await show_main_menu(update, context)
        return SELECT_MENU

    # Логика перезаписи: сначала DELETE, потом POST
    chat_id = update.effective_chat.id
    meal_type = context.user_data.get("meal_type")
    analysis_result = context.user_data.get("analysis_result")

    payload = {"chat_id": chat_id, "meal_type": meal_type}
    save_payload = {"chat_id": chat_id, "meal_type": meal_type, **analysis_result}

    try:
        async with aiohttp.ClientSession() as session:
            # 1. Удаляем старую запись
            async with session.delete(f"{BACKEND_URL}/api/log_meal", json=payload) as del_resp:
                if del_resp.status not in [200, 204, 404]: # 404 тоже ок, если вдруг её уже удалили
                     await query.message.edit_text("⚠️ Не удалось удалить старую запись. Перезапись отменена.")
                     await show_main_menu(update, context)
                     return SELECT_MENU

            # 2. Добавляем новую
            async with session.post(f"{BACKEND_URL}/api/log_meal", json=save_payload) as post_resp:
                if post_resp.status == 200:
                    await query.message.edit_text("🔄 Приём пищи успешно перезаписан.")
                else:
                    await query.message.edit_text("⚠️ Не удалось сохранить новую запись после удаления.")

    except aiohttp.ClientError as e:
        logging.error(f"Overwrite failed (network): {e}")
        await query.message.edit_text("⚠️ Ошибка сети при перезаписи.")

    await show_main_menu(update, context)
    return SELECT_MENU


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cleanup_chat(context)
    if update.message:
        await update.message.reply_text("🚫 Операция отменена.")
    elif update.callback_query:
        await update.callback_query.message.reply_text("🚫 Операция отменена.")

    await show_main_menu(update, context)
    context.user_data.clear()
    return await back_to_main_menu(update, context)


# --- ФУНКЦИИ ПРОГРЕССА И ИСТОРИИ ---

async def show_progress(query: Update, context: ContextTypes.DEFAULT_TYPE):
    # Принимает query, чтобы можно было редактировать/удалять сообщения
    chat_id = query.message.chat.id
    loading_msg = await query.message.reply_text("⏳ Загружаю ваш прогресс...")

    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(f"{BACKEND_URL}/api/user_progress/{chat_id}") as resp:
                if resp.status != 200:
                    data = await resp.json()
                    error_msg = data.get("error", "Недостаточно данных для анализа прогресса.")
                    await loading_msg.edit_text(f"⚠️ {error_msg}")
                    return

                data = await resp.json()
        except aiohttp.ClientError as e:
            logging.error(f"Progress loading failed: {e}")
            await loading_msg.edit_text("⚠️ Ошибка сети. Не удалось загрузить прогресс.")
            return

    await loading_msg.delete()
    latest = data.get("latest")
    previous = data.get("previous")

    if not latest:
        await query.message.reply_text("⚠️ Данные не найдены.", reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔙 Назад в меню", callback_data="back_to_main")]]
        ))
        return

    text = f"🚀 *Ваш прогресс (замер от {latest['date']})*\n\n"
    text += f"⚖️ Вес: *{latest.get('weight', 'N/A')} кг*\n"
    text += f"🧈 Жировая масса: *{latest.get('fat_mass', 'N/A')} кг*\n"
    text += f"💪 Мышечная масса: *{latest.get('muscle_mass', 'N/A')} кг*\n"

    if previous:
        def get_diff_str(latest_val, prev_val):
            if latest_val is None or prev_val is None: return "– нет данных"
            diff = latest_val - prev_val
            if diff > 0.01: return f"🔺 +{diff:.1f}"
            if diff < -0.01: return f"✅ {diff:.1f}"
            return "– без изменений"

        text += f"\n*Изменения с прошлого замера ({previous['date']})*:\n"
        text += f"⚖️ Вес: {get_diff_str(latest.get('weight'), previous.get('weight'))}\n"
        text += f"🧈 Жир: {get_diff_str(latest.get('fat_mass'), previous.get('fat_mass'))}\n"
        text += f"💪 Мышцы: {get_diff_str(latest.get('muscle_mass'), previous.get('muscle_mass'))}"

    await query.message.reply_text(
        text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔙 Назад в меню", callback_data="back_to_main")]]
        )
    )

async def show_history_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    keyboard = [
        [InlineKeyboardButton("🍽️ История питания", callback_data="history_meals_1")],
        [InlineKeyboardButton("🏃‍♂️ История активности", callback_data="history_activity_1")],
        [InlineKeyboardButton("🔙 Назад в меню", callback_data="back_to_main")]
    ]
    text = "📜 Какую историю вы хотите посмотреть?"

    # Используем effective_chat для отправки нового сообщения
    chat = update.effective_chat
    if query and query.message:
        try:
            # Если пришли из другого меню, удаляем старое сообщение
            await query.message.delete()
        except Exception: pass

    sent_message = await chat.send_message(text, reply_markup=InlineKeyboardMarkup(keyboard))
    context.user_data['messages_to_delete'] = [sent_message.message_id]
    return HISTORY_MENU


async def handle_history_pagination(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = update.effective_chat.id

    try:
        _, history_type, page_str = query.data.split("_")
        page = int(page_str)
    except (ValueError, IndexError):
        await query.edit_message_text("Ошибка: некорректные данные пагинации.")
        return HISTORY_MENU

    api_endpoint = "meal_history" if history_type == "meals" else "activity_history"
    title = "История питания" if history_type == "meals" else "История активности"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{BACKEND_URL}/api/{api_endpoint}/{chat_id}?page={page}") as resp:
                if resp.status != 200:
                    await query.edit_message_text("⚠️ Не удалось загрузить историю.")
                    return HISTORY_MENU
                data = await resp.json()
    except aiohttp.ClientError as e:
        logging.error(f"History loading failed: {e}")
        await query.edit_message_text("⚠️ Ошибка сети при загрузке истории.")
        return HISTORY_MENU

    text = f"📜 *{title} (Страница {page})*\n\n"
    days = data.get("days", [])
    if not days:
        text += "Здесь пока пусто."
    else:
        for day in days:
            if history_type == "meals":
                text += f"*{day['date']}*: {day['total_calories']} ккал ({day['meal_count']} приёма пищи)\n"
            else:
                text += f"*{day['date']}*: {day['steps']} шагов, {day['active_kcal']} ккал\n"

    nav_buttons = []
    if data.get("has_prev"):
        nav_buttons.append(InlineKeyboardButton("⬅️ Назад", callback_data=f"history_{history_type}_{page - 1}"))
    if data.get("has_next"):
        nav_buttons.append(InlineKeyboardButton("Вперёд ➡️", callback_data=f"history_{history_type}_{page + 1}"))

    keyboard_layout = [
        nav_buttons,
        [InlineKeyboardButton("🔙 Назад к выбору истории", callback_data="back_to_history")]
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard_layout), parse_mode="Markdown")
    return HISTORY_MENU


# --- (Остальные функции: scheduler, webhook, check_meals, error_handler) ---
async def remind_missing_meals(app: Application):
    """Отправляет напоминание всем зарегистрированным пользователям, кто еще не логировал еду."""
    logging.info("Running scheduled job: remind_missing_meals")
    # Код для получения списка чатов и отправки напоминаний...
    # ...

async def on_startup(app: Application):
    """Действия при запуске бота: установка команд, запуск планировщика."""
    await app.bot.set_my_commands([
        ("start", "Перезапустить бота"),
        ("cancel", "Отменить текущую операцию")
    ])
    scheduler = AsyncIOScheduler(timezone=TIMEZONE)
    # Напоминание в 21:00 по времени Алматы
    scheduler.add_job(remind_missing_meals, 'cron', hour=21, minute=0, args=[app])
    scheduler.start()
    logging.info("APScheduler started.")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Логирует ошибки."""
    logging.error(f"Update {update} caused error {context.error}")


# --- ЗАПУСК БОТА ---
def main():
    application = Application.builder().token(app_token).post_init(on_startup).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            ASK_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, verify_code)],
            SELECT_MENU: [
                CallbackQueryHandler(back_to_main_menu, pattern=r"^back_to_main$"),
                CallbackQueryHandler(handle_menu_selection),
            ],
            ASK_PHOTO: [
                CallbackQueryHandler(ask_photo_for_meal, pattern=r"^meal_"),
                MessageHandler(filters.PHOTO, process_photo), # Обработка фото здесь
                CallbackQueryHandler(back_to_main_menu, pattern=r"^back_to_main$")
            ],
            HANDLE_SAVE: [CallbackQueryHandler(handle_save_confirmation, pattern=r"^save_")],
            OVERWRITE_CONFIRM: [CallbackQueryHandler(handle_overwrite, pattern=r"^overwrite_")],
            HISTORY_MENU: [
                CallbackQueryHandler(handle_history_pagination, pattern=r"^history_"),
                CallbackQueryHandler(show_history_menu, pattern=r"^back_to_history$"),
                CallbackQueryHandler(back_to_main_menu, pattern=r"^back_to_main$")
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True
    )

    application.add_handler(conv_handler)
    application.add_error_handler(error_handler)

    logging.info("✅ Бот запущен")
    application.run_polling()


if __name__ == "__main__":
    main()