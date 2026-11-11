import os
import re
import time
import json
import logging
import datetime
import tempfile
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import html
import requests

# ---------------------------------------------------------
# НАЛАШТУВАННЯ ТА ЛОГУВАННЯ
# ---------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger("NEWS_365_BOT")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID", "@kropivnit").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
PORT = int(os.getenv("PORT", "10000"))
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL", "").strip()

# ПАУЗА ТА ЛІМІТИ (стосуються ТІЛЬКИ звичайних новин!):
# Для звичайних новин: пауза 3 хвилини (180 с), щоб не спамити стрічку
REGULAR_COOLDOWN = int(os.getenv("REGULAR_COOLDOWN", "180"))
# Ліміт на годину для звичайних новин (термінові виходять БЕЗ лімітів!)
MAX_REGULAR_PER_HOUR = int(os.getenv("MAX_REGULAR_PER_HOUR", "8"))

DEFAULT_DONORS = [
    "insiderUKR",
    "voynareal",
    "u_now",
    "uniannet",
    "RBC_ua_news",
    "oko_ukr",
    "suspilnenews",
    "truexanewsua",
    "babel"
]

DONOR_CHANNELS = [ch.strip().replace("@", "") for ch in os.getenv("DONOR_CHANNELS", "").split(",") if ch.strip()] or DEFAULT_DONORS

SEEN_POSTS_FILE = "seen_posts.txt"
MAX_SEEN_POSTS = 2000

recent_regular_post_timestamps = []

# ---------------------------------------------------------
# ЗЧИТУВАННЯ ВЛАСНОГО КАНАЛУ (ЗАХИСТ ПРИ ПЕРЕЗАПУСКУ НА RENDER)
# ---------------------------------------------------------
def fetch_own_channel_history(channel_username: str) -> list:
    """
    Зчитує останні пости з самого каналу @kropivnit, щоб після рестарту на Render
    бот ТОЧНО знав, які теми вже є в каналі, і передавав їх у Groq для уникнення дублів.
    """
    ch = channel_username.replace("@", "").strip()
    url = f"https://t.me/s/{ch}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)"
    }
    history = []
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            texts = re.findall(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', resp.text, re.DOTALL)
            for raw_t in texts:
                clean_t = re.sub(r'<[^>]+>', '', raw_t).strip()
                if clean_t and len(clean_t) > 20:
                    first_line = clean_t.split('\n')[0].replace('*', '').strip()
                    history.append(first_line)
            logger.info(f"Завантажено {len(history)} останніх заголовків із каналу @{ch}")
    except Exception as e:
        logger.warning(f"Не вдалося зчитати історію @{ch}: {e}")
    return history[-25:]

# ---------------------------------------------------------
# ВИЗНАЧЕННЯ ТЕРМІНОВОСТІ (ЕКСКЛЮЗИВ / МЕГА ВАЖЛИВЕ)
# ---------------------------------------------------------
BREAKING_KEYWORDS = [
    'термінов', 'breaking', 'блискавк', 'вибух', 'влучан', 'приліт',
    'масован', 'почався обстріл', 'заява зеленськ', 'путін', 'трамп',
    'ексклюзив', 'важливо!', 'офіційно', 'переговори', 'надзвичайн',
    'повітряні сили повідомляють', 'збито ракету', 'збито літак',
    'атака на', 'загиблих', 'постраждалих'
]

def is_breaking_news(text: str) -> bool:
    t = text.lower()
    return any(k in t for k in BREAKING_KEYWORDS)

# ---------------------------------------------------------
# ФІЛЬТРИ: ТІЛЬКИ ПРЯМА РЕКЛАМА ТА РАДАРНІ ТРИВОГИ
# ---------------------------------------------------------
STRICT_AD_KEYWORDS = [
    'favbet', 'фавбет', 'cosmolot', 'космолот', 'slotoking', 'слотокінг',
    'онлайн-казино', 'промокод', 'партнерський матеріал', 'на правах реклами',
    'erid:', 'рекламний пост', 'підпишись на наш інший канал'
]

def is_strict_ad(text: str) -> bool:
    t = text.lower()
    return any(kw in t for kw in STRICT_AD_KEYWORDS)

def is_air_tracking_alert(text: str) -> bool:
    """
    Варіант А: Суворо відсікаємо радарні повідомлення про рух цілей,
    якщо немає ПІДТВЕРДЖЕНИХ руйнувань, жертв або влучань.
    """
    t = text.lower()

    impact_markers = [
        'влучан', 'приліт', 'руйнуван', 'пожеж', 'постраждал', 'загинул',
        'поранен', 'вирва', 'пошкоджен', 'розібрали завали', 'наслідки атаки',
        'було збито', 'збито 0', 'збито 1', 'збито 2', 'збито 3', 'збито 4',
        'збито 5', 'збито 6', 'збито 7', 'збито 8', 'збито 9', 'результати роботи ппо'
    ]
    if any(m in t for m in impact_markers):
        return False

    target_markers = [
        'шахед', 'шахеди', 'бпла', 'дрон', 'дрони', 'безпілотник', 'ракета', 'ракети',
        'каб', 'каби', 'балістик', 'крилат', 'кинджал', 'іскандер', 'повітрян', 'ціль',
        'цілі', 'борт', 'ту-95', 'міг-31', 'авіаці', 'реактивн'
    ]

    tracking_markers = [
        'курс', 'напрямок', 'напрямку', 'вектор', 'повз', 'в бік', 'в сторону',
        'наближаєть', 'підлітає', 'заходить', 'фіксуєть', 'чисто', 'відбій',
        'загроза', 'рухаєть', 'помічено', 'над містом', 'над ', 'в районі', 'з півдня',
        'з півночі', 'зі сходу', 'з заходу', 'маневрує', 'висота', 'локаційно',
        'швидкісна ціль', 'пуск', 'пуски', 'тривога', 'укритт', 'не ігноруйте',
        'лунають вибухи', 'чутно вибухи', 'атакували', 'атакують'
    ]

    has_target = any(m in t for m in target_markers)
    has_tracking = any(m in t for m in tracking_markers)

    if has_target and has_tracking:
        return True

    short_alerts = ['повітряна тривога', 'відбій тривоги', 'загроза балістики', 'ракетна небезпека', 'увага!']
    if any(m in t for m in short_alerts) and len(t) < 200:
        return True

    return False

# ---------------------------------------------------------
# ОЧИЩЕННЯ ВІД СУРЖИКУ ТА ПАФОСУ
# ---------------------------------------------------------
SURZHYK_MAP = [
    (r'\bв октябр[іе]\b', 'у жовтні'),
    (r'\bв ноябр[іе]\b', 'у листопаді'),
    (r'\bв декабр[іе]\b', 'у грудні'),
    (r'\bв январ[іе]\b', 'у січні'),
    (r'\bв феврал[іе]\b', 'у лютому'),
    (r'\bв март[іе]\b', 'у березні'),
    (r'\bв апр[іе]л[іе]\b', 'у квітні'),
    (r'\bв ма[єе]\b', 'у травні'),
    (r'\bв іюн[іе]\b', 'у червні'),
    (r'\bв іюл[іе]\b', 'у липні'),
    (r'\bв август[іе]\b', 'у серпні'),
    (r'\bв сентябр[іе]\b', 'у вересні'),
    (r'\bГосстат\b', 'Держстат'),
    (r'\bводоотведення\b', 'водовідведення'),
    (r'\bпаливо і смазки\b', 'паливно-мастильні матеріали'),
    (r'\bпаливно-змащувальні\b', 'паливно-мастильні'),
    (r'\bпідросли\b', 'зросли'),
    (r'\bсклала\b', 'становила'),
    (r'\bв даний час\b', 'наразі'),
    (r'\bна протязі\b', 'протягом'),
    (r'\bприймати участь\b', 'брати участь'),
    (r'\bпо крайній мірі\b', 'принаймні'),
    (r'\bслідуючий\b', 'наступний'),
    (r'\bв якості\b', 'як'),
    (r'\bзгідно даних\b', 'за даними'),
    (r'\bводночас з цим\b', 'водночас')
]

def clean_surzhyk_and_russian(text: str) -> str:
    cleaned = text
    for pattern, replacement in SURZHYK_MAP:
        cleaned = re.sub(pattern, replacement, cleaned, flags=re.IGNORECASE)
    return cleaned

def clean_ai_fluff(text: str) -> str:
    fluff_patterns = [
        r'\n+(?:Це свідчить|Це вкотре|Це черговий доказ|Так тримати|Ворог продовжує|Варто зазначити|Слід зазначити|Нагадаємо, що|Використання .* дозволяє).*?$'
    ]
    cleaned = text
    for p in fluff_patterns:
        cleaned = re.sub(p, '', cleaned, flags=re.IGNORECASE | re.DOTALL)
    return cleaned.strip()

# ---------------------------------------------------------
# GROQ API
# ---------------------------------------------------------
SYSTEM_PROMPT = """Ти — професійний редактор провідного українського Telegram-каналу "NEWS УКРАЇНА 365" (@kropivnit).
Твоє завдання — переписати надану новину чистою літературною українською мовою у сухому, стриманому, телеграмному стилі (як Суспільне, Бабель, РБК-Україна).

ГОЛОВНІ ПРАВИЛА:
1. РОЗУМНА ПЕРЕВІРКА НА ПОВТОРИ (ДУБЛІ):
   - Тобі надано список заголовків новин, які вже вийшли в каналі.
   - Якщо ця новина описує ТУ САМУ ПОДІЮ чи заяву, що вже опублікована (навіть якщо з іншого каналу чи іншими словами) — відповідай ТІЛЬКИ ОДНИМ СЛОВОМ: DUPLICATE
   - АЛЕ якщо подія ІНША (наприклад: інше місто, інший новий удар, окрема нова заява чи інший випадок), навіть якщо є схожі слова (наприклад: знову про обстріл чи переговори) — це НОВА новина, її ТРЕБА опублікувати!

2. РОЗПІЗНАВАННЯ РЕКЛАМИ ТА ПРИХОВАНОГО ПІАРУ (ДЖИНСИ):
   - Якщо текст є самопіаром персони, бізнесмена чи політика, промо-кампанією приватного закладу чи фонду (наприклад: промо-кампанії Ківалова, реклама курсів, товарів чи казино) — відповідай ТІЛЬКИ ОДНИМ СЛОВОМ: SKIP
   - Офіційні новини держави (рішення Кабміну, ВР, президента, державні реформи та загальнонаціональні виплати/стипендії) — це НЕ джинса, це повноцінні новини, їх ПУБЛІКУВАТИ!

3. ЖОДНОЇ ВОДИ, ПАФОСУ ТА ПОВЧАНЬ:
   - КАТЕГОРИЧНО ЗАБОРОНЕНО писати штучні пафосні висновки: «Це свідчить про те, що...», «Це черговий доказ нашої сили...», «Так тримати!», «Це вкотре доводить...», «Ворог продовжує...».
   - Тільки сухі перевірені факти, без особистих емоцій чи моралізаторства.

4. ЖОДНИХ ПІДРУЧНИКОВИХ ПОЯСНЕНЬ ТА «ВІКІПЕДІЇ»:
   - Заборонено писати банальні технічні пояснення на зразок: «Використання реактивних двигунів дозволяє розвивати більшу швидкість...». Новини не повинні бути лекціями!

5. ПРОПОРЦІЙНІСТЬ ОБСЯГУ:
   - Якщо вхідний текст короткий (1-2 речення, коротка цитата) — твій пост має бути ТАКИМ ЖЕ КОРОТКИМ:
     **Жирний заголовок**
     
     «Текст цитати або короткої новини», — спікер / джерело.
   - Якщо новина велика — стисни її до 1–2 лаконічних інформативних абзаців.

6. ФАКТИЧНА ТОЧНІСТЬ:
   - Не вигадуй назв зброї чи моделей техніки. Не приписуй подію іншим силам, якщо в першоджерелі цього немає.

7. ФОРМАТУВАННЯ:
   - Рядок 1: інформативний жирний заголовок (**Заголовок новини**).
   - Порожній рядок.
   - Тіло новини: 1–2 лаконічні абзаци з ключовими словами, виділеними **жирним** (міста, прізвища, важливі цифри). Без підписів і хештегів.

8. МОВА:
   - Тільки чиста українська мова без суржику та русизмів.

Якщо текст є спамом, непотрібним побутовим дріб'язком або рекламою — відповідай: SKIP.
"""

def get_best_groq_model() -> str:
    candidate_models = [
        "openai/gpt-oss-120b",
        "qwen/qwen3.8-27b",
        "meta-llama/llama-3.3-70b-instruct",
        "llama-3.3-70b-versatile"
    ]
    try:
        resp = requests.get(
            "https://api.groq.com/openai/v1/models",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
            timeout=8
        )
        if resp.status_code == 200:
            available_ids = [m["id"] for m in resp.json().get("data", [])]
            for cand in candidate_models:
                if cand in available_ids:
                    return cand
            for m_id in available_ids:
                if "70b" in m_id or "120b" in m_id or "27b" in m_id:
                    return m_id
    except Exception as e:
        logger.warning(f"Помилка запиту моделей Groq: {e}")
    return "openai/gpt-oss-120b"

def rewrite_news(raw_text: str, published_titles: list) -> str:
    if not GROQ_API_KEY:
        logger.error("GROQ_API_KEY не задано!")
        return ""

    model = get_best_groq_model()

    recent_titles_str = "\n".join([f"- {t}" for t in published_titles[-20:]]) if published_titles else "(ще немає опублікованих новин)"

    user_prompt = f"""СПИСОК ОСТАННІХ ОПУБЛІКОВАНИХ НОВИН У КАНАЛІ:
{recent_titles_str}

НОВА НОВИНА ДЛЯ ОБРОБКИ:
{raw_text}

Якщо ця новина описує ТУ САМУ ПОДІЮ, що вже є в списку вище — напиши тільки DUPLICATE.
Якщо це прихований піар особи/закладу чи реклама — напиши SKIP.
Якщо це окрема, інша подія або важлива новина — перепиши якісно згідно з правилами."""

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": 0.1,
        "max_tokens": 2048
    }

    try:
        resp = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json"
            },
            json=payload,
            timeout=25
        )
        if resp.status_code != 200:
            logger.error(f"Помилка Groq API ({resp.status_code}): {resp.text}")
            return ""

        res_json = resp.json()
        content = res_json["choices"][0]["message"]["content"].strip()

        content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()

        if "DUPLICATE" in content.upper()[:15]:
            logger.info("Groq визначив новину як семантичний повтор (DUPLICATE)")
            return ""

        if content.upper() == "SKIP" or "SKIP" in content[:10]:
            logger.info("Groq відхилив новину як піар/рекламу/дрібниці (SKIP)")
            return ""

        content = clean_ai_fluff(content)
        content = clean_surzhyk_and_russian(content)

        return content
    except Exception as e:
        logger.error(f"Виняток під час виклику Groq: {e}")
        return ""

# ---------------------------------------------------------
# ПАРСИНГ ТЕЛЕГРАМ-КАНАЛІВ
# ---------------------------------------------------------
def clean_html_text(raw_html: str) -> str:
    text = re.sub(r'<br\s*/?>', '\n', raw_html, flags=re.IGNORECASE)
    text = re.sub(r'<a\b[^>]*>(.*?)</a>', r'\1', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<[^>]+>', '', text)
    text = html.unescape(text)
    return text.strip()

def scrape_channel(channel: str, seen_set: set) -> list:
    url = f"https://t.me/s/{channel}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)"
    }

    try:
        resp = requests.get(url, headers=headers, timeout=12)
        if resp.status_code != 200:
            return []

        html_content = resp.text
        message_chunks = re.findall(
            r'(<div class="tgme_widget_message_wrap[^"]*"[^>]*>.*?)(?=<div class="tgme_widget_message_wrap|$)',
            html_content,
            re.DOTALL
        )

        now = datetime.datetime.now(datetime.timezone.utc)
        results = []

        # Беремо тільки найсвіжіші 5 постів каналу
        for chunk in message_chunks[-5:]:
            id_match = re.search(r'data-post="([^"]+)"', chunk)
            if not id_match:
                continue
            post_id = id_match.group(1)

            if post_id in seen_set:
                continue

            time_match = re.search(r'<time[^>]+datetime="([^"]+)"', chunk)
            if time_match:
                try:
                    dt_str = time_match.group(1).replace('Z', '+00:00')
                    post_dt = datetime.datetime.fromisoformat(dt_str)
                    if (now - post_dt).total_seconds() > 24 * 3600:
                        seen_set.add(post_id)
                        continue
                except Exception:
                    pass

            text_match = re.search(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', chunk, re.DOTALL)
            raw_text = clean_html_text(text_match.group(1)) if text_match else ""

            if not raw_text or len(raw_text) < 25:
                seen_set.add(post_id)
                continue

            if is_strict_ad(raw_text):
                logger.info(f"Пропуск явної реклами у {post_id}")
                seen_set.add(post_id)
                continue

            if is_air_tracking_alert(raw_text):
                logger.info(f"Пропуск радарного моніторингу цілей у {post_id}")
                seen_set.add(post_id)
                continue

            photo_url = None
            photo_match = re.search(r'tgme_widget_message_photo_wrap[^"]*"[^>]*style="[^"]*background-image:url\([\'"]?(.*?)[\'"]?\)', chunk)
            if photo_match:
                photo_url = photo_match.group(1)

            video_url = None
            video_match = re.search(r'<video[^>]+src="([^"]+)"', chunk)
            if video_match:
                video_url = video_match.group(1)

            results.append({
                "post_id": post_id,
                "text": raw_text,
                "photo_url": photo_url,
                "video_url": video_url
            })

        return results

    except Exception as e:
        logger.error(f"Помилка скрапінгу {channel}: {e}")
        return []

# ---------------------------------------------------------
# ПУБЛІКАЦІЯ В TELEGRAM КАНАЛ
# ---------------------------------------------------------
def markdown_to_telegram_html(text: str) -> str:
    t = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    t = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', t)
    t = re.sub(r'\*(.*?)\*', r'<i>\1</i>', t)
    return t

def post_to_telegram(text: str, photo_url: str = None, video_url: str = None) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHANNEL_ID:
        logger.error("TELEGRAM_BOT_TOKEN або TELEGRAM_CHANNEL_ID не задано!")
        return False

    formatted_text = markdown_to_telegram_html(text)
    signature = '\n\n👉 <a href="https://t.me/kropivnit">NEWS УКРАЇНА 365 | Підписатися</a>'
    
    caption = formatted_text + signature
    if len(caption) > 1020:
        max_body_len = 1020 - len(signature)
        formatted_text = formatted_text[:max_body_len].rstrip()
        caption = formatted_text + signature

    base_api = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

    # 1. Відео
    if video_url:
        try:
            v_resp = requests.get(video_url, timeout=20, stream=True)
            if v_resp.status_code == 200:
                with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tf:
                    for chunk in v_resp.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            tf.write(chunk)
                    tmp_path = tf.name

                try:
                    with open(tmp_path, "rb") as vf:
                        res = requests.post(
                            f"{base_api}/sendVideo",
                            data={
                                "chat_id": TELEGRAM_CHANNEL_ID,
                                "caption": caption,
                                "parse_mode": "HTML"
                            },
                            files={"video": vf},
                            timeout=60
                        )
                        if res.status_code == 200:
                            logger.info("Відео успішно опубліковано")
                            return True
                finally:
                    if os.path.exists(tmp_path):
                        os.remove(tmp_path)
        except Exception as e:
            logger.error(f"Помилка відео: {e}")

    # 2. Фото
    if photo_url:
        try:
            p_resp = requests.get(photo_url, timeout=15)
            if p_resp.status_code == 200:
                with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tf:
                    tf.write(p_resp.content)
                    tmp_path = tf.name

                try:
                    with open(tmp_path, "rb") as pf:
                        res = requests.post(
                            f"{base_api}/sendPhoto",
                            data={
                                "chat_id": TELEGRAM_CHANNEL_ID,
                                "caption": caption,
                                "parse_mode": "HTML"
                            },
                            files={"photo": pf},
                            timeout=30
                        )
                        if res.status_code == 200:
                            logger.info("Фото успішно опубліковано")
                            return True
                finally:
                    if os.path.exists(tmp_path):
                        os.remove(tmp_path)
        except Exception as e:
            logger.error(f"Помилка фото: {e}")

    # 3. Текст
    try:
        full_text = formatted_text + signature
        res = requests.post(
            f"{base_api}/sendMessage",
            json={
                "chat_id": TELEGRAM_CHANNEL_ID,
                "text": full_text,
                "parse_mode": "HTML",
                "disable_web_page_preview": False
            },
            timeout=20
        )
        if res.status_code == 200:
            logger.info("Текст успішно опубліковано")
            return True
        else:
            logger.error(f"sendMessage помилка: {res.text}")
            return False
    except Exception as e:
        logger.error(f"Виняток sendMessage: {e}")
        return False

# ---------------------------------------------------------
# ГОЛОВНИЙ ЦИКЛ БОТА ІЗ РОЗУМНИМ КОНТРОЛЕМ
# ---------------------------------------------------------
def bot_worker_loop():
    logger.info("Запуск бота...")
    seen_set = set()
    last_regular_post_time = 0

    # 1. ЗАХИСТ ВІД ПОВТОРІВ ПІСЛЯ РЕСТАРТУ НА RENDER:
    published_titles = fetch_own_channel_history(TELEGRAM_CHANNEL_ID)

    # 2. ЗАХИСТ ВІД СТАРТОВОЇ ЛАВИНИ:
    logger.info("Ініціалізація донорів: фіксуємо старий беклог, щоб не спамити...")
    for ch in DONOR_CHANNELS:
        try:
            url = f"https://t.me/s/{ch}"
            resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
            if resp.status_code == 200:
                post_ids = re.findall(r'data-post="([^"]+)"', resp.text)
                for pid in post_ids:
                    seen_set.add(pid)
        except Exception:
            pass
        time.sleep(0.8)
    logger.info(f"Ініціалізацію завершено. Зафіксовано {len(seen_set)} існуючих постів. Бот чекає свіжих новин!")

    while True:
        try:
            now = time.time()

            recent_regular_post_timestamps[:] = [t for t in recent_regular_post_timestamps if now - t < 3600]

            regular_limit_reached = len(recent_regular_post_timestamps) >= MAX_REGULAR_PER_HOUR
            posted_in_this_cycle = False

            for channel in DONOR_CHANNELS:
                posts = scrape_channel(channel, seen_set)

                for item in posts:
                    post_id = item["post_id"]
                    raw_text = item["text"]

                    seen_set.add(post_id)

                    is_breaking = is_breaking_news(raw_text)

                    # --- ЛОГІКА РОЗПОДІЛУ ТЕМПУ ---
                    # Якщо новина МЕГА ВАЖЛИВА (термінова) -> ЛІМІТИ НЕ ДІЮТЬ! Вона публікується ОДРАЗУ!
                    # Якщо новина ЗВИЧАЙНА -> діє пауза 3 хвилини і годинний ліміт
                    if not is_breaking:
                        if posted_in_this_cycle:
                            continue

                        if regular_limit_reached:
                            logger.info(f"Годинний ліміт звичайних новин ({MAX_REGULAR_PER_HOUR}) вичерпано. Пропуск другорядної новини.")
                            continue

                        time_since_last = time.time() - last_regular_post_time
                        if time_since_last < REGULAR_COOLDOWN:
                            continue

                    # Семантична перевірка на повтор через Groq
                    logger.info(f"Обробка поста {post_id} (Терміново: {is_breaking})...")
                    rewritten = rewrite_news(raw_text, published_titles)

                    if not rewritten:
                        continue

                    # Публікація в канал
                    success = post_to_telegram(
                        text=rewritten,
                        photo_url=item.get("photo_url"),
                        video_url=item.get("video_url")
                    )

                    if success:
                        current_t = time.time()
                        first_line = rewritten.split('\n')[0].replace('*', '').strip()
                        published_titles.append(first_line)
                        if len(published_titles) > 30:
                            published_titles.pop(0)

                        if is_breaking:
                            logger.info(f"🚨 МЕГА ВАЖЛИВА НОВИНА ОПУБЛІКОВАНА НЕГАЙНО: {first_line}")
                        else:
                            last_regular_post_time = current_t
                            recent_regular_post_timestamps.append(current_t)
                            posted_in_this_cycle = True
                            logger.info(f"📰 Звичайна новина опублікована: {first_line}. Пауза 3 хв.")
                            break

                time.sleep(1.5)

            # Пауза між колами перевірки каналів
            time.sleep(40)

        except Exception as e:
            logger.error(f"Помилка в головному циклі: {e}", exc_info=True)
            time.sleep(25)

# ---------------------------------------------------------
# ВЕБ-СЕРВЕР ДЛЯ RENDER ТА KEEP-ALIVE
# ---------------------------------------------------------
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "application/json")
        self.end_headers()
        response = json.dumps({
            "status": "healthy",
            "service": "NEWS УКРАЇНА 365 Bot",
            "regular_posts_last_hour": len(recent_regular_post_timestamps)
        })
        self.wfile.write(response.encode("utf-8"))

    def log_message(self, format, *args):
        pass

def start_health_server():
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    logger.info(f"Health check сервер слухає порт {PORT}")
    server.serve_forever()

def keep_alive_pinger():
    if not RENDER_EXTERNAL_URL:
        return
    logger.info(f"Запуск keep-alive пінгера для: {RENDER_EXTERNAL_URL}")
    while True:
        try:
            time.sleep(600)
            requests.get(RENDER_EXTERNAL_URL, timeout=10)
        except Exception:
            pass

# ---------------------------------------------------------
# ТОЧКА ВХОДУ
# ---------------------------------------------------------
if __name__ == "__main__":
    server_thread = threading.Thread(target=start_health_server, daemon=True)
    server_thread.start()

    pinger_thread = threading.Thread(target=keep_alive_pinger, daemon=True)
    pinger_thread.start()

    bot_worker_loop()
