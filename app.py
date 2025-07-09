import os
import datetime
import base64
import json
import requests
from flask import Flask, render_template, request, redirect, session, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
from werkzeug.utils import secure_filename
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "supersecret")

# Config DB
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///35healthclubs.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = 'uploads'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
db = SQLAlchemy(app)
bcrypt = Bcrypt(app)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

# ------------------ MODELS ------------------

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(128), nullable=False)
    name = db.Column(db.String(50), nullable=False)
    height = db.Column(db.Integer)
    weight = db.Column(db.Float)
    muscle_mass = db.Column(db.Float)
    fat_mass = db.Column(db.Float)
    metabolism = db.Column(db.Integer)
    bmi = db.Column(db.Float)
    analysis_comment = db.Column(db.Text)
    telegram_chat_id = db.Column(db.String(50), nullable=True)
    updated_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)

class Diet(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    date = db.Column(db.Date, default=datetime.date.today)
    breakfast = db.Column(db.Text)
    lunch = db.Column(db.Text)
    dinner = db.Column(db.Text)
    snack = db.Column(db.Text)
    total_kcal = db.Column(db.Integer)
    protein = db.Column(db.Float)
    fat = db.Column(db.Float)
    carbs = db.Column(db.Float)
    user = db.relationship('User', backref=db.backref('diets', lazy=True))

with app.app_context():
    db.create_all()

# ------------------ UTILS ------------------

@app.context_processor
def utility_processor():
    def get_bmi_category(bmi):
        if bmi is None:
            return ""
        if bmi < 18.5:
            return "Недостаточный вес"
        elif bmi < 25:
            return "Норма"
        elif bmi < 30:
            return "Избыточный вес"
        else:
            return "Ожирение"
    return dict(get_bmi_category=get_bmi_category)

# ------------------ ROUTES ------------------

@app.route('/')
def home():
    return redirect('/login')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        user = User.query.filter_by(email=email).first()
        if user and bcrypt.check_password_hash(user.password, password):
            session['user_id'] = user.id
            return redirect('/profile')
        return render_template('login.html', error="Неверный логин или пароль")
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        password = request.form['password']
        hashed_pw = bcrypt.generate_password_hash(password).decode('utf-8')
        user = User(name=name, email=email, password=hashed_pw)
        db.session.add(user)
        db.session.commit()
        return redirect('/login')
    return render_template('register.html')

@app.route('/profile')
def profile():
    user_id = session.get('user_id')
    if not user_id:
        return redirect('/login')

    user = db.session.get(User, user_id)
    diet = Diet.query.filter_by(user_id=user_id).order_by(Diet.date.desc()).first()

    return render_template(
        'profile.html',
        user=user,
        diet=diet,
        breakfast=json.loads(diet.breakfast) if diet else [],
        lunch=json.loads(diet.lunch) if diet else [],
        dinner=json.loads(diet.dinner) if diet else [],
        snack=json.loads(diet.snack) if diet else []
    )

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')

@app.route('/upload_analysis', methods=['POST'])
def upload_analysis():
    file = request.files.get('file')
    user_id = session.get('user_id')
    if not file or not user_id:
        return redirect('/profile')

    filename = secure_filename(file.filename)
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(filepath)

    with open(filepath, "rb") as image_file:
        base64_image = base64.b64encode(image_file.read()).decode("utf-8")

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {
                "role": "system",
                "content": (
                    "Ты фитнес-аналитик. На изображении — скрин с биоимпедансного анализа тела.\n"
                    "Извлеки параметры: height, weight, muscle_mass, fat_mass, metabolism.\n"
                    "Также рассчитай и верни bmi (индекс массы тела), и краткий анализ состояния тела.\n"
                    "Формат ответа строго JSON без текста. Пример:\n"
                    "```json {\n"
                    "  \"height\": 175,\n"
                    "  \"weight\": 70,\n"
                    "  \"muscle_mass\": 30,\n"
                    "  \"fat_mass\": 15,\n"
                    "  \"metabolism\": 1600,\n"
                    "  \"bmi\": 22.9,\n"
                    "  \"analysis\": \"Ваш ИМТ в пределах нормы. Уровень жира и мышечной массы хороший.\"\n"
                    "}```"
                )
            },
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}},
                    {"type": "text", "text": "Извлеки параметры из анализа тела."}
                ]
            }
        ],
        max_tokens=500
    )

    try:
        content = response.choices[0].message.content.strip()
        if '```json' in content:
            content = content.split('```json')[1].split('```')[0].strip()
        result = json.loads(content)
        session['temp_analysis'] = result
        return render_template('confirm_analysis.html', data=result)
    except Exception as e:
        return f"Ошибка анализа: {e}"

@app.route('/confirm_analysis', methods=['POST'])
def confirm_analysis():
    user_id = session.get('user_id')
    if not user_id or 'temp_analysis' not in session:
        return redirect('/profile')

    data = session.pop('temp_analysis')
    user = db.session.get(User, user_id)

    user.height = data.get('height')
    user.weight = data.get('weight')
    user.muscle_mass = data.get('muscle_mass')
    user.fat_mass = data.get('fat_mass')
    user.metabolism = data.get('metabolism')
    user.bmi = data.get('bmi')
    user.analysis_comment = data.get('analysis')
    user.updated_at = datetime.datetime.utcnow()
    db.session.commit()

    return redirect('/profile')
@app.route('/generate_diet')
def generate_diet():
    user_id = session.get('user_id')
    user = db.session.get(User, user_id)
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    goal = request.args.get("goal", "maintain")
    gender = request.args.get("gender", "male")
    preferences = request.args.get("preferences", "")

    prompt = f"""
    У пользователя следующие параметры:
    Рост: {user.height} см
    Вес: {user.weight} кг
    Мышечная масса: {user.muscle_mass} кг
    Жировая масса: {user.fat_mass} кг
    Метаболизм: {user.metabolism} ккал
    Цель: {goal}
    Пол: {gender}
    Предпочтения: {preferences}

    Составь рацион питания на 1 день: завтрак, обед, ужин, перекус. Для каждого укажи:
    - название блюда ("name")
    - граммовку ("grams")
    - калории ("kcal")
    - подробный пошаговый рецепт приготовления ("recipe")

    Верни JSON строго по формату:
    ```json
    {{
        "breakfast": [{{"name": "...", "grams": 0, "kcal": 0, "recipe": "..."}}],
        "lunch": [...],
        "dinner": [...],
        "snack": [...],
        "total_kcal": 0,
        "protein": 0,
        "fat": 0,
        "carbs": 0
    }}
    ```
    """

    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "Ты профессиональный диетолог. Отвечай строго в формате JSON."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=1500
        )

        content = response.choices[0].message.content.strip()
        if '```json' in content:
            content = content.split('```json')[1].split('```')[0].strip()
        diet_data = json.loads(content)

        diet = Diet(
            user_id=user_id,
            date=datetime.date.today(),
            breakfast=json.dumps(diet_data.get('breakfast', []), ensure_ascii=False),
            lunch=json.dumps(diet_data.get('lunch', []), ensure_ascii=False),
            dinner=json.dumps(diet_data.get('dinner', []), ensure_ascii=False),
            snack=json.dumps(diet_data.get('snack', []), ensure_ascii=False),
            total_kcal=diet_data.get('total_kcal'),
            protein=diet_data.get('protein'),
            fat=diet_data.get('fat'),
            carbs=diet_data.get('carbs')
        )
        db.session.add(diet)
        db.session.commit()

        # Отправка в Telegram
        # Отправка в Telegram
        if user.telegram_chat_id:
            message = f"🍽️ Ваша диета на сегодня:\n\n"

            def format_meal(title, items):
                lines = [f"🍱 {title}:"]
                for it in items:
                    lines.append(f"- {it['name']} ({it['grams']} г, {it['kcal']} ккал)")
                return "\n".join(lines)

            message += format_meal("Завтрак", diet_data.get("breakfast", [])) + "\n\n"
            message += format_meal("Обед", diet_data.get("lunch", [])) + "\n\n"
            message += format_meal("Ужин", diet_data.get("dinner", [])) + "\n\n"
            message += format_meal("Перекус", diet_data.get("snack", [])) + "\n\n"
            message += f"🔥 Калории: {diet_data['total_kcal']} ккал\n"
            message += f"🍗 Белки: {diet_data['protein']} г\n"
            message += f"🥑 Жиры: {diet_data['fat']} г\n"
            message += f"🥔 Углеводы: {diet_data['carbs']} г"

            try:
                requests.post(TELEGRAM_API_URL, data={
                    "chat_id": user.telegram_chat_id,
                    "text": message
                })
            except Exception as e:
                print(f"[Telegram Error] {e}")

        return jsonify({"redirect": "/diet"})

    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/diet')
def diet():
    user_id = session.get('user_id')
    diet = Diet.query.filter_by(user_id=user_id).order_by(Diet.date.desc()).first()
    if not diet:
        return "Диета ещё не сгенерирована."

    return render_template("confirm_diet.html", diet=diet,
                           breakfast=json.loads(diet.breakfast),
                           lunch=json.loads(diet.lunch),
                           dinner=json.loads(diet.dinner),
                           snack=json.loads(diet.snack))

@app.route('/reset_diet', methods=['POST'])
def reset_diet():
    user_id = session.get('user_id')
    if not user_id:
        return redirect('/login')

    diet = Diet.query.filter_by(user_id=user_id).order_by(Diet.date.desc()).first()
    if diet:
        db.session.delete(diet)
        db.session.commit()

    return redirect('/profile')

if __name__ == '__main__':
    app.run(debug=True)