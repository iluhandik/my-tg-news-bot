import os
import re
import sys
import time
import html
import json
import hashlib
import subprocess
import urllib
import urllib.request
import urllib.parse
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timezone
from typing import List, Dict, Optional, Tuple, Set
import requests
from PIL import Image, ImageFilter
from dotenv import load_dotenv

load_dotenv()

# Миттєвий вивід логів у консоль Render
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)

# Фоновий веб-сервер для статусу Live
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, format, *args):
        return

def run_health_check():
    port = int(os.getenv("PORT", "10000"))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    server.serve_forever()

threading.Thread(target=run_health_check, daemon=True).start()

# ==================== НАЛАШТУВАННЯ ====================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID", "@kropivnit").strip()
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID", "").strip()

CHANNEL_NAME = "NEWS УКРАЇНА 365"
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "").strip() or (TELEGRAM_CHANNEL_ID if TELEGRAM_CHANNEL_ID.startswith("@") else "@kropivnit")

AI_API_KEY = os.getenv("AI_API_KEY", "").strip()
AI_MODEL = os.getenv("AI_MODEL", "llama-3.3-70b-versatile").strip()

DONOR_CHANNELS = [
    "u_now",
    "uniannet",
    "insiderUKR",
    "UaOnlii",
    "operativnoZSU",
    "suspilnenews",
    "rbc_ukraine",
    "ssternenko",
    "DeepStateUA",
    "nefedoov365",
    "inslandd",
    "zsuwar",
    "supernova_plus",
    "chp_zahid"
]

CHANNELS_WITH_HEAVY_WATERMARKS = {
    "supernova_plus",
    "exilenova_plus"
}

MAX_POSTS_PER_RUN = 5
DELAY_BETWEEN_POSTS = 20
MAX_POST_AGE_HOURS = 4.0

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MEDIA_DIR = os.path.join(BASE_DIR, "media")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
TOPICS_FILE = os.path.join(BASE_DIR, "published_topics.json")
MEDIA_HASH_FILE = os.path.join(BASE_DIR, "media_hashes.json")
PROCESSED_IDS_FILE = os.path.join(BASE_DIR, "processed_ids.json")
WATERMARK_FILE = os.path.join(BASE_DIR, "watermark.png")

os.makedirs(MEDIA_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ==================== АВТОВИПРАВЛЕННЯ СУРЖИКУ ====================
CALQUE_REPLACEMENTS = [
    (r"(?i)\bзапорі[зс]хсталь\b", "Запоріжсталь"),
    (r"(?i)\bзапорожсталь\b", "Запоріжсталь"),
    (r"(?i)\bбалл[іи]стич([а-яіїєґ]*)\b", r"балістич\1"),
    (r"(?i)\bбаллістик([а-яіїєґ]*)\b", r"балістик\1"),
    (r"(?i)\bслежк([а-яіїєґ]*)\b", "стеження"),
    (r"(?i)\bтенево([а-яіїєґ]*)\b", r"тіньово\1"),
    (r"(?i)\bтеневи([а-яіїєґ]*)\b", r"тіньови\1"),
    (r"(?i)\bгорящ([а-яіїєґ]*)\b", r"палаюч\1"),
    (r"(?i)\bявляется\b", "є"),
    (r"(?i)\bявляются\b", "є"),
]

def fix_ukrainian_calques(text: str) -> str:
    text = re.sub(r"\bвсу\b", "ЗСУ", text, flags=re.IGNORECASE)
    text = re.sub(r"\bвійськові сили україни\b", "Збройні Сили України", text, flags=re.IGNORECASE)
    def fix_moment(m):
        orig = m.group(0)
        return "Наразі" if orig[0].isupper() else "наразі"
    text = re.sub(r"\b(у|в|на)\s+дан(и|н)й\s+момент\b", fix_moment, text, flags=re.IGNORECASE)
    for pattern, replacement in CALQUE_REPLACEMENTS:
        text = re.sub(pattern, replacement, text)
    return text

# ==================== ФІЛЬТР СПАМУ ====================
AD_PATTERNS = [
    r"(?i)\b(erid|реклама|спонсор|партнерський)\b",
    r"(?i)(кол-центр|колцентр|офіс|шахрайськ.*офіс|100\s*000\s*грн|винагород|зароби.*на.*викритті)",
    r"(?i)(подпишись|підпишись|подписаться|підписатися)\s*(на|в)?\s*(канал|нас|резерв|чат|джерело)",
    r"(?i)(казино|ставки|1win|parimatch|слоти|гемблінг|бонус|промокод|виграш|бездеп|беттинг)",
    r"(?i)(крипта|криптовалют|сигнали|трейдинг|btc|eth|usdt|заробіток|пасивний дохід|схема)",
    r"(?i)(переходьте за посиланням|тисни сюди|дивіться тут|продовження за лінком|подробиці за лінком)",
    r"(?i)(прислать новость|надіслати новину|зв'язок з адміном|наш бот|замовити рекламу)",
    r"(?i)t\.me/\+[a-zA-Z0-9_-]+",
]

def is_unwanted_content(channel: str, text: str) -> bool:
    if not text or len(text.strip()) < 15:
        return True
    clean = html.unescape(text).lower()

    for pattern in AD_PATTERNS:
        if re.search(pattern, clean):
            return True

    if any(w in clean for w in ["родная гавань", "девочки, смотрите что прислали"]):
        return True

    if re.search(r"(?i)\b(жовта тривога|навчальна тривога|тестування систем сповіщення|перевірка сирен)\b", clean):
        if not any(m in clean for m in ["вибух", "збит", "влучан", "приліт", "пожеж", "руйнуван", "поранен"]):
            return True

    return False

# ==================== ВОТЕРМАРКА ====================
def apply_watermark_to_image(img_path: str, output_path: str) -> str:
    try:
        if not os.path.exists(WATERMARK_FILE) or not os.path.exists(img_path):
            return img_path

        base_img = Image.open(img_path).convert("RGB")
        width, height = base_img.size
        wm = Image.open(WATERMARK_FILE).convert("RGBA")

        wm_w = max(200, int(width * 0.35))
        wm_h = int(wm.height * (wm_w / wm.width))
        wm_resized = wm.resize((wm_w, wm_h), Image.Resampling.LANCZOS)

        erase_w = int(width * 0.55)
        erase_h = int(height * 0.16)
        erase_x = (width - erase_w) // 2
        erase_y = (height - erase_h) // 2

        blur_box = (erase_x, erase_y, erase_x + erase_w, erase_y + erase_h)
        erased_area = base_img.crop(blur_box).filter(ImageFilter.GaussianBlur(18))
        base_img.paste(erased_area, blur_box)

        pos_x = (width - wm_w) // 2
        pos_y = (height - wm_h) // 2
        base_img.paste(wm_resized, (pos_x, pos_y), mask=wm_resized)

        base_img.save(output_path, "JPEG", quality=92)
        return output_path
    except Exception:
        return img_path

def apply_watermark_to_video(video_path: str, output_path: str) -> str:
    try:
        if not os.path.exists(WATERMARK_FILE) or not os.path.exists(video_path):
            return video_path

        filter_complex = (
            "[0:v]split[main][crop_in];"
            "[crop_in]crop=w=in_w*0.55:h=in_h*0.16:x=(in_w-out_w)/2:y=(in_h-out_h)/2,boxblur=10:2[blurred];"
            "[main][blurred]overlay=(W-w)/2:(H-h)/2[base];"
            "[1:v][base]scale2ref=w=main_w*0.35:h=-1[wm][base_ref];"
            "[base_ref][wm]overlay=(W-w)/2:(H-h)/2[v]"
        )

        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-i", WATERMARK_FILE,
            "-filter_complex", filter_complex,
            "-map", "[v]", "-map", "0:a?",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
            "-c:a", "copy",
            output_path
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90)
        if res.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 1000:
            return output_path
        return video_path
    except Exception:
        return video_path

# ==================== ЗБЕРЕЖЕННЯ ТА ТОЧНИЙ АНТИДУБЛЬ ====================
def load_json(path: str) -> list:
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []

def save_json(path: str, data: list):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

STOP_WORDS = {
    "сьогодні", "зараз", "вчора", "цього", "після", "також", "було", "проти", "може",
    "через", "буде", "тільки", "цьому", "коли", "якщо", "були", "вони", "яких", "який",
    "повідомляє", "новини", "року", "потім", "перед", "під", "над", "щоб", "стало",
    "відомо", "заявив", "словами", "джерело", "офіційно", "увага", "терміново"
}

def extract_stems(text: str) -> Set[str]:
    clean = re.sub(r"[^\w\s]", " ", text.lower(), flags=re.UNICODE)
    stems = set()
    for w in clean.split():
        if len(w) >= 4 and w not in STOP_WORDS:
            stems.add(w[:5])
    return stems

def is_topic_already_covered(text: str) -> bool:
    topics = load_json(TOPICS_FILE)
    cur_stems = extract_stems(text)
    if not cur_stems or len(cur_stems) < 3:
        return False

    now = time.time()
    min_time = now - (18 * 3600)

    for t in topics[-40:]:
        if t.get("timestamp", 0) < min_time:
            continue
        prev_stems = set(t.get("stems", []))
        if not prev_stems:
            continue

        # Збіг ключових слів (якщо однакових слів більше 35% — це одна й та сама новина)
        intersection = cur_stems.intersection(prev_stems)
        union = cur_stems.union(prev_stems)
        similarity = len(intersection) / len(union)

        if similarity >= 0.35:
            print(f"[АНТИДУБЛЬ]: Збіг {similarity:.2f} з темою '{t.get('headline', '')[:30]}...'. Відхилено!")
            return True

    return False

def register_published_topic(headline: str, text: str):
    topics = load_json(TOPICS_FILE)
    stems = extract_stems(headline + " " + text)
    topics.append({
        "headline": headline,
        "stems": list(stems),
        "timestamp": time.time()
    })
    save_json(TOPICS_FILE, topics[-200:])

def is_media_duplicate(media_bytes: bytes, media_url: str = "") -> bool:
    if not media_bytes and not media_url:
        return False
    history = load_json(MEDIA_HASH_FILE)
    c_hash = hashlib.md5(media_bytes).hexdigest() if media_bytes else ""
    now = time.time()
    min_time = now - (24 * 3600)

    for item in history:
        if item.get("timestamp", 0) < min_time:
            continue
        if c_hash and item.get("content_hash") == c_hash:
            return True
    return False

def register_media(media_bytes: bytes, media_url: str = ""):
    history = load_json(MEDIA_HASH_FILE)
    c_hash = hashlib.md5(media_bytes).hexdigest() if media_bytes else ""
    history.append({
        "content_hash": c_hash,
        "timestamp": time.time()
    })
    save_json(MEDIA_HASH_FILE, history[-200:])

def is_post_id_processed(post_id: str) -> bool:
    ids = set(load_json(PROCESSED_IDS_FILE))
    return post_id in ids

def register_post_id(post_id: str):
    if not post_id: return
    ids = load_json(PROCESSED_IDS_FILE)
    if post_id not in ids:
        ids.append(post_id)
        save_json(PROCESSED_IDS_FILE, ids[-500:])

# ==================== ЧИТАННЯ ВЛАСНОГО КАНАЛУ ====================
def sync_channel_history(channel_username: str) -> List[str]:
    recent_texts = []
    clean_name = channel_username.replace("https://t.me/", "").replace("t.me/", "").replace("@", "").strip("/")
    if clean_name:
        url = f"https://t.me/s/{clean_name}"
        headers = {"User-Agent": "Mozilla/5.0"}
        try:
            r = requests.get(url, headers=headers, timeout=10)
            if r.status_code == 200:
                blocks = r.text.split('<div class="tgme_widget_message_wrap')
                for b in blocks[1:][-20:]:
                    m = re.search(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>\s*<div class="tgme_widget_message_footer', b, re.DOTALL)
                    if not m: continue
                    clean = html.unescape(re.sub(r"<[^>]+>", " ", m.group(1))).strip()
                    if len(clean) > 20:
                        head = clean.splitlines()[0][:60]
                        register_published_topic(head, clean)
                        recent_texts.append(clean[:120])
        except Exception:
            pass
    return recent_texts

# ==================== ШІ-ОБРОБКА (100% УКРАЇНСЬКА МОВА ТА ЧИТАБЕЛЬНІСТЬ) ====================
SYSTEM_PROMPT = """Ти — головний редактор всеукраїнського Telegram-каналу новин.
Твоє завдання — перетворити текст новини на красивий, легкочитний пост ЧИСТОЮ СОЛОВ'ЇНОЮ МОВОЮ.

ГОЛОВНІ ВИМОГИ:
1. МОВА: 100% УКРАЇНСЬКА! Категорично заборонено залишати будь-які речення чи цитати російською мовою! Будь-яку пряму мову обов'язково перекладай українською!
2. ЧИТАБЕЛЬНІСТЬ (НЕ СУЦІЛЬНИЙ ТЕКСТ):
   - Розбивай текст на 2-3 короткі, охайні абзаци (по 2-3 речення кожен).
   - Між абзацами ОБОВ'ЯЗКОВО порожній рядок.
   - Використовуй жирний шрифт для ключових слів (імена, міста, головні цифри).
3. ТОЧНІСТЬ: Пиши виключно за фактами донора, не вигадуй неіснуючі подробиці чи цифри.
4. ОФОРМЛЕННЯ:
   - Заголовок <b>жирним</b> з 1 емодзі. Звичайними реченнями, БЕЗ капсу.
   - Пряму мову оформи через <blockquote>...</blockquote> українською мовою!
   - ЖОДНИХ підписів у кінці, жодного значка "💬".

Формат відповіді СТРОГО JSON:
{
  "is_duplicate": false,
  "is_ad": false,
  "telegram_post": "Текст гарного українського посту з тегами <b>, <i> та <blockquote>",
  "news_score": 7
}
"""

def clean_donor_mentions(text: str) -> str:
    text = html.unescape(text)
    text = re.sub(r"https?://\S+|t\.me/\S+|@[a-zA-Z0-9_]+", "", text)
    text = re.sub(r"(?i)\b(ус\s*/?|украина сейчас|инсайдер|униан|supernova|труха|нефедов)\b", "", text).strip()
    return text

def process_news_with_ai(raw_text: str, recent_channel_posts: List[str]) -> Optional[Dict]:
    cleaned = clean_donor_mentions(raw_text)
    if len(cleaned) < 20:
        return None

    api_key = AI_API_KEY.strip()
    if not api_key:
        return None

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    recent_context = ""
    if recent_channel_posts:
        recent_context = "ОСТАННІ ОПУБЛІКОВАНІ ПОСТИ В КАНАЛІ:\n" + "\n".join([f"- {p}" for p in recent_channel_posts[:15]]) + "\n\n"

    user_prompt = f"{recent_context}НОВА НОВИНА (переклади 100% українською, зроби 2-3 короткі абзаци, перевір на дубль):\n{cleaned}"

    models = ["llama-3.3-70b-versatile", "openai/gpt-oss-120b", "llama-3.1-8b-instant"]

    for model in models:
        try:
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt}
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0.0
            }
            resp = requests.post("https://api.groq.com/openai/v1/chat/completions", json=payload, headers=headers, timeout=25)
            if resp.status_code != 200:
                continue
            data = resp.json()
            parsed = json.loads(data["choices"][0]["message"]["content"])
            if parsed.get("is_duplicate", False) or parsed.get("is_ad", False):
                return None

            if "telegram_post" in parsed:
                parsed["telegram_post"] = fix_ukrainian_calques(parsed["telegram_post"])

            return parsed
        except Exception:
            continue
    return None

# ==================== ПУБЛІКАЦІЯ В TELEGRAM ====================
def send_telegram_post(post_text: str, photo_url: Optional[str] = None, video_url: Optional[str] = None, donor: str = "") -> bool:
    url_base = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
    caption = post_text[:1024]
    has_heavy_wm = donor.lower() in CHANNELS_WITH_HEAVY_WATERMARKS

    # Якщо новина має відео — надсилаємо ВИКЛЮЧНО відео!
    if video_url:
        temp_vid = os.path.join(MEDIA_DIR, "temp_vid.mp4")
        branded_vid = os.path.join(MEDIA_DIR, "branded_vid.mp4")
        try:
            r = requests.get(video_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=60, stream=True)
            if r.status_code == 200:
                with open(temp_vid, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1024*1024):
                        if chunk: f.write(chunk)

                with open(temp_vid, "rb") as vf:
                    video_sample = vf.read(2 * 1024 * 1024)

                if is_media_duplicate(video_sample, video_url):
                    return False

                if has_heavy_wm and os.path.exists(WATERMARK_FILE):
                    final_video = apply_watermark_to_video(temp_vid, branded_vid)
                else:
                    final_video = temp_vid

                with open(final_video, "rb") as f:
                    resp = requests.post(
                        f"{url_base}/sendVideo",
                        data={"chat_id": TELEGRAM_CHANNEL_ID, "caption": caption, "parse_mode": "HTML"},
                        files={"video": ("video.mp4", f, "video/mp4")},
                        timeout=90
                    )
                    if resp.json().get("ok", False):
                        register_media(video_sample, video_url)
                        return True
        except Exception as e:
            print(f"[ПОМИЛКА ВІДЕО]: {e}")
        # Якщо відео не надіслалося, НЕ надсилаємо стоп-кадр як фото!
        return False

    # Якщо це оригінальне фото
    if photo_url:
        try:
            r = requests.get(photo_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
            if r.status_code == 200 and len(r.content) > 500:
                if is_media_duplicate(r.content, photo_url):
                    return False
                temp_img = os.path.join(MEDIA_DIR, "temp_img.jpg")
                branded_img = os.path.join(MEDIA_DIR, "branded_img.jpg")
                with open(temp_img, "wb") as f:
                    f.write(r.content)

                if has_heavy_wm and os.path.exists(WATERMARK_FILE):
                    final_img = apply_watermark_to_image(temp_img, branded_img)
                else:
                    final_img = temp_img

                with open(final_img, "rb") as f:
                    resp = requests.post(
                        f"{url_base}/sendPhoto",
                        data={"chat_id": TELEGRAM_CHANNEL_ID, "caption": caption, "parse_mode": "HTML"},
                        files={"photo": ("photo.jpg", f, "image/jpeg")},
                        timeout=30
                    )
                    if resp.json().get("ok", False):
                        register_media(r.content, photo_url)
                        return True
        except Exception:
            pass
        return False

    # Текстовий пост
    try:
        resp = requests.post(
            f"{url_base}/sendMessage",
            data={"chat_id": TELEGRAM_CHANNEL_ID, "text": post_text, "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=20
        )
        return resp.json().get("ok", False)
    except Exception:
        return False

# ==================== ПАРСЕР З ЧІТКИМ ВІДЕО-ДЕТЕКТОРОМ ====================
def fetch_posts(channel: str) -> List[Dict]:
    url = f"https://t.me/s/{channel}"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0 Safari/537.36"}
    posts = []
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=12) as resp:
            html_raw = resp.read().decode("utf-8", errors="ignore")
        blocks = html_raw.split('<div class="tgme_widget_message_wrap')
        for b in blocks[1:][-8:]:
            p_id = re.search(r'data-post="([^"]+)"', b)
            if not p_id: continue
            post_id = p_id.group(1)

            if is_post_id_processed(post_id):
                continue

            time_match = re.search(r'<time[^>]+datetime="([^"]+)"', b)
            if time_match:
                try:
                    dt = datetime.fromisoformat(time_match.group(1).replace("Z", "+00:00"))
                    diff_hours = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
                    if diff_hours > MAX_POST_AGE_HOURS:
                        continue
                except Exception:
                    pass

            t_match = re.search(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>\s*<div class="tgme_widget_message_footer', b, re.DOTALL)
            raw_text = html.unescape(re.sub(r"<[^>]+>", " ", t_match.group(1))).strip() if t_match else ""

            unescaped = html.unescape(b)

            # Визначаємо справжнє відео
            v_match = re.search(r'<video[^>]+src=[\'"](https?://[^\'"]+)[\'"]', unescaped)
            video_url = v_match.group(1) if v_match else None

            # Якщо це відео — photo_url ставимо None!
            photo_url = None
            if not video_url:
                p_match = re.search(r'background-image:\s*url\([\'"]?(https?://[^\'")\s]+)[\'"]?\)', unescaped)
                photo_url = p_match.group(1) if p_match else None

            if raw_text or photo_url or video_url:
                posts.append({
                    "channel": channel,
                    "text": raw_text,
                    "photo_url": photo_url,
                    "video_url": video_url,
                    "post_id": post_id
                })
    except Exception:
        pass
    return posts

# ==================== ГОЛОВНИЙ ЦИКЛ ====================
def run():
    recent_channel_posts = sync_channel_history(CHANNEL_USERNAME)
    published_count = 0
    channel_posts_used = set()

    candidates_by_channel = {}
    for ch in DONOR_CHANNELS:
        posts = fetch_posts(ch)
        if posts:
            candidates_by_channel[ch] = posts

    interleaved_candidates = []
    max_len = max((len(p) for p in candidates_by_channel.values()), default=0)
    for idx in range(max_len):
        for ch in DONOR_CHANNELS:
            if ch in candidates_by_channel and idx < len(candidates_by_channel[ch]):
                interleaved_candidates.append(candidates_by_channel[ch][idx])

    for p in interleaved_candidates:
        if published_count >= MAX_POSTS_PER_RUN:
            break

        channel = p["channel"]
        if channel in channel_posts_used and len(candidates_by_channel) > 1:
            continue

        text = p["text"]
        photo = p["photo_url"]
        video = p["video_url"]
        post_id = p.get("post_id", "")

        if is_unwanted_content(channel, text):
            register_post_id(post_id)
            continue

        # Перевірка на дубль новини
        if is_topic_already_covered(text):
            register_post_id(post_id)
            continue

        if video and is_media_duplicate(b"", video):
            register_post_id(post_id)
            continue

        ai = process_news_with_ai(text, recent_channel_posts)
        if not ai:
            register_post_id(post_id)
            continue

        post_text = ai.get("telegram_post", "")
        if len(post_text) < 20:
            register_post_id(post_id)
            continue

        # Додаткова перевірка згенерованого заголовка на дублікат
        head = post_text.splitlines()[0][:60] if post_text.splitlines() else post_text[:60]
        if is_topic_already_covered(head):
            register_post_id(post_id)
            continue

        if published_count > 0:
            time.sleep(DELAY_BETWEEN_POSTS)

        posted = send_telegram_post(post_text, photo_url=photo, video_url=video, donor=channel)
        if posted:
            published_count += 1
            channel_posts_used.add(channel)
            register_published_topic(head, text)
            register_post_id(post_id)
            recent_channel_posts.insert(0, head)
            print(f"✅ ОПУБЛІКОВАНО ({published_count}/{MAX_POSTS_PER_RUN}) з @{channel}: {head[:45]}...")

if __name__ == "__main__":
    print("🚀 Бот працює в режимі швидкого оновлення 24/7!")
    while True:
        try:
            run()
        except Exception as e:
            print(f"[ПОМИЛКА]: {e}")
        time.sleep(60)
