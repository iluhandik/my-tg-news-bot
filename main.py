import os
import re
import sys
import time
import html
import json
import hashlib
import subprocess
import urllib.request
import urllib.parse
from datetime import datetime
from typing import List, Dict, Optional, Tuple, Set
import requests
from PIL import Image, ImageFilter
from dotenv import load_dotenv

load_dotenv()

# ==================== НАЛАШТУВАННЯ ====================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID", "@kropivnit").strip()
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID", "").strip()

CHANNEL_NAME = "NEWS УКРАЇНА 365"
CHANNEL_USERNAME = TELEGRAM_CHANNEL_ID if TELEGRAM_CHANNEL_ID.startswith("@") else "@kropivnit"

AI_PROVIDER = os.getenv("AI_PROVIDER", "groq").strip().lower()
AI_API_KEY = os.getenv("AI_API_KEY", "").strip()
AI_MODEL = os.getenv("AI_MODEL", "llama-3.1-8b-instant").strip()
BREAKING_NEWS_THRESHOLD = 8

DONOR_CHANNELS = [
    "u_now",
    "uniannet",
    "UaOnlii",
    "insiderUKR",
    "supernova_plus",
    "exilenova_plus",
    "chp_zahid",
    "zsuwar",
    "nefedoov365",
    "inslandd"
]

# Канали з нав'язливими вотермарками для автоматичної заміни
CHANNELS_WITH_HEAVY_WATERMARKS = {
    "supernova_plus",
    "exilenova_plus"
}

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MEDIA_DIR = os.path.join(BASE_DIR, "media")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
TOPICS_FILE = os.path.join(BASE_DIR, "published_topics.json")
MEDIA_HASH_FILE = os.path.join(BASE_DIR, "media_hashes.json")
WATERMARK_FILE = os.path.join(BASE_DIR, "watermark.png")

os.makedirs(MEDIA_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ==================== АНТИРЕКЛАМА ====================
COMMON_STOP_WORDS = {
    "сьогодні", "зараз", "вчора", "цього", "після", "також", "було", "проти", "може",
    "через", "буде", "тільки", "цьому", "коли", "якщо", "були", "вони", "яких", "який",
    "яка", "яке", "повідомляє", "новини", "року", "потім", "перед", "під", "над", "щоб",
    "стало", "відомо", "заявив", "словами", "джерело", "офіційно", "увага", "терміново",
    "внаслідок", "відповідно", "разом", "перший", "другий", "можуть", "бути", "своїх"
}

AD_PATTERNS = [
    r"(?i)\b(erid|реклама|спонсор|партнерський)\b",
    r"(?i)(кол-центр|колцентр|офіс|шахрайськ.*офіс|100\s*000\s*грн|винагород|зароби.*на.*викритті)",
    r"(?i)(подпишись|підпишись|подписаться|підписатися)\s*(на|в)?\s*(канал|нас|резерв|чат|джерело)",
    r"(?i)(казино|ставки|1win|parimatch|слоти|гемблінг|бонус|промокод|виграш|бездеп|беттинг)",
    r"(?i)(крипта|криптовалют|сигнали|трейдинг|btc|eth|usdt|заробіток|пасивний дохід|схема)",
    r"(?i)(переходьте за посиланням|тисни сюди|дивіться тут|продовження за лінком|подробиці за лінком)",
    r"(?i)(прислать новость|надіслати новину|зв'язок з адміном|наш бот|замовити рекламу)",
    r"(?i)(знижк|акці|розпродаж|купити зі знижкою|встигни забрати)",
    r"(?i)t\.me/\+[a-zA-Z0-9_-]+",
    r"(?i)\b(лайків|лайки|коментарів|фанатів|противників|просвітів|харчок|ізонів|янос)\b",
]

ROUTINE_ALERT_TRIGGERS = [
    r"(?i)\b(жовта тривога|повітряна тривога|оголошено тривогу|загроза бпла|пройдіть в укриття|відбій тривоги)\b",
    r"(?i)\b(активність ворожої авіації|загроза застосування балістики|зліт міг|тривога в областях)\b",
    r"(?i)\b(тривога в києві та областях|тривога в приазов|увага тривога)\b",
]

REAL_EVENT_MARKERS = [
    "вибух", "збит", "влучан", "приліт", "пожеж", "руйнуван", "поранен", "загинул",
    "удар", "пошкоджен", "атакован", "знищен", "ракета впала", "дрон поцілив"
]

def is_advertisement(text: str) -> bool:
    if not text or len(text.strip()) < 20:
        return True
    clean = html.unescape(text).lower()
    for pattern in AD_PATTERNS:
        if re.search(pattern, clean):
            return True
    is_routine = any(re.search(pat, clean) for pat in ROUTINE_ALERT_TRIGGERS)
    if is_routine:
        has_real_event = any(m in clean for m in REAL_EVENT_MARKERS)
        if not has_real_event:
            return True
    return False

# ==================== БРЕНДОВИЙ МЕДІА-БЕЙДЖ (ЛОГОТИП + НАЗВА КАНАЛУ) ====================
def apply_watermark_to_image(img_path: str, output_path: str) -> str:
    """
    Замінює чужу вотермарку на стильний медіа-бейдж:
    [Аватар Шевченка] + NEWS УКРАЇНА 365 + t.me/kropivnit.
    Накладається на місце чужого тексту, розмиваючи його під собою.
    """
    try:
        if not os.path.exists(WATERMARK_FILE) or not os.path.exists(img_path):
            return img_path

        base_img = Image.open(img_path).convert("RGB")
        width, height = base_img.size
        wm = Image.open(WATERMARK_FILE).convert("RGBA")

        # Розмір плашки: приблизно 32% ширини фото для чіткої читабельності назви каналу
        wm_w = max(180, int(width * 0.32))
        wm_h = int(wm.height * (wm_w / wm.width))
        wm_resized = wm.resize((wm_w, wm_h), Image.Resampling.LANCZOS)

        # Координати: центр/нижня частина кадру (де зазвичай ліплять Supernova)
        pos_x = (width - wm_w) // 2
        pos_y = int(height * 0.65)

        # Локальне розмиття саме під плашкою, щоб стерти чужий текст
        blur_box = (
            max(0, pos_x - 10),
            max(0, pos_y - 8),
            min(width, pos_x + wm_w + 10),
            min(height, pos_y + wm_h + 8)
        )
        crop_area = base_img.crop(blur_box).filter(ImageFilter.GaussianBlur(14))
        base_img.paste(crop_area, blur_box)

        # Накладаємо брендовий бейдж
        base_img.paste(wm_resized, (pos_x, pos_y), mask=wm_resized)
        base_img.save(output_path, "JPEG", quality=92)
        return output_path
    except Exception as e:
        print(f"[БРЕНДУВАННЯ ФОТО]: Помилка: {e}")
        return img_path

def apply_watermark_to_video(video_path: str, output_path: str) -> str:
    """Замінює чужий вотермарк на відео на фірмовий медіа-бейдж каналу через FFmpeg."""
    try:
        if not os.path.exists(WATERMARK_FILE) or not os.path.exists(video_path):
            return video_path

        # Накладаємо медіа-бейдж у зону чужого напису (W*0.32, y=H*0.65)
        filter_complex = f"[1:v]scale=iw*0.32:-1[wm];[0:v][wm]overlay=(W-w)/2:H*0.65[v]"

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
    except Exception as e:
        print(f"[БРЕНДУВАННЯ ВІДЕО]: Помилка: {e}")
        return video_path

# ==================== ЗБЕРЕЖЕННЯ ТА АНТИДУБЛІКАТ ====================
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

def extract_entities_and_stems(text: str) -> Tuple[Set[str], Set[str]]:
    capitals = set(re.findall(r"\b[А-ЯІЇЄҐA-Z][а-яіїєґa-z]{2,}", text))
    capitals_norm = {c.lower()[:5] for c in capitals if c.lower() not in COMMON_STOP_WORDS}
    clean = re.sub(r"[^\w\s]", " ", text.lower(), flags=re.UNICODE)
    stems = set()
    for w in clean.split():
        if len(w) >= 4 and w not in COMMON_STOP_WORDS:
            stems.add(w[:5] if len(w) >= 5 else w)
    return capitals_norm, stems

def is_topic_already_covered(text: str) -> bool:
    topics = load_json(TOPICS_FILE)
    cur_caps, cur_stems = extract_entities_and_stems(text)
    now = time.time()
    min_time = now - (24 * 3600)

    for t in topics:
        if t.get("timestamp", 0) < min_time:
            continue
        prev_caps = set(t.get("capitals", []))
        prev_stems = set(t.get("stems", []))

        # 1. Збіг 2 власних назв
        common_caps = cur_caps.intersection(prev_caps)
        if len(common_caps) >= 2:
            print(f"[АНТИДУБЛЬ ГЕО]: Збіг сутностей {common_caps} з постом '{t.get('headline', '')[:35]}...'. ВІДХИЛЕНО!")
            return True

        # 2. Збіг 3 ключових коренів
        common_stems = cur_stems.intersection(prev_stems)
        if len(common_stems) >= 3:
            print(f"[АНТИДУБЛЬ СЕНСУ]: Збіг слів {common_stems} з постом '{t.get('headline', '')[:35]}...'. ВІДХИЛЕНО!")
            return True
    return False

def register_published_topic(headline: str, text: str):
    topics = load_json(TOPICS_FILE)
    caps, stems = extract_entities_and_stems(headline + " " + text)
    topics.append({
        "headline": headline,
        "capitals": list(caps),
        "stems": list(stems),
        "timestamp": time.time()
    })
    save_json(TOPICS_FILE, topics[-250:])

def is_media_duplicate(media_bytes: bytes, media_url: str = "") -> bool:
    if not media_bytes and not media_url:
        return False
    history = load_json(MEDIA_HASH_FILE)
    c_hash = hashlib.md5(media_bytes).hexdigest() if media_bytes else ""
    u_hash = hashlib.md5(media_url.encode("utf-8")).hexdigest() if media_url else ""
    now = time.time()
    min_time = now - (24 * 3600)

    for item in history:
        if item.get("timestamp", 0) < min_time:
            continue
        if c_hash and item.get("content_hash") == c_hash:
            print("[АНТИДУБЛЬ МЕДІА]: Збіг фото/відео за цифровим відбитком! ВІДХИЛЕНО!")
            return True
        if u_hash and item.get("url_hash") == u_hash:
            print("[АНТИДУБЛЬ МЕДІА]: Збіг посилання на медіафайл! ВІДХИЛЕНО!")
            return True
    return False

def register_media(media_bytes: bytes, media_url: str = ""):
    history = load_json(MEDIA_HASH_FILE)
    c_hash = hashlib.md5(media_bytes).hexdigest() if media_bytes else ""
    u_hash = hashlib.md5(media_url.encode("utf-8")).hexdigest() if media_url else ""
    history.append({
        "content_hash": c_hash,
        "url_hash": u_hash,
        "timestamp": time.time()
    })
    save_json(MEDIA_HASH_FILE, history[-250:])

# ==================== ЧИТАННЯ ВЛАСНОГО КАНАЛУ @kropivnit ====================
def sync_channel_history(channel_username: str) -> List[str]:
    clean_name = channel_username.replace("https://t.me/", "").replace("t.me/", "").replace("@", "").strip("/")
    url = f"https://t.me/s/{clean_name}"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0 Safari/537.36"}
    recent_texts = []
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=12) as resp:
            html_raw = resp.read().decode("utf-8", errors="ignore")
        blocks = html_raw.split('<div class="tgme_widget_message_wrap')
        for b in blocks[1:][-20:]:
            m = re.search(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>\s*<div class="tgme_widget_message_footer', b, re.DOTALL)
            if not m:
                continue
            clean = html.unescape(re.sub(r"<[^>]+>", " ", m.group(1))).strip()
            if len(clean) > 20:
                head = clean.split("\n")[0][:60]
                register_published_topic(head, clean)
                recent_texts.append(clean[:120])
        print(f"[СИНХРОНІЗАЦІЯ]: Зчитано {len(recent_texts)} постів із @{clean_name}.")
    except Exception as e:
        print(f"[СИНХРОНІЗАЦІЯ]: Помилка читання @{clean_name}: {e}")
    return recent_texts

# ==================== ШІ-ОБРОБКА (АНТИФЕЙК + АНТИДУБЛЬ) ====================
SYSTEM_PROMPT = """Ти — професійний український журналіст та суворий фактчекер новинного каналу.
Твоє завдання — відредагувати новину солов'їною мовою СУВОРО ЗА РЕАЛЬНИМИ ФАКТАМИ.

СУВОРІ ПРАВИЛА:
1. ПЕРЕВІРКА НА ДУБЛІКАТ:
   - Тобі надано список останніх публікацій каналу.
   - Якщо ця сама подія ВЖЕ висвітлена (наприклад: той самий дрон на дереві, той самий блекаут у Керчі чи Сімферополі, та сама Боровська Андріївка) — встанови "is_duplicate": true!
2. КАТЕГОРИЧНА ЗАБОРОНА ВИГАДУВАТИ ЛЮДЕЙ ТА ЦИТАТИ:
   - Суворо заборонено вигадувати мерів, людей (як вигадані «Ковальчуки» чи фейкові «жителі Керчу»), посадовців чи органи влади!
   - Блок <blockquote> використовуй ТІЛЬКИ тоді, коли в тексті донора Є реальна цитата конкретної реальної особи. Якщо прямої мови в оригіналі НЕМАЄ — жодних <blockquote> НЕ СТАВ!
3. МОВА: 100% чиста українська мова.
   - Жодних «Сем'я» (тільки «Сім'я»), жодних «пожежних вибухів», «відео розповсюдився», «великого вогню».
4. ОФОРМЛЕННЯ:
   - Заголовок <b>жирним</b> з 1 емодзі. Звичайними реченнями, БЕЗ КАПСУ!
   - 1-2 короткі фактичні абзаци.
   - ЖОДНИХ підписів у кінці, жодного значка "💬".

Формат відповіді СТРОГО JSON:
{
  "is_duplicate": false,
  "is_ad": false,
  "telegram_post": "Текст посту з тегами <b>, <i> та <blockquote>",
  "news_score": 7,
  "is_mega_news": false,
  "video_headline": "Заголовок для відео",
  "video_script": "Текст озвучки"
}
"""

def process_news_with_ai(raw_text: str, recent_channel_posts: List[str]) -> Optional[Dict]:
    cleaned = html.unescape(raw_text)
    cleaned = re.sub(r"https?://\S+|t\.me/\S+|@[a-zA-Z0-9_]+", "", cleaned)
    cleaned = re.sub(r"(?i)\b(ус\s*/?|украина сейчас|инсайдер|униан|supernova|труха|нефедов)\b", "", cleaned).strip()
    if len(cleaned) < 25:
        return None

    api_key = AI_API_KEY.strip()
    if not api_key:
        print("[AI]: AI_API_KEY не налаштовано!")
        return None

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0 Safari/537.36",
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    recent_context = ""
    if recent_channel_posts:
        recent_context = "ОСЬ ОСТАННІ ПОСТИ В КАНАЛІ КОРИСТУВАЧА:\n" + "\n".join([f"- {p}" for p in recent_channel_posts[:15]]) + "\n\n"

    user_prompt = f"{recent_context}НОВА НОВИНА ДЛЯ ОБРОБКИ (перевір на дубль та оформи):\n{cleaned}"

    models_to_try = ["llama-3.1-8b-instant", "llama-3.3-70b-versatile", "gemma2-9b-it"]
    try:
        r = requests.get("https://api.groq.com/openai/v1/models", headers=headers, timeout=10)
        if r.status_code == 200:
            active = [m["id"] for m in r.json().get("data", []) if not any(x in m["id"].lower() for x in ["whisper", "guard"])]
            if active:
                models_to_try = active
    except Exception:
        pass

    for model in models_to_try:
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
            if parsed.get("is_duplicate", False):
                print("[AI ДУБЛЬ]: AI розпізнав цю подію як повтор того, що вже є в каналі. ВІДХИЛЕНО!")
                return None
            if parsed.get("is_ad", False):
                return None
            return parsed
        except Exception:
            continue
    return None

# ==================== ПУБЛІКАЦІЯ В TELEGRAM ====================
def send_telegram_post(post_text: str, photo_url: Optional[str] = None, video_url: Optional[str] = None, donor: str = "") -> bool:
    url_base = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
    caption = post_text[:1024]
    has_heavy_wm = donor.lower() in CHANNELS_WITH_HEAVY_WATERMARKS

    # 1. Якщо є відео
    if video_url:
        if is_media_duplicate(b"", video_url):
            return False
        temp_vid = os.path.join(MEDIA_DIR, "temp_vid.mp4")
        branded_vid = os.path.join(MEDIA_DIR, "branded_vid.mp4")
        try:
            r = requests.get(video_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=35, stream=True)
            if r.status_code == 200:
                with open(temp_vid, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1024*1024):
                        if chunk: f.write(chunk)

                # Замінюємо чужу вотермарку на НАШ МЕДІА-БЕЙДЖ Тільки для supernova чи exilenova
                if has_heavy_wm and os.path.exists(WATERMARK_FILE):
                    final_video = apply_watermark_to_video(temp_vid, branded_vid)
                    print(f"[Telegram]: Замінено чужий вотермарк з @{donor} на медіа-бейдж {CHANNEL_NAME}.")
                else:
                    final_video = temp_vid
                    print(f"[Telegram]: Чисте відео з @{donor}, публікуємо без вотермарки.")

                with open(final_video, "rb") as f:
                    resp = requests.post(f"{url_base}/sendVideo", data={"chat_id": TELEGRAM_CHANNEL_ID, "caption": caption, "parse_mode": "HTML"}, files={"video": ("video.mp4", f, "video/mp4")}, timeout=75)
                    if resp.json().get("ok", False):
                        register_media(b"", video_url)
                        return True
        except Exception as e:
            print(f"[Telegram]: Помилка відправки відео: {e}")

    # 2. Якщо є фото
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
                    print(f"[Telegram]: Замінено чужий вотермарк з @{donor} на медіа-бейдж {CHANNEL_NAME}.")
                else:
                    final_img = temp_img
                    print(f"[Telegram]: Чисте фото з @{donor}, публікуємо без вотермарки.")

                with open(final_img, "rb") as f:
                    resp = requests.post(f"{url_base}/sendPhoto", data={"chat_id": TELEGRAM_CHANNEL_ID, "caption": caption, "parse_mode": "HTML"}, files={"photo": ("photo.jpg", f, "image/jpeg")}, timeout=30)
                    if resp.json().get("ok", False):
                        register_media(r.content, photo_url)
                        return True
        except Exception as e:
            print(f"[Telegram]: Помилка відправки фото: {e}")

    # 3. Текст
    try:
        resp = requests.post(f"{url_base}/sendMessage", data={"chat_id": TELEGRAM_CHANNEL_ID, "text": post_text, "parse_mode": "HTML", "disable_web_page_preview": True}, timeout=20)
        return resp.json().get("ok", False)
    except Exception as e:
        print(f"[Telegram]: Помилка надсилання тексту: {e}")
        return False

# ==================== ПАРСЕР ДОНОРІВ ====================
def fetch_posts(channel: str) -> List[Dict]:
    url = f"https://t.me/s/{channel}"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0 Safari/537.36"}
    posts = []
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            html_raw = resp.read().decode("utf-8", errors="ignore")
        blocks = html_raw.split('<div class="tgme_widget_message_wrap')
        for b in blocks[1:][-3:]:
            p_id = re.search(r'data-post="([^"]+)"', b)
            if not p_id: continue
            
            t_match = re.search(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>\s*<div class="tgme_widget_message_footer', b, re.DOTALL)
            raw_text = html.unescape(re.sub(r"<[^>]+>", " ", t_match.group(1))).strip() if t_match else ""

            unescaped = html.unescape(b)
            p_match = re.search(r'background-image:\s*url\([\'"]?(https?://[^\'")\s]+)[\'"]?\)', unescaped)
            photo_url = p_match.group(1) if p_match else None
            v_match = re.search(r'<video[^>]+src=[\'"](https?://[^\'"]+)[\'"]', unescaped)
            video_url = v_match.group(1) if v_match else None

            if raw_text or photo_url or video_url:
                posts.append({
                    "channel": channel,
                    "text": raw_text,
                    "photo_url": photo_url,
                    "video_url": video_url,
                    "post_id": p_id.group(1)
                })
    except Exception as e:
        print(f"[ПАРСЕР]: Помилка читання @{channel}: {e}")
    return posts

# ==================== ГОЛОВНИЙ ЦИКЛ ====================
def run():
    print(f"\n{'='*55}")
    print(f"[{datetime.now().strftime('%H:%M:%S')}] СКАНИНГ КАНАЛІВ. Повний антидублікат + медіа-бейдж.")
    print(f"{'='*55}")

    recent_channel_posts = sync_channel_history(CHANNEL_USERNAME)

    for channel in DONOR_CHANNELS:
        posts = fetch_posts(channel)

        for p in posts:
            text = p["text"]
            photo = p["photo_url"]
            video = p["video_url"]

            if is_advertisement(text): continue
            if is_topic_already_covered(text): continue
            if video and is_media_duplicate(b"", video): continue

            ai = process_news_with_ai(text, recent_channel_posts)
            if not ai: continue

            post_text = ai.get("telegram_post", "")
            if len(post_text) < 25: continue

            posted = send_telegram_post(post_text, photo_url=photo, video_url=video, donor=channel)
            if posted:
                head = post_text.split("\n")[0]
                register_published_topic(head, text)
                print(f"✅ УСПІШНО ОПУБЛІКОВАНО: {head[:50]}...")
                print("[ЗАВЕРШЕННЯ]: Опубліковано 1 свіжий пост. Робимо паузу до наступного запуску.")
                return

    print("Немає нових якісних подій для публікації. Чекаємо наступного запуску.")

if __name__ == "__main__":
    run()
