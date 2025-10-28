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

# Проверка токенов и DATABASE_URL111
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

            # ----- после создания allowed_admins -----
            cur.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_name = 'allowed_delta_admins'
                );
            """)
            if not cur.fetchone()[0]:
                cur.execute("""
                    CREATE TABLE allowed_delta_admins (
                        id BIGINT NOT NULL PRIMARY KEY
                    );
                """)
                logger.info("Таблица allowed_delta_admins создана.")
            else:
                logger.info("Таблица allowed_delta_admins уже существует.")

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

            # === БЕЗОПАСНОЕ ОБНОВЛЕНИЕ ТАБЛИЦЫ reports ===
            try:
                with conn.cursor() as cur:
                    # Добавляем report_title
                    cur.execute("""
                        ALTER TABLE reports 
                        ADD COLUMN IF NOT EXISTS report_title TEXT;
                    """)

                    # Добавляем reminder_interval_minutes
                    cur.execute("""
                        ALTER TABLE reports 
                        ADD COLUMN IF NOT EXISTS reminder_interval_minutes INTEGER DEFAULT 60;
                    """)

                    # Добавляем is_reminder_active
                    cur.execute("""
                        ALTER TABLE reports 
                        ADD COLUMN IF NOT EXISTS is_reminder_active BOOLEAN DEFAULT TRUE;
                    """)

                    # Для старых отчетов — заполняем title (если пусто)
                    cur.execute("""
                        UPDATE reports 
                        SET report_title = 'Отчет за неделю ' || week_number || ' ' || year
                        WHERE report_title IS NULL;
                    """)

                cur.execute("""
                    ALTER TABLE reports 
                    ADD COLUMN IF NOT EXISTS id SERIAL PRIMARY KEY;
                """)

                conn.commit()
                logger.info(
                    "Таблица reports обновлена: добавлены report_title, reminder_interval_minutes, is_reminder_active")
            except Exception as e:
                logger.error(f"Ошибка при обновлении reports: {str(e)}")
                conn.rollback()


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


# === Часовые пояса регионов ===
import pytz
from datetime import time

REGION_TIMEZONES = {
    # Дальний Восток
    "Камчатский край": "Asia/Kamchatka",
    "Чукотский автономный округ": "Asia/Anadyr",
    "Магаданская область": "Asia/Magadan",
    "Сахалинская область": "Asia/Sakhalin",
    "Приморский край": "Asia/Vladivostok",
    "Хабаровский край": "Asia/Vladivostok",
    "Амурская область": "Asia/Yakutsk",
    "Республика Саха (Якутия)": "Asia/Yakutsk",
    "Еврейская автономная область": "Asia/Vladivostok",
    "Забайкальский край": "Asia/Chita",

    # Сибирь
    "Республика Бурятия": "Asia/Irkutsk",
    "Иркутская область": "Asia/Irkutsk",
    "Красноярский край": "Asia/Krasnoyarsk",
    "Республика Хакасия": "Asia/Krasnoyarsk",
    "Республика Тыва": "Asia/Krasnoyarsk",
    "Алтайский край": "Asia/Barnaul",
    "Республика Алтай": "Asia/Barnaul",
    "Кемеровская область": "Asia/Novokuznetsk",
    "Новосибирская область": "Asia/Novosibirsk",
    "Омская область": "Asia/Omsk",
    "Томская область": "Asia/Tomsk",

    # Урал
    "Свердловская область": "Asia/Yekaterinburg",
    "Челябинская область": "Asia/Yekaterinburg",
    "Курганская область": "Asia/Yekaterinburg",
    "Тюменская область": "Asia/Yekaterinburg",
    "Ханты-Мансийский автономный округ — Югра": "Asia/Yekaterinburg",
    "Ямало-Ненецкий автономный округ": "Asia/Yekaterinburg",

    # Поволжье
    "Республика Татарстан": "Europe/Moscow",
    "Республика Башкортостан": "Asia/Yekaterinburg",
    "Самарская область": "Europe/Samara",
    "Саратовская область": "Europe/Saratov",
    "Ульяновская область": "Europe/Moscow",
    "Пензенская область": "Europe/Moscow",
    "Оренбургская область": "Asia/Yekaterinburg",
    "Пермский край": "Asia/Yekaterinburg",
    "Кировская область": "Europe/Moscow",
    "Нижегородская область": "Europe/Moscow",
    "Республика Марий Эл": "Europe/Moscow",
    "Республика Мордовия": "Europe/Moscow",
    "Чувашская Республика": "Europe/Moscow",
    "Удмуртская Республика": "Europe/Samara",

    # Центр
    "Москва": "Europe/Moscow",
    "Московская область": "Europe/Moscow",
    "Санкт-Петербург": "Europe/Moscow",
    "Ленинградская область": "Europe/Moscow",
    "Белгородская область": "Europe/Moscow",
    "Брянская область": "Europe/Moscow",
    "Владимирская область": "Europe/Moscow",
    "Воронежская область": "Europe/Moscow",
    "Ивановская область": "Europe/Moscow",
    "Калужская область": "Europe/Moscow",
    "Костромская область": "Europe/Moscow",
    "Курская область": "Europe/Moscow",
    "Липецкая область": "Europe/Moscow",
    "Орловская область": "Europe/Moscow",
    "Рязанская область": "Europe/Moscow",
    "Смоленская область": "Europe/Moscow",
    "Тамбовская область": "Europe/Moscow",
    "Тверская область": "Europe/Moscow",
    "Тульская область": "Europe/Moscow",
    "Ярославская область": "Europe/Moscow",

    # Северо-Запад
    "Республика Карелия": "Europe/Moscow",
    "Республика Коми": "Europe/Moscow",
    "Архангельская область": "Europe/Moscow",
    "Вологодская область": "Europe/Moscow",
    "Мурманская область": "Europe/Moscow",
    "Новгородская область": "Europe/Moscow",
    "Псковская область": "Europe/Moscow",
    "Калининградская область": "Europe/Kaliningrad",
    "Ненецкий автономный округ": "Europe/Moscow",

    # Юг
    "Республика Адыгея": "Europe/Moscow",
    "Республика Калмыкия": "Europe/Moscow",
    "Краснодарский край": "Europe/Moscow",
    "Астраханская область": "Europe/Volgograd",
    "Волгоградская область": "Europe/Volgograd",
    "Ростовская область": "Europe/Moscow",
    "Республика Крым": "Europe/Simferopol",
    "Севастополь": "Europe/Simferopol",

    # Северный Кавказ
    "Республика Дагестан": "Europe/Moscow",
    "Республика Ингушетия": "Europe/Moscow",
    "Кабардино-Балкарская Республика": "Europe/Moscow",
    "Карачаево-Черкесская Республика": "Europe/Moscow",
    "Республика Северная Осетия — Алания": "Europe/Moscow",
    "Чеченская Республика": "Europe/Moscow",
    "Ставропольский край": "Europe/Moscow",

    # Новые территории
    "Донецкая Народная Республика": "Europe/Moscow",
    "Луганская Народная Республика": "Europe/Moscow",
    "Запорожская область": "Europe/Moscow",
    "Херсонская область": "Europe/Moscow",

    # По умолчанию
    None: "Europe/Moscow"
}

def get_user_timezone(region: str) -> str:
    return REGION_TIMEZONES.get(region, "Europe/Moscow")

def is_working_hours(region: str) -> bool:
    if not region:
        return False
    tz = pytz.timezone(get_user_timezone(region))
    now = datetime.now(tz)
    return time(9, 0) <= now.time() <= time(18, 0) and now.weekday() < 5  # пн-пт


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

# === НОВЫЕ ФУНКЦИИ ===
def load_allowed_delta_admins() -> List[int]:
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM allowed_delta_admins")
            delta = [row[0] for row in cur.fetchall()]
            logger.info(f"Загружено {len(delta)} delta-админов")
            return delta
    except Exception as e:
        logger.error(f"Ошибка при загрузке allowed_delta_admins: {str(e)}")
        conn.rollback()
        return []

def is_admin_or_delta(user_id: int) -> bool:
    return user_id in ALLOWED_ADMINS or user_id in ALLOWED_DELTA_ADMINS

def save_allowed_delta_admins(delta_admins: List[int]) -> None:
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM allowed_delta_admins")
            for d_id in delta_admins:
                cur.execute("INSERT INTO allowed_delta_admins (id) VALUES (%s)", (d_id,))
            conn.commit()
            logger.info(f"Сохранено {len(delta_admins)} delta-админов")
    except Exception as e:
        logger.error(f"Ошибка при сохранении allowed_delta_admins: {str(e)}")
        conn.rollback()

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


def delete_allowed_admin(admin_id_to_delete: int, requesting_admin_id: int) -> bool:
    """
    Удаляет администратора из таблицы allowed_admins.

    Args:
        admin_id_to_delete (int): ID администратора, которого нужно удалить.
        requesting_admin_id (int): ID администратора, выполняющего удаление.

    Returns:
        bool: True, если удаление успешно, False в противном случае.
    """
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM allowed_admins WHERE id = %s", (admin_id_to_delete,))
            if cur.rowcount > 0:
                conn.commit()
                logger.info(f"Администратор с ID {admin_id_to_delete} удален администратором {requesting_admin_id}")
                return True
            else:
                logger.warning(
                    f"Администратор с ID {admin_id_to_delete} не найден для удаления администратором {requesting_admin_id}")
                return False
    except Exception as e:
        logger.error(f"Ошибка при удалении администратора с ID {admin_id_to_delete}: {str(e)}")
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
def create_report(report_id: str, user_id: int, questions: List[str], week_number: int, year: int, title: str, interval_minutes: int) -> None:
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO reports 
                (report_id, user_id, week_number, year, questions, answers, status, created_at, 
                 report_title, reminder_interval_minutes, is_reminder_active)
                VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), %s, %s, TRUE)
                """,
                (report_id, user_id, week_number, year, questions, [], 'pending', title, interval_minutes)
            )
            conn.commit()
            logger.info(f"Отчет {report_id} создан: {title}, интервал {interval_minutes} мин")
    except Exception as e:
        logger.error(f"Ошибка при создании отчета {report_id}: {str(e)}")
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


def export_users_to_excel() -> BytesIO:
    """
    Выгружает данные зарегистрированных пользователей в Excel-файл.

    Returns:
        BytesIO: Поток данных Excel-файла.
    """
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id, fio, region FROM user_profiles")
            users = [{"ID": row[0], "ФИО": row[1] or "Не указано", "Регион": row[2] or "Не указано"} for row in
                     cur.fetchall()]
        df = pd.DataFrame(users)
        output = BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Пользователи')
        output.seek(0)
        logger.info("Данные пользователей успешно выгружены в Excel")
        return output
    except Exception as e:
        logger.error(f"Ошибка при выгрузке пользователей в Excel: {str(e)}")
        raise


def export_admins_to_excel() -> BytesIO:
    """
    Выгружает данные администраторов в Excel-файл.

    Returns:
        BytesIO: Поток данных Excel-файла.
    """
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM allowed_admins")
            admin_ids = [row[0] for row in cur.fetchall()]
            admins = []
            for admin_id in admin_ids:
                cur.execute("SELECT fio, region FROM user_profiles WHERE user_id = %s", (admin_id,))
                profile = cur.fetchone()
                admins.append({
                    "ID": admin_id,
                    "ФИО": profile[0] if profile and profile[0] else "Не указано",
                    "Регион": profile[1] if profile and profile[1] else "Не указано"
                })
        df = pd.DataFrame(admins)
        output = BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Администраторы')
        output.seek(0)
        logger.info("Данные администраторов успешно выгружены в Excel")
        return output
    except Exception as e:
        logger.error(f"Ошибка при выгрузке администраторов в Excel: {str(e)}")
        raise

def export_broadcasts_to_excel(week_number: int, year: int) -> BytesIO:
    """
    Выгружает данные о рассылках (отчетах) за указанную неделю и год в Excel-файл.

    Args:
        week_number (int): Номер недели.
        year (int): Год.

    Returns:
        BytesIO: Поток данных Excel-файла.
    """
    try:
        reports = get_reports_by_week(week_number, year)
        data = []
        for report in reports:
            user_profile = USER_PROFILES.get(report['user_id'], {})
            user_name_report = user_profile.get('name', f"ID {report['user_id']}")
            region = user_profile.get('region', 'Не указан')
            row = {
                'Report ID': report['report_id'],
                'User ID': report['user_id'],
                'Имя': user_name_report,
                'Регион': region,
                'Статус': report['status'],
                'Создано': report['created_at'].strftime('%Y-%m-%d %H:%M:%S') if report['created_at'] else ''
            }
            for idx, question in enumerate(report['questions'], 1):
                row[f'Вопрос {idx}'] = question
                row[f'Ответ {idx}'] = report['answers'][idx-1] if idx-1 < len(report['answers']) else 'Не заполнено'
            data.append(row)
        df = pd.DataFrame(data)
        output = BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name=f'Рассылки_неделя_{week_number}_{year}')
        output.seek(0)
        logger.info(f"Данные рассылок за неделю {week_number} {year} выгружены в Excel")
        return output
    except Exception as e:
        logger.error(f"Ошибка при выгрузке рассылок в Excel: {str(e)}")
        raise


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
ALLOWED_DELTA_ADMINS = load_allowed_delta_admins()
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


async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id: int = update.effective_user.id
    user_name = get_user_name(user_id)

    # ---------- ОСНОВНОЕ МЕНЮ ----------
    base_buttons = [
        ['Документы для РО', 'Архив документов РО'],
        ['Загрузить файл']
    ]

    if user_id in ALLOWED_ADMINS:
        # Полный админ
        keyboard = [
            ['Управление пользователями', 'Управление фактами'],
            ['Отчеты', 'Рассылки'],
            ['Файлы из папок']
        ]
    elif user_id in ALLOWED_DELTA_ADMINS:
        # Дельта-админ
        keyboard = base_buttons + [
            ['Отчеты', 'Рассылки'],
            ['Файлы из папок']
        ]
    else:
        # Обычный пользователь
        keyboard = base_buttons

    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    context.user_data['default_reply_markup'] = reply_markup
    context.user_data.pop('current_mode', None)
    context.user_data.pop('current_path', None)
    context.user_data.pop('file_list', None)
    context.user_data.pop('awaiting_user_id', None)
    context.user_data.pop('awaiting_admin_id', None)
    context.user_data.pop('awaiting_delete_admin_id', None)
    context.user_data.pop('awaiting_upload', None)
    context.user_data.pop('awaiting_fact_id', None)
    context.user_data.pop('awaiting_delete_user_id', None)
    context.user_data.pop('awaiting_new_fact', None)
    context.user_data.pop('awaiting_broadcast', None)
    context.user_data.pop('broadcast_type', None)
    context.user_data.pop('awaiting_report_week', None)
    context.user_data.pop('awaiting_export_week', None)
    context.user_data.pop('awaiting_broadcast_export_week', None)
    context.user_data.pop('awaiting_report_title', None)
    context.user_data.pop('awaiting_report_questions', None)
    context.user_data.pop('current_questions', None)
    context.user_data.pop('question_index', None)
    await update.message.reply_text(f"{user_name}, выберите действие:", reply_markup=reply_markup)


async def show_admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id: int = update.effective_user.id
    user_name = get_user_name(user_id)
    if user_id not in ALLOWED_ADMINS:
        await update.message.reply_text(f"{user_name}, только администраторы могут управлять пользователями.",
                                       reply_markup=context.user_data.get('default_reply_markup'))
        return
    keyboard = [
        ['Добавить пользователя', 'Список пользователей'],
        ['Удалить пользователя', 'Выгрузить пользователей в Excel'],
        ['Добавить администратора', 'Список администраторов'],
        ['Удалить администратора', 'Выгрузить администраторов в Excel'],
        ['Добавить delta-админа', 'Список delta-админов'],
        ['Удалить delta-админа'],
        ['Назад']
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text(f"{user_name}, выберите действие:", reply_markup=reply_markup)

async def show_facts_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id: int = update.effective_user.id
    user_name = get_user_name(user_id)
    if user_id not in ALLOWED_ADMINS:
        await update.message.reply_text(f"{user_name}, только администраторы могут управлять фактами.",
                                       reply_markup=context.user_data.get('default_reply_markup'))
        return
    keyboard = [
        ['Добавить факт', 'Все факты'],
        ['Удалить факт', 'Назад']
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text(f"{user_name}, выберите действие:", reply_markup=reply_markup)

async def show_reports_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id: int = update.effective_user.id
    user_name = get_user_name(user_id)
    if user_id not in ALLOWED_ADMINS:
        await update.message.reply_text(f"{user_name}, только администраторы могут управлять отчетами.",
                                       reply_markup=context.user_data.get('default_reply_markup'))
        return
    keyboard = [
        ['Создать отчет', 'Просмотреть отчеты'],
        ['Выгрузить отчеты в Excel', 'Остановить напоминания'],
        ['Назад']
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text(f"{user_name}, выберите действие:", reply_markup=reply_markup)



async def show_broadcast_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id: int = update.effective_user.id
    user_name = get_user_name(user_id)
    if user_id not in ALLOWED_ADMINS:
        await update.message.reply_text(f"{user_name}, только администраторы могут делать рассылки.",
                                       reply_markup=context.user_data.get('default_reply_markup'))
        return
    keyboard = [
        ['Рассылка пользователям', 'Рассылка администраторам'],
        ['Выгрузка рассылок в Excel', 'Назад']
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text(f"{user_name}, выберите тип рассылки:", reply_markup=reply_markup)


async def show_files_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id: int = update.effective_user.id
    user_name = get_user_name(user_id)
    keyboard = [
        ['Документы для РО', 'Архив документов РО'],
        ['Загрузить файл', 'Назад']
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text(f"{user_name}, выберите действие:", reply_markup=reply_markup)

# Отображение содержимого папки в /documents/
async def show_current_docs(update: Update, context: ContextTypes.DEFAULT_TYPE, is_return: bool = False) -> None:
    user_id: int = update.effective_user.id
    user_name = get_user_name(user_id)

    current_path = context.user_data.get('current_path', '/documents/')
    folder_name = current_path.rstrip('/').split('/')[-1]
    if current_path == '/documents/':
        folder_name = "Документы для РО"

    files = list_yandex_disk_files(current_path)
    dirs = list_yandex_disk_directories(current_path)

    # === Клавиатура папок ===
    keyboard = [[dir_name] for dir_name in dirs]
    if current_path != '/documents/':
        keyboard.append(['Назад'])
    keyboard.append(['В главное меню'])
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    # === ВСЕГДА СОХРАНЯЕМ ПУТЬ И ФАЙЛЫ ===
    context.user_data['current_path'] = current_path
    context.user_data['file_list'] = files

    if files:
        file_keyboard = [
            [InlineKeyboardButton(item['name'], callback_data=f"doc_download:{idx}")]
            for idx, item in enumerate(files)
        ]
        file_reply_markup = InlineKeyboardMarkup(file_keyboard)
        await update.message.reply_text(
            f"*{folder_name}*:",
            reply_markup=file_reply_markup,
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text(
            f"*{folder_name}*:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )

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
    await query.answer()                                   # <-- подтверждение callback-запроса

    user_id: int = update.effective_user.id
    user_name = get_user_name(user_id)
    default_reply_markup = context.user_data.get('default_reply_markup', ReplyKeyboardRemove())
    profile = USER_PROFILES.get(user_id)

    if not profile or "region" not in profile:
        await query.message.reply_text(
            f"{user_name}, ошибка: регион не определён.",
            reply_markup=default_reply_markup
        )
        return

    try:
        # ------------------- Скачивание файла из /documents/ -------------------
        if query.data.startswith("doc_download:"):
            file_idx = int(query.data.split(":", 1)[1])
            current_path = context.user_data.get('current_path', '/documents/')
            files = context.user_data.get('file_list', []) or list_yandex_disk_files(current_path)

            if file_idx >= len(files):
                await query.message.reply_text(
                    f"{user_name}, файл не найден.", reply_markup=default_reply_markup
                )
                return

            file_name = files[file_idx]['name']
            file_path = f"{current_path.rstrip('/')}/{file_name}"
            download_url = get_yandex_disk_file(file_path)

            if not download_url:
                await query.message.reply_text(
                    f"{user_name}, не удалось получить файл.", reply_markup=default_reply_markup
                )
                return

            file_response = requests.get(download_url)
            if file_response.status_code == 200:
                file_size_mb = len(file_response.content) / (1024 * 1024)
                if file_size_mb > 20:
                    await query.message.reply_text(
                        f"{user_name}, файл слишком большой (>20 МБ).",
                        reply_markup=default_reply_markup
                    )
                    return

                await query.message.reply_document(
                    document=InputFile(file_response.content, filename=file_name)
                )

                keyboard = []
                if context.user_data.get('current_path', '/documents/') != '/documents/':
                    keyboard.append(['Назад'])
                keyboard.append(['В главное меню'])
                reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

                try:
                    await query.message.reply_text("\u200B", reply_markup=reply_markup)
                except Exception as e:
                    logger.warning(f"Ошибка при показе клавиатуры после файла: {e}")
            else:
                await query.message.reply_text(
                    f"{user_name}, ошибка при скачивании.", reply_markup=default_reply_markup
                )
            return

        # ------------------- Админ: скачивание из любого региона -------------------
        elif query.data.startswith("admin_download:"):
            file_idx = int(query.data.split(":", 1)[1])
            files = context.user_data.get('admin_region_files', [])
            region = context.user_data.get('selected_region')

            if file_idx >= len(files):
                await query.message.reply_text(f"{user_name}, файл не найден.")
                return

            file_name = files[file_idx]['name']
            file_path = f"/regions/{region}/{file_name}"
            download_url = get_yandex_disk_file(file_path)

            if not download_url:
                await query.message.reply_text(f"{user_name}, не удалось получить файл.")
                return

            file_response = requests.get(download_url)
            if file_response.status_code == 200:
                await query.message.reply_document(
                    document=InputFile(file_response.content, filename=file_name)
                )
                keyboard = [['Назад в архив']]
                await query.message.reply_text(
                    "\u200B",
                    reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
                )
            else:
                await query.message.reply_text(f"{user_name}, ошибка при скачивании.")
            return

        # ------------------- Скачивание из личного архива пользователя -------------------
        elif query.data.startswith("download:"):
            file_idx = int(query.data.split(":", 1)[1])
            current_path = f"/regions/{profile['region']}/"
            files = context.user_data.get('file_list', []) or list_yandex_disk_files(current_path)
            context.user_data['file_list'] = files
            context.user_data['current_path'] = current_path

            if file_idx >= len(files):
                await query.message.reply_text(
                    f"{user_name}, ошибка: файл не найден.", reply_markup=default_reply_markup
                )
                return

            file_name = files[file_idx]['name']
            file_path = f"{current_path.rstrip('/')}/{file_name}"
            download_url = get_yandex_disk_file(file_path)

            if not download_url:
                await query.message.reply_text(
                    f"{user_name}, ошибка: не удалось получить файл.", reply_markup=default_reply_markup
                )
                return

            file_response = requests.get(download_url)
            if file_response.status_code == 200:
                file_size_mb = len(file_response.content) / (1024 * 1024)
                if file_size_mb > 20:
                    await query.message.reply_text(
                        f"{user_name}, файл слишком большой (>20 МБ).", reply_markup=default_reply_markup
                    )
                    return

                await query.message.reply_document(
                    document=InputFile(file_response.content, filename=file_name)
                )

                keyboard = [['Назад'], ['В главное меню']]
                reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

                try:
                    await query.message.reply_text("\u200B", reply_markup=reply_markup)
                except Exception as e:
                    logger.warning(f"Ошибка при показе клавиатуры после файла: {e}")
            else:
                await query.message.reply_text(
                    f"{user_name}, ошибка при скачивании.", reply_markup=default_reply_markup
                )
            return

        # ------------------- Запуск отчёта -------------------
        elif query.data.startswith("start_report:"):
            report_id = query.data.split(":", 1)[1]

            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT questions, answers, status
                    FROM reports
                    WHERE report_id = %s AND user_id = %s
                    """,
                    (report_id, user_id)
                )
                result = cur.fetchone()

            if not result:
                await query.message.reply_text(
                    f"{user_name}, отчет не найден.", reply_markup=default_reply_markup
                )
                return

            questions, answers, status = result

            if status == 'completed':
                await query.message.reply_text(
                    f"{user_name}, этот отчет уже заполнен.", reply_markup=default_reply_markup
                )
                return

            context.user_data['current_report_id'] = report_id
            context.user_data['current_question_index'] = len(answers) if answers else 0
            context.user_data['current_answers'] = answers if answers else []

            question = questions[context.user_data['current_question_index']]
            await query.message.reply_text(
                f"{user_name}, вопрос {context.user_data['current_question_index'] + 1}:\n{question}",
                reply_markup=ReplyKeyboardMarkup([['Отмена']], resize_keyboard=True)
            )
            return

        # ------------------- Остановка напоминаний (s) -------------------
        elif query.data.startswith("s"):
            pk = int(query.data[1:])
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE reports SET is_reminder_active = FALSE WHERE id = %s",
                    (pk,)
                )
                if cur.rowcount > 0:
                    conn.commit()
                    await query.message.edit_text(f"{user_name}, напоминания остановлены.")
                    await query.message.reply_text(
                        "Вернуться в меню?",
                        reply_markup=ReplyKeyboardMarkup([['Отчеты', 'Назад']], resize_keyboard=True)
                    )
                else:
                    await query.message.edit_text(f"{user_name}, отчет не найден.")
            return

        # ------------------- Просмотр отчёта (v) -------------------
        elif query.data.startswith("v"):
            pk = int(query.data[1:])
            await view_report_by_pk(query, pk, user_name)
            return

        # ------------------- Выгрузка отчёта в Excel (e) -------------------
        elif query.data.startswith("e"):
            pk = int(query.data[1:])
            await export_report_by_pk(query, pk, user_name)
            return

        # ------------------- Просмотр/выгрузка по названию отчёта -------------------
        elif query.data.startswith("view_by_title:") or query.data.startswith("export_by_title:"):
            action = "view" if query.data.startswith("view_by_title:") else "export"
            safe_title = query.data.split(":", 1)[1]

            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT report_id, user_id, questions, answers, status, created_at, report_title
                        FROM reports
                        WHERE report_title LIKE %s
                        ORDER BY created_at
                        """,
                        (safe_title + '%',)
                    )
                    reports = cur.fetchall()
            except Exception as e:
                logger.error(f"Ошибка при получении отчетов: {str(e)}")
                await query.message.reply_text(f"{user_name}, ошибка при получении отчетов: {str(e)}.")
                return

            if not reports:
                await query.message.reply_text(f"{user_name}, отчеты с названием '{safe_title}' не найдены.")
                return

            if action == "view":
                text = f"*{reports[0][6]}*\n\n"
                for r in reports:
                    user_name_report = get_user_name(r[1])
                    text += f"• {user_name_report} — {r[4]}\n"
                await send_long_text(query, text, parse_mode='Markdown')
            else:  # export
                output = export_reports_by_title(reports, reports[0][6])
                safe_file_name = reports[0][6][:20].replace(' ', '_').replace(':', '') + '.xlsx'
                await query.message.reply_document(
                    InputFile(output, safe_file_name),
                    caption=f"{user_name}, отчет: {reports[0][6]}"
                )
            return

    except Exception as e:
        logger.error(f"Ошибка в handle_callback_query: {str(e)}")
        await query.message.reply_text(
            f"{user_name}, ошибка: {str(e)}.", reply_markup=default_reply_markup
        )

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



async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global KNOWLEDGE_BASE, ALLOWED_USERS, ALLOWED_ADMINS
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

    # Устанавливаем клавиатуру по умолчанию в зависимости от роли пользователя
    if user_id in ALLOWED_ADMINS:
        keyboard = [
            ['Управление пользователями', 'Управление фактами'],
            ['Отчеты', 'Рассылки'],
            ['Файлы из папок']
        ]
    else:
        keyboard = [
            ['Документы для РО', 'Архив документов РО'],
            ['Загрузить файл']
        ]
    default_reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    context.user_data['default_reply_markup'] = default_reply_markup

    if context.user_data.get('awaiting_report_title', False):
        if user_input == "Назад":
            context.user_data.pop('awaiting_report_title', None)
            await show_reports_menu(update, context)
            return
        report_title = user_input.strip()
        context.user_data['report_title'] = report_title
        context.user_data.pop('awaiting_report_title', None)
        context.user_data['awaiting_reminder_interval'] = True
        await update.message.reply_text(
            f"{user_name}, через сколько минут напоминать?\n(5–360, по умолчанию 60)",
            reply_markup=ReplyKeyboardMarkup([['60', '30', '120', '360', 'Назад']], resize_keyboard=True)
        )
        return

    elif context.user_data.get('awaiting_reminder_interval', False):
        if user_input == "Назад":
            context.user_data.pop('awaiting_reminder_interval', None)
            context.user_data['awaiting_report_title'] = True
            await update.message.reply_text("Введите название отчета:")
            return
        try:
            interval = int(user_input)
            if not 5 <= interval <= 360:
                raise ValueError
        except:
            await update.message.reply_text("Введите число от 5 до 360.")
            return
        context.user_data['reminder_interval'] = interval
        context.user_data.pop('awaiting_reminder_interval', None)
        context.user_data['awaiting_report_questions'] = True
        context.user_data['current_questions'] = []
        context.user_data['question_index'] = 1
        await update.message.reply_text(
            f"{user_name}, введите вопрос 1 (или 'Готово'):",
            reply_markup=ReplyKeyboardMarkup([['Готово', 'Назад']], resize_keyboard=True)
        )
        return

    if context.user_data.get('awaiting_report_questions', False):
        if user_input == "Назад":
            context.user_data.pop('awaiting_report_questions', None)
            context.user_data.pop('report_title', None)
            context.user_data.pop('current_questions', None)
            context.user_data.pop('question_index', None)
            await show_reports_menu(update, context)
            return
        if user_input.lower() == "готово":
            questions = context.user_data.get('current_questions', [])
            if not questions:
                await update.message.reply_text(f"{user_name}, добавьте хотя бы один вопрос.",
                                               reply_markup=ReplyKeyboardMarkup([['Готово', 'Назад']],
                                                                                resize_keyboard=True))
                return
            report_title = context.user_data.get('report_title', 'Отчет')
            broadcast_message = f"{report_title}\n\n" + "\n".join([f"{i + 1}. {q}" for i, q in enumerate(questions)])
            report_id = str(uuid.uuid4())
            week_number = datetime.now().isocalendar().week
            year = datetime.now().year
            recipients = ALLOWED_USERS.copy()
            sent_count = 0
            for recipient_id in recipients:
                if recipient_id == user_id:
                    continue
                try:
                    interval = context.user_data.get('reminder_interval', 60)
                    report_title = context.user_data.get('report_title', 'Отчет')
                    create_report(report_id, recipient_id, questions, week_number, year, report_title, interval)

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
            context.user_data.pop('awaiting_report_questions', None)
            context.user_data.pop('report_title', None)
            context.user_data.pop('current_questions', None)
            context.user_data.pop('question_index', None)
            return
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
        sent_count = 0
        for recipient_id in recipients:
            if recipient_id == user_id:
                continue
            try:
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
            await show_facts_menu(update, context)
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
            await show_facts_menu(update, context)
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
        if user_id not in ALLOWED_ADMINS:
            await update.message.reply_text(f"{user_name}, только администраторы могут добавлять пользователей.",
                                           reply_markup=default_reply_markup)
            context.user_data.pop("awaiting_user_id", None)
            return
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
        except ValueError:
            await update.message.reply_text(f"{user_name}, пожалуйста, введите корректный user_id (число).",
                                           reply_markup=ReplyKeyboardMarkup([['Назад']], resize_keyboard=True))
        return

    if context.user_data.get("awaiting_admin_id", False):
        if user_id not in ALLOWED_ADMINS:
            await update.message.reply_text(f"{user_name}, только администраторы могут добавлять администраторов.",
                                           reply_markup=default_reply_markup)
            context.user_data.pop("awaiting_admin_id", None)
            return
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
        except ValueError:
            await update.message.reply_text(f"{user_name}, пожалуйста, введите корректный admin_id (число).",
                                           reply_markup=ReplyKeyboardMarkup([['Назад']], resize_keyboard=True))
        return

    if context.user_data.get("awaiting_delete_user_id", False):
        if user_id not in ALLOWED_ADMINS:
            await update.message.reply_text(f"{user_name}, только администраторы могут удалять пользователей.",
                                           reply_markup=default_reply_markup)
            context.user_data.pop("awaiting_delete_user_id", None)
            return
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
        except ValueError:
            await update.message.reply_text(f"{user_name}, пожалуйста, введите корректный user_id (число).",
                                           reply_markup=ReplyKeyboardMarkup([['Назад']], resize_keyboard=True))
        return

    if context.user_data.get("awaiting_delete_admin_id", False):
        if user_id not in ALLOWED_ADMINS:
            await update.message.reply_text(f"{user_name}, только администраторы могут удалять администраторов.",
                                           reply_markup=default_reply_markup)
            context.user_data.pop("awaiting_delete_admin_id", None)
            return
        try:
            admin_id_to_delete = int(user_input)
            if admin_id_to_delete == user_id:
                await update.message.reply_text(f"{user_name}, вы не можете удалить самого себя.",
                                               reply_markup=ReplyKeyboardMarkup([['Назад']], resize_keyboard=True))
            elif delete_allowed_admin(admin_id_to_delete, user_id):
                ALLOWED_ADMINS.remove(admin_id_to_delete)
                save_allowed_admins(ALLOWED_ADMINS)
                await update.message.reply_text(f"{user_name}, администратор с ID {admin_id_to_delete} успешно удалён.",
                                               reply_markup=ReplyKeyboardMarkup([['Назад']], resize_keyboard=True))
                logger.info(f"Администратор {admin_id_to_delete} удалён администратором {user_id}")
            else:
                await update.message.reply_text(f"{user_name}, администратор с ID {admin_id_to_delete} не найден.",
                                               reply_markup=ReplyKeyboardMarkup([['Назад']], resize_keyboard=True))
            context.user_data.pop("awaiting_delete_admin_id", None)
        except ValueError:
            await update.message.reply_text(f"{user_name}, пожалуйста, введите корректный admin_id (число).",
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
        return

    if user_input == "Управление пользователями":
        if user_id not in ALLOWED_ADMINS:
            await update.message.reply_text(f"{user_name}, только администраторы могут управлять пользователями.",
                                           reply_markup=default_reply_markup)
            return
        await show_admin_menu(update, context)
        return

    elif user_input == "Управление фактами":
        if user_id not in ALLOWED_ADMINS:
            await update.message.reply_text(f"{user_name}, только администраторы могут управлять фактами.",
                                           reply_markup=default_reply_markup)
            return
        await show_facts_menu(update, context)
        return

    elif user_input == "Отчеты":
        if not is_admin_or_delta(user_id):
            await update.message.reply_text(f"{user_name}, только администраторы могут управлять отчетами.",
                                            reply_markup=default_reply_markup)
            return
        await show_reports_menu(update, context)
        return

    elif user_input == "Рассылки":
        if not is_admin_or_delta(user_id):
            await update.message.reply_text(f"{user_name}, только администраторы могут делать рассылки.",
                                            reply_markup=default_reply_markup)
            return
        await show_broadcast_menu(update, context)
        return

    elif user_input == "Рассылка пользователям":
        if not is_admin_or_delta(user_id):
            await update.message.reply_text(f"{user_name}, только администраторы могут делать рассылки.",
                                           reply_markup=default_reply_markup)
            return
        context.user_data['awaiting_broadcast'] = True
        context.user_data['broadcast_type'] = 'users'
        context.user_data.pop('awaiting_upload', None)
        await update.message.reply_text(
            f"{user_name}, введите текст сообщения для рассылки пользователям:",
            reply_markup=ReplyKeyboardMarkup([['Назад']], resize_keyboard=True))
        return

    elif user_input == "Рассылка администраторам":
        if not is_admin_or_delta(user_id):
            await update.message.reply_text(f"{user_name}, только администраторы могут делать рассылки.",
                                           reply_markup=default_reply_markup)
            return
        context.user_data['awaiting_broadcast'] = True
        context.user_data['broadcast_type'] = 'admins'
        context.user_data.pop('awaiting_upload', None)
        await update.message.reply_text(
            f"{user_name}, введите текст сообщения для рассылки администраторам:",
            reply_markup=ReplyKeyboardMarkup([['Назад']], resize_keyboard=True))
        return

    elif user_input == "Выгрузка рассылок в Excel":
        if not is_admin_or_delta(user_id):
            await update.message.reply_text(f"{user_name}, только администраторы могут выгружать данные рассылок.",
                                           reply_markup=default_reply_markup)
            return
        context.user_data["awaiting_broadcast_export_week"] = True
        context.user_data.pop('awaiting_upload', None)
        await update.message.reply_text(
            f"{user_name}, введите номер недели и год (например, '42 2025') для выгрузки рассылок в Excel:",
            reply_markup=ReplyKeyboardMarkup([['Назад']], resize_keyboard=True))
        return

    elif user_input == "Файлы из папок":
        if not is_admin_or_delta(user_id):
            await update.message.reply_text(f"{user_name}, только администраторы могут управлять файлами.", reply_markup=default_reply_markup)
            return

        keyboard = [
            ['Документы для РО'],
            ['Архив документов РО'],
            ['Назад']
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        await update.message.reply_text(f"{user_name}, выберите раздел:", reply_markup=reply_markup)
        return


    elif user_input == "Загрузить файл":
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
        if not is_admin_or_delta(user_id):
            await show_file_list(update, context)
            return

        keyboard = [
            ['Скачать файл'],
            ['Удалить файл'],
            ['Назад']
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        await update.message.reply_text(f"{user_name}, выберите действие:", reply_markup=reply_markup)
        return

    elif user_input == "Скачать файл":
        if not is_admin_or_delta(user_id):
            await update.message.reply_text(f"{user_name}, доступ запрещён.", reply_markup=default_reply_markup)
            return
        await show_regions_list(update, context, action="download")
        return

    elif user_input == "Удалить файл":
        if not is_admin_or_delta(user_id):
            await update.message.reply_text(f"{user_name}, доступ запрещён.", reply_markup=default_reply_markup)
            return
        await show_regions_list(update, context, action="delete")
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

    elif user_input == "Удалить администратора":
        if user_id not in ALLOWED_ADMINS:
            await update.message.reply_text(f"{user_name}, только администраторы могут удалять администраторов.",
                                           reply_markup=default_reply_markup)
            return
        context.user_data["awaiting_delete_admin_id"] = True
        context.user_data.pop('awaiting_upload', None)
        admins_list = "\n".join([f"ID: {aid}" for aid in ALLOWED_ADMINS]) or "Список администраторов пуст."
        await update.message.reply_text(
            f"{user_name}, выберите ID администратора для удаления:\n{admins_list}\n\nВведите ID:",
            reply_markup=ReplyKeyboardMarkup([['Назад']], resize_keyboard=True))
        return

    elif user_input == "Выгрузить пользователей в Excel":
        if user_id not in ALLOWED_ADMINS:
            await update.message.reply_text(f"{user_name}, только администраторы могут выгружать данные пользователей.",
                                           reply_markup=default_reply_markup)
            return
        try:
            output = export_users_to_excel()
            file_name = f"users_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
            await update.message.reply_document(
                document=InputFile(output, filename=file_name),
                caption=f"{user_name}, данные пользователей выгружены в Excel."
            )
            logger.info(f"Данные пользователей выгружены в Excel для админа {user_id}")
            await show_admin_menu(update, context)
        except Exception as e:
            logger.error(f"Ошибка при выгрузке пользователей для админа {user_id}: {str(e)}")
            await update.message.reply_text(
                f"{user_name}, ошибка при выгрузке данных пользователей: {str(e)}.",
                reply_markup=default_reply_markup
            )
        return

    elif user_input == "Выгрузить администраторов в Excel":
        if user_id not in ALLOWED_ADMINS:
            await update.message.reply_text(
                f"{user_name}, только администраторы могут выгружать данные администраторов.",
                reply_markup=default_reply_markup)
            return
        try:
            output = export_admins_to_excel()
            file_name = f"admins_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
            await update.message.reply_document(
                document=InputFile(output, filename=file_name),
                caption=f"{user_name}, данные администраторов выгружены в Excel."
            )
            logger.info(f"Данные администраторов выгружены в Excel для админа {user_id}")
            await show_admin_menu(update, context)
        except Exception as e:
            logger.error(f"Ошибка при выгрузке администраторов для админа {user_id}: {str(e)}")
            await update.message.reply_text(
                f"{user_name}, ошибка при выгрузке данных администраторов: {str(e)}.",
                reply_markup=default_reply_markup
            )
        return

    if context.user_data.get("awaiting_delta_admin_id", False):
        try:
            new_id = int(user_input)
            if new_id in ALLOWED_DELTA_ADMINS:
                await update.message.reply_text(f"{user_name}, delta-админ {new_id} уже существует.")
            else:
                ALLOWED_DELTA_ADMINS.append(new_id)
                save_allowed_delta_admins(ALLOWED_DELTA_ADMINS)
                await update.message.reply_text(f"{user_name}, delta-админ {new_id} добавлен.")
            context.user_data.pop("awaiting_delta_admin_id", None)
        except ValueError:
            await update.message.reply_text("Введите корректный ID.")
        return

    if context.user_data.get("awaiting_delete_delta_id", False):
        try:
            del_id = int(user_input)
            if del_id not in ALLOWED_DELTA_ADMINS:
                await update.message.reply_text(f"{user_name}, delta-админ {del_id} не найден.")
            else:
                ALLOWED_DELTA_ADMINS.remove(del_id)
                save_allowed_delta_admins(ALLOWED_DELTA_ADMINS)
                await update.message.reply_text(f"{user_name}, delta-админ {del_id} удалён.")
            context.user_data.pop("awaiting_delete_delta_id", None)
        except ValueError:
            await update.message.reply_text("Введите корректный ID.")
        return

    elif user_input == "Добавить delta-админа":
        if user_id not in ALLOWED_ADMINS:
            await update.message.reply_text(
                f"{user_name}, только администраторы могут добавлять delta-админов.",
                reply_markup=default_reply_markup
            )
            return
        context.user_data["awaiting_delta_admin_id"] = True
        await update.message.reply_text(
            f"{user_name}, введите user_id нового delta-админа:",
            reply_markup=ReplyKeyboardMarkup([['Назад']], resize_keyboard=True)
        )
        return

    elif user_input == "Список delta-админов":
        if user_id not in ALLOWED_ADMINS:
            await update.message.reply_text(
                f"{user_name}, только администраторы могут просматривать список delta-админов.",
                reply_markup=default_reply_markup
            )
            return
        delta_list = "\n".join([f"ID: {d}" for d in ALLOWED_DELTA_ADMINS]) or "Список пуст."
        await update.message.reply_text(
            f"{user_name}, delta-админы:\n{delta_list}",
            reply_markup=ReplyKeyboardMarkup([['Назад']], resize_keyboard=True)
        )
        return

    elif user_input == "Удалить delta-админа":
        if user_id not in ALLOWED_ADMINS:
            await update.message.reply_text(
                f"{user_name}, только администраторы могут удалять delta-админов.",
                reply_markup=default_reply_markup
            )
            return
        context.user_data["awaiting_delete_delta_id"] = True
        delta_list = "\n".join([f"ID: {d}" for d in ALLOWED_DELTA_ADMINS]) or "Список пуст."
        await update.message.reply_text(
            f"{user_name}, выберите ID delta-админа для удаления:\n{delta_list}\n\nВведите ID:",
            reply_markup=ReplyKeyboardMarkup([['Назад']], resize_keyboard=True)
        )
        return

    # === ОЖИДАНИЕ ВВОДА ID ДЛЯ ДОБАВЛЕНИЯ ===
    if context.user_data.get("awaiting_delta_admin_id"):
        try:
            new_id = int(user_input)
            if new_id == user_id:
                await update.message.reply_text(f"{user_name}, нельзя добавить самого себя.")
            elif new_id in ALLOWED_DELTA_ADMINS:
                await update.message.reply_text(f"{user_name}, delta-админ {new_id} уже существует.")
            elif new_id in ALLOWED_ADMINS:
                await update.message.reply_text(f"{user_name}, {new_id} — главный админ, нельзя.")
            else:
                ALLOWED_DELTA_ADMINS.append(new_id)
                save_allowed_delta_admins(ALLOWED_DELTA_ADMINS)
                await update.message.reply_text(f"{user_name}, delta-админ {new_id} добавлен.")
            context.user_data.pop("awaiting_delta_admin_id", None)
        except ValueError:
            await update.message.reply_text(
                f"{user_name}, введите корректный ID (число).",
                reply_markup=ReplyKeyboardMarkup([['Назад']], resize_keyboard=True)
            )
        return

    # === ОЖИДАНИЕ ВВОДА ID ДЛЯ УДАЛЕНИЯ ===
    if context.user_data.get("awaiting_delete_delta_id"):
        try:
            del_id = int(user_input)
            if del_id not in ALLOWED_DELTA_ADMINS:
                await update.message.reply_text(f"{user_name}, delta-админ {del_id} не найден.")
            elif del_id == user_id:
                await update.message.reply_text(f"{user_name}, нельзя удалить самого себя.")
            else:
                ALLOWED_DELTA_ADMINS.remove(del_id)
                save_allowed_delta_admins(ALLOWED_DELTA_ADMINS)
                await update.message.reply_text(f"{user_name}, delta-админ {del_id} удалён.")
            context.user_data.pop("awaiting_delete_delta_id", None)
        except ValueError:
            await update.message.reply_text(
                f"{user_name}, введите корректный ID (число).",
                reply_markup=ReplyKeyboardMarkup([['Назад']], resize_keyboard=True)
            )
        return

    # ← ВСТАВЬ СЮДА (перед строкой else:)
    elif user_input == "Назад":
        # Сбрасываем ожидание ID delta-админа
        context.user_data.pop("awaiting_delta_admin_id", None)
        context.user_data.pop("awaiting_delete_delta_id", None)

        # === НАВИГАЦИЯ ПО ДОКУМЕНТАМ (остаётся как было) ===
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
            await show_main_menu(update, context)
        return

    else:
        response = await generate_ai_response(user_id, user_input, user_name, chat_id)

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
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT report_id, user_id, questions, reminder_sent_at, reminder_interval_minutes, report_title
                FROM reports 
                WHERE status != 'completed' 
                  AND is_reminder_active = TRUE
                  AND (reminder_sent_at IS NULL OR reminder_sent_at < NOW() - INTERVAL '1 minute' * reminder_interval_minutes)
            """)
            reports = cur.fetchall()
    except Exception as e:
        logger.error(f"Ошибка проверки напоминаний: {e}")
        return

    for row in reports:
        report_id, user_id, questions, last_sent, interval_min, title = row
        profile = USER_PROFILES.get(user_id, {})
        region = profile.get("region")

        if not region or not is_working_hours(region):
            continue

        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE reports SET reminder_sent_at = NOW() WHERE report_id = %s",
                    (report_id,)
                )
                conn.commit()

            reply_markup = InlineKeyboardMarkup([[
                InlineKeyboardButton("Заполнить отчет", callback_data=f"start_report:{report_id}")
            ]])
            user_name = get_user_name(user_id)
            await context.bot.send_message(
                chat_id=user_id,
                text=f"{user_name}, заполните отчет:\n\n*{title}*\n\n" + "\n".join([f"{i+1}. {q}" for i, q in enumerate(questions)]),
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
        except Exception as e:
            logger.error(f"Ошибка отправки напоминания {report_id}: {e}")

# === НОВЫЕ ФУНКЦИИ ДЛЯ АДМИНА: УПРАВЛЕНИЕ АРХИВОМ РЕГИОНОВ ===

async def show_regions_list(update: Update, context: ContextTypes.DEFAULT_TYPE, action: str) -> None:
    """Показывает список регионов для скачивания/удаления файлов"""
    user_id = update.effective_user.id
    user_name = get_user_name(user_id)

    # Получаем все уникальные регионы из профилей
    regions = sorted({profile['region'] for profile in USER_PROFILES.values() if profile.get('region')})
    if not regions:
        await update.message.reply_text(f"{user_name}, нет зарегистрированных регионов.")
        return

    keyboard = [[region] for region in regions]
    keyboard.append(['Назад'])
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    context.user_data['admin_archive_action'] = action  # 'download' или 'delete'
    context.user_data['awaiting_region_selection'] = True

    action_text = "скачать" if action == "download" else "удалить"
    await update.message.reply_text(
        f"{user_name}, выберите регион, из которого хотите {action_text} файл:",
        reply_markup=reply_markup
    )


async def show_region_files(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает файлы в выбранном регионе"""
    user_id = update.effective_user.id
    user_name = get_user_name(user_id)
    region = context.user_data.get('selected_region')
    action = context.user_data.get('admin_archive_action')

    if not region:
        await update.message.reply_text(f"{user_name}, регион не выбран.")
        return

    folder_path = f"/regions/{region}/"
    create_yandex_folder(folder_path)
    files = list_yandex_disk_files(folder_path)

    if not files:
        await update.message.reply_text(f"{user_name}, в папке региона *{region}* нет файлов.", parse_mode='Markdown')
        context.user_data.pop('awaiting_region_selection', None)
        context.user_data.pop('selected_region', None)
        context.user_data.pop('admin_archive_action', None)
        return

    context.user_data['admin_region_files'] = files
    context.user_data['current_path'] = folder_path

    if action == "download":
        file_keyboard = [
            [InlineKeyboardButton(item['name'], callback_data=f"admin_download:{idx}")]
            for idx, item in enumerate(files)
        ]
        reply_markup = InlineKeyboardMarkup(file_keyboard)
        await update.message.reply_text(
            f"*{region}* — выберите файл для скачивания:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    else:  # delete
        keyboard = [[item['name']] for item in files]
        keyboard.append(['Назад'])
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        await update.message.reply_text(
            f"*{region}* — выберите файл для удаления:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )


async def delete_yandex_file(file_path: str) -> bool:
    """Удаляет файл с Яндекс.Диска"""
    encoded_path = quote(file_path, safe='/')
    url = f'https://cloud-api.yandex.net/v1/disk/resources?path={encoded_path}'
    headers = {'Authorization': f'OAuth {YANDEX_TOKEN}'}
    try:
        response = requests.delete(url, headers=headers)
        if response.status_code == 204:
            logger.info(f"Файл удалён: {file_path}")
            return True
        else:
            logger.error(f"Ошибка удаления файла {file_path}: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        logger.error(f"Исключение при удалении {file_path}: {str(e)}")
        return False

# === НОВЫЕ ФУНКЦИИ ===

async def stop_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    user_name = get_user_name(user_id)
    if user_id not in ALLOWED_ADMINS:
        await update.message.reply_text(f"{user_name}, только администраторы могут останавливать напоминания.")
        return

    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, report_title, week_number, year
                FROM reports
                WHERE status != 'completed' AND is_reminder_active = TRUE
                ORDER BY created_at DESC LIMIT 20
            """)
            active = cur.fetchall()
    except Exception as e:
        logger.error(f"Ошибка: {str(e)}")
        await update.message.reply_text(f"{user_name}, ошибка: {str(e)}.")
        return

    if not active:
        await update.message.reply_text(f"{user_name}, нет активных напоминаний.")
        return

    keyboard = []
    for pk, title, week, year in active:
        short_title = title[:30] + "..." if len(title) > 30 else title
        button_text = f"{short_title} (н{week} {year})"
        callback = f"s{pk}"  # ← s1, s2, s3
        keyboard.append([InlineKeyboardButton(button_text, callback_data=callback)])

    await update.message.reply_text(
        f"{user_name}, выберите отчет для остановки:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def list_reports_by_title(update: Update, context: ContextTypes.DEFAULT_TYPE, action: str):
    user_id = update.effective_user.id
    user_name = get_user_name(user_id)

    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT r.id, r.report_title, r.created_at, r.user_id
                FROM reports r
                WHERE r.report_title IS NOT NULL AND r.report_title != ''
                ORDER BY r.created_at DESC
            """)
            reports = cur.fetchall()
    except Exception as e:
        await update.message.reply_text(f"{user_name}, ошибка: {str(e)}.")
        return

    if not reports:
        await update.message.reply_text(f"{user_name}, нет отчетов.")
        return

    keyboard = []
    for pk, title, created_at, report_user_id in reports:
        # Получаем имя и регион пользователя, который создал отчёт
        user_profile = USER_PROFILES.get(report_user_id, {})
        report_user_name = user_profile.get('name', f"ID {report_user_id}")
        region = user_profile.get('region', '—')

        # Формируем подпись кнопки
        date_str = created_at.strftime("%d.%m.%Y %H:%M") if created_at else ""
        short_title = title[:25] + "..." if len(title) > 25 else title
        button_text = f"{short_title} | {report_user_name} | {region} ({date_str})"

        prefix = "v" if action == "view" else "e"
        callback = f"{prefix}{pk}"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=callback)])

    text = "просмотра" if action == "view" else "выгрузки"
    await update.message.reply_text(
        f"{user_name}, выберите отчет для {text}:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

def export_reports_by_title(reports, title):
    data = []
    for r in reports:
        user_profile = USER_PROFILES.get(r[1], {})
        row = {
            'ID': r[0],
            'Имя': user_profile.get('name', f"ID {r[1]}"),
            'Регион': user_profile.get('region', '—'),
            'Статус': r[4],
            'Создано': r[5].strftime('%Y-%m-%d %H:%M') if r[5] else ''
        }
        for i, q in enumerate(r[2], 1):
            row[f'В{i}'] = q
            row[f'О{i}'] = r[3][i-1] if i-1 < len(r[3]) else '—'
        data.append(row)
    df = pd.DataFrame(data)
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name=title[:31])  # Ограничиваем длину имени листа
    output.seek(0)
    return output


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

# === НОВАЯ ФУНКЦИЯ 1: ПРОСМОТР ОТЧЕТА ===
async def view_report_by_pk(query, pk, user_name):
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT report_title, user_id, status, questions, answers
                FROM reports WHERE id = %s
            """, (pk,))
            row = cur.fetchone()
            if not row:
                await query.message.reply_text("Отчет не найден.")
                return
            title, user_id, status, questions, answers = row

            user_profile = USER_PROFILES.get(user_id, {})
            report_user_name = user_profile.get('name', f"ID {user_id}")
            region = user_profile.get('region', '—')

            text = f"*{title}*\n"
            text += f"Пользователь: {report_user_name} | Регион: {region}\n"
            text += f"Статус: {status}\n\n"

            for i, (q, a) in enumerate(zip(questions, answers or []), 1):
                text += f"{i}. {q}\n"
                text += f"Ответ: {a or '—'}\n\n"

            # Отправляем частями, с parse_mode
            max_length = 4000
            for i in range(0, len(text), max_length):
                part = text[i:i + max_length]
                if i + max_length >= len(text):
                    await query.message.reply_text(part, parse_mode='Markdown')
                else:
                    await query.message.reply_text(part, parse_mode='Markdown')
    except Exception as e:
        await query.message.reply_text(f"Ошибка: {str(e)}")

# === НОВАЯ ФУНКЦИЯ 2: ВЫГРУЗКА В EXCEL ===
async def export_report_by_pk(query, pk, user_name):
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT report_title, user_id, questions, answers, status, created_at
                FROM reports WHERE id = %s
            """, (pk,))
            row = cur.fetchone()
            if not row:
                await query.message.reply_text("Отчет не найден.")
                return
            output = export_reports_by_title([row], row[0])
            safe_name = row[0][:20].replace(' ', '_') + '.xlsx'
            await query.message.reply_document(
                InputFile(output, safe_name),
                caption=f"{user_name}, отчет выгружен."
            )
    except Exception as e:
        await query.message.reply_text(f"Ошибка: {str(e)}")

if __name__ == '__main__':
    main()
