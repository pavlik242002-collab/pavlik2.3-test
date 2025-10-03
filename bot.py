from __future__ import annotations

import os
import logging
import requests
import json
from typing import Dict, List, Any
from dotenv import load_dotenv
from telegram import ReplyKeyboardMarkup, ReplyKeyboardRemove, InlineKeyboardMarkup, InlineKeyboardButton, Update
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from telegram import InputFile
from urllib.parse import quote
from openai import OpenAI
import psycopg2
from duckduckgo_search import DDGS

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
                initial_facts = [
                    ("Привет! Чем могу помочь?", 6909708460),
                    ("Документы по награждениям находятся в папке /documents/Награждения.", 6909708460),
                    ("Всё отлично, спасибо за вопрос!", 6909708460),
                    ("ВСКС - Всероссийский студенческий корпус спасателей, основанный 22 апреля 2001 года. Организация объединяет свыше 8 000 добровольцев из 88 субъектов России, которые участвуют в ликвидации последствий чрезвычайных ситуаций, таких как пожары и наводнения, а также проводят гуманитарные миссии.", 6909708460),
                    ("Козеев Евгений Викторович - Руководитель ВСКС", 6909708460),
                    ("Гуманитарные миссии - Всероссийский студенческий корпус спасателей (ВСКС) проводит гуманитарные миссии по нескольким направлениям: Ростовская область, Курская область, Запорожская область, Херсонская область, Донецкая Народная Республика, Луганская Народная Республика. Гуманитарные миссии проводятся 2 раза в месяц, каждые 1-15 и 15-30 числа месяца. Условия: проживание, питание и проезд за счёт ВСКС и партнёров. Заявки для участия можно подать через @kristina_pavlik.", 6909708460),
                    ("ЧС в которых ВСКС принимал участие - Добровольцы ВСКС приняли участие в ликвидации свыше 50 крупных чрезвычайных ситуаций и их последствий. Студенты-спасатели участвовали в ликвидации последствий лесных пожаров в Центральном федеральном округе, Тюменской области, Красноярском и Забайкальском краях; наводнений в Иркутской, Оренбургской, Курганской областях, Краснодарском и Алтайском краях, на Дальнем Востоке, в Республике Крым; степных пожаров в Забайкальском крае, ликвидации последствий разлива нефтепродуктов в Чёрное море и других ЧС. Добровольцы также помогают в ликвидации ЧС и их последствий на региональном уровне.", 6909708460),
                    ("В ВСКС - Свыше 8 000 добровольцев из 88 субъектов Российской Федерации.", 6909708460),
                    ("ВСКС основан - 22 апреля 2001 года по инициативе министра МЧС России того времени Сергея Кужугетовича Шойгу.", 6909708460),
                    ("Багаутдинов Ахмет Айратович - Начальник отдела регионального взаимодействия ЦУ ВСКС, координирует работу отдела, контакт: @baa_msk.", 6909708460),
                    ("Павлик Кристина Валентиновна - Заместитель начальника отдела регионального взаимодействия ЦУ ВСКС, занимается набором добровольцев на гуманитарные миссии ВСКС и ликвидации последствий ЧС, контакт: @kristina_pavlik.", 6909708460),
                    ("Кременецкая Галина Сергеевна - Сотрудник отдела регионального взаимодействия ЦУ ВСКС, занимается набором добровольцев из региональных отделений ВСКС на обучение по первоначальной подготовке спасателей на базе Всероссийского центра координации, подготовки и переподготовки студенческих добровольных спасательных формирований (ВЦПСФ), контакт: @ikremenetskaya.", 6909708460),
                    ("Локтионова Дарья Петровна - Сотрудник отдела регионального взаимодействия ЦУ ВСКС, занимается обработкой служебных записок региональных отделений ВСКС по выдаче форменной одежды, контакт: @otoorukun.", 6909708460),
                    ("Форум ВСКС - Всероссийский форум волонтёров безопасности.", 6909708460),
                    ("Слёт ВСКС - Всероссийский слёт студентов-спасателей и добровольцев в ЧС, V Всероссийский слёт студентов-спасателей и добровольцев в ЧС пройдёт с 30 сентября по 5 октября 2025 года на территории учебно-тренировочного полигона пожарных и спасателей в Московской области.", 6909708460),
                    ("Андреев Алексей Евгеньевич - Заместитель руководителя ВСКС по развитию региональных отделений ВСКС и взаимодействию с ними.", 6909708460)
                ]
                for fact, admin_id in initial_facts:
                    cur.execute("""
                        INSERT INTO knowledge_base (fact_text, added_by) VALUES (%s, %s) ON CONFLICT DO NOTHING
                    """, (fact, admin_id))
                logger.info("Таблица knowledge_base создана с начальными фактами.")
            else:
                logger.info("Таблица knowledge_base уже существует.")

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

# Функции для работы с базой знаний в Postgres
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
KNOWLEDGE_BASE = load_knowledge_base()

# Системный промпт для ИИ
system_prompt = """
Вы — полезный чат-бот, который логически анализирует всю историю переписки, чтобы давать последовательные ответы.
Обязательно используй актуальные данные из поиска в истории сообщений для ответов на вопросы о фактах, организациях или событиях.
Если данные из поиска доступны, основывайся только на них и отвечай подробно, но кратко.
Если данных нет, используй свои знания и базу знаний, предоставленную системой.
Не упоминай процесс поиска, источники или фразы вроде "не знаю" или "уточните".
Всегда учитывай полный контекст разговора.
Отвечай кратко, по делу, на русском языке, без лишних объяснений.
"""

# Сохранение истории переписки
histories: Dict[int, Dict[str, Any]] = {}

# Обработчик команды /start
async def send_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id: int = update.effective_user.id
    user_name = USER_PROFILES.get(user_id, {}).get("name", "Пользователь")
    if user_id not in ALLOWED_USERS and user_id not in ALLOWED_ADMINS:
        await update.message.reply_text(f"{user_name}, ваш user_id: {user_id}\nИзвините, у вас нет доступа.",
                                        reply_markup=ReplyKeyboardRemove())
        return
    if user_id not in USER_PROFILES:
        context.user_data["awaiting_fio"] = True
        await update.message.reply_text(f"{user_name}, напишите своё ФИО.", reply_markup=ReplyKeyboardRemove())
        return
    profile = USER_PROFILES[user_id]
    if profile.get("name") is None:
        context.user_data["awaiting_name"] = True
        await update.message.reply_text(f"{user_name}, как я могу к вам обращаться? Укажите краткое имя (например, Кристина).",
                                        reply_markup=ReplyKeyboardRemove())
    else:
        await show_main_menu(update, context)

# Команда /add_fact для добавления фактов (только для админов)
async def add_fact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global KNOWLEDGE_BASE
    user_id: int = update.effective_user.id
    user_name = USER_PROFILES.get(user_id, {}).get("name", "Администратор")
    if user_id not in ALLOWED_ADMINS:
        await update.message.reply_text(f"{user_name}, только администраторы могут добавлять факты.",
                                        reply_markup=ReplyKeyboardRemove())
        return
    args = context.args
    if not args:
        await update.message.reply_text(f"{user_name}, использование: /add_fact <факт>",
                                        reply_markup=ReplyKeyboardRemove())
        return
    fact = ' '.join(args).strip()
    if not any(f['text'] == fact for f in KNOWLEDGE_BASE):
        save_knowledge_fact(fact, user_id)
        KNOWLEDGE_BASE = load_knowledge_base()
        await update.message.reply_text(f"{user_name}, факт '{fact}' добавлен в базу знаний.",
                                        reply_markup=ReplyKeyboardRemove())
        logger.info(f"Факт '{fact}' добавлен администратором {user_id} в knowledge_base")
    else:
        await update.message.reply_text(f"{user_name}, факт '{fact}' уже существует в базе знаний.",
                                        reply_markup=ReplyKeyboardRemove())

# Команда /delete_fact для удаления фактов (только для админов)
async def delete_fact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global KNOWLEDGE_BASE
    user_id: int = update.effective_user.id
    user_name = USER_PROFILES.get(user_id, {}).get("name", "Администратор")
    if user_id not in ALLOWED_ADMINS:
        await update.message.reply_text(f"{user_name}, только администраторы могут удалять факты.",
                                        reply_markup=ReplyKeyboardRemove())
        return
    if not KNOWLEDGE_BASE:
        await update.message.reply_text(f"{user_name}, база знаний пуста.", reply_markup=ReplyKeyboardRemove())
        return
    facts_list = "\n".join([f"ID: {fact['id']} — {fact['text']}" for fact in KNOWLEDGE_BASE])
    context.user_data["awaiting_fact_id"] = True
    await update.message.reply_text(
        f"{user_name}, выберите ID факта для удаления:\n{facts_list}\n\nВведите ID:",
        reply_markup=ReplyKeyboardMarkup([['Назад']], resize_keyboard=True)
    )
    logger.info(f"Администратор {user_id} запросил удаление факта. Показаны факты:\n{facts_list}")

# Отображение главного меню
async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id: int = update.effective_user.id
    user_name = USER_PROFILES.get(user_id, {}).get("name", "Пользователь")
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
    await update.message.reply_text(f"{user_name}, выберите действие:", reply_markup=reply_markup)

# Отображение меню управления пользователями
async def show_admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id: int = update.effective_user.id
    user_name = USER_PROFILES.get(user_id, {}).get("name", "Администратор")
    keyboard = [
        ['Добавить пользователя', 'Добавить администратора'],
        ['Список пользователей', 'Список администраторов'],
        ['Удалить файл', 'Удалить факт'],
        ['Назад']
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text(f"{user_name}, выберите действие:", reply_markup=reply_markup)

# Отображение содержимого папки в /documents/
async def show_current_docs(update: Update, context: ContextTypes.DEFAULT_TYPE, is_return: bool = False) -> None:
    user_id: int = update.effective_user.id
    user_name = USER_PROFILES.get(user_id, {}).get("name", "Пользователь")
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

# Обработка callback-запросов
async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id: int = update.effective_user.id
    user_name = USER_PROFILES.get(user_id, {}).get("name", "Пользователь")
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
                await query.message.reply_text(f"{user_name}, ошибка: файл не найден.", reply_markup=default_reply_markup)
                logger.error(f"Файл с индексом {file_idx} не найден в папке {current_path} для user_id {user_id}")
                return

            file_name = files[file_idx]['name']
            file_path = f"{current_path.rstrip('/')}/{file_name}"
            logger.info(f"Попытка скачать файл {file_path} для user_id {user_id}")

            download_url = get_yandex_disk_file(file_path)
            if not download_url:
                await query.message.reply_text(f"{user_name}, ошибка: не удалось получить ссылку на файл. Проверьте YANDEX_TOKEN.",
                                              reply_markup=default_reply_markup)
                logger.error(f"Не удалось получить ссылку для файла {file_path}")
                return

            file_response = requests.get(download_url)
            if file_response.status_code == 200:
                file_size = len(file_response.content) / (1024 * 1024)
                if file_size > 20:
                    await query.message.reply_text(f"{user_name}, файл слишком большой (>20 МБ).", reply_markup=default_reply_markup)
                    logger.warning(f"Файл {file_name} слишком большой: {file_size} МБ")
                    return
                await query.message.reply_document(document=InputFile(file_response.content, filename=file_name))
                logger.info(f"Файл {file_name} успешно отправлен пользователю {user_id} из {current_path}")
            else:
                await query.message.reply_text(f"{user_name}, не удалось загрузить файл. Статус: {file_response.status_code}",
                                              reply_markup=default_reply_markup)
                logger.error(f"Ошибка загрузки файла {file_path}: статус {file_response.status_code}")
        except Exception as e:
            await query.message.reply_text(f"{user_name}, ошибка при скачивании: {str(e)}. Проверьте YANDEX_TOKEN.",
                                          reply_markup=default_reply_markup)
            logger.error(f"Ошибка при отправке файла: {str(e)}")

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

# Обработка текстовых сообщений
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global KNOWLEDGE_BASE
    user_id: int = update.effective_user.id
    chat_id: int = update.effective_chat.id
    user_input: str = update.message.text.strip()
    user_name = USER_PROFILES.get(user_id, {}).get("name", "Пользователь")
    logger.info(f"Получено сообщение от {chat_id} (user_id: {user_id}): {user_input}")
    log_request(user_id, user_input, "Обработка сообщения...")

    if user_id not in ALLOWED_USERS and user_id not in ALLOWED_ADMINS:
        await update.message.reply_text(f"{user_name}, извините, у вас нет доступа.", reply_markup=ReplyKeyboardRemove())
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
            await update.message.reply_text(f"{user_name}, выберите федеральный округ:",
                                            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))
            return
        await update.message.reply_text(f"{user_name}, сначала пройдите регистрацию с /start.")
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

    if context.user_data.get("awaiting_fact_id", False):
        if user_id not in ALLOWED_ADMINS:
            await update.message.reply_text(f"{user_name}, только администраторы могут удалять факты.",
                                            reply_markup=default_reply_markup)
            context.user_data.pop("awaiting_fact_id", None)
            return
        if user_input == "Назад":
            context.user_data.pop("awaiting_fact_id", None)
            await show_main_menu(update, context)
            return
        try:
            fact_id = int(user_input)
            if delete_knowledge_fact(fact_id, user_id):
                KNOWLEDGE_BASE = load_knowledge_base()
                await update.message.reply_text(f"{user_name}, факт с ID {fact_id} удалён.", reply_markup=default_reply_markup)
            else:
                await update.message.reply_text(f"{user_name}, факт с ID {fact_id} не найден.", reply_markup=default_reply_markup)
            context.user_data.pop("awaiting_fact_id", None)
        except ValueError:
            await update.message.reply_text(f"{user_name}, введите корректный ID факта (число).",
                                            reply_markup=ReplyKeyboardMarkup([['Назад']], resize_keyboard=True))
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

    if context.user_data.get("awaiting_federal_district", False):
        if user_input in FEDERAL_DISTRICTS:
            context.user_data["selected_federal_district"] = user_input
            context.user_data["awaiting_federal_district"] = False
            context.user_data["awaiting_region"] = True
            regions = FEDERAL_DISTRICTS[user_input]
            keyboard = [[region] for region in regions]
            await update.message.reply_text(f"{user_name}, выберите регион:",
                                            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))
            return
        await update.message.reply_text(f"{user_name}, выберите из предложенных округов.", reply_markup=ReplyKeyboardMarkup(
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
            await update.message.reply_text(f"{user_name}, как я могу к вам обращаться? Укажите краткое имя (например, Кристина).",
                                            reply_markup=ReplyKeyboardRemove())
            return
        await update.message.reply_text(f"{user_name}, выберите из предложенных регионов.",
                                        reply_markup=ReplyKeyboardMarkup([[region] for region in regions]))
        return

    if context.user_data.get("awaiting_name", False):
        USER_PROFILES[user_id]["name"] = user_input.strip()
        save_user_profiles(USER_PROFILES)
        context.user_data["awaiting_name"] = False
        await show_main_menu(update, context)
        await update.message.reply_text(f"{user_name}, рад знакомству! Задавайте вопросы или используйте меню.",
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
            await update.message.reply_text(f"{user_name}, только администраторы могут управлять пользователями.",
                                            reply_markup=default_reply_markup)
            return
        context.user_data.pop('awaiting_upload', None)
        await show_admin_menu(update, context)
        handled = True

    elif user_input == "Добавить пользователя":
        if user_id not in ALLOWED_ADMINS:
            await update.message.reply_text(f"{user_name}, только администраторы могут добавлять пользователей.",
                                            reply_markup=default_reply_markup)
            return
        context.user_data["awaiting_user_id"] = True
        context.user_data.pop('awaiting_upload', None)
        await update.message.reply_text(f"{user_name}, введите user_id нового пользователя (число):",
                                        reply_markup=ReplyKeyboardMarkup([['Назад']], resize_keyboard=True))
        handled = True

    elif user_input == "Добавить администратора":
        if user_id not in ALLOWED_ADMINS:
            await update.message.reply_text(f"{user_name}, только администраторы могут добавлять администраторов.",
                                            reply_markup=default_reply_markup)
            return
        context.user_data["awaiting_admin_id"] = True
        context.user_data.pop('awaiting_upload', None)
        await update.message.reply_text(f"{user_name}, введите user_id нового администратора (число):",
                                        reply_markup=ReplyKeyboardMarkup([['Назад']], resize_keyboard=True))
        handled = True

    elif user_input == "Список пользователей":
        if user_id not in ALLOWED_ADMINS:
            await update.message.reply_text(f"{user_name}, только администраторы могут просматривать список пользователей.",
                                            reply_markup=default_reply_markup)
            return
        context.user_data.pop('awaiting_upload', None)
        users_list = "\n".join([f"ID: {uid}" for uid in ALLOWED_USERS]) or "Список пользователей пуст."
        await update.message.reply_text(f"{user_name}, список пользователей:\n{users_list}",
                                        reply_markup=ReplyKeyboardMarkup([['Назад']], resize_keyboard=True))
        handled = True

    elif user_input == "Список администраторов":
        if user_id not in ALLOWED_ADMINS:
            await update.message.reply_text(f"{user_name}, только администраторы могут просматривать список администраторов.",
                                            reply_markup=default_reply_markup)
            return
        context.user_data.pop('awaiting_upload', None)
        admins_list = "\n".join([f"ID: {aid}" for aid in ALLOWED_ADMINS]) or "Список администраторов пуст."
        await update.message.reply_text(f"{user_name}, список администраторов:\n{admins_list}",
                                        reply_markup=ReplyKeyboardMarkup([['Назад']], resize_keyboard=True))
        handled = True

    elif user_input == "Удалить файл":
        if user_id not in ALLOWED_ADMINS:
            await update.message.reply_text(f"{user_name}, только администраторы могут удалять файлы.",
                                            reply_markup=default_reply_markup)
            return
        context.user_data.pop('awaiting_upload', None)
        await show_file_list(update, context, for_deletion=True)
        handled = True

    elif user_input == "Удалить факт":
        if user_id not in ALLOWED_ADMINS:
            await update.message.reply_text(f"{user_name}, только администраторы могут удалять факты.",
                                            reply_markup=default_reply_markup)
            return
        context.user_data.pop('awaiting_upload', None)
        await delete_fact(update, context)
        handled = True

    elif user_input == "Назад":
        context.user_data.pop('awaiting_upload', None)
        context.user_data.pop('awaiting_fact_id', None)
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

    # Если сообщение не было обработано как специальная команда или состояние, обрабатываем как запрос к AI
    if not handled:
        logger.info(f"Обрабатываю AI-запрос для user_id {user_id}: {user_input}")
        logger.info(f"История сообщений для chat_id {chat_id}: {histories.get(chat_id, {})}")
        if not KNOWLEDGE_BASE:
            logger.warning("База знаний пуста или не загружена")
            KNOWLEDGE_BASE = load_knowledge_base()
        logger.info(f"База знаний содержит {len(KNOWLEDGE_BASE)} фактов")
        # Обработка текстового сообщения через API
        if chat_id not in histories:
            histories[chat_id] = {"name": None, "messages": [{"role": "system", "content": system_prompt}]}

        # Добавляем базу знаний в контекст для всех пользователей
        if KNOWLEDGE_BASE:
            knowledge_text = "Известные факты для использования в ответах: " + "; ".join([fact['text'] for fact in KNOWLEDGE_BASE])
            histories[chat_id]["messages"].insert(1, {"role": "system", "content": knowledge_text})
            logger.info(f"Добавлены знания в контекст для user_id {user_id}: {len(KNOWLEDGE_BASE)} фактов")

        # Проверка необходимости веб-поиска
        need_search = any(word in user_input.lower() for word in [
            "актуальная информация", "последние новости", "найди в интернете", "поиск",
            "что такое", "информация о", "расскажи о", "найди", "поиск по", "детали о",
            "вскс", "спасатели", "корпус спасателей"
        ])
        if need_search:
            logger.info(f"Запускаю веб-поиск для запроса: {user_input}")
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
        else:
            logger.info(f"Веб-поиск не требуется для запроса: {user_input}")

        histories[chat_id]["messages"].append({"role": "user", "content": user_input})
        if len(histories[chat_id]["messages"]) > 20:
            histories[chat_id]["messages"] = histories[chat_id]["messages"][:1] + histories[chat_id]["messages"][-19:]

        messages = histories[chat_id]["messages"]

        # Запрос к API
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
                logger.info(f"Ответ модели {model} для user_id {user_id}: {ai_response}")
                break
            except openai.AuthenticationError as auth_err:
                logger.error(f"Ошибка авторизации для {model}: {str(auth_err)}")
                ai_response = "Ошибка авторизации: неверный API-ключ. Проверьте XAI_TOKEN."
                break
            except openai.APIError as api_err:
                if "403" in str(api_err):
                    logger.warning(f"403 Forbidden для {model}. Пробуем следующую модель.")
                    continue
                logger.error(f"Ошибка API для {model}: {str(api_err)}")
                ai_response = f"Ошибка API: {str(api_err)}"
                break
            except openai.RateLimitError as rate_err:
                logger.error(f"Превышен лимит для {model}: {str(rate_err)}")
                ai_response = "Превышен лимит запросов. Попробуйте позже."
                break
            except Exception as e:
                logger.error(f"Неизвестная ошибка для {model}: {str(e)}")
                ai_response = f"Неизвестная ошибка: {str(e)}"
                break
        else:
            logger.error("Все модели недоступны (403). Проверьте токен и подписку.")
            ai_response = "Все модели недоступны (403). Обновите SuperGrok или X Premium+."

        final_response = f"{user_name}, {ai_response}"
        histories[chat_id]["messages"].append({"role": "assistant", "content": ai_response})
        await update.message.reply_text(final_response, reply_markup=default_reply_markup)
        log_request(user_id, user_input, final_response)

# Обработка загруженных документов
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id: int = update.effective_user.id
    user_name = USER_PROFILES.get(user_id, {}).get("name", "Пользователь")
    if not context.user_data.get('awaiting_upload', False):
        await update.message.reply_text(f"{user_name}, используйте кнопку 'Загрузить файл' перед отправкой документа.")
        return
    document = update.message.document
    file_name = document.file_name
    if not file_name.lower().endswith(
            ('.pdf', '.doc', '.docx', '.xls', '.xlsx', '.cdr', '.eps', '.png', '.jpg', '.jpeg')):
        await update.message.reply_text(
            f"{user_name}, поддерживаются только файлы .pdf, .doc, .docx, .xls, .xlsx, .cdr, .eps, .png, .jpg, .jpeg.")
        context.user_data.pop('awaiting_upload', None)
        return
    file_size = document.file_size / (1024 * 1024)
    if file_size > 50:
        await update.message.reply_text(f"{user_name}, файл слишком большой (>50 МБ).")
        context.user_data.pop('awaiting_upload', None)
        return
    profile = USER_PROFILES.get(user_id)
    if not profile or "region" not in profile:
        await update.message.reply_text(f"{user_name}, ошибка: регион не определён.")
        context.user_data.pop('awaiting_upload', None)
        return
    region_folder = f"/regions/{profile['region']}/"
    create_yandex_folder(region_folder)
    try:
        file = await context.bot.get_file(document.file_id)
        file_content = await file.download_as_bytearray()
        if upload_to_yandex_disk(file_content, file_name, region_folder):
            await update.message.reply_text(f"{user_name}, файл успешно загружен в папку {region_folder}")
            logger.info(f"Файл {file_name} загружен пользователем {user_id} в {region_folder}")
        else:
            await update.message.reply_text(f"{user_name}, ошибка при загрузке файла. Проверьте YANDEX_TOKEN.")
            logger.error(f"Ошибка загрузки файла {file_name} в {region_folder} для user_id {user_id}")
    except Exception as e:
        await update.message.reply_text(f"{user_name}, ошибка: {str(e)}. Проверьте YANDEX_TOKEN.")
        logger.error(f"Ошибка обработки документа {file_name}: {str(e)}")
    context.user_data.pop('awaiting_upload', None)
    await show_main_menu(update, context)

# Отображение списка файлов
async def show_file_list(update: Update, context: ContextTypes.DEFAULT_TYPE, for_deletion: bool = False) -> None:
    user_id: int = update.effective_user.id
    user_name = USER_PROFILES.get(user_id, {}).get("name", "Пользователь")
    profile = USER_PROFILES.get(user_id)
    if not profile or "region" not in profile:
        await update.message.reply_text(f"{user_name}, ошибка: регион не определён.",
                                        reply_markup=context.user_data.get('default_reply_markup', ReplyKeyboardRemove()))
        return
    region_folder = f"/regions/{profile['region']}/"
    create_yandex_folder(region_folder)
    files = list_yandex_disk_files(region_folder)
    if not files:
        await update.message.reply_text(f"{user_name}, в папке {region_folder} нет файлов.",
                                        reply_markup=context.user_data.get('default_reply_markup', ReplyKeyboardRemove()))
        return
    context.user_data['file_list'] = files
    context.user_data['current_path'] = region_folder
    keyboard = [[InlineKeyboardButton(item['name'], callback_data=f"{'delete' if for_deletion else 'download'}:{idx}")]
                for idx, item in enumerate(files)]
    await update.message.reply_text(f"{user_name}, выберите файл для удаления:" if for_deletion else f"{user_name}, список всех файлов:",
                                    reply_markup=InlineKeyboardMarkup(keyboard))

# Основная функция
def main():
    try:
        app = Application.builder().token(TELEGRAM_TOKEN).build()
        app.add_handler(CommandHandler("start", send_welcome))
        app.add_handler(CommandHandler("add_fact", add_fact))
        app.add_handler(CommandHandler("delete_fact", delete_fact))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
        app.add_handler(CallbackQueryHandler(handle_callback_query))
        app.run_polling(allowed_updates=Update.ALL_TYPES)
    except Exception as e:
        logger.error(f"Ошибка при запуске бота: {str(e)}")
        raise

if __name__ == '__main__':
    main()