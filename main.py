import os
import re
import html
import time
import json
import logging
import hashlib
import threading
import urllib.request
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# --- CONFIGURATION ---
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHANNEL_ID = os.environ.get("TELEGRAM_CHANNEL_ID", "@kropivnit").strip()
GROQ_API_KEY = (os.environ.get("GROQ_API_KEY") or os.environ.get("AI_API_KEY") or "").strip()
PORT = int(os.environ.get("PORT", 10000))
RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL", "").strip()

# РОЗШИРЕНИЙ ТОП-СПИСОК ДОНОРІВ УКРАЇНИ
DONOR_CHANNELS = [
    # Оперативні мас-медіа та агрегатори
    "insiderUKR",
    "truexanewsua",
    "u_now",
    "kievreal1",
    "nefedoov365",
    "RBC_ua_news",
    "uniannet",
    "lachentyt",
    # Верифіковані офіційні ЗМІ
    "suspilnenews",
    "ukrpravda_news",
    # Військові ексклюзиви та ППО
    "operativnoZSU",
    "Tsaplienko"
]

STATE_FILE = "bot_state.json"

# --- HEALTH CHECK HTTP SERVER & KEEP-ALIVE ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_HEAD(self):
        # Відповідаємо 200 OK на системні пінги Render/UptimeRobot
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"OK: News Bot is running 24/7.")

def run_http_server():
    server = HTTPServer(("0.0.0.0", PORT), HealthCheckHandler)
    logger.info(f"Health-check HTTP server listening on port {PORT}")
    server.serve_forever()

def run_keep_alive():
    if not RENDER_EXTERNAL_URL:
        logger.info("RENDER_EXTERNAL_URL not set. Native keep-alive disabled.")
        return
    url = RENDER_EXTERNAL_URL if RENDER_EXTERNAL_URL.startswith("http") else f"https://{RENDER_EXTERNAL_URL}"
    logger.info(f"Keep-alive worker activated for URL: {url}")
    while True:
        try:
            time.sleep(480)
            r = requests.get(url, timeout=15)
            logger.info(f"Keep-alive ping sent to {url}, status: {r.status_code}")
        except Exception as e:
            logger.warning(f"Keep-alive ping error: {e}")

# --- ENTITY-BASED DEDUPLICATION STATE ---
def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"posted_hashes": [], "recent_entities": []}

def save_state(state):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Failed to save state: {e}")

def get_hash(text):
    clean = re.sub(r'\W+', '', text.lower())
    return hashlib.md5(clean.encode('utf-8')).hexdigest()

def extract_key_entities(text):
    clean = re.sub(r'<[^>]+>', ' ', text).lower()
    words = re.findall(r'[а-яіїєґa-z0-9]{4,}', clean)
    stop_words = {
        'який', 'яка', 'яке', 'які', 'його', 'її', 'цього', 'було', 'буде', 'коли', 
        'також', 'після', 'через', 'про', 'для', 'новини', 'канал', 'підписатися', 
        'фото', 'відео', 'повідомляє', 'заявив', 'каже', 'стало', 'відомо', 'повідомили', 
        'словами', 'офіційно', 'року', 'сьогодні', 'зараз', 'україни', 'росії', 'російських',
        'українських', 'період', 'області', 'районі', 'місті', 'внаслідок', 'людей'
    }
    return sorted(list(set(w for w in words if w not in stop_words)))

# --- SCRAPER FOR TELEGRAM DONORS ---
def scrape_channel(channel_name):
    url = f"https://t.me/s/{channel_name.lstrip('@')}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    }
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=12) as response:
            html_content = response.read().decode('utf-8', errors='ignore')
    except Exception as e:
        logger.warning(f"Failed to fetch {url}: {e}")
        return []

    messages = []
    blocks = re.findall(r'(<div class="tgme_widget_message\b.*?)(?=<div class="tgme_widget_message\b|$)', html_content, re.DOTALL)
    
    for block in blocks:
        post_match = re.search(r'data-post=["\']([^"\']+)["\']', block)
        if not post_match:
            continue
        data_post = post_match.group(1)
        parts = data_post.split('/')
        donor_username = "@" + parts[0]
        try:
            msg_id = int(parts[1])
        except Exception:
            continue

        text_match = re.search(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', block, re.DOTALL)
        raw_text = ""
        if text_match:
            raw_text = text_match.group(1)
            raw_text = re.sub(r'<br\s*/?>', '\n', raw_text)
            raw_text = re.sub(r'<a\b[^>]*>.*?</a>', '', raw_text)
            raw_text = re.sub(r'<[^>]+>', '', raw_text)
            raw_text = html.unescape(raw_text).strip()

        has_video = ('tgme_widget_message_video_player' in block or '<video' in block or 'tgme_widget_message_video_thumb' in block)
        has_photo = ('tgme_widget_message_photo_wrap' in block)

        photos = []
        bg_urls = re.findall(r'background-image:\s*url\((?:&quot;|\'|")?([^&\'"\)\s]+)', block)
        for u in bg_urls:
            u = html.unescape(u).strip()
            if any(skip in u.lower() for skip in ['user_photo', 'avatar', 'emoji', 'stickers']):
                continue
            if u not in photos:
                photos.append(u)

        if raw_text or has_video or has_photo:
            messages.append({
                "donor_channel": donor_username,
                "msg_id": msg_id,
                "text": raw_text,
                "has_video": has_video,
                "has_photo": has_photo,
                "photos": photos,
                "hash": get_hash(raw_text) if raw_text else None,
                "entities": extract_key_entities(raw_text)
            })

    return messages

# --- CHANNEL HISTORY VERIFICATION ---
def get_channel_recent_posts(target_channel):
    posts = []
    clean_target = target_channel.lstrip('@')
    url = f"https://t.me/s/{clean_target}"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            content = resp.read().decode('utf-8', errors='ignore')
        blocks = re.findall(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', content, re.DOTALL)
        for b in blocks[-50:]:
            clean = re.sub(r'<br\s*/?>', ' ', b)
            clean = re.sub(r'<[^>]+>', '', clean)
            clean = html.unescape(clean).strip()
            if clean:
                posts.append(clean)
    except Exception as e:
        logger.warning(f"Could not read recent posts from {target_channel}: {e}")
    return posts

def is_duplicate_post(candidate_entities, channel_posts, recent_state_entities):
    if len(candidate_entities) < 2:
        return False
    cand_set = set(candidate_entities)

    for past_item in recent_state_entities:
        past_set = set(past_item.get("entities", []))
        common = cand_set & past_set
        if len(common) >= 2 and len(common) / len(cand_set) >= 0.4:
            logger.info(f"Duplicate blocked by state cache! Overlap: {common}")
            return True

    for post_text in channel_posts:
        p_entities = set(extract_key_entities(post_text))
        common = cand_set & p_entities
        if len(common) >= 3 or (len(common) >= 2 and len(common) / len(cand_set) >= 0.45):
            logger.info(f"Duplicate blocked by channel history! Overlap: {common}")
            return True
    return False

# --- DYNAMIC GROQ MODEL DISCOVERY ---
def get_working_groq_model():
    if not GROQ_API_KEY:
        return "llama-3.1-8b-instant"
    url = "https://api.groq.com/openai/v1/models"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}"}
    try:
        r = requests.get(url, headers=headers, timeout=8)
        if r.status_code == 200:
            data = r.json().get("data", [])
            valid_ids = [
                m["id"] for m in data 
                if m.get("active", True) 
                and not any(x in m["id"].lower() for x in ["whisper", "guard", "embed", "vision", "audio"])
            ]
            for pref in ["qwen/qwen3.8-27b", "llama-3.3-70b-versatile", "llama-3.1-8b-instant"]:
                if pref in valid_ids:
                    return pref
            if valid_ids:
                return valid_ids[0]
    except Exception as e:
        logger.warning(f"Model lookup failed: {e}")
    return "llama-3.1-8b-instant"

# --- AI REWRITE & JOURNALISTIC FACT GUARD ---
def rewrite_news_with_groq(text, model_name):
    if not GROQ_API_KEY or not text or len(text.strip()) < 30:
        return None

    lower_raw = text.lower()
    local_fluff_keywords = [
        "дтек ввів", "дтек запровадив", "екстрені відключення", "стабілізаційні відключення",
        "графіки відключень", "ремонт водопроводу", "ремонт мосту", "перекрили рух",
        "на південному мосту", "на північному мосту"
    ]
    if any(k in lower_raw for k in local_fluff_keywords) and "масований обстріл" not in lower_raw:
        logger.info("Skipping local municipal/utility fluff.")
        return "SKIP"

    system_prompt = (
        "ТИ — ГОЛОВНИЙ РЕДАКТОР УКРАЇНСЬКОГО ТЕЛЕГРАМ-КАНАЛУ «NEWS УКРАЇНА 365» (@kropivnit).\n\n"
        "СУВОРІ РЕДАКЦІЙНІ ПРАВИЛА:\n"
        "1. ПРАВДА ПРО УДАРИ ПО РФ: Якщо ЗСУ або дрони уразили завод, склад чи базу в РФ — новина ТІЛЬКИ про успішний удар, вибухи та пожежу! "
        "Категорично заборонено перекручувати удар ЗСУ у вигляд «РФ нарощує виробництво» чи ретранслювати заяви МО РФ як факт.\n"
        "2. ТОЧНІСТЬ МІСЦЯ ПОДІЙ: Не плутай міста! Місто в заголовку зобов'язане збігатися з місцем безпосереднього удару.\n"
        "3. ЛОКАЛЬНІ ВІДКЛЮЧЕННЯ: Локальні графіки світла ДТЕК (Київщина, Одеса тощо) — пиши SKIP.\n"
        "4. КАТЕГОРИЧНО ЗАБОРОНЕНО писати примітки, міркування, вступати в діалог, писати від першої особи («я», «ми», «примітка:»).\n"
        "5. Чиста українська мова: «ДСНС» (не «ГСЧС»), «вирва» (не «воронка»), «величезна» (не «огромна»), «натовп» (не «толпа»), «ЗСУ» (не «ВСУ»).\n\n"
        "ФОРМАТ ПОСТА:\n"
        "⚡ <b>Короткий влучний заголовок</b>\n\n"
        "1-2 фактичні абзаци. Ключові дані виділяй <b>жирним</b>.\n"
        "Цитати: «Текст», — <b>Прізвище</b>.\n\n"
        "ВІДПОВІДЬ: ТІЛЬКИ ГОТОВИЙ ПОСТ АБО СЛОВО SKIP."
    )

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Ось вихідна новина:\n\n{text}"}
        ],
        "temperature": 0.15,
        "max_tokens": 800
    }

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=25)
        if resp.status_code != 200:
            logger.error(f"Groq API error {resp.status_code}: {resp.text}")
            return None
        res_json = resp.json()
        raw_out = res_json["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.error(f"Groq call exception: {e}")
        return None

    raw_out = re.sub(r'<think>.*?</think>', '', raw_out, flags=re.DOTALL).strip()

    if raw_out.upper() == "SKIP" or "SKIP" in raw_out[:10]:
        return "SKIP"

    out = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', raw_out)
    out = re.sub(r'__([\s\S]*?)__', r'<i>\1</i>', out)
    out = out.replace("<blockquote>", "").replace("</blockquote>", "")

    forbidden_meta = [
        r'\(у контексті', r'\*?примітка:?', r'я виправлю', r'я не можу', 
        r'давайте припустимо', r'перевіряю', r'за інструкцією', r'вхідний текст', 
        r'фактична хибність', r'суперечність', r'дотримаємось', r'якщо я напишу'
    ]
    for pattern in forbidden_meta:
        if re.search(pattern, out, re.IGNORECASE):
            logger.warning(f"AI leak detected ({pattern})! Dropping post.")
            return None

    valid_endings = ('.', '!', '?', '»', '"', '</b>', '</i>', ')')
    if not out.rstrip().endswith(valid_endings):
        last_punct = max(out.rfind('.'), out.rfind('!'), out.rfind('?'), out.rfind('»'))
        if last_punct > 120:
            out = out[:last_punct+1].strip()
        else:
            logger.warning("Post was cut off mid-sentence, dropping.")
            return None

    ru_fixes = {
        r'\bГСЧС\b': 'ДСНС',
        r'\bгсчс\b': 'ДСНС',
        r'\bогромн(а|у|е|і|их|им|ому)\b': r'величезн\1',
        r'\bворонку\b': 'вирву',
        r'\bворонка\b': 'вирва',
        r'\bворонки\b': 'вирви',
        r'\bукраїнські зсу\b': 'ЗСУ',
        r'\bтолп[а-я]*': 'натовп',
        r'\bпозор[а-я]*': 'ганьба',
        r'\bу даний момент\b': 'наразі',
        r'\bв даний час\b': 'наразі',
        r'\bВСУ\b': 'ЗСУ'
    }
    for r_pattern, replacement in ru_fixes.items():
        out = re.sub(r_pattern, replacement, out, flags=re.IGNORECASE)

    return out

# --- POSTING TO TELEGRAM (NATIVE MEDIA COPYING) ---
def publish_native_post(donor_channel, msg_id, text, has_media, photos):
    caption = text.strip()
    if len(caption) > 1020:
        caption = caption[:1015].rsplit(' ', 1)[0] + '...'

    # 1. Якщо у донора є відео/фото — копіюємо оригінал нативно
    if has_media and donor_channel and msg_id:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/copyMessage"
        payload = {
            "chat_id": TELEGRAM_CHANNEL_ID,
            "from_chat_id": donor_channel,
            "message_id": msg_id,
            "caption": caption,
            "parse_mode": "HTML"
        }
        try:
            r = requests.post(url, json=payload, timeout=25)
            res = r.json()
            if res.get("ok"):
                logger.info(f"Successfully copied native media from {donor_channel}/{msg_id}!")
                return True
            logger.warning(f"copyMessage failed ({res.get('error_code')}): {res.get('description')}")
        except Exception as e:
            logger.warning(f"copyMessage exception: {e}")

    # 2. Якщо це чисте фото (і copyMessage не спрацював)
    if photos:
        unique_photos = list(dict.fromkeys(photos))
        for p_url in unique_photos[:1]:
            try:
                headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://t.me/"}
                resp = requests.get(p_url, headers=headers, timeout=15)
                if resp.status_code == 200 and len(resp.content) > 1000:
                    files = {"photo": ("image.jpg", resp.content, "image/jpeg")}
                    data = {"chat_id": TELEGRAM_CHANNEL_ID, "caption": caption, "parse_mode": "HTML"}
                    r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto", data=data, files=files, timeout=30)
                    if r.json().get("ok"):
                        logger.info("Successfully published fallback photo post!")
                        return True
            except Exception as e:
                logger.warning(f"Fallback photo upload failed: {e}")

    # 3. Текстовий пост
    logger.info("Publishing as text post...")
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHANNEL_ID, "text": text, "parse_mode": "HTML"}
    r = requests.post(url, json=payload, timeout=15)
    return r.json().get("ok", False)

# --- MAIN AUTOMATION LOOP ---
def main_loop():
    logger.info("Initializing news bot with expanded donor pool...")
    state = load_state()

    # Cold start: populate hashes and key entities from donors
    if not state.get("posted_hashes"):
        logger.info("Cold start: seeding existing messages into cache...")
        for ch in DONOR_CHANNELS:
            msgs = scrape_channel(ch)
            for m in msgs:
                if m["hash"]:
                    state["posted_hashes"].append(m["hash"])
                if m["entities"]:
                    state["recent_entities"].append({"entities": m["entities"], "time": time.time()})
        save_state(state)

    while True:
        try:
            model_name = get_working_groq_model()
            logger.info(f"Checking {len(DONOR_CHANNELS)} donor channels using model: {model_name}...")

            cutoff_time = time.time() - (36 * 3600)
            state["recent_entities"] = [
                e for e in state.get("recent_entities", []) if e.get("time", 0) > cutoff_time
            ]

            channel_posts = get_channel_recent_posts(TELEGRAM_CHANNEL_ID)

            for channel in DONOR_CHANNELS:
                items = scrape_channel(channel)
                # Скануємо останні 3 повідомлення з кожного каналу
                for item in items[-3:]:
                    item_hash = item["hash"]
                    if not item_hash or item_hash in state["posted_hashes"]:
                        continue

                    raw_text = item["text"]
                    if not raw_text or len(raw_text) < 30:
                        continue

                    # 1. Перевірка на дублі за сутностями
                    if is_duplicate_post(item["entities"], channel_posts, state["recent_entities"]):
                        state["posted_hashes"].append(item_hash)
                        save_state(state)
                        continue

                    # 2. Рерайтинг AI за журналістськими стандартами
                    rewritten = rewrite_news_with_groq(raw_text, model_name)
                    if rewritten == "SKIP":
                        state["posted_hashes"].append(item_hash)
                        save_state(state)
                        continue

                    if not rewritten:
                        continue

                    # 3. Повторна перевірка згенерованого тексту проти історії
                    rewritten_entities = extract_key_entities(rewritten)
                    if is_duplicate_post(rewritten_entities, channel_posts, state["recent_entities"]):
                        state["posted_hashes"].append(item_hash)
                        save_state(state)
                        continue

                    # 4. Публікація в Telegram
                    has_media = item["has_video"] or item["has_photo"]
                    success = publish_native_post(
                        item["donor_channel"], 
                        item["msg_id"], 
                        rewritten, 
                        has_media, 
                        item["photos"] if not item["has_video"] else []
                    )

                    if success:
                        logger.info(f"Successfully published post from {item['donor_channel']}!")
                        state["posted_hashes"].append(item_hash)
                        state["recent_entities"].append({
                            "entities": item["entities"],
                            "time": time.time()
                        })
                        if len(state["posted_hashes"]) > 1000:
                            state["posted_hashes"] = state["posted_hashes"][-800:]
                        save_state(state)
                        channel_posts.append(rewritten)
                        time.sleep(15)

                time.sleep(1.5) # Пауза між донорами для захисту від блокувань

        except Exception as e:
            logger.error(f"Error in main loop: {e}", exc_info=True)

        time.sleep(60) # Швидка перевірка щохвилини для максимальної оперативності

if __name__ == "__main__":
    t_http = threading.Thread(target=run_http_server, daemon=True)
    t_http.start()

    t_ping = threading.Thread(target=run_keep_alive, daemon=True)
    t_ping.start()

    main_loop()
