import os
import re
import time
import json
import logging
import datetime
import tempfile
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import unquote
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
MAX_SEEN_POSTS = 1500

# ---------------------------------------------------------
# ЗБЕРІГАННЯ ОБРОБЛЕНИХ ПОСТІВ
# ---------------------------------------------------------
def load_seen_posts() -> set:
    if os.path.exists(SEEN_POSTS_FILE):
        try:
            with open(SEEN_POSTS_FILE, "r", encoding="utf-8") as f:
                return set(line.strip() for line in f if line.strip())
        except Exception as e:
            logger.error(f"Помилка читання {SEEN_POSTS_FILE}: {e}")
    return set()

def save_seen_post(post_id: str, seen_set: set):
    seen_set.add(post_id)
    try:
        posts_list = list(seen_set)[-MAX_SEEN_POSTS:]
        with open(SEEN_POSTS_FILE, "w", encoding="utf-8") as f:
            for p in posts_list:
                f.write(f"{p}\n")
    except Exception as e:
        logger.error(f"Помилка запису {SEEN_POSTS_FILE}: {e}")

# ---------------------------------------------------------
# ФІЛЬТРИ: РЕКЛАМА ТА РАДАРНИЙ МОНІТОРИНГ
# ---------------------------------------------------------
AD_KEYWORDS = [
    'ахметов', 'метінвест', 'дтек', 'favbet', 'фавбет', 'cosmolot', 'космолот',
    'slotoking', 'казино', 'онлайн-казино', 'промокод', 'знижк', 'акція', 'розпродаж',
    'партнерський матеріал', 'на правах реклами', 'реклама', 'купуйте', 'замовляйте',
    'підпишись на наш', 'посилання в закріпі', 'детальніше за посиланням', 'джерело в описі',
    'erid:', 'рекламний пост', 'партнерський спецпроєкт'
]

def is_ad(text: str) -> bool:
    t = text.lower()
    return any(kw in t for kw in AD_KEYWORDS)

def is_air_tracking_alert(text: str) -> bool:
    """
    Варіант А: Суворо пропускаємо (не публікуємо) повідомлення радарного моніторингу
    про рух повітряних цілей, якщо немає ПІДТВЕРДЖЕНИХ руйнувань, жертв або влучань.
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
# ОЧИЩЕННЯ ТЕКСТУ ВІД СУРЖИКУ ТА ПАФОСНИХ КЛІШЕ
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
    """
    Видаляє пафосні штучні висновки, повчання та лекції, які ШІ іноді дописує в кінці.
    """
    fluff_patterns = [
        r'\n+(?:Це свідчить|Це вкотре|Це черговий доказ|Так тримати|Ворог продовжує|Варто зазначити|Слід зазначити|Нагадаємо, що|Використання .* дозволяє).*?$'
    ]
    cleaned = text
    for p in fluff_patterns:
        cleaned = re.sub(p, '', cleaned, flags=re.IGNORECASE | re.DOTALL)
    return cleaned.strip()

# ---------------------------------------------------------
# GROQ API: РЕДАГУВАННЯ ТА СТИЛІЗАЦІЯ
# ---------------------------------------------------------
SYSTEM_PROMPT = """Ти — професійний редактор провідного українського Telegram-каналу "NEWS УКРАЇНА 365" (@kropivnit).
Твоє завдання — переписати надану новину чистою літературною українською мовою у сухому, стриманому, телеграмному стилі (як Суспільне, Бабель, РБК-Україна).

ГОЛОВНІ ПРАВИЛА (СУВОРО!):
1. ЖОДНОЇ ВОДИ, ПАФОСУ ТА ПОВЧАНЬ:
   - КАТЕГОРИЧНО ЗАБОРОНЕНО писати штучні пафосні висновки: «Це свідчить про те, що...», «Це черговий доказ нашої сили...», «Так тримати!», «Це вкотре доводить...», «Ворог продовжує...».
   - Тільки сухі перевірені факти, без особистих емоцій чи моралізаторства.

2. ЖОДНИХ ПІДРУЧНИКОВИХ ПОЯСНЕНЬ ТА «ВІКІПЕДІЇ»:
   - Заборонено писати банальні технічні пояснення на зразок: «Використання реактивних двигунів дозволяє розвивати більшу швидкість...». Новини не повинні бути лекціями!

3. ПРОПОРЦІЙНІСТЬ ОБСЯГУ (ДУЖЕ ВАЖЛИВО!):
   - Якщо вхідний текст короткий (1-2 речення, коротка цитата, наприклад: «Україна та РФ наблизилися до миру... — Мединський») — твій пост має бути ТАКИМ ЖЕ КОРОТКИМ!
     Формат:
     **Жирний заголовок**
     
     «Текст цитати або короткої новини», — спікер / джерело.
   - КАТЕГОРИЧНО ЗАБОРОНЕНО роздувати коротку цитату чи заяву в 3 абзаци аналітики або роздумів!
   - Якщо новина велика — стисни її до 1–2 лаконічних інформативних абзаців (Хто, Що, Де, Коли, з якими наслідками).

4. ФАКТИЧНА ТОЧНІСТЬ:
   - Не вигадуй назв зброї чи моделей техніки (якщо в джерелі просто «вертоліт» — не пиши «Ка-52 і Мі-8»).
   - Не приписуй подію іншим силам, якщо в першоджерелі цього немає (не видавай дружній вогонь чи аварії в РФ за операції ЗСУ, якщо про це явно не зазначено).

5. ФОРМАТУВАННЯ:
   - Рядок 1: інформативний жирний заголовок (**Заголовок новини**).
   - Порожній рядок.
   - Тіло новини: 1–2 лаконічні абзаци з ключовими словами, виділеними **жирним** (міста, прізвища, важливі цифри).
   - Не додавай підпис каналу, посилання чи хештеги — підпис додається ботом автоматично.

6. МОВА:
   - Тільки чиста українська мова без суржику та русизмів.

Якщо текст є рекламою, спамом або не містить інформації — відповідай одним словом: SKIP.
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

def rewrite_news(raw_text: str) -> str:
    if not GROQ_API_KEY:
        logger.error("GROQ_API_KEY не задано!")
        return ""

    model = get_best_groq_model()

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Перепиши новину для каналу:\n\n{raw_text}"}
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

        # Видаляємо теги міркувань моделей reasoning
        content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()

        if content.upper() == "SKIP" or "SKIP" in content[:10]:
            logger.info("Groq відхилив новину як рекламу або неінформативну (SKIP)")
            return ""

        content = clean_ai_fluff(content)
        content = clean_surzhyk_and_russian(content)

        return content
    except Exception as e:
        logger.error(f"Виняток під час виклику Groq: {e}")
        return ""

# ---------------------------------------------------------
# ПАРСИНГ ТЕЛЕГРАМ-КАНАЛІВ (WEB-PREVIEW)
# ---------------------------------------------------------
def clean_html_text(raw_html: str) -> str:
    text = re.sub(r'<br\s*/?>', '\n', raw_html, flags=re.IGNORECASE)
    # Зберігаємо внутрішній текст посилань: <a ...>Текст</a> -> Текст
    text = re.sub(r'<a\b[^>]*>(.*?)</a>', r'\1', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<[^>]+>', '', text)
    text = html.unescape(text)
    return text.strip()

def scrape_channel(channel: str, seen_set: set) -> list:
    url = f"https://t.me/s/{channel}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    }

    try:
        resp = requests.get(url, headers=headers, timeout=12)
        if resp.status_code != 200:
            logger.warning(f"Канал {channel} повернув статус {resp.status_code}")
            return []

        html_content = resp.text
        message_chunks = re.findall(
            r'(<div class="tgme_widget_message_wrap[^"]*"[^>]*>.*?)(?=<div class="tgme_widget_message_wrap|$)',
            html_content,
            re.DOTALL
        )

        now = datetime.datetime.now(datetime.timezone.utc)
        results = []

        for chunk in message_chunks:
            id_match = re.search(r'data-post="([^"]+)"', chunk)
            if not id_match:
                continue
            post_id = id_match.group(1)

            if post_id in seen_set:
                continue

            # Перевірка свіжості новини (не старіша за 24 години)
            time_match = re.search(r'<time[^>]+datetime="([^"]+)"', chunk)
            if time_match:
                try:
                    dt_str = time_match.group(1).replace('Z', '+00:00')
                    post_dt = datetime.datetime.fromisoformat(dt_str)
                    if (now - post_dt).total_seconds() > 24 * 3600:
                        save_seen_post(post_id, seen_set)
                        continue
                except Exception:
                    pass

            # Текст повідомлення
            text_match = re.search(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', chunk, re.DOTALL)
            raw_text = clean_html_text(text_match.group(1)) if text_match else ""

            if not raw_text or len(raw_text) < 25:
                save_seen_post(post_id, seen_set)
                continue

            if is_ad(raw_text):
                logger.info(f"Пропуск реклами в {post_id}")
                save_seen_post(post_id, seen_set)
                continue

            if is_air_tracking_alert(raw_text):
                logger.info(f"Пропуск радарного моніторингу цілей у {post_id}")
                save_seen_post(post_id, seen_set)
                continue

            # Пошук медіа
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

    # 1. Відправка відео, якщо є
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
                        else:
                            logger.warning(f"sendVideo повернув помилку: {res.text}")
                finally:
                    if os.path.exists(tmp_path):
                        os.remove(tmp_path)
        except Exception as e:
            logger.error(f"Помилка завантаження/відправки відео: {e}")

    # 2. Відправка фото, якщо є
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
                        else:
                            logger.warning(f"sendPhoto повернув помилку: {res.text}")
                finally:
                    if os.path.exists(tmp_path):
                        os.remove(tmp_path)
        except Exception as e:
            logger.error(f"Помилка завантаження/відправки фото: {e}")

    # 3. Текстовий пост, якщо немає медіа або відправка не вдалася
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
            logger.info("Текстове повідомлення успішно опубліковано")
            return True
        else:
            logger.error(f"sendMessage помилка: {res.text}")
            return False
    except Exception as e:
        logger.error(f"Виняток під час sendMessage: {e}")
        return False

# ---------------------------------------------------------
# ГОЛОВНИЙ ЦИКЛ БОТА
# ---------------------------------------------------------
def bot_worker_loop():
    logger.info("Запуск основного циклу обробки новин...")
    seen_set = load_seen_posts()
    logger.info(f"Завантажено {len(seen_set)} раніше оброблених постів")

    while True:
        try:
            for channel in DONOR_CHANNELS:
                logger.info(f"Перевірка каналу: @{channel}")
                posts = scrape_channel(channel, seen_set)

                for item in posts:
                    post_id = item["post_id"]
                    raw_text = item["text"]

                    logger.info(f"Обробка поста {post_id}...")
                    rewritten = rewrite_news(raw_text)

                    if not rewritten:
                        save_seen_post(post_id, seen_set)
                        continue

                    success = post_to_telegram(
                        text=rewritten,
                        photo_url=item.get("photo_url"),
                        video_url=item.get("video_url")
                    )

                    save_seen_post(post_id, seen_set)

                    if success:
                        logger.info(f"Успішно опубліковано новину з {post_id}")
                        time.sleep(25)
                    else:
                        time.sleep(5)

                time.sleep(2.0)

            time.sleep(60)

        except Exception as e:
            logger.error(f"Помилка в головному циклі: {e}", exc_info=True)
            time.sleep(30)

# ---------------------------------------------------------
# ВЕБ-СЕРВЕР ДЛЯ RENDER ТА KEEP-ALIVE
# ---------------------------------------------------------
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "application/json")
        self.end_headers()
        response = json.dumps({"status": "healthy", "service": "NEWS УКРАЇНА 365 Bot"})
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
