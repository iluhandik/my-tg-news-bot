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

    words = re.findall(r'[а-яіїєґ0-9]{3,}', t)
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
                "roots": extract_canonical_roots(raw_text)
            })

    return messages

# --- ЗЧИТУВАННЯ ІСТОРІЇ КАНАЛУ (СВІЖІ ПОСТИ ЗАВЖДИ НА ПОЧАТКУ) ---
def get_channel_recent_posts(target_channel):
    posts = []
    clean_target = target_channel.lstrip('@')
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    current_url = f"https://t.me/s/{clean_target}"
    for _ in range(2):
        try:
            req = urllib.request.Request(current_url, headers=headers)
            with urllib.request.urlopen(req, timeout=8) as resp:
                content = resp.read().decode('utf-8', errors='ignore')

            blocks = re.findall(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', content, re.DOTALL)
            page_posts = []
            for b in blocks:
                clean = re.sub(r'<br\s*/?>', ' ', b)
                clean = re.sub(r'<[^>]+>', '', clean)
                clean = html.unescape(clean).strip()
                if clean:
                    page_posts.append(clean)

            # Додаємо сторінку
            posts.extend(page_posts)

            post_ids = re.findall(r'data-post=["\'][^/]+/(\d+)["\']', content)
            if post_ids:
                oldest_id = min(int(x) for x in post_ids)
                current_url = f"https://t.me/s/{clean_target}?before={oldest_id}"
            else:
                break
        except Exception as e:
            logger.warning(f"Error reading channel history: {e}")
            break

    # ГОЛОВНИЙ ФІКС: РОЗВЕРТАЄМО СПИСОК, ЩОБ НАЙСВІЖІШІ ПОСТИ БУЛИ НА ПОЧАТКУ!
    posts.reverse()
    return posts

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

# --- ШВИДКА ПЕРЕВІРКА НА ПОВТОР БЕЗ ОНОВЛЕНЬ ---
def is_blatant_duplicate(candidate_roots, raw_text, recent_posts):
    """
    Якщо збігаються 3 корені (наприклад 'рютте' + 'зеленськ' + 'швейцар')
    і в тексті немає маркерів нових фактів — це 100% повтор.
    """
    if len(candidate_roots) < 3:
        return False

    update_markers = [
        "повторн", "новий удар", "обвалив", "зруйновано повністю",
        "кількість зросла", "підтвердив генштаб", "з'явилися кадри",
        "з'явилося відео", "загинуло вже"
    ]
    has_update = any(m in raw_text.lower() for m in update_markers)
    if has_update:
        return False # Є оновлення, даємо шанс моделі обробити новий факт

    for p in recent_posts[:25]:
        p_roots = extract_canonical_roots(p)
        common = candidate_roots & p_roots
        if len(common) >= 3:
            logger.info(f"BLATANT DUPLICATE BLOCKED: common roots {common}")
            return True

    return False

# --- AI РЕДАКТУРА ТА РОЗУМНЕ ВІДСІЮВАННЯ ДУБЛІВ ---
def process_news_with_smart_editorial(text, model_name, recent_channel_posts):
    if not GROQ_API_KEY or not text or len(text.strip()) < 30:
        return None

    lower_raw = text.lower()

    # Відсікаємо некрологи
    memorial_words = [
        "хвилина мовчання", "пам'яті геро", "пам'яті загибл", "вічна пам'ять",
        "загинув у 2022", "загинув у 2023", "загинув 2022", "загинув 2023",
        "загинув 2024", "загинула в 2022", "загинула в 2023", "поховали на кладовищі"
    ]
    if any(m in lower_raw for m in memorial_words):
        logger.info("Skipping past years memorial.")
        return "SKIP"

    # Відсікаємо радарний нічний спам
    radar_words = [
        "летять на захід", "летять на схід", "курсом на", "вектор руху",
        "повітряна тривога в області", "загроза балістики", "ракета в напрямку",
        "шахеди повз", "бпла курсом", "чисто по областях", "відбій тривоги"
    ]
    if any(r in lower_raw for r in radar_words) and not any(k in lower_raw for k in ["збито", "відбито", "наслідки", "знищено"]):
        logger.info("Skipping transient flight radar alert.")
        return "SKIP"

    # ТЕПЕР ТУТ ТОЧНО ОСТАННІ 20 ОПУБЛІКОВАНИХ НОВИН
    recent_context_lines = []
    for i, p in enumerate(recent_channel_posts[:20], 1):
        snippet = p.replace('\n', ' ')[:140]
        recent_context_lines.append(f"{i}. {snippet}")
    context_str = "\n".join(recent_context_lines)

    system_prompt = (
        "ТИ — ДОСВІДЧЕНИЙ ГОЛОВНИЙ РЕДАКТОР УКРАЇНСЬКОГО ТЕЛЕГРАМ-КАНАЛУ «NEWS УКРАЇНА 365» (@kropivnit).\n\n"
        f"ОСЬ НАЙСВІЖІШІ НОВИНИ, ЯКІ ЩОЙНО ВИЙШЛИ В КАНАЛІ:\n{context_str}\n\n"
        "РОЗУМНИЙ РЕДАКТОРСЬКИЙ КОНТРОЛЬ ДУБЛІВ ТА АПДЕЙТІВ:\n"
        "1. ЯКЩО ЦЕ ДУБЛЬ: Якщо вхідна новина описує ТУ САМУ ПОДІЮ, яка вже є у списку вище (наприклад, слова Рютте про Швейцарію/гору/випити, міст на річці Міус, удар по тому ж мосту в Києві), "
        "і НЕ МІСТИТЬ принципово нового факту — ТИ ЗОБОВ'ЯЗАНИЙ ПОВЕРНУТИ ТІЛЬКИ ОДНЕ СЛОВО: SKIP.\n"
        "2. ЯКЩО ЦЕ НОВИЙ ФАКТ / РОЗВИТОК ПОДІЙ: Якщо стався ПОВТОРНИЙ УДАР, міст завалився, з'явилося перше відео прильоту — пиши новий пост, акцентуючи увагу на оновленні.\n"
        "3. ПРАВДА ПРО УДАРИ ПО РФ: Якщо ЗСУ уразили об'єкт у РФ — новина ТІЛЬКИ про успішний удар ЗСУ!\n"
        "4. КАТЕГОРИЧНО ЗАБОРОНЕНО писати примітки, міркування чи текст від першої особи.\n"
        "5. Чиста українська мова: «ДСНС» (не «ГСЧС»), «вирва» (не «воронка»), «величезна» (не «огромна»).\n\n"
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
            {"role": "user", "content": f"Вхідна новина для аналізу:\n\n{text}"}
        ],
        "temperature": 0.1,
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

# --- POSTING TO TELEGRAM ---
def publish_native_post(donor_channel, msg_id, text, has_media, photos):
    caption = text.strip()
    if len(caption) > 1020:
        caption = caption[:1015].rsplit(' ', 1)[0] + '...'

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

    # Захист від розмитих фото менше 30 Кб
    if photos:
        unique_photos = list(dict.fromkeys(photos))
        for p_url in unique_photos[:1]:
            try:
                headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://t.me/"}
                resp = requests.get(p_url, headers=headers, timeout=15)
                if resp.status_code == 200 and len(resp.content) >= 30000:
                    files = {"photo": ("image.jpg", resp.content, "image/jpeg")}
                    data = {"chat_id": TELEGRAM_CHANNEL_ID, "caption": caption, "parse_mode": "HTML"}
                    r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto", data=data, files=files, timeout=30)
                    if r.json().get("ok"):
                        logger.info(f"Published HIGH-RES photo ({len(resp.content)} bytes)!")
                        return True
                else:
                    logger.warning(f"Rejected blurry image ({len(resp.content)} bytes). Posting text.")
            except Exception as e:
                logger.warning(f"Fallback photo upload failed: {e}")

    logger.info("Publishing as text post...")
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHANNEL_ID, "text": text, "parse_mode": "HTML"}
    r = requests.post(url, json=payload, timeout=15)
    return r.json().get("ok", False)

# --- MAIN LOOP ---
def main_loop():
    logger.info("Initializing news bot with True Chronological Memory...")
    state = load_state()

    # Завантажуємо свіжу історію каналу (найновіші пости перші)
    channel_posts = get_channel_recent_posts(TELEGRAM_CHANNEL_ID)
    if not state.get("recent_published_posts"):
        state["recent_published_posts"] = channel_posts[:30]

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
            logger.info(f"Scanning {len(DONOR_CHANNELS)} channels using model: {model_name}...")

            if loop_count % 8 == 0:
                channel_posts = get_channel_recent_posts(TELEGRAM_CHANNEL_ID)
                # Синхронізуємо пам'ять
                for p in channel_posts[:15]:
                    if p not in state["recent_published_posts"]:
                        state["recent_published_posts"].insert(0, p)
                state["recent_published_posts"] = state["recent_published_posts"][:40]
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

                    # БАР'ЄР 1: Швидка перевірка на повтор без оновлень (блокує Рютте+Швейцарія)
                    if is_blatant_duplicate(item["roots"], raw_text, state["recent_published_posts"]):
                        logger.info(f"Skipping duplicate without updates: {list(item['roots'])[:3]}")
                        state["posted_hashes"].append(item_hash)
                        save_state(state)
                        continue

                    # БАР'ЄР 2: Модель бачить НАЙНОВІШІ пости прямо перед очима
                    rewritten = process_news_with_smart_editorial(raw_text, model_name, state["recent_published_posts"])
                    if rewritten == "SKIP":
                        state["posted_hashes"].append(item_hash)
                        save_state(state)
                        continue

                    if not rewritten:
                        continue

                    # Публікація в Telegram
                    has_media = item["has_video"] or item["has_photo"]
                    success = publish_native_post(
                        item["donor_channel"], 
                        item["msg_id"], 
                        rewritten, 
                        has_media, 
                        item["photos"]
                    )

                    if success:
                        headline = rewritten.split('\n')[0]
                        logger.info(f"Published: {headline}")
                        state["posted_hashes"].append(item_hash)
                        # МИТТЄВО ДОДАЄМО СВІЖИЙ ПОСТ НА ПЕРШУ ПОЗИЦІЮ ПАМ'ЯТІ
                        state["recent_published_posts"].insert(0, rewritten)
                        if len(state["recent_published_posts"]) > 40:
                            state["recent_published_posts"] = state["recent_published_posts"][:40]
                        if len(state["posted_hashes"]) > 2000:
                            state["posted_hashes"] = state["posted_hashes"][-1500:]
                        save_state(state)
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
