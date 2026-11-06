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

# ----------------- АВТОМАТИЧНИЙ ВИБІР ДІЮЧОЇ МОДЕЛІ GROQ -----------------
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
            # Шукаємо якісну текстову модель
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

STOP_WORDS = {
    "і", "й", "та", "в", "у", "на", "з", "із", "зі", "до", "по", "за", "про", "проти",
    "від", "під", "над", "при", "через", "після", "для", "як", "що", "щоб", "бо", "тому",
    "але", "проте", "однак", "це", "ця", "цей", "ці", "того", "тому", "його", "її", "їх",
    "ми", "ви", "вони", "він", "вона", "воно", "я", "ти", "себе", "свій", "своя", "свої",
    "був", "була", "було", "були", "буде", "будуть", "є", "не", "ні", "так", "вже", "ще",
    "теж", "також", "лише", "тільки", "навіть", "дуже", "може", "можуть", "заявив", "повідомив",
    "сказав", "зазначив", "йдеється", "відомо", "словами", "даними"
}

STATE_FILE = "bot_state.json"

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"posted_hashes": [], "recent_channel_posts": [], "last_sync_time": 0, "last_post_time": 0}

def save_state(state):
    try:
        state["posted_hashes"] = state["posted_hashes"][-350:]
        state["recent_channel_posts"] = state["recent_channel_posts"][-40:]
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

def get_text_stem_words(text):
    text_clean = text.lower().translate(str.maketrans('', '', string.punctuation + '«»—–"\'$'))
    words = text_clean.split()
    return {w[:5] for w in words if len(w) > 3 and w not in STOP_WORDS}

# ----------------- РЕГУЛЯРНА СИНХРОНІЗАЦІЯ З ВАШИМ КАНАЛОМ -----------------
def sync_with_my_channel(force=False):
    now = time.time()
    # Синхронізуємося щоразу на старті або раз на 10 хвилин
    if not force and (now - state.get("last_sync_time", 0)) < 600:
        return

    try:
        # Завжди читаємо ваш публічний канал @kropivnit
        url = "https://t.me/s/kropivnit"
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=12)
        if resp.status_code == 200:
            raw_html = resp.text
            matches = re.findall(r'class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', raw_html, re.DOTALL)
            channel_posts = []
            for raw in matches[-30:]:
                clean = re.sub(r'<[^>]+>', '', re.sub(r'<br\s*/?>', '\n', raw))
                clean = html.unescape(clean_raw_text(clean)).strip()
                if len(clean) > 20:
                    first_line = clean.split('\n')[0][:80]
                    channel_posts.append(first_line)
                    state["posted_hashes"].append(str(hash(clean[:120])))

            if channel_posts:
                state["recent_channel_posts"] = channel_posts
                state["last_sync_time"] = now
                save_state(state)
                logger.info(f"Синхронізовано {len(channel_posts)} свіжих постів прямо з каналу @kropivnit!")
    except Exception as e:
        logger.error(f"Помилка синхронізації з каналом: {e}")

sync_with_my_channel(force=True)

# ----------------- ЛЮДСЬКИЙ АНАЛІЗ НА ПОВТОР (ЧИСЛА + СУТНІСТЬ) -----------------
def is_duplicate_human_logic(candidate_text, past_posts):
    low_cand = candidate_text.lower()
    cand_stems = get_text_stem_words(candidate_text)
    
    # Шукаємо всі конкретні числа (наприклад 16, 78, 42, 224 тощо)
    cand_numbers = set(re.findall(r'\b\d+\b', low_cand))
    # Виключаємо дрібні цифри типу 1, 2, 3
    meaningful_numbers = {n for n in cand_numbers if int(n) >= 5}

    for past in past_posts:
        low_past = past.lower()
        past_stems = get_text_stem_words(past)

        # 1. ПЕРЕВІРКА ЗА КЛЮЧОВИМИ ЦИФРАМИ ТА СУБ'ЄКТОМ (як $16 млрд від США):
        past_numbers = {n for n in re.findall(r'\b\d+\b', low_past) if int(n) >= 5}
        shared_nums = meaningful_numbers & past_numbers
        if shared_nums:
            # Якщо збігається специфічне число (наприклад 16) і є хоч 2 спільних слова:
            shared_words = cand_stems & past_stems
            if len(shared_words) >= 2:
                logger.info(f"Заблоковано повтор за цифрою {shared_nums} та темою: {past}")
                return True

        # 2. ПЕРЕВІРКА ЗА ЗАГАЛЬНОЮ СХОЖІСТЮ ТЕМИ (Jaccard):
        intersection = len(cand_stems & past_stems)
        union = len(cand_stems | past_stems)
        similarity = intersection / union if union > 0 else 0
        if similarity >= 0.28:
            logger.info(f"Заблоковано смисловий повтор ({similarity:.2f}) з: {past}")
            return True

    return False

# ----------------- ПАРСИНГ З ДОНОРІВ (БЕЗ ЧУЖИХ АВАТАРОК) -----------------
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

        latest_blocks = raw_messages[-5:] if len(raw_messages) > 5 else raw_messages[1:]

        for msg_block in reversed(latest_blocks):
            time_match = re.search(r'<time[^>]+datetime="([^"]+)"', msg_block)
            if not time_match:
                continue

            try:
                dt_str = time_match.group(1).replace('Z', '+00:00')
                dt = datetime.datetime.fromisoformat(dt_str)
                age_seconds = (now - dt).total_seconds()
                
                # Новини за останні 3.5 години
                if age_seconds > 3.5 * 3600 or age_seconds < -120:
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
            
            # ЧІТКИЙ ПОШУК ФОТО (СУВОРО БЕЗ АВАТАРОК!):
            photo_match = re.search(r'tgme_widget_message_photo_wrap[^>]+style=["\'][^"\']*background-image:url\([\'"]?(https?://[^\'")]+)', msg_block, re.IGNORECASE)

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

# ----------------- РЕДАКТОР НОВИН З ПОВНИМ КОНТЕКСТОМ КАНАЛУ -----------------
def generate_channel_post(raw_text, past_posts):
    # Показуємо моделі останні 15 новин з каналу
    channel_history = "\n".join([f"• {p}" for p in past_posts[-16:]])

    prompt = f"""Ти — випусковий редактор новинного Telegram-каналу "NEWS УКРАЇНА 365".
Твоє головне завдання — відсікти повтори і написати ЧИСТУ ФАКТИЧНУ НОВИНУ.

ОСЬ НОВИНИ, ЯКІ ВЖЕ ВИЙШЛИ В НАШОМУ КАНАЛІ:
{channel_history}

СУВОРЕ ПРАВИЛО ЗВІРКИ (ДУМАТИ ЯК ЛЮДИНА!):
Уважно порівняй суть нової новини зі стрічкою вище.
- Якщо новина розповідає про ТУ САМУ ТЕМУ, подію чи заяву (наприклад, про $16 млрд допомоги від США, навіть якщо з'явилися нові документи чи таблиці) — це ПОВТОР! Відповідай ТІЛЬКИ ОДНИМ СЛОВОМ: SKIP
- Публікуй тільки якщо це ДІЙСНО НОВА подія або рішення.

СТИЛЬ ПОСТУ:
1. Жирний заголовок у <b>...</b> з емодзі.
2. Порожній рядок.
3. 1-2 інформативні абзаци фактів з прямою мовою («...», — Прізвище).
❌ ЖОДНИХ особистих думок, моралізаторства чи оцінок!
❌ ЖОДНИХ слів "– ГВА", "позор", "толпа", рамок blockquote.

ТЕКСТ НОВОЇ НОВИНИ:
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
                    {"role": "system", "content": "Ти професійний український новинний редактор. Твоє першочергове завдання — не допускати повторів новин у каналі під жодним приводом."},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.15,
                "max_tokens": 380
            }
            resp = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=payload, timeout=20)
            if resp.status_code == 200:
                data = resp.json()
                result = data['choices'][0]['message']['content'].strip()
                if "SKIP" in result.upper()[:15]:
                    return "SKIP"
                result = re.sub(r'</?blockquote[^>]*>', '', result)
                
                result = re.sub(r'\bтолп[а-яіїє]*\b', 'натовп', result, flags=re.IGNORECASE)
                result = re.sub(r'\bпозор[а-яіїє]*\b', 'ганьба', result, flags=re.IGNORECASE)
                result = re.sub(r'картини жахливі', 'ситуація напружена', result, flags=re.IGNORECASE)
                
                return result.strip()
            elif resp.status_code == 429:
                time.sleep(6)
            else:
                break
        except Exception:
            break

    return None

# ----------------- ВІДПРАВКА В TELEGRAM -----------------
def post_to_telegram(text, media_type=None, media_url=None):
    base_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
    caption = text[:1020]
    
    try:
        # Відправка фото файлом:
        if media_type == 'photo' and media_url:
            img_res = requests.get(media_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=12)
            if img_res.status_code == 200:
                files = {"photo": ("image.jpg", img_res.content, "image/jpeg")}
                data = {"chat_id": TELEGRAM_CHANNEL_ID, "caption": caption, "parse_mode": "HTML"}
                res = requests.post(f"{base_url}/sendPhoto", data=data, files=files, timeout=25)
                if res.status_code == 200:
                    return True
                logger.warning(f"sendPhoto помилка ({res.status_code}): {res.text}")

        # Відправка відео файлом:
        if media_type == 'video' and media_url:
            vid_res = requests.get(media_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
            if vid_res.status_code == 200 and len(vid_res.content) < 45 * 1024 * 1024:
                files = {"video": ("video.mp4", vid_res.content, "video/mp4")}
                data = {"chat_id": TELEGRAM_CHANNEL_ID, "caption": caption, "parse_mode": "HTML"}
                res = requests.post(f"{base_url}/sendVideo", data=data, files=files, timeout=35)
                if res.status_code == 200:
                    return True
                logger.warning(f"sendVideo помилка ({res.status_code}): {res.text}")

        # Текстове повідомлення:
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
    logger.info("Бот запущено з регулярною синхронізацією каналу...")

    while True:
        try:
            # Оновлюємо стрічку з вашого каналу кожні 10 хвилин
            sync_with_my_channel()

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

            max_posts_this_cycle = 3 if len(all_candidates) >= 5 and not is_night else 1
            posts_published = 0

            past_posts = state.get("recent_channel_posts", [])

            for h, item in all_candidates:
                if posts_published >= max_posts_this_cycle:
                    break

                # 1. Швидкий людський фільтр за числами та ключовими темами каналу
                if is_duplicate_human_logic(item['text'], past_posts):
                    state["posted_hashes"].append(h)
                    continue

                # 2. Нейромережевий аналіз на повтор по суті
                res = generate_channel_post(item['text'], past_posts)
                
                if res == "SKIP":
                    state["posted_hashes"].append(h)
                    time.sleep(2)
                    continue

                if not res:
                    time.sleep(3)
                    continue

                formatted_post = res

                if re.search(r'[ыэъёЫЭЪЁ]', formatted_post):
                    state["posted_hashes"].append(h)
                    continue

                success = post_to_telegram(formatted_post, item.get('media_type'), item.get('media_url'))
                if success:
                    posts_published += 1
                    logger.info(f"Опубліковано чисту новину ({posts_published}/{max_posts_this_cycle}) з @{item['channel']} (Медіа: {item.get('media_type')})!")
                    state["posted_hashes"].append(h)
                    state["last_post_time"] = time.time()

                    first_line = re.sub(r'<[^>]+>', '', formatted_post).split('\n')[0][:80]
                    state.setdefault("recent_channel_posts", []).append(first_line)
                    past_posts.append(first_line)

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
