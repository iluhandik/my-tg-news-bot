import os
import sys
import re
import json
import time
import string
import html
import logging
import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
import requests

# ----------------- НАЛАШТУВАННЯ ТА ЛОГУВАННЯ -----------------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("NewsBot")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")
GROQ_API_KEY = (os.getenv("GROQ_API_KEY") or os.getenv("AI_API_KEY") or "").strip()
PORT = int(os.getenv("PORT", 8080))

if not all([TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID, GROQ_API_KEY]):
    logger.error("Критичні змінні оточення відсутні!")

# ----------------- СПИСОК ДОНОРІВ -----------------
DONOR_CHANNELS = [
    "u_now", "uniannet", "insiderUKR", "UaOnlii", "operativnoZSU",
    "suspilnenews", "rbc_ukraine", "ssternenko", "DeepStateUA",
    "nefedoov365", "inslandd", "zsuwar", "supernova_plus", "chp_zahid"
]

# ----------------- HEALTH SERVER ДЛЯ RENDER -----------------
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"News Bot 24/7 Running OK")

    def log_message(self, format, *args):
        pass

def run_health_server():
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    logger.info(f"Health server запущено на порту {PORT}")
    server.serve_forever()

threading.Thread(target=run_health_server, daemon=True).start()

# ----------------- ВИБІР НАЙКРАЩОЇ МОДЕЛІ LLAMA -----------------
ACTIVE_MODEL = None

def get_working_groq_model():
    global ACTIVE_MODEL
    if ACTIVE_MODEL:
        return ACTIVE_MODEL

    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    try:
        resp = requests.get("https://api.groq.com/openai/v1/models", headers=headers, timeout=10)
        if resp.status_code == 200:
            live_ids = [m["id"] for m in resp.json().get("data", [])]
            for pref in ["llama-3.3-70b", "llama-3.1-70b", "llama-3.1-8b", "llama-3", "llama"]:
                for m in live_ids:
                    if pref in m.lower() and "guard" not in m.lower() and "whisper" not in m.lower():
                        ACTIVE_MODEL = m
                        logger.info(f"Вибрано модель LLAMA: {ACTIVE_MODEL}")
                        return ACTIVE_MODEL
    except Exception as e:
        logger.error(f"Помилка вибору моделі: {e}")

    ACTIVE_MODEL = os.getenv("AI_MODEL") or "llama-3.1-8b-instant"
    return ACTIVE_MODEL

# ----------------- ФІЛЬТР ШЛАКУ -----------------
JUNK_PHRASES = [
    "підписатися", "подписаться", "джерело:", "источник:", "реакція користувачів",
    "реакция пользователей", "реакції громадян", "надіслати новину", "став лайк",
    "розіграш", "казино", "онлайн-казино", "патріарх кирил", "філатов",
    "голлівуд", "шекспірівськ", "гордон", "продюсер", "шоу-бізнес",
    "прогноз погоди", "синоптики попередили", "похолодання", "магнітні бурі",
    "вчені назвали", "дієтологи", "гороскоп", "знак зодіаку", "курс валют",
    "п'яний водій", "напідпитку", "врізався в стовп", "побив сусіда", "вкрав",
    "повітряна тривога", "воздушная тревога", "відбій тривоги", "чисто по",
    "перевірка сирен", "18+", "шок контент"
]

STATE_FILE = "bot_state.json"

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"posted_hashes": [], "recent_stories": [], "last_post_time": 0}

def save_state(state):
    try:
        state["posted_hashes"] = state["posted_hashes"][-300:]
        state["recent_stories"] = state["recent_stories"][-40:]
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Помилка збереження стану: {e}")

state = load_state()

def clean_raw_text(text):
    text = re.sub(r'Реакція користувачів:.*', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'Реакция пользователей:.*', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'Реакції громадян:.*', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'https?://t\.me/\S+', '', text)
    text = re.sub(r't\.me/\S+', '', text)
    text = re.sub(r'@\w+', '', text)
    text = re.sub(r'[ \t]+', ' ', text)
    return text.strip()

def is_junk_news(text):
    low = text.lower()
    if len(text) < 65:
        return True
    for phrase in JUNK_PHRASES:
        if phrase in low:
            return True
    return False

# ----------------- СИНХРОНІЗАЦІЯ З КАНАЛОМ ПРИ СТАРТІ -----------------
def sync_with_my_channel():
    try:
        channel_name = TELEGRAM_CHANNEL_ID.lstrip('@') if TELEGRAM_CHANNEL_ID.startswith('@') else "kropivnit"
        url = f"https://t.me/s/{channel_name}"
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        if resp.status_code == 200:
            raw_html = resp.text
            matches = re.findall(r'class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', raw_html, re.DOTALL)
            count = 0
            for raw in matches[-20:]:
                clean = re.sub(r'<[^>]+>', '', re.sub(r'<br\s*/?>', '\n', raw))
                clean = html.unescape(clean_raw_text(clean)).strip()
                if len(clean) > 20 and clean not in state.get("recent_stories", []):
                    state.setdefault("recent_stories", []).append(clean[:200])
                    state["posted_hashes"].append(str(hash(clean[:120])))
                    count += 1
            save_state(state)
            logger.info(f"Синхронізовано {count} історій з каналу для семантичного захисту від повторів!")
    except Exception as e:
        logger.error(f"Помилка синхронізації: {e}")

sync_with_my_channel()

# ----------------- ПАРСИНГ ТІЛЬКИ СВІЖИХ НОВИН -----------------
def scrape_donor(channel_username):
    url = f"https://t.me/s/{channel_username}"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        resp = requests.get(url, headers=headers, timeout=12)
        if resp.status_code != 200:
            return []
        
        raw_html = resp.text
        raw_messages = raw_html.split('class="tgme_widget_message_wrap')
        results = []
        now = datetime.datetime.now(datetime.timezone.utc)

        latest_blocks = raw_messages[-4:] if len(raw_messages) > 4 else raw_messages[1:]

        for msg_block in reversed(latest_blocks):
            time_match = re.search(r'<time[^>]+datetime="([^"]+)"', msg_block)
            if not time_match:
                continue

            try:
                dt_str = time_match.group(1).replace('Z', '+00:00')
                dt = datetime.datetime.fromisoformat(dt_str)
                age_seconds = (now - dt).total_seconds()
                
                if age_seconds > 2.0 * 3600 or age_seconds < -120:
                    continue
            except Exception:
                continue

            text_match = re.search(r'class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', msg_block, re.DOTALL)
            if not text_match:
                continue

            raw_text = text_match.group(1)
            raw_text = re.sub(r'<br\s*/?>', '\n', raw_text)
            raw_text = re.sub(r'<[^>]+>', '', raw_text)
            raw_text = html.unescape(raw_text)
            raw_text = clean_raw_text(raw_text)

            if is_junk_news(raw_text):
                continue

            video_match = re.search(r'<video[^>]+src=["\'](https?://[^"\']+)["\']', msg_block, re.IGNORECASE)
            photo_match = re.search(r'background-image:url\([\'"]?(https?://[^\'")]+)', msg_block, re.IGNORECASE) or re.search(r'<img[^>]+src=["\'](https?://[^"\']+)["\']', msg_block, re.IGNORECASE)

            media_type = None
            media_url = None

            if video_match:
                media_type = 'video'
                media_url = video_match.group(1)
            elif photo_match:
                media_type = 'photo'
                media_url = photo_match.group(1)

            results.append({
                "channel": channel_username,
                "text": raw_text,
                "media_type": media_type,
                "media_url": media_url
            })
        return results
    except Exception as e:
        logger.error(f"Помилка парсингу @{channel_username}: {e}")
        return []

# ----------------- РЕДАКТОР НОВИН (ЧИСТІ ФАКТИ БЕЗ ВЛАСНОЇ ДУМКИ) -----------------
def generate_channel_post(raw_text, recent_stories):
    stories_context = "\n---\n".join([f"• {s}" for s in recent_stories[-25:]])

    prompt = f"""Ти — професійний новинний репортер Telegram-каналу "NEWS УКРАЇНА 365".
Твоє завдання — подавати ЧИСТІ ФАКТИ ТА НОВИНИ В СОЛІДНОМУ ЖУРНАЛІСТСЬКОМУ СТИЛІ БЕЗ ВЛАСНОЇ ДУМКИ.

ОСЬ ПУБЛІКАЦІЇ В НАШОМУ КАНАЛІ (ЯКЩО НОВИНА ОПИСУЄ ТУ САМУ ПОДІЮ — ПИШИ SKIP):
{stories_context}

КАТЕГОРИЧНІ ПРАВИЛА:
❌ СУВОРО ЗАБОРОНЕНО писати особисту суб'єктивну думку, моралізаторство та авторські роздуми («Уявіть собі цю логіку», «Це не просто подвійні стандарти», «Поки хтось рахує жертви»). Автор каналу коментує сам, коли схоче!
❌ Твоє завдання — ЧИСТІ ФАКТИ: хто заявив, що конкретно сказав, суть події.
❌ ЖОДНИХ росіянізмів: "позор" (тільки "ганьба"), "толпа" (тільки "натовп"), "– ГВА".
❌ НІЯКИХ рамок blockquote!

СТРУКТУРА:
1. Жирний заголовок у тегах <b>...</b> з емодзі.
2. Порожній рядок.
3. 1-2 інформативні абзаци фактів: контекст та точна пряма мова («Цитата...», — Прізвище).

ЗРАЗОК ОФОРМЛЕННЯ:
<b>🇺🇸 Заява Трампа щодо війни в Україні та цивільних втрат</b>

Дональд Трамп прокоментував удари росії по мирних містах України, заявивши, що жертви є з обох сторін конфлікту.

«Я завжди про це говорю. Але я також говорю про іншу сторону, бо там теж гинуть мирні жителі. Це кровопролиття», — заявив Трамп.

ТЕКСТ НОВОЇ НОВИНИ:
{raw_text}

ГОТОВИЙ ПОСТ У ФОРМАТІ HTML (АБО СЛОВО SKIP):"""

    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }

    model_name = get_working_groq_model()

    try:
        payload = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": "Ти об'єктивний новинний редактор. Твої тексти — це чиста інформація та точні факти без повчань, сарказму чи особистих оцінок."},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.15,
            "max_tokens": 450
        }
        resp = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=payload, timeout=25)
        if resp.status_code == 200:
            data = resp.json()
            result = data['choices'][0]['message']['content'].strip()
            if "SKIP" in result.upper()[:15]:
                return None
            result = re.sub(r'</?blockquote[^>]*>', '', result)
            
            result = re.sub(r'\bтолп[а-яіїє]*\b', 'натовп', result, flags=re.IGNORECASE)
            result = re.sub(r'\bпозор[а-яіїє]*\b', 'ганьба', result, flags=re.IGNORECASE)
            result = re.sub(r'картини жахливі', 'ситуація напружена', result, flags=re.IGNORECASE)
            
            return result.strip()
        else:
            logger.error(f"Помилка Groq API ({resp.status_code}) на {model_name}: {resp.text}")
    except Exception as e:
        logger.error(f"Збій запиту до {model_name}: {e}")

    return None

# ----------------- ВІДПРАВКА В TELEGRAM -----------------
def post_to_telegram(text, media_type=None, media_url=None):
    base_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
    try:
        if media_type == 'video' and media_url:
            payload = {
                "chat_id": TELEGRAM_CHANNEL_ID,
                "video": media_url,
                "caption": text,
                "parse_mode": "HTML"
            }
            res = requests.post(f"{base_url}/sendVideo", json=payload, timeout=25)
            if res.status_code == 200:
                return True
            logger.warning(f"sendVideo не пройшов ({res.status_code}), пробуємо текстом...")

        if media_type == 'photo' and media_url:
            payload = {
                "chat_id": TELEGRAM_CHANNEL_ID,
                "photo": media_url,
                "caption": text,
                "parse_mode": "HTML"
            }
            res = requests.post(f"{base_url}/sendPhoto", json=payload, timeout=20)
            if res.status_code == 200:
                return True
            logger.warning(f"sendPhoto не пройшов ({res.status_code}), пробуємо текстом...")

        payload = {
            "chat_id": TELEGRAM_CHANNEL_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }
        res = requests.post(f"{base_url}/sendMessage", json=payload, timeout=15)
        return res.status_code == 200
    except Exception as e:
        logger.error(f"Помилка відправки: {e}")
        return False

# ----------------- ГОЛОВНИЙ ЦИКЛ -----------------
def main_loop():
    logger.info("Бот запущено успішно...")

    while True:
        try:
            now_utc = datetime.datetime.now(datetime.timezone.utc)
            kyiv_hour = (now_utc.hour + 2) % 24
            is_night = kyiv_hour >= 23 or kyiv_hour < 6

            all_candidates = []
            for donor in DONOR_CHANNELS:
                items = scrape_donor(donor)
                for item in items:
                    h = str(hash(item['text'][:120]))
                    if h not in state.get("posted_hashes", []):
                        all_candidates.append((h, item))
                time.sleep(1.2)

            logger.info(f"Знайдено кандидатів: {len(all_candidates)} (Ніч: {is_night})")

            max_posts_this_cycle = 3 if len(all_candidates) >= 6 and not is_night else 1
            posts_published = 0

            recent_stories = state.get("recent_stories", [])

            for h, item in all_candidates:
                if posts_published >= max_posts_this_cycle:
                    break

                formatted_post = generate_channel_post(item['text'], recent_stories)
                if not formatted_post:
                    state["posted_hashes"].append(h)
                    continue

                if re.search(r'[ыэъёЫЭЪЁ]', formatted_post):
                    state["posted_hashes"].append(h)
                    continue

                success = post_to_telegram(formatted_post, item.get('media_type'), item.get('media_url'))
                if success:
                    posts_published += 1
                    logger.info(f"Опубліковано чисту новину ({posts_published}/{max_posts_this_cycle}) з @{item['channel']}!")
                    state["posted_hashes"].append(h)
                    state["last_post_time"] = time.time()

                    summary_clean = re.sub(r'<[^>]+>', '', formatted_post)
                    state.setdefault("recent_stories", []).append(summary_clean[:220])
                    recent_stories.append(summary_clean[:220])

                    save_state(state)

                    if posts_published < max_posts_this_cycle:
                        time.sleep(35)

            if posts_published > 0:
                sleep_time = 75
            elif is_night:
                sleep_time = 180
            else:
                sleep_time = 110

            time.sleep(sleep_time)

        except Exception as e:
            logger.error(f"Помилка циклу: {e}")
            time.sleep(60)

if __name__ == "__main__":
    main_loop()
