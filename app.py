import os
from datetime import datetime, date, timedelta
import base64
import json
from flask import jsonify # Убедись, что jsonify импортирован вверху файла
import requests
from flask import Flask, render_template, request, redirect, session, jsonify, url_for, flash, abort, \
    send_from_directory
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
from functools import wraps
from PIL import Image  # Import Pillow

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "supersecret")
app.jinja_env.globals.update(getattr=getattr)

app.config['UPLOAD_FOLDER'] = 'uploads'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# --- Image Resizing Configuration ---
CHAT_IMAGE_MAX_SIZE = (200, 200)  # Max width and height for chat images


def resize_image(filepath, max_size):
    """Resizes an image and saves it back to the same path."""
    try:
        with Image.open(filepath) as img:
            print(f"DEBUG: Resizing image: {filepath}, original size: {img.size}")
            img.thumbnail(max_size, Image.Resampling.LANCZOS)
            img.save(filepath)  # Overwrites the original
            print(f"DEBUG: Image resized to: {img.size}")
    except Exception as e:
        print(f"ERROR: Failed to resize image {filepath}: {e}")


@app.route('/uploads/<path:filename>')
def serve_uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


ADMIN_EMAIL = "admin@healthclub.local"


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect('/login')
        return f(*args, **kwargs)

    return decorated_function


def get_current_user():
    user_id = session.get('user_id')
    if user_id:
        return db.session.get(User, user_id)
    return None


def is_admin():
    user = get_current_user()
    return user and user.email == ADMIN_EMAIL


def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('user_id'):
            return redirect(url_for('login', next=request.url))
        if not is_admin():
            abort(403)  # Forbidden
        return f(*args, **kwargs)

    return decorated_function


# Config DB
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///35healthclubs.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

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

    # --- Старые поля, которые больше не обновляются, но могут использоваться для чтения ---
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
    # --- НОВЫЕ ПОЛЯ ДЛЯ ЦЕЛЕЙ ---
    fat_mass_goal = db.Column(db.Float, nullable=True)
    muscle_mass_goal = db.Column(db.Float, nullable=True)

    is_trainer = db.Column(db.Boolean, default=False, nullable=False)
    # Сохраняем только имя файла аватарки
    avatar = db.Column(db.String(200), nullable=True)

    analysis_comment = db.Column(db.Text)
    telegram_chat_id = db.Column(db.String(50), nullable=True)
    telegram_code = db.Column(db.String(10), nullable=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow)


class Group(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text, nullable=True)  # «дивиз» группы
    trainer_id = db.Column(db.Integer, db.ForeignKey('user.id'), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    trainer = db.relationship('User', backref=db.backref('own_group', uselist=False))
    members = db.relationship('GroupMember', back_populates='group', cascade='all, delete-orphan')
    messages = db.relationship('GroupMessage', back_populates='group', cascade='all, delete-orphan')


class GroupMember(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('group.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    joined_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('group_id', 'user_id', name='uq_group_user'),
    )

    group = db.relationship('Group', back_populates='members')
    user = db.relationship('User', backref=db.backref('groups', lazy='dynamic'))


class GroupMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('group.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    text = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    # New: Add support for image messages (stores filename)
    image_file = db.Column(db.String(200), nullable=True)

    group = db.relationship('Group', back_populates='messages')
    user = db.relationship('User')
    # New: Relationship for reactions
    reactions = db.relationship('MessageReaction', back_populates='message', cascade='all, delete-orphan')


class MessageReaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    message_id = db.Column(db.Integer, db.ForeignKey('group_message.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    # For simplicity, let's just use 'like' or an emoji string
    reaction_type = db.Column(db.String(20), nullable=False, default='👍')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('message_id', 'user_id', name='uq_message_user_reaction'),
    )
    message = db.relationship('GroupMessage', back_populates='reactions')
    user = db.relationship('User')


class GroupTask(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('group.id'), nullable=False)
    trainer_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=True)
    is_announcement = db.Column(db.Boolean, default=False, nullable=False)  # True for announcements, False for tasks
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    due_date = db.Column(db.Date, nullable=True)  # Optional due date for tasks

    group = db.relationship('Group', backref=db.backref('tasks', cascade='all, delete-orphan', lazy='dynamic'))
    trainer = db.relationship('User')


class MealLog(db.Model):
    __tablename__ = 'meal_logs'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    date = db.Column(db.Date, nullable=False, default=date.today)
    meal_type = db.Column(db.String(20), nullable=False)  # 'breakfast','lunch','dinner','snack'
    # новые поля для расчётов
    name = db.Column(db.String(100), nullable=True)  # Название блюда от AI
    verdict = db.Column(db.String(200), nullable=True)  # Краткий вердикт от AI

    calories = db.Column(db.Integer, nullable=False)
    protein = db.Column(db.Float, nullable=False)
    fat = db.Column(db.Float, nullable=False)
    carbs = db.Column(db.Float, nullable=False)
    # оригинальный текст (на всякий случай)
    analysis = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', backref=db.backref('meals', lazy=True))
    __table_args__ = (
        UniqueConstraint('user_id', 'date', 'meal_type', name='uq_user_date_meal'),
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

    return dict(
        get_bmi_category=get_bmi_category,
        calculate_age=calculate_age,  # <-- теперь в шаблоне доступна
        today=date.today(),  # <-- и переменная today
    )


@app.context_processor
def inject_user():
    return {'current_user': get_current_user()}


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
@login_required # Заменяем вашу старую функцию profile этой

def profile():
    user_id = session.get('user_id')
    user = db.session.get(User, user_id)
    if not user_id:
        return redirect('/login')

    session['user_email_before_edit'] = user.email


    user = db.session.get(User, user_id)
    age = calculate_age(user.date_of_birth) if user.date_of_birth else None
    diet = Diet.query.filter_by(user_id=user_id).order_by(Diet.date.desc()).first()
    today_activity = Activity.query.filter_by(user_id=user_id, date=date.today()).first()

    analyses = BodyAnalysis.query.filter_by(user_id=user_id).order_by(BodyAnalysis.timestamp.desc()).limit(2).all()
    latest_analysis = analyses[0] if len(analyses) > 0 else None
    previous_analysis = analyses[1] if len(analyses) > 1 else None

    total_meals = db.session.query(func.sum(MealLog.calories)).filter_by(user_id=user.id,
                                                                         date=date.today()).scalar() or 0
    today_meals = MealLog.query.filter_by(user_id=user.id, date=date.today()).all()

    metabolism = latest_analysis.metabolism if latest_analysis else user.metabolism or 0
    active_kcal = today_activity.active_kcal if today_activity else None
    steps = today_activity.steps if today_activity else None
    distance_km = today_activity.distance_km if today_activity else None
    resting_kcal = today_activity.resting_kcal if today_activity else None

    missing_meals = (total_meals == 0)
    missing_activity = (active_kcal is None)

    deficit = None
    if not missing_meals and not missing_activity and metabolism is not None:
        deficit = (metabolism + (active_kcal or 0)) - total_meals

    user_memberships = GroupMember.query.filter_by(user_id=user.id).all()
    user_joined_group = user.own_group if user.own_group else (user_memberships[0].group if user_memberships else None)

    fat_loss_progress = None
    if latest_analysis and user.fat_mass_goal and latest_analysis.fat_mass > user.fat_mass_goal:
        start_datetime = latest_analysis.timestamp
        today = date.today()

        meal_data = db.session.query(MealLog.date, func.sum(MealLog.calories)).filter(
            MealLog.user_id == user_id, MealLog.date >= start_datetime.date()
        ).group_by(MealLog.date).all()
        meal_map = dict(meal_data)

        activity_data = db.session.query(Activity.date, Activity.active_kcal).filter(
            Activity.user_id == user_id, Activity.date >= start_datetime.date()
        ).all()
        activity_map = dict(activity_data)

        total_accumulated_deficit = 0
        delta_days = (today - start_datetime.date()).days

        if delta_days >= 0:
            for i in range(delta_days + 1):
                current_day = start_datetime.date() + timedelta(days=i)
                consumed = meal_map.get(current_day, 0)
                burned_active = activity_map.get(current_day, 0)

                # --- ИЗМЕНЕНИЯ ЗДЕСЬ ---
                if i == 0:  # Это день анализа
                    # Убираем калории, съеденные ДО замера
                    calories_before_analysis = db.session.query(func.sum(MealLog.calories)).filter(
                        MealLog.user_id == user_id,
                        MealLog.date == current_day,
                        MealLog.created_at < start_datetime
                    ).scalar() or 0
                    consumed -= calories_before_analysis
                    # Игнорируем активность за день замера, т.к. нет точного времени
                    burned_active = 0
                # --- КОНЕЦ ИЗМЕНЕНИЙ ---

                daily_deficit = (metabolism + burned_active) - consumed
                if daily_deficit > 0:
                    total_accumulated_deficit += daily_deficit

        KCAL_PER_KG_FAT = 7700
        total_fat_to_lose_kg = latest_analysis.fat_mass - user.fat_mass_goal
        estimated_fat_burned_kg = min(total_accumulated_deficit / KCAL_PER_KG_FAT, total_fat_to_lose_kg)

        percentage = 0
        if total_fat_to_lose_kg > 0:
            percentage = (estimated_fat_burned_kg / total_fat_to_lose_kg) * 100

        fat_loss_progress = {
            'percentage': min(100, max(0, percentage)),
            'burned_kg': estimated_fat_burned_kg,
            'total_to_lose_kg': total_fat_to_lose_kg,
            'initial_kg': latest_analysis.fat_mass,
            'goal_kg': user.fat_mass_goal,
            'current_kg': latest_analysis.fat_mass - estimated_fat_burned_kg
        }

    return render_template(
        'profile.html',
        user=user,
        age=age,
        diet=diet,
        today_activity=today_activity,
        latest_analysis=latest_analysis,
        previous_analysis=previous_analysis,
        total_meals=total_meals,
        today_meals=today_meals,
        metabolism=metabolism,
        active_kcal=active_kcal,
        steps=steps,
        distance_km=distance_km,
        resting_kcal=resting_kcal,
        deficit=deficit,
        missing_meals=missing_meals,
        missing_activity=missing_activity,
        user_joined_group=user_joined_group,
        fat_loss_progress=fat_loss_progress
    )
@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')


@app.route('/upload_analysis', methods=['POST'])
def upload_analysis():
    file = request.files.get('file')
    user_id = session.get('user_id')
    user = db.session.get(User, user_id)
    if not file or not user:
        flash("Файл не загружен или пользователь не авторизован.", "error")
        return redirect('/profile')

    filename = secure_filename(file.filename)
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(filepath)

    with open(filepath, "rb") as image_file:
        base64_image = base64.b64encode(image_file.read()).decode("utf-8")

    try:
        # --- ШАГ 1: Извлечение данных с изображения ---
        response_metrics = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Ты — фитнес-аналитик. Извлеки следующие параметры из фото анализа тела (bioimpedance):"
                        "height, weight, muscle_mass, muscle_percentage, body_water, protein_percentage, "
                        "bone_mineral_percentage, skeletal_muscle_mass, visceral_fat_rating, metabolism, "
                        "waist_hip_ratio, body_age, fat_mass, bmi, fat_free_body_weight. "
                        "Верни СТРОГО JSON с найденными числовыми значениями."
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
            max_tokens=1000,
            response_format={"type": "json_object"}
        )
        content = response_metrics.choices[0].message.content.strip()
        result = json.loads(content)

        if "error" in result:
            missing = ', '.join(result.get("missing", []))
            flash(f"Недостаточно данных в анализе: {missing}.", "error")
            return redirect('/profile')

        # --- ШАГ 2: Генерация целей на основе извлеченных данных ---
        age = calculate_age(user.date_of_birth) if user.date_of_birth else 'не указан'
        prompt_goals = (
            f"Для пользователя с параметрами: возраст {age}, рост {result.get('height')} см, "
            f"вес {result.get('weight')} кг, жировая масса {result.get('fat_mass')} кг, "
            f"мышечная масса {result.get('muscle_mass')} кг. "
            f"Предложи реалистичные цели по снижению жировой массы и увеличению мышечной массы. "
            f"Верни СТРОГО JSON в формате: "
            f'{{"fat_mass_goal": <число>, "muscle_mass_goal": <число>}}'
        )
        response_goals = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "Ты — профессиональный фитнес-тренер. Давай цели в формате JSON."},
                {"role": "user", "content": prompt_goals}
            ],
            max_tokens=200,
            response_format={"type": "json_object"}
        )
        goals_content = response_goals.choices[0].message.content.strip()
        goals_result = json.loads(goals_content)

        # Объединяем результаты
        result.update(goals_result)

        session['temp_analysis'] = result
        return render_template('confirm_analysis.html', data=result)


    except Exception as e:

        # ДОБАВЬТЕ ЭТУ СТРОКУ ДЛЯ ДИАГНОСТИКИ

        print(f"!!! ОШИБКА В UPLOAD_ANALYSIS: {e}")

        flash(f"Не удалось проанализировать изображение. Проверьте консоль сервера для деталей.", "error")

        return redirect('/profile')


# УДАЛИТЕ СТАРУЮ ФУНКЦИЮ @app.route('/add_meal')

# ЗАМЕНИТЕ СТАРУЮ ФУНКЦИЮ meals НА ЭТУ
@app.route("/meals", methods=["GET", "POST"])
@login_required
def meals():
    user = get_current_user()

    # --- ЛОГИКА СОХРАНЕНИЯ (POST-ЗАПРОС) ---
    if request.method == "POST":
        meal_type = request.form.get('meal_type')
        if not meal_type:
            flash("Произошла ошибка: не указан тип приёма пищи.", "error")
            return redirect(url_for('meals'))

        try:
            calories = int(request.form.get('calories', 0))
            protein = float(request.form.get('protein', 0.0))
            fat = float(request.form.get('fat', 0.0))
            carbs = float(request.form.get('carbs', 0.0))
            name = request.form.get('name')
            verdict = request.form.get('verdict')
            analysis = request.form.get('analysis', '')

            existing_meal = MealLog.query.filter_by(
                user_id=user.id, date=date.today(), meal_type=meal_type
            ).first()

            if existing_meal:
                existing_meal.calories = calories
                existing_meal.protein = protein
                existing_meal.fat = fat
                existing_meal.carbs = carbs
                existing_meal.name = name
                existing_meal.verdict = verdict
                existing_meal.analysis = analysis
                flash(f"Приём пищи '{meal_type.capitalize()}' успешно обновлён!", "success")
            else:
                new_meal = MealLog(
                    user_id=user.id, date=date.today(), meal_type=meal_type,
                    calories=calories, protein=protein, fat=fat, carbs=carbs,
                    name=name, verdict=verdict, analysis=analysis
                )
                db.session.add(new_meal)
                flash(f"Приём пищи '{meal_type.capitalize()}' успешно добавлен!", "success")

            db.session.commit()

        except (ValueError, TypeError) as e:
            db.session.rollback()
            flash(f"Ошибка в формате данных от AI. Не удалось сохранить. ({e})", "error")

        # После обработки POST-запроса, перенаправляем на ту же страницу
        # чтобы избежать повторной отправки формы при обновлении
        return redirect(url_for('meals'))

    # --- ЛОГИКА ОТОБРАЖЕНИЯ (GET-ЗАПРОС) ---
    today_meals = MealLog.query.filter_by(user_id=user.id, date=date.today()).all()
    grouped = {
        "breakfast": [], "lunch": [], "dinner": [], "snack": []
    }
    for m in today_meals:
        grouped[m.meal_type].append(m)

    latest_analysis = BodyAnalysis.query.filter_by(user_id=user.id).order_by(BodyAnalysis.timestamp.desc()).first()

    return render_template("profile.html",
                           user=user,
                           meals=grouped,
                           latest_analysis=latest_analysis,
                           tab='meals')

@app.route('/confirm_analysis', methods=['POST'])
def confirm_analysis():
    user_id = session.get('user_id')
    if not user_id or 'temp_analysis' not in session:
        flash("Нет данных для подтверждения анализа.", "error")
        return redirect('/profile')

    # Данные, извлеченные из фото
    analysis_data = session.pop('temp_analysis')
    user = db.session.get(User, user_id)

    # 1. Обновляем только цели и рост в таблице User
    user.height = request.form.get('height', user.height, type=int)
    user.fat_mass_goal = request.form.get('fat_mass_goal', user.fat_mass_goal, type=float)
    user.muscle_mass_goal = request.form.get('muscle_mass_goal', user.muscle_mass_goal, type=float)
    user.analysis_comment = analysis_data.get("analysis")  # Комментарий от AI, если он был
    user.updated_at = datetime.utcnow()

    # 2. Создаем новую запись в истории BodyAnalysis
    # Всегда создаем новую запись, чтобы сохранить полную историю замеров
    new_analysis_entry = BodyAnalysis(
        user_id=user.id,
        timestamp=datetime.utcnow()
    )

    # Заполняем запись всеми данными из анализа
    for field, value in analysis_data.items():
        if hasattr(new_analysis_entry, field):
            setattr(new_analysis_entry, field, value)

    # Убедимся, что рост из формы также сохраняется в исторической записи
    new_analysis_entry.height = user.height

    db.session.add(new_analysis_entry)
    db.session.commit()

    flash("Данные анализа тела и цели успешно сохранены!", "success")
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

    latest_analysis = BodyAnalysis.query.filter_by(user_id=user_id).order_by(BodyAnalysis.timestamp.desc()).first()

    # Проверка наличия всех необходимых данных для генерации диеты
    if not (latest_analysis and
            all(getattr(latest_analysis, attr, None) is not None
                for attr in ['height', 'weight', 'muscle_mass', 'fat_mass', 'metabolism'])):
        flash("Пожалуйста, загрузите актуальный анализ тела для генерации диеты.", "warning")
        # Возвращаем JSON с командой на редирект, чтобы фронтенд мог обработать это
        return jsonify({"redirect": url_for('profile')})

    prompt = f"""
    У пользователя следующие параметры:
    Рост: {latest_analysis.height} см
    Вес: {latest_analysis.weight} кг
    Мышечная масса: {latest_analysis.muscle_mass} кг
    Жировая масса: {latest_analysis.fat_mass} кг
    Метаболизм: {latest_analysis.metabolism} ккал
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

        # Удаляем старую диету за сегодня, если она есть
        existing_diet = Diet.query.filter_by(user_id=user_id, date=date.today()).first()
        if existing_diet:
            db.session.delete(existing_diet)
            db.session.commit()

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

        flash("Диета успешно сгенерирована!", "success")

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
        flash(f"Ошибка генерации диеты: {e}", "error")
        return jsonify({"error": str(e)}), 500


@app.route('/edit_profile', methods=['POST'])
@login_required
def edit_profile():
    user = get_current_user()
    if not user:
        # Эта проверка на всякий случай, т.к. login_required уже есть
        return redirect(url_for('login'))

    # --- Обновление текстовых полей ---
    user.name = request.form.get('name', user.name)
    user.email = request.form.get('email', user.email)
    date_of_birth_str = request.form.get('date_of_birth')
    if date_of_birth_str:
        try:
            user.date_of_birth = datetime.strptime(date_of_birth_str, '%Y-%m-%d').date()
        except ValueError:
            flash("Неверный формат даты рождения.", "error")
            return redirect(url_for('profile'))

    # --- Проверка на уникальность нового email ---
    # Проверяем, только если email был изменен
    if 'email' in request.form and user.email != session.get('user_email_before_edit'):
        existing_user = User.query.filter(User.email == user.email, User.id != user.id).first()
        if existing_user:
            flash("Этот email уже используется другим пользователем.", "error")
            # Откатываем изменение email обратно, чтобы не сохранять
            user.email = session.get('user_email_before_edit')
            return redirect(url_for('profile'))

    # --- Обновление пароля (если он был введен) ---
    new_password = request.form.get('new_password')
    confirm_password = request.form.get('confirm_password')

    if new_password:
        if new_password != confirm_password:
            flash("Пароли не совпадают.", "error")
            return redirect(url_for('profile'))
        if len(new_password) < 6:
            flash("Пароль должен содержать не менее 6 символов.", "error")
            return redirect(url_for('profile'))

        user.password = bcrypt.generate_password_hash(new_password).decode('utf-8')

    # --- Загрузка новой аватарки (если она была отправлена) ---
    if 'avatar' in request.files:
        file = request.files['avatar']
        if file.filename != '':
            # Удаляем старый аватар, если он есть и это не дефолтный
            if user.avatar:
                old_avatar_path = os.path.join(app.config['UPLOAD_FOLDER'], user.avatar)
                if os.path.exists(old_avatar_path):
                    try:
                        os.remove(old_avatar_path)
                    except OSError as e:
                        print(f"Error deleting old avatar: {e}")

            filename = secure_filename(f"avatar_{user.id}_{file.filename}")
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(file_path)
            user.avatar = filename

    try:
        db.session.commit()
        flash("Профиль успешно обновлен!", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Произошла ошибка при обновлении профиля: {e}", "error")

    return redirect(url_for('profile'))

@app.route('/diet')
@login_required
def diet():
    user_id = session.get('user_id')
    diet = Diet.query.filter_by(user_id=user_id).order_by(Diet.date.desc()).first()
    if not diet:
        flash("Диета ещё не сгенерирована. Сгенерируйте ее из профиля.", "info")
        return redirect('/profile')

    return render_template("confirm_diet.html", diet=diet,
                           breakfast=json.loads(diet.breakfast),
                           lunch=json.loads(diet.lunch),
                           dinner=json.loads(diet.dinner),
                           snack=json.loads(diet.snack))


@app.route('/upload_activity', methods=['POST'])
def upload_activity():
    data = request.json
    email = data.get('email')
    user = User.query.filter_by(email=email).first()
    if not user:
        return jsonify({'error': 'Пользователь не найден'}), 404

    # Удаляем старую активность за сегодня, если она есть
    existing_activity = Activity.query.filter_by(user_id=user.id, date=date.today()).first()
    if existing_activity:
        db.session.delete(existing_activity)
        db.session.commit()

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
@login_required
def manual_activity():
    user_id = session.get('user_id')
    user = db.session.get(User, user_id)

    if request.method == 'POST':
        steps = request.form.get('steps')
        active_kcal = request.form.get('active_kcal')
        resting_kcal = request.form.get('resting_kcal')
        heart_rate_avg = request.form.get('heart_rate_avg')
        distance_km = request.form.get('distance_km')

        # Удаляем старую активность за сегодня, если она есть
        existing_activity = Activity.query.filter_by(user_id=user.id, date=date.today()).first()
        if existing_activity:
            db.session.delete(existing_activity)
            db.session.commit()

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
        flash("Активность за сегодня успешно обновлена!", "success")
        return redirect('/profile')

    # Предзаполнение формы текущими данными, если они есть
    today_activity = Activity.query.filter_by(user_id=user_id, date=date.today()).first()
    return render_template('manual_activity.html', user=user, today_activity=today_activity)


@app.route('/diet_history')
@login_required
def diet_history():
    user_id = session.get('user_id')

    today = date.today()
    week_ago = today - timedelta(days=7)
    month_ago = today - timedelta(days=30)

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
    last_7_days = [today - timedelta(days=i) for i in range(6, -1, -1)]
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


@app.route('/add_meal', methods=['POST'])
@login_required
def add_meal():
    user_id = session.get('user_id')
    meal_type = request.form.get('meal_type')
    today = date.today()

    if not meal_type:
        flash("Произошла ошибка: не указан тип приёма пищи.", "error")
        return redirect(url_for('meals')) # Перенаправляем на страницу с приёмами пищи

    try:
        # Безопасно получаем данные из формы с помощью .get()
        name = request.form.get('name')
        verdict = request.form.get('verdict')
        analysis = request.form.get('analysis', '')
        # Преобразуем в числа с обработкой ошибок
        calories = int(request.form.get('calories', 0))
        protein = float(request.form.get('protein', 0.0))
        fat = float(request.form.get('fat', 0.0))
        carbs = float(request.form.get('carbs', 0.0))

        # Ищем существующую запись для обновления или создаём новую
        existing_meal = MealLog.query.filter_by(
            user_id=user_id,
            date=today,
            meal_type=meal_type
        ).first()

        if existing_meal:
            # Обновляем существующую запись
            existing_meal.name = name
            existing_meal.verdict = verdict
            existing_meal.calories = calories
            existing_meal.protein = protein
            existing_meal.fat = fat
            existing_meal.carbs = carbs
            existing_meal.analysis = analysis
            flash(f"Приём пищи '{meal_type.capitalize()}' успешно обновлён!", "success")
        else:
            # Создаём новую запись
            new_meal = MealLog(
                user_id=user_id,
                date=today,
                meal_type=meal_type,
                name=name,
                verdict=verdict,
                calories=calories,
                protein=protein,
                fat=fat,
                carbs=carbs,
                analysis=analysis
            )
            db.session.add(new_meal)
            flash(f"Приём пищи '{meal_type.capitalize()}' успешно добавлен!", "success")

        db.session.commit()

    except (ValueError, TypeError) as e:
        # Ловим ошибки, если данные от AI пришли в неверном формате
        db.session.rollback()
        flash(f"Ошибка сохранения данных. Пожалуйста, попробуйте снова. ({e})", "error")

    # Перенаправляем пользователя обратно на вкладку "Приёмы пищи"
    return redirect(url_for('meals'))

@app.route('/diet/<int:diet_id>')
@login_required
def view_diet(diet_id):
    user_id = session.get('user_id')
    diet = Diet.query.filter_by(id=diet_id, user_id=user_id).first()
    if not diet:
        flash("Диета не найдена.", "error")
        return redirect('/diet_history')

    return render_template("confirm_diet.html", diet=diet,
                           breakfast=json.loads(diet.breakfast),
                           lunch=json.loads(diet.lunch),
                           dinner=json.loads(diet.dinner),
                           snack=json.loads(diet.snack))


@app.route('/reset_diet', methods=['POST'])
@login_required
def reset_diet():
    user_id = session.get('user_id')

    diet = Diet.query.filter_by(user_id=user_id).order_by(Diet.date.desc()).first()
    if diet and diet.date == date.today():  # Удаляем только диету за сегодня
        db.session.delete(diet)
        db.session.commit()
        flash("Сегодняшняя диета успешно сброшена. Вы можете сгенерировать новую.", "success")
    else:
        flash("Нет сегодняшней диеты для сброса.", "info")

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
@login_required
def activity():
    user_id = session.get('user_id')

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
        activity_for_day = next((a for a in activities if a.date == day),
                                None)  # Переименовано, чтобы избежать конфликта
        chart_data['steps'].append(activity_for_day.steps if activity_for_day else 0)
        chart_data['calories'].append(activity_for_day.active_kcal if activity_for_day else 0)
        chart_data['heart_rate'].append(activity_for_day.heart_rate_avg if activity_for_day else 0)

    # Здесь возвращаем activity.html, если он есть, или используем profile.html с нужным табом
    return render_template(
        'profile.html',
        user=user,
        today_activity=today_activity,
        chart_data=chart_data,
        tab='activity'  # Указываем активный таб
    )


@app.route('/api/log_meal', methods=['POST'])
def log_meal():
    data = request.get_json()
    user = User.query.filter_by(telegram_chat_id=str(data['chat_id'])).first_or_404()

    # Сначала попробуем взять готовые числа из payload
    calories = data.get("calories")
    protein = data.get("protein")
    fat = data.get("fat")
    carbs = data.get("carbs")

    raw = data.get("analysis", "")

    # Если хоть одно из полей не пришло — падём на разбор текста
    if None in (calories, protein, fat, carbs):
        # парсим из raw
        def ptn(p):
            m = re.search(p, raw, flags=re.IGNORECASE)
            return float(m.group(1)) if m else None

        calories = ptn(r'Калории[:\s]+(\d+)')
        protein = ptn(r'Белки[:\s]+([\d.]+)')
        fat = ptn(r'Жиры[:\s]+([\d.]+)')
        carbs = ptn(r'Углеводы[:\s]+([\d.]+)')

    # если всё ещё что‑то не распарсилось — 400
    if None in (calories, protein, fat, carbs):
        return jsonify({"error": "cannot parse BJU"}), 400

    meal = MealLog(
        user_id=user.id,
        date=date.today(),
        meal_type=data['meal_type'],
        calories=int(calories),
        protein=float(protein),
        fat=float(fat),
        carbs=float(carbs),
        analysis=raw
    )

    try:
        db.session.add(meal)
        db.session.commit()
        return jsonify({"status": "ok"}), 200

    except IntegrityError:
        db.session.rollback()
        return jsonify({"error": "exists"}), 409


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


# ЭТО ПРАВИЛЬНЫЙ КОД
from flask import jsonify # Убедись, что jsonify импортирован вверху файла

@app.route('/analyze_meal_photo', methods=['POST'])
def analyze_meal_photo():
    file = request.files.get('file')
    if not file:
        return jsonify({"error": "Файл не найден"}), 400

    # ... (код сохранения файла) ...
    filename = secure_filename(file.filename)
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(filepath)


    try:
        with open(filepath, 'rb') as f:
            b64 = base64.b64encode(f.read()).decode('utf-8')

        # --- ИЗМЕНЕННЫЙ ПРОМПТ ---
        system_prompt = (
            "Ты — профессиональный диетолог. Проанализируй фото еды. Определи:"
            "\n- Название блюда (в поле 'name')."
            "\n- Калорийность, Белки, Жиры, Углеводы (в полях 'calories', 'protein', 'fat', 'carbs')."
            "\n- Дай подробный текстовый анализ блюда (в поле 'analysis')."
            "\n- Сделай краткий вывод: насколько блюдо полезно или вредно для диеты (в поле 'verdict')."
            '\nВерни JSON СТРОГО в формате: {"name": "...", "calories": 0, "protein": 0.0, "fat": 0.0, "carbs": 0.0, "analysis": "...", "verdict": "..."}'
        )

        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                    {"type": "text", "text": "Проанализируй блюдо на фото."}
                ]}
            ],
            max_tokens=500,
            response_format={"type": "json_object"}
        )

        content = response.choices[0].message.content.strip()
        data = json.loads(content)

        return jsonify(data)

    except Exception as e:
        return jsonify({"error": f"Ошибка анализа фото: {e}"}), 500


@app.route('/api/meals/today/<int:chat_id>')
def get_today_meals_api(chat_id):
    # Находим пользователя по ID чата в телеграме
    user = User.query.filter_by(telegram_chat_id=str(chat_id)).first_or_404()

    # Ищем все записи о приемах пищи для этого пользователя за сегодня
    logs = MealLog.query.filter_by(user_id=user.id, date=date.today()).order_by(MealLog.created_at).all()

    # Считаем итоговые калории
    total_calories = sum(m.calories for m in logs)

    # Формируем данные для ответа
    meal_data = [
        {
            'meal_type': m.meal_type,
            'name': m.name or "Без названия",
            'calories': m.calories,
            'protein': m.protein,
            'fat': m.fat,
            'carbs': m.carbs
        }
        for m in logs
    ]

    return jsonify({"meals": meal_data, "total_calories": total_calories}), 200



@app.route('/api/meals/today/<int:chat_id>')
def get_today_meals(chat_id):
    user = User.query.filter_by(telegram_chat_id=str(chat_id)).first_or_404()
    logs = MealLog.query.filter_by(user_id=user.id, date=date.today()).all()
    return jsonify([
        {'meal_type': m.meal_type, 'analysis': m.analysis, 'time': m.created_at.isoformat()}
        for m in logs
    ]), 200


@app.route('/metrics')
@login_required
def metrics():
    user_id = session.get('user_id')
    user = db.session.get(User, user_id)
    latest_analysis = BodyAnalysis.query.filter_by(user_id=user_id).order_by(BodyAnalysis.timestamp.desc()).first()

    # 1) Суммарные калории по приёмам пищи за сегодня
    total_meals = db.session.query(func.sum(MealLog.calories)) \
                      .filter_by(user_id=user.id, date=date.today()) \
                      .scalar() or 0

    # Получаем список приёмов пищи
    today_meals = MealLog.query \
        .filter_by(user_id=user.id, date=date.today()) \
        .all()

    # 2) Базовый метаболизм из последнего замера
    metabolism = latest_analysis.metabolism if latest_analysis else user.metabolism or 0

    # 3) Активная калорийность
    activity = Activity.query.filter_by(user_id=user.id, date=date.today()).first()
    active_kcal = activity.active_kcal if activity else None
    steps = activity.steps if activity else None
    distance_km = activity.distance_km if activity else None
    resting_kcal = activity.resting_kcal if activity else None

    # Проверяем данные
    missing_meals = (total_meals == 0)
    missing_activity = (active_kcal is None)

    # 4) Дефицит
    deficit = None
    if not missing_meals and not missing_activity and metabolism is not None:
        deficit = (metabolism + active_kcal) - total_meals

    return render_template(
        'profile.html',
        user=user,
        age=calculate_age(user.date_of_birth) if user.date_of_birth else None,
        # для табов профиля и активности
        diet=Diet.query.filter_by(user_id=user.id).order_by(Diet.date.desc()).first(),
        today_activity=activity,
        latest_analysis=latest_analysis,
        previous_analysis=BodyAnalysis.query.filter_by(user_id=user.id).order_by(BodyAnalysis.timestamp.desc()).offset(
            1).first(),
        chart_data=None,  # Отключаем для этой страницы, если не нужно

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
        missing_activity=missing_activity,
        tab='metrics'  # Указываем активный таб
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


# ---------------- ADMIN PANEL ----------------

@app.route("/admin")
@admin_required  # Защита маршрута для админа
def admin_dashboard():
    users = User.query.order_by(User.id).all()  # Order by ID for stable display
    today = date.today()

    statuses = {}
    details = {}

    # Define metrics consistent with profile.html
    metrics_def = [
        ('Рост', 'height', '📏', 'см', True),
        ('Вес', 'weight', '⚖️', 'кг', False),
        ('Мышцы', 'muscle_mass', '💪', 'кг', True),
        ('Жир', 'fat_mass', '🧈', 'кг', False),
        ('Вода', 'body_water', '💧', '%', True),
        ('Метаболизм', 'metabolism', '⚡', 'ккал', True),
        ('Белок', 'protein_percentage', '🥚', '%', True),
        ('Висц. жир', 'visceral_fat_rating', '🔥', '', False),
        ('ИМТ', 'bmi', '📐', '', False),
    ]

    for u in users:
        # statuses
        has_meal = MealLog.query.filter_by(user_id=u.id, date=today).count() > 0
        has_activity = Activity.query.filter_by(user_id=u.id, date=today).count() > 0
        statuses[u.id] = {'meal': has_meal, 'activity': has_activity}

        # meals
        meals = MealLog.query.filter_by(user_id=u.id, date=today).all()
        meals_data = [{
            'type': m.meal_type,
            'cal': m.calories,
            'prot': m.protein,
            'fat': m.fat,
            'carbs': m.carbs
        } for m in meals]

        # activity
        act = Activity.query.filter_by(user_id=u.id, date=today).first()
        activity_data = None
        if act:
            activity_data = {
                'steps': act.steps,
                'active_kcal': act.active_kcal,
                'resting_kcal': act.resting_kcal,
                'distance_km': act.distance_km,
                'hr_avg': act.heart_rate_avg
            }

        # body analysis
        last = BodyAnalysis.query.filter_by(user_id=u.id) \
            .order_by(BodyAnalysis.timestamp.desc()).first()
        prev = BodyAnalysis.query.filter_by(user_id=u.id) \
            .order_by(BodyAnalysis.timestamp.desc()).offset(1).first()

        # metrics with deltas
        metrics = []
        for label, field, icon, unit, good_up in metrics_def:
            cur = getattr(last, field, None)
            pr = getattr(prev, field, None)
            diff = pct = arrow = None
            is_good = None
            if cur is not None and pr is not None:
                diff = cur - pr
                if pr != 0:
                    pct = diff / pr * 100
                arrow = '↑' if diff > 0 else '↓' if diff < 0 else ''
                # Handle cases where diff is 0 for arrow display
                if diff == 0:
                    arrow = ''  # No arrow for no change
                    is_good = True  # Can consider no change as good/neutral
                else:
                    is_good = (diff > 0 and good_up) or (diff < 0 and not good_up)
            metrics.append({
                'label': label,
                'icon': icon,
                'unit': unit,
                'cur': cur,
                'diff': diff,
                'pct': pct,
                'arrow': arrow,
                'is_good': is_good
            })

        details[u.id] = {
            'meals': meals_data,
            'activity': activity_data,
            'metrics': metrics
        }

    return render_template(
        "admin_dashboard.html",
        users=users,
        statuses=statuses,
        details=details,
        today=today
    )


@app.route("/admin/user/create", methods=["GET", "POST"])
@admin_required
def admin_create_user():
    errors = []
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '').strip()
        date_str = request.form.get('date_of_birth', '').strip()
        is_trainer = 'is_trainer' in request.form

        if not name:
            errors.append("Имя обязательно.")
        if not email:
            errors.append("Email обязателен.")
        if not password or len(password) < 6:
            errors.append("Пароль обязателен и должен содержать минимум 6 символов.")
        if User.query.filter_by(email=email).first():
            errors.append("Этот email уже зарегистрирован.")

        date_of_birth = None
        if date_str:
            try:
                date_of_birth = datetime.strptime(date_str, "%Y-%m-%d").date()
                if date_of_birth > date.today():
                    errors.append("Дата рождения не может быть в будущем.")
            except ValueError:
                errors.append("Некорректный формат даты рождения.")

        if errors:
            return render_template('admin_create_user.html', errors=errors, form_data=request.form)

        hashed_pw = bcrypt.generate_password_hash(password).decode('utf-8')
        new_user = User(
            name=name,
            email=email,
            password=hashed_pw,
            date_of_birth=date_of_birth,
            is_trainer=is_trainer
        )
        db.session.add(new_user)
        db.session.commit()
        flash(f"Пользователь '{new_user.name}' успешно создан!", "success")
        return redirect(url_for("admin_dashboard"))
    return render_template("admin_create_user.html", errors=errors, form_data={})


@app.route("/admin/user/<int:user_id>")
@admin_required
def admin_user_detail(user_id):
    user = db.session.get(User, user_id)
    if not user:
        flash("Пользователь не найден", "error")
        return redirect(url_for("admin_dashboard"))

    # Fetch all historical data for the user
    meal_logs = MealLog.query.filter_by(user_id=user.id).order_by(MealLog.date.desc()).all()
    activities = Activity.query.filter_by(user_id=user.id).order_by(Activity.date.desc()).all()
    body_analyses = BodyAnalysis.query.filter_by(user_id=user.id).order_by(BodyAnalysis.timestamp.desc()).all()
    diets = Diet.query.filter_by(user_id=user.id).order_by(Diet.date.desc()).all()

    # Determine current status for today
    today = date.today()
    has_meal_today = any(m.date == today for m in meal_logs)
    has_activity_today = any(a.date == today for a in activities)

    # For charts: last 30 days activity
    last_30_days = [today - timedelta(days=i) for i in range(29, -1, -1)]
    activity_chart_labels = [d.strftime("%d.%m") for d in last_30_days]
    activity_steps_values = []
    activity_kcal_values = []

    activity_map = {a.date: a for a in activities if a.date in last_30_days}  # optimize lookup
    for d in last_30_days:
        activity_for_day = activity_map.get(d)
        activity_steps_values.append(activity_for_day.steps if activity_for_day else 0)
        activity_kcal_values.append(activity_for_day.active_kcal if activity_for_day else 0)

    # For charts: last 30 days diet (calories)
    diet_chart_labels = [d.strftime("%d.%m") for d in last_30_days]
    diet_kcal_values = []

    diet_map = {d.date: d for d in diets if d.date in last_30_days}  # optimize lookup
    for d in last_30_days:
        diet_for_day = diet_map.get(d)
        diet_kcal_values.append(diet_for_day.total_kcal if diet_for_day else 0)

    return render_template(
        "admin_user_detail.html",
        user=user,
        meal_logs=meal_logs,
        activities=activities,
        body_analyses=body_analyses,
        diets=diets,
        has_meal_today=has_meal_today,
        has_activity_today=has_activity_today,
        # Chart data
        activity_chart_labels=json.dumps(activity_chart_labels),
        activity_steps_values=json.dumps(activity_steps_values),
        activity_kcal_values=json.dumps(activity_kcal_values),
        diet_chart_labels=json.dumps(diet_chart_labels),
        diet_kcal_values=json.dumps(diet_kcal_values),
        today=today
    )


@app.route("/admin/user/<int:user_id>/edit", methods=["POST"])
@admin_required
def admin_user_edit(user_id):
    user = db.session.get(User, user_id)
    if not user:
        flash("Пользователь не найден", "error")
        return redirect(url_for("admin_dashboard"))

    original_email = user.email  # Keep original email for unique check

    user.name = request.form["name"].strip()
    user.email = request.form["email"].strip()
    user.is_trainer = 'is_trainer' in request.form  # Update trainer status

    dob = request.form.get("date_of_birth")
    user.date_of_birth = datetime.strptime(dob, "%Y-%m-%d").date() if dob else None

    # Handle password change if provided
    new_password = request.form.get("password")
    if new_password:
        if len(new_password) < 6:
            flash("Новый пароль должен быть не менее 6 символов.", "error")
            return redirect(url_for("admin_user_detail", user_id=user.id))
        user.password = bcrypt.generate_password_hash(new_password).decode('utf-8')

    # Check for duplicate email only if changed
    if user.email != original_email and User.query.filter_by(email=user.email).first():
        flash("Этот email уже занят другим пользователем.", "error")
        return redirect(url_for("admin_user_detail", user_id=user.id))

    # Handle avatar upload
    if 'avatar' in request.files:
        file = request.files['avatar']
        if file.filename != '':
            filename = secure_filename(file.filename)
            # You might want to delete the old avatar file here if it exists
            # os.remove(os.path.join(app.config['UPLOAD_FOLDER'], user.avatar))
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(file_path)
            user.avatar = filename

    try:
        db.session.commit()
        flash("Данные пользователя обновлены", "success")
    except IntegrityError:
        db.session.rollback()
        flash("Ошибка при обновлении пользователя. Возможно, email уже используется.", "error")

    return redirect(url_for("admin_user_detail", user_id=user.id))


@app.route("/admin/user/<int:user_id>/delete", methods=["POST"])
@admin_required
def admin_delete_user(user_id):
    user = db.session.get(User, user_id)
    if not user:
        flash("Пользователь не найден.", "error")
        return redirect(url_for("admin_dashboard"))

    if user.email == ADMIN_EMAIL:
        flash("Вы не можете удалить аккаунт администратора.", "error")
        return redirect(url_for("admin_dashboard"))

    try:
        # Cascade delete should handle related records (meals, activities, etc.)
        # Ensure your model relationships have `cascade='all, delete-orphan'` if you want related data to be deleted.
        # Otherwise, you would manually delete them here:
        # MealLog.query.filter_by(user_id=user.id).delete()
        # Activity.query.filter_by(user_id=user.id).delete()
        # BodyAnalysis.query.filter_by(user_id=user.id).delete()
        # Diet.query.filter_by(user_id=user.id).delete()
        # GroupMember.query.filter_by(user_id=user.id).delete()
        # GroupTask.query.filter_by(trainer_id=user.id).delete() # if they were trainers
        # GroupMessage.query.filter_by(user_id=user.id).delete()
        # MessageReaction.query.filter_by(user_id=user.id).delete()

        # If user owns a group, delete the group first or reassign
        if user.own_group:
            # Option 1: Delete the group
            db.session.delete(user.own_group)
            # Option 2: Reassign the group to admin (if desired)
            # user.own_group.trainer_id = <ADMIN_USER_ID>
            # flash("Группа, принадлежащая пользователю, была переназначена администратору (или удалена).", "info")

        db.session.delete(user)
        db.session.commit()
        flash(f"Пользователь '{user.name}' и все связанные данные удалены.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Ошибка при удалении пользователя: {e}", "error")

    return redirect(url_for("admin_dashboard"))


@app.route('/groups')
@login_required
def groups_list():
    user = get_current_user()
    # если тренер — показываем его группу (или кнопку создания)
    if user.is_trainer:
        return render_template('groups_list.html', group=user.own_group)
    # обычный пользователь — список всех групп
    groups = Group.query.all()
    return render_template('groups_list.html', groups=groups)


@app.route('/groups/new', methods=['GET', 'POST'])
@login_required
def create_group():
    user = get_current_user()
    if not user.is_trainer:
        abort(403)
    if user.own_group:
        flash("Вы уже являетесь тренером группы. Вы можете создать только одну группу.", "warning")
        return redirect(url_for('group_detail', group_id=user.own_group.id))
    if request.method == 'POST':
        name = request.form['name']
        description = request.form.get('description', '').strip()
        if not name:
            flash("Название группы обязательно!", "error")
            return render_template('group_new.html')

        group = Group(name=name, description=description, trainer=user)
        db.session.add(group)
        db.session.commit()
        flash(f"Группа '{group.name}' успешно создана!", "success")
        return redirect(url_for('group_detail', group_id=group.id))
    return render_template('group_new.html')


@app.route('/groups/<int:group_id>')
@login_required
def group_detail(group_id):
    group = Group.query.get_or_404(group_id)
    user = get_current_user()
    is_member = any(m.user_id == user.id for m in group.members)

    raw_messages = GroupMessage.query.filter_by(group_id=group.id).order_by(GroupMessage.timestamp.desc()).all()
    processed_messages = []
    last_sender_id = None
    for message in raw_messages:
        show_avatar = (message.user_id != last_sender_id)
        processed_messages.append({
            'id': message.id, 'group_id': message.group_id, 'user_id': message.user_id, 'user': message.user,
            'text': message.text, 'timestamp': message.timestamp, 'image_file': message.image_file,
            'reactions': message.reactions, 'show_avatar': show_avatar, 'is_current_user': (message.user_id == user.id)
        })
        last_sender_id = message.user_id
    all_posts = GroupTask.query.filter_by(group_id=group.id).order_by(GroupTask.created_at.desc()).all()

    group_member_stats = []
    if user.is_trainer and group.trainer_id == user.id:
        today = date.today()
        all_relevant_members = [m.user for m in group.members]
        if group.trainer not in all_relevant_members and group.trainer.email != ADMIN_EMAIL:
            all_relevant_members.append(group.trainer)

        for member_user in all_relevant_members:
            if member_user.email == ADMIN_EMAIL and not any(m.user_id == member_user.id for m in group.members):
                continue

            latest_analysis = BodyAnalysis.query.filter_by(user_id=member_user.id).order_by(
                BodyAnalysis.timestamp.desc()).first()

            fat_loss_progress = None
            if latest_analysis and member_user.fat_mass_goal and latest_analysis.fat_mass > member_user.fat_mass_goal:
                start_datetime = latest_analysis.timestamp

                meal_data = db.session.query(MealLog.date, func.sum(MealLog.calories)).filter(
                    MealLog.user_id == member_user.id, MealLog.date >= start_datetime.date()
                ).group_by(MealLog.date).all()
                meal_map = dict(meal_data)

                activity_data = db.session.query(Activity.date, Activity.active_kcal).filter(
                    Activity.user_id == member_user.id, Activity.date >= start_datetime.date()
                ).all()
                activity_map = dict(activity_data)

                member_metabolism = latest_analysis.metabolism or 0
                total_accumulated_deficit = 0
                delta_days = (today - start_datetime.date()).days

                if delta_days >= 0:
                    for i in range(delta_days + 1):
                        current_day = start_datetime.date() + timedelta(days=i)
                        consumed = meal_map.get(current_day, 0)
                        burned_active = activity_map.get(current_day, 0)

                        # --- ИЗМЕНЕНИЯ ЗДЕСЬ ---
                        if i == 0:  # Это день анализа
                            # Убираем калории, съеденные ДО замера
                            calories_before_analysis = db.session.query(func.sum(MealLog.calories)).filter(
                                MealLog.user_id == member_user.id,
                                MealLog.date == current_day,
                                MealLog.created_at < start_datetime
                            ).scalar() or 0
                            consumed -= calories_before_analysis
                            # Игнорируем активность за день замера, т.к. нет точного времени
                            burned_active = 0
                        # --- КОНЕЦ ИЗМЕНЕНИЙ ---

                        daily_deficit = (member_metabolism + burned_active) - consumed
                        if daily_deficit > 0:
                            total_accumulated_deficit += daily_deficit

                KCAL_PER_KG_FAT = 7700
                total_fat_to_lose_kg = latest_analysis.fat_mass - member_user.fat_mass_goal

                estimated_fat_burned_kg = min(total_accumulated_deficit / KCAL_PER_KG_FAT, total_fat_to_lose_kg)

                percentage = 0
                if total_fat_to_lose_kg > 0:
                    percentage = (estimated_fat_burned_kg / total_fat_to_lose_kg) * 100

                fat_loss_progress = {
                    'percentage': min(100, max(0, percentage)),
                    'initial_kg': latest_analysis.fat_mass,
                    'goal_kg': member_user.fat_mass_goal,
                    'current_kg': latest_analysis.fat_mass - estimated_fat_burned_kg
                }

            group_member_stats.append({
                'user': member_user,
                'fat_loss_progress': fat_loss_progress,
                'is_trainer_in_group': (member_user.id == group.trainer_id)
            })
        group_member_stats.sort(key=lambda x: (not x['is_trainer_in_group'], x['user'].name.lower()))

    return render_template('group_detail.html',
                           group=group,
                           is_member=is_member,
                           processed_messages=processed_messages,
                           group_member_stats=group_member_stats,
                           all_posts=all_posts)

@app.route('/group_message/<int:message_id>/react', methods=['POST'])
@login_required
def react_to_message(message_id):
    message = GroupMessage.query.get_or_404(message_id)
    user = get_current_user()

    existing_reaction = MessageReaction.query.filter_by(
        message_id=message_id,
        user_id=user.id
    ).first()

    user_reacted = False
    if existing_reaction:
        db.session.delete(existing_reaction)
    else:
        reaction = MessageReaction(message=message, user=user, reaction_type='👍')
        db.session.add(reaction)
        user_reacted = True

    db.session.commit()

    new_like_count = MessageReaction.query.filter_by(message_id=message_id).count()

    return jsonify({
        "success": True,
        "new_like_count": new_like_count,
        "user_reacted": user_reacted
    })


@app.route('/api/groups/<int:group_id>/messages')
@login_required
def get_group_messages(group_id):
    # Убедимся, что группа существует
    Group.query.get_or_404(group_id)
    user_id = get_current_user().id

    messages = GroupMessage.query.filter_by(group_id=group_id).order_by(GroupMessage.timestamp.asc()).all()

    # Собираем данные в нужный формат
    results = []
    for msg in messages:
        reactions_data = []
        user_has_reacted = False
        for reaction in msg.reactions:
            reactions_data.append({'user_id': reaction.user_id})
            if reaction.user_id == user_id:
                user_has_reacted = True

        results.append({
            "id": msg.id,
            "text": msg.text,
            "image_url": url_for('serve_uploaded_file', filename=msg.image_file) if msg.image_file else None,
            "user": {
                "name": msg.user.name,
                "avatar_url": url_for('serve_uploaded_file', filename=msg.user.avatar) if msg.user.avatar else url_for(
                    'static', filename='default-avatar.png')
            },
            "is_current_user": msg.user_id == user_id,
            "reactions_count": len(reactions_data),
            "current_user_reacted": user_has_reacted
        })

    return jsonify(results)

@app.route('/groups/<int:group_id>/tasks/new', methods=['POST'])
@login_required
def create_group_task(group_id):
    group = Group.query.get_or_404(group_id)
    user = get_current_user()

    # Only the group's trainer can create tasks/announcements
    if not (user.is_trainer and group.trainer_id == user.id):
        abort(403)

    title = request.form['title'].strip()
    description = request.form.get('description', '').strip()
    is_announcement = 'is_announcement' in request.form
    due_date_str = request.form.get('due_date')

    if not title:
        flash("Заголовок обязателен.", "error")
        return redirect(url_for('group_detail', group_id=group_id))

    due_date = None
    if due_date_str:
        try:
            due_date = datetime.strptime(due_date_str, '%Y-%m-%d').date()
        except ValueError:
            flash("Неверный формат даты. Используйте ГГГГ-ММ-ДД.", "error")
            return redirect(url_for('group_detail', group_id=group_id))

    task = GroupTask(
        group=group,
        trainer=user,
        title=title,
        description=description,
        is_announcement=is_announcement,
        due_date=due_date
    )
    db.session.add(task)
    db.session.commit()  # Сначала сохраняем задачу

    # --- НАЧАЛО НОВОГО КОДА ---
    try:
        # Собираем chat_id всех участников группы
        chat_ids = [member.user.telegram_chat_id for member in group.members if member.user.telegram_chat_id]

        if chat_ids:
            # Формируем сообщение
            task_type = "Объявление" if is_announcement else "Новая задача"
            message_text = f"🔔 **{task_type} от тренера {user.name}**\n\n**{title}**\n\n_{description}_"

            # URL вашего бота (нужно будет указать, когда бот будет на сервере)
            BOT_WEBHOOK_URL = os.getenv("BOT_WEBHOOK_URL")  # Например, https://your-bot-domain.com/notify
            BOT_SECRET_TOKEN = os.getenv("BOT_SECRET_TOKEN")  # Секретный токен для безопасности

            if BOT_WEBHOOK_URL and BOT_SECRET_TOKEN:
                payload = {
                    "chat_ids": chat_ids,
                    "message": message_text,
                    "secret": BOT_SECRET_TOKEN
                }
                # Отправляем запрос боту, не дожидаясь ответа
                print(f"INFO: Sending notification to bot at {BOT_WEBHOOK_URL} for {len(chat_ids)} users.")
                requests.post(BOT_WEBHOOK_URL, json=payload, timeout=2)
            else:
                print("WARNING: BOT_WEBHOOK_URL or BOT_SECRET_TOKEN not set in .env. Skipping notification.")

    except Exception as e:
        print(f"Failed to send notification to bot: {e}")
    # --- КОНЕЦ НОВОГО КОДА ---

    flash(f"{'Объявление' if is_announcement else 'Задача'} '{title}' успешно добавлено!", "success")
    return redirect(url_for('group_detail', group_id=group_id))


# Добавьте в app.py
@app.route('/api/user_progress/<int:chat_id>')
def get_user_progress(chat_id):
    user = User.query.filter_by(telegram_chat_id=str(chat_id)).first_or_404()

    analyses = BodyAnalysis.query.filter_by(user_id=user.id).order_by(BodyAnalysis.timestamp.desc()).limit(2).all()

    if len(analyses) == 0:
        return jsonify({"error": "Нет данных для сравнения"}), 404

    latest = analyses[0]
    previous = analyses[1] if len(analyses) > 1 else None

    def serialize(analysis):
        if not analysis: return None
        return {
            "date": analysis.timestamp.strftime('%d.%m.%Y'),
            "weight": analysis.weight,
            "fat_mass": analysis.fat_mass,
            "muscle_mass": analysis.muscle_mass
        }

    return jsonify({
        "latest": serialize(latest),
        "previous": serialize(previous)
    })

# Добавьте в app.py

@app.route('/api/meal_history/<int:chat_id>')
def get_meal_history(chat_id):
    user = User.query.filter_by(telegram_chat_id=str(chat_id)).first_or_404()
    page = request.args.get('page', 1, type=int)

    # Группируем приемы пищи по дням и считаем сумму калорий
    daily_meals = db.session.query(
        MealLog.date,
        func.sum(MealLog.calories).label('total_calories'),
        func.count(MealLog.id).label('meal_count')
    ).filter_by(user_id=user.id).group_by(MealLog.date).order_by(MealLog.date.desc()).paginate(page=page, per_page=5, error_out=False)

    return jsonify({
        "days": [
            {"date": d.date.strftime('%d.%m.%Y'), "total_calories": d.total_calories, "meal_count": d.meal_count}
            for d in daily_meals.items
        ],
        "has_next": daily_meals.has_next,
        "has_prev": daily_meals.has_prev,
        "page": page
    })

@app.route('/api/activity_history/<int:chat_id>')
def get_activity_history(chat_id):
    user = User.query.filter_by(telegram_chat_id=str(chat_id)).first_or_404()
    page = request.args.get('page', 1, type=int)

    daily_activity = Activity.query.filter_by(user_id=user.id).order_by(Activity.date.desc()).paginate(page=page, per_page=5, error_out=False)

    return jsonify({
        "days": [
            {"date": a.date.strftime('%d.%m.%Y'), "steps": a.steps, "active_kcal": a.active_kcal}
            for a in daily_activity.items
        ],
        "has_next": daily_activity.has_next,
        "has_prev": daily_activity.has_prev,
        "page": page
    })

@app.route('/groups/tasks/<int:task_id>/delete', methods=['POST'])
@login_required
def delete_group_task(task_id):
    task = GroupTask.query.get_or_404(task_id)
    user = get_current_user()

    # Only the trainer who created it (or group's trainer) can delete
    if not (user.is_trainer and task.trainer_id == user.id):
        abort(403)

    db.session.delete(task)
    db.session.commit()
    flash(f"{'Объявление' if task.is_announcement else 'Задача'} '{task.title}' удалено.", "info")
    return redirect(url_for('group_detail', group_id=task.group_id))


# Route for handling image uploads with chat messages
@app.route('/groups/<int:group_id>/message/image', methods=['POST'])
@login_required
def post_group_image_message(group_id):
    group = Group.query.get_or_404(group_id)
    user = get_current_user()
    is_member = any(m.user_id == user.id for m in group.members)

    if not (user.is_trainer and group.trainer_id == user.id or is_member):
        abort(403)

    text = request.form.get('text', '').strip()
    file = request.files.get('image')

    image_filename = None
    if file and file.filename != '':
        filename = secure_filename(file.filename)
        # ... (здесь может быть ваша логика проверки расширений)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        resize_image(filepath, CHAT_IMAGE_MAX_SIZE)
        image_filename = filename

    if not text and not image_filename:
        return jsonify({"error": "Сообщение не может быть пустым"}), 400

    msg = GroupMessage(group=group, user=user, text=text, image_file=image_filename)
    db.session.add(msg)
    db.session.commit()

    # Вместо редиректа возвращаем JSON с данными нового сообщения
    return jsonify({
        "success": True,
        "message": {
            "id": msg.id,
            "text": msg.text,
            "image_url": url_for('serve_uploaded_file', filename=msg.image_file) if msg.image_file else None,
            "user": {
                "name": user.name,
                "avatar_url": url_for('serve_uploaded_file', filename=user.avatar) if user.avatar else url_for('static', filename='default-avatar.png')
            },
            "is_current_user": True,
            "reactions": []
        }
    })
@app.route('/groups/<int:group_id>/join', methods=['POST'])
@login_required
def join_group(group_id):
    group = Group.query.get_or_404(group_id)
    user = get_current_user()

    # Prevent joining if already a member
    if GroupMember.query.filter_by(group_id=group.id, user_id=user.id).first():
        flash("Вы уже состоите в этой группе.", "info")
        return redirect(url_for('group_detail', group_id=group.id))

    # Prevent trainer from joining another group as a member
    if user.is_trainer and user.own_group and user.own_group.id != group_id:
        flash("Как тренер, вы не можете присоединиться к другой группе.", "error")
        return redirect(url_for('groups_list'))

    member = GroupMember(group=group, user=user)
    db.session.add(member)
    db.session.commit()
    flash(f"Вы успешно присоединились к группе '{group.name}'!", "success")
    return redirect(url_for('group_detail', group_id=group.id))


@app.route('/groups/<int:group_id>/leave', methods=['POST'])
@login_required
def leave_group(group_id):
    group = Group.query.get_or_404(group_id)
    user = get_current_user()

    member = GroupMember.query.filter_by(group_id=group.id, user_id=user.id).first()
    if not member:
        flash("Вы не состоите в этой группе.", "info")
        return redirect(url_for('group_detail', group_id=group_id))

    # Prevent trainers from leaving their own group if they are the trainer
    if user.is_trainer and group.trainer_id == user.id:
        flash("Как тренер, вы не можете покинуть свою собственную группу.", "error")
        return redirect(url_for('group_detail', group_id=group_id))

    db.session.delete(member)
    db.session.commit()
    flash(f"Вы покинули группу '{group.name}'.", "success")
    return redirect(url_for('groups_list'))


# --- Admin Group Management ---

@app.route("/admin/groups")
@admin_required
def admin_groups_list():
    groups = Group.query.all()
    return render_template("admin_groups_list.html", groups=groups)


@app.route("/admin/groups/<int:group_id>/edit", methods=["GET", "POST"])
@admin_required
def admin_edit_group(group_id):
    group = db.session.get(Group, group_id)
    if not group:
        flash("Группа не найдена.", "error")
        return redirect(url_for("admin_groups_list"))

    trainers = User.query.filter_by(is_trainer=True).all()  # For assigning new trainer

    if request.method == "POST":
        group.name = request.form['name'].strip()
        group.description = request.form.get('description', '').strip()
        new_trainer_id = request.form.get('trainer_id')

        # Check for unique group name (if you want to enforce this)
        # existing_group = Group.query.filter(Group.name == group.name, Group.id != group_id).first()
        # if existing_group:
        #     flash("Группа с таким названием уже существует.", "error")
        #     return render_template("admin_edit_group.html", group=group, trainers=trainers)

        if new_trainer_id and int(new_trainer_id) != group.trainer_id:
            # Check if new trainer already owns a group
            potential_trainer = db.session.get(User, int(new_trainer_id))
            if potential_trainer and potential_trainer.own_group and potential_trainer.own_group.id != group_id:
                flash(f"Тренер {potential_trainer.name} уже руководит другой группой.", "error")
                return render_template("admin_edit_group.html", group=group, trainers=trainers)
            group.trainer_id = int(new_trainer_id)
            group.trainer.is_trainer = True  # Ensure new trainer is marked as trainer

        db.session.commit()
        flash("Группа успешно обновлена.", "success")
        return redirect(url_for("admin_groups_list"))

    return render_template("admin_edit_group.html", group=group, trainers=trainers)


@app.route("/admin/groups/<int:group_id>/delete", methods=["POST"])
@admin_required
def admin_delete_group(group_id):
    group = db.session.get(Group, group_id)
    if not group:
        flash("Группа не найдена.", "error")
        return redirect(url_for("admin_groups_list"))

    try:
        db.session.delete(group)  # Cascade will delete members, messages, tasks
        db.session.commit()
        flash(f"Группа '{group.name}' и все связанные данные удалены.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Ошибка при удалении группы: {e}", "error")
    return redirect(url_for("admin_groups_list"))


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)