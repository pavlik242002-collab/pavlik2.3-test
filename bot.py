from __future__ import annotations
import os
import json
import logging
import openai
import requests
from typing import Dict, List, Any
from dotenv import load_dotenv
from duckduckgo_search import DDGS
from telegram import ReplyKeyboardMarkup, ReplyKeyboardRemove, InlineKeyboardMarkup, InlineKeyboardButton, Update
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from telegram import InputFile
from urllib.parse import quote
from openai import OpenAI
import psycopg2
from datetime import datetime

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
load_dotenv()  # Пытаемся загрузить .env для локальной разработки, если файл существует
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
YANDEX_TOKEN = os.getenv("YANDEX_TOKEN")
HF_TOKEN = os.getenv("HF_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

# Отладка: выводим статус переменных
logger.info(f"TELEGRAM_TOKEN: {'Set' if TELEGRAM_TOKEN else 'Not set'}")
logger.info(f"YANDEX_TOKEN: {'Set' if YANDEX_TOKEN else 'Not set'}")
logger.info(f"HF_TOKEN: {'Set' if HF_TOKEN else 'Not set'}")
logger.info(f"DATABASE_URL: {'Set' if DATABASE_URL else 'Not set'}")

# Проверка токенов
missing_tokens = []
if not TELEGRAM_TOKEN:
    missing_tokens.append("TELEGRAM_TOKEN")
if not YANDEX_TOKEN:
    missing_tokens.append("YANDEX_TOKEN")
if not HF_TOKEN:
    missing_tokens.append("HF_TOKEN")
if not DATABASE_URL:
    missing_tokens.append("DATABASE_URL")

if missing_tokens:
    logger.error(f"Отсутствуют переменные окружения: {', '.join(missing_tokens)}")
    raise ValueError(f"Необходимо задать следующие переменные окружения: {', '.join(missing_tokens)}")

# Инициализация клиента OpenAI для Hugging Face
client = OpenAI(
    base_url="https://api-inference.huggingface.co/models/microsoft/DialoGPT-medium",  # Пример HF модели для чата; измените на нужную
    api_key=HF_TOKEN,
)

# Функция для подключения к БД
def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

# Инициализация таблиц в БД
def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS allowed_admins (
            id SERIAL PRIMARY KEY,
            user_id BIGINT UNIQUE NOT NULL
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS allowed_users (
            id SERIAL PRIMARY KEY,
            user_id BIGINT UNIQUE NOT NULL
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_profiles (
            user_id BIGINT PRIMARY KEY,
            fio TEXT,
            name TEXT,
            region TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_base (
            id SERIAL PRIMARY KEY,
            fact TEXT UNIQUE NOT NULL
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS request_logs (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            timestamp TIMESTAMP NOT NULL,
            message TEXT NOT NULL,
            response TEXT
        )
    """)
    conn.commit()
    cur.close()
    conn.close()
    logger.info("Таблицы в БД инициализированы.")

# Функции для работы с администраторами
def load_allowed_admins() -> List[int]:
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM allowed_admins")
    admins = [row[0] for row in cur.fetchall()]
    cur.close()
    conn.close()
    if not admins:
        # Добавляем дефолтного админа, если таблица пуста (замените на свой ID)
        add_allowed_admin(123456789)
        admins = [123456789]
    return admins

def add_allowed_admin(user_id: int) -> None:
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO allowed_admins (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING", (user_id,))
    conn.commit()
    cur.close()
    conn.close()

def remove_allowed_admin(user_id: int) -> None:
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM allowed_admins WHERE user_id = %s", (user_id,))
    conn.commit()
    cur.close()
    conn.close()

# Функции для работы с пользователями
def load_allowed_users() -> List[int]:
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM allowed_users")
    users = [row[0] for row in cur.fetchall()]
    cur.close()
    conn.close()
    return users

def add_allowed_user(user_id: int) -> None:
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO allowed_users (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING", (user_id,))
    conn.commit()
    cur.close()
    conn.close()

def remove_allowed_user(user_id: int) -> None:
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM allowed_users WHERE user_id = %s", (user_id,))
    conn.commit()
    cur.close()
    conn.close()

# Функции для профилей пользователей
def load_user_profiles() -> Dict[int, Dict[str, str]]:
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT user_id, fio, name, region FROM user_profiles")
    profiles = {row[0]: {"fio": row[1], "name": row[2], "region": row[3]} for row in cur.fetchall()}
    cur.close()
    conn.close()
    return profiles

def save_user_profile(user_id: int, profile: Dict[str, str]) -> None:
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO user_profiles (user_id, fio, name, region) 
        VALUES (%s, %s, %s, %s) 
        ON CONFLICT (user_id) DO UPDATE SET fio = EXCLUDED.fio, name = EXCLUDED.name, region = EXCLUDED.region
    """, (user_id, profile.get("fio"), profile.get("name"), profile.get("region")))
    conn.commit()
    cur.close()
    conn.close()

# Функции для базы знаний
def load_knowledge_base() -> List[str]:
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT fact FROM knowledge_base")
    facts = [row[0] for row in cur.fetchall()]
    cur.close()
    conn.close()
    logger.info(f"Загружено {len(facts)} фактов из БД.")
    return facts

def list_knowledge_with_ids() -> List[Dict[str, Any]]:
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, fact FROM knowledge_base")
    facts = [{"id": row[0], "fact": row[1]} for row in cur.fetchall()]
    cur.close()
    conn.close()
    return facts

def add_knowledge(fact: str) -> None:
    if not fact.strip():
        return
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO knowledge_base (fact) VALUES (%s) ON CONFLICT (fact) DO NOTHING", (fact.strip(),))
    conn.commit()
    cur.close()
    conn.close()
    logger.info(f"Добавлен факт: {fact}")

def remove_knowledge_by_id(fact_id: int) -> bool:
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM knowledge_base WHERE id = %s", (fact_id,))
    deleted = cur.rowcount > 0
    conn.commit()
    cur.close()
    conn.close()
    if deleted:
        logger.info(f"Факт с ID {fact_id} удалён.")
    return deleted

def remove_knowledge_by_text(fact_text: str) -> bool:
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM knowledge_base WHERE fact = %s", (fact_text.strip(),))
    deleted = cur.rowcount > 0
    conn.commit()
    cur.close()
    conn.close()
    return deleted

# Функция для логирования запросов
def log_request(user_id: int, message: str, response: str | None = None) -> None:
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO request_logs (user_id, timestamp, message, response) 
        VALUES (%s, %s, %s, %s)
    """, (user_id, datetime.now(), message, response))
    conn.commit()
    cur.close()
    conn.close()

# Словарь федеральных округов
FEDERAL_DISTRICTS = {
    "Центральный федеральный округ": [
        "Москва", "Белгородская область", "Брянская область", "Владимирская область", "Воронежская область",
        "Ивановская область", "Калужская область", "Костромская область", "Курская область",
        "Липецкая область", "Московская область", "Орловская область", "Рязанская область",
        "Смоленская область", "Тамбовская область", "Тверская область", "Тульская область",
        "Ярославская область"
    ],
    "Северо-Западный федеральный округ": [
        "Республика Карелия", "Республика Коми", "Архангельская область", "Вологодская область",
        "Ленинградская область", "Мурманская область", "Новгородская область", "Псковская область",
        "Калининградская область", "Ненецкий автономный округ", "Санкт-Петербург"
    ],
    "Южный федеральный округ": [
        "Республика Адыгея", "Республика Калмыкия", "Республика Крым", "Краснодарский край",
        "Астраханская область", "Волгоградская область", "Ростовская область", "Севастополь",
        "Донецкая Народная Республика", "Луганская Народная Республика", "Запорожская область", "Херсонская область"
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
        "Омская область", "Томская область", "Забайкальский край"
    ],
    "Дальневосточный федеральный округ": [
        "Республика Саха (Якутия)", "Приморский край", "Хабаровский край", "Амурская область",
        "Камчатский край", "Магадская область", "Сахалинская область", "Еврейская автономная область",
        "Чукотский автономный округ"
    ]
}

# Инициализация глобальных переменных
init_db()
ALLOWED_ADMINS = load_allowed_admins()
ALLOWED_USERS = load_allowed_users()
USER_PROFILES = load_user_profiles()
KNOWLEDGE_BASE = load_knowledge_base()

# Новый системный промпт для ИИ
system_prompt = """
Вы — полезный чат-бот, который логически анализирует всю историю переписки, чтобы давать последовательные ответы.
Обязательно используй актуальные данные из поиска в истории сообщений для ответов на вопросы о фактах, организациях или событиях.
Если данные из поиска доступны, основывайся только на них и отвечай подробно, но кратко.
Если данных нет, используй свои знания и базу знаний, предоставленную системой.
Не упоминая процесс поиска, источники или фразы вроде "не знаю" или "уточните".
Всегда учитывай полный контекст разговора.
Отвечай кратко, по делу, на русском языке, без лишних объяснений.
"""

# Хранение истории переписки
histories: Dict[int, Dict[str, Any]] = {}

# Функции для работы с Яндекс.Диском
def create_yandex_folder(folder_path: str) -> bool:
    """Создаёт папку на Яндекс.Диске."""
    folder_path = folder_path.rstrip('/')
    url = f'https://cloud-api.yandex.net/v1/disk/resources?path={quote(folder_path)}'
    headers = {'Authorization': f'OAuth {YANDEX_TOKEN}', 'Content-Type': 'application/json'}
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            logger.info(f"Папка {folder_path} уже существует.")
            return True
        if response.status_code == 401:
            logger.error(f"401 Unauthorized для папки {folder_path}. Проверьте YANDEX_TOKEN (возможно, истёк или неверный).")
            return False
        response = requests.put(url, headers=headers)
        if response.status_code in (201, 409):
            logger.info(f"Папка {folder_path} создана.")
            return True
        if response.status_code == 401:
            logger.error(f"401 Unauthorized при создании {folder_path}. Проверьте YANDEX_TOKEN.")
            return False
        logger.error(f"Ошибка создания папки {folder_path}: код {response.status_code}, ответ: {response.text}")
        return False
    except Exception as e:
        logger.error(f"Ошибка при создании папки {folder_path}: {str(e)}")
        return False

def list_yandex_disk_items(folder_path: str, item_type: str = None) -> List[Dict[str, str]]:
    """Возвращает список элементов (файлов или директорий) в папке на Яндекс.Диске."""
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
        if response.status_code == 401:
            logger.error(f"401 Unauthorized для списка элементов в {folder_path}. Проверьте YANDEX_TOKEN.")
            return []
        logger.error(f"Ошибка Яндекс.Диска: код {response.status_code}, ответ: {response.text}")
        return []
    except Exception as e:
        logger.error(f"Ошибка при запросе списка элементов в {folder_path}: {str(e)}")
        return []

def list_yandex_disk_directories(folder_path: str) -> List[str]:
    """Возвращает список имен поддиректорий в папке."""
    items = list_yandex_disk_items(folder_path, item_type='dir')
    return [item['name'] for item in items]

def list_yandex_disk_files(folder_path: str) -> List[Dict[str, str]]:
    """Возвращает список файлов в папке на Яндекс.Диске (с фильтром по расширениям)."""
    folder_path = folder_path.rstrip('/')
    items = list_yandex_disk_items(folder_path, item_type='file')
    supported_extensions = ('.pdf', '.doc', '.docx', '.xls', '.xlsx', '.cdr', '.eps', '.png', '.jpg', '.jpeg')
    files = [item for item in items if item['name'].lower().endswith(supported_extensions)]
    logger.info(f"Найдено {len(files)} файлов в папке {folder_path}: {[item['name'] for item in files]}")
    return files

def get_yandex_disk_file(file_path: str) -> str | None:
    """Получает ссылку для скачивания файла с Яндекс.Диска."""
    file_path = file_path.rstrip('/')
    encoded_path = quote(file_path, safe='/')
    url = f'https://cloud-api.yandex.net/v1/disk/resources/download?path={encoded_path}'
    headers = {'Authorization': f'OAuth {YANDEX_TOKEN}'}
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            return response.json().get('href')
        if response.status_code == 401:
            logger.error(f"401 Unauthorized для файла {file_path}. Проверьте YANDEX_TOKEN.")
            return None
        logger.error(f"Ошибка Яндекс.Диска для файла {file_path}: код {response.status_code}, ответ: {response.text}")
        return None
    except Exception as e:
        logger.error(f"Ошибка при запросе к Яндекс.Диску для файла {file_path}: {str(e)}")
        return None

def upload_to_yandex_disk(file_content: bytes, file_name: str, folder_path: str) -> bool:
    """Загружает файл на Яндекс.Диск."""
    folder_path = folder_path.rstrip('/')
    file_path = f"{folder_path}/{file_name}"
    encoded_path = quote(file_path, safe='/')
    url = f'https://cloud-api.yandex.net/v1/disk/resources/upload?path={encoded_path}&overwrite=true'
    headers = {'Authorization': f'OAuth {YANDEX_TOKEN}'}
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            upload_url = response.json().get('href')
            if upload_url:
                upload_response = requests.put(upload_url, data=file_content)
                if upload_response.status_code in (201, 202):
                    logger.info(f"Файл {file_name} загружен в {folder_path}")
                    return True
                if upload_response.status_code == 401:
                    logger.error(f"401 Unauthorized при загрузке {file_path}. Проверьте YANDEX_TOKEN.")
                    return False
                logger.error(
                    f"Ошибка загрузки файла {file_path}: код {upload_response.status_code}, ответ: {upload_response.text}")
                return False
            logger.error(f"Не получен URL для загрузки файла {file_path}")
            return False
        if response.status_code == 401:
            logger.error(f"401 Unauthorized при получении URL для {file_path}. Проверьте YANDEX_TOKEN.")
            return False
        logger.error(
            f"Ошибка получения URL для загрузки {file_path}: код {response.status_code}, ответ: {response.text}")
        return False
    except Exception as e:
        logger.error(f"Ошибка при загрузке файла {file_path}: {str(e)}")
        return False

def delete_yandex_disk_file(file_path: str) -> bool:
    """Удаляет файл с Яндекс.Диска."""
    file_path = file_path.rstrip('/')
    encoded_path = quote(file_path, safe='/')
    url = f'https://cloud-api.yandex.net/v1/disk/resources?path={encoded_path}'
    headers = {'Authorization': f'OAuth {YANDEX_TOKEN}'}
    try:
        response = requests.delete(url, headers=headers)
        if response.status_code in (204, 202):
            logger.info(f"Файл {file_path} удалён.")
            return True
        if response.status_code == 401:
            logger.error(f"401 Unauthorized при удалении {file_path}. Проверьте YANDEX_TOKEN.")
            return False
        logger.error(f"Ошибка удаления файла {file_path}: код {response.status_code}, ответ: {response.text}")
        return False
    except Exception as e:
        logger.error(f"Ошибка при удалении файла {file_path}: {str(e)}")
        return False

# Функция веб-поиска
def web_search(query: str) -> str:
    """Выполняет поиск в интернете и кэширует результаты."""
    cache_file = 'search_cache.json'
    try:
        if not os.path.exists(cache_file):
            logger.warning("Файл search_cache.json не найден, создаётся новый.")
            with open(cache_file, 'w', encoding='utf-8') as f:
                json.dump({}, f, ensure_ascii=False)
        with open(cache_file, 'r', encoding='utf-8') as f:
            cache = json.load(f)
    except Exception as e:
        logger.error(f"Ошибка при загрузке search_cache.json: {str(e)}")
        cache = {}
    if query in cache:
        logger.info(f"Использую кэш для запроса: {query}")
        return cache[query]
    try:
        with DDGS() as ddgs:
            results = [r for r in ddgs.text(query, max_results=3)]
        search_results = json.dumps(results, ensure_ascii=False, indent=2)
        cache[query] = search_results
        with open(cache_file, 'w', encoding='utf-8') as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
        logger.info(f"Поиск выполнен для запроса: {query}")
        return search_results
    except Exception as e:
        logger.error(f"Ошибка при поиске: {str(e)}")
        return json.dumps({"error": "Не удалось выполнить поиск."}, ensure_ascii=False)

# Обработчик команды /learn
async def handle_learn(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка команды /learn для добавления знаний."""
    user_id: int = update.effective_user.id
    if user_id not in ALLOWED_ADMINS:
        await update.message.reply_text("Только администраторы могут обучать бота.")
        return

    if not context.args:
        await update.message.reply_text("Использование: /learn <факт>. Например: /learn Земля круглая.")
        return

    fact = ' '.join(context.args)
    add_knowledge(fact)
    global KNOWLEDGE_BASE
    KNOWLEDGE_BASE = load_knowledge_base()
    await update.message.reply_text(f"Факт добавлен: '{fact}'. Теперь бот использует его во всех ответах!")
    logger.info(f"Администратор {user_id} добавил факт: {fact}")

# Обработчик команды /forget
async def handle_forget(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка команды /forget для удаления факта из базы знаний."""
    user_id: int = update.effective_user.id
    if user_id not in ALLOWED_ADMINS:
        await update.message.reply_text("Только администраторы могут удалять факты.")
        logger.info(f"Пользователь {user_id} попытался использовать команду /forget.")
        return

    if not context.args:
        await update.message.reply_text("Использование: /forget <факт>. Например: /forget Земля круглая.")
        return

    fact = ' '.join(context.args)
    if remove_knowledge_by_text(fact):
        global KNOWLEDGE_BASE
        KNOWLEDGE_BASE = load_knowledge_base()
        await update.message.reply_text(f"Факт удалён: '{fact}'.")
        logger.info(f"Администратор {user_id} удалил факт: {fact}")
    else:
        await update.message.reply_text(f"Факт '{fact}' не найден в базе знаний.")
        logger.info(f"Администратор {user_id} пытался удалить несуществующий факт: {fact}")

# Обработчик команды /start
async def send_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка команды /start: регистрация или главное меню."""
    if update.effective_user is None or update.effective_chat is None:
        logger.error("Ошибка: update.effective_user или update.effective_chat is None")
        await update.message.reply_text("Ошибка: не удалось определить пользователя или чат.")
        return

    user_id: int = update.effective_user.id
    chat_id: int = update.effective_chat.id

    # Очистка временных данных
    context.user_data.clear()

    # Проверка доступа
    if user_id not in ALLOWED_USERS and user_id not in ALLOWED_ADMINS:
        welcome_message = f"Ваш user_id: {user_id}\nИзвините, у вас нет доступа. Передайте user_id администратору."
        await update.message.reply_text(welcome_message, reply_markup=ReplyKeyboardRemove())
        logger.info(f"Пользователь {user_id} попытался получить доступ.")
        return

    # Проверка профиля
    if user_id not in USER_PROFILES:
        context.user_data["awaiting_fio"] = True
        welcome_message = "Доброго времени суток!\nДля начала работы напишите своё ФИО."
        await update.message.reply_text(welcome_message, reply_markup=ReplyKeyboardRemove())
        logger.info(f"Пользователь {chat_id} начал регистрацию.")
        return

    profile = USER_PROFILES[user_id]
    if profile.get("name") is None:
        context.user_data["awaiting_name"] = True
        await update.message.reply_text("Как я могу к Вам обращаться (кратко для удобства)?",
                                        reply_markup=ReplyKeyboardRemove())
    else:
        await show_main_menu(update, context)

# Отображение главного меню
async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает главное меню с командами."""
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
    context.user_data.pop('current_dir', None)
    context.user_data.pop('file_list', None)
    context.user_data.pop('current_path', None)
    await update.message.reply_text("Выберите действие:", reply_markup=reply_markup)

# Обработчик команды /getfile
async def get_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка команды /getfile: скачивание файла."""
    if update.effective_user is None or update.effective_chat is None:
        logger.error("Ошибка: update.effective_user или update.effective_chat is None")
        await update.message.reply_text("Ошибка: не удалось определить пользователя или чат.")
        return

    user_id: int = update.effective_user.id
    chat_id: int = update.effective_chat.id

    if user_id not in ALLOWED_USERS and user_id not in ALLOWED_ADMINS:
        await update.message.reply_text("Извините, у вас нет доступа.", reply_markup=ReplyKeyboardRemove())
        logger.info(f"Пользователь {user_id} попытался скачать файл.")
        return

    if user_id not in USER_PROFILES:
        await update.message.reply_text("Сначала пройдите регистрацию с /start.")
        return

    if not context.args:
        await update.message.reply_text("Укажите название файла (например, file.pdf).")
        return

    file_name = ' '.join(context.args).strip()
    await search_and_send_file(update, context, file_name)

# Поиск и отправка файла из региона
async def search_and_send_file(update: Update, context: ContextTypes.DEFAULT_TYPE, file_name: str) -> None:
    """Ищет и отправляет файл с Яндекс.Диска из региональной папки."""
    user_id: int = update.effective_user.id
    profile = USER_PROFILES.get(user_id)
    if not profile or "region" not in profile:
        await update.message.reply_text("Ошибка: регион не определён. Перезапустите /start.")
        logger.error(f"Ошибка: регион не определён для пользователя {user_id}.")
        return

    region_folder = f"/regions/{profile['region']}/"
    if not create_yandex_folder(region_folder):
        await update.message.reply_text("Ошибка: не удалось проверить или создать папку региона (проверьте токен Яндекс.Диска).")
        logger.error(f"Не удалось создать папку {region_folder} для пользователя {user_id}.")
        return

    if not file_name.lower().endswith(('.pdf', '.doc', '.docx', '.xls', '.xlsx', '.cdr', '.eps', '.png', '.jpg', '.jpeg')):
        await update.message.reply_text("Поддерживаются только файлы .pdf, .doc, .docx, .xls, .xlsx, .cdr, .eps, .png, .jpg, .jpeg.")
        logger.error(f"Неподдерживаемый формат файла {file_name} для пользователя {user_id}.")
        return

    files = list_yandex_disk_files(region_folder)
    matching_file = next((item for item in files if item['name'].lower() == file_name.lower()), None)

    if not matching_file:
        await update.message.reply_text(f"Файл '{file_name}' не найден в папке {region_folder}.")
        logger.info(f"Файл '{file_name}' не найден для пользователя {user_id}.")
        return

    file_path = matching_file['path']
    download_url = get_yandex_disk_file(file_path)
    if not download_url:
        await update.message.reply_text("Ошибка: не удалось получить ссылку для скачивания (проверьте токен Яндекс.Диска).")
        logger.error(f"Не удалось получить ссылку для файла {file_path}.")
        return

    try:
        file_response = requests.get(download_url)
        if file_response.status_code == 200:
            file_size = len(file_response.content) / (1024 * 1024)
            if file_size > 20:
                await update.message.reply_text("Файл слишком большой (>20 МБ).")
                logger.error(f"Файл {file_name} слишком большой: {file_size} МБ")
                return
            await update.message.reply_document(
                document=InputFile(file_response.content, filename=file_name)
            )
            logger.info(f"Файл {file_name} отправлен пользователю {user_id}.")
        else:
            await update.message.reply_text("Не удалось загрузить файл с Яндекс.Диска.")
            logger.error(
                f"Ошибка загрузки файла {file_path}: код {file_response.status_code}, ответ: {file_response.text}")
    except Exception as e:
        await update.message.reply_text(f"Ошибка при отправке файла: {str(e)}")
        logger.error(f"Ошибка при отправке файла {file_path}: {str(e)}")

# Обработка загруженных документов
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка загруженных документов."""
    user_id: int = update.effective_user.id
    if not context.user_data.get('awaiting_upload', False):
        await update.message.reply_text("Используйте кнопку 'Загрузить файл' перед отправкой документа.")
        return

    document = update.message.document
    file_name = document.file_name
    if not file_name.lower().endswith(('.pdf', '.doc', '.docx', '.xls', '.xlsx', '.cdr', '.eps', '.png', '.jpg', '.jpeg')):
        await update.message.reply_text("Поддерживаются только файлы .pdf, .doc, .docx, .xls, .xlsx, .cdr, .eps, .png, .jpg, .jpeg.")
        return

    file_size = document.file_size / (1024 * 1024)
    if file_size > 50:
        await update.message.reply_text("Файл слишком большой (>50 МБ).")
        return

    profile = USER_PROFILES.get(user_id)
    if not profile or "region" not in profile:
        await update.message.reply_text("Ошибка: регион не определён. Обновите профиль с /start.")
        return
    region_folder = f"/regions/{profile['region']}/"
    if not create_yandex_folder(region_folder):
        await update.message.reply_text("Ошибка: не удалось создать папку региона (проверьте токен Яндекс.Диска).")
        logger.error(f"Не удалось создать папку {region_folder} для пользователя {user_id}.")
        return

    try:
        file = await context.bot.get_file(document.file_id)
        file_content = await file.download_as_bytearray()
        if upload_to_yandex_disk(file_content, file_name, region_folder):
            await update.message.reply_text(f"Файл успешно загружен в папку {region_folder}")
        else:
            await update.message.reply_text("Ошибка при загрузке файла на Яндекс.Диск (проверьте токен).")
    except Exception as e:
        await update.message.reply_text(f"Ошибка при обработке файла: {str(e)}")
        logger.error(f"Ошибка обработки документа от {user_id}: {str(e)}")

    context.user_data.pop('awaiting_upload', None)
    logger.info(f"Пользователь {user_id} загрузил файл {file_name} в {region_folder}.")

# Отображение списка файлов (для регионов)
async def show_file_list(update: Update, context: ContextTypes.DEFAULT_TYPE, for_deletion: bool = False) -> None:
    """Показывает список файлов в папке региона."""
    user_id: int = update.effective_user.id
    profile = USER_PROFILES.get(user_id)
    if not profile or "region" not in profile:
        await update.message.reply_text("Ошибка: регион не определён. Обновите профиль с /start.",
                                        reply_markup=context.user_data.get('default_reply_markup',
                                                                          ReplyKeyboardRemove()))
        logger.error(f"Ошибка: регион не определён для пользователя {user_id}.")
        return

    region_folder = f"/regions/{profile['region']}/"
    if not create_yandex_folder(region_folder):
        await update.message.reply_text("Ошибка: не удалось создать/проверить папку региона (проверьте токен Яндекс.Диска).",
                                        reply_markup=context.user_data.get('default_reply_markup',
                                                                          ReplyKeyboardRemove()))
        logger.error(f"Не удалось создать папку {region_folder} для пользователя {user_id}.")
        return

    files = list_yandex_disk_files(region_folder)
    if not files:
        await update.message.reply_text(f"В папке {region_folder} нет файлов.",
                                        reply_markup=context.user_data.get('default_reply_markup',
                                                                          ReplyKeyboardRemove()))
        logger.info(f"Папка {region_folder} пуста для пользователя {user_id}.")
        return

    context.user_data['file_list'] = files
    keyboard = []
    for idx, item in enumerate(files):
        action = 'delete' if for_deletion else 'download'
        callback_data = f"{action}:{idx}"
        keyboard.append([InlineKeyboardButton(item['name'], callback_data=callback_data)])
    reply_markup = InlineKeyboardMarkup(keyboard)
    action_text = "Выберите файл для удаления:" if for_deletion else "Список всех файлов:"
    await update.message.reply_text(action_text, reply_markup=reply_markup)
    logger.info(f"Пользователь {user_id} запросил список файлов в {region_folder}: {[item['name'] for item in files]}")

# Отображение содержимого текущей папки в /documents/
async def show_current_docs(update: Update, context: ContextTypes.DEFAULT_TYPE, is_return: bool = False) -> None:
    """Показывает файлы и/или поддиректории в текущей папке в /documents/."""
    user_id: int = update.effective_user.id
    context.user_data.pop('file_list', None)
    current_path = context.user_data.get('current_path', '/documents/')
    folder_name = current_path.rstrip('/').split('/')[-1] or "Документы"
    if not create_yandex_folder(current_path):
        await update.message.reply_text(f"Ошибка: не удалось создать папку {current_path} (проверьте токен Яндекс.Диска).",
                                        reply_markup=context.user_data.get('default_reply_markup',
                                                                          ReplyKeyboardRemove()))
        logger.error(f"Не удалось создать папку {current_path} для пользователя {user_id}.")
        return

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
        file_keyboard = []
        for idx, item in enumerate(files):
            callback_data = f"doc_download:{idx}"
            file_keyboard.append([InlineKeyboardButton(item['name'], callback_data=callback_data)])
        file_reply_markup = InlineKeyboardMarkup(file_keyboard)
        await update.message.reply_text(f"Файлы в папке {folder_name}:", reply_markup=file_reply_markup)
        logger.info(
            f"Пользователь {user_id} получил список файлов в {current_path}: {[item['name'] for item in files]}")
    elif dirs:
        if not is_return:
            message = "Документы для РО" if current_path == '/documents/' else f"Папки в {folder_name}:"
            await update.message.reply_text(message, reply_markup=reply_markup)
        logger.info(f"Пользователь {user_id} получил список подпапок в {current_path}: {dirs}")
    else:
        await update.message.reply_text(f"Папка {folder_name} пуста.", reply_markup=reply_markup)
        logger.info(f"Папка {current_path} пуста для пользователя {user_id}.")

# Обработка callback-запросов
async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка кнопок для скачивания/удаления файлов."""
    query = update.callback_query
    await query.answer()

    user_id: int = update.effective_user.id
    profile = USER_PROFILES.get(user_id)
    default_reply_markup = context.user_data.get('default_reply_markup', ReplyKeyboardRemove())

    if not query.message:
        logger.error(f"Ошибка: query.message is None для user_id {user_id}")
        await query.message.reply_text("Ошибка: сообщение недоступно.", reply_markup=default_reply_markup)
        return

    if not profile or "region" not in profile:
        await query.message.reply_text("Ошибка: регион не определён. Перезапустите /start.",
                                       reply_markup=default_reply_markup)
        logger.error(f"Ошибка: регион не определён для пользователя {user_id}.")
        return

    region_folder = f"/regions/{profile['region']}/"
    if not create_yandex_folder(region_folder):
        await query.message.reply_text("Ошибка: не удалось создать папку региона (проверьте токен Яндекс.Диска).", reply_markup=default_reply_markup)
        logger.error(f"Не удалось создать папку {region_folder} для пользователя {user_id}.")
        return

    if query.data.startswith("doc_download:"):
        parts = query.data.split(":", 1)
        if len(parts) != 2:
            await query.message.reply_text("Ошибка: неверный формат запроса.", reply_markup=default_reply_markup)
            logger.error(f"Неверный формат callback_data: {query.data}")
            return
        try:
            file_idx = int(parts[1])
        except ValueError:
            await query.message.reply_text("Ошибка: неверный индекс файла.", reply_markup=default_reply_markup)
            logger.error(f"Неверный индекс в callback_data: {query.data}")
            return
        current_path = context.user_data.get('current_path', '/documents/')
        files = context.user_data.get('file_list', [])
        if not files:
            files = list_yandex_disk_files(current_path)
            context.user_data['file_list'] = files
            logger.info(f"Перезагружен file_list для {current_path}: {[item['name'] for item in files]}")
        if not files or file_idx >= len(files):
            await query.message.reply_text("Ошибка: файл не найден. Попробуйте обновить список.",
                                           reply_markup=default_reply_markup)
            logger.error(
                f"Файл с индексом {file_idx} не найден в file_list для user_id {user_id}, file_list: {[item['name'] for item in files]}")
            return
        file_name = files[file_idx]['name']
        file_path = f"{current_path.rstrip('/')}/{file_name}"
        download_url = get_yandex_disk_file(file_path)
        if not download_url:
            await query.message.reply_text("Ошибка: не удалось получить ссылку для скачивания (проверьте токен).",
                                           reply_markup=default_reply_markup)
            logger.error(f"Не удалось получить ссылку для файла {file_path}.")
            return

        try:
            file_response = requests.get(download_url)
            if file_response.status_code == 200:
                file_size = len(file_response.content) / (1024 * 1024)
                if file_size > 20:
                    await query.message.reply_text("Файл слишком большой (>20 МБ).", reply_markup=default_reply_markup)
                    logger.error(f"Файл {file_name} слишком большой: {file_size} МБ")
                    return
                await query.message.reply_document(
                    document=InputFile(file_response.content, filename=file_name)
                )
                logger.info(f"Файл {file_name} из {current_path} отправлен пользователю {user_id}.")
            else:
                await query.message.reply_text("Не удалось загрузить файл с Яндекс.Диска.",
                                               reply_markup=default_reply_markup)
                logger.error(
                    f"Ошибка загрузки файла {file_path}: код {file_response.status_code}, ответ: {file_response.text}")
        except Exception as e:
            await query.message.reply_text(f"Ошибка при отправке файла: {str(e)}", reply_markup=default_reply_markup)
            logger.error(f"Ошибка при отправке файла {file_path}: {str(e)}")
        return

    if query.data.startswith("download:") or query.data.startswith("delete:"):
        action, file_idx_str = query.data.split(":", 1)
        try:
            file_idx = int(file_idx_str)
        except ValueError:
            await query.message.reply_text("Ошибка: неверный индекс файла.", reply_markup=default_reply_markup)
            logger.error(f"Неверный индекс в callback_data: {query.data}")
            return

        files = context.user_data.get('file_list', [])
        if not files:
            files = list_yandex_disk_files(region_folder)
            context.user_data['file_list'] = files
            logger.info(f"Перезагружен file_list для {region_folder}: {[item['name'] for item in files]}")
        if not files or file_idx >= len(files):
            await query.message.reply_text("Ошибка: файл не найден. Попробуйте обновить список.",
                                           reply_markup=default_reply_markup)
            logger.error(
                f"Файл с индексом {file_idx} не найден в file_list для user_id {user_id}, file_list: {[item['name'] for item in files]}")
            return

        file_name = files[file_idx]['name']
        file_path = f"{region_folder.rstrip('/')}/{file_name}"

        if action == "download":
            if not file_name.lower().endswith(('.pdf', '.doc', '.docx', '.xls', '.xlsx', '.cdr', '.eps', '.png', '.jpg', '.jpeg')):
                await query.message.reply_text("Поддерживаются только файлы .pdf, .doc, .docx, .xls, .xlsx, .cdr, .eps, .png, .jpg, .jpeg.",
                                               reply_markup=default_reply_markup)
                logger.error(f"Неподдерживаемый формат файла {file_name} для пользователя {user_id}.")
                return

            download_url = get_yandex_disk_file(file_path)
            if not download_url:
                await query.message.reply_text("Ошибка: не удалось получить ссылку для скачивания (проверьте токен).",
                                               reply_markup=default_reply_markup)
                logger.error(f"Не удалось получить ссылку для файла {file_path}.")
                return

            try:
                file_response = requests.get(download_url)
                if file_response.status_code == 200:
                    file_size = len(file_response.content) / (1024 * 1024)
                    if file_size > 20:
                        await query.message.reply_text("Файл слишком большой (>20 МБ).",
                                                       reply_markup=default_reply_markup)
                        logger.error(f"Файл {file_name} слишком большой: {file_size} МБ")
                        return
                    await query.message.reply_document(
                        document=InputFile(file_response.content, filename=file_name)
                    )
                    logger.info(f"Файл {file_name} отправлен пользователю {user_id}.")
                else:
                    await query.message.reply_text("Не удалось загрузить файл с Яндекс.Диска.",
                                                   reply_markup=default_reply_markup)
                    logger.error(
                        f"Ошибка загрузки файла {file_path}: код {file_response.status_code}, ответ: {file_response.text}")
            except Exception as e:
                await query.message.reply_text(f"Ошибка при отправке файла: {str(e)}",
                                               reply_markup=default_reply_markup)
                logger.error(f"Ошибка при отправке файла {file_path}: {str(e)}")

        elif action == "delete":
            if user_id not in ALLOWED_ADMINS:
                await query.message.reply_text("Только администраторы могут удалять файлы.",
                                               reply_markup=default_reply_markup)
                logger.info(f"Пользователь {user_id} попытался удалить файл.")
                return

            if delete_yandex_disk_file(file_path):
                await query.message.reply_text(f"Файл '{file_name}' удалён из папки {region_folder}.",
                                               reply_markup=default_reply_markup)
                logger.info(f"Администратор {user_id} удалил файл {file_name}.")
            else:
                await query.message.reply_text(f"Ошибка при удалении файла '{file_name}' (проверьте токен).",
                                               reply_markup=default_reply_markup)
                logger.error(f"Ошибка при удалении файла {file_name} для пользователя {user_id}.")

            context.user_data.pop('file_list', None)
            await show_file_list(update, context, for_deletion=True)

# Обработка текстовых сообщений
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка текстовых сообщений: регистрация, команды, поиск."""
    if update.effective_user is None or update.effective_chat is None:
        logger.error("Ошибка: update.effective_user или update.effective_chat is None")
        await update.message.reply_text("Ошибка: не удалось определить пользователя или чат.")
        return

    user_id: int = update.effective_user.id
    chat_id: int = update.effective_chat.id
    user_input: str = update.message.text.strip()
    logger.info(f"Получено сообщение от {chat_id} (user_id: {user_id}): {user_input}")
    log_request(user_id, user_input)

    if user_id not in ALLOWED_USERS and user_id not in ALLOWED_ADMINS:
        await update.message.reply_text("Извините, у вас нет доступа.", reply_markup=ReplyKeyboardRemove())
        logger.info(f"Пользователь {user_id} попытался отправить сообщение.")
        return

    if user_id not in USER_PROFILES:
        if context.user_data.get("awaiting_fio", False):
            logger.info(f"Сохранение ФИО для user_id {user_id}: {user_input}")
            USER_PROFILES[user_id] = {"fio": user_input, "name": None, "region": None}
            save_user_profile(user_id, USER_PROFILES[user_id])
            context.user_data["awaiting_fio"] = False
            context.user_data["awaiting_federal_district"] = True
            keyboard = [[district] for district in FEDERAL_DISTRICTS.keys()]
            reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
            await update.message.reply_text("Выберите федеральный округ:", reply_markup=reply_markup)
            return
        else:
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

    if context.user_data.get("awaiting_federal_district", False):
        if user_input in FEDERAL_DISTRICTS:
            context.user_data["selected_federal_district"] = user_input
            context.user_data["awaiting_federal_district"] = False
            context.user_data["awaiting_region"] = True
            regions = FEDERAL_DISTRICTS[user_input]
            keyboard = [[region] for region in regions]
            reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
            await update.message.reply_text("Выберите регион:", reply_markup=reply_markup)
            return
        else:
            await update.message.reply_text("Пожалуйста, выберите из предложенных округов.",
                                            reply_markup=ReplyKeyboardMarkup(
                                                [[district] for district in FEDERAL_DISTRICTS.keys()],
                                                resize_keyboard=True))
            return

    if context.user_data.get("awaiting_region", False):
        selected_district = context.user_data.get("selected_federal_district")
        regions = FEDERAL_DISTRICTS.get(selected_district, [])
        if user_input in regions:
            logger.info(f"Сохранение региона для user_id {user_id}: {user_input}")
            USER_PROFILES[user_id]["region"] = user_input
            save_user_profile(user_id, USER_PROFILES[user_id])
            region_folder = f"/regions/{user_input}/"
            if not create_yandex_folder(region_folder):
                await update.message.reply_text("Ошибка: не удалось создать папку региона (проверьте токен Яндекс.Диска).")
                logger.error(f"Не удалось создать папку {region_folder} для пользователя {user_id}.")
                return
            context.user_data.pop("awaiting_region", None)
            context.user_data.pop("selected_federal_district", None)
            context.user_data["awaiting_name"] = True
            await update.message.reply_text("Как я могу к Вам обращаться (кратко для удобства)?",
                                            reply_markup=ReplyKeyboardRemove())
            logger.info(f"Пользователь {user_id} зарегистрирован с регионом {user_input}.")
            return
        else:
            await update.message.reply_text("Пожалуйста, выберите из предложенных регионов.",
                                            reply_markup=ReplyKeyboardMarkup([[region] for region in regions],
                                                                             resize_keyboard=True))
            return

    if context.user_data.get("awaiting_name", False):
        logger.info(f"Сохранение имени для user_id {user_id}: {user_input}")
        profile = USER_PROFILES[user_id]
        profile["name"] = user_input
        save_user_profile(user_id, profile)
        context.user_data["awaiting_name"] = False
        await show_main_menu(update, context)
        reply_markup = context.user_data.get('default_reply_markup', ReplyKeyboardRemove())
        await update.message.reply_text(
            f"Рад знакомству, {user_input}! Задавайте вопросы или используйте меню.",
            reply_markup=reply_markup
        )
        logger.info(f"Имя пользователя {chat_id} сохранено: {user_input}")
        return

    handled = False

    if user_input == "Документы для РО":
        context.user_data['current_mode'] = 'documents_nav'
        context.user_data['current_path'] = '/documents/'
        context.user_data.pop('file_list', None)
        if not create_yandex_folder('/documents/'):
            await update.message.reply_text("Ошибка: не удалось создать папку /documents/ (проверьте токен).")
            logger.error(f"Не удалось создать папку /documents/ для пользователя {user_id}.")
            return
        await show_current_docs(update, context)
        handled = True

    if user_input == "Архив документов РО":
        context.user_data.pop('current_mode', None)
        context.user_data.pop('current_path', None)
        context.user_data.pop('file_list', None)
        await show_file_list(update, context)
        handled = True

    if user_input == "Управление пользователями":
        if user_id not in ALLOWED_ADMINS:
            await update.message.reply_text("Только администраторы могут управлять пользователями.",
                                            reply_markup=default_reply_markup)
            logger.info(f"Пользователь {user_id} попытался использовать управление пользователями.")
            return
        keyboard = [
            ['Добавить пользователя', 'Добавить администратора'],
            ['Список пользователей', 'Список администраторов'],
            ['Все факты', 'Добавить факт', 'Удалить факт'],
            ['Удалить файл'],
            ['Назад']
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        context.user_data.pop('current_mode', None)
        context.user_data.pop('current_path', None)
        context.user_data.pop('file_list', None)
        await update.message.reply_text("Выберите действие:", reply_markup=reply_markup)
        logger.info(f"Администратор {user_id} запросил управление пользователями.")
        handled = True

    if user_input == "Загрузить файл":
        profile = USER_PROFILES.get(user_id)
        if not profile or "region" not in profile:
            await update.message.reply_text("Ошибка: регион не определён. Обновите профиль с /start.",
                                            reply_markup=default_reply_markup)
            return
        context.user_data.pop('current_mode', None)
        context.user_data.pop('current_path', None)
        context.user_data.pop('file_list', None)
        await update.message.reply_text(
            "Отправьте файл для загрузки.",
            reply_markup=default_reply_markup
        )
        context.user_data['awaiting_upload'] = True
        logger.info(f"Пользователь {user_id} начал загрузку файла.")
        handled = True

    if user_input == "Удалить файл":
        if user_id not in ALLOWED_ADMINS:
            await update.message.reply_text("Только администраторы могут удалять файлы.",
                                            reply_markup=default_reply_markup)
            logger.info(f"Пользователь {user_id} попытался удалить файл.")
            return
        context.user_data['awaiting_delete'] = True
        context.user_data.pop('current_mode', None)
        context.user_data.pop('current_path', None)
        context.user_data.pop('file_list', None)
        await show_file_list(update, context, for_deletion=True)
        handled = True

    if user_input == "Назад":
        await show_main_menu(update, context)
        handled = True

    if context.user_data.get('current_mode') == 'documents_nav':
        current_path = context.user_data.get('current_path', '/documents/')
        logger.info(f"Пользователь {user_id} пытается перейти в папку: {user_input}, текущий путь: {current_path}")
        dirs = list_yandex_disk_directories(current_path)
        if user_input in dirs:
            context.user_data.pop('file_list', None)
            context.user_data['current_path'] = f"{current_path.rstrip('/')}/{user_input}/"
            logger.info(f"Пользователь {user_id} перешёл в папку: {context.user_data['current_path']}")
            if not create_yandex_folder(context.user_data['current_path']):
                await update.message.reply_text(
                    f"Ошибка: не удалось создать папку {context.user_data['current_path']} (проверьте токен).",
                    reply_markup=default_reply_markup)
                logger.error(
                    f"Не удалось создать папку {context.user_data['current_path']} для пользователя {user_id}.")
                return
            await show_current_docs(update, context)
            handled = True
        elif user_input == 'В главное меню':
            logger.info(f"Пользователь {user_id} вернулся в главное меню из {current_path}")
            await show_main_menu(update, context)
            handled = True
        elif user_input == 'Назад' and current_path != '/documents/':
            context.user_data.pop('file_list', None)
            parts = current_path.rstrip('/').split('/')
            new_path = '/'.join(parts[:-1]) + '/' if len(parts) > 2 else '/documents/'
            context.user_data['current_path'] = new_path
            logger.info(f"Пользователь {user_id} вернулся назад в {new_path}")
            await show_current_docs(update, context, is_return=True)
            handled = True

    if context.user_data.get('awaiting_user_id'):
        try:
            new_id = int(user_input)
            if context.user_data['awaiting_user_id'] == 'add_user':
                if new_id in ALLOWED_USERS:
                    await update.message.reply_text(f"Пользователь с ID {new_id} уже имеет доступ.",
                                                    reply_markup=default_reply_markup)
                    return
                ALLOWED_USERS.append(new_id)
                add_allowed_user(new_id)
                await update.message.reply_text(f"Пользователь с ID {new_id} добавлен!",
                                                reply_markup=default_reply_markup)
                logger.info(f"Администратор {user_id} добавил пользователя {new_id}.")
            elif context.user_data['awaiting_user_id'] == 'add_admin':
                if new_id in ALLOWED_ADMINS:
                    await update.message.reply_text(f"Пользователь с ID {new_id} уже администратор.",
                                                    reply_markup=default_reply_markup)
                    return
                ALLOWED_ADMINS.append(new_id)
                add_allowed_admin(new_id)
                await update.message.reply_text(f"Пользователь с ID {new_id} назначен администратором!",
                                                reply_markup=default_reply_markup)
                logger.info(f"Администратор {user_id} назначил администратора {new_id}.")
            context.user_data.pop('awaiting_user_id', None)
            handled = True
        except ValueError:
            await update.message.reply_text("Ошибка: user_id должен быть числом.", reply_markup=default_reply_markup)
            logger.error(f"Ошибка: Неверный формат user_id от {user_id}.")
            handled = True

    if user_input == "Добавить пользователя":
        if user_id not in ALLOWED_ADMINS:
            await update.message.reply_text("Только администраторы могут добавлять пользователей.",
                                            reply_markup=default_reply_markup)
            logger.info(f"Пользователь {user_id} попытался добавить пользователя.")
            return
        await update.message.reply_text("Укажите user_id для добавления.",
                                        reply_markup=default_reply_markup)
        context.user_data['awaiting_user_id'] = 'add_user'
        logger.info(f"Администратор {user_id} запросил добавление пользователя.")
        handled = True

    if user_input == "Добавить администратора":
        if user_id not in ALLOWED_ADMINS:
            await update.message.reply_text("Только администраторы могут назначать администраторов.",
                                            reply_markup=default_reply_markup)
            logger.info(f"Пользователь {user_id} попытался добавить администратора.")
            return
        await update.message.reply_text("Укажите user_id для назначения администратором.",
                                        reply_markup=default_reply_markup)
        context.user_data['awaiting_user_id'] = 'add_admin'
        logger.info(f"Администратор {user_id} запросил добавление администратора.")
        handled = True

    if user_input == "Список пользователей":
        if user_id not in ALLOWED_ADMINS:
            await update.message.reply_text("Только администраторы могут просматривать список пользователей.",
                                            reply_markup=default_reply_markup)
            logger.info(f"Пользователь {user_id} попытался просмотреть список пользователей.")
            return
        ALLOWED_USERS = load_allowed_users()
        if not ALLOWED_USERS:
            await update.message.reply_text("Список пользователей пуст.", reply_markup=default_reply_markup)
            return
        users_list = "\n".join([f"ID: {uid}" for uid in ALLOWED_USERS])
        await update.message.reply_text(f"Разрешённые пользователи:\n{users_list}", reply_markup=default_reply_markup)
        logger.info(f"Администратор {user_id} запросил список пользователей.")
        handled = True

    if user_input == "Список администраторов":
        if user_id not in ALLOWED_ADMINS:
            await update.message.reply_text("Только администраторы могут просматривать список администраторов.",
                                            reply_markup=default_reply_markup)
            logger.info(f"Пользователь {user_id} попытался просмотреть список администраторов.")
            return
        ALLOWED_ADMINS = load_allowed_admins()
        if not ALLOWED_ADMINS:
            await update.message.reply_text("Список администраторов пуст.", reply_markup=default_reply_markup)
            return
        admins_list = "\n".join([f"ID: {uid}" for uid in ALLOWED_ADMINS])
        await update.message.reply_text(f"Администраторы:\n{admins_list}", reply_markup=default_reply_markup)
        logger.info(f"Администратор {user_id} запросил список администраторов.")
        handled = True

    if user_input == "Все факты":
        if user_id not in ALLOWED_ADMINS:
            await update.message.reply_text("Только администраторы могут просматривать факты.", reply_markup=default_reply_markup)
            return
        facts_with_ids = list_knowledge_with_ids()
        if not facts_with_ids:
            await update.message.reply_text("База знаний пуста.", reply_markup=default_reply_markup)
            return
        facts_text = "\n".join([f"{f['id']}: {f['fact']}" for f in facts_with_ids])
        await update.message.reply_text(f"Все факты (с ID):\n{facts_text}", reply_markup=default_reply_markup)
        handled = True

    if user_input == "Добавить факт":
        if user_id not in ALLOWED_ADMINS:
            await update.message.reply_text("Только администраторы могут добавлять факты.", reply_markup=default_reply_markup)
            return
        await update.message.reply_text("Введите текст факта для добавления:", reply_markup=default_reply_markup)
        context.user_data['awaiting_fact_add'] = True
        handled = True

    if user_input == "Удалить факт":
        if user_id not in ALLOWED_ADMINS:
            await update.message.reply_text("Только администраторы могут удалять факты.", reply_markup=default_reply_markup)
            return
        await update.message.reply_text("Введите ID факта для удаления:", reply_markup=default_reply_markup)
        context.user_data['awaiting_fact_delete'] = True
        handled = True

    if context.user_data.get('awaiting_fact_add'):
        fact = user_input.strip()
        add_knowledge(fact)
        global KNOWLEDGE_BASE
        KNOWLEDGE_BASE = load_knowledge_base()
        await update.message.reply_text(f"Факт добавлен: '{fact}'.", reply_markup=default_reply_markup)
        context.user_data.pop('awaiting_fact_add')
        handled = True

    if context.user_data.get('awaiting_fact_delete'):
        try:
            fact_id = int(user_input)
            if remove_knowledge_by_id(fact_id):
                global KNOWLEDGE_BASE
                KNOWLEDGE_BASE = load_knowledge_base()
                await update.message.reply_text(f"Факт с ID {fact_id} удалён.", reply_markup=default_reply_markup)
            else:
                await update.message.reply_text(f"Факт с ID {fact_id} не найден.", reply_markup=default_reply_markup)
        except ValueError:
            await update.message.reply_text("ID должен быть числом.", reply_markup=default_reply_markup)
        context.user_data.pop('awaiting_fact_delete')
        handled = True

    if not handled:
        if chat_id not in histories:
            histories[chat_id] = {"name": None, "messages": [{"role": "system", "content": system_prompt}]}

        global KNOWLEDGE_BASE
        if KNOWLEDGE_BASE:
            knowledge_text = "Известные факты для использования в ответах: " + "; ".join(KNOWLEDGE_BASE)
            histories[chat_id]["messages"].insert(1, {"role": "system", "content": knowledge_text})
            logger.info(f"Добавлены знания в контекст для user_id {user_id}: {len(KNOWLEDGE_BASE)} фактов")

        need_search = any(word in user_input.lower() for word in [
            "актуальная информация", "последние новости", "найди в интернете", "поиск",
            "что такое", "информация о", "расскажи о", "найди", "поиск по", "детали о",
            "вскс", "спасатели", "корпус спасателей"
        ])

        if need_search:
            logger.info(f"Выполняется поиск для запроса: {user_input}")
            search_results_json = web_search(user_input)
            try:
                results = json.loads(search_results_json)
                if isinstance(results, list):
                    extracted_text = "\n".join(
                        [f"Источник: {r.get('title', '')}\n{r.get('body', '')}" for r in results if r.get('body')])
                else:
                    extracted_text = search_results_json
                histories[chat_id]["messages"].append({"role": "system", "content": f"Актуальные факты: {extracted_text}"})
                logger.info(f"Извлечено из поиска: {extracted_text[:200]}...")
            except json.JSONDecodeError:
                histories[chat_id]["messages"].append(
                    {"role": "system", "content": f"Ошибка поиска: {search_results_json}"})

        histories[chat_id]["messages"].append({"role": "user", "content": user_input})
        if len(histories[chat_id]["messages"]) > 20:
            histories[chat_id]["messages"] = histories[chat_id]["messages"][:1] + histories[chat_id]["messages"][-19:]

        messages = histories[chat_id]["messages"]

        # Модели для HF (OpenAI-совместимые)
        models_to_try = ["microsoft/DialoGPT-medium", "gpt2"]  # Примеры HF моделей; измените на нужные
        response_text = "Извините, не удалось получить ответ от HF API. Проверьте HF_TOKEN и модель."

        for model in models_to_try:
            try:
                # Для HF используем conversations API, но адаптируем под OpenAI
                completion = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=0.7,
                    stream=False
                )
                response_text = completion.choices[0].message.content.strip()
                logger.info(f"Ответ модели {model} для user_id {user_id}: {response_text}")
                break
            except openai.AuthenticationError as auth_err:
                logger.error(f"Ошибка авторизации для {model}: {str(auth_err)}")
                response_text = "Ошибка авторизации: неверный HF_TOKEN."
                break
            except openai.APIError as api_err:
                if "401" in str(api_err):
                    logger.warning(f"401 Unauthorized для {model}. Пробуем следующую модель.")
                    continue
                logger.error(f"Ошибка API для {model}: {str(api_err)}")
                response_text = f"Ошибка API: {str(api_err)}"
                break
            except openai.RateLimitError as rate_err:
                logger.error(f"Превышен лимит для {model}: {str(rate_err)}")
                response_text = "Превышен лимит запросов. Попробуйте позже."
                break
            except Exception as e:
                logger.error(f"Неизвестная ошибка для {model}: {str(e)}")
                response_text = f"Неизвестная ошибка: {str(e)}"
                break
        else:
            logger.error("Все модели недоступны. Проверьте HF_TOKEN.")
            response_text = "Все модели недоступны. Обновите HF_TOKEN."

        user_name = USER_PROFILES.get(user_id, {}).get("name", "Друг")
        final_response = f"{user_name}, {response_text}"
        histories[chat_id]["messages"].append({"role": "assistant", "content": response_text})
        await update.message.reply_text(final_response, reply_markup=default_reply_markup)
        log_request(user_id, user_input, response_text)

# Обработчик ошибок
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка ошибок бота."""
    logger.error(f"Update {update} caused error {context.error}")
    if update and update.message:
        await update.message.reply_text("Произошла ошибка, попробуйте позже.")

# Главная функция
def main() -> None:
    """Запуск бота."""
    logger.info("Запуск Telegram бота...")
    # Создание корневых папок с обработкой ошибок
    if not create_yandex_folder('/regions/'):
        logger.error("Не удалось создать папку /regions/ (проверьте YANDEX_TOKEN). Бот запустится, но функции Диска не будут работать.")
    if not create_yandex_folder('/documents/'):
        logger.error("Не удалось создать папку /documents/ (проверьте YANDEX_TOKEN). Бот запустится, но функции Диска не будут работать.")
    try:
        app = Application.builder().token(TELEGRAM_TOKEN).build()
        app.add_handler(CommandHandler("start", send_welcome))
        app.add_handler(CommandHandler("getfile", get_file))
        app.add_handler(CommandHandler("learn", handle_learn))
        app.add_handler(CommandHandler("forget", handle_forget))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
        app.add_handler(CallbackQueryHandler(handle_callback_query))
        app.add_error_handler(error_handler)
        app.run_polling()
    except Exception as e:
        logger.error(f"Ошибка при запуске бота: {str(e)}")

if __name__ == "__main__":
    main()