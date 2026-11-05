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

# ----------------- АВТОМАТИЧНИЙ ВИБІР АКТИВНОЇ МОДЕЛІ GROQ -----------------
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
            chat_models = [m for m in live_ids if "whisper" not in m.lower() and "guard" not in m.lower()]
            if chat_models:
                for m in chat_models:
                    if "qwen" in m.lower():
                        ACTIVE_MODEL = m
                        return ACTIVE_MODEL
                ACTIVE_MODEL = chat_models[0]
                return ACTIVE_MODEL
    except Exception as e:
        logger.error(f"Помилка вибору моделі: {e}")

    ACTIVE_MODEL = "qwen/qwen3.8-27b"
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
    return {"posted_hashes": [], "recent_titles": [], "last_post_time": 0}

def save_state(state):
    try:
        state["posted_hashes"] = state["posted_hashes"][-300:]
        state["recent_titles"] = state["recent_titles"][-30:]
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

# ----------------- СИНХРОНІЗАЦІЯ З КАНАЛОМ -----------------
def sync_with_my_channel():
    try:
        channel_name = TELEGRAM_CHANNEL_ID.lstrip('@') if TELEGRAM_CHANNEL_ID.startswith('@') else "kropivnit"
        url = f"https://t.me/s/{channel_name}"
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        if resp.status_code == 200:
            raw_html = resp.text
            matches = re.findall(r'class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', raw_html, re.DOTALL)
            count = 0
            for raw in matches[-15:]:
                clean = re.sub(r'<[^>]+>', '', re.sub(r'<br\s*/?>', '\n', raw))
                clean = html.unescape(clean_raw_text(clean)).strip()
                first_line = clean.split('\n')[0][:70]
                if len(first_line) > 10 and first_line not in state.get("recent_titles", []):
                    state.setdefault("recent_titles", []).append(first_line)
                    state["posted_hashes"].append(str(hash(clean[:120])))
                    count += 1
            save_state(state)
            logger.info(f"Синхронізовано {count} тем з каналу!")
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

# ----------------- РЕДАКТОР НОВИН -----------------
def generate_channel_post(raw_text, recent_titles):
    titles_summary = "\n".join([f"• {t}" for t in recent_titles[-15:]])

    prompt = f"""Ти — професійний новинний репортер Telegram-каналу "NEWS УКРАЇНА 365".
Твоє завдання — подавати ЧИСТІ ФАКТИ В СОЛІДНОМУ СТИЛІ БЕЗ ВЛАСНОЇ ДУМКИ.

ОСТАННІ ТЕМИ В НАШОМУ КАНАЛІ (ЯКЩО НОВИНА ОПИСУЄ ЦЮ САМУ ПОДІЮ — ПИШИ SKIP):
{titles_summary}

КАТЕГОРИЧНІ ПРАВИЛА:
❌ СУВОРО ЗАБОРОНЕНО писати особисту суб'єктивну думку та моралізаторство («Уявіть собі», «Це подвійні стандарти»). Тільки факти і пряма мова!
❌ ЖОДНИХ росіянізмів: "позор" (тільки "ганьба"), "толпа" (тільки "натовп"), "– ГВА".
❌ НІЯКИХ рамок blockquote!

СТРУКТУРА:
1. Жирний заголовок у тегах <b>...</b> з емодзі.
2. Порожній рядок.
3. 1-2 інформативні абзаци фактів та контексту з прямою мовою («...», — Прізвище).

ТЕКСТ НОВИНИ:
{raw_text}

ГОТОВИЙ ПОСТ У ФОРМАТІ HTML (АБО СЛОВО SKIP):"""

    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }

    model_name = get_working_groq_model()

    for attempt in range(2):
        try:
            payload = {
                "model": model_name,
                "messages": [
                    {"role": "system", "content": "Ти об'єктивний новинний редактор. Твої тексти — чисті факти без особистих суджень українською мовою."},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.15,
                "max_tokens": 400
            }
            resp = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=payload, timeout=20)
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
            elif resp.status_code == 429:
                time.sleep(8)
            else:
                break
        except Exception:
            break

    return None

# ----------------- ВІДПРАВКА В TELEGRAM З ПРЯМИМ ЗАВАНТАЖЕННЯМ ФАЙЛУ -----------------
def post_to_telegram(text, media_type=None, media_url=None):
    base_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
    caption = text[:1020] # Захист від ліміту довжини підпису Телеграму
    
    try:
        # ПРЯМЕ ЗАВАНТАЖЕННЯ ФОТО ЯК ФАЙЛУ (ГАРАНТОВАНА ВІДПРАВКА):
        if media_type == 'photo' and media_url:
            img_res = requests.get(media_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=12)
            if img_res.status_code == 200:
                files = {"photo": ("image.jpg", img_res.content, "image/jpeg")}
                data = {"chat_id": TELEGRAM_CHANNEL_ID, "caption": caption, "parse_mode": "HTML"}
                res = requests.post(f"{base_url}/sendPhoto", data=data, files=files, timeout=25)
                if res.status_code == 200:
                    return True
                logger.warning(f"sendPhoto файл помилка ({res.status_code}): {res.text}")

        # ПРЯМЕ ЗАВАНТАЖЕННЯ ВІДЕО:
        if media_type == 'video' and media_url:
            vid_res = requests.get(media_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
            if vid_res.status_code == 200 and len(vid_res.content) < 45 * 1024 * 1024:
                files = {"video": ("video.mp4", vid_res.content, "video/mp4")}
                data = {"chat_id": TELEGRAM_CHANNEL_ID, "caption": caption, "parse_mode": "HTML"}
                res = requests.post(f"{base_url}/sendVideo", data=data, files=files, timeout=35)
                if res.status_code == 200:
                    return True
                logger.warning(f"sendVideo файл помилка ({res.status_code}): {res.text}")

        # Текстовий резерв:
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

            recent_titles = state.get("recent_titles", [])

            for h, item in all_candidates:
                if posts_published >= max_posts_this_cycle:
                    break

                formatted_post = generate_channel_post(item['text'], recent_titles)
                if not formatted_post:
                    state["posted_hashes"].append(h)
                    time.sleep(2)
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

                    first_line = re.sub(r'<[^>]+>', '', formatted_post).split('\n')[0][:70]
                    state.setdefault("recent_titles", []).append(first_line)
                    recent_titles.append(first_line)

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
