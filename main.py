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

DONOR_CHANNELS = [
    "insiderUKR",
    "truexanewsua",
    "u_now",
    "kievreal1",
    "nefedoov365",
    "lachentyt",
    "voynareal",
    "RBC_ua_news",
    "suspilnenews",
    "uniannet",
    "ukrpravda_news",
    "babel",
    "hromadske_ua",
    "nvua_official",
    "tsnua",
    "znua_live",
    "gruntmedia",
    "operativnoZSU",
    "Tsaplienko",
    "DeepStateUA",
    "kpszsu",
    "supernova_plus",
    "mysiagin"
]

STATE_FILE = "bot_state.json"

# --- HEALTH CHECK HTTP SERVER & KEEP-ALIVE ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_HEAD(self):
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

# --- DEDUPLICATION & STATE ---
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
        with urllib.request.urlopen(req, timeout=10) as response:
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

        has_video = ('tgme_widget_message_video_player' in block or '<video' in block)
        has_photo = ('tgme_widget_message_photo_wrap' in block)

        # Збираємо ТІЛЬКИ повнорозмірні фотографії (відкидаємо video_thumb та link_preview)
        photos = []
        if not has_video:
            photo_matches = re.findall(r'class="[^"]*tgme_widget_message_photo_wrap[^"]*"[^>]*style="[^"]*background-image:\s*url\((?:&quot;|\'|")?([^&\'"\)\s]+)', block)
            for u in photo_matches:
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

# --- ГЛИБОКЕ СКАНУВАННЯ ІСТОРІЇ КАНАЛУ (60-80 ПОСТІВ) ---
def get_channel_recent_posts(target_channel):
    posts = []
    clean_target = target_channel.lstrip('@')
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    # Скануємо 3 сторінки назад через параметр before
    current_url = f"https://t.me/s/{clean_target}"
    for _ in range(3):
        try:
            req = urllib.request.Request(current_url, headers=headers)
            with urllib.request.urlopen(req, timeout=8) as resp:
                content = resp.read().decode('utf-8', errors='ignore')

            blocks = re.findall(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', content, re.DOTALL)
            for b in blocks:
                clean = re.sub(r'<br\s*/?>', ' ', b)
                clean = re.sub(r'<[^>]+>', '', clean)
                clean = html.unescape(clean).strip()
                if clean and clean not in posts:
                    posts.append(clean)

            # Шукаємо найстаріший ID для пагінації назад
            post_ids = re.findall(r'data-post=["\'][^/]+/(\d+)["\']', content)
            if post_ids:
                oldest_id = min(int(x) for x in post_ids)
                current_url = f"https://t.me/s/{clean_target}?before={oldest_id}"
            else:
                break
        except Exception as e:
            logger.warning(f"Error reading channel history page: {e}")
            break

    logger.info(f"Loaded {len(posts)} historical posts from @{clean_target} for deep anti-duplicate check.")
    return posts

def is_duplicate_post(candidate_entities, channel_posts, recent_state_entities):
    if len(candidate_entities) < 2:
        return False
    cand_set = set(candidate_entities)

    # 1. Перевірка кешу стану
    for past_item in recent_state_entities:
        past_set = set(past_item.get("entities", []))
        common = cand_set & past_set
        if len(common) >= 2 and len(common) / len(cand_set) >= 0.4:
            logger.info(f"Duplicate blocked by state cache! Overlap: {common}")
            return True

    # 2. Глибока перевірка 60-80 постів каналу
    for post_text in channel_posts:
        p_entities = set(extract_key_entities(post_text))
        common = cand_set & p_entities
        # Якщо збігаються 2 важливих маркери (наприклад, 'кличко' + 'міст' або 'загиблих' + 'комишуваха')
        if len(common) >= 2 and len(common) / len(cand_set) >= 0.4:
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

    # 1. Відсікаємо некрологи минулих років
    memorial_words = [
        "хвилина мовчання", "пам'яті геро", "пам'яті загибл", "вічна пам'ять",
        "загинув у 2022", "загинув у 2023", "загинув 2022", "загинув 2023",
        "загинув 2024", "загинула в 2022", "загинула в 2023", "поховали на кладовищі"
    ]
    if any(m in lower_raw for m in memorial_words):
        logger.info("Skipping past years memorial/obituary.")
        return "SKIP"

    # 2. Відсікаємо радарний нічний спам
    radar_words = [
        "летять на захід", "летять на схід", "курсом на", "вектор руху",
        "повітряна тривога в області", "загроза балістики", "ракета в напрямку",
        "шахеди повз", "бпла курсом", "чисто по областях", "відбій тривоги"
    ]
    if any(r in lower_raw for r in radar_words) and not any(k in lower_raw for k in ["збито", "відбито", "наслідки", "знищено"]):
        logger.info("Skipping transient flight radar alert.")
        return "SKIP"

    # 3. Відсікаємо комуналку
    local_fluff_keywords = [
        "дтек ввів", "дтек запровадив", "екстрені відключення", "стабілізаційні відключення",
        "графіки відключень", "ремонт водопроводу", "ремонт мосту", "перекрили рух",
        "на південному мосту"
    ]
    if any(k in lower_raw for k in local_fluff_keywords) and "масований обстріл" not in lower_raw:
        logger.info("Skipping local municipal/utility fluff.")
        return "SKIP"

    system_prompt = (
        "ТИ — ГОЛОВНИЙ РЕДАКТОР УКРАЇНСЬКОГО ТЕЛЕГРАМ-КАНАЛУ «NEWS УКРАЇНА 365» (@kropivnit).\n\n"
        "СУВОРІ РЕДАКЦІЙНІ ПРАВИЛА:\n"
        "1. ПРАВДА ПРО УДАРИ ПО РФ: Якщо ЗСУ або дрони уразили ціль у РФ — новина ТІЛЬКИ про успішний удар ЗСУ! Заборонено цитувати версії ворога як факт.\n"
        "2. ТОЧНІСТЬ МІСЦЯ: Місто в заголовку зобов'язане збігатися з місцем безпосередньої події.\n"
        "3. НЕКРОЛОГИ МИНУЛИХ РОКІВ І РАДАРНИЙ СПАМ: Якщо новина — це стара згадка про загибель воїна у 2022-2024 рр. або хвилинне попередження про проліт дрона — пиши SKIP.\n"
        "4. КАТЕГОРИЧНО ЗАБОРОНЕНО писати коментарі, примітки чи від першої особи.\n"
        "5. Чиста українська: «ДСНС» (не «ГСЧС»), «вирва» (не «воронка»), «величезна» (не «огромна»).\n\n"
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
            return None
        raw_out = resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.error(f"Groq call exception: {e}")
        return None

    raw_out = re.sub(r'<think>.*?</think>', '', raw_out, flags=re.DOTALL).strip()

    if raw_out.upper() == "SKIP" or "SKIP" in raw_out[:10]:
        return "SKIP"

    out = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', raw_out)
    out = re.sub(r'__([\s\S]*?)__', r'<i>\1</i>', out)
    out = out.replace("<blockquote>", "").replace("</blockquote>", "")

    valid_endings = ('.', '!', '?', '»', '"', '</b>', '</i>', ')')
    if not out.rstrip().endswith(valid_endings):
        last_punct = max(out.rfind('.'), out.rfind('!'), out.rfind('?'), out.rfind('»'))
        if last_punct > 120:
            out = out[:last_punct+1].strip()
        else:
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

# --- POSTING TO TELEGRAM (ANTI-BLUR MEDIA GUARD) ---
def publish_native_post(donor_channel, msg_id, text, has_media, photos):
    caption = text.strip()
    if len(caption) > 1020:
        caption = caption[:1015].rsplit(' ', 1)[0] + '...'

    # 1. Спроба скопіювати нативно
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
        except Exception as e:
            logger.warning(f"copyMessage exception: {e}")

    # 2. Якщо це справжнє фото — перевіряємо його розмір на якість (Anti-Blur Guard)
    if photos:
        unique_photos = list(dict.fromkeys(photos))
        for p_url in unique_photos[:1]:
            try:
                headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://t.me/"}
                resp = requests.get(p_url, headers=headers, timeout=15)
                # ЖОРСТКИЙ ФІЛЬТР: якщо картинка важить менше 30 Кб — це розмите мікро-прев'ю, відкидаємо!
                if resp.status_code == 200 and len(resp.content) >= 30000:
                    files = {"photo": ("image.jpg", resp.content, "image/jpeg")}
                    data = {"chat_id": TELEGRAM_CHANNEL_ID, "caption": caption, "parse_mode": "HTML"}
                    r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto", data=data, files=files, timeout=30)
                    if r.json().get("ok"):
                        logger.info(f"Published HIGH-RES photo ({len(resp.content)} bytes)!")
                        return True
                else:
                    logger.warning(f"Rejected low-res/blurry image ({len(resp.content)} bytes). Will post clean text.")
            except Exception as e:
                logger.warning(f"Fallback photo upload failed: {e}")

    # 3. Чистий текстовий пост (без розмитого піксельного мотлоху)
    logger.info("Publishing as clean text post...")
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHANNEL_ID, "text": text, "parse_mode": "HTML"}
    r = requests.post(url, json=payload, timeout=15)
    return r.json().get("ok", False)

# --- MAIN AUTOMATION LOOP ---
def main_loop():
    logger.info("Initializing news bot with Deep Memory & Anti-Blur Guard...")
    state = load_state()

    # Завантажуємо глибоку історію каналу (60-80 постів) одразу на старті
    channel_posts = get_channel_recent_posts(TELEGRAM_CHANNEL_ID)

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

    loop_count = 0
    while True:
        try:
            model_name = get_working_groq_model()
            logger.info(f"Scanning {len(DONOR_CHANNELS)} channels using model: {model_name}...")

            # Очищуємо застарілі сутності з кешу (старші 48 годин)
            cutoff_time = time.time() - (48 * 3600)
            state["recent_entities"] = [
                e for e in state.get("recent_entities", []) if e.get("time", 0) > cutoff_time
            ]

            # Раз на 10 циклів оновлюємо глибоку історію каналу
            if loop_count % 10 == 0:
                channel_posts = get_channel_recent_posts(TELEGRAM_CHANNEL_ID)
            loop_count += 1

            for channel in DONOR_CHANNELS:
                items = scrape_channel(channel)
                for item in items[-2:]:
                    item_hash = item["hash"]
                    if not item_hash or item_hash in state["posted_hashes"]:
                        continue

                    raw_text = item["text"]
                    if not raw_text or len(raw_text) < 30:
                        continue

                    # 1. Глибока перевірка на дублі проти 80 попередніх постів
                    if is_duplicate_post(item["entities"], channel_posts, state["recent_entities"]):
                        logger.info(f"Skipping duplicate: {item['entities'][:3]}")
                        state["posted_hashes"].append(item_hash)
                        save_state(state)
                        continue

                    # 2. Редакторська обробка AI
                    rewritten = rewrite_news_with_groq(raw_text, model_name)
                    if rewritten == "SKIP":
                        state["posted_hashes"].append(item_hash)
                        save_state(state)
                        continue

                    if not rewritten:
                        continue

                    # 3. Перевірка згенерованого тексту проти глибокої історії
                    rewritten_entities = extract_key_entities(rewritten)
                    if is_duplicate_post(rewritten_entities, channel_posts, state["recent_entities"]):
                        logger.info("Skipping rewritten duplicate.")
                        state["posted_hashes"].append(item_hash)
                        save_state(state)
                        continue

                    # 4. Публікація з фільтрацією якості зображень
                    has_media = item["has_video"] or item["has_photo"]
                    success = publish_native_post(
                        item["donor_channel"], 
                        item["msg_id"], 
                        rewritten, 
                        has_media, 
                        item["photos"]
                    )

                    if success:
                        logger.info(f"Published verified news from {item['donor_channel']}!")
                        state["posted_hashes"].append(item_hash)
                        state["recent_entities"].append({
                            "entities": item["entities"],
                            "time": time.time()
                        })
                        if len(state["posted_hashes"]) > 2000:
                            state["posted_hashes"] = state["posted_hashes"][-1500:]
                        save_state(state)
                        channel_posts.insert(0, rewritten)
                        time.sleep(15)

                time.sleep(0.6)

        except Exception as e:
            logger.error(f"Error in main loop: {e}", exc_info=True)

        time.sleep(45)

if __name__ == "__main__":
    t_http = threading.Thread(target=run_http_server, daemon=True)
    t_http.start()

    t_ping = threading.Thread(target=run_keep_alive, daemon=True)
    t_ping.start()

    main_loop()
