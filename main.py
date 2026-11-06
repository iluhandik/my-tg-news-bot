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
    "nefedoov365"
]

STATE_FILE = "bot_state.json"

# --- HEALTH CHECK HTTP SERVER & KEEP-ALIVE ---
class HealthCheckHandler(BaseHTTPRequestHandler):
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
            time.sleep(480) # 8 minutes
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
    return {"posted_hashes": [], "recent_titles": []}

def save_state(state):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Failed to save state: {e}")

def get_hash(text):
    clean = re.sub(r'\W+', '', text.lower())
    return hashlib.md5(clean.encode('utf-8')).hexdigest()

def extract_keywords(text):
    clean = re.sub(r'<[^>]+>', ' ', text)
    words = re.findall(r'[а-яіїєґa-z0-9]{4,}', clean.lower())
    stop_words = {
        'який', 'яка', 'яке', 'які', 'його', 'її', 'цього', 'було', 'буде', 'коли', 
        'також', 'після', 'через', 'про', 'для', 'новини', 'канал', 'підписатися', 
        'фото', 'відео', 'повідомляє', 'заявив', 'каже', 'стало', 'відомо', 'повідомили', 
        'словами', 'офіційно', 'року', 'сьогодні', 'зараз'
    }
    return set(w for w in words if w not in stop_words)

# --- SCRAPER FOR TELEGRAM CHANNELS ---
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
    blocks = re.findall(r'(<div class="tgme_widget_message_wrap\b.*?)(?=<div class="tgme_widget_message_wrap\b|$)', html_content, re.DOTALL)
    
    for block in blocks:
        post_match = re.search(r'data-post=["\']([^"\']+)["\']', block)
        if not post_match:
            continue
        data_post = post_match.group(1) # e.g. "insiderUKR/12345"
        parts = data_post.split('/')
        donor_user = "@" + parts[0]
        try:
            msg_id = int(parts[1])
        except Exception:
            msg_id = None

        # Check timestamp
        time_match = re.search(r'<time[^>]+datetime=["\']([^"\']+)["\']', block)
        
        # Extract text
        text_match = re.search(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', block, re.DOTALL)
        raw_text = ""
        if text_match:
            raw_text = text_match.group(1)
            raw_text = re.sub(r'<br\s*/?>', '\n', raw_text)
            raw_text = re.sub(r'<a\b[^>]*>.*?</a>', '', raw_text)
            raw_text = re.sub(r'<[^>]+>', '', raw_text)
            raw_text = html.unescape(raw_text).strip()

        # Check media
        has_video = ('tgme_widget_message_video_player' in block or '<video' in block or 'tgme_widget_message_video_thumb' in block)
        
        # Direct images
        photo_matches = re.findall(r'tgme_widget_message_photo_wrap[^>]+url\((?:&quot;|\'|\")?([^&\'\"\)]+)', block)
        media_urls = []
        for p in photo_matches:
            if 'user_photo' not in p and p not in media_urls:
                media_urls.append(p)

        # Direct video tag if available
        v_matches = re.findall(r'<video[^>]+src=["\']([^"\']+)["\']', block)
        for v in v_matches:
            if v not in media_urls:
                media_urls.append(v)

        if raw_text or media_urls or has_video:
            messages.append({
                "donor_user": donor_user,
                "msg_id": msg_id,
                "text": raw_text,
                "media": media_urls,
                "has_video": has_video,
                "hash": get_hash(raw_text) if raw_text else None
            })

    return messages

# --- CHECK TARGET CHANNEL FOR MANUAL / PRIOR POSTS ---
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
        for b in blocks[-25:]:
            clean = re.sub(r'<br\s*/?>', ' ', b)
            clean = re.sub(r'<[^>]+>', '', clean)
            clean = html.unescape(clean).strip()
            if clean:
                posts.append(clean)
    except Exception as e:
        logger.warning(f"Could not read recent posts from {target_channel}: {e}")
    return posts

def is_already_on_channel(candidate_text, channel_posts):
    cand_kw = extract_keywords(candidate_text)
    if len(cand_kw) < 2:
        return False
    for p_text in channel_posts:
        p_kw = extract_keywords(p_text)
        common = cand_kw & p_kw
        # If 3 or more significant keywords match or 50%+ overlap
        if len(common) >= 3 or (len(common) >= 2 and len(common) / len(cand_kw) >= 0.5):
            logger.info(f"Duplicate detected in channel! Common keywords: {common}")
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

# --- AI REWRITE & VALIDATION ---
def rewrite_news_with_groq(text, model_name):
    if not GROQ_API_KEY or not text or len(text.strip()) < 30:
        return None

    system_prompt = (
        "ТИ — РОБОТ-РЕДАКТОР УКРАЇНСЬКОГО ТЕЛЕГРАМ-КАНАЛУ «NEWS УКРАЇНА 365» (@kropivnit).\n\n"
        "СУВОРІ ПРАВИЛА:\n"
        "1. КАТЕГОРИЧНО ЗАБОРОНЕНО писати БУДЬ-ЯКІ примітки, роздуми чи коментарі вголос! "
        "Ніяких слів від першої особи: «я», «ми», «примітка:», «у контексті», «перевіряю», «виправлю», «давайте припустимо».\n"
        "2. Якщо у вхідному тексті є нестиковка або протиріччя в іменах/посадах — пиши тільки сухий очевидний факт або поверни слово SKIP.\n"
        "3. Відкидай рекламу, розіграші, казино, крипту, джинсу політиків та дрібні міські ремонти/мости. Для них пиши SKIP.\n"
        "4. Мова — виключно чиста, грамотна українська (жодних русизмів: «натовп» замість «толпа», «ЗСУ» замість «ВСУ»).\n"
        "5. ФОРМАТ ПОСТА:\n"
        "⚡ <b>Короткий влучний заголовок</b>\n\n"
        "1-2 фактичні абзаци по суті. Ключові деталі виділяй <b>жирним</b>.\n"
        "Цитати оформлюй як: «Текст прямої мови», — <b>Прізвище</b>.\n\n"
        "ВІДПОВІДЬ МАЄ МІСТИТИ ТІЛЬКИ ТЕКСТ ПОСТА АБО ОДНЕ СЛОВО SKIP."
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
            {"role": "user", "content": f"Ось новина-донор:\n\n{text}"}
        ],
        "temperature": 0.2,
        "max_tokens": 800
    }

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=25)
        if resp.status_code != 200:
            logger.error(f"Groq API returned error {resp.status_code}: {resp.text}")
            return None
        res_json = resp.json()
        raw_out = res_json["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.error(f"Groq call exception: {e}")
        return None

    # Strip thinking tags if present
    raw_out = re.sub(r'<think>.*?</think>', '', raw_out, flags=re.DOTALL).strip()

    if raw_out.upper() == "SKIP" or "SKIP" in raw_out[:10]:
        return "SKIP"

    # Sanitize markdown formatting into HTML
    out = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', raw_out)
    out = re.sub(r'__([\s\S]*?)__', r'<i>\1</i>', out)
    out = out.replace("<blockquote>", "").replace("</blockquote>", "")

    # Strict check: ban internal reasoning / reflections leakage
    forbidden_meta = [
        r'\(у контексті',
        r'\*?примітка:?',
        r'я виправлю',
        r'я не можу',
        r'давайте припустимо',
        r'перевіряю',
        r'за інструкцією',
        r'вхідний текст',
        r'фактична хибність',
        r'суперечність',
        r'дотримаємось',
        r'якщо я напишу'
    ]
    for pattern in forbidden_meta:
        if re.search(pattern, out, re.IGNORECASE):
            logger.warning(f"AI reasoning leak detected ({pattern})! Dropping post.")
            return None

    # Check for abrupt cutoff at the end
    valid_endings = ('.', '!', '?', '»', '"', '</b>', '</i>', ')')
    if not out.rstrip().endswith(valid_endings):
        last_punct = max(out.rfind('.'), out.rfind('!'), out.rfind('?'), out.rfind('»'))
        if last_punct > 120:
            out = out[:last_punct+1].strip()
        else:
            logger.warning("Post was cut off mid-sentence, dropping.")
            return None

    # Fix common Russian words
    ru_fixes = {
        r'\bтолп[а-я]*': 'натовп',
        r'\bпозор[а-я]*': 'ганьба',
        r'\bу даний момент\b': 'наразі',
        r'\bВСУ\b': 'ЗСУ',
        r'\bв даний час\b': 'наразі'
    }
    for r_pattern, replacement in ru_fixes.items():
        out = re.sub(r_pattern, replacement, out, flags=re.IGNORECASE)

    return out

# --- POSTING TO TELEGRAM ---
def try_copy_message(from_channel, message_id, caption):
    """Copies native video/photo directly via Telegram API, preserving 100% video quality."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/copyMessage"
    payload = {
        "chat_id": TELEGRAM_CHANNEL_ID,
        "from_chat_id": from_channel,
        "message_id": message_id,
        "caption": caption,
        "parse_mode": "HTML"
    }
    try:
        r = requests.post(url, json=payload, timeout=20)
        res = r.json()
        if res.get("ok"):
            logger.info(f"Successfully copied native media from {from_channel}/{message_id}")
            return True
        logger.warning(f"copyMessage failed: {res.get('description')}")
    except Exception as e:
        logger.warning(f"copyMessage exception: {e}")
    return False

def send_fallback_media(text, media_urls):
    """Fallback if copyMessage cannot be used."""
    if not media_urls:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHANNEL_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": False
        }
        r = requests.post(url, json=payload, timeout=20)
        return r.json().get("ok", False)

    first_media = media_urls[0]
    if first_media.endswith('.mp4'):
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendVideo"
        payload = {"chat_id": TELEGRAM_CHANNEL_ID, "video": first_media, "caption": text, "parse_mode": "HTML"}
    else:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
        payload = {"chat_id": TELEGRAM_CHANNEL_ID, "photo": first_media, "caption": text, "parse_mode": "HTML"}
    
    try:
        r = requests.post(url, json=payload, timeout=25)
        res = r.json()
        if res.get("ok"):
            return True
        logger.warning(f"Direct media send failed: {res.get('description')}")
    except Exception as e:
        logger.warning(f"send_fallback_media exception: {e}")
    
    # Ultimate text fallback
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHANNEL_ID, "text": text, "parse_mode": "HTML"}
    r = requests.post(url, json=payload, timeout=15)
    return r.json().get("ok", False)

# --- MAIN AUTOMATION LOOP ---
def main_loop():
    logger.info("Initializing news bot...")
    state = load_state()

    # Cold start: seed existing donor messages so old posts aren't blasted
    if not state.get("posted_hashes"):
        logger.info("Cold start: seeding existing messages into seen cache...")
        for ch in DONOR_CHANNELS:
            msgs = scrape_channel(ch)
            for m in msgs:
                if m["hash"]:
                    state["posted_hashes"].append(m["hash"])
        save_state(state)

    while True:
        try:
            model_name = get_working_groq_model()
            logger.info(f"Checking donor channels using model: {model_name}...")

            # Get recent posts from target channel to prevent duplicating manual admin posts
            channel_posts = get_channel_recent_posts(TELEGRAM_CHANNEL_ID)

            for channel in DONOR_CHANNELS:
                items = scrape_channel(channel)
                # Only check the latest 4 messages
                for item in items[-4:]:
                    item_hash = item["hash"]
                    if not item_hash or item_hash in state["posted_hashes"]:
                        continue

                    raw_text = item["text"]
                    if not raw_text or len(raw_text) < 30:
                        continue

                    # 1. Check if already posted on our channel manually
                    if is_already_on_channel(raw_text, channel_posts):
                        logger.info("Skipping news: already published on channel.")
                        state["posted_hashes"].append(item_hash)
                        save_state(state)
                        continue

                    # 2. Rewrite with AI
                    rewritten = rewrite_news_with_groq(raw_text, model_name)
                    if rewritten == "SKIP":
                        state["posted_hashes"].append(item_hash)
                        save_state(state)
                        continue

                    if not rewritten:
                        continue # Retry later

                    # Double check rewritten text against channel
                    if is_already_on_channel(rewritten, channel_posts):
                        logger.info("Skipping rewritten news: already present on channel.")
                        state["posted_hashes"].append(item_hash)
                        save_state(state)
                        continue

                    # 3. Publish to Telegram
                    success = False
                    # If donor message has media (video/photo) and valid ID, copy natively
                    if (item["has_video"] or item["media"]) and item["msg_id"]:
                        success = try_copy_message(item["donor_user"], item["msg_id"], rewritten)

                    # If not copied or text-only, use fallback
                    if not success:
                        success = send_fallback_media(rewritten, item["media"] if not item["has_video"] else [])

                    if success:
                        logger.info("Successfully published new post!")
                        state["posted_hashes"].append(item_hash)
                        # Keep list manageable
                        if len(state["posted_hashes"]) > 1000:
                            state["posted_hashes"] = state["posted_hashes"][-800:]
                        save_state(state)
                        channel_posts.append(rewritten)
                        time.sleep(15) # Spacing between posts

                time.sleep(3)

        except Exception as e:
            logger.error(f"Error in main loop: {e}", exc_info=True)

        time.sleep(90) # Check every 1.5 minutes

if __name__ == "__main__":
    t_http = threading.Thread(target=run_http_server, daemon=True)
    t_http.start()

    t_ping = threading.Thread(target=run_keep_alive, daemon=True)
    t_ping.start()

    main_loop()
