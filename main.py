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
    "exilenova_plus",
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
        'район', 'місті', 'внасл', 'людей', 'траси', 'нового', 'перший', 'повідо',
        'зеленськ', 'президент', 'трамп', 'путін', 'єрмак', 'буданов', 'сирськ', 'залужн'
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

# --- ПЕРЕВІРКА ПРАВ АДМІНІСТРАТОРА КАНАЛУ ---
def is_channel_admin(user_id):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getChatMember"
    try:
        r = requests.get(url, params={"chat_id": TELEGRAM_CHANNEL_ID, "user_id": user_id}, timeout=8)
        data = r.json()
        if data.get("ok"):
            status = data.get("result", {}).get("status", "")
            return status in ["creator", "administrator"]
    except Exception as e:
        logger.warning(f"Admin check error: {e}")
    return False

# --- ПІДПИС КАНАЛУ ---
def append_channel_signature(text):
    clean_text = text.strip()
    channel_user = TELEGRAM_CHANNEL_ID.lstrip('@')
    signature = f'\n\n👉 <a href="https://t.me/{channel_user}">NEWS УКРАЇНА 365 | Підписатися</a>'
    max_text_len = 1015 - len(signature)
    if len(clean_text) > max_text_len:
        clean_text = clean_text[:max_text_len].rsplit(' ', 1)[0] + '...'
    return clean_text + signature

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

# --- AI РЕДАКТУРА ДЛЯ АДМІН-ПОСТІВ ТА АВТОМАТИКИ ---
def rewrite_news_for_channel(text, model_name, skip_duplicates_check=False, recent_headlines=[]):
    if not GROQ_API_KEY or not text or len(text.strip()) < 15:
        return None

    lower_raw = text.lower()

    if not skip_duplicates_check:
        digest_markers = [
            "важливе за день", "головне за день", "підсумки дня", "підсумок дня",
            "головне за сьогодні", "топ новин", "головні новини дня", "головне за ніч",
            "дайджест", "вечірній дайджест", "ранковий дайджест"
        ]
        if any(d in lower_raw for d in digest_markers):
            return "SKIP"

        flight_radar_words = [
            "рухаються у", "рухаються в", "курс на", "курсом на", "фіксують у", "фіксуються в",
            "напрямок руху", "напрямку чорнобиля", "напрямку києва", "повз миронівку", "повз "
        ]
        has_real_impact = any(k in lower_raw for k in ["збито", "відбито", "влучання", "руйнування", "пожежа", "загинули"])
        if any(r in lower_raw for r in flight_radar_words) and not has_real_impact:
            return "SKIP"

    context_str = "\n".join([f"{i}. {h}" for i, h in enumerate(recent_headlines[:15], 1)]) if recent_headlines else ""

    system_prompt = (
        "ТИ — ГОЛОВНИЙ РЕДАКТОР УКРАЇНСЬКОГО ТЕЛЕГРАМ-КАНАЛУ «NEWS УКРАЇНА 365» (@kropivnit).\n\n"
        f"ОСТАННІ ОПУБЛІКОВАНІ ТЕМИ:\n{context_str}\n\n"
        "СУВОРІ РЕДАКЦІЙНІ ПРАВИЛА:\n"
        "1. АНТИДУБЛЬ (якщо це авто-пост): Якщо це та сама подія без нових фактів — пиши SKIP. Якщо пост надіслав адмін — оформи обов'язково!\n"
        "2. ЗВЕРНЕННЯ ТА ЗАЯВИ: Різні теми одного звернення публікувати обов'язково окремими постами!\n"
        "3. БЕЗ ПАНІКИ: Якщо новина — подробиці старої трагедії, пиши «Розкрито подробиці», а не «Стався теракт».\n"
        "4. ЧИСТА МОВА: «В інтерв'ю» (НЕ «У інтерв'ю»), «стікав кров'ю» (НЕ «истекав кров'ю»), «ДСНС» (НЕ «ГСЧС»), «вирва» (НЕ «воронка»).\n"
        "5. СТАТУС ТРАМПА: Дональд Трамп є Президентом США (НЕ «екс-президент»!).\n\n"
        "ФОРМАТ ПОСТА:\n"
        "⚡ <b>Короткий влучний заголовок</b>\n\n"
        "1-2 фактичні абзаци. Ключові дані виділяй <b>жирним</b>.\n"
        "Цитати: «Текст», — <b>Прізвище</b>.\n\n"
        "ВІДПОВІДЬ: ТІЛЬКИ ГОТОВИЙ ПОСТ АБО СЛОВО SKIP."
    )

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
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
        r'\bВСУ\b': 'ЗСУ'
    }
    for r_pattern, replacement in ru_fixes.items():
        out = re.sub(r_pattern, replacement, out, flags=re.IGNORECASE)

    return out

# --- ВИКАЧУВАННЯ ФАЙЛІВ З TELEGRAM ДЛЯ ПЕРЕСЛАНИХ ПОВІДОМЛЕНЬ ---
def download_tg_file(file_id):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getFile?file_id={file_id}"
        r = requests.get(url, timeout=10)
        file_path = r.json().get("result", {}).get("file_path")
        if file_path:
            download_url = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_path}"
            f_resp = requests.get(download_url, timeout=30)
            if f_resp.status_code == 200:
                return f_resp.content
    except Exception as e:
        logger.warning(f"Error downloading tg file: {e}")
    return None

# --- ФОНОВИЙ СЛУХАЧ ДЛЯ ПОВІДОМЛЕНЬ АДМІНІСТРАТОРА (VIP ПУШ) ---
def run_admin_listener():
    logger.info("Admin VIP listener activated for private forwards...")
    offset = 0
    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
            params = {"offset": offset, "timeout": 20}
            r = requests.get(url, params=params, timeout=25)
            data = r.json()
            if not data.get("ok"):
                time.sleep(2)
                continue

            for update in data.get("result", []):
                offset = update["update_id"] + 1
                msg = update.get("message")
                if not msg:
                    continue

                user_id = msg.get("from", {}).get("id")
                chat_type = msg.get("chat", {}).get("type")

                # Приймаємо тільки особисті повідомлення (в ПП боту)
                if chat_type != "private":
                    continue

                # Перевіряємо, чи користувач є адміном або власником @kropivnit
                if not is_channel_admin(user_id):
                    requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={
                        "chat_id": user_id,
                        "text": "⛔ Доступ лише для адміністраторів та власника каналу @kropivnit."
                    })
                    continue

                # Повідомляємо адміна про старт обробки
                requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={
                    "chat_id": user_id,
                    "text": "⏳ Отримав! Обробляю через AI та публікую в канал..."
                })

                # Витягуємо текст
                text_content = msg.get("text") or msg.get("caption") or ""
                
                # Перевіряємо, чи це посилання на пост (наприклад, t.me/exilenova_plus/12345)
                link_match = re.search(r't\.me/([^/]+)/(\d+)', text_content)
                video_bytes = None
                photo_bytes = None

                if link_match:
                    ch_name = link_match.group(1)
                    m_id = link_match.group(2)
                    v_url, p_urls = fetch_media_from_embed(ch_name, m_id)
                    if v_url:
                        v_resp = requests.get(v_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
                        if v_resp.status_code == 200:
                            video_bytes = v_resp.content
                    elif p_urls:
                        p_resp = requests.get(p_urls[0], headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
                        if p_resp.status_code == 200:
                            photo_bytes = p_resp.content
                else:
                    # Якщо переслано напряму з відео або фото
                    if "video" in msg:
                        video_bytes = download_tg_file(msg["video"]["file_id"])
                    elif "photo" in msg:
                        # беремо найбільший розмір фото
                        photo_bytes = download_tg_file(msg["photo"][-1]["file_id"])

                # Робимо рерайт через AI
                model_name = get_working_groq_model()
                rewritten = rewrite_news_for_channel(text_content, model_name, skip_duplicates_check=True)
                if not rewritten or rewritten == "SKIP":
                    rewritten = text_content # якщо AI відмовився, лишаємо текст адміна

                caption = append_channel_signature(rewritten)

                # Публікуємо в канал
                success = False
                if video_bytes:
                    files = {"video": ("video.mp4", video_bytes, "video/mp4")}
                    data_post = {"chat_id": TELEGRAM_CHANNEL_ID, "caption": caption, "parse_mode": "HTML"}
                    res = requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendVideo", data=data_post, files=files, timeout=45)
                    success = res.json().get("ok", False)
                elif photo_bytes:
                    files = {"photo": ("image.jpg", photo_bytes, "image/jpeg")}
                    data_post = {"chat_id": TELEGRAM_CHANNEL_ID, "caption": caption, "parse_mode": "HTML"}
                    res = requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto", data=data_post, files=files, timeout=25)
                    success = res.json().get("ok", False)
                else:
                    res = requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={
                        "chat_id": TELEGRAM_CHANNEL_ID,
                        "text": caption,
                        "parse_mode": "HTML",
                        "disable_web_page_preview": True
                    }, timeout=15)
                    success = res.json().get("ok", False)

                # Звіт адміну
                if success:
                    requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={
                        "chat_id": user_id,
                        "text": f"🚀 <b>Успішно опубліковано в @kropivnit!</b>\n\n{rewritten.split(chr(10))[0]}",
                        "parse_mode": "HTML"
                    })
                else:
                    requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={
                        "chat_id": user_id,
                        "text": "❌ Помилка під час публікації. Перевірте формат повідомлення."
                    })

        except Exception as e:
            logger.warning(f"Admin listener loop error: {e}")
            time.sleep(2)

# --- SCRAPER & EMBED UTILITIES ---
def scrape_channel(channel_name):
    url = f"https://t.me/s/{channel_name.lstrip('@')}"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=5) as response:
            html_content = response.read().decode('utf-8', errors='ignore')
    except Exception as e:
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

def fetch_media_from_embed(donor_channel, msg_id):
    clean_channel = donor_channel.lstrip('@')
    embed_url = f"https://t.me/{clean_channel}/{msg_id}?embed=1"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Referer": f"https://t.me/s/{clean_channel}"}
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
        logger.warning(f"Embed fetch error: {e}")
    return video_url, photo_urls

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
        except Exception:
            break
    posts.reverse()
    return posts

def is_blatant_duplicate(candidate_roots, raw_text, recent_posts):
    if len(candidate_roots) < 2:
        return False
    update_markers = ["повторн", "новий удар", "обвалив", "зруйновано повністю", "кількість зросла", "підтвердив генштаб", "указ", "нагородив", "орден"]
    if any(m in raw_text.lower() for m in update_markers):
        return False
    for p in recent_posts[:25]:
        p_roots = extract_canonical_roots(p)
        common = candidate_roots & p_roots
        if len(common) >= 2 and len(common) / len(candidate_roots) >= 0.4:
            return True
    return False

def publish_post_with_real_media(donor_channel, msg_id, text):
    caption = append_channel_signature(text)
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Referer": "https://t.me/"}
    video_url, photo_urls = fetch_media_from_embed(donor_channel, msg_id)

    if video_url:
        try:
            resp = requests.get(video_url, headers=headers, timeout=25)
            if resp.status_code == 200 and len(resp.content) > 5000 and len(resp.content) <= 50 * 1024 * 1024:
                files = {"video": ("video.mp4", resp.content, "video/mp4")}
                data = {"chat_id": TELEGRAM_CHANNEL_ID, "caption": caption, "parse_mode": "HTML"}
                r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendVideo", data=data, files=files, timeout=45)
                if r.json().get("ok"):
                    return True
        except Exception:
            pass

    if photo_urls:
        for p_url in photo_urls[:1]:
            try:
                resp = requests.get(p_url, headers=headers, timeout=12)
                if resp.status_code == 200 and len(resp.content) >= 20000:
                    files = {"photo": ("image.jpg", resp.content, "image/jpeg")}
                    data = {"chat_id": TELEGRAM_CHANNEL_ID, "caption": caption, "parse_mode": "HTML"}
                    r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto", data=data, files=files, timeout=25)
                    if r.json().get("ok"):
                        return True
            except Exception:
                pass

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHANNEL_ID, "text": caption, "parse_mode": "HTML", "disable_web_page_preview": True}
    r = requests.post(url, json=payload, timeout=12)
    return r.json().get("ok", False)

# --- ОСНОВНИЙ ЦИКЛ ОНОВЛЕННЯ ---
def main_loop():
    logger.info("Initializing news bot with Admin VIP Listener...")
    state = load_state()
    channel_posts = get_channel_recent_posts(TELEGRAM_CHANNEL_ID)
    if not state.get("recent_published_posts"):
        state["recent_published_posts"] = channel_posts[:60]

    if not state.get("posted_hashes"):
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
            if loop_count % 12 == 0:
                channel_posts = get_channel_recent_posts(TELEGRAM_CHANNEL_ID)
                for p in channel_posts[:20]:
                    if p not in state["recent_published_posts"]:
                        state["recent_published_posts"].insert(0, p)
                state["recent_published_posts"] = state["recent_published_posts"][:100]
            loop_count += 1

            recent_headlines = [p.strip().split('\n')[0][:90] for p in state["recent_published_posts"][:15] if p]

            for channel in DONOR_CHANNELS:
                items = scrape_channel(channel)
                for item in items[-3:]:
                    item_hash = item["hash"]
                    if not item_hash or item_hash in state["posted_hashes"]:
                        continue

                    raw_text = item["text"]
                    if not raw_text or len(raw_text) < 30:
                        continue

                    if is_blatant_duplicate(item["roots"], raw_text, state["recent_published_posts"]):
                        state["posted_hashes"].append(item_hash)
                        save_state(state)
                        continue

                    rewritten = rewrite_news_for_channel(raw_text, model_name, skip_duplicates_check=False, recent_headlines=recent_headlines)
                    if rewritten == "SKIP":
                        state["posted_hashes"].append(item_hash)
                        save_state(state)
                        continue

                    if not rewritten:
                        continue

                    success = publish_post_with_real_media(item["donor_channel"], item["msg_id"], rewritten)
                    if success:
                        headline = rewritten.split('\n')[0]
                        logger.info(f"Published auto-post: {headline}")
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

    # Запускаємо VIP-слухача для пересилань від власника в приваті
    t_admin = threading.Thread(target=run_admin_listener, daemon=True)
    t_admin.start()

    main_loop()
