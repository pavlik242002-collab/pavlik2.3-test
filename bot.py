from __future__ import annotations

import os
import logging
import requests
import json
import re
from typing import Dict, List, Any
from dotenv import load_dotenv
from telegram import ReplyKeyboardMarkup, ReplyKeyboardRemove, InlineKeyboardMarkup, InlineKeyboardButton, Update
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from telegram import InputFile
from urllib.parse import quote
from openai import OpenAI
import psycopg2

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('bot.log')
    ]
)
logger = logging.getLogger(__name__)

# Загрузка переменных окружения
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
YANDEX_TOKEN = os.getenv("YANDEX_TOKEN")
XAI_TOKEN = os.getenv("XAI_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
XAI_MODEL = os.getenv("XAI_MODEL", "grok-3")  # Модель по умолчанию

# Проверка токенов и DATABASE_URL
if not all([TELEGRAM_TOKEN, YANDEX_TOKEN, XAI_TOKEN, DATABASE_URL]):
    logger.error("Токены или DATABASE_URL не найдены в .env файле!")
    raise ValueError("Укажите TELEGRAM_TOKEN, YANDEX_TOKEN, XAI_TOKEN, DATABASE_URL в .env")

# Подключение к Postgres
try:
    conn = psycopg2.connect(DATABASE_URL)
    logger.info("Подключение к Postgres успешно.")
except Exception as e:
    logger.error(f"Ошибка подключения к Postgres: {str(e)}")
    raise ValueError("Не удалось подключиться к базе данных.")

# Инициализация клиента OpenAI
client = OpenAI(
    base_url="https://api.x.ai/v1",
    api_key=XAI_TOKEN,
)

# Инициализация таблиц в PostgreSQL
def init_db(conn):
    """Создаёт таблицы в базе данных, если они не существуют, сохраняя существующие данные."""
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_name = 'allowed_admins'
                );
            """)
            if not cur.fetchone()[0]:
                cur.execute("""
                    CREATE TABLE allowed_admins (
                        id BIGINT NOT NULL PRIMARY KEY
                    );
                    INSERT INTO allowed_admins (id) VALUES (6909708460) ON CONFLICT DO NOTHING;
                """)
                logger.info("Таблица allowed_admins создана.")
            else:
                logger.info("Таблица allowed_admins уже существует.")

            cur.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_name = 'allowed_users'
                );
            """)
            if not cur.fetchone()[0]:
                cur.execute("""
                    CREATE TABLE allowed_users (
                        id BIGINT NOT NULL PRIMARY KEY
                    );
                """)
                logger.info("Таблица allowed_users создана.")
            else:
                logger.info("Таблица allowed_users уже существует.")

            cur.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_name = 'user_profiles'
                );
            """)
            if not cur.fetchone()[0]:
                cur.execute("""
                    CREATE TABLE user_profiles (
                        user_id BIGINT NOT NULL PRIMARY KEY,
                        fio TEXT,
                        name TEXT,
                        region TEXT
                    );
                """)
                logger.info("Таблица user_profiles создана.")
            else:
                logger.info("Таблица user_profiles уже существует.")

            cur.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_name = 'request_logs'
                );
            """)
            if not cur.fetchone()[0]:
                cur.execute("""
                    CREATE TABLE request_logs (
                        id SERIAL PRIMARY KEY,
                        user_id BIGINT NOT NULL,
                        request_text TEXT NOT NULL,
                        response_text TEXT,
                        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                """)
                logger.info("Таблица request_logs создана.")
            else:
                cur.execute("""
                    SELECT EXISTS (
                        SELECT FROM information_schema.columns 
                        WHERE table_name = 'request_logs' AND column_name = 'request_text'
                    );
                """)
                if not cur.fetchone()[0]:
                    cur.execute("ALTER TABLE request_logs ADD COLUMN request_text TEXT NOT NULL;")
                    logger.info("Добавлен столбец request_text в таблицу request_logs.")
                logger.info("Таблица request_logs уже существует.")

            conn.commit()
            logger.info("Все таблицы проверены и созданы при необходимости.")
    except Exception as e:
        logger.error(f"Ошибка при инициализации базы данных: {str(e)}")
        conn.rollback()
        raise

init_db(conn)

# Словарь федеральных округов
FEDERAL_DISTRICTS = {
    "Центральный федеральный округ": [
        "Белгородская область", "Брянская область", "Владимирская область", "Воронежская область",
        "Ивановская область", "Калужская область", "Костромская область", "Курская область",
        "Липецкая область", "Московская область", "Орловская область", "Рязанская область",
        "Смоленская область", "Тамбовская область", "Тверская область", "Тульская область",
        "Ярославская область", "Москва"
    ],
    "Северо-Западный федеральный округ": [
        "Республика Карелия", "Республика Коми", "Архангельская область", "Вологодская область",
        "Ленинградская область", "Мурманская область", "Новгородская область", "Псковская область",
        "Калининградская область", "Ненецкий автономный округ", "Санкт-Петербург"
    ],
    "Южный федеральный округ": [
        "Республика Адыгея", "Республика Калмыкия", "Республика Крым", "Краснодарский край",
        "Астраханская область", "Волгоградская область", "Ростовская область", "Севастополь"
    ],
    "Северо-Кавказский федеральный округ": [
        "Республика Дагестан", "Республика Ингушетия", "Кабардино-Балкарская Республика",
        "Карачаево-Черкесская Республика", "Республика Северная Осетия — Алания",
        "Чеченская Республика", "Ставропольский край"
    ],
    "Приволжский федеральный округ": [
        "Республика Башкортостан", "Республика Марий Эл", "Республика Мордовия", "Республика Татарстан",
        "Удмуртская Республика", "Чувашская Республика", "Кировская область", "Нижегородская область",
        "Оренбургская область", "Пензенская область", "Пермский край", "Самарская область",
        "Саратовская область", "Ульяновская область"
    ],
    "Уральский федеральный округ": [
        "Курганская область", "Свердловская область", "Тюменская область", "Ханты-Мансийский автономный округ — Югра",
        "Челябинская область", "Ямало-Ненецкий автономный округ"
    ],
    "Сибирский федеральный округ": [
        "Республика Алтай", "Республика Тыва", "Республика Хакасия", "Алтайский край",
        "Красноярский край", "Иркутская область", "Кемеровская область", "Новосибирская область",
        "Омская область", "Томская область"
    ],
    "Дальневосточный федеральный округ": [
        "Республика Бурятия", "Республика Саха (Якутия)", "Забайкальский край", "Камчатский край",
        "Приморский край", "Хабаровский край", "Амурская область", "Магаданская область",
        "Сахалинская область", "Еврейская автономная область", "Чукотский автономный округ"
    ]
}

# Функции для работы с администраторами
def load_allowed_admins() -> List[int]:
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM allowed_admins")
            admins = [row[0] for row in cur.fetchall()]
            logger.info(f"Загружено {len(admins)} администраторов")
            if not admins:
                cur.execute("INSERT INTO allowed_admins (id) VALUES (%s) ON CONFLICT DO NOTHING", (6909708460,))
                conn.commit()
                admins = [6909708460]
            return admins
    except Exception as e:
        logger.error(f"Ошибка при загрузке allowed_admins: {str(e)}")
        conn.rollback()
        return [6909708460]

def save_allowed_admins(allowed_admins: List[int]) -> None:
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM allowed_admins")
            for admin_id in allowed_admins:
                cur.execute("INSERT INTO allowed_admins (id) VALUES (%s)", (admin_id,))
            conn.commit()
            logger.info(f"Сохранено {len(allowed_admins)} администраторов")
    except Exception as e:
        logger.error(f"Ошибка при сохранении allowed_admins: {str(e)}")
        conn.rollback()

# Функции для работы с пользователями
def load_allowed_users() -> List[int]:
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM allowed_users")
            users = [row[0] for row in cur.fetchall()]
            logger.info(f"Загружено {len(users)} пользователей")
            return users
    except Exception as e:
        logger.error(f"Ошибка при загрузке allowed_users: {str(e)}")
        conn.rollback()
        return []

def save_allowed_users(allowed_users: List[int]) -> None:
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM allowed_users")
            for user_id in allowed_users:
                cur.execute("INSERT INTO allowed_users (id) VALUES (%s)", (user_id,))
            conn.commit()
            logger.info(f"Сохранено {len(allowed_users)} пользователей")
    except Exception as e:
        logger.error(f"Ошибка при сохранении allowed_users: {str(e)}")
        conn.rollback()

# Функции для профилей пользователей
def load_user_profiles() -> Dict[int, Dict[str, str]]:
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id, fio, name, region FROM user_profiles")
            profiles = {}
            for row in cur.fetchall():
                profiles[row[0]] = {"fio": row[1], "name": row[2], "region": row[3]}
            logger.info(f"Загружено {len(profiles)} профилей пользователей")
            return profiles
    except Exception as e:
        logger.error(f"Ошибка при загрузке user_profiles: {str(e)}")
        conn.rollback()
        return {}

def save_user_profiles(profiles: Dict[int, Dict[str, str]]) -> None:
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM user_profiles")
            for user_id, profile in profiles.items():
                cur.execute(
                    "INSERT INTO user_profiles (user_id, fio, name, region) VALUES (%s, %s, %s, %s)",
                    (user_id, profile.get("fio"), profile.get("name"), profile.get("region"))
                )
            conn.commit()
            logger.info(f"Сохранено {len(profiles)} профилей пользователей")
    except Exception as e:
        logger.error(f"Ошибка при сохранении user_profiles: {str(e)}")
        conn.rollback()

# Функции для работы с базой знаний в JSON
def load_knowledge_base_json() -> List[str]:
    try:
        if os.path.exists('knowledge_base.json'):
            with open('knowledge_base.json', 'r', encoding='utf-8') as f:
                facts = json.load(f)
                if not isinstance(facts, list):
                    facts = []
                facts = [str(fact).strip() for fact in facts]
        else:
            facts = [
                "Привет! Чем могу помочь?",
                "Документы по награждениям находятся в папке /documents/Награждения.",
                "Всё отлично, спасибо за вопрос!",
                "ВСКС Всероссийский студенческий корпус спасателей"
            ]
            with open('knowledge_base.json', 'w', encoding='utf-8') as f:
                json.dump(facts, f, ensure_ascii=False, indent=4)
        logger.info(f"Загружено {len(facts)} фактов из knowledge_base.json: {facts}")
        return facts
    except Exception as e:
        logger.error(f"Ошибка при загрузке knowledge_base.json: {str(e)}")
        return []

def save_knowledge_base_json(facts: List[str]) -> None:
    try:
        with open('knowledge_base.json', 'w', encoding='utf-8') as f:
            json.dump(facts, f, ensure_ascii=False, indent=4)
        logger.info(f"Сохранено {len(facts)} фактов в knowledge_base.json")
    except Exception as e:
        logger.error(f"Ошибка при сохранении knowledge_base.json: {str(e)}")

# Функция для извлечения ключевого слова из запроса
def extract_keyword(user_input: str) -> str:
    """Извлекает ключевое слово из запросов вида 'Что такое ВСКС' или 'Что значит ВСКС'."""
    user_input_lower = user_input.lower().strip()
    patterns = [
        r'^\s*(что\s+такое|что\s+значит|что)\s+(.+?)\s*$',
        r'^\s*(.+?)\s*$'
    ]
    for pattern in patterns:
        match = re.match(pattern, user_input_lower)
        if match:
            keyword = match.group(2) if len(match.groups()) > 1 else match.group(1)
            keyword = keyword.strip('?').strip()
            logger.debug(f"Извлечено ключевое слово: '{keyword}' из запроса '{user_input}'")
            return keyword
    logger.debug(f"Не удалось извлечь ключевое слово из запроса '{user_input}'")
    return user_input_lower

# Функция для логирования запросов
def log_request(user_id: int, request: str, response: str) -> None:
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO request_logs (user_id, request_text, response_text, timestamp) VALUES (%s, %s, %s, NOW())",
                (user_id, request, response)
            )
            conn.commit()
            logger.info(f"Запрос от {user_id} залогирован")
    except Exception as e:
        logger.error(f"Ошибка при логировании запроса: {str(e)}")
        conn.rollback()

# Функции для работы с Яндекс.Диском
def create_yandex_folder(folder_path: str) -> bool:
    folder_path = folder_path.rstrip('/')
    url = f'https://cloud-api.yandex.net/v1/disk/resources?path={quote(folder_path)}'
    headers = {'Authorization': f'OAuth {YANDEX_TOKEN}', 'Content-Type': 'application/json'}
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            logger.info(f"Папка {folder_path} уже существует")
            return True
        elif response.status_code == 401:
            logger.error(f"Ошибка авторизации Яндекс.Диска: {response.text}")
            return False
        elif response.status_code == 404:
            response = requests.put(url, headers=headers)
            if response.status_code in (201, 409):
                logger.info(f"Папка {folder_path} создана")
                return True
            else:
                logger.error(f"Ошибка создания папки {folder_path}: {response.status_code} - {response.text}")
                return False
        else:
            logger.error(f"Неожиданный статус при проверке папки {folder_path}: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        logger.error(f"Ошибка при создании/проверке папки {folder_path}: {str(e)}")
        return False

def list_yandex_disk_items(folder_path: str, item_type: str = None) -> List[Dict[str, str]]:
    folder_path = folder_path.rstrip('/')
    url = f'https://cloud-api.yandex.net/v1/disk/resources?path={quote(folder_path)}&fields=_embedded.items.name,_embedded.items.type,_embedded.items.path&limit=100'
    headers = {'Authorization': f'OAuth {YANDEX_TOKEN}'}
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            items = response.json().get('_embedded', {}).get('items', [])
            if item_type:
                return [item for item in items if item['type'] == item_type]
            return items
        elif response.status_code == 401:
            logger.error(f"Ошибка авторизации Яндекс.Диска при получении списка: {response.text}")
        else:
            logger.error(f"Ошибка Яндекс.Диска при получении списка: {response.status_code} - {response.text}")
        return []
    except Exception as e:
        logger.error(f"Ошибка при запросе списка элементов: {str(e)}")
        return []

def list_yandex_disk_directories(folder_path: str) -> List[str]:
    items = list_yandex_disk_items(folder_path, item_type='dir')
    return [item['name'] for item in items]

def list_yandex_disk_files(folder_path: str) -> List[Dict[str, str]]:
    folder_path = folder_path.rstrip('/')
    items = list_yandex_disk_items(folder_path, item_type='file')
    supported_extensions = ('.pdf', '.doc', '.docx', '.xls', '.xlsx', '.cdr', '.eps', '.png', '.jpg', '.jpeg')
    files = [item for item in items if item['name'].lower().endswith(supported_extensions)]
    logger.info(f"Найдено {len(files)} файлов в папке {folder_path}")
    return files

def get_yandex_disk_file(file_path: str) -> str | None:
    file_path = file_path.rstrip('/')
    encoded_path = quote(file_path, safe='/')
    url = f'https://cloud-api.yandex.net/v1/disk/resources/download?path={encoded_path}'
    headers = {'Authorization': f'OAuth {YANDEX_TOKEN}'}
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            return response.json().get('href')
        elif response.status_code == 401:
            logger.error(f"Ошибка авторизации Яндекс.Диска для файла {file_path}: {response.text}")
        else:
            logger.error(f"Ошибка Яндекс.Диска для файла {file_path}: {response.status_code} - {response.text}")
        return None
    except Exception as e:
        logger.error(f"Ошибка при запросе файла {file_path}: {str(e)}")
        return None

def upload_to_yandex_disk(file_content: bytes, file_name: str, folder_path: str) -> bool:
    folder_path = folder_path.rstrip('/')
    file_path = f"{folder_path}/{file_name}"
    encoded_path = quote(file_path, safe='/')
    url = f'https://cloud-api.yandex.net/v1/disk/resources/upload?path={encoded_path}&overwrite=true'
    headers = {'Authorization': f'OAuth {YANDEX_TOKEN}'}
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            upload_url = response.json().get('href')
            upload_response = requests.put(upload_url, data=file_content)
            if upload_response.status_code in (201, 202):
                logger.info(f"Файл {file_name} загружен")
                return True
            logger.error(f"Ошибка загрузки файла {file_path}: {upload_response.status_code}")
            return False
        logger.error(f"Ошибка получения URL для загрузки {file_path}: {response.status_code}")
        return False
    except Exception as e:
        logger.error(f"Ошибка при загрузке файла {file_path}: {str(e)}")
        return False

# Инициализация глобальных переменных
ALLOWED_ADMINS = load_allowed_admins()
ALLOWED_USERS = load_allowed_users()
USER_PROFILES = load_user_profiles()
KNOWLEDGE_BASE_JSON = load_knowledge_base_json()

# Системный промпт для ИИ
system_prompt = """
Вы — полезный чат-бот, который логически анализирует историю переписки. 
Сначала проверяй базу знаний из knowledge_base.json. Если ответа нет, используй свои знания.
Отвечай кратко, на русском языке, без лишних объяснений.
"""

# Сохранение истории переписки
histories: Dict[int, Dict[str, Any]] = {}

# Обработчик команды /start
async def send_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id: int = update.effective_user.id
    if user_id not in ALLOWED_USERS and user_id not in ALLOWED_ADMINS:
        await update.message.reply_text(f"Ваш user_id: {user_id}\nИзвините, у вас нет доступа.",
                                        reply_markup=ReplyKeyboardRemove())
        return
    if user_id not in USER_PROFILES:
        context.user_data["awaiting_fio"] = True
        await update.message.reply_text("Напишите своё ФИО.", reply_markup=ReplyKeyboardRemove())
        return
    profile = USER_PROFILES[user_id]
    if profile.get("name") is None:
        context.user_data["awaiting_name"] = True
        await update.message.reply_text("Как я могу к Вам обращаться?", reply_markup=ReplyKeyboardRemove())
    else:
        await show_main_menu(update, context)

# Команда /add_fact для добавления фактов
async def add_fact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id: int = update.effective_user.id
    if user_id not in ALLOWED_ADMINS:
        await update.message.reply_text("Только администраторы могут добавлять факты.",
                                        reply_markup=ReplyKeyboardRemove())
        return
    args = context.args
    if not args:
        await update.message.reply_text("Использование: /add_fact <факт>", reply_markup=ReplyKeyboardRemove())
        return
    fact = ' '.join(args).strip()
    global KNOWLEDGE_BASE_JSON
    if fact not in KNOWLEDGE_BASE_JSON:
        KNOWLEDGE_BASE_JSON.append(fact)
        save_knowledge_base_json(KNOWLEDGE_BASE_JSON)
        await update.message.reply_text(f"Факт '{fact}' добавлен в базу знаний.", reply_markup=ReplyKeyboardRemove())
        logger.info(f"Факт '{fact}' добавлен администратором {user_id} в knowledge_base.json")
    else:
        await update.message.reply_text(f"Факт '{fact}' уже существует в базе знаний.",
                                        reply_markup=ReplyKeyboardRemove())

# Отображение главного меню
async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id: int = update.effective_user.id
    admin_keyboard = [
        ['Управление пользователями', 'Загрузить файл'],
        ['Архив документов РО', 'Документы для РО']
    ] if user_id in ALLOWED_ADMINS else [
        ['Загрузить файл'],
        ['Архив документов РО', 'Документы для РО']
    ]
    reply_markup = ReplyKeyboardMarkup(admin_keyboard, resize_keyboard=True)
    context.user_data['default_reply_markup'] = reply_markup
    context.user_data.pop('current_mode', None)
    context.user_data.pop('current_path', None)
    context.user_data.pop('file_list', None)
    context.user_data.pop('awaiting_user_id', None)
    context.user_data.pop('awaiting_admin_id', None)
    context.user_data.pop('awaiting_upload', None)
    await update.message.reply_text("Выберите действие:", reply_markup=reply_markup)

# Отображение меню управления пользователями
async def show_admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = [
        ['Добавить пользователя', 'Добавить администратора'],
        ['Список пользователей', 'Список администраторов'],
        ['Удалить файл'],
        ['Назад']
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text("Выберите действие:", reply_markup=reply_markup)

# Отображение содержимого папки в /documents/
async def show_current_docs(update: Update, context: ContextTypes.DEFAULT_TYPE, is_return: bool = False) -> None:
    user_id: int = update.effective_user.id
    context.user_data.pop('file_list', None)
    current_path = context.user_data.get('current_path', '/documents/')
    folder_name = current_path.rstrip('/').split('/')[-1] or "Документы"
    if not create_yandex_folder(current_path):
        logger.warning(f"Не удалось создать папку {current_path}, возможно, она уже существует или проблема с токеном.")
    files = list_yandex_disk_files(current_path)
    dirs = list_yandex_disk_directories(current_path)
    logger.info(f"Пользователь {user_id} в папке {current_path}, найдено файлов: {len(files)}, папок: {len(dirs)}")
    keyboard = [[dir_name] for dir_name in dirs]
    if current_path != '/documents/':
        keyboard.append(['Назад'])
    keyboard.append(['В главное меню'])
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    if files:
        context.user_data['file_list'] = files
        context.user_data['current_path'] = current_path
        file_keyboard = [[InlineKeyboardButton(item['name'], callback_data=f"doc_download:{idx}")] for idx, item in
                         enumerate(files)]
        file_reply_markup = InlineKeyboardMarkup(file_keyboard)
        await update.message.reply_text(f"Файлы в папке {folder_name}:", reply_markup=file_reply_markup)
    elif dirs:
        if not is_return:
            message = "Документы для РО" if current_path == '/documents/' else f"Папки в {folder_name}:"
            await update.message.reply_text(message, reply_markup=reply_markup)
    else:
        await update.message.reply_text(f"Папка {folder_name} пуста.", reply_markup=reply_markup)

# Обработка callback-запросов
async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id: int = update.effective_user.id
    default_reply_markup = context.user_data.get('default_reply_markup', ReplyKeyboardRemove())
    profile = USER_PROFILES.get(user_id)
    if not profile or "region" not in profile:
        await query.message.reply_text("Ошибка: регион не определён.", reply_markup=default_reply_markup)
        return

    if query.data.startswith("doc_download:") or query.data.startswith("download:"):
        try:
            file_idx = int(query.data.split(":", 1)[1])
            if query.data.startswith("doc_download:"):
                current_path = context.user_data.get('current_path', '/documents/')
            else:
                current_path = f"/regions/{profile['region']}/"

            files = context.user_data.get('file_list', []) or list_yandex_disk_files(current_path)
            context.user_data['file_list'] = files
            context.user_data['current_path'] = current_path

            if file_idx >= len(files):
                await query.message.reply_text("Ошибка: файл не найден.", reply_markup=default_reply_markup)
                logger.error(f"Файл с индексом {file_idx} не найден в папке {current_path} для user_id {user_id}")
                return

            file_name = files[file_idx]['name']
            file_path = f"{current_path.rstrip('/')}/{file_name}"
            logger.info(f"Попытка скачать файл {file_path} для user_id {user_id}")

            download_url = get_yandex_disk_file(file_path)
            if not download_url:
                await query.message.reply_text("Ошибка: не удалось получить ссылку на файл. Проверьте YANDEX_TOKEN.",
                                              reply_markup=default_reply_markup)
                logger.error(f"Не удалось получить ссылку для файла {file_path}")
                return

            file_response = requests.get(download_url)
            if file_response.status_code == 200:
                file_size = len(file_response.content) / (1024 * 1024)
                if file_size > 20:
                    await query.message.reply_text("Файл слишком большой (>20 МБ).", reply_markup=default_reply_markup)
                    logger.warning(f"Файл {file_name} слишком большой: {file_size} МБ")
                    return
                await query.message.reply_document(document=InputFile(file_response.content, filename=file_name))
                logger.info(f"Файл {file_name} успешно отправлен пользователю {user_id} из {current_path}")
            else:
                await query.message.reply_text(f"Не удалось загрузить файл. Статус: {file_response.status_code}",
                                              reply_markup=default_reply_markup)
                logger.error(f"Ошибка загрузки файла {file_path}: статус {file_response.status_code}")
        except Exception as e:
            await query.message.reply_text(f"Ошибка при скачивании: {str(e)}. Проверьте YANDEX_TOKEN.",
                                          reply_markup=default_reply_markup)
            logger.error(f"Ошибка при отправке файла: {str(e)}")

# Обработка текстовых сообщений
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id: int = update.effective_user.id
    chat_id: int = update.effective_chat.id
    user_input: str = update.message.text.strip()
    logger.info(f"Получено сообщение от {chat_id} (user_id: {user_id}): {user_input}")
    log_request(user_id, user_input, "Обработка сообщения...")

    if user_id not in ALLOWED_USERS and user_id not in ALLOWED_ADMINS:
        await update.message.reply_text("Извините, у вас нет доступа.", reply_markup=ReplyKeyboardRemove())
        return

    if user_id not in USER_PROFILES:
        if context.user_data.get("awaiting_fio", False):
            USER_PROFILES[user_id] = {"fio": user_input, "name": None, "region": None}
            save_user_profiles(USER_PROFILES)
            if user_id not in ALLOWED_USERS:
                ALLOWED_USERS.append(user_id)
                save_allowed_users(ALLOWED_USERS)
            context.user_data["awaiting_fio"] = False
            context.user_data["awaiting_federal_district"] = True
            keyboard = [[district] for district in FEDERAL_DISTRICTS.keys()]
            await update.message.reply_text("Выберите федеральный округ:",
                                            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))
            return
        await update.message.reply_text("Сначала пройдите регистрацию с /start.")
        return

    admin_keyboard = [
        ['Управление пользователями', 'Загрузить файл'],
        ['Архив документов РО', 'Документы для РО']
    ] if user_id in ALLOWED_ADMINS else [
        ['Загрузить файл'],
        ['Архив документов РО', 'Документы для РО']
    ]
    default_reply_markup = ReplyKeyboardMarkup(admin_keyboard, resize_keyboard=True)
    context.user_data['default_reply_markup'] = default_reply_markup

    if context.user_data.get("awaiting_user_id", False):
        try:
            new_user_id = int(user_input)
            if new_user_id in ALLOWED_USERS:
                await update.message.reply_text(f"Пользователь с ID {new_user_id} уже существует.",
                                                reply_markup=ReplyKeyboardMarkup([['Назад']], resize_keyboard=True))
            else:
                ALLOWED_USERS.append(new_user_id)
                save_allowed_users(ALLOWED_USERS)
                await update.message.reply_text(f"Пользователь с ID {new_user_id} успешно добавлен.",
                                                reply_markup=ReplyKeyboardMarkup([['Назад']], resize_keyboard=True))
                logger.info(f"Пользователь {new_user_id} добавлен администратором {user_id}")
            context.user_data.pop("awaiting_user_id", None)
            return
        except ValueError:
            await update.message.reply_text("Пожалуйста, введите корректный user_id (число).",
                                            reply_markup=ReplyKeyboardMarkup([['Назад']], resize_keyboard=True))
            return

    if context.user_data.get("awaiting_admin_id", False):
        try:
            new_admin_id = int(user_input)
            if new_admin_id in ALLOWED_ADMINS:
                await update.message.reply_text(f"Администратор с ID {new_admin_id} уже существует.",
                                                reply_markup=ReplyKeyboardMarkup([['Назад']], resize_keyboard=True))
            else:
                ALLOWED_ADMINS.append(new_admin_id)
                save_allowed_admins(ALLOWED_ADMINS)
                await update.message.reply_text(f"Администратор с ID {new_admin_id} успешно добавлен.",
                                                reply_markup=ReplyKeyboardMarkup([['Назад']], resize_keyboard=True))
                logger.info(f"Администратор {new_admin_id} добавлен администратором {user_id}")
            context.user_data.pop("awaiting_admin_id", None)
            return
        except ValueError:
            await update.message.reply_text("Пожалуйста, введите корректный admin_id (число).",
                                            reply_markup=ReplyKeyboardMarkup([['Назад']], resize_keyboard=True))
            return

    if context.user_data.get("awaiting_federal_district", False):
        if user_input in FEDERAL_DISTRICTS:
            context.user_data["selected_federal_district"] = user_input
            context.user_data["awaiting_federal_district"] = False
            context.user_data["awaiting_region"] = True
            regions = FEDERAL_DISTRICTS[user_input]
            keyboard = [[region] for region in regions]
            await update.message.reply_text("Выберите регион:",
                                            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))
            return
        await update.message.reply_text("Выберите из предложенных округов.", reply_markup=ReplyKeyboardMarkup(
            [[district] for district in FEDERAL_DISTRICTS.keys()]))
        return

    if context.user_data.get("awaiting_region", False):
        selected_district = context.user_data.get("selected_federal_district")
        regions = FEDERAL_DISTRICTS.get(selected_district, [])
        if user_input in regions:
            USER_PROFILES[user_id]["region"] = user_input
            save_user_profiles(USER_PROFILES)
            region_folder = f"/regions/{user_input}/"
            create_yandex_folder(region_folder)
            context.user_data.pop("awaiting_region", None)
            context.user_data.pop("selected_federal_district", None)
            context.user_data["awaiting_name"] = True
            await update.message.reply_text("Как я могу к Вам обращаться?", reply_markup=ReplyKeyboardRemove())
            return
        await update.message.reply_text("Выберите из предложенных регионов.",
                                        reply_markup=ReplyKeyboardMarkup([[region] for region in regions]))
        return

    if context.user_data.get("awaiting_name", False):
        USER_PROFILES[user_id]["name"] = user_input
        save_user_profiles(USER_PROFILES)
        context.user_data["awaiting_name"] = False
        await show_main_menu(update, context)
        await update.message.reply_text(f"Рад знакомству, {user_input}! Задавайте вопросы или используйте меню.",
                                        reply_markup=default_reply_markup)
        return

    handled = False
    if user_input == "Документы для РО":
        context.user_data['current_mode'] = 'documents_nav'
        context.user_data['current_path'] = '/documents/'
        context.user_data.pop('file_list', None)
        context.user_data.pop('awaiting_upload', None)
        create_yandex_folder('/documents/')
        await show_current_docs(update, context)
        handled = True

    elif user_input == "Архив документов РО":
        context.user_data.pop('current_mode', None)
        context.user_data.pop('current_path', None)
        context.user_data.pop('file_list', None)
        context.user_data.pop('awaiting_upload', None)
        await show_file_list(update, context)
        handled = True

    elif user_input == "Управление пользователями":
        if user_id not in ALLOWED_ADMINS:
            await update.message.reply_text("Только администраторы могут управлять пользователями.",
                                            reply_markup=default_reply_markup)
            return
        context.user_data.pop('awaiting_upload', None)
        await show_admin_menu(update, context)
        handled = True

    elif user_input == "Добавить пользователя":
        if user_id not in ALLOWED_ADMINS:
            await update.message.reply_text("Только администраторы могут добавлять пользователей.",
                                            reply_markup=default_reply_markup)
            return
        context.user_data["awaiting_user_id"] = True
        context.user_data.pop('awaiting_upload', None)
        await update.message.reply_text("Введите user_id нового пользователя (число):",
                                        reply_markup=ReplyKeyboardMarkup([['Назад']], resize_keyboard=True))
        handled = True

    elif user_input == "Добавить администратора":
        if user_id not in ALLOWED_ADMINS:
            await update.message.reply_text("Только администраторы могут добавлять администраторов.",
                                            reply_markup=default_reply_markup)
            return
        context.user_data["awaiting_admin_id"] = True
        context.user_data.pop('awaiting_upload', None)
        await update.message.reply_text("Введите user_id нового администратора (число):",
                                        reply_markup=ReplyKeyboardMarkup([['Назад']], resize_keyboard=True))
        handled = True

    elif user_input == "Список пользователей":
        if user_id not in ALLOWED_ADMINS:
            await update.message.reply_text("Только администраторы могут просматривать список пользователей.",
                                            reply_markup=default_reply_markup)
            return
        context.user_data.pop('awaiting_upload', None)
        users_list = "\n".join([f"ID: {uid}" for uid in ALLOWED_USERS]) or "Список пользователей пуст."
        await update.message.reply_text(f"Список пользователей:\n{users_list}",
                                        reply_markup=ReplyKeyboardMarkup([['Назад']], resize_keyboard=True))
        handled = True

    elif user_input == "Список администраторов":
        if user_id not in ALLOWED_ADMINS:
            await update.message.reply_text("Только администраторы могут просматривать список администраторов.",
                                            reply_markup=default_reply_markup)
            return
        context.user_data.pop('awaiting_upload', None)
        admins_list = "\n".join([f"ID: {aid}" for aid in ALLOWED_ADMINS]) or "Список администраторов пуст."
        await update.message.reply_text(f"Список администраторов:\n{admins_list}",
                                        reply_markup=ReplyKeyboardMarkup([['Назад']], resize_keyboard=True))
        handled = True

    elif user_input == "Удалить файл":
        if user_id not in ALLOWED_ADMINS:
            await update.message.reply_text("Только администраторы могут удалять файлы.",
                                            reply_markup=default_reply_markup)
            return
        context.user_data.pop('awaiting_upload', None)
        await show_file_list(update, context, for_deletion=True)
        handled = True

    elif user_input == "Загрузить файл":
        profile = USER_PROFILES.get(user_id)
        if not profile or "region" not in profile:
            await update.message.reply_text("Ошибка: регион не определён.", reply_markup=default_reply_markup)
            return
        context.user_data['awaiting_upload'] = True
        context.user_data.pop('current_mode', None)
        context.user_data.pop('current_path', None)
        context.user_data.pop('file_list', None)
        await update.message.reply_text(
            f"Прикрепите файл для загрузки в папку /regions/{profile['region']}/. "
            "Поддерживаются: .pdf, .doc, .docx, .xls, .xlsx, .cdr, .eps, .png, .jpg, .jpeg (до 50 МБ).",
            reply_markup=ReplyKeyboardMarkup([['Назад']], resize_keyboard=True))
        logger.info(f"Пользователь {user_id} готовится загрузить файл в /regions/{profile['region']}/")
        handled = True

    elif user_input == "Назад":
        context.user_data.pop('awaiting_upload', None)
        await show_main_menu(update, context)
        handled = True

    if context.user_data.get('current_mode') == 'documents_nav':
        current_path = context.user_data.get('current_path', '/documents/')
        dirs = list_yandex_disk_directories(current_path)
        dirs_lower = [d.lower() for d in dirs]
        user_input_lower = user_input.lower()
        if user_input_lower in dirs_lower:
            original_dir = next(d for d in dirs if d.lower() == user_input_lower)
            context.user_data['current_path'] = f"{current_path.rstrip('/')}/{original_dir}/"
            create_yandex_folder(context.user_data['current_path'])
            await show_current_docs(update, context)
            handled = True
        elif user_input == 'В главное меню':
            context.user_data.pop('awaiting_upload', None)
            await show_main_menu(update, context)
            handled = True
        elif user_input == 'Назад' and current_path != '/documents/':
            parts = current_path.rstrip('/').split('/')
            context.user_data['current_path'] = '/'.join(parts[:-1]) + '/' if len(parts) > 2 else '/documents/'
            await show_current_docs(update, context, is_return=True)
            handled = True

    # Проверка фактов из knowledge_base.json для всех пользователей
    if not handled:
        keyword = extract_keyword(user_input)
        logger.info(
            f"Поиск в knowledge_base.json для запроса '{user_input}' (ключевое слово: '{keyword}') от user_id {user_id} (админ: {user_id in ALLOWED_ADMINS})")

        if keyword:
            keyword_lower = keyword.lower().strip()
            for fact in KNOWLEDGE_BASE_JSON:
                fact_lower = fact.lower().strip()
                if fact_lower.startswith(keyword_lower) or keyword_lower in fact_lower:
                    await update.message.reply_text(fact, reply_markup=default_reply_markup)
                    log_request(user_id, user_input, fact)
                    logger.info(
                        f"Ответ найден в knowledge_base.json для запроса '{user_input}' (ключевое слово: '{keyword}'): {fact}")
                    return
                else:
                    logger.debug(f"Факт '{fact}' не соответствует ключевому слову '{keyword_lower}'")
        else:
            logger.warning(f"Ключевое слово не извлечено из запроса '{user_input}'")

        # Если факт не найден, обращаемся к Grok API
        logger.info(f"Факт для '{user_input}' не найден в knowledge_base.json, обращение к Grok API")
        if chat_id not in histories:
            histories[chat_id] = {"name": USER_PROFILES[user_id]["name"],
                                  "messages": [{"role": "system", "content": system_prompt}]}
        histories[chat_id]["messages"].append({"role": "user", "content": user_input})
        models_to_try = [XAI_MODEL, "grok", "grok-3", "grok-4"]
        ai_response = None
        error_msg = "Ошибка: Не удалось подключиться к ИИ. Проверьте настройки API или используйте базу знаний."
        for model in models_to_try:
            try:
                response = client.chat.completions.create(
                    model=model,
                    messages=histories[chat_id]["messages"],
                    max_tokens=1000
                )
                ai_response = response.choices[0].message.content.strip()
                logger.info(f"Успешный запрос к модели {model} для user_id {user_id}")
                break
            except Exception as e:
                logger.error(f"Ошибка Grok API для модели {model}: {str(e)}")
                if "404" in str(e):
                    error_msg = f"Ошибка: Модель {model} недоступна. Проверьте XAI_TOKEN или обратитесь в поддержку xAI (team ID: 4c40134b-82d4-4d27-a7e0-c6566cc04178)."
                continue

        if ai_response:
            histories[chat_id]["messages"].append({"role": "assistant", "content": ai_response})
            await update.message.reply_text(ai_response, reply_markup=default_reply_markup)
            log_request(user_id, user_input, ai_response)
        else:
            await update.message.reply_text(error_msg, reply_markup=default_reply_markup)
            log_request(user_id, user_input, error_msg)

# Обработка загруженных документов
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id: int = update.effective_user.id
    if not context.user_data.get('awaiting_upload', False):
        await update.message.reply_text("Используйте кнопку 'Загрузить файл' перед отправкой документа.")
        return
    document = update.message.document
    file_name = document.file_name
    if not file_name.lower().endswith(
            ('.pdf', '.doc', '.docx', '.xls', '.xlsx', '.cdr', '.eps', '.png', '.jpg', '.jpeg')):
        await update.message.reply_text(
            "Поддерживаются только файлы .pdf, .doc, .docx, .xls, .xlsx, .cdr, .eps, .png, .jpg, .jpeg.")
        context.user_data.pop('awaiting_upload', None)
        return
    file_size = document.file_size / (1024 * 1024)
    if file_size > 50:
        await update.message.reply_text("Файл слишком большой (>50 МБ).")
        context.user_data.pop('awaiting_upload', None)
        return
    profile = USER_PROFILES.get(user_id)
    if not profile or "region" not in profile:
        await update.message.reply_text("Ошибка: регион не определён.")
        context.user_data.pop('awaiting_upload', None)
        return
    region_folder = f"/regions/{profile['region']}/"
    create_yandex_folder(region_folder)
    try:
        file = await context.bot.get_file(document.file_id)
        file_content = await file.download_as_bytearray()
        if upload_to_yandex_disk(file_content, file_name, region_folder):
            await update.message.reply_text(f"Файл успешно загружен в папку {region_folder}")
            logger.info(f"Файл {file_name} загружен пользователем {user_id} в {region_folder}")
        else:
            await update.message.reply_text("Ошибка при загрузке файла. Проверьте YANDEX_TOKEN.")
            logger.error(f"Ошибка загрузки файла {file_name} в {region_folder} для user_id {user_id}")
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {str(e)}. Проверьте YANDEX_TOKEN.")
        logger.error(f"Ошибка обработки документа {file_name}: {str(e)}")
    context.user_data.pop('awaiting_upload', None)
    await show_main_menu(update, context)

# Отображение списка файлов
async def show_file_list(update: Update, context: ContextTypes.DEFAULT_TYPE, for_deletion: bool = False) -> None:
    user_id: int = update.effective_user.id
    profile = USER_PROFILES.get(user_id)
    if not profile or "region" not in profile:
        await update.message.reply_text("Ошибка: регион не определён.",
                                        reply_markup=context.user_data.get('default_reply_markup', ReplyKeyboardRemove()))
        return
    region_folder = f"/regions/{profile['region']}/"
    create_yandex_folder(region_folder)
    files = list_yandex_disk_files(region_folder)
    if not files:
        await update.message.reply_text(f"В папке {region_folder} нет файлов.",
                                        reply_markup=context.user_data.get('default_reply_markup', ReplyKeyboardRemove()))
        return
    context.user_data['file_list'] = files
    context.user_data['current_path'] = region_folder
    keyboard = [[InlineKeyboardButton(item['name'], callback_data=f"{'delete' if for_deletion else 'download'}:{idx}")]
                for idx, item in enumerate(files)]
    await update.message.reply_text("Выберите файл для удаления:" if for_deletion else "Список всех файлов:",
                                    reply_markup=InlineKeyboardMarkup(keyboard))

# Основная функция
def main():
    try:
        app = Application.builder().token(TELEGRAM_TOKEN).build()
        app.add_handler(CommandHandler("start", send_welcome))
        app.add_handler(CommandHandler("add_fact", add_fact))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
        app.add_handler(CallbackQueryHandler(handle_callback_query))
        app.run_polling(allowed_updates=Update.ALL_TYPES)
    except Exception as e:
        logger.error(f"Ошибка при запуске бота: {str(e)}")
        raise

if __name__ == '__main__':
    main()