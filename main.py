import asyncio
from threading import Thread
from flask import Flask
from telegram_bot import run_bot  # Импортируйте функцию для запуска бота

# Инициализация Flask-приложения
app = Flask(__name__)

@app.route('/')
def home():
    return "Hello from Flask!"

# Функция для запуска Flask
def run_flask():
    app.run(host='0.0.0.0', port=5000, debug=False)  # Отключаем debug

# Функция для запуска Telegram-бота
def run_telegram_bot():
    loop = asyncio.new_event_loop()  # Создаем новый цикл событий
    asyncio.set_event_loop(loop)     # Устанавливаем его для текущего потока
    loop.run_until_complete(run_bot())  # Запуск бота

if __name__ == "__main__":
    # Запуск Flask-приложения в отдельном потоке
    flask_thread = Thread(target=run_flask)
    flask_thread.start()

    # Запуск Telegram-бота в основном потоке через asyncio
    telegram_thread = Thread(target=run_telegram_bot)
    telegram_thread.start()

    # Ожидаем завершения потоков (если нужно)
    flask_thread.join()
    telegram_thread.join()
