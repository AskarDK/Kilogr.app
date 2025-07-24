import os
from datetime import datetime, date, timedelta
import base64
import json
import requests
from flask import Flask, render_template, request, redirect, session, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
from werkzeug.utils import secure_filename
from openai import OpenAI
from dotenv import load_dotenv
import random
import string
from sqlalchemy import UniqueConstraint
from sqlalchemy.exc import IntegrityError
import re
from sqlalchemy import func
from telegram_bot import run_bot  # Импортируем функцию для запуска бота
import threading




load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "supersecret")
app.jinja_env.globals.update(getattr=getattr)


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
    date_of_birth = db.Column(db.Date)

    height = db.Column(db.Integer)
    weight = db.Column(db.Float)
    muscle_mass = db.Column(db.Float)
    muscle_percentage = db.Column(db.Float)
    body_water = db.Column(db.Float)
    protein_percentage = db.Column(db.Float)
    bone_mineral_percentage = db.Column(db.Float)
    skeletal_muscle_mass = db.Column(db.Float)
    visceral_fat_rating = db.Column(db.Float)
    metabolism = db.Column(db.Integer)
    waist_hip_ratio = db.Column(db.Float)
    body_age = db.Column(db.Integer)
    fat_mass = db.Column(db.Float)
    bmi = db.Column(db.Float)
    fat_free_body_weight = db.Column(db.Float)

    analysis_comment = db.Column(db.Text)
    telegram_chat_id = db.Column(db.String(50), nullable=True)
    telegram_code = db.Column(db.String(10), nullable=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow)

class MealLog(db.Model):
    __tablename__ = 'meal_logs'
    id          = db.Column(db.Integer,   primary_key=True)
    user_id     = db.Column(db.Integer,   db.ForeignKey('user.id'), nullable=False)
    date        = db.Column(db.Date,      nullable=False, default=date.today)
    meal_type   = db.Column(db.String(20),nullable=False)   # 'breakfast','lunch','dinner','snack'
    # новые поля для расчётов
    calories    = db.Column(db.Integer,   nullable=False)
    protein     = db.Column(db.Float,     nullable=False)
    fat         = db.Column(db.Float,     nullable=False)
    carbs       = db.Column(db.Float,     nullable=False)
    # оригинальный текст (на всякий случай)
    analysis    = db.Column(db.Text,      nullable=False)
    created_at  = db.Column(db.DateTime,  default=datetime.utcnow)

    user = db.relationship('User', backref=db.backref('meals', lazy=True))
    __table_args__ = (
        UniqueConstraint('user_id','date','meal_type', name='uq_user_date_meal'),
    )

class Activity(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    date = db.Column(db.Date, default=date.today)
    steps = db.Column(db.Integer)
    active_kcal = db.Column(db.Integer)
    resting_kcal = db.Column(db.Integer)
    distance_km = db.Column(db.Float)
    heart_rate_avg = db.Column(db.Integer)
    source = db.Column(db.String(50))  # например: "apple_watch", "mi_band", "manual"

    user = db.relationship("User", backref=db.backref("activities", lazy=True))


class Diet(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    date = db.Column(db.Date, default=date.today)
    breakfast = db.Column(db.Text)
    lunch = db.Column(db.Text)
    dinner = db.Column(db.Text)
    snack = db.Column(db.Text)
    total_kcal = db.Column(db.Integer)
    protein = db.Column(db.Float)
    fat = db.Column(db.Float)
    carbs = db.Column(db.Float)
    user = db.relationship('User', backref=db.backref('diets', lazy=True))

class BodyAnalysis(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    height = db.Column(db.Integer)
    weight = db.Column(db.Float)
    muscle_mass = db.Column(db.Float)
    muscle_percentage = db.Column(db.Float)
    body_water = db.Column(db.Float)
    protein_percentage = db.Column(db.Float)
    bone_mineral_percentage = db.Column(db.Float)
    skeletal_muscle_mass = db.Column(db.Float)
    visceral_fat_rating = db.Column(db.Float)
    metabolism = db.Column(db.Integer)
    waist_hip_ratio = db.Column(db.Float)
    body_age = db.Column(db.Integer)
    fat_mass = db.Column(db.Float)
    bmi = db.Column(db.Float)
    fat_free_body_weight = db.Column(db.Float)

    user = db.relationship('User', backref=db.backref('analyses', lazy=True))

with app.app_context():
    db.create_all()


def calculate_age(born):
    today = date.today()
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))
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
    errors = []

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '').strip()
        date_str = request.form.get('date_of_birth', '').strip()

        # Проверка обязательных полей
        if not name:
            errors.append("Имя обязательно.")
        if not email:
            errors.append("Email обязателен.")
        if not password or len(password) < 6:
            errors.append("Пароль обязателен и должен содержать минимум 6 символов.")

        # Проверка уникальности email
        if User.query.filter_by(email=email).first():
            errors.append("Этот email уже зарегистрирован.")

        # Проверка даты рождения
        date_of_birth = None
        if date_str:
            try:
                date_of_birth = datetime.strptime(date_str, "%Y-%m-%d")
                if date_of_birth > datetime.now():
                    errors.append("Дата рождения не может быть в будущем.")
            except ValueError:
                errors.append("Некорректный формат даты рождения.")
        else:
            errors.append("Дата рождения обязательна.")

        if errors:
            return render_template('register.html', errors=errors)

        # Хеширование пароля и сохранение пользователя
        hashed_pw = bcrypt.generate_password_hash(password).decode('utf-8')
        user = User(
            name=name,
            email=email,
            password=hashed_pw,
            date_of_birth=date_of_birth
        )
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
    age = calculate_age(user.date_of_birth) if user.date_of_birth else None
    diet = Diet.query.filter_by(user_id=user_id).order_by(Diet.date.desc()).first()
    today_activity = Activity.query.filter_by(user_id=user_id, date=date.today()).first()

    # берём два последних замера из BodyAnalysis
    analyses = BodyAnalysis.query\
        .filter_by(user_id=user_id)\
        .order_by(BodyAnalysis.timestamp.desc())\
        .limit(2).all()
    latest = analyses[0] if len(analyses) > 0 else None
    previous = analyses[1] if len(analyses) > 1 else None

    return render_template(
        'profile.html',
        user=user,
        age=age,
        diet=diet,
        today_activity=today_activity,
        latest_analysis=latest,
        previous_analysis=previous,
        breakfast=json.loads(diet.breakfast) if diet and diet.breakfast else [],
        lunch=json.loads(diet.lunch) if diet and diet.lunch else [],
        dinner=json.loads(diet.dinner) if diet and diet.dinner else [],
        snack=json.loads(diet.snack) if diet and diet.snack else []
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
                    "Ты — фитнес-аналитик. На изображении — фото анализа тела (bioimpedance).\n"
                    "Извлеки следующие параметры, если они присутствуют на изображении:\n"
                    "- height (рост в см)\n"
                    "- weight (вес в кг)\n"
                    "- muscle_mass (в кг)\n"
                    "- muscle_percentage (в %)\n"
                    "- body_water (в %)\n"
                    "- protein_percentage (в %)\n"
                    "- bone_mineral_percentage (в %)\n"
                    "- skeletal_muscle_mass (в кг)\n"
                    "- visceral_fat_rating (число)\n"
                    "- metabolism (basal metabolic rate в ккал)\n"
                    "- waist_hip_ratio (коэффициент типа 0.87)\n"
                    "- body_age (в годах)\n"
                    "- fat_mass (в кг)\n"
                    "- bmi (рассчитанный индекс массы тела)\n"
                    "- fat_free_body_weight (в кг)\n\n"
                    "Если не удаётся найти один или более из этих параметров, верни JSON в виде:\n"
                    "```json\n"
                    "{\n"
                    "  \"error\": \"Недостаточно данных\",\n"
                    "  \"missing\": [\"muscle_percentage\", \"protein_percentage\"]\n"
                    "}\n"
                    "```\n\n"
                    "Если все параметры найдены — верни такой JSON:\n"
                    "```json\n"
                    "{\n"
                    "  \"height\": 175,\n"
                    "  \"weight\": 70,\n"
                    "  \"muscle_mass\": 30,\n"
                    "  \"muscle_percentage\": 42,\n"
                    "  \"body_water\": 55,\n"
                    "  \"protein_percentage\": 18,\n"
                    "  \"bone_mineral_percentage\": 4,\n"
                    "  \"skeletal_muscle_mass\": 27,\n"
                    "  \"visceral_fat_rating\": 6,\n"
                    "  \"metabolism\": 1600,\n"
                    "  \"waist_hip_ratio\": 0.85,\n"
                    "  \"body_age\": 25,\n"
                    "  \"fat_mass\": 14,\n"
                    "  \"bmi\": 22.9,\n"
                    "  \"fat_free_body_weight\": 56\n"
                    "}"
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
        max_tokens=1000
    )

    try:
        content = response.choices[0].message.content.strip()
        if '```json' in content:
            content = content.split('```json')[1].split('```')[0].strip()
        result = json.loads(content)

        if "error" in result:
            missing = ', '.join(result.get("missing", []))
            return render_template('confirm_analysis.html', data=None, error=f"Недостаточно данных: {missing}")

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

    # 1) Сохраняем предыдущие в историю, если уже были данные
    if user.height is not None:
        history = BodyAnalysis(
            user_id=user.id,
            height=user.height,
            weight=user.weight,
            muscle_mass=user.muscle_mass,
            muscle_percentage=user.muscle_percentage,
            body_water=user.body_water,
            protein_percentage=user.protein_percentage,
            bone_mineral_percentage=user.bone_mineral_percentage,
            skeletal_muscle_mass=user.skeletal_muscle_mass,
            visceral_fat_rating=user.visceral_fat_rating,
            metabolism=user.metabolism,
            waist_hip_ratio=user.waist_hip_ratio,
            body_age=user.body_age,
            fat_mass=user.fat_mass,
            bmi=user.bmi,
            fat_free_body_weight=user.fat_free_body_weight
        )
        db.session.add(history)

    # 2) Обновляем user полями из data
    for f in [
        "height", "weight", "muscle_mass", "muscle_percentage", "body_water",
        "protein_percentage", "bone_mineral_percentage", "skeletal_muscle_mass",
        "visceral_fat_rating", "metabolism", "waist_hip_ratio", "body_age",
        "fat_mass", "bmi", "fat_free_body_weight"
    ]:
        if f in data:
            setattr(user, f, data[f])

    user.analysis_comment = data.get("analysis")
    user.updated_at = datetime.utcnow()
    db.session.commit()

    # 3) Сохраняем новый замер сразу после обновления user
    new_analysis = BodyAnalysis(
        user_id=user.id,
        height=user.height,
        weight=user.weight,
        muscle_mass=user.muscle_mass,
        muscle_percentage=user.muscle_percentage,
        body_water=user.body_water,
        protein_percentage=user.protein_percentage,
        bone_mineral_percentage=user.bone_mineral_percentage,
        skeletal_muscle_mass=user.skeletal_muscle_mass,
        visceral_fat_rating=user.visceral_fat_rating,
        metabolism=user.metabolism,
        waist_hip_ratio=user.waist_hip_ratio,
        body_age=user.body_age,
        fat_mass=user.fat_mass,
        bmi=user.bmi,
        fat_free_body_weight=user.fat_free_body_weight
    )
    db.session.add(new_analysis)
    db.session.commit()

    return redirect('/profile')


@app.route('/generate_telegram_code')
def generate_telegram_code():
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401

    code = ''.join(random.choices(string.digits, k=8))
    user = db.session.get(User, user_id)
    user.telegram_code = code
    db.session.commit()

    return jsonify({'code': code})

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
            date=date.today(),
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

from sqlalchemy import func


@app.route('/upload_activity', methods=['POST'])
def upload_activity():
    data = request.json
    email = data.get('email')
    user = User.query.filter_by(email=email).first()
    if not user:
        return jsonify({'error': 'Пользователь не найден'}), 404

    activity = Activity(
        user_id=user.id,
        date=date.today(),
        steps=data.get('steps'),
        active_kcal=data.get('active_kcal'),
        resting_kcal=data.get('resting_kcal'),
        heart_rate_avg=data.get('heart_rate_avg'),
        distance_km=data.get('distance_km'),
        source=data.get('source', 'manual')
    )
    db.session.add(activity)
    db.session.commit()

    return jsonify({'message': 'Активность сохранена'})

@app.route('/manual_activity', methods=['GET', 'POST'])
def manual_activity():
    user_id = session.get('user_id')
    if not user_id:
        return redirect('/login')
    user = db.session.get(User, user_id)

    if request.method == 'POST':
        steps = request.form.get('steps')
        active_kcal = request.form.get('active_kcal')
        resting_kcal = request.form.get('resting_kcal')
        heart_rate_avg = request.form.get('heart_rate_avg')
        distance_km = request.form.get('distance_km')

        activity = Activity(
            user_id=user.id,
            date=date.today(),
            steps=int(steps or 0),
            active_kcal=int(active_kcal or 0),
            resting_kcal=int(resting_kcal or 0),
            heart_rate_avg=int(heart_rate_avg or 0),
            distance_km=float(distance_km or 0),
            source='manual'
        )
        db.session.add(activity)
        db.session.commit()
        return redirect('/profile')

    return render_template('manual_activity.html', user=user)


@app.route('/diet_history')
def diet_history():
    user_id = session.get('user_id')
    if not user_id:
        return redirect('/login')

    today = date.today()
    week_ago = today - datetime.timedelta(days=7)
    month_ago = today - datetime.timedelta(days=30)

    diets = Diet.query.filter_by(user_id=user_id).order_by(Diet.date.desc()).all()
    week_total = db.session.query(func.sum(Diet.total_kcal)).filter(
        Diet.user_id == user_id,
        Diet.date >= week_ago
    ).scalar() or 0

    month_total = db.session.query(func.sum(Diet.total_kcal)).filter(
        Diet.user_id == user_id,
        Diet.date >= month_ago
    ).scalar() or 0

    # 📊 График за 7 дней
    last_7_days = [today - datetime.timedelta(days=i) for i in range(6, -1, -1)]
    chart_labels = [d.strftime("%d.%m") for d in last_7_days]
    chart_values = []

    for d in last_7_days:
        total = db.session.query(func.sum(Diet.total_kcal)).filter_by(user_id=user_id, date=d).scalar()
        chart_values.append(total or 0)

    return render_template(
        "diet_history.html",
        diets=diets,
        week_total=week_total,
        month_total=month_total,
        chart_labels=json.dumps(chart_labels),
        chart_values=json.dumps(chart_values)
    )

@app.route('/diet/<int:diet_id>')
def view_diet(diet_id):
    user_id = session.get('user_id')
    diet = Diet.query.filter_by(id=diet_id, user_id=user_id).first()
    if not diet:
        return "Диета не найдена."

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

@app.route('/api/link_telegram', methods=['POST'])
def link_telegram():
    data = request.json
    code = data.get("code")
    chat_id = data.get("chat_id")

    user = User.query.filter_by(telegram_code=code).first()
    if not user:
        return jsonify({"error": "Неверный код"}), 404

    user.telegram_chat_id = str(chat_id)
    user.telegram_code = None
    db.session.commit()
    return jsonify({"message": "OK"}), 200


@app.route('/api/is_registered/<int:chat_id>')
def is_registered(chat_id):
    user = User.query.filter_by(telegram_chat_id=str(chat_id)).first()
    if user:
        return jsonify({"ok": True}), 200
    return jsonify({"ok": False}), 404

@app.route('/api/current_diet/<int:chat_id>')
def api_current_diet(chat_id):
    user = User.query.filter_by(telegram_chat_id=str(chat_id)).first()
    if not user:
        return jsonify({"error": "not found"}), 404

    diet = Diet.query.filter_by(user_id=user.id).order_by(Diet.date.desc()).first()
    if not diet:
        return jsonify({"error": "no diet"}), 404

    return jsonify({
        "date": diet.date.isoformat(),
        "breakfast": json.loads(diet.breakfast),
        "lunch": json.loads(diet.lunch),
        "dinner": json.loads(diet.dinner),
        "snack": json.loads(diet.snack),
        "total_kcal": diet.total_kcal,
        "protein": diet.protein,
        "fat": diet.fat,
        "carbs": diet.carbs
    })


@app.route('/activity')
def activity():
    user_id = session.get('user_id')
    if not user_id:
        return redirect('/login')

    user = db.session.get(User, user_id)
    today_activity = Activity.query.filter_by(user_id=user_id, date=date.today()).first()

    # Получаем активность за последние 7 дней для графиков
    week_ago = date.today() - timedelta(days=7)
    activities = Activity.query.filter(
        Activity.user_id == user_id,
        Activity.date >= week_ago
    ).order_by(Activity.date).all()

    # Подготавливаем данные для графиков
    chart_data = {
        'dates': [],
        'steps': [],
        'calories': [],
        'heart_rate': []
    }

    for day in (date.today() - timedelta(days=i) for i in range(6, -1, -1)):
        chart_data['dates'].append(day.strftime('%d.%m'))
        activity = next((a for a in activities if a.date == day), None)
        chart_data['steps'].append(activity.steps if activity else 0)
        chart_data['calories'].append(activity.active_kcal if activity else 0)
        chart_data['heart_rate'].append(activity.heart_rate_avg if activity else 0)

    return render_template(
        'profile.html',  # Используем тот же шаблон, что и для профиля
        user=user,
        today_activity=today_activity,
        chart_data=chart_data
    )

@app.route('/api/log_meal', methods=['POST'])
def log_meal():
    data = request.get_json()
    user = User.query.filter_by(telegram_chat_id=str(data['chat_id'])).first_or_404()

    # Сначала попробуем взять готовые числа из payload
    calories = data.get("calories")
    protein  = data.get("protein")
    fat      = data.get("fat")
    carbs    = data.get("carbs")

    raw = data.get("analysis", "")

    # Если хоть одно из полей не пришло — падём на разбор текста
    if None in (calories, protein, fat, carbs):
        # парсим из raw
        def ptn(p):
            m = re.search(p, raw, flags=re.IGNORECASE)
            return float(m.group(1)) if m else None

        calories = ptn(r'Калории[:\s]+(\d+)')
        protein  = ptn(r'Белки[:\s]+([\d.]+)')
        fat      = ptn(r'Жиры[:\s]+([\d.]+)')
        carbs    = ptn(r'Углеводы[:\s]+([\d.]+)')

    # если всё ещё что‑то не распарсилось — 400
    if None in (calories, protein, fat, carbs):
        return jsonify({"error":"cannot parse BJU"}), 400

    meal = MealLog(
        user_id   = user.id,
        date      = date.today(),
        meal_type = data['meal_type'],
        calories  = int(calories),
        protein   = float(protein),
        fat       = float(fat),
        carbs     = float(carbs),
        analysis  = raw
    )

    try:
        db.session.add(meal)
        db.session.commit()
        return jsonify({"status":"ok"}), 200

    except IntegrityError:
        db.session.rollback()
        return jsonify({"error":"exists"}), 409

@app.route('/api/log_meal', methods=['DELETE'])
def delete_meal():
    data = request.get_json()
    user = User.query.filter_by(telegram_chat_id=str(data['chat_id'])).first_or_404()
    meal = MealLog.query.filter_by(
        user_id=user.id,
        date=date.today(),
        meal_type=data['meal_type']
    ).first_or_404()
    db.session.delete(meal)
    db.session.commit()
    return '', 200


@app.route('/api/meals/today/<int:chat_id>')
def get_today_meals(chat_id):
    user = User.query.filter_by(telegram_chat_id=str(chat_id)).first_or_404()
    logs = MealLog.query.filter_by(user_id=user.id, date=date.today()).all()
    return jsonify([
        {'meal_type': m.meal_type, 'analysis': m.analysis, 'time': m.created_at.isoformat()}
        for m in logs
    ]), 200


@app.route('/metrics')
def metrics():
    user_id = session.get('user_id')
    if not user_id:
        return redirect('/login')
    user = db.session.get(User, user_id)

    # 1) Суммарные калории по приёмам пищи за сегодня
    total_meals = db.session.query(func.sum(MealLog.calories)) \
        .filter_by(user_id=user.id, date=date.today()) \
        .scalar() or 0

    # Получаем список приёмов пищи
    today_meals = MealLog.query \
        .filter_by(user_id=user.id, date=date.today()) \
        .all()

    # 2) Базовый метаболизм
    metabolism = user.metabolism or 0

    # 3) Активная калорийность
    activity = Activity.query.filter_by(user_id=user.id, date=date.today()).first()
    active_kcal   = activity.active_kcal  if activity else None
    steps         = activity.steps        if activity else None
    distance_km   = activity.distance_km  if activity else None
    resting_kcal  = activity.resting_kcal if activity else None

    # Проверяем данные
    missing_meals    = (total_meals == 0)
    missing_activity = (active_kcal is None)

    # 4) Дефицит
    deficit = None
    if not missing_meals and not missing_activity:
        deficit = (metabolism + active_kcal) - total_meals

    return render_template(
        'profile.html',
        user=user,
        age=calculate_age(user.date_of_birth) if user.date_of_birth else None,
        # для табов профиля и активности
        diet=Diet.query.filter_by(user_id=user.id).order_by(Diet.date.desc()).first(),
        today_activity=activity,
        latest_analysis=BodyAnalysis.query.filter_by(user_id=user.id).order_by(BodyAnalysis.timestamp.desc()).first(),
        previous_analysis=BodyAnalysis.query.filter_by(user_id=user.id).order_by(BodyAnalysis.timestamp.desc()).offset(1).first(),
        chart_data=None,

        # новые переменные для metrics
        total_meals=total_meals,
        today_meals=today_meals,
        metabolism=metabolism,
        active_kcal=active_kcal,
        steps=steps,
        distance_km=distance_km,
        resting_kcal=resting_kcal,
        deficit=deficit,
        missing_meals=missing_meals,
        missing_activity=missing_activity
    )

@app.route('/api/registered_chats')
def registered_chats():
    """Возвращает список всех телеграм‑chat_id, которые привязаны к пользователям."""
    chats = (
        db.session.query(User.telegram_chat_id)
        .filter(User.telegram_chat_id.isnot(None))
        .all()
    )
    # chats — список кортежей, поэтому разбираем
    chat_ids = [c[0] for c in chats]
    return jsonify({"chat_ids": chat_ids})

if __name__ == '__main__':
    telegram_thread = threading.Thread(target=run_bot)
    telegram_thread.start()

    app.run(debug=True)
