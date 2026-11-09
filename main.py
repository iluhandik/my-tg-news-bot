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

# --- STATE MANAGEMENT ---
def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"posted_hashes": [], "recent_published_posts": []}

def save_state(state):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Failed to save state: {e}")

def get_hash(text):
    clean = re.sub(r'\W+', '', text.lower())
    return hashlib.md5(clean.encode('utf-8')).hexdigest()

def extract_canonical_roots(text):
    t = text.lower()
    t = t.replace('ы', 'и').replace('э', 'е').replace('ё', 'е').replace('ъ', '').replace('ь', '')
    t = t.replace('и', 'і')

    words = re.findall(r'[а-яіїєґa-z0-9]{3,}', t)
    stop_roots = {
        'який', 'яка', 'яке', 'які', 'його', 'цього', 'було', 'буде', 'коли', 
        'також', 'після', 'через', 'про', 'для', 'новини', 'канал', 'підписатися', 
        'фото', 'відео', 'повідомляє', 'заявив', 'каже', 'стало', 'відомо', 'повідомили', 
        'словами', 'офіційно', 'року', 'сьогодні', 'зараз', 'украї', 'росі', 'облас', 
        'район', 'місті', 'внасл', 'людей', 'траси', 'нового', 'перший', 'повідо'
    }

    roots = set()
    for w in words:
        root = w[:5] if len(w) >= 5 else w
        if root in stop_roots:
            continue
        if root.startswith('міст'):
            root = 'мост' + root[4:]
        roots.add(root)
    return roots

# --- ШВИДКИЙ СКРАПЕР ДОНОРІВ ---
def scrape_channel(channel_name):
    url = f"https://t.me/s/{channel_name.lstrip('@')}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    }
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=5) as response:
            html_content = response.read().decode('utf-8', errors='ignore')
    except Exception as e:
        logger.warning(f"Fetch {channel_name} skipped: {e}")
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

        has_media = ('tgme_widget_message_video_player' in block or '<video' in block or 'tgme_widget_message_photo_wrap' in block)

        if raw_text or has_media:
            messages.append({
                "donor_channel": donor_username,
                "msg_id": msg_id,
                "text": raw_text,
                "has_media": has_media,
                "hash": get_hash(raw_text) if raw_text else None,
                "roots": extract_canonical_roots(raw_text)
            })

    return messages

# --- ЗЧИТУВАННЯ МЕДІА (MP4 ТА HD ФОТО) ---
def fetch_media_from_embed(donor_channel, msg_id):
    clean_channel = donor_channel.lstrip('@')
    embed_url = f"https://t.me/{clean_channel}/{msg_id}?embed=1"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Referer": f"https://t.me/s/{clean_channel}"
    }
    video_url = None
    photo_urls = []
    try:
        req = urllib.request.Request(embed_url, headers=headers)
        with urllib.request.urlopen(req, timeout=6) as resp:
            html_text = resp.read().decode('utf-8', errors='ignore')

        v_match = re.search(r'<video[^>]+src=["\']([^"\']+)["\']', html_text)
        if v_match:
            video_url = v_match.group(1).replace('&amp;', '&')

        p_matches = re.findall(r'class="[^"]*tgme_widget_message_photo_wrap[^"]*"[^>]*style="[^"]*background-image:\s*url\((?:&quot;|\'|")?([^&\'"\)\s]+)', html_text)
        for p in p_matches:
            p = p.replace('&amp;', '&').strip()
            if any(skip in p.lower() for skip in ['user_photo', 'avatar', 'emoji', 'stickers']):
                continue
            if p not in photo_urls:
                photo_urls.append(p)

    except Exception as e:
        logger.warning(f"Error fetching embed media from {embed_url}: {e}")

    return video_url, photo_urls

# --- ЗЧИТУВАННЯ ІСТОРІЇ КАНАЛУ ---
def get_channel_recent_posts(target_channel):
    posts = []
    clean_target = target_channel.lstrip('@')
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    current_url = f"https://t.me/s/{clean_target}"
    for _ in range(2):
        try:
            req = urllib.request.Request(current_url, headers=headers)
            with urllib.request.urlopen(req, timeout=6) as resp:
                content = resp.read().decode('utf-8', errors='ignore')

            blocks = re.findall(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', content, re.DOTALL)
            page_posts = []
            for b in blocks:
                clean = re.sub(r'<br\s*/?>', ' ', b)
                clean = re.sub(r'<[^>]+>', '', clean)
                clean = html.unescape(clean).strip()
                if clean:
                    page_posts.append(clean)

            posts.extend(page_posts)

            post_ids = re.findall(r'data-post=["\'][^/]+/(\d+)["\']', content)
            if post_ids:
                oldest_id = min(int(x) for x in post_ids)
                current_url = f"https://t.me/s/{clean_target}?before={oldest_id}"
            else:
                break
        except Exception as e:
            break

    posts.reverse()
    return posts

# --- DYNAMIC GROQ MODEL DISCOVERY ---
def get_working_groq_model():
    if not GROQ_API_KEY:
        return "llama-3.1-8b-instant"
    url = "https://api.groq.com/openai/v1/models"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}"}
    try:
        r = requests.get(url, headers=headers, timeout=5)
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
        pass
    return "llama-3.1-8b-instant"

# --- ШВИДКИЙ PYTHON-АНТИДУБЛЬ ---
def is_blatant_duplicate(candidate_roots, raw_text, recent_posts):
    if len(candidate_roots) < 2:
        return False

    update_markers = [
        "повторн", "новий удар", "обвалив", "зруйновано повністю",
        "кількість зросла", "підтвердив генштаб", "з'явилися кадри",
        "з'явилося відео", "загинуло вже"
    ]
    if any(m in raw_text.lower() for m in update_markers):
        return False

    for p in recent_posts[:30]:
        p_roots = extract_canonical_roots(p)
        common = candidate_roots & p_roots
        if len(common) >= 2 and len(common) / len(candidate_roots) >= 0.25:
            logger.info(f"Duplicate blocked instantly in Python: {common}")
            return True

    return False

# --- ЛЕГКИЙ AI РЕДАКТОР З ФІЛЬТРОМ ЧУЖИХ ДАЙДЖЕСТІВ ---
def process_news_with_smart_editorial(text, model_name, recent_channel_posts):
    if not GROQ_API_KEY or not text or len(text.strip()) < 30:
        return None

    lower_raw = text.lower()

    # 1. ТОТАЛЬНИЙ БАН ЧУЖИХ ПІДСУМКІВ ТА ДАЙДЖЕСТІВ ДНЯ
    digest_markers = [
        "важливе за день", "головне за день", "підсумки дня", "підсумок дня",
        "головне за сьогодні", "топ новин", "головні новини дня", "головне за ніч",
        "дайджест", "вечірній дайджест", "ранковий дайджест", "що відбулося за день",
        "головні події дня", "картина дня", "хроніка дня", "найважливіше за день",
        "коротко про головне", "головне на цей час", "головне станом на"
    ]
    if any(d in lower_raw for d in digest_markers):
        logger.info("Skipping donor daily digest/summary banner.")
        return "SKIP"

    # 2. Бан радарного спаму (напрямки дронів над селами)
    flight_radar_words = [
        "рухаються у", "рухаються в", "курс на", "курсом на", "фіксують у", "фіксуються в",
        "напрямок руху", "напрямку чорнобиля", "напрямку києва", "напрямку білої",
        "повз миронівку", "повз ", "вектор руху", "бпла над", "шахеди над",
        "ракета через", "ракета в бік", "загроза для", "повітряна тривога в",
        "підвищена небезпека", "зафіксовано рух", "наближаються до", "група бпла",
        "шахеди курсом", "бпла повз", "чисто по областях", "відбій тривоги"
    ]
    has_real_impact = any(k in lower_raw for k in ["збито", "відбито", "влучання", "руйнування", "пожежа", "загинули", "постраждали"])
    if any(r in lower_raw for r in flight_radar_words) and not has_real_impact:
        logger.info("Skipping flight radar alert.")
        return "SKIP"

    # 3. Бан некрологів минулих років
    memorial_words = [
        "хвилина мовчання", "пам'яті геро", "пам'яті загибл", "вічна пам'ять",
        "загинув у 2022", "загинув у 2023", "загинув 2022", "загинув 2023",
        "загинув 2024", "загинула в 2022", "загинула в 2023", "поховали на кладовищі"
    ]
    if any(m in lower_raw for m in memorial_words):
        logger.info("Skipping past years memorial.")
        return "SKIP"

    # Однорядкові заголовки останніх 15 новин (~120 токенів)
    recent_headlines = []
    for p in recent_channel_posts[:20]:
        first_line = p.strip().split('\n')[0][:90]
        if first_line and first_line not in recent_headlines:
            recent_headlines.append(first_line)
    context_str = "\n".join([f"{i}. {h}" for i, h in enumerate(recent_headlines[:15], 1)])

    system_prompt = (
        "ТИ — ГОЛОВНИЙ РЕДАКТОР УКРАЇНСЬКОГО ТЕЛЕГРАМ-КАНАЛУ «NEWS УКРАЇНА 365» (@kropivnit).\n\n"
        f"ОСТАННІ ОПУБЛІКОВАНІ ТЕМИ В КАНАЛІ:\n{context_str}\n\n"
        "СУВОРІ РЕДАКЦІЙНІ ПРАВИЛА:\n"
        "1. ЗАБОРОНА ЧУЖИХ ДАЙДЖЕСТІВ ТА ПІДСУМКІВ: Категорично заборонено брати щоденні добірки та підсумки чужих каналів («Важливе за день», «Головне за день», «Підсумки дня», «Дайджест»). "
        "Якщо в дописі зібрано кілька різних тем докупи або це брендований підсумок чужого медіа — ПОВЕРНИ ТІЛЬКИ СЛОВО: SKIP.\n"
        "2. АНТИДУБЛЬ: Якщо новина описує ТУ САМУ ПОДІЮ, яка вже є у списку вище (без нових фактів) — пиши SKIP.\n"
        "3. БЕЗ ПАНІКИ: Якщо новина — це подробиці старої трагедії чи теракту (як у Кракові), заборонено писати заголовок «Сталася стрілянина/теракт»! Заголовок має казати: «Стрілянина у Кракові: нові подробиці розслідування». Якщо нових деталей нема — пиши SKIP.\n"
        "4. ГЕОГРАФІЯ: Столиця Польщі — Варшава (НЕ Краків!).\n"
        "5. СТАТУС ТРАМПА: Дональд Трамп є Президентом США (НЕ «екс-президент»!).\n"
        "6. ЧИСТА МОВА: «В інтерв'ю» (НЕ «У інтерв'ю»), «стікав кров'ю» (НЕ «истекав кров'ю»), «ДСНС» (НЕ «ГСЧС»), «вирва» (НЕ «воронка»).\n\n"
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
            {"role": "user", "content": f"Вхідна новина:\n\n{text}"}
        ],
        "temperature": 0.1,
        "max_tokens": 800
    }

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=15)
        if resp.status_code != 200:
            logger.warning(f"Groq API {resp.status_code}: {resp.text[:100]}")
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
        r'\bекс-президент(а|у|ом|і)?\s+США\s+(Дональд\s+)?Трамп(а|у|ом|і)?\b': r'Президент\1 США \2Трамп\3',
        r'\bекс-президент(а|у|ом|і)?\s+США\b': r'Президент\1 США',
        r'\bекс-президент(а|у|ом|і)?\s+(Дональд\s+)?Трамп(а|у|ом|і)?\b': r'Президент\1 \2Трамп\3',
        r'\bколишн(ій|ього|ьому|ім)\s+президент(а|у|ом|і)?\s+США\b': r'Президент\2 США',
        r'\bУ\s+інтерв[\'’]ю\b': 'В інтерв\'ю',
        r'\bу\s+інтерв[\'’]ю\b': 'в інтерв\'ю',
        r'\b[иі]стека[а-я]* кров[\'’]ю\b': 'стікав кров\'ю',
        r'\b[иі]стека[а-я]*\b': 'стікав',
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

# --- ДОДАВАННЯ ПІДПИСУ КАНАЛУ ---
def append_channel_signature(text):
    clean_text = text.strip()
    channel_user = TELEGRAM_CHANNEL_ID.lstrip('@')
    signature = f'\n\n👉 <a href="https://t.me/{channel_user}">NEWS УКРАЇНА 365 | Підписатися</a>'
    
    max_text_len = 1015 - len(signature)
    if len(clean_text) > max_text_len:
        clean_text = clean_text[:max_text_len].rsplit(' ', 1)[0] + '...'
        
    return clean_text + signature

# --- ПУБЛІКАЦІЯ В TELEGRAM ---
def publish_post_with_real_media(donor_channel, msg_id, text):
    caption = append_channel_signature(text)

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Referer": "https://t.me/"
    }

    video_url, photo_urls = fetch_media_from_embed(donor_channel, msg_id)

    # 1. Відео MP4
    if video_url:
        try:
            logger.info(f"Downloading MP4 from {video_url[:60]}...")
            resp = requests.get(video_url, headers=headers, timeout=25)
            if resp.status_code == 200 and len(resp.content) > 5000:
                if len(resp.content) <= 50 * 1024 * 1024:
                    files = {"video": ("video.mp4", resp.content, "video/mp4")}
                    data = {"chat_id": TELEGRAM_CHANNEL_ID, "caption": caption, "parse_mode": "HTML"}
                    r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendVideo", data=data, files=files, timeout=45)
                    if r.json().get("ok"):
                        logger.info("Published post with REAL VIDEO!")
                        return True
        except Exception as e:
            logger.warning(f"Failed to upload video: {e}")

    # 2. Фото
    if photo_urls:
        for p_url in photo_urls[:1]:
            try:
                resp = requests.get(p_url, headers=headers, timeout=12)
                if resp.status_code == 200 and len(resp.content) >= 20000:
                    files = {"photo": ("image.jpg", resp.content, "image/jpeg")}
                    data = {"chat_id": TELEGRAM_CHANNEL_ID, "caption": caption, "parse_mode": "HTML"}
                    r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto", data=data, files=files, timeout=25)
                    if r.json().get("ok"):
                        logger.info("Published post with REAL PHOTO!")
                        return True
            except Exception as e:
                logger.warning(f"Failed to upload photo: {e}")

    # 3. Текст
    logger.info("Publishing as text post...")
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHANNEL_ID, 
        "text": caption, 
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    r = requests.post(url, json=payload, timeout=12)
    return r.json().get("ok", False)

# --- ОСНОВНИЙ ЦИКЛ ОНОВЛЕННЯ ---
def main_loop():
    logger.info("Initializing news bot with Foreign Digest Filter...")
    state = load_state()

    channel_posts = get_channel_recent_posts(TELEGRAM_CHANNEL_ID)
    if not state.get("recent_published_posts"):
        state["recent_published_posts"] = channel_posts[:60]

    if not state.get("posted_hashes"):
        logger.info("Cold start: seeding existing messages into cache...")
        for ch in DONOR_CHANNELS:
            msgs = scrape_channel(ch)
            for m in msgs:
                if m["hash"]:
                    state["posted_hashes"].append(m["hash"])
        save_state(state)

    loop_count = 0
    while True:
        try:
            model_name = get_working_groq_model()
            logger.info(f"Fast-scanning {len(DONOR_CHANNELS)} channels ({model_name})...")

            if loop_count % 12 == 0:
                channel_posts = get_channel_recent_posts(TELEGRAM_CHANNEL_ID)
                for p in channel_posts[:20]:
                    if p not in state["recent_published_posts"]:
                        state["recent_published_posts"].insert(0, p)
                state["recent_published_posts"] = state["recent_published_posts"][:100]
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

                    # 1. Швидкий Python-фільтр
                    if is_blatant_duplicate(item["roots"], raw_text, state["recent_published_posts"]):
                        state["posted_hashes"].append(item_hash)
                        save_state(state)
                        continue

                    # 2. Легкий AI-рерайтер із фільтрацією чужих дайджестів
                    rewritten = process_news_with_smart_editorial(raw_text, model_name, state["recent_published_posts"])
                    if rewritten == "SKIP":
                        state["posted_hashes"].append(item_hash)
                        save_state(state)
                        continue

                    if not rewritten:
                        continue

                    # 3. Публікація в Telegram
                    success = publish_post_with_real_media(
                        item["donor_channel"], 
                        item["msg_id"], 
                        rewritten
                    )

                    if success:
                        headline = rewritten.split('\n')[0]
                        logger.info(f"Published in real-time: {headline}")
                        state["posted_hashes"].append(item_hash)
                        state["recent_published_posts"].insert(0, rewritten)
                        if len(state["recent_published_posts"]) > 100:
                            state["recent_published_posts"] = state["recent_published_posts"][:100]
                        if len(state["posted_hashes"]) > 2500:
                            state["posted_hashes"] = state["posted_hashes"][-2000:]
                        save_state(state)
                        time.sleep(10)

                time.sleep(0.3)

        except Exception as e:
            logger.error(f"Error in main loop: {e}", exc_info=True)

        time.sleep(20)

if __name__ == "__main__":
    t_http = threading.Thread(target=run_http_server, daemon=True)
    t_http.start()

    t_ping = threading.Thread(target=run_keep_alive, daemon=True)
    t_ping.start()

    main_loop()
