import os
from datetime import datetime, date, timedelta
import base64
import json
import requests
from flask import Flask, render_template, request, redirect, session, jsonify, url_for, flash, abort, send_from_directory
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
from PIL import Image # Import Pillow

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "supersecret")
app.jinja_env.globals.update(getattr=getattr)

app.config['UPLOAD_FOLDER'] = 'uploads'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# --- Image Resizing Configuration ---
CHAT_IMAGE_MAX_SIZE = (200, 200) # Max width and height for chat images

def resize_image(filepath, max_size):
    """Resizes an image and saves it back to the same path."""
    try:
        with Image.open(filepath) as img:
            print(f"DEBUG: Resizing image: {filepath}, original size: {img.size}")
            img.thumbnail(max_size, Image.Resampling.LANCZOS)
            img.save(filepath) # Overwrites the original
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
            abort(403) # Forbidden
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
    is_trainer = db.Column(db.Boolean, default=False, nullable=False)
    # Сохраняем только имя файла аватарки
    avatar = db.Column(db.String(200), nullable=True)

    analysis_comment = db.Column(db.Text)
    telegram_chat_id = db.Column(db.String(50), nullable=True)
    telegram_code = db.Column(db.String(10), nullable=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow)


class Group(db.Model):
    id          = db.Column(db.Integer, primary_key=True)
    name        = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text, nullable=True)  # «дивиз» группы
    trainer_id  = db.Column(db.Integer, db.ForeignKey('user.id'), unique=True, nullable=False)
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)

    trainer     = db.relationship('User', backref=db.backref('own_group', uselist=False))
    members     = db.relationship('GroupMember', back_populates='group', cascade='all, delete-orphan')
    messages    = db.relationship('GroupMessage', back_populates='group', cascade='all, delete-orphan')


class GroupMember(db.Model):
    id        = db.Column(db.Integer, primary_key=True)
    group_id  = db.Column(db.Integer, db.ForeignKey('group.id'), nullable=False)
    user_id   = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    joined_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('group_id', 'user_id', name='uq_group_user'),
    )

    group = db.relationship('Group', back_populates='members')
    user  = db.relationship('User', backref=db.backref('groups', lazy='dynamic'))


class GroupMessage(db.Model):
    id        = db.Column(db.Integer, primary_key=True)
    group_id  = db.Column(db.Integer, db.ForeignKey('group.id'), nullable=False)
    user_id   = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    text      = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    # New: Add support for image messages (stores filename)
    image_file = db.Column(db.String(200), nullable=True)

    group = db.relationship('Group', back_populates='messages')
    user  = db.relationship('User')
    # New: Relationship for reactions
    reactions = db.relationship('MessageReaction', back_populates='message', cascade='all, delete-orphan')


class MessageReaction(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    message_id = db.Column(db.Integer, db.ForeignKey('group_message.id'), nullable=False)
    user_id    = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    # For simplicity, let's just use 'like' or an emoji string
    reaction_type = db.Column(db.String(20), nullable=False, default='👍')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('message_id', 'user_id', name='uq_message_user_reaction'),
    )
    message = db.relationship('GroupMessage', back_populates='reactions')
    user = db.relationship('User')


class GroupTask(db.Model):
    id          = db.Column(db.Integer, primary_key=True)
    group_id    = db.Column(db.Integer, db.ForeignKey('group.id'), nullable=False)
    trainer_id  = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    title       = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=True)
    is_announcement = db.Column(db.Boolean, default=False, nullable=False) # True for announcements, False for tasks
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)
    due_date    = db.Column(db.Date, nullable=True) # Optional due date for tasks

    group = db.relationship('Group', backref=db.backref('tasks', cascade='all, delete-orphan', lazy='dynamic'))
    trainer = db.relationship('User')


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

    return dict(
        get_bmi_category=get_bmi_category,
        calculate_age=calculate_age,     # <-- теперь в шаблоне доступна
        today=date.today(),              # <-- и переменная today
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


# ... (existing imports) ...

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

    # Дополнительно для меню 'Метрики'
    total_meals = db.session.query(func.sum(MealLog.calories)) \
        .filter_by(user_id=user.id, date=date.today()) \
        .scalar() or 0
    today_meals = MealLog.query \
        .filter_by(user_id=user.id, date=date.today()) \
        .all()
    metabolism = user.metabolism or 0
    active_kcal   = today_activity.active_kcal  if today_activity else None
    steps         = today_activity.steps        if today_activity else None
    distance_km   = today_activity.distance_km  if today_activity else None
    resting_kcal  = today_activity.resting_kcal if today_activity else None

    missing_meals    = (total_meals == 0)
    missing_activity = (active_kcal is None)

    deficit = None
    if not missing_meals and not missing_activity and metabolism is not None:
        deficit = (metabolism + (active_kcal or 0)) - total_meals

    # --- NEW: Fetch user's group memberships ---
    user_memberships = GroupMember.query.filter_by(user_id=user.id).all()
    # If the user is a trainer and owns a group, that's their primary group.
    # Otherwise, they might be a member of other groups.
    user_joined_group = None
    if user.own_group:
        user_joined_group = user.own_group
    elif user_memberships:
        # For simplicity, if a user is a member of multiple groups,
        # you might choose to show the first one, or modify this to show a list.
        # For now, let's just pick the first one they are a member of.
        user_joined_group = user_memberships[0].group


    return render_template(
        'profile.html',
        user=user,
        age=age,
        diet=diet,
        today_activity=today_activity,
        latest_analysis=latest,
        previous_analysis=previous,
        # Данные для метрик
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
        # --- NEW: Pass user_joined_group ---
        user_joined_group=user_joined_group
    )

# ... (rest of your existing code) ...

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')

@app.route('/upload_analysis', methods=['POST'])
def upload_analysis():
    file = request.files.get('file')
    user_id = session.get('user_id')
    if not file or not user_id:
        flash("Файл не загружен или пользователь не авторизован.", "error")
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
            flash(f"Недостаточно данных в анализе: {missing}. Пожалуйста, загрузите более четкое изображение или заполните данные вручную.", "error")
            return redirect('/profile') # Или на страницу с ручным вводом

        session['temp_analysis'] = result
        return render_template('confirm_analysis.html', data=result)

    except Exception as e:
        flash(f"Ошибка анализа изображения: {e}", "error")
        return redirect('/profile')


@app.route("/meals", methods=["GET", "POST"])
@login_required
def meals():
    user = get_current_user()
    if request.method == "POST":
        meal_type = request.form["meal_type"]
        # Эти поля больше не используются напрямую, т.к. данные берутся из GPT
        # name = request.form["name"]
        # grams = float(request.form["grams"])
        # kcal = float(request.form["kcal"])

        # Убран старый код добавления, т.к. теперь используется /add_meal
        flash("Используйте форму 'Добавить приём пищи' или 'Загрузить фото' на странице профиля.", "info")
        return redirect("/profile")

    today_meals = MealLog.query.filter_by(user_id=user.id, date=datetime.utcnow().date()).all()
    grouped = {
        "breakfast": [],
        "lunch": [],
        "dinner": [],
        "snack": []
    }
    for m in today_meals:
        grouped[m.meal_type].append(m)

    return render_template("profile.html", user=user, meals=grouped, tab='meals')


@app.route('/confirm_analysis', methods=['POST'])
def confirm_analysis():
    user_id = session.get('user_id')
    if not user_id or 'temp_analysis' not in session:
        flash("Нет данных для подтверждения анализа.", "error")
        return redirect('/profile')

    data = session.pop('temp_analysis')
    user = db.session.get(User, user_id)

    # 1) Сохраняем предыдущие в историю, если уже были данные
    if user.height is not None: # Проверяем, были ли ранее загружены данные
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

    user.analysis_comment = data.get("analysis") # Это поле не из анализа, если оно было в GPT
    user.updated_at = datetime.utcnow()
    db.session.commit()

    # 3) Сохраняем новый замер в историю (дублирование для удобства истории)
    # Это создает новую запись в BodyAnalysis для текущих данных пользователя
    # после того, как поля пользователя были обновлены.
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

    flash("Данные анализа тела успешно обновлены!", "success")
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

    # Проверка наличия всех необходимых данных для генерации диеты
    if None in [user.height, user.weight, user.muscle_mass, user.fat_mass, user.metabolism]:
        flash("Пожалуйста, заполните данные анализа тела (рост, вес, мышечная масса, жировая масса, метаболизм) для генерации диеты.", "warning")
        return jsonify({"redirect": "/profile"}) # Перенаправляем на профиль с предупреждением

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

    meal = MealLog(
        user_id=user_id,
        date=date.today(),
        meal_type=request.form['meal_type'],
        calories=int(request.form['calories']),
        protein=float(request.form['protein']),
        fat=float(request.form['fat']),
        carbs=float(request.form['carbs']),
        analysis=request.form.get('analysis', '')
    )

    try:
        db.session.add(meal)
        db.session.commit()
        flash(f"Приём пищи '{meal.meal_type}' успешно добавлен!", "success")
    except IntegrityError:
        db.session.rollback()
        flash(f"Приём пищи типа '{meal.meal_type}' уже добавлен на сегодня. Отредактируйте существующий.", "error")

    return redirect('/profile')


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
    if diet and diet.date == date.today(): # Удаляем только диету за сегодня
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
        activity_for_day = next((a for a in activities if a.date == day), None) # Переименовано, чтобы избежать конфликта
        chart_data['steps'].append(activity_for_day.steps if activity_for_day else 0)
        chart_data['calories'].append(activity_for_day.active_kcal if activity_for_day else 0)
        chart_data['heart_rate'].append(activity_for_day.heart_rate_avg if activity_for_day else 0)

    # Здесь возвращаем activity.html, если он есть, или используем profile.html с нужным табом
    return render_template(
        'profile.html',
        user=user,
        today_activity=today_activity,
        chart_data=chart_data,
        tab='activity' # Указываем активный таб
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

@app.route('/analyze_meal_photo', methods=['POST'])
@login_required
def analyze_meal_photo():
    file     = request.files.get('file')
    meal_type= request.form.get('meal_type')
    if not file or not meal_type:
        flash('Не передан файл или тип приёма пищи.', 'error')
        return redirect('/profile')

    filename = secure_filename(file.filename)
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(filepath)

    # кодируем в base64
    with open(filepath, 'rb') as f:
        b64 = base64.b64encode(f.read()).decode('utf-8')

    # формируем запрос в GPT
    system = (
      "Ты — профессиональный диетолог и эксперт по анализу блюд. "
      "На входе — фото еды. Определи:"
      "\n- Название блюда "
      "- Калорийность (ккал) "
      "- Белки (г), Жиры (г), Углеводы (г) "
      "- Дай короткий текстовый анализ блюда."
      "\nВерни JSON строго в формате:"
      '{"name": "...", "calories": 0, "protein": 0.0, "fat": 0.0, "carbs": 0.0, "analysis": "..."}'
    )

    try:
        response = client.chat.completions.create(
          model="gpt-4o",
          messages=[
            {"role":"system","content":system},
            {"role":"user", "content":[
               {"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{b64}"}},
               {"type":"text","text":"Проанализируй блюдо на фото."}
            ]}
          ],
          max_tokens=500
        )

        content = response.choices[0].message.content.strip()
        # убираем ```json если есть
        if '```json' in content:
            content = content.split('```json')[1].split('```')[0]
        data = json.loads(content)

        # Сохраняем данные в MealLog
        meal = MealLog(
            user_id=session.get('user_id'),
            date=date.today(),
            meal_type=meal_type,
            calories=int(data.get('calories', 0)),
            protein=float(data.get('protein', 0.0)),
            fat=float(data.get('fat', 0.0)),
            carbs=float(data.get('carbs', 0.0)),
            analysis=data.get('analysis', '')
        )
        db.session.add(meal)
        db.session.commit()
        flash(f"Приём пищи '{meal_type}' успешно добавлен по фото!", "success")
        return redirect('/profile')

    except Exception as e:
        flash(f"Ошибка анализа фото или сохранения данных: {e}", "error")
        return redirect('/profile')


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
    if not missing_meals and not missing_activity and metabolism is not None:
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
        chart_data=None, # Отключаем для этой страницы, если не нужно

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
        tab='metrics' # Указываем активный таб
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
@admin_required # Защита маршрута для админа
def admin_dashboard():
    users = User.query.order_by(User.updated_at.desc()).all()
    today = date.today()

    statuses = {}
    details  = {}

    # Определяем тот же список полей, что и в profile.html
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
        # статусы
        has_meal     = MealLog.query.filter_by(user_id=u.id, date=today).count() > 0
        has_activity = Activity.query.filter_by(user_id=u.id, date=today).count() > 0
        statuses[u.id] = {'meal': has_meal, 'activity': has_activity}

        # приемы пищи
        meals = MealLog.query.filter_by(user_id=u.id, date=today).all()
        meals_data = [{
            'type': m.meal_type,
            'cal':  m.calories,
            'prot': m.protein,
            'fat':  m.fat,
            'carbs':m.carbs
        } for m in meals]

        # активность
        act = Activity.query.filter_by(user_id=u.id, date=today).first()
        activity_data = None
        if act:
            activity_data = {
                'steps':        act.steps,
                'active_kcal':  act.active_kcal,
                'resting_kcal': act.resting_kcal,
                'distance_km':  act.distance_km,
                'hr_avg':       act.heart_rate_avg
            }

        # анализ тела
        last = BodyAnalysis.query.filter_by(user_id=u.id)\
                                  .order_by(BodyAnalysis.timestamp.desc()).first()
        prev = BodyAnalysis.query.filter_by(user_id=u.id)\
                                  .order_by(BodyAnalysis.timestamp.desc()).offset(1).first()

        # собираем массив метрик с дельтами
        metrics = []
        for label, field, icon, unit, good_up in metrics_def:
            cur = getattr(last, field, None)
            pr  = getattr(prev, field, None)
            diff = pct = arrow = None
            is_good = None
            if cur is not None and pr is not None:
                diff = cur - pr
                if pr != 0:
                    pct = diff / pr * 100
                arrow = '↑' if diff > 0 else '↓' if diff < 0 else '' # Добавлено для отображения стрелки даже при отсутствии is_good
                is_good = (diff > 0 and good_up) or (diff < 0 and not good_up)
            metrics.append({
                'label':    label,
                'icon':     icon,
                'unit':     unit,
                'cur':      cur,
                'diff':     diff,
                'pct':      pct,
                'arrow':    arrow,
                'is_good':  is_good
            })

        details[u.id] = {
            'meals':    meals_data,
            'activity': activity_data,
            'metrics':  metrics
        }

    return render_template(
        "admin_dashboard.html",
        users=users,
        statuses=statuses,
        details=details,
        today=today
    )

@app.route("/admin/user/<int:user_id>")
@admin_required
def admin_user_detail(user_id):
    user = db.session.get(User, user_id)
    if not user:
        flash("Пользователь не найден", "error")
        return redirect(url_for("admin_dashboard"))

    today = date.today()
    has_meal     = MealLog.query.filter_by(user_id=user.id,     date=today).count() > 0
    has_activity = Activity.query.filter_by(user_id=user.id, date=today).count() > 0

    meals = MealLog.query\
            .filter_by(user_id=user.id)\
            .order_by(MealLog.date.desc())\
            .all()

    return render_template(
        "admin_user.html",
        user=user,
        meals=meals,
        has_meal=has_meal,
        has_activity=has_activity
    )


@app.route("/admin/user/<int:user_id>/edit", methods=["POST"])
@admin_required
def admin_user_edit(user_id):
    user = db.session.get(User, user_id)
    if not user:
        flash("Пользователь не найден", "error")
        return redirect(url_for("admin_dashboard"))

    user.name = request.form["name"].strip()
    user.email = request.form["email"].strip()
    dob = request.form.get("date_of_birth")
    user.date_of_birth = datetime.strptime(dob, "%Y-%m-%d").date() if dob else None

    # Обработка загрузки аватарки
    if 'avatar' in request.files:
        file = request.files['avatar']
        if file.filename != '':
            filename = secure_filename(file.filename)
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(file_path)
            user.avatar = filename # Сохраняем только имя файла в БД

    db.session.commit()
    flash("Данные пользователя обновлены", "success")
    return redirect(url_for("admin_user_detail", user_id=user.id))

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
        name        = request.form['name']
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
    user  = get_current_user()
    is_member = any(m.user_id == user.id for m in group.members)

    # Process messages for grouping in the template
    raw_messages = GroupMessage.query.filter_by(group_id=group.id).order_by(GroupMessage.timestamp.desc()).all()
    processed_messages = []
    last_sender_id = None

    for message in raw_messages:
        show_avatar = (message.user_id != last_sender_id)
        processed_messages.append({
            'id': message.id,
            'group_id': message.group_id,
            'user_id': message.user_id,
            'user': message.user, # Eagerly load user for template
            'text': message.text,
            'timestamp': message.timestamp,
            'image_file': message.image_file,
            'reactions': message.reactions, # Eagerly load reactions
            'show_avatar': show_avatar,
            'is_current_user': (message.user_id == user.id)
        })
        last_sender_id = message.user_id

    # Fetch tasks and announcements
    tasks = GroupTask.query.filter_by(group_id=group.id, is_announcement=False).order_by(GroupTask.created_at.desc()).all()
    announcements = GroupTask.query.filter_by(group_id=group.id, is_announcement=True).order_by(GroupTask.created_at.desc()).all()


    group_member_stats = []
    if user.is_trainer and group.trainer_id == user.id:
        today = date.today()
        # Include trainer in stats if they are a member of their own group (optional, but consistent)
        all_relevant_members = [m.user for m in group.members]
        if group.trainer not in all_relevant_members and group.trainer.email != ADMIN_EMAIL:
             all_relevant_members.append(group.trainer)

        for member_user in all_relevant_members:
            # Skip admin user from stats if they are not part of the group formally
            if member_user.email == ADMIN_EMAIL and not any(m.user_id == member_user.id for m in group.members):
                continue

            # Check if the user has logged meals today
            has_meals_today = MealLog.query.filter_by(user_id=member_user.id, date=today).count() > 0

            # Calculate total calorie intake from meals for today
            total_meals_kcal = db.session.query(func.sum(MealLog.calories)) \
                .filter_by(user_id=member_user.id, date=today) \
                .scalar() or 0

            # Get today's activity for active calories
            member_activity = Activity.query.filter_by(user_id=member_user.id, date=today).first()
            active_kcal = member_activity.active_kcal if member_activity else 0

            deficit = None
            # Ensure the user has a basal metabolism value to calculate deficit
            if member_user.metabolism is not None:
                deficit = (member_user.metabolism + active_kcal) - total_meals_kcal

            group_member_stats.append({
                'user': member_user,
                'has_meals_today': has_meals_today,
                'deficit': deficit,
                'is_trainer_in_group': (member_user.id == group.trainer_id) # Flag for template
            })
        # Sort stats: trainer first, then by name or a key metric
        group_member_stats.sort(key=lambda x: (not x['is_trainer_in_group'], x['user'].name.lower()))


    return render_template('group_detail.html',
                           group=group,
                           is_member=is_member,
                           processed_messages=processed_messages, # Pass processed messages
                           group_member_stats=group_member_stats,
                           tasks=tasks,
                           announcements=announcements)


@app.route('/group_message/<int:message_id>/react', methods=['POST'])
@login_required
def react_to_message(message_id):
    message = GroupMessage.query.get_or_404(message_id)
    user = get_current_user()

    # Check if user already reacted to this message
    existing_reaction = MessageReaction.query.filter_by(
        message_id=message_id,
        user_id=user.id
    ).first()

    if existing_reaction:
        # If already reacted, remove the reaction (toggle)
        db.session.delete(existing_reaction)
        db.session.commit()
        flash("Реакция удалена.", "info")
    else:
        # Otherwise, add a new reaction
        reaction = MessageReaction(message=message, user=user, reaction_type='👍') # Default to thumbs up
        db.session.add(reaction)
        db.session.commit()
        flash("Реакция добавлена!", "success")

    return redirect(url_for('group_detail', group_id=message.group_id))


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
    db.session.commit()
    flash(f"{'Объявление' if is_announcement else 'Задача'} '{title}' успешно добавлено!", "success")
    return redirect(url_for('group_detail', group_id=group_id))


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

# ... (your existing imports and code) ...

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
        return redirect(url_for('group_detail', group_id=group.id))

    # Prevent trainers from leaving their own group if they are the trainer
    if user.is_trainer and group.trainer_id == user.id:
        flash("Как тренер, вы не можете покинуть свою собственную группу.", "error")
        return redirect(url_for('group_detail', group_id=group.id))

    db.session.delete(member)
    db.session.commit()
    flash(f"Вы покинули группу '{group.name}'.", "success")
    return redirect(url_for('groups_list'))

# ... (rest of your existing code) ...

# Route for handling image uploads with chat messages
@app.route('/groups/<int:group_id>/message/image', methods=['POST'])
@login_required
def post_group_image_message(group_id):
    group = Group.query.get_or_404(group_id)
    user = get_current_user()

    if not (user.is_trainer and group.trainer_id == user.id or any(m.user_id == user.id for m in group.members)):
        abort(403)

    text = request.form.get('text', '').strip()
    file = request.files.get('image') # Assuming input name is 'image'

    image_filename = None
    if file and file.filename != '':
        filename = secure_filename(file.filename)
        allowed_extensions = {'png', 'jpg', 'jpeg', 'gif'}
        if '.' in filename and filename.rsplit('.', 1)[1].lower() in allowed_extensions:
            image_filename = filename
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)
            print(f"DEBUG: Saved original image to {filepath}")
            print(f"DEBUG: Original file size: {os.path.getsize(filepath)} bytes")

            resize_image(filepath, CHAT_IMAGE_MAX_SIZE)  # Resize the image
            print(f"DEBUG: After resizing, file size: {os.path.getsize(filepath)} bytes")  # Check size AFTER resizing
        else:
            flash("Неподдерживаемый формат файла. Разрешены: png, jpg, jpeg, gif.", "error")
            return redirect(url_for('group_detail', group_id=group_id))

    if text or image_filename:
        msg = GroupMessage(group=group, user=user, text=text, image_file=image_filename)
        db.session.add(msg)
        db.session.commit()
        flash("Сообщение отправлено!", "success")
    else:
        flash("Сообщение не может быть пустым (текст или изображение).", "warning")

    return redirect(url_for('group_detail', group_id=group_id))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)