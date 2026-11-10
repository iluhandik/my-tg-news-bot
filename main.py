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
from datetime import datetime, timezone
from typing import List, Dict, Optional, Tuple, Set
import requests
from dotenv import load_dotenv
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading

load_dotenv()

# ==================== НАЛАШТУВАННЯ ====================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID", "@kropivnit").strip()
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID", "").strip()
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL", "").strip()

CHANNEL_NAME = "NEWS УКРАЇНА 365"
CHANNEL_USERNAME = TELEGRAM_CHANNEL_ID if TELEGRAM_CHANNEL_ID.startswith("@") else "@kropivnit"
CHANNEL_INVITE_LINK = os.getenv("CHANNEL_INVITE_LINK", f"https://t.me/{CHANNEL_USERNAME.lstrip('@')}").strip()

AI_API_KEY = (os.getenv("GROQ_API_KEY") or os.getenv("AI_API_KEY", "")).strip()

# 24 елітні українські донори новин
DONOR_CHANNELS = [
    "u_now",
    "uniannet",
    "insiderUKR",
    "truexanewsua",
    "kievreal1",
    "nefedoov365",
    "lachentyt",
    "voynareal",
    "RBC_ua_news",
    "suspilnenews",
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

MAX_POSTS_PER_RUN = 3
DELAY_BETWEEN_POSTS = 25
MAX_POST_AGE_HOURS = 4.0

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TOPICS_FILE = os.path.join(BASE_DIR, "published_topics.json")
PROCESSED_IDS_FILE = os.path.join(BASE_DIR, "processed_ids.json")
MEDIA_HASH_FILE = os.path.join(BASE_DIR, "media_hashes.json")

# ==================== ДОПОМІЖНІ ФУНКЦІЇ ДЛЯ JSON ====================
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

# ==================== ОЧИЩЕННЯ ТА АНТИКАЛЬКА ====================
COMMON_STOP_WORDS = {
    "сьогодні", "зараз", "вчора", "цього", "після", "також", "було", "проти", "може",
    "через", "буде", "тільки", "цьому", "коли", "якщо", "були", "вони", "яких", "який",
    "яка", "яке", "повідомляє", "новини", "року", "потім", "перед", "під", "над", "щоб",
    "стало", "відомо", "заявив", "словами", "джерело", "офіційно", "увага", "терміново",
    "внаслідок", "відповідно", "разом", "перший", "другий", "можуть", "бути", "своїх"
}

def fix_ukrainian_calques(text: str) -> str:
    text = re.sub(r"\bвсу\b", "ЗСУ", text, flags=re.IGNORECASE)
    text = re.sub(r"\bвійськові сили україни\b", "Збройні Сили України", text, flags=re.IGNORECASE)
    text = re.sub(r"\bзапорі[зс]хсталь\b", "Запоріжсталь", text, flags=re.IGNORECASE)
    text = re.sub(r"\bбалл[іи]стич([а-яіїєґ]*)\b", r"балістич\1", text, flags=re.IGNORECASE)
    text = re.sub(r"\bбаллістик([а-яіїєґ]*)\b", r"балістик\1", text, flags=re.IGNORECASE)
    text = re.sub(r"—\s*зазначив\s*\.", "", text)
    text = re.sub(r"—\s*наголосив\s*\.", "", text)
    return text

def strip_foreign_signatures(text: str) -> str:
    patterns = [
        r"(?i)(?:👉\s*)?(?:подписаться|підписатися|підписатись|присоединиться|стежити|читати|джерело)\s*[:\-—]?\s*(?:https?://\S+|t\.me/\S+|@[a-zA-Z0-9_]+|[^\n]+)?",
        r"(?i)\b(?:Украина\s*Сейчас|Україна\s*Online|Труха|INSIDER\s*UA|УНІАН|ТСН|RBC|Суспільне|Бабель|Supernova|Exilenova|Лачен|WarLife|Оперативний\s*ЗСУ)\b[^\n]*",
        r"(?i)Фото\s*[:\-—]\s*[^\n]+",
        r"https?://t\.me/\S+",
        r"@[a-zA-Z0-9_]+"
    ]
    cleaned = text
    for p in patterns:
        cleaned = re.sub(p, "", cleaned)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()

# ==================== АНТИДУБЛІКАТ ====================
def extract_entities_and_stems(text: str) -> Tuple[Set[str], Set[str]]:
    # Власні назви та абревіатури
    caps_raw = set(re.findall(r"\b[А-ЯІЇЄҐA-Z]{2,}\b|\b[А-ЯІЇЄҐA-Z][а-яіїєґa-z]{2,}\b", text))
    capitals = set()
    for c in caps_raw:
        cl = c.lower()
        if cl not in COMMON_STOP_WORDS and len(cl) >= 3:
            capitals.add(cl[:5] if len(cl) >= 5 else cl)

    clean = re.sub(r"[^\w\s]", " ", text.lower(), flags=re.UNICODE)
    stems = set()
    for w in clean.split():
        if len(w) >= 4 and w not in COMMON_STOP_WORDS:
            stems.add(w[:5] if len(w) >= 5 else w)

    return capitals, stems

def is_topic_already_covered(text: str) -> bool:
    topics = load_json(TOPICS_FILE)
    cur_caps, cur_stems = extract_entities_and_stems(text)
    now = time.time()
    min_time = now - (36 * 3600)  # перевірка за останні 36 годин

    for t in topics:
        if t.get("timestamp", 0) < min_time:
            continue
        prev_caps = set(t.get("capitals", []))
        prev_stems = set(t.get("stems", []))

        # 1. Збіг 2 географічних або власних назв
        common_caps = cur_caps.intersection(prev_caps)
        if len(common_caps) >= 2:
            print(f"[АНТИДУБЛЬ ГЕО]: Збіг сутностей {common_caps} з постом '{t.get('headline', '')[:35]}...'. ВІДХИЛЕНО!")
            return True

        # 2. Збіг 4+ значущих слів-коренів
        common_stems = cur_stems.intersection(prev_stems)
        if len(common_stems) >= 4:
            print(f"[АНТИДУБЛЬ СЕНСУ]: Збіг слів {common_stems} з постом '{t.get('headline', '')[:35]}...'. ВІДХИЛЕНО!")
            return True

        # 3. Лексична схожість за Жаккаром
        if cur_stems and prev_stems:
            overlap = len(common_stems) / len(cur_stems.union(prev_stems))
            if overlap >= 0.25:
                print(f"[АНТИДУБЛЬ JACCARD]: Схожість {overlap:.2f} з постом '{t.get('headline', '')[:35]}...'. ВІДХИЛЕНО!")
                return True

    return False

def register_published_topic(headline: str, text: str):
    topics = load_json(TOPICS_FILE)
    caps, stems = extract_entities_and_stems(headline + " " + text)
    # Перевірка на дублікат самого запису в списку
    for item in topics[-50:]:
        if item.get("headline") == headline:
            return
    topics.append({
        "headline": headline,
        "capitals": list(caps),
        "stems": list(stems),
        "timestamp": time.time()
    })
    save_json(TOPICS_FILE, topics[-400:])

def register_post_id(post_id: str):
    if not post_id: return
    ids = load_json(PROCESSED_IDS_FILE)
    if post_id not in ids:
        ids.append(post_id)
        save_json(PROCESSED_IDS_FILE, ids[-600:])

def is_post_id_processed(post_id: str) -> bool:
    ids = set(load_json(PROCESSED_IDS_FILE))
    return post_id in ids

# ==================== СИНХРОНІЗАЦІЯ З КАНАЛОМ ====================
def sync_channel_history(channel_username: str) -> List[str]:
    clean_name = channel_username.replace("https://t.me/", "").replace("t.me/", "").replace("@", "").strip("/")
    recent_texts = []
    seen = set()

    if not clean_name:
        return recent_texts

    url = f"https://t.me/s/{clean_name}"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0 Safari/537.36"}
    try:
        r = requests.get(url, headers=headers, timeout=12)
        if r.status_code == 200:
            blocks = r.text.split('<div class="tgme_widget_message_wrap')
            for b in blocks[1:][-40:]:
                m = re.search(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', b, re.DOTALL)
                if not m:
                    continue
                clean = html.unescape(re.sub(r"<[^>]+>", " ", m.group(1))).strip()
                clean = re.sub(r"\s+", " ", clean)
                if len(clean) > 20 and clean not in seen:
                    seen.add(clean)
                    head = clean.splitlines()[0][:70]
                    register_published_topic(head, clean)
                    recent_texts.insert(0, head)
            print(f"[СИНХРОНІЗАЦІЯ]: Успішно зчитано {len(seen)} свіжих постів з @{clean_name}.")
    except Exception as e:
        print(f"[СИНХРОНІЗАЦІЯ]: Помилка читання @{clean_name}: {e}")

    return recent_texts

# ==================== GROQ AI РЕРАЙТ ====================
SYSTEM_PROMPT = """Ти — головний редактор провідного українського новинного каналу.
Твоє завдання — перекласти та оформити новину ЧИСТОЮ СОЛОВ'ЇНОЮ МОВОЮ без води, емоцій та маніпуляцій.

ПРАВИЛА:
1. АНТИДУБЛІКАТ: Якщо ця подія, заява чи звіт ВЖЕ описані в списку публікацій каналу (навіть іншими словами або з новими цитатами) — негайно поверни "is_duplicate": true!
2. Жодних тривог, сирен, польотів дронів без влучань/збиття (це рутина, став "is_ad": true).
3. Жодних неперевірених чуток та чорного піару.
4. Оформлення:
   - Перший рядок: лаконічний заголовок <b>жирним</b> з 1 тематичним емодзі на початку.
   - 1-2 чіткі абзаци з головними фактами.
   - Важливі акценти (прізвища, назви, цифри) виділяй <b>жирним</b>. Пряму мову — в <blockquote>.

Відповідай ВИКЛЮЧНО валідним JSON:
{
  "is_duplicate": false,
  "is_ad": false,
  "telegram_post": "Текст новини з тегами <b> та <blockquote>"
}
"""

def process_news_with_ai(raw_text: str, recent_channel_posts: List[str]) -> Optional[Dict]:
    cleaned = strip_foreign_signatures(raw_text)
    if len(cleaned) < 25 or not AI_API_KEY:
        return None

    recent_context = ""
    if recent_channel_posts:
        recent_context = "ОСЬ ОСТАННІ ОПУБЛІКОВАНІ ПОСТИ КАНАЛУ (НЕ ПОВТОРЮЙ ЇХ):\n" + "\n".join([f"- {p}" for p in recent_channel_posts[:35]]) + "\n\n"

    user_prompt = f"{recent_context}НОВА ІНФОРМАЦІЯ ДЛЯ ОБРОБКИ:\n{cleaned}"

    models = ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"]
    for model in models:
        try:
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt}
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0.1
            }
            resp = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {AI_API_KEY}", "Content-Type": "application/json"},
                timeout=25
            )
            if resp.status_code == 200:
                parsed = json.loads(resp.json()["choices"][0]["message"]["content"])
                if parsed.get("is_duplicate") or parsed.get("is_ad"):
                    return None
                if "telegram_post" in parsed:
                    parsed["telegram_post"] = fix_ukrainian_calques(parsed["telegram_post"])
                    return parsed
        except Exception:
            continue
    return None

# ==================== ПУБЛІКАЦІЯ В TELEGRAM ====================
def send_telegram_post(text: str, photo_url: str = None, video_url: str = None) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHANNEL_ID:
        return False

    final_text = f"{text}\n\n👉 <a href=\"{CHANNEL_INVITE_LINK}\">{CHANNEL_NAME} | Підписатися</a>"

    try:
        if video_url:
            r_vid = requests.get(video_url, timeout=30)
            if r_vid.status_code == 200:
                files = {"video": ("video.mp4", r_vid.content, "video/mp4")}
                data = {"chat_id": TELEGRAM_CHANNEL_ID, "caption": final_text, "parse_mode": "HTML"}
                res = requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendVideo", data=data, files=files, timeout=60).json()
                if res.get("ok"): return True

        if photo_url:
            r_img = requests.get(photo_url, timeout=15)
            if r_img.status_code == 200:
                files = {"photo": ("photo.jpg", r_img.content, "image/jpeg")}
                data = {"chat_id": TELEGRAM_CHANNEL_ID, "caption": final_text, "parse_mode": "HTML"}
                res = requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto", data=data, files=files, timeout=30).json()
                if res.get("ok"): return True

        res = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHANNEL_ID, "text": final_text, "parse_mode": "HTML", "disable_web_page_preview": False},
            timeout=15
        ).json()
        return res.get("ok", False)
    except Exception as e:
        print(f"[TELEGRAM SEND]: Помилка відправки: {e}")
        return False

# ==================== ПАРСИНГ ДОНОРІВ ====================
def fetch_posts(channel: str) -> List[Dict]:
    url = f"https://t.me/s/{channel}"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0 Safari/537.36"}
    posts = []
    try:
        r = requests.get(url, headers=headers, timeout=12)
        if r.status_code != 200:
            return posts

        blocks = r.text.split('<div class="tgme_widget_message_wrap')
        for b in blocks[1:][-4:]:
            p_id = re.search(r'data-post="([^"]+)"', b)
            if not p_id: continue
            post_id = p_id.group(1)

            if is_post_id_processed(post_id):
                continue

            t_match = re.search(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', b, re.DOTALL)
            raw_text = html.unescape(re.sub(r"<[^>]+>", " ", t_match.group(1))).strip() if t_match else ""

            p_match = re.search(r'background-image:\s*url\([\'"]?(https?://[^\'")\s]+)[\'"]?\)', b)
            photo_url = p_match.group(1) if p_match else None

            v_match = re.search(r'<video[^>]+src="([^"]+)"', b)
            video_url = v_match.group(1) if v_match else None

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

# ==================== VIP ADMIN WEBHOOK & АЛЬБОМИ ====================
album_buffer = {}
album_lock = threading.Lock()

def process_vip_album(media_group_id: str):
    time.sleep(2.0)
    with album_lock:
        items = album_buffer.pop(media_group_id, [])

    if not items: return
    caption = ""
    for it in items:
        if it.get("caption"):
            caption = it["caption"]
            break

    ai = process_news_with_ai(caption, sync_channel_history(CHANNEL_USERNAME)) if caption else None
    final_caption = ai.get("telegram_post", caption) if ai else strip_foreign_signatures(caption)
    final_text = f"{final_caption}\n\n👉 <a href=\"{CHANNEL_INVITE_LINK}\">{CHANNEL_NAME} | Підписатися</a>"

    media_group = []
    files = {}
    for i, it in enumerate(items):
        m_type = it["type"]
        f_bytes = it["bytes"]
        f_name = f"file_{i}.{'mp4' if m_type == 'video' else 'jpg'}"
        files[f_name] = f_bytes
        entry = {"type": m_type, "media": f"attach://{f_name}"}
        if i == 0:
            entry["caption"] = final_text
            entry["parse_mode"] = "HTML"
        media_group.append(entry)

    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMediaGroup",
        data={"chat_id": TELEGRAM_CHANNEL_ID, "media": json.dumps(media_group)},
        files=files,
        timeout=60
    )

class WebhookHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_len = int(self.headers.get("Content-Length", 0))
        post_data = self.rfile.read(content_len)
        try:
            update = json.loads(post_data.decode("utf-8"))
            # 1. Фіксуємо публікації з каналу, щоб вони миттєво ставали антидублікатами
            if "channel_post" in update:
                cp = update["channel_post"]
                t = cp.get("text") or cp.get("caption") or ""
                if t:
                    register_published_topic(t.splitlines()[0][:70], t)

            # 2. Пересилання від адміна
            msg = update.get("message")
            if msg:
                mg_id = msg.get("media_group_id")
                caption = msg.get("caption") or msg.get("text") or ""
                # Обробка фото/відео
                file_id = None
                m_type = "photo"
                if msg.get("photo"):
                    file_id = msg["photo"][-1]["file_id"]
                    m_type = "photo"
                elif msg.get("video"):
                    file_id = msg["video"]["file_id"]
                    m_type = "video"

                if file_id and mg_id:
                    f_info = requests.get(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getFile?file_id={file_id}").json()
                    if f_info.get("ok"):
                        f_path = f_info["result"]["file_path"]
                        f_bytes = requests.get(f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{f_path}").content
                        with album_lock:
                            if mg_id not in album_buffer:
                                album_buffer[mg_id] = []
                                threading.Thread(target=process_vip_album, args=(mg_id,), daemon=True).start()
                            album_buffer[mg_id].append({"type": m_type, "bytes": f_bytes, "caption": caption})
        except Exception:
            pass

        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

def start_webhook_server():
    server = HTTPServer(("0.0.0.0", 10000), WebhookHandler)
    server.serve_forever()

# ==================== ГОЛОВНИЙ ЦИКЛ СКАНУВАННЯ ====================
def run_scanner_loop():
    while True:
        try:
            recent_posts = sync_channel_history(CHANNEL_USERNAME)
            published = 0

            for ch in DONOR_CHANNELS:
                if published >= MAX_POSTS_PER_RUN:
                    break

                posts = fetch_posts(ch)
                for p in posts:
                    if published >= MAX_POSTS_PER_RUN:
                        break

                    text = p["text"]
                    p_id = p["post_id"]

                    if is_topic_already_covered(text):
                        register_post_id(p_id)
                        continue

                    ai = process_news_with_ai(text, recent_posts)
                    if not ai:
                        register_post_id(p_id)
                        continue

                    post_text = ai.get("telegram_post", "")
                    if len(post_text) < 25 or is_topic_already_covered(post_text):
                        register_post_id(p_id)
                        continue

                    if send_telegram_post(post_text, p["photo_url"], p["video_url"]):
                        published += 1
                        head = post_text.splitlines()[0][:70]
                        register_published_topic(head, post_text)
                        register_post_id(p_id)
                        recent_posts.insert(0, head)
                        time.sleep(DELAY_BETWEEN_POSTS)

        except Exception as e:
            print(f"[ЦИКЛ]: Помилка: {e}")

        time.sleep(20)

if __name__ == "__main__":
    if TELEGRAM_BOT_TOKEN and RENDER_EXTERNAL_URL:
        webhook_url = f"{RENDER_EXTERNAL_URL.rstrip('/')}/tg-webhook"
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/setWebhook",
            json={"url": webhook_url, "allowed_updates": ["message", "channel_post", "edited_channel_post"]}
        )

    t_web = threading.Thread(target=start_webhook_server, daemon=True)
    t_web.start()

    run_scanner_loop()
