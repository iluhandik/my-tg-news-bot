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

CHANNEL_NAME = "NEWS УКРАЇНА 365"
CHANNEL_USERNAME = TELEGRAM_CHANNEL_ID if TELEGRAM_CHANNEL_ID.startswith("@") else "@kropivnit"
CHANNEL_INVITE_LINK = os.getenv("CHANNEL_INVITE_LINK", f"https://t.me/{CHANNEL_USERNAME.lstrip('@')}").strip()

AI_API_KEY = (os.getenv("GROQ_API_KEY") or os.getenv("AI_API_KEY") or os.getenv("AI_KEY") or os.getenv("GROQ_KEY") or "").strip()

# 24 канали-донори для автономного режиму
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

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TOPICS_FILE = os.path.join(BASE_DIR, "published_topics.json")
PROCESSED_IDS_FILE = os.path.join(BASE_DIR, "processed_ids.json")

# ==================== JSON ХЕЛПЕРИ ====================
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

# ==================== ОЧИЩЕННЯ ТА КАЛЬКИ ====================
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
        r"(?i)Великий\s*геостратег[^\n]*",
        r"https?://t\.me/\S+",
        r"@[a-zA-Z0-9_]+"
    ]
    cleaned = text
    for p in patterns:
        cleaned = re.sub(p, "", cleaned)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()

def free_google_translate_to_uk(text: str) -> str:
    """Гарантований резервний перекладач на солов'їну, якщо API Groq дало збій."""
    try:
        url = "https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl=uk&dt=t&q=" + urllib.parse.quote(text)
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0 Safari/537.36"}
        r = requests.get(url, headers=headers, timeout=8)
        if r.status_code == 200:
            data = r.json()
            translated = "".join([part[0] for part in data[0] if part[0]])
            return translated
    except Exception:
        pass
    return text

# ==================== АНТИДУБЛІКАТ ====================
def extract_entities_and_stems(text: str) -> Tuple[Set[str], Set[str]]:
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
    min_time = now - (36 * 3600)

    for t in topics:
        if t.get("timestamp", 0) < min_time:
            continue
        prev_caps = set(t.get("capitals", []))
        prev_stems = set(t.get("stems", []))

        common_caps = cur_caps.intersection(prev_caps)
        if len(common_caps) >= 2:
            return True

        common_stems = cur_stems.intersection(prev_stems)
        if len(common_stems) >= 4:
            return True

        if cur_stems and prev_stems:
            overlap = len(common_stems) / len(cur_stems.union(prev_stems))
            if overlap >= 0.25:
                return True

    return False

def register_published_topic(headline: str, text: str):
    topics = load_json(TOPICS_FILE)
    caps, stems = extract_entities_and_stems(headline + " " + text)
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

# ==================== СИНХРОНІЗАЦІЯ КАНАЛУ ====================
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
    except Exception as e:
        print(f"[СИНХРОНІЗАЦІЯ]: Помилка: {e}")

    return recent_texts

# ==================== РЕРАЙТ ДЛЯ ВАШИХ РУЧНИХ ПЕРЕСИЛАНЬ ====================
def process_admin_post_with_ai(raw_text: str) -> str:
    cleaned = strip_foreign_signatures(raw_text)
    if not cleaned: return ""

    prompt = f"""Ти — професійний український журналіст. Твоє завдання — перекласти та стильно оформити новину ЧИСТОЮ СОЛОВ'ЇНОЮ УКРАЇНСЬКОЮ МОВОЮ для Telegram-каналу.
Вимоги:
- Жодного слова російською!
- Перший рядок: лаконічний заголовок <b>жирним</b> з 1 тематичним емодзі.
- Далі: 1-2 чіткі абзаци за фактами. Прізвища, цифри та назви виділяй <b>жирним</b>, пряму мову — в <blockquote>.
- Жодних підписів каналу.

Текст новини:
{cleaned}

Відповідай ВИКЛЮЧНО валідним JSON:
{{"telegram_post": "Текст новини українською мовою з тегами <b> та <blockquote>"}}"""

    if AI_API_KEY:
        models = ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"]
        for model in models:
            try:
                resp = requests.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": prompt}],
                        "response_format": {"type": "json_object"},
                        "temperature": 0.2
                    },
                    headers={"Authorization": f"Bearer {AI_API_KEY}", "Content-Type": "application/json"},
                    timeout=25
                )
                if resp.status_code == 200:
                    data = resp.json()
                    res_text = json.loads(data["choices"][0]["message"]["content"]).get("telegram_post", "")
                    if res_text and not re.search(r"[ыэъё]", res_text, re.IGNORECASE):
                        return fix_ukrainian_calques(res_text)
            except Exception:
                continue

    # Резервний автопереклад, якщо ШІ був недоступний
    translated = free_google_translate_to_uk(cleaned)
    lines = [l.strip() for l in translated.splitlines() if l.strip()]
    if lines:
        head = f"⚡ <b>{lines[0]}</b>"
        body = "\n\n".join(lines[1:])
        return fix_ukrainian_calques(f"{head}\n\n{body}" if body else head)
    return fix_ukrainian_calques(translated)

# ==================== GROQ AI ДЛЯ АВТОНОМНОГО СКАНЕРА ====================
SYSTEM_PROMPT = """Ти — головний редактор провідного українського новинного каналу.
Твоє завдання — перекласти та оформити новину ЧИСТОЮ СОЛОВ'ЇНОЮ МОВОЮ без води, емоцій та маніпуляцій.

ПРАВИЛА:
1. АНТИДУБЛІКАТ: Якщо ця подія, заява чи звіт ВЖЕ описані в списку публікацій каналу — поверни "is_duplicate": true!
2. Жодних тривог, сирен, польотів дронів без влучань/збиття (це рутина, став "is_ad": true).
3. Оформлення:
   - Перший рядок: лаконічний заголовок <b>жирним</b> з 1 тематичним емодзі.
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
                    post_res = fix_ukrainian_calques(parsed["telegram_post"])
                    if not re.search(r"[ыэъё]", post_res, re.IGNORECASE):
                        parsed["telegram_post"] = post_res
                        return parsed
        except Exception:
            continue
    return None

# ==================== ВІДПРАВКА В TELEGRAM ====================
def send_admin_reply(chat_id: int, text: str):
    if not TELEGRAM_BOT_TOKEN or not chat_id: return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=10
        )
    except Exception:
        pass

def download_tg_file(file_id: str) -> Optional[bytes]:
    try:
        r = requests.get(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getFile?file_id={file_id}", timeout=10).json()
        if r.get("ok"):
            f_path = r["result"]["file_path"]
            dl_url = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{f_path}"
            dl_resp = requests.get(dl_url, timeout=40)
            if dl_resp.status_code == 200:
                return dl_resp.content
    except Exception as e:
        print(f"[TG GETFILE]: {e}")
    return None

def extract_media_from_embed(channel_username: str, msg_id: int) -> Tuple[Optional[bytes], Optional[str]]:
    try:
        url = f"https://t.me/{channel_username}/{msg_id}?embed=1"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0 Safari/537.36"}
        r = requests.get(url, headers=headers, timeout=12)
        if r.status_code == 200:
            v_match = re.search(r'<video[^>]+src="([^"]+)"', r.text)
            if v_match:
                vid_url = v_match.group(1)
                v_resp = requests.get(vid_url, headers=headers, timeout=45)
                if v_resp.status_code == 200 and len(v_resp.content) > 1000:
                    return v_resp.content, "video"
            p_match = re.search(r'background-image:\s*url\([\'"]?(https?://[^\'")\s]+)[\'"]?\)', r.text)
            if p_match:
                img_url = p_match.group(1)
                p_resp = requests.get(img_url, headers=headers, timeout=20)
                if p_resp.status_code == 200 and len(p_resp.content) > 5000:
                    return p_resp.content, "photo"
    except Exception as e:
        print(f"[EMBED EXTRACT]: {e}")
    return None, None

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
        print(f"[TELEGRAM SEND]: {e}")
        return False

# ==================== VIP ADMIN ОБРОБКА ====================
album_buffer = {}
album_lock = threading.Lock()

def process_vip_album(media_group_id: str):
    time.sleep(2.0)
    with album_lock:
        data = album_buffer.pop(media_group_id, None)

    if not data or not data["items"]: return
    chat_id = data["chat_id"]
    caption = data.get("caption", "")

    final_caption = process_admin_post_with_ai(caption) if caption else ""
    final_text = f"{final_caption}\n\n👉 <a href=\"{CHANNEL_INVITE_LINK}\">{CHANNEL_NAME} | Підписатися</a>" if final_caption else f"👉 <a href=\"{CHANNEL_INVITE_LINK}\">{CHANNEL_NAME} | Підписатися</a>"

    media_group = []
    files = {}
    for i, it in enumerate(data["items"]):
        m_type = it["type"]
        f_name = f"file_{i}.{'mp4' if m_type == 'video' else 'jpg'}"
        mime = "video/mp4" if m_type == "video" else "image/jpeg"
        files[f_name] = (f_name, it["bytes"], mime)
        entry = {"type": m_type, "media": f"attach://{f_name}"}
        if i == 0:
            entry["caption"] = final_text
            entry["parse_mode"] = "HTML"
        media_group.append(entry)

    res = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMediaGroup",
        data={"chat_id": TELEGRAM_CHANNEL_ID, "media": json.dumps(media_group)},
        files=files,
        timeout=60
    ).json()

    if res.get("ok"):
        head = final_caption.splitlines()[0][:70] if final_caption else "Альбом"
        register_published_topic(head, final_caption)
        send_admin_reply(chat_id, f"🚀 Успішно опубліковано альбом у {TELEGRAM_CHANNEL_ID}!")
    else:
        send_admin_reply(chat_id, "❌ Помилка надсилання альбому в канал.")

def publish_single_media_post(chat_id: int, caption: str, media_bytes: Optional[bytes], media_type: str, fwd_channel: str = None, fwd_msg_id: int = None):
    if not media_bytes and fwd_channel and fwd_msg_id:
        media_bytes, detected_type = extract_media_from_embed(fwd_channel, fwd_msg_id)
        if detected_type:
            media_type = detected_type

    if not media_bytes:
        send_admin_reply(chat_id, "❌ Не вдалося отримати медіафайл.")
        return

    final_caption = process_admin_post_with_ai(caption) if caption else ""
    final_text = f"{final_caption}\n\n👉 <a href=\"{CHANNEL_INVITE_LINK}\">{CHANNEL_NAME} | Підписатися</a>" if final_caption else f"👉 <a href=\"{CHANNEL_INVITE_LINK}\">{CHANNEL_NAME} | Підписатися</a>"

    success = False
    if media_type == "video":
        files = {"video": ("video.mp4", media_bytes, "video/mp4")}
        data = {"chat_id": TELEGRAM_CHANNEL_ID, "caption": final_text, "parse_mode": "HTML"}
        res = requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendVideo", data=data, files=files, timeout=60).json()
        success = res.get("ok", False)
    else:
        files = {"photo": ("photo.jpg", media_bytes, "image/jpeg")}
        data = {"chat_id": TELEGRAM_CHANNEL_ID, "caption": final_text, "parse_mode": "HTML"}
        res = requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto", data=data, files=files, timeout=35).json()
        success = res.get("ok", False)

    if success:
        head = final_caption.splitlines()[0][:70] if final_caption else "Пост"
        register_published_topic(head, final_caption)
        send_admin_reply(chat_id, f"🚀 Успішно опубліковано у {TELEGRAM_CHANNEL_ID}!")
    else:
        send_admin_reply(chat_id, "❌ Помилка публікації в канал.")

def publish_text_post(chat_id: int, text: str):
    final_text = process_admin_post_with_ai(text)
    full_post = f"{final_text}\n\n👉 <a href=\"{CHANNEL_INVITE_LINK}\">{CHANNEL_NAME} | Підписатися</a>"

    res = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        json={"chat_id": TELEGRAM_CHANNEL_ID, "text": full_post, "parse_mode": "HTML", "disable_web_page_preview": False},
        timeout=15
    ).json()

    if res.get("ok"):
        head = final_text.splitlines()[0][:70]
        register_published_topic(head, final_text)
        send_admin_reply(chat_id, f"🚀 Успішно опубліковано у {TELEGRAM_CHANNEL_ID}!")
    else:
        send_admin_reply(chat_id, "❌ Помилка публікації в канал.")

def handle_admin_message(msg: dict):
    chat_id = msg.get("chat", {}).get("id")
    if not chat_id: return

    mg_id = msg.get("media_group_id")
    caption = msg.get("caption") or msg.get("text") or ""

    fwd_chat = msg.get("forward_from_chat", {})
    fwd_channel = fwd_chat.get("username")
    fwd_msg_id = msg.get("forward_from_message_id")

    if mg_id:
        file_id, m_type = None, "photo"
        if msg.get("photo"):
            file_id = msg["photo"][-1]["file_id"]
            m_type = "photo"
        elif msg.get("video"):
            file_id = msg["video"]["file_id"]
            m_type = "video"

        f_bytes = None
        if file_id:
            f_bytes = download_tg_file(file_id)
        if not f_bytes and fwd_channel and fwd_msg_id:
            f_bytes, detected_type = extract_media_from_embed(fwd_channel, fwd_msg_id)
            if detected_type: m_type = detected_type

        if f_bytes:
            with album_lock:
                is_first = mg_id not in album_buffer
                if is_first:
                    album_buffer[mg_id] = {"items": [], "chat_id": chat_id, "caption": caption}
                    send_admin_reply(chat_id, "⏳ Збираю альбом і перекладаю українською...")
                    threading.Thread(target=process_vip_album, args=(mg_id,), daemon=True).start()

                if caption and not album_buffer[mg_id]["caption"]:
                    album_buffer[mg_id]["caption"] = caption

                album_buffer[mg_id]["items"].append({"type": m_type, "bytes": f_bytes})
        return

    if msg.get("photo"):
        send_admin_reply(chat_id, "⏳ Перекладаю та публікую пост у канал...")
        file_id = msg["photo"][-1]["file_id"]
        f_bytes = download_tg_file(file_id)
        publish_single_media_post(chat_id, caption, f_bytes, "photo", fwd_channel, fwd_msg_id)
        return

    if msg.get("video"):
        send_admin_reply(chat_id, "⏳ Перекладаю та публікую відео у канал...")
        file_id = msg["video"]["file_id"]
        f_bytes = download_tg_file(file_id)
        publish_single_media_post(chat_id, caption, f_bytes, "video", fwd_channel, fwd_msg_id)
        return

    if caption:
        send_admin_reply(chat_id, "⏳ Перекладаю новину на чисту солов'їну...")
        publish_text_post(chat_id, caption)
        return

# ==================== WEBHOOK СЕРВЕР ====================
class WebhookHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_len = int(self.headers.get("Content-Length", 0))
        post_data = self.rfile.read(content_len)
        try:
            update = json.loads(post_data.decode("utf-8"))

            if "channel_post" in update:
                cp = update["channel_post"]
                t = cp.get("text") or cp.get("caption") or ""
                if t:
                    register_published_topic(t.splitlines()[0][:70], t)

            msg = update.get("message")
            if msg and msg.get("chat", {}).get("type") == "private":
                threading.Thread(target=handle_admin_message, args=(msg,), daemon=True).start()
        except Exception:
            pass

        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

def start_webhook_server():
    port = int(os.getenv("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), WebhookHandler)
    server.serve_forever()

# ==================== АВТОНОМНИЙ СКАНЕР ====================
def fetch_posts(channel: str) -> List[Dict]:
    url = f"https://t.me/s/{channel}"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0 Safari/537.36"}
    posts = []
    try:
        r = requests.get(url, headers=headers, timeout=12)
        if r.status_code != 200:
            return posts

        blocks = r.text.split('<div class="tgme_widget_message_wrap')
        for b in blocks[1:][-5:]:
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

    posts.reverse()
    return posts

def run_scanner_loop():
    print("[АВТОНОМНІСТЬ]: Автономний сканер запущено успішно!")
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
                        print(f"✅ [АВТОНОМНІСТЬ]: Опубліковано з @{ch}: {head[:45]}...")
                        time.sleep(DELAY_BETWEEN_POSTS)

        except Exception as e:
            print(f"[СКАНЕР]: Помилка: {e}")

        time.sleep(20)

# ==================== ЗАПУСК ====================
if __name__ == "__main__":
    if TELEGRAM_BOT_TOKEN:
        render_host = os.getenv("RENDER_EXTERNAL_HOSTNAME") or os.getenv("RENDER_EXTERNAL_URL") or "my-tg-news-bot-53bh.onrender.com"
        render_url = render_host if render_host.startswith("http") else f"https://{render_host}"
        webhook_url = f"{render_url.rstrip('/')}/tg-webhook"
        print(f"[WEBHOOK]: Реєстрація: {webhook_url}")
        try:
            res = requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/setWebhook",
                json={"url": webhook_url, "allowed_updates": ["message", "channel_post", "edited_channel_post"], "drop_pending_updates": False},
                timeout=15
            ).json()
            print(f"[WEBHOOK STATUS]: {res}")
        except Exception as e:
            print(f"[WEBHOOK ERROR]: {e}")

    # 1. Запуск автономного сканера у фоновому потоці
    t_scanner = threading.Thread(target=run_scanner_loop, daemon=True)
    t_scanner.start()

    # 2. Запуск вебхука для миттєвих пересилань від вас
    start_webhook_server()
