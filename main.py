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

sys.stdout.reconfigure(line_buffering=True)
load_dotenv()

# ==================== НАЛАШТУВАННЯ ====================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID", "@kropivnit").strip()
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID", "").strip()

RENDER_HOST = os.getenv("RENDER_EXTERNAL_HOSTNAME") or os.getenv("RENDER_EXTERNAL_URL") or "my-tg-news-bot-53bh.onrender.com"
RENDER_URL = RENDER_HOST if RENDER_HOST.startswith("http") else f"https://{RENDER_HOST}"

CHANNEL_NAME = "NEWS УКРАЇНА 365"
CHANNEL_USERNAME = TELEGRAM_CHANNEL_ID if TELEGRAM_CHANNEL_ID.startswith("@") else "@kropivnit"
CHANNEL_INVITE_LINK = os.getenv("CHANNEL_INVITE_LINK", f"https://t.me/{CHANNEL_USERNAME.lstrip('@')}").strip()

AI_API_KEY = (os.getenv("GROQ_API_KEY") or os.getenv("AI_API_KEY") or os.getenv("AI_KEY") or os.getenv("GROQ_KEY") or "").strip().strip('"').strip("'")

MAX_POSTS_PER_RUN = 2
DELAY_BETWEEN_POSTS = 25

# 23 перевірені канали-донори
DONOR_CHANNELS = [
    "u_now",
    "uniannet",
    "insiderUKR",
    "truexanewsua",
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

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TOPICS_FILE = os.path.join(BASE_DIR, "published_topics.json")

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

# ==================== ПРОСТІ ФІЛЬТРИ ====================
def is_routine_siren(text: str) -> bool:
    t = text.lower()
    has_siren = any(w in t for w in ["оголошена тривога", "оголошено тривогу", "повітряна тривога", "відбій тривоги", "відбій повітряної", "карта тривог"])
    has_impact = any(w in t for w in ["вибух", "прильот", "влучан", "збит", "пожеж", "руйнув", "постражд", "загинул", "уражен", "наслідк", "пошкодж"])
    return has_siren and not has_impact

def is_ad(text: str) -> bool:
    t = text.lower()
    if "#реклама" in t or "#промо" in t or "#ad" in t:
        return True
    if any(w in t for w in ["знахідки з", "товари з китаю", "aliexpress", "wildberries", "маркетплейс"]):
        return True
    return False

def fix_ukrainian_calques(text: str) -> str:
    text = re.sub(r"\bвсу\b", "ЗСУ", text, flags=re.IGNORECASE)
    text = re.sub(r"\bвійськові сили україни\b", "Збройні Сили України", text, flags=re.IGNORECASE)
    text = re.sub(r"\bзапорі[зс]хсталь\b", "Запоріжсталь", text, flags=re.IGNORECASE)
    text = re.sub(r"—\s*зазначив\s*\.", "", text)
    text = re.sub(r"—\s*наголосив\s*\.", "", text)
    return text

def strip_foreign_signatures(text: str) -> str:
    patterns = [
        r"(?i)(?:👉\s*)?(?:подписаться|підписатися|підписатись|присоединиться|стежити|читати|джерело)\s*[:\-—]?\s*(?:https?://\S+|t\.me/\S+|@[a-zA-Z0-9_]+|[^\n]+)?",
        r"(?i)\b(?:Украина\s*Сейчас|Україна\s*Online|Труха|INSIDER\s*UA|УНІАН|ТСН|RBC|Суспільне|Бабель|Supernova|Exilenova|Лачен|WarLife|Оперативний\s*ЗСУ)\b[^\n]*",
        r"(?i)Фото\s*[:\-—]\s*[^\n]+",
        r"(?i)\b(?:бачити менше|читати далі|подробиці за посиланням)\b[^\n]*",
        r"(?i)(?:ексклюзивно для|спеціально для)\s*(?:труха|уніан|інсайдер|[^\n]+)?",
        r"(?i)\bус\s*[/|]?\s*$",
        r"(?i)\bуніан\s*(?:у|в)\s*.*$",
        r"https?://\S+",
        r"@[a-zA-Z0-9_]+"
    ]
    cleaned = text
    for p in patterns:
        cleaned = re.sub(p, "", cleaned)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()

def safe_caption(text: str, max_len: int = 1024) -> str:
    text = text.strip()
    sig = f"\n\n👉 <a href=\"{CHANNEL_INVITE_LINK}\">{CHANNEL_NAME} | Підписатися</a>"
    if len(text) + len(sig) <= max_len:
        return f"{text}{sig}"
    avail = max_len - len(sig) - 3
    return f"{text[:avail]}...{sig}"

# ==================== АНТИДУБЛІКАТ ====================
def is_topic_already_covered(text: str) -> bool:
    topics = load_json(TOPICS_FILE)
    clean = re.sub(r"[^\w\s]", " ", text.lower())
    words = {w[:5] for w in clean.split() if len(w) >= 4}
    if not words:
        return False

    for t in topics[-40:]:
        t_words = set(t.get("stems", []))
        if words and t_words:
            overlap = len(words.intersection(t_words)) / len(words.union(t_words))
            # Дубль фіксується ТІЛЬКИ якщо текст збігається більш ніж наполовину
            if overlap >= 0.50:
                return True
    return False

def register_published_topic(headline: str, text: str):
    topics = load_json(TOPICS_FILE)
    clean = re.sub(r"[^\w\s]", " ", (headline + " " + text).lower())
    words = list({w[:5] for w in clean.split() if len(w) >= 4})
    topics.append({
        "headline": headline,
        "stems": words,
        "timestamp": time.time()
    })
    save_json(TOPICS_FILE, topics[-200:])

# ==================== СИНХРОНІЗАЦІЯ КАНАЛУ ====================
def sync_channel_history(channel_username: str) -> List[str]:
    clean_name = channel_username.replace("https://t.me/", "").replace("t.me/", "").replace("@", "").strip("/")
    recent_texts = []
    seen = set()

    if not clean_name:
        return recent_texts

    url = f"https://t.me/s/{clean_name}"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        r = requests.get(url, headers=headers, timeout=12)
        if r.status_code == 200:
            blocks = r.text.split('<div class="tgme_widget_message_wrap')
            for b in blocks[1:][-25:]:
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
        print(f"[СИНХРОНІЗАЦІЯ]: {e}", flush=True)

    return recent_texts

# ==================== ФІРМОВИЙ СТИЛЬ GROQ ====================
SYSTEM_PROMPT = """Ти — професійний український редактор новинного каналу "NEWS УКРАЇНА 365".
Твоє завдання — перетворити вхідну інформацію на якісний, лаконічний новинний пост у нашому фірмовому стилі.

НАШ СТИЛЬ:
⚡️ <b>Сили Оборони знищили колону техніки ворога на Сході</b>

Українські захисники зупинили спробу прориву окупантів у районі <b>Покровська</b>. Влучними ударами артилерії та дронів ліквідовано <b>4 танки</b> та <b>3 БМП</b> противника.

«Завдяки чітким діям розвідки наступ ворога повністю зірвано», — зазначили в командуванні.

ПРАВИЛА:
1. Заголовок: перший рядок обов'язково жирним <b>...</b> з 1 тематичним емодзі на початку.
2. Тіло: 1-2 чіткі фактичні абзаци. Важливі цифри, прізвища та міста виділяй <b>жирним</b>.
3. Пряму мову загортай у <blockquote>.
4. Жодних чужих каналів («Труха», «УС», «ексклюзивно для»).
5. Якщо це реклама або порожня тривога без наслідків — поверни "is_ad": true!
6. Якщо подія вже є в списку каналу — поверни "is_duplicate": true!

Формат відповіді СТРОГО JSON:
{
  "is_ad": false,
  "is_duplicate": false,
  "telegram_post": "Текст новини у фірмовому стилі з тегами <b> та <blockquote>"
}
"""

def process_news_with_ai(raw_text: str, recent_channel_posts: List[str]) -> Optional[Dict]:
    cleaned = strip_foreign_signatures(raw_text)
    if len(cleaned) < 25 or not AI_API_KEY:
        return None

    recent_context = ""
    if recent_channel_posts:
        recent_context = "ОСТАННІ ОПУБЛІКОВАНІ ПОСТИ (НЕ ПОВТОРЮВАТИ):\n" + "\n".join([f"- {p}" for p in recent_channel_posts[:8]]) + "\n\n"

    user_prompt = f"{recent_context}НОВА ІНФОРМАЦІЯ ДЛЯ ОБРОБКИ:\n{cleaned}"

    try:
        resp = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            json={
                "model": "llama-3.1-8b-instant",
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt}
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0.15
            },
            headers={"Authorization": f"Bearer {AI_API_KEY}", "Content-Type": "application/json"},
            timeout=15
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
    except Exception as e:
        print(f"[GROQ ПОМИЛКА]: {e}", flush=True)

    return None

def process_admin_post_with_ai(raw_text: str) -> str:
    cleaned = strip_foreign_signatures(raw_text)
    if not cleaned: return ""

    prompt = f"""Ти — професійний український редактор новин. ПЕРЕКЛАДИ ТА ОФОРМИ цю новину ЧИСТОЮ СОЛОВ'ЇНОЮ УКРАЇНСЬКОЮ МОВОЮ для новинного каналу Telegram.
Вимоги:
- Перший рядок: лаконічний заголовок <b>жирним</b> з 1 тематичним емодзі.
- Далі: 1-2 чіткі абзаци за фактами. Ключові слова виділяй <b>жирним</b>, цитати — в <blockquote>.
- Жодних чужих каналів і підписів.

Текст:
{cleaned}

Відповідай ВИКЛЮЧНО JSON:
{{"telegram_post": "Текст новини у фірмовому стилі з тегами <b> та <blockquote>"}}"""

    if AI_API_KEY:
        try:
            resp = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                json={
                    "model": "llama-3.1-8b-instant",
                    "messages": [{"role": "user", "content": prompt}],
                    "response_format": {"type": "json_object"},
                    "temperature": 0.2
                },
                headers={"Authorization": f"Bearer {AI_API_KEY}", "Content-Type": "application/json"},
                timeout=15
            )
            if resp.status_code == 200:
                data = resp.json()
                res_text = json.loads(data["choices"][0]["message"]["content"]).get("telegram_post", "")
                if res_text and not re.search(r"[ыэъё]", res_text, re.IGNORECASE):
                    return fix_ukrainian_calques(res_text)
        except Exception:
            pass

    # ДЛЯ ВАШИХ ПЕРЕСИЛАНЬ: якщо ШІ тимчасово зайнятий — публікуємо без відхилень!
    lines = [l.strip() for l in cleaned.splitlines() if l.strip()]
    if lines:
        head = f"⚡️ <b>{lines[0]}</b>"
        body = "\n\n".join(lines[1:])
        return fix_ukrainian_calques(f"{head}\n\n{body}" if body else head)
    return fix_ukrainian_calques(cleaned)

# ==================== ВІДПРАВКА В TELEGRAM ====================
def send_admin_reply(chat_id: int, text: str):
    if not TELEGRAM_BOT_TOKEN or not chat_id: return
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": text}, timeout=10)
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
        print(f"[TG GETFILE]: {e}", flush=True)
    return None

def extract_media_from_embed(channel_username: str, msg_id: int) -> Tuple[Optional[bytes], Optional[str]]:
    try:
        url = f"https://t.me/{channel_username}/{msg_id}?embed=1"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
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
                if p_resp.status_code == 200 and len(p_resp.content) > 15000:
                    return p_resp.content, "photo"
    except Exception as e:
        print(f"[EMBED EXTRACT]: {e}", flush=True)
    return None, None

def send_telegram_post(text: str, photo_url: str = None, video_url: str = None) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHANNEL_ID:
        return False

    caption = safe_caption(text, max_len=1024)
    full_msg = f"{text}\n\n👉 <a href=\"{CHANNEL_INVITE_LINK}\">{CHANNEL_NAME} | Підписатися</a>"

    if video_url:
        try:
            r_vid = requests.get(video_url, timeout=20)
            if r_vid.status_code == 200 and len(r_vid.content) > 1000:
                files = {"video": ("video.mp4", r_vid.content, "video/mp4")}
                data = {"chat_id": TELEGRAM_CHANNEL_ID, "caption": caption, "parse_mode": "HTML"}
                res = requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendVideo", data=data, files=files, timeout=50).json()
                if res.get("ok"): return True
                data["parse_mode"] = ""
                data["caption"] = re.sub(r"<[^>]+>", "", caption)
                res2 = requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendVideo", data=data, files={"video": ("video.mp4", r_vid.content, "video/mp4")}, timeout=50).json()
                if res2.get("ok"): return True
        except Exception:
            pass

    if photo_url:
        try:
            r_img = requests.get(photo_url, timeout=15)
            if r_img.status_code == 200 and len(r_img.content) > 15000:
                files = {"photo": ("photo.jpg", r_img.content, "image/jpeg")}
                data = {"chat_id": TELEGRAM_CHANNEL_ID, "caption": caption, "parse_mode": "HTML"}
                res = requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto", data=data, files=files, timeout=30).json()
                if res.get("ok"): return True
                data["parse_mode"] = ""
                data["caption"] = re.sub(r"<[^>]+>", "", caption)
                res2 = requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto", data=data, files={"photo": ("photo.jpg", r_img.content, "image/jpeg")}, timeout=30).json()
                if res2.get("ok"): return True
        except Exception:
            pass

    try:
        res = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHANNEL_ID, "text": full_msg, "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=15
        ).json()
        if res.get("ok"): return True
        plain = re.sub(r"<[^>]+>", "", full_msg)
        res_plain = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHANNEL_ID, "text": plain, "disable_web_page_preview": True},
            timeout=15
        ).json()
        return res_plain.get("ok", False)
    except Exception as e:
        print(f"[SEND MSG ERR]: {e}", flush=True)
        return False

# ==================== VIP ADMIN ====================
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
    final_text = safe_caption(final_caption, max_len=1024) if final_caption else f"👉 <a href=\"{CHANNEL_INVITE_LINK}\">{CHANNEL_NAME} | Підписатися</a>"

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
    final_text = safe_caption(final_caption, max_len=1024) if final_caption else f"👉 <a href=\"{CHANNEL_INVITE_LINK}\">{CHANNEL_NAME} | Підписатися</a>"

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
        json={"chat_id": TELEGRAM_CHANNEL_ID, "text": full_post, "parse_mode": "HTML", "disable_web_page_preview": True},
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
                    send_admin_reply(chat_id, "⏳ Збираю альбом і оформлюю пост...")
                    threading.Thread(target=process_vip_album, args=(mg_id,), daemon=True).start()

                if caption and not album_buffer[mg_id]["caption"]:
                    album_buffer[mg_id]["caption"] = caption

                album_buffer[mg_id]["items"].append({"type": m_type, "bytes": f_bytes})
        return

    if msg.get("photo"):
        send_admin_reply(chat_id, "⏳ Оформлюю пост у нашому стилі...")
        file_id = msg["photo"][-1]["file_id"]
        f_bytes = download_tg_file(file_id)
        publish_single_media_post(chat_id, caption, f_bytes, "photo", fwd_channel, fwd_msg_id)
        return

    if msg.get("video"):
        send_admin_reply(chat_id, "⏳ Оформлюю відео у нашому стилі...")
        file_id = msg["video"]["file_id"]
        f_bytes = download_tg_file(file_id)
        publish_single_media_post(chat_id, caption, f_bytes, "video", fwd_channel, fwd_msg_id)
        return

    if caption:
        send_admin_reply(chat_id, "⏳ Оформлюю новину у нашому стилі...")
        publish_text_post(chat_id, caption)
        return

# ==================== WEBHOOK СЕРВЕР ====================
class WebhookHandler(BaseHTTPRequestHandler):
    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"NEWS BOT 365 ALIVE")

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

# ==================== АВТОПІНГЕР ====================
def keepalive_pinger(server_url: str):
    time.sleep(30)
    print(f"[KEEPALIVE]: Автопінгер запущено: {server_url}", flush=True)
    while True:
        time.sleep(480)
        try:
            r = requests.get(server_url, timeout=15)
            print(f"[{datetime.now().strftime('%H:%M:%S')}] [KEEPALIVE]: Сервіс активний ({r.status_code})", flush=True)
        except Exception as e:
            print(f"[KEEPALIVE]: Помилка пінгу: {e}", flush=True)

# ==================== АВТОНОМНИЙ СКАНЕР ====================
# Пам'ять поточної сесії — НЕ блокує свіжі пости з минулих запусків!
seen_session_post_ids = set()

def fetch_posts(channel: str, session: requests.Session = None) -> List[Dict]:
    url = f"https://t.me/s/{channel}"
    req_obj = session or requests
    posts = []
    try:
        r = req_obj.get(url, timeout=12)
        if r.status_code != 200:
            return posts

        blocks = r.text.split('<div class="tgme_widget_message_wrap')
        # Беремо останні 8 повідомлень каналу
        for b in blocks[1:][-8:]:
            p_id = re.search(r'data-post="([^"]+)"', b)
            if not p_id: continue
            post_id = p_id.group(1)

            if post_id in seen_session_post_ids:
                continue

            t_match = re.search(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', b, re.DOTALL)
            raw_text = html.unescape(re.sub(r"<[^>]+>", " ", t_match.group(1))).strip() if t_match else ""

            if is_routine_siren(raw_text) or is_ad(raw_text):
                seen_session_post_ids.add(post_id)
                continue

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
    print(f"[{datetime.now().strftime('%H:%M:%S')}] [АВТОНОМНІСТЬ]: Автономний сканер запущено з чистим кешем!", flush=True)

    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})

    while True:
        try:
            recent_posts = sync_channel_history(CHANNEL_USERNAME)
            published = 0
            print(f"[{datetime.now().strftime('%H:%M:%S')}] [СКАНЕР]: Починаю обхід 23 донорів...", flush=True)

            for ch in DONOR_CHANNELS:
                if published >= MAX_POSTS_PER_RUN:
                    break

                posts = fetch_posts(ch, session=session)
                for p in posts:
                    if published >= MAX_POSTS_PER_RUN:
                        break

                    text = p["text"]
                    p_id = p["post_id"]

                    if is_routine_siren(text) or is_ad(text):
                        seen_session_post_ids.add(p_id)
                        continue

                    # Якщо новина вже є в @kropivnit — не дублюємо
                    if is_topic_already_covered(text):
                        seen_session_post_ids.add(p_id)
                        continue

                    ai = process_news_with_ai(text, recent_posts)
                    if not ai:
                        seen_session_post_ids.add(p_id)
                        continue

                    post_text = ai.get("telegram_post", "")
                    if len(post_text) < 25 or is_topic_already_covered(post_text):
                        seen_session_post_ids.add(p_id)
                        continue

                    sent = send_telegram_post(post_text, p["photo_url"], p["video_url"])
                    if sent:
                        published += 1
                        seen_session_post_ids.add(p_id)
                        head = post_text.splitlines()[0][:70]
                        register_published_topic(head, post_text)
                        recent_posts.insert(0, head)
                        print(f"[{datetime.now().strftime('%H:%M:%S')}] ✅ [АВТОНОМНІСТЬ]: Опубліковано з @{ch}: {head[:45]}...", flush=True)
                        time.sleep(DELAY_BETWEEN_POSTS)

            print(f"[{datetime.now().strftime('%H:%M:%S')}] [СКАНЕР]: Обхід завершено. Опубліковано: {published}. Пауза 25 сек...", flush=True)

        except Exception as e:
            print(f"[СКАНЕР ПОМИЛКА]: {e}", flush=True)

        time.sleep(25)

# ==================== ЗАПУСК ====================
if __name__ == "__main__":
    if TELEGRAM_BOT_TOKEN:
        webhook_url = f"{RENDER_URL.rstrip('/')}/tg-webhook"
        print(f"[WEBHOOK]: Реєстрація: {webhook_url}", flush=True)
        try:
            res = requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/setWebhook",
                json={"url": webhook_url, "allowed_updates": ["message", "channel_post", "edited_channel_post"], "drop_pending_updates": False},
                timeout=15
            ).json()
            print(f"[WEBHOOK STATUS]: {res}", flush=True)
        except Exception as e:
            print(f"[WEBHOOK ERROR]: {e}", flush=True)

    t_scanner = threading.Thread(target=run_scanner_loop, daemon=True)
    t_scanner.start()

    t_pinger = threading.Thread(target=keepalive_pinger, args=(RENDER_URL,), daemon=True)
    t_pinger.start()

    start_webhook_server()
