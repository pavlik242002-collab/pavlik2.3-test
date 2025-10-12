from __future__ import annotations

import os
import logging
import requests
import json
import uuid
from datetime import datetime, timedelta
from typing import Dict, List, Any
from dotenv import load_dotenv
from telegram import ReplyKeyboardMarkup, ReplyKeyboardRemove, InlineKeyboardMarkup, InlineKeyboardButton, Update
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes, JobQueue
from telegram import InputFile
from urllib.parse import quote
from openai import OpenAI
import psycopg2
from duckduckgo_search import DDGS
import pandas as pd
from io import BytesIO

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
XAI_MODEL = os.getenv("XAI_MODEL", "grok-3")

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
                logger.info("Таблица request_logs уже существует.")

            cur.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_name = 'knowledge_base'
                );
            """)
            if not cur.fetchone()[0]:
                cur.execute("""
                    CREATE TABLE knowledge_base (
                        id SERIAL PRIMARY KEY,
                        fact_text TEXT NOT NULL,
                        added_by BIGINT NOT NULL,
                        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                """)
                logger.info("Таблица knowledge_base создана.")
            else:
                logger.info("Таблица knowledge_base уже существует.")

            cur.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_name = 'reports'
                );
            """)
            if not cur.fetchone()[0]:
                cur.execute("""
                    CREATE TABLE reports (
                        id SERIAL PRIMARY KEY,
                        report_id UUID NOT NULL,
                        user_id BIGINT NOT NULL,
                        week_number INTEGER NOT NULL,
                        year INTEGER NOT NULL,
                        questions TEXT[] NOT NULL,
                        answers TEXT[],
                        status VARCHAR(20) DEFAULT 'pending',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        reminder_sent_at TIMESTAMP
                    );
                """)
                logger.info("Таблица reports создана.")
            else:
                logger.info("Таблица reports уже существует.")

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

def delete_allowed_user(user_id_to_delete: int, admin_id: int) -> bool:
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM allowed_users WHERE id = %s", (user_id_to_delete,))
            if cur.rowcount > 0:
                conn.commit()
                logger.info(f"Пользователь с ID {user_id_to_delete} удален администратором {admin_id}")
                return True
            else:
                logger.warning(
                    f"Пользователь с ID {user_id_to_delete} не найден для удаления администратором {admin_id}")
                return False
    except Exception as e:
        logger.error(f"Ошибка при удалении пользователя с ID {user_id_to_delete}: {str(e)}")
        conn.rollback()
        return False

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

# Функции для работы с базой знаний
def load_knowledge_base() -> List[Dict[str, Any]]:
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, fact_text FROM knowledge_base ORDER BY timestamp DESC")
            facts = [{"id": row[0], "text": row[1]} for row in cur.fetchall()]
            logger.info(f"Загружено {len(facts)} фактов из таблицы knowledge_base")
            return facts
    except Exception as e:
        logger.error(f"Ошибка при загрузке knowledge_base: {str(e)}")
        conn.rollback()
        return []

def save_knowledge_fact(fact: str, added_by: int) -> None:
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO knowledge_base (fact_text, added_by) VALUES (%s, %s)",
                (fact.strip(), added_by)
            )
            conn.commit()
            logger.info(f"Факт '{fact}' добавлен в knowledge_base администратором {added_by}")
    except Exception as e:
        logger.error(f"Ошибка при сохранении факта в knowledge_base: {str(e)}")
        conn.rollback()

def delete_knowledge_fact(fact_id: int, admin_id: int) -> bool:
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM knowledge_base WHERE id = %s", (fact_id,))
            if cur.rowcount > 0:
                conn.commit()
                logger.info(f"Факт с ID {fact_id} удален администратором {admin_id}")
                return True
            else:
                logger.warning(f"Факт с ID {fact_id} не найден для удаления администратором {admin_id}")
                return False
    except Exception as e:
        logger.error(f"Ошибка при удалении факта с ID {fact_id}: {str(e)}")
        conn.rollback()
        return False
        # Функции для работы с отчетами
def create_report(report_id: str, user_id: int, questions: List[str], week_number: int, year: int) -> None:
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO reports (report_id, user_id, week_number, year, questions, answers, status, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                """,
                (report_id, user_id, week_number, year, questions, [], 'pending')
            )
            conn.commit()
            logger.info(f"Отчет {report_id} создан для пользователя {user_id} на неделю {week_number} {year}")
    except Exception as e:
        logger.error(f"Ошибка при создании отчета {report_id} для {user_id}: {str(e)}")
        conn.rollback()

def update_report_answers(report_id: str, user_id: int, answers: List[str], status: str = 'in_progress') -> bool:
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE reports 
                SET answers = %s, status = %s, updated_at = NOW()
                WHERE report_id = %s AND user_id = %s
                """,
                (answers, status, report_id, user_id)
            )
            if cur.rowcount > 0:
                conn.commit()
                logger.info(f"Отчет {report_id} обновлен для пользователя {user_id}")
                return True
            return False
    except Exception as e:
        logger.error(f"Ошибка при обновлении отчета {report_id} для {user_id}: {str(e)}")
        conn.rollback()
        return False

def check_overdue_reports() -> List[Dict[str, Any]]:
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT report_id, user_id, questions, reminder_sent_at
                FROM reports 
                WHERE status != 'completed' 
                AND (reminder_sent_at IS NULL OR reminder_sent_at < %s)
                AND created_at < %s
                """,
                (datetime.now() - timedelta(hours=24), datetime.now() - timedelta(hours=24))
            )
            overdue = [
                {"report_id": row[0], "user_id": row[1], "questions": row[2], "reminder_sent_at": row[3]}
                for row in cur.fetchall()
            ]
            logger.info(f"Найдено {len(overdue)} просроченных отчетов")
            return overdue
    except Exception as e:
        logger.error(f"Ошибка при проверке просроченных отчетов: {str(e)}")
        return []

def get_reports_by_week(week_number: int, year: int) -> List[Dict[str, Any]]:
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT report_id, user_id, questions, answers, status, created_at
                FROM reports 
                WHERE week_number = %s AND year = %s
                ORDER BY created_at
                """,
                (week_number, year)
            )
            reports = [
                {
                    "report_id": row[0],
                    "user_id": row[1],
                    "questions": row[2],
                    "answers": row[3],
                    "status": row[4],
                    "created_at": row[5]
                }
                for row in cur.fetchall()
            ]
            logger.info(f"Найдено {len(reports)} отчетов за неделю {week_number} {year}")
            return reports
    except Exception as e:
        logger.error(f"Ошибка при получении отчетов за неделю {week_number} {year}: {str(e)}")
        return []

# Улучшенный поиск фактов (топ-5 релевантных)
def find_knowledge_facts(query: str, knowledge_base: List[Dict[str, Any]]) -> List[str]:
    query_lower = query.lower().strip()
    synonyms = {
        "вскс": ["вскс", "студенческий корпус спасателей", "спасатели"],
        "андреев": ["андреев", "алексей евгеньевич"],
        "гуманитарные миссии": ["гуманитарные", "миссии", "помощь"],
    }

    scores = []
    for fact in knowledge_base:
        fact_lower = fact['text'].lower()
        score = 0
        if query_lower in fact_lower:
            score += 3
        query_words = query_lower.split()
        score += sum(1 for word in query_words if word in fact_lower)
        for syn_key, syn_list in synonyms.items():
            if syn_key in query_lower:
                score += sum(1 for syn in syn_list if syn in fact_lower)
        if score > 0:
            scores.append((score, fact['text']))

    scores.sort(key=lambda x: x[0], reverse=True)
    matching_facts = [fact for _, fact in scores[:5]]
    logger.info(
        f"Найдено {len(matching_facts)} релевантных фактов для '{query}': {[f[:50] + '...' for f in matching_facts]}")
    return matching_facts

# Функция для веб-поиска
def web_search(query: str) -> str:
    cache_file = 'search_cache.json'
    try:
        if not os.path.exists(cache_file):
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
            logger.error(
                f"Неожиданный статус при проверке папки {folder_path}: {response.status_code} - {response.text}")
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
KNOWLEDGE_BASE = load_knowledge_base()

# Системный промпт
system_prompt = """
Ты — полезный чат-бот ВСКС. Всегда отвечай на русском языке, кратко, по делу. Начинай ответ с "{user_name}, ".

ПРИОРИТЕТ: Используй факты из базы знаний как основной источник. Если релевантные факты предоставлены, объединяй их в связный ответ, добавляя объяснения и предложения уточнить.

Примеры ответов:
- Запрос: "кто такой Андреев Алексей?"
  Ответ: "Кристина, Андреев Алексей Евгеньевич — заместитель руководителя Всероссийского студенческого корпуса спасателей (ВСКС) по развитию региональных отделений и взаимодействию с ними. Он отвечает за координацию работы с региональными структурами организации. Если есть конкретные вопросы, связанные с его деятельностью, могу помочь уточнить детали."

- Запрос: "Что такое ВСКС?"
  Ответ: "Кристина, ВСКС — это Всероссийский студенческий корпус спасателей. Организация основана 22 апреля 2001 года по инициативе Министра МЧС России Сергея Кужугетовича Шойгу. ВСКС объединяет более 8 000 добровольцев из 88 субъектов РФ. Основные задачи включают участие в ликвидации последствий чрезвычайных ситуаций (ЧС), проведение гуманитарных миссий, подготовку студентов-спасателей и организацию мероприятий, таких как форумы и слёты. Если есть вопросы о структуре, задачах или участии, готов рассказать подробнее!"

Если фактов нет, используй веб-поиск или свои знания, но всегда проверяй на актуальность.
"""

# Сохранение истории переписки
histories: Dict[int, Dict[str, Any]] = {}

# Функция для генерации AI-ответа
async def generate_ai_response(user_id: int, user_input: str, user_name: str, chat_id: int) -> str:
    global KNOWLEDGE_BASE
    if not user_input.strip():
        return f"{user_name}, введите корректный запрос."
    if not KNOWLEDGE_BASE:
        KNOWLEDGE_BASE = load_knowledge_base()

    matching_facts = find_knowledge_facts(user_input, KNOWLEDGE_BASE)
    if chat_id not in histories:
        histories[chat_id] = {"name": user_name, "messages": [
            {"role": "system", "content": system_prompt.replace("{user_name}", user_name)}]}

    messages = histories[chat_id]["messages"]
    if matching_facts:
        facts_text = "\n".join(matching_facts)
        fact_prompt = f"""
Используй ТОЛЬКО эти релевантные факты из базы знаний для ответа на вопрос '{user_input}'.
Факты: {facts_text}

Объедини факты в связный, информативный ответ. Добавь объяснения, структуру и предложение уточнить. 
Не добавляй информацию извне.
        """
        messages.append({"role": "system", "content": fact_prompt})
        logger.info(f"Генерирую ответ на основе {len(matching_facts)} фактов для user_id {user_id}")
    else:
        if any(word in user_input.lower() for word in ["вскс", "спасатели", "корпус"]):
            top_facts = [fact['text'] for fact in KNOWLEDGE_BASE[:10]]
            facts_text = "; ".join(top_facts)
            messages.append({"role": "system", "content": f"База знаний (используй как приоритет): {facts_text}"})
        need_search = any(word in user_input.lower() for word in [
            "актуальная информация", "последние новости", "найди в интернете", "поиск",
            "что такое", "информация о", "расскажи о", "найди", "поиск по", "детали о"
        ])
        if need_search:
            search_results_json = web_search(user_input)
            try:
                results = json.loads(search_results_json)
                if isinstance(results, list):
                    extracted_text = "\n".join(
                        [f"Источник: {r.get('title', '')}\n{r.get('body', '')}" for r in results])
                    messages.append({"role": "system", "content": f"Актуальные факты из поиска: {extracted_text}"})
            except json.JSONDecodeError:
                pass

    messages.append({"role": "user", "content": user_input})
    if len(messages) > 20:
        messages = messages[:1] + messages[-19:]

    models_to_try = [XAI_MODEL, "grok", "grok-3", "grok-4"]
    ai_response = "Извините, не удалось получить ответ от API. Проверьте подписку на SuperGrok или X Premium+."

    for model in models_to_try:
        try:
            completion = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0.7,
                stream=False
            )
            ai_response = completion.choices[0].message.content.strip()
            logger.info(f"Ответ модели {model} для user_id {user_id}: {ai_response[:100]}...")
            break
        except Exception as e:
            logger.error(f"Ошибка для {model}: {str(e)}")
            continue

    histories[chat_id]["messages"].append({"role": "assistant", "content": ai_response})
    return ai_response

# Функция для получения user_name
def get_user_name(user_id: int) -> str:
    profile = USER_PROFILES.get(user_id)
    return profile.get("name") or "Пользователь" if profile else "Пользователь"

# Обработчик команды /start
async def send_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id: int = update.effective_user.id
    user_name = get_user_name(user_id)
    if user_id not in ALLOWED_USERS and user_id not in ALLOWED_ADMINS:
        await update.message.reply_text(f"{user_name}, ваш user_id: {user_id}\nИзвините, у вас нет доступа.",
                                        reply_markup=ReplyKeyboardRemove())
        return
    if user_id not in USER_PROFILES:
        context.user_data["awaiting_fio"] = True
        await update.message.reply_text("Пожалуйста, напишите своё ФИО.", reply_markup=ReplyKeyboardRemove())
        return
    profile = USER_PROFILES[user_id]
    if profile.get("name") is None:
        context.user_data["awaiting_name"] = True
        await update.message.reply_text("Как я могу к вам обращаться? Укажите краткое имя (например, Кристина).",
                                        reply_markup=ReplyKeyboardRemove())
    else:
        await show_main_menu(update, context)

# Отображение главного меню
async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id: int = update.effective_user.id
    user_name = get_user_name(user_id)
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
    context.user_data.pop('awaiting_fact_id', None)
    context.user_data.pop('awaiting_delete_user_id', None)
    context.user_data.pop('awaiting_new_fact', None)
    context.user_data.pop('awaiting_broadcast', None)
    context.user_data.pop('broadcast_type', None)
    context.user_data.pop('awaiting_report_week', None)
    await update.message.reply_text(f"{user_name}, выберите действие:", reply_markup=reply_markup)

# Отображение меню управления пользователями
async def show_admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id: int = update.effective_user.id
    user_name = get_user_name(user_id)
    keyboard = [
        ['Добавить пользователя', 'Добавить администратора'],
        ['Список пользователей', 'Список администраторов'],
        ['Удалить пользователя', 'Удалить файл'],
        ['Все факты (с ID)', 'Добавить факт'],
        ['Удалить факт', 'Рассылка'],
        ['Просмотреть отчеты', 'Выгрузить отчеты в Excel'],
        ['Назад']
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text(f"{user_name}, выберите действие:", reply_markup=reply_markup)

# Отображение меню рассылки
async def show_broadcast_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id: int = update.effective_user.id
    user_name = get_user_name(user_id)
    keyboard = [
        ['Рассылка пользователям', 'Рассылка админам', 'Отчеты'],
        ['Назад']
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text(f"{user_name}, выберите тип рассылки:", reply_markup=reply_markup)

# Отображение содержимого папки в /documents/
async def show_current_docs(update: Update, context: ContextTypes.DEFAULT_TYPE, is_return: bool = False) -> None:
    user_id: int = update.effective_user.id
    user_name = get_user_name(user_id)
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
        await update.message.reply_text(f"{user_name}, файлы в папке {folder_name}:", reply_markup=file_reply_markup)
    elif dirs:
        if not is_return:
            message = "Документы для РО" if current_path == '/documents/' else f"Папки в {folder_name}:"
            await update.message.reply_text(f"{user_name}, {message}", reply_markup=reply_markup)
    else:
        await update.message.reply_text(f"{user_name}, папка {folder_name} пуста.", reply_markup=reply_markup)

# Отображение файлов в папке региона
async def show_file_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id: int = update.effective_user.id
    user_name = get_user_name(user_id)
    profile = USER_PROFILES.get(user_id)
    if not profile or not profile.get('region'):
        await update.message.reply_text(f"{user_name}, регион не указан. Обратитесь к администратору.",
                                        reply_markup=context.user_data.get('default_reply_markup'))
        return
    region_folder = f"/regions/{profile['region']}/"
    create_yandex_folder(region_folder)
    files = list_yandex_disk_files(region_folder)
    context.user_data['current_path'] = region_folder
    context.user_data['file_list'] = files
    if files:
        file_keyboard = [[InlineKeyboardButton(item['name'], callback_data=f"download:{idx}")] for idx, item in enumerate(files)]
        reply_markup = InlineKeyboardMarkup(file_keyboard)
        await update.message.reply_text(f"{user_name}, файлы в папке региона {profile['region']}:", reply_markup=reply_markup)
    else:
        await update.message.reply_text(f"{user_name}, папка региона {profile['region']} пуста.",
                                        reply_markup=context.user_data.get('default_reply_markup'))

# Обработка callback-запросов
async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id: int = update.effective_user.id
    user_name = get_user_name(user_id)
    default_reply_markup = context.user_data.get('default_reply_markup', ReplyKeyboardRemove())
    profile = USER_PROFILES.get(user_id)
    if not profile or "region" not in profile:
        await query.message.reply_text(f"{user_name}, ошибка: регион не определён.", reply_markup=default_reply_markup)
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
                await query.message.reply_text(f"{user_name}, ошибка: файл не найден.",
                                               reply_markup=default_reply_markup)
                logger.error(f"Файл с индексом {file_idx} не найден в папке {current_path} для user_id {user_id}")
                return

            file_name = files[file_idx]['name']
            file_path = f"{current_path.rstrip('/')}/{file_name}"
            logger.info(f"Попытка скачать файл {file_path} для user_id {user_id}")

            download_url = get_yandex_disk_file(file_path)
            if not download_url:
                await query.message.reply_text(
                    f"{user_name}, ошибка: не удалось получить ссылку на файл. Проверьте YANDEX_TOKEN.",
                    reply_markup=default_reply_markup)
                logger.error(f"Не удалось получить ссылку для файла {file_path}")
                return

            file_response = requests.get(download_url)
            if file_response.status_code == 200:
                file_size = len(file_response.content) / (1024 * 1024)
                if file_size > 20:
                    await query.message.reply_text(f"{user_name}, файл слишком большой (>20 МБ).",
                                                   reply_markup=default_reply_markup)
                    logger.warning(f"Файл {file_name} слишком большой: {file_size} МБ")
                    return
                await query.message.reply_document(document=InputFile(file_response.content, filename=file_name))
                logger.info(f"Файл {file_name} успешно отправлен пользователю {user_id} из {current_path}")
            else:
                await query.message.reply_text(
                    f"{user_name}, не удалось загрузить файл. Статус: {file_response.status_code}",
                    reply_markup=default_reply_markup)
                logger.error(f"Ошибка загрузки файла {file_path}: статус {file_response.status_code}")
        except Exception as e:
            await query.message.reply_text(f"{user_name}, ошибка при скачивании: {str(e)}. Проверьте YANDEX_TOKEN.",
                                           reply_markup=default_reply_markup)
            logger.error(f"Ошибка при отправке файла: {str(e)}")
    elif query.data.startswith("start_report:"):
        report_id = query.data.split(":", 1)[1]
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT questions, answers, status FROM reports WHERE report_id = %s AND user_id = %s",
                    (report_id, user_id)
                )
                result = cur.fetchone()
                if not result:
                    await query.message.reply_text(f"{user_name}, отчет не найден.",
                                                   reply_markup=default_reply_markup)
                    return
                questions, answers, status = result
                if status == 'completed':
                    await query.message.reply_text(f"{user_name}, этот отчет уже заполнен.",
                                                   reply_markup=default_reply_markup)
                    return
                context.user_data['current_report_id'] = report_id
                context.user_data['current_question_index'] = len(answers) if answers else 0
                context.user_data['current_answers'] = answers if answers else []
                question = questions[context.user_data['current_question_index']]
                await query.message.reply_text(
                    f"{user_name}, вопрос {context.user_data['current_question_index'] + 1}:\n{question}",
                    reply_markup=ReplyKeyboardMarkup([['Отмена']], resize_keyboard=True)
                )
        except Exception as e:
            logger.error(f"Ошибка при начале заполнения отчета {report_id} для {user_id}: {str(e)}")
            await query.message.reply_text(f"{user_name}, ошибка при начале заполнения отчета.",
                                           reply_markup=default_reply_markup)

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

# Функция для отправки длинного текста частями
async def send_long_text(update: Update, text: str, reply_markup=None, max_length=4096):
    for i in range(0, len(text), max_length):
        part = text[i:i + max_length]
        await update.message.reply_text(part, reply_markup=reply_markup if i + max_length >= len(text) else None)

# Обработка текстовых сообщений
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global KNOWLEDGE_BASE, ALLOWED_USERS
    user_id: int = update.effective_user.id
    chat_id: int = update.effective_chat.id
    user_input: str = update.message.text.strip()
    user_name = get_user_name(user_id)
    logger.info(f"Получено сообщение от {chat_id} (user_id: {user_id}): {user_input}")
    log_request(user_id, user_input, "Обработка сообщения...")

    if user_id not in ALLOWED_USERS and user_id not in ALLOWED_ADMINS:
        await update.message.reply_text(f"{user_name}, извините, у вас нет доступа.",
                                        reply_markup=ReplyKeyboardRemove())
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
    if context.user_data.get('awaiting_report_title', False):
        if user_input == "Назад":
            context.user_data.pop('awaiting_report_title', None)
            context.user_data.pop('current_questions', None)
            await show_broadcast_menu(update, context)
            return
        report_title = user_input.strip()
        context.user_data['report_title'] = report_title
        context.user_data.pop('awaiting_report_title', None)
        context.user_data['awaiting_report_questions'] = True
        context.user_data['question_index'] = 1  # Начинаем с вопроса 1
        await update.message.reply_text(
            f"{user_name}, введите вопрос 1 (или 'Готово' для завершения):",
            reply_markup=ReplyKeyboardMarkup([['Готово', 'Назад']], resize_keyboard=True))
        return

    if context.user_data.get('awaiting_report_questions', False):
        if user_input == "Назад":
            context.user_data.pop('awaiting_report_questions', None)
            context.user_data.pop('report_title', None)
            context.user_data.pop('current_questions', None)
            context.user_data.pop('question_index', None)
            await show_broadcast_menu(update, context)
            return
        if user_input.lower() == "готово":
            questions = context.user_data.get('current_questions', [])
            if not questions:
                await update.message.reply_text(f"{user_name}, добавьте хотя бы один вопрос.",
                                                reply_markup=ReplyKeyboardMarkup([['Готово', 'Назад']],
                                                                                 resize_keyboard=True))
                return
            # Формируем сообщение для рассылки
            report_title = context.user_data.get('report_title', 'Отчет')
            broadcast_message = f"{report_title}\n\n" + "\n".join([f"{i + 1}. {q}" for i, q in enumerate(questions)])
            # Рассылка как отчет (используем существующий код)
            report_id = str(uuid.uuid4())
            week_number = datetime.now().isocalendar().week
            year = datetime.now().year
            recipients = ALLOWED_USERS.copy()  # Рассылка пользователям (можно изменить на админов)
            sent_count = 0
            for recipient_id in recipients:
                if recipient_id == user_id:
                    continue
                try:
                    create_report(report_id, recipient_id, questions, week_number, year)
                    reply_markup = InlineKeyboardMarkup([
                        [InlineKeyboardButton("Заполнить отчет", callback_data=f"start_report:{report_id}")]
                    ])
                    await context.bot.send_message(
                        chat_id=recipient_id,
                        text=f"{get_user_name(recipient_id)}, заполните отчет за неделю {week_number} {year}:\n\n{broadcast_message}",
                        reply_markup=reply_markup
                    )
                    sent_count += 1
                except Exception as e:
                    logger.error(f"Ошибка отправки отчета пользователю {recipient_id}: {str(e)}")
            await update.message.reply_text(f"{user_name}, отчет '{report_title}' отправлен {sent_count} получателям.",
                                            reply_markup=default_reply_markup)
            # Очищаем данные
            context.user_data.pop('awaiting_report_questions', None)
            context.user_data.pop('report_title', None)
            context.user_data.pop('current_questions', None)
            context.user_data.pop('question_index', None)
            return
        # Добавляем вопрос в список
        question = user_input.strip()
        context.user_data['current_questions'].append(question)
        context.user_data['question_index'] += 1
        await update.message.reply_text(
            f"{user_name}, введите вопрос {context.user_data['question_index']} (или 'Готово' для завершения):",
            reply_markup=ReplyKeyboardMarkup([['Готово', 'Назад']], resize_keyboard=True))
        return

    if context.user_data.get('awaiting_broadcast', False):
        if user_id not in ALLOWED_ADMINS:
            await update.message.reply_text(f"{user_name}, только администраторы могут делать рассылки.",
                                            reply_markup=default_reply_markup)
            context.user_data.pop('awaiting_broadcast', None)
            context.user_data.pop('broadcast_type', None)
            return
        if user_input == "Назад":
            context.user_data.pop('awaiting_broadcast', None)
            context.user_data.pop('broadcast_type', None)
            await show_broadcast_menu(update, context)
            return
        broadcast_message = user_input.strip()
        broadcast_type = context.user_data.get('broadcast_type')
        if broadcast_type == 'users':
            recipients = ALLOWED_USERS.copy()
        elif broadcast_type == 'admins':
            recipients = ALLOWED_ADMINS.copy()
        else:
            await update.message.reply_text(f"{user_name}, ошибка типа рассылки.",
                                            reply_markup=default_reply_markup)
            context.user_data.pop('awaiting_broadcast', None)
            context.user_data.pop('broadcast_type', None)
            return

        questions = [q.strip() for q in broadcast_message.split('\n') if q.strip()]
        is_report = len(questions) > 1 or any(q.startswith(('1.', '2.', '3.', '4.', '5.', '6.')) for q in questions)
        report_id = str(uuid.uuid4()) if is_report else None
        week_number = datetime.now().isocalendar().week
        year = datetime.now().year

        sent_count = 0
        for recipient_id in recipients:
            if recipient_id == user_id:
                continue
            try:
                if is_report:
                    create_report(report_id, recipient_id, questions, week_number, year)
                    reply_markup = InlineKeyboardMarkup([
                        [InlineKeyboardButton("Заполнить отчет", callback_data=f"start_report:{report_id}")]
                    ])
                    await context.bot.send_message(
                        chat_id=recipient_id,
                        text=f"{get_user_name(recipient_id)}, заполните отчет за неделю {week_number} {year}:\n\n{broadcast_message}",
                        reply_markup=reply_markup
                    )
                else:
                    await context.bot.send_message(chat_id=recipient_id, text=broadcast_message)
                sent_count += 1
            except Exception as e:
                logger.error(f"Ошибка отправки сообщения пользователю {recipient_id}: {str(e)}")

        await update.message.reply_text(f"{user_name}, рассылка отправлена {sent_count} получателям.",
                                        reply_markup=default_reply_markup)
        context.user_data.pop('awaiting_broadcast', None)
        context.user_data.pop('broadcast_type', None)
        return

    if context.user_data.get("awaiting_fact_id", False):
        if user_id not in ALLOWED_ADMINS:
            await update.message.reply_text(f"{user_name}, только администраторы могут удалять факты.",
                                            reply_markup=default_reply_markup)
            context.user_data.pop("awaiting_fact_id", None)
            return
        if user_input == "Назад":
            context.user_data.pop("awaiting_fact_id", None)
            await show_admin_menu(update, context)
            return
        try:
            fact_id = int(user_input)
            if delete_knowledge_fact(fact_id, user_id):
                KNOWLEDGE_BASE = load_knowledge_base()
                await update.message.reply_text(f"{user_name}, факт с ID {fact_id} удалён.",
                                                reply_markup=default_reply_markup)
            else:
                await update.message.reply_text(f"{user_name}, факт с ID {fact_id} не найден.",
                                                reply_markup=default_reply_markup)
            context.user_data.pop("awaiting_fact_id", None)
        except ValueError:
            await update.message.reply_text(f"{user_name}, введите корректный ID факта (число).",
                                            reply_markup=ReplyKeyboardMarkup([['Назад']], resize_keyboard=True))
        return

    if context.user_data.get("awaiting_new_fact", False):
        if user_id not in ALLOWED_ADMINS:
            await update.message.reply_text(f"{user_name}, только администраторы могут добавлять факты.",
                                            reply_markup=default_reply_markup)
            context.user_data.pop("awaiting_new_fact", None)
            return
        if user_input == "Назад":
            context.user_data.pop("awaiting_new_fact", None)
            await show_admin_menu(update, context)
            return
        fact = user_input.strip()
        if not any(f['text'] == fact for f in KNOWLEDGE_BASE):
            save_knowledge_fact(fact, user_id)
            KNOWLEDGE_BASE = load_knowledge_base()
            await update.message.reply_text(f"{user_name}, факт '{fact}' добавлен в базу знаний.",
                                            reply_markup=default_reply_markup)
            logger.info(f"Факт '{fact}' добавлен администратором {user_id} в knowledge_base")
        else:
            await update.message.reply_text(f"{user_name}, факт '{fact}' уже существует в базе знаний.",
                                            reply_markup=default_reply_markup)
        context.user_data.pop("awaiting_new_fact", None)
        return

    if context.user_data.get("awaiting_user_id", False):
        try:
            new_user_id = int(user_input)
            if new_user_id in ALLOWED_USERS:
                await update.message.reply_text(f"{user_name}, пользователь с ID {new_user_id} уже существует.",
                                                reply_markup=ReplyKeyboardMarkup([['Назад']], resize_keyboard=True))
            else:
                ALLOWED_USERS.append(new_user_id)
                save_allowed_users(ALLOWED_USERS)
                await update.message.reply_text(f"{user_name}, пользователь с ID {new_user_id} успешно добавлен.",
                                                reply_markup=ReplyKeyboardMarkup([['Назад']], resize_keyboard=True))
                logger.info(f"Пользователь {new_user_id} добавлен администратором {user_id}")
            context.user_data.pop("awaiting_user_id", None)
            return
        except ValueError:
            await update.message.reply_text(f"{user_name}, пожалуйста, введите корректный user_id (число).",
                                            reply_markup=ReplyKeyboardMarkup([['Назад']], resize_keyboard=True))
            return

    if context.user_data.get("awaiting_admin_id", False):
        try:
            new_admin_id = int(user_input)
            if new_admin_id in ALLOWED_ADMINS:
                await update.message.reply_text(f"{user_name}, администратор с ID {new_admin_id} уже существует.",
                                                reply_markup=ReplyKeyboardMarkup([['Назад']], resize_keyboard=True))
            else:
                ALLOWED_ADMINS.append(new_admin_id)
                save_allowed_admins(ALLOWED_ADMINS)
                await update.message.reply_text(f"{user_name}, администратор с ID {new_admin_id} успешно добавлен.",
                                                reply_markup=ReplyKeyboardMarkup([['Назад']], resize_keyboard=True))
                logger.info(f"Администратор {new_admin_id} добавлен администратором {user_id}")
            context.user_data.pop("awaiting_admin_id", None)
            return
        except ValueError:
            await update.message.reply_text(f"{user_name}, пожалуйста, введите корректный admin_id (число).",
                                            reply_markup=ReplyKeyboardMarkup([['Назад']], resize_keyboard=True))
            return

    if context.user_data.get("awaiting_delete_user_id", False):
        try:
            user_id_to_delete = int(user_input)
            if user_id_to_delete == user_id:
                await update.message.reply_text(f"{user_name}, вы не можете удалить самого себя.",
                                                reply_markup=ReplyKeyboardMarkup([['Назад']], resize_keyboard=True))
            elif user_id_to_delete in ALLOWED_ADMINS:
                await update.message.reply_text(f"{user_name}, вы не можете удалить администратора через эту функцию.",
                                                reply_markup=ReplyKeyboardMarkup([['Назад']], resize_keyboard=True))
            elif delete_allowed_user(user_id_to_delete, user_id):
                ALLOWED_USERS.remove(user_id_to_delete)
                if user_id_to_delete in USER_PROFILES:
                    del USER_PROFILES[user_id_to_delete]
                    save_user_profiles(USER_PROFILES)
                await update.message.reply_text(f"{user_name}, пользователь с ID {user_id_to_delete} успешно удалён.",
                                                reply_markup=ReplyKeyboardMarkup([['Назад']], resize_keyboard=True))
                logger.info(f"Пользователь {user_id_to_delete} удалён администратором {user_id}")
            else:
                await update.message.reply_text(f"{user_name}, пользователь с ID {user_id_to_delete} не найден.",
                                                reply_markup=ReplyKeyboardMarkup([['Назад']], resize_keyboard=True))
            context.user_data.pop("awaiting_delete_user_id", None)
            return
        except ValueError:
            await update.message.reply_text(f"{user_name}, пожалуйста, введите корректный user_id (число).",
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
            await update.message.reply_text("Как я могу к вам обращаться? Укажите краткое имя (например, Кристина).",
                                            reply_markup=ReplyKeyboardRemove())
            return
        await update.message.reply_text("Выберите из предложенных регионов.",
                                        reply_markup=ReplyKeyboardMarkup([[region] for region in regions]))
        return

    if context.user_data.get("awaiting_name", False):
        USER_PROFILES[user_id]["name"] = user_input.strip()
        save_user_profiles(USER_PROFILES)
        context.user_data["awaiting_name"] = False
        user_name = user_input.strip()
        await show_main_menu(update, context)
        await update.message.reply_text(f"{user_name}, рад знакомству! Задавайте вопросы или используйте меню.",
                                        reply_markup=default_reply_markup)
        return

    if context.user_data.get('current_report_id', False):
        if user_input == "Отмена":
            context.user_data.pop('current_report_id', None)
            context.user_data.pop('current_question_index', None)
            context.user_data.pop('current_answers', None)
            await show_main_menu(update, context)
            return
        report_id = context.user_data['current_report_id']
        question_index = context.user_data['current_question_index']
        answers = context.user_data['current_answers']
        answers.append(user_input.strip())
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT questions FROM reports WHERE report_id = %s AND user_id = %s",
                            (report_id, user_id))
                questions = cur.fetchone()[0]
                if question_index + 1 < len(questions):
                    context.user_data['current_question_index'] += 1
                    context.user_data['current_answers'] = answers
                    update_report_answers(report_id, user_id, answers, 'in_progress')
                    next_question = questions[question_index + 1]
                    # Исправляем нумерацию вопроса (было question_index + 2, теперь question_index + 1)
                    await update.message.reply_text(
                        f"{user_name}, вопрос {context.user_data['current_question_index'] + 1}:\n{next_question}",
                        reply_markup=ReplyKeyboardMarkup([['Отмена']], resize_keyboard=True)
                    )
                else:
                    update_report_answers(report_id, user_id, answers, 'completed')
                    context.user_data.pop('current_report_id', None)
                    context.user_data.pop('current_question_index', None)
                    context.user_data.pop('current_answers', None)
                    await update.message.reply_text(
                        f"{user_name}, отчет успешно заполнен!",
                        reply_markup=default_reply_markup
                    )
                    logger.info(f"Отчет {report_id} заполнен пользователем {user_id}")
        except Exception as e:
            logger.error(f"Ошибка при обработке ответа на отчет {report_id}: {str(e)}")
            await update.message.reply_text(
                f"{user_name}, ошибка при сохранении ответа. Попробуйте снова.",
                reply_markup=default_reply_markup
            )
            context.user_data.pop('current_report_id', None)
            context.user_data.pop('current_question_index', None)
            context.user_data.pop('current_answers', None)
        return  # Добавляем return, чтобы предотвратить вызов AI

    if user_input == "Загрузить файл":
        context.user_data["awaiting_upload"] = True
        await update.message.reply_text(
            f"{user_name}, отправьте файл (поддерживаются .pdf, .doc, .docx, .xls, .xlsx, .cdr, .eps, .png, .jpg, .jpeg).",
            reply_markup=ReplyKeyboardMarkup([['Отмена']], resize_keyboard=True))
        return

    elif user_input == "Документы для РО":
        context.user_data['current_mode'] = 'documents_nav'
        context.user_data['current_path'] = '/documents/'
        context.user_data.pop('file_list', None)
        context.user_data.pop('awaiting_upload', None)
        create_yandex_folder('/documents/')
        await show_current_docs(update, context)
        return

    elif user_input == "Архив документов РО":
        context.user_data.pop('current_mode', None)
        context.user_data.pop('current_path', None)
        context.user_data.pop('file_list', None)
        context.user_data.pop('awaiting_upload', None)
        await show_file_list(update, context)
        return

    elif user_input == "Управление пользователями":
        if user_id not in ALLOWED_ADMINS:
            await update.message.reply_text(f"{user_name}, только администраторы могут управлять пользователями.",
                                            reply_markup=default_reply_markup)
            return
        context.user_data.pop('awaiting_upload', None)
        await show_admin_menu(update, context)
        return

    elif user_input == "Рассылка":
        if user_id not in ALLOWED_ADMINS:
            await update.message.reply_text(f"{user_name}, только администраторы могут делать рассылки.",
                                            reply_markup=default_reply_markup)
            return
        context.user_data.pop('awaiting_upload', None)
        await show_broadcast_menu(update, context)
        return

    elif user_input == "Рассылка пользователям":
        if user_id not in ALLOWED_ADMINS:
            await update.message.reply_text(f"{user_name}, только администраторы могут делать рассылки.",
                                            reply_markup=default_reply_markup)
            return
        context.user_data['broadcast_type'] = 'users'
        context.user_data['awaiting_broadcast'] = True
        await update.message.reply_text(
            f"{user_name}, введите текст сообщения для рассылки пользователям. Если это отчет, перечислите вопросы (каждый с новой строки):",
            reply_markup=ReplyKeyboardMarkup([['Назад']], resize_keyboard=True))
        return

    elif user_input == "Рассылка админам":
        if user_id not in ALLOWED_ADMINS:
            await update.message.reply_text(f"{user_name}, только администраторы могут делать рассылки.",
                                            reply_markup=default_reply_markup)
            return
        context.user_data['broadcast_type'] = 'admins'
        context.user_data['awaiting_broadcast'] = True
        await update.message.reply_text(
            f"{user_name}, введите текст сообщения для рассылки администраторам. Если это отчет, перечислите вопросы (каждый с новой строки):",
            reply_markup=ReplyKeyboardMarkup([['Назад']], resize_keyboard=True))
        return

    elif user_input == "Отчеты":
        if user_id not in ALLOWED_ADMINS:
            await update.message.reply_text(f"{user_name}, только администраторы могут создавать отчеты.",
                                            reply_markup=default_reply_markup)
            return

        context.user_data['awaiting_report_title'] = True
        context.user_data['current_questions'] = []  # Список для вопросов
        await update.message.reply_text(
            f"{user_name}, введите название отчета (это будет заголовок, например, 'Прогнозная информация по мероприятиям на этой неделе'):",
            reply_markup=ReplyKeyboardMarkup([['Назад']], resize_keyboard=True))
        return

    elif user_input == "Добавить пользователя":
        if user_id not in ALLOWED_ADMINS:
            await update.message.reply_text(f"{user_name}, только администраторы могут добавлять пользователей.",
                                            reply_markup=default_reply_markup)
            return
        context.user_data["awaiting_user_id"] = True
        context.user_data.pop('awaiting_upload', None)
        await update.message.reply_text(f"{user_name}, введите user_id нового пользователя (число):",
                                        reply_markup=ReplyKeyboardMarkup([['Назад']], resize_keyboard=True))
        return

    elif user_input == "Добавить администратора":
        if user_id not in ALLOWED_ADMINS:
            await update.message.reply_text(f"{user_name}, только администраторы могут добавлять администраторов.",
                                            reply_markup=default_reply_markup)
            return
        context.user_data["awaiting_admin_id"] = True
        context.user_data.pop('awaiting_upload', None)
        await update.message.reply_text(f"{user_name}, введите user_id нового администратора (число):",
                                        reply_markup=ReplyKeyboardMarkup([['Назад']], resize_keyboard=True))
        return

    elif user_input == "Список пользователей":
        if user_id not in ALLOWED_ADMINS:
            await update.message.reply_text(
                f"{user_name}, только администраторы могут просматривать список пользователей.",
                reply_markup=default_reply_markup)
            return
        context.user_data.pop('awaiting_upload', None)
        users_list = "\n".join([f"ID: {uid}" for uid in ALLOWED_USERS]) or "Список пользователей пуст."
        await update.message.reply_text(f"{user_name}, список пользователей:\n{users_list}",
                                        reply_markup=ReplyKeyboardMarkup([['Назад']], resize_keyboard=True))
        return

    elif user_input == "Список администраторов":
        if user_id not in ALLOWED_ADMINS:
            await update.message.reply_text(
                f"{user_name}, только администраторы могут просматривать список администраторов.",
                reply_markup=default_reply_markup)
            return
        context.user_data.pop('awaiting_upload', None)
        admins_list = "\n".join([f"ID: {aid}" for aid in ALLOWED_ADMINS]) or "Список администраторов пуст."
        await update.message.reply_text(f"{user_name}, список администраторов:\n{admins_list}",
                                        reply_markup=ReplyKeyboardMarkup([['Назад']], resize_keyboard=True))
        return

    elif user_input == "Удалить пользователя":
        if user_id not in ALLOWED_ADMINS:
            await update.message.reply_text(f"{user_name}, только администраторы могут удалять пользователей.",
                                            reply_markup=default_reply_markup)
            return
        context.user_data["awaiting_delete_user_id"] = True
        context.user_data.pop('awaiting_upload', None)
        users_list = "\n".join([f"ID: {uid}" for uid in ALLOWED_USERS]) or "Список пользователей пуст."
        await update.message.reply_text(
            f"{user_name}, выберите ID пользователя для удаления:\n{users_list}\n\nВведите ID:",
            reply_markup=ReplyKeyboardMarkup([['Назад']], resize_keyboard=True))
        return

    elif user_input == "Все факты (с ID)":
        if user_id not in ALLOWED_ADMINS:
            await update.message.reply_text(f"{user_name}, только администраторы могут просматривать факты.",
                                            reply_markup=default_reply_markup)
            return
        context.user_data.pop('awaiting_upload', None)
        if not KNOWLEDGE_BASE:
            await update.message.reply_text(f"{user_name}, база знаний пуста.", reply_markup=ReplyKeyboardMarkup([['Назад']], resize_keyboard=True))
            return
        facts_list = f"{user_name}, все факты:\n" + "\n".join([f"ID: {fact['id']} — {fact['text']}" for fact in KNOWLEDGE_BASE])
        await send_long_text(update, facts_list, reply_markup=ReplyKeyboardMarkup([['Назад']], resize_keyboard=True))
        logger.info(f"Администратор {user_id} запросил список фактов. Показаны факты.")
        return

    elif user_input == "Добавить факт":
        if user_id not in ALLOWED_ADMINS:
            await update.message.reply_text(f"{user_name}, только администраторы могут добавлять факты.",
                                            reply_markup=default_reply_markup)
            return
        context.user_data["awaiting_new_fact"] = True
        context.user_data.pop('awaiting_upload', None)
        await update.message.reply_text(f"{user_name}, введите текст нового факта:", reply_markup=ReplyKeyboardMarkup([['Назад']], resize_keyboard=True))
        return

    elif user_input == "Удалить факт":
        if user_id not in ALLOWED_ADMINS:
            await update.message.reply_text(f"{user_name}, только администраторы могут удалять факты.",
                                            reply_markup=default_reply_markup)
            return
        context.user_data.pop('awaiting_upload', None)
        if not KNOWLEDGE_BASE:
            await update.message.reply_text(f"{user_name}, база знаний пуста.", reply_markup=ReplyKeyboardMarkup([['Назад']], resize_keyboard=True))
            return
        facts_list = f"{user_name}, выберите ID факта для удаления:\n" + "\n".join([f"ID: {fact['id']} — {fact['text']}" for fact in KNOWLEDGE_BASE]) + "\n\nВведите ID:"
        await send_long_text(update, facts_list, reply_markup=ReplyKeyboardMarkup([['Назад']], resize_keyboard=True))
        context.user_data["awaiting_fact_id"] = True
        logger.info(f"Администратор {user_id} запросил удаление факта. Показаны факты.")
        return

    elif user_input == "Просмотреть отчеты":
        if user_id not in ALLOWED_ADMINS:
            await update.message.reply_text(f"{user_name}, только администраторы могут просматривать отчеты.",
                                            reply_markup=default_reply_markup)
            return
        context.user_data["awaiting_report_week"] = True
        context.user_data.pop('awaiting_upload', None)
        await update.message.reply_text(
            f"{user_name}, введите номер недели и год (например, '42 2025') для просмотра отчетов:",
            reply_markup=ReplyKeyboardMarkup([['Назад']], resize_keyboard=True))
        return

    elif context.user_data.get("awaiting_report_week", False):
        if user_input == "Назад":
            context.user_data.pop("awaiting_report_week", None)
            await show_admin_menu(update, context)
            return
        try:
            week_number, year = map(int, user_input.split())
            reports = get_reports_by_week(week_number, year)
            if not reports:
                await update.message.reply_text(
                    f"{user_name}, отчеты за неделю {week_number} {year} не найдены.",
                    reply_markup=default_reply_markup
                )
            else:
                report_text = f"{user_name}, отчеты за неделю {week_number} {year}:\n\n"
                for report in reports:
                    user_profile = USER_PROFILES.get(report['user_id'], {})
                    user_name_report = user_profile.get('name', f"ID {report['user_id']}")
                    region = user_profile.get('region', 'Не указан')
                    report_text += f"Пользователь: {user_name_report} (Регион: {region}, Статус: {report['status']})\n"
                    for idx, (question, answer) in enumerate(zip(report['questions'], report['answers'] or []), 1):
                        report_text += f"{idx}. {question}\nОтвет: {answer or 'Не заполнено'}\n"
                    report_text += "\n"
                await send_long_text(update, report_text, reply_markup=default_reply_markup)
            context.user_data.pop("awaiting_report_week", None)
        except ValueError:
            await update.message.reply_text(
                f"{user_name}, введите корректный номер недели и год (например, '42 2025').",
                reply_markup=ReplyKeyboardMarkup([['Назад']], resize_keyboard=True))
        return
    elif user_input == "Выгрузить отчеты в Excel":
        if user_id not in ALLOWED_ADMINS:
            await update.message.reply_text(f"{user_name}, только администраторы могут выгружать отчеты.",
                                            reply_markup=default_reply_markup)
            return
        context.user_data["awaiting_export_week"] = True
        context.user_data.pop('awaiting_upload', None)
        await update.message.reply_text(
            f"{user_name}, введите номер недели и год (например, '42 2025') для выгрузки отчетов в Excel:",
            reply_markup=ReplyKeyboardMarkup([['Назад']], resize_keyboard=True))
        return

    elif context.user_data.get("awaiting_export_week", False):
        if user_input == "Назад":
            context.user_data.pop("awaiting_export_week", None)
            await show_admin_menu(update, context)
            return
        try:
            week_number, year = map(int, user_input.split())
            reports = get_reports_by_week(week_number, year)
            if not reports:
                await update.message.reply_text(
                    f"{user_name}, отчеты за неделю {week_number} {year} не найдены.",
                    reply_markup=default_reply_markup
                )
                context.user_data.pop("awaiting_export_week", None)
                return
            # Подготовка данных для Excel
            data = []
            for report in reports:
                user_profile = USER_PROFILES.get(report['user_id'], {})
                user_name_report = user_profile.get('name', f"ID {report['user_id']}")
                region = user_profile.get('region', 'Не указан')
                row = {
                    'User ID': report['user_id'],
                    'Имя': user_name_report,
                    'Регион': region,
                    'Статус': report['status'],
                    'Создано': report['created_at'].strftime('%Y-%m-%d %H:%M:%S') if report['created_at'] else '',
                }
                for idx, (question, answer) in enumerate(zip(report['questions'], report['answers'] or []), 1):
                    row[f'Вопрос {idx}'] = question
                    row[f'Ответ {idx}'] = answer or 'Не заполнено'
                data.append(row)
            # Создание DataFrame и Excel
            df = pd.DataFrame(data)
            output = BytesIO()
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                df.to_excel(writer, index=False, sheet_name=f'Отчеты_неделя_{week_number}_{year}')
            output.seek(0)
            # Отправка файла
            file_name = f'reports_week_{week_number}_{year}.xlsx'
            await update.message.reply_document(
                document=InputFile(output, filename=file_name),
                caption=f"{user_name}, отчеты за неделю {week_number} {year} выгружены в Excel."
            )
            logger.info(f"Отчеты за неделю {week_number} {year} выгружены в Excel для админа {user_id}")
            context.user_data.pop("awaiting_export_week", None)
            await show_admin_menu(update, context)
        except ValueError:
            await update.message.reply_text(
                f"{user_name}, введите корректный номер недели и год (например, '42 2025').",
                reply_markup=ReplyKeyboardMarkup([['Назад']], resize_keyboard=True))
        return

    elif user_input == "Назад":
        context.user_data.pop('awaiting_upload', None)
        context.user_data.pop('awaiting_fact_id', None)
        context.user_data.pop('awaiting_delete_user_id', None)
        context.user_data.pop('awaiting_new_fact', None)
        context.user_data.pop('awaiting_broadcast', None)
        context.user_data.pop('broadcast_type', None)
        if context.user_data.get('current_mode') == 'documents_nav':
            current_path = context.user_data.get('current_path', '/documents/')
            if current_path == '/documents/':
                context.user_data.pop('current_mode', None)
                context.user_data.pop('current_path', None)
                context.user_data.pop('file_list', None)
                await show_main_menu(update, context)
            else:
                parent_path = '/'.join(current_path.rstrip('/').split('/')[:-1]) + '/'
                context.user_data['current_path'] = parent_path
                await show_current_docs(update, context, is_return=True)
        else:
            await show_admin_menu(update, context) if 'broadcast_type' in context.user_data else await show_main_menu(update, context)
        return

    elif user_input == "Отмена":
        context.user_data.pop('awaiting_upload', None)
        context.user_data.pop('current_report_id', None)
        context.user_data.pop('current_question_index', None)
        context.user_data.pop('current_answers', None)
        await show_main_menu(update, context)
        return

    elif context.user_data.get('current_mode') == 'documents_nav':
        current_path = context.user_data.get('current_path', '/documents/')
        if user_input == "В главное меню":
            context.user_data.pop('current_mode', None)
            context.user_data.pop('current_path', None)
            context.user_data.pop('file_list', None)
            await show_main_menu(update, context)
            return
        elif user_input == "Назад":
            if current_path == '/documents/':
                context.user_data.pop('current_mode', None)
                context.user_data.pop('current_path', None)
                context.user_data.pop('file_list', None)
                await show_main_menu(update, context)
            else:
                parent_path = '/'.join(current_path.rstrip('/').split('/')[:-1]) + '/'
                context.user_data['current_path'] = parent_path
                await show_current_docs(update, context, is_return=True)
            return
        else:
            new_path = f"{current_path.rstrip('/')}/{user_input}/"
            if create_yandex_folder(new_path):
                context.user_data['current_path'] = new_path
                await show_current_docs(update, context)
            else:
                await update.message.reply_text(
                    f"{user_name}, ошибка при переходе в папку {user_input}.",
                    reply_markup=default_reply_markup
                )
            return

    else:
        response = await generate_ai_response(user_id, user_input, user_name, chat_id)
        log_request(user_id, user_input, response)
        await send_long_text(update, response, reply_markup=default_reply_markup)

# Обработка загруженных документов
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id: int = update.effective_user.id
    user_name = get_user_name(user_id)
    default_reply_markup = context.user_data.get('default_reply_markup', ReplyKeyboardRemove())

    if not context.user_data.get('awaiting_upload', False):
        await update.message.reply_text(
            f"{user_name}, сначала выберите 'Загрузить файл' в меню.",
            reply_markup=default_reply_markup
        )
        return

    if user_id not in USER_PROFILES or not USER_PROFILES[user_id].get('region'):
        await update.message.reply_text(
            f"{user_name}, регион не указан. Обратитесь к администратору.",
            reply_markup=default_reply_markup
        )
        context.user_data.pop('awaiting_upload', None)
        return

    document = update.message.document
    if not document:
        await update.message.reply_text(
            f"{user_name}, пожалуйста, отправьте файл.",
            reply_markup=ReplyKeyboardMarkup([['Отмена']], resize_keyboard=True)
        )
        return

    file_name = document.file_name
    supported_extensions = ('.pdf', '.doc', '.docx', '.xls', '.xlsx', '.cdr', '.eps', '.png', '.jpg', '.jpeg')
    if not file_name.lower().endswith(supported_extensions):
        await update.message.reply_text(
            f"{user_name}, поддерживаются только файлы: .pdf, .doc, .docx, .xls, .xlsx, .cdr, .eps, .png, .jpg, .jpeg.",
            reply_markup=ReplyKeyboardMarkup([['Отмена']], resize_keyboard=True)
        )
        return

    try:
        file = await document.get_file()
        file_content = await file.download_as_bytearray()
        region = USER_PROFILES[user_id]['region']
        folder_path = f"/regions/{region}/"
        create_yandex_folder(folder_path)
        if upload_to_yandex_disk(file_content, file_name, folder_path):
            await update.message.reply_text(
                f"{user_name}, файл {file_name} успешно загружен в папку региона {region}.",
                reply_markup=default_reply_markup
            )
            logger.info(f"Файл {file_name} загружен пользователем {user_id} в {folder_path}")
        else:
            await update.message.reply_text(
                f"{user_name}, ошибка при загрузке файла. Проверьте YANDEX_TOKEN.",
                reply_markup=default_reply_markup
            )
            logger.error(f"Ошибка при загрузке файла {file_name} пользователем {user_id}")
        context.user_data.pop('awaiting_upload', None)
    except Exception as e:
        logger.error(f"Ошибка при обработке документа от {user_id}: {str(e)}")
        await update.message.reply_text(
            f"{user_name}, ошибка при загрузке файла: {str(e)}.",
            reply_markup=default_reply_markup
        )
        context.user_data.pop('awaiting_upload', None)

# Функция для проверки и отправки напоминаний о просроченных отчетах
async def check_reminders(context: ContextTypes.DEFAULT_TYPE) -> None:
    overdue_reports = check_overdue_reports()
    for report in overdue_reports:
        user_id = report['user_id']
        report_id = report['report_id']
        questions = report['questions']
        user_name = get_user_name(user_id)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE reports SET reminder_sent_at = NOW() WHERE report_id = %s AND user_id = %s",
                    (report_id, user_id)
                )
                conn.commit()
            reply_markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("Заполнить отчет", callback_data=f"start_report:{report_id}")]
            ])
            await context.bot.send_message(
                chat_id=user_id,
                text=f"{user_name}, напоминание: вы не заполнили отчет за неделю {datetime.now().isocalendar().week} {datetime.now().year}:\n\n" + "\n".join(questions),
                reply_markup=reply_markup
            )
            logger.info(f"Напоминание отправлено пользователю {user_id} для отчета {report_id}")
        except Exception as e:
            logger.error(f"Ошибка при отправке напоминания пользователю {user_id} для отчета {report_id}: {str(e)}")

# Основная функция запуска бота
def main() -> None:
    try:
        application = Application.builder().token(TELEGRAM_TOKEN).build()
        application.add_handler(CommandHandler("start", send_welcome))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        application.add_handler(MessageHandler(filters.Document.ALL, handle_document))
        application.add_handler(CallbackQueryHandler(handle_callback_query))
        application.job_queue.run_repeating(check_reminders, interval=21600, first=60)  # Каждые 6 часов
        logger.info("Бот запущен, начинаю polling...")
        application.run_polling(allowed_updates=Update.ALL_TYPES)
    except Exception as e:
        logger.error(f"Ошибка при запуске бота: {str(e)}")
        raise

if __name__ == '__main__':
    main()