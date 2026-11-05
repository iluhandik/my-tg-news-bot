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
    logger.error("Критичні змінні оточення відсутні! Перевірте налаштування на Render.")

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

# ----------------- РОЗШИРЕНИЙ ФІЛЬТР ШЛАКУ ТА КЛІКБЕЙТУ -----------------
JUNK_PHRASES = [
    # Реклама, підписки та реакції
    "підписатися", "подписаться", "джерело:", "источник:", "реакція користувачів",
    "реакция пользователей", "надіслати новину", "прислать новость", "фото:", "відео:",
    "став лайк", "розіграш", "казино", "онлайн-казино", "патріарх кирил", "філатов",
    # Погода, природа, здоров'я
    "прогноз погоди", "синоптики попередили", "похолодання", "потепління", "температурний рекорд",
    "магнітні бурі", "магнітна буря", "вчені назвали", "дієтологи", "лікарі попередили",
    "гороскоп", "знак зодіаку", "курс валют", "долар по", "євро по", "свято сьогодні",
    # Локальний кримінал і побутові ДТП (шлак)
    "п'яний водій", "п'яний кермувальник", "напідпитку", "врізався в стовп", "врізався в дерево",
    "не розминулися", "перекинувся легковик", "побив сусіда", "вкрав велосипед", "вкрав телефон",
    "побутовий конфлікт", "підпалив траву", "знайдено труп", "затримали хабарника на",
    # Рутинні спостереження тривог
    "повітряна тривога", "воздушная тревога", "відбій тривоги", "отбой тревоги",
    "чисто по", "рух бпла", "загроза застосування балістики", "перевірка сирен",
    "18+", "шок контент"
]

STOP_WORDS = {
    "і", "й", "та", "в", "у", "на", "з", "із", "зі", "до", "по", "за", "про", "проти",
    "від", "під", "над", "при", "через", "після", "для", "як", "що", "щоб", "бо", "тому",
    "але", "проте", "однак", "це", "ця", "цей", "ці", "того", "тому", "його", "її", "їх",
    "ми", "ви", "вони", "він", "вона", "воно", "я", "ти", "себе", "свій", "своя", "свої",
    "був", "була", "було", "були", "буде", "будуть", "є", "не", "ні", "так", "вже", "ще",
    "теж", "також", "лише", "тільки", "навіть", "дуже", "може", "можуть", "заявив", "повідомив",
    "сказав", "зазначив", "йдеться", "відомо", "словами", "даними", "джерела"
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
        if similarity >= 0.38:
            logger.info(f"Дублікат ({similarity:.2f}) з: {topic.get('title', '')}")
            return True
    return False

# ----------------- ПАРСИНГ ТЕЛЕГРАМУ -----------------
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

            # Фільтр часу: тільки свіжі новини до 3.5 годин
            time_tag = msg_div.find('time', class_='time')
            if time_tag and time_tag.get('datetime'):
                try:
                    dt = datetime.datetime.fromisoformat(time_tag['datetime'].replace('Z', '+00:00'))
                    if (now - dt).total_seconds() > 3.5 * 3600:
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

            video_tag = msg_div.find('video')
            photo_tag = msg_div.find('a', class_='tgme_widget_message_photo_wrap')

            media_type = None
            media_url = None

            if video_tag and video_tag.get('src'):
                media_type = 'video'
                media_url = video_tag['src']
            elif photo_tag and photo_tag.get('style'):
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

# ----------------- РЕДАКТОР GROQ З ФІЛЬТРОМ ЦІННОСТІ -----------------
def generate_channel_post(raw_text, recent_titles):
    prompt = f"""Ти — головний редактор провідного всеукраїнського Telegram-каналу "NEWS УКРАЇНА 365".
Твоє завдання — відібрати ТІЛЬКИ СПРАВЖНІ ТОП-НОВИНИ і переписати їх чистою, живою українською мовою.

ОСТАННІ ТЕМИ В КАНАЛІ:
{json.dumps(recent_titles, ensure_ascii=False)}

КРИТЕРІЙ ВАЖЛИВОСТІ (СУВОРО! ЖОДНОГО ШЛАКУ!):
ПУБЛІКУЙ ТІЛЬКИ ЯКЩО ЦЕ:
1. Фронт, бойові дії, операції ЗСУ/ГУР, втрати ворога, аналітика DeepState.
2. Ракетні/дронові удари по Україні, прильоти, наслідки, робота ППО, загиблі, стан енергетики (ДТЕК/Укренерго/блекаути).
3. Перші особи та топ-політика (Зеленський, Сирський, Буданов, уряд, важливі закони, гучні арешти СБУ/ДБР).
4. Велика геополітика ЩОДО УКРАЇНИ (Трамп, США, допомога, зброя, санкції проти рф, НАТО, ЄС, мирні переговори).

СУВОРО ВІДХИЛЯЙ І ПОВЕРТАЙ "SKIP", ЯКЩО ЦЕ:
- Дрібні побутові ДТП, локальні бійки, пограбування або кримінал одного села/міста.
- Погода, магнітні бурі, поради лікарів, свята, гороскопи.
- Дрібні закордонні новини, які не стосуються України (страйки в Європі, місцеві вибори без зв'язку з війною).
- Та сама подія, що вже є в списку останніх тем (дублікат). Але якщо це інша теза з виступу — публікуй!
- ЯКЩО СУМНІВАЄШСЯ У ЗНАЧУЩОСТІ — ОБОВ'ЯЗКОВО ПОВЕРТАЙ: SKIP

СТИЛЬ:
- Короткий, живий, без води. Якщо цитата: ⚡️ «Текст цитати...», — Автор.
- Якщо подія: емодзі заголовок (💥, ⚡️, 💧, 🕯) + 1-2 коротких абзаци по суті.
- Жодних повторів слів перед лапками. 100% переклад українською без росіянізмів.

ТЕКСТ НОВИНИ:
{raw_text}

НАПИШИ ПОСТ (АБО СЛОВО SKIP):"""

    try:
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": "Ти топовий український новинний редактор. Ти безжально відсікаєш нецікавий шлак і пусті новини, публікуючи тільки головне."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.25,
            max_tokens=650
        )
        result = response.choices[0].message.content.strip()
        if "SKIP" in result.upper()[:15]:
            return None
        return result
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
            logger.warning(f"sendVideo не пройшов ({res.text}), пробуємо фото/текст")

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
            logger.warning(f"sendPhoto не пройшов ({res.text}), відправляємо текстом")

        payload = {
            "chat_id": TELEGRAM_CHANNEL_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }
        res = requests.post(f"{base_url}/sendMessage", json=payload, timeout=15)
        return res.status_code == 200
    except Exception as e:
        logger.error(f"Помилка відправки в Telegram: {e}")
        return False

# ----------------- ГОЛОВНИЙ ЦИКЛ -----------------
def main_loop():
    logger.info("Бот запущено з фільтром шлаку та адаптивним темпом...")

    while True:
        try:
            # Визначаємо, чи зараз ніч за Києвом (UTC+2 / UTC+3)
            # Вночі поріг пропуску ще суворіший
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

            logger.info(f"Знайдено сирих кандидатів: {len(all_candidates)} (Нічний режим: {is_night})")

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
                    # Новину відхилено як шлак або дублікат
                    state["posted_hashes"].append(h)
                    continue

                if re.search(r'[ыэъёЫЭЪЁ]', formatted_post):
                    logger.warning("Знайдено російські літери, пост відхилено.")
                    state["posted_hashes"].append(h)
                    continue

                success = post_to_telegram(formatted_post, item.get('media_type'), item.get('media_url'))
                if success:
                    posts_published += 1
                    logger.info(f"Опубліковано важливу новину ({posts_published}/{max_posts_this_cycle}) з @{item['channel']}!")
                    state["posted_hashes"].append(h)
                    state["last_post_time"] = time.time()

                    first_line = formatted_post.split('\n')[0][:80]
                    stems = list(get_text_stem_words(item['text']))
                    state["recent_topics"].append({"title": first_line, "stems": stems})
                    recent_titles.append(first_line)

                    save_state(state)

                    if posts_published < max_posts_this_cycle:
                        time.sleep(35)

            # Адаптивна пауза:
            if posts_published > 0:
                sleep_time = 70
            elif is_night:
                sleep_time = 180  # Вночі затишшя — перевіряємо раз на 3 хвилини
            else:
                sleep_time = 110  # Вдень без новин — пауза трохи менше 2 хвилин

            time.sleep(sleep_time)

        except Exception as e:
            logger.error(f"Помилка в головному циклі: {e}")
            time.sleep(60)

if __name__ == "__main__":
    main_loop()
