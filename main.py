import os
import sys
import re
import json
import time
import string
import logging
import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
import requests
from bs4 import BeautifulSoup
from groq import Groq

# ----------------- НАЛАШТУВАННЯ ТА ЛОГУВАННЯ -----------------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("NewsBot")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
PORT = int(os.getenv("PORT", 8080))

if not all([TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID, GROQ_API_KEY]):
    logger.error("Критичні змінні оточення відсутні!")

groq_client = Groq(api_key=GROQ_API_KEY)

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

# ----------------- ФІЛЬТР ШЛАКУ -----------------
JUNK_PHRASES = [
    # Реклама та реакції
    "підписатися", "подписаться", "джерело:", "источник:", "реакція користувачів",
    "реакция пользователей", "реакції громадян", "надіслати новину", "став лайк",
    "розіграш", "казино", "онлайн-казино", "патріарх кирил", "філатов",
    # Жовта преса та таблоїди
    "голлівуд", "шекспірівськ", "гордон", "продюсер", "шоу-бізнес",
    # Погода та псевдонаука
    "прогноз погоди", "синоптики попередили", "похолодання", "магнітні бурі",
    "вчені назвали", "дієтологи", "гороскоп", "знак зодіаку", "курс валют",
    # Дрібний кримінал і ДТП
    "п'яний водій", "напідпитку", "врізався в стовп", "побив сусіда", "вкрав",
    # Рутинні тривоги
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
    "сказав", "зазначив", "йдеться", "відомо", "словами", "даними"
}

STATE_FILE = "bot_state.json"

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"posted_hashes": [], "recent_topics": [], "last_post_time": 0}

def save_state(state):
    try:
        state["posted_hashes"] = state["posted_hashes"][-300:]
        state["recent_topics"] = state["recent_topics"][-50:]
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
    text_clean = text.lower().translate(str.maketrans('', '', string.punctuation + '«»—–"\''))
    words = text_clean.split()
    return {w[:5] for w in words if len(w) > 3 and w not in STOP_WORDS}

def is_text_duplicate(candidate_text, state):
    cand_stems = get_text_stem_words(candidate_text)
    if not cand_stems:
        return True

    for topic in state.get("recent_topics", []):
        old_stems = set(topic.get("stems", []))
        if not old_stems:
            continue
        intersection = len(cand_stems & old_stems)
        union = len(cand_stems | old_stems)
        similarity = intersection / union if union > 0 else 0
        if similarity >= 0.35:
            logger.info(f"Дублікат ({similarity:.2f}) з: {topic.get('title', '')}")
            return True
    return False

# ----------------- ПАРСИНГ ТЕЛЕГРАМУ З ЧЕСНИМ МЕДІА -----------------
def scrape_donor(channel_username):
    url = f"https://t.me/s/{channel_username}"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        resp = requests.get(url, headers=headers, timeout=12)
        if resp.status_code != 200:
            return []
        soup = BeautifulSoup(resp.text, 'html.parser')
        messages = soup.find_all('div', class_='tgme_widget_message_wrap')
        results = []
        now = datetime.datetime.now(datetime.timezone.utc)

        for msg in messages:
            msg_div = msg.find('div', class_='tgme_widget_message')
            if not msg_div:
                continue

            # Фільтр свіжості (до 3 годин)
            time_tag = msg_div.find('time', class_='time')
            if time_tag and time_tag.get('datetime'):
                try:
                    dt = datetime.datetime.fromisoformat(time_tag['datetime'].replace('Z', '+00:00'))
                    if (now - dt).total_seconds() > 3.0 * 3600:
                        continue
                except Exception:
                    pass
            else:
                continue

            text_div = msg_div.find('div', class_='tgme_widget_message_text')
            if not text_div:
                continue

            raw_text = clean_raw_text(text_div.get_text(separator="\n"))
            if is_junk_news(raw_text):
                continue

            # ПЕРЕВІРКА МЕДІА: ЖОДНИХ СТОП-КАДРІВ ЗАМІСТЬ ВІДЕО!
            video_tag = msg_div.find('video')
            video_thumb = msg_div.find('i', class_='tgme_widget_message_video_thumb')
            photo_tag = msg_div.find('a', class_='tgme_widget_message_photo_wrap')

            media_type = None
            media_url = None

            # Якщо це реальне відео з прямим посиланням
            if video_tag and video_tag.get('src') and video_tag.get('src').startswith('http'):
                media_type = 'video'
                media_url = video_tag['src']
            elif video_tag or video_thumb:
                # Це відео, але веб-телеграм сховав пряме посилання!
                # КАТЕГОРИЧНО НЕ СТАВИМО ФОТО-ОБКЛАДИНКУ — піде чистий текст!
                media_type = None
                media_url = None
            elif photo_tag and photo_tag.get('style'):
                # Це справжнє фото
                m = re.search(r"background-image:url\('([^']+)'\)", photo_tag['style'])
                if m:
                    media_type = 'photo'
                    media_url = m.group(1)

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

# ----------------- РЕДАКТОР З ЖИВИМИ ПРИКЛАДАМИ -----------------
def generate_channel_post(raw_text, recent_titles):
    prompt = f"""Ти — редактор провідного українського Telegram-каналу "NEWS УКРАЇНА 365".
Пиши ВИКЛЮЧНО так, як пишуть топові українські канали: коротко, сильно, без води, без машинних рамок.

ОСЬ ОСТАННІ ОПУБЛІКОВАНІ ТЕМИ В КАНАЛІ:
{json.dumps(recent_titles, ensure_ascii=False)}

ПРАВИЛО ДУБЛІКАТІВ ТА ШЛАКУ:
- Якщо новина описує ТОЙ САМИЙ факт, що вже був — пиши тільки: SKIP
- Якщо це жовта преса, плітки, шоубіз чи Гордон — пиши тільки: SKIP
- Якщо це інша важлива теза виступу або нова подія — пиши пост!

СУВОРО ЗАБОРОНЕНО:
❌ НІЯКИХ blockquote (ніяких рамок для цитат)!
❌ НІЯКОГО опису того, що на картинці («Трамп стоїть», «на фото видно», «на столі кепка»)!
❌ НІЯКИХ дивних слів на кшталт «трепетний виступ», «лафа»!
❌ НІЯКИХ повторів (коли спочатку переказуєш цитату, а потім вставляєш її ж у лапках)!

ПРИКЛАДИ СТИЛЮ ДЛЯ НОВИН:

Приклад 1 (Коротка цитата/заява):
«Наші вибори настільки нечесні, що ви навіть не уявляєте», — Трамп

Приклад 2 (Заява з акцентом):
⚡️ Ми можемо мати менше території, менше грошей і зброї, але гідності в нас все ж більше, ніж у багатьох. Ми будемо робити все, щоб ця війна закінчилася, але закінчилася гідно для нас, — Зеленський.

Приклад 3 (Важлива подія/удар):
💥 Російський обстріл логістичного центру в Київській області

У Київській області російські війська вдарили по логістичному центру, спалахнула пожежа. На місці працюють рятувальники. Наразі відомо про одного пораненого.

Приклад 4 (Конкретний факт):
рф б'є по реле-підстанціях, які треба було закопати під землю. Наразі з 84 реле закопано лише 3, — нардеп Нагорняк.

ТЕКСТ НОВИНИ:
{raw_text}

НАПИШИ ГОТОВИЙ ПОСТ (АБО СЛОВО SKIP):"""

    try:
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": "Ти професійний український новинний редактор. Твої тексти лаконічні, живі, точні та позбавлені будь-якої штучності."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.2,
            max_tokens=450
        )
        result = response.choices[0].message.content.strip()
        if "SKIP" in result.upper()[:15]:
            return None
        # Зачищаємо будь-які спроби вставити html blockquote
        result = re.sub(r'</?blockquote[^>]*>', '', result)
        return result.strip()
    except Exception as e:
        logger.error(f"Помилка Groq: {e}")
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
    logger.info("Бот запущено з оновленим фільтром...")

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

            recent_titles = [t.get("title", "") for t in state.get("recent_topics", [])[-30:]]

            for h, item in all_candidates:
                if posts_published >= max_posts_this_cycle:
                    break

                if is_text_duplicate(item['text'], state):
                    state["posted_hashes"].append(h)
                    continue

                formatted_post = generate_channel_post(item['text'], recent_titles)
                if not formatted_post:
                    state["posted_hashes"].append(h)
                    continue

                if re.search(r'[ыэъёЫЭЪЁ]', formatted_post):
                    state["posted_hashes"].append(h)
                    continue

                success = post_to_telegram(formatted_post, item.get('media_type'), item.get('media_url'))
                if success:
                    posts_published += 1
                    logger.info(f"Опубліковано ({posts_published}/{max_posts_this_cycle}) з @{item['channel']}!")
                    state["posted_hashes"].append(h)
                    state["last_post_time"] = time.time()

                    first_line = formatted_post.split('\n')[0][:80]
                    stems = list(get_text_stem_words(item['text']))
                    state["recent_topics"].append({"title": first_line, "stems": stems})
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
