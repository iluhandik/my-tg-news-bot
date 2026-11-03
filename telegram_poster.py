import json
import urllib.request
import urllib.parse
import os
import re
import requests
from typing import Optional
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID, MEDIA_DIR, CHANNEL_USERNAME
from media_watermarker import brand_image, brand_video
from topic_filter import is_media_duplicate, register_media

CHANNELS_WITH_HEAVY_WATERMARKS = {
    "supernova_plus",
    "exilenova_plus"
}

def strip_html_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text)

def send_telegram_message(text: str, channel_id: Optional[str] = None) -> bool:
    target_id = channel_id or TELEGRAM_CHANNEL_ID
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": target_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    try:
        resp = requests.post(url, data=payload, timeout=20)
        res = resp.json()
        if not res.get("ok", False):
            payload["text"] = strip_html_tags(text)
            payload.pop("parse_mode", None)
            resp = requests.post(url, data=payload, timeout=20)
            res = resp.json()
        return res.get("ok", False)
    except Exception as e:
        print(f"[Telegram]: Помилка надсилання тексту: {e}")
        return False

def send_telegram_photo(photo_url: str, caption: str, donor_channel: str = "", channel_id: Optional[str] = None) -> bool:
    """
    Завантажує фото, перевіряє на дублікат за хешем зображення, брендує при потребі та публікує.
    """
    target_id = channel_id or TELEGRAM_CHANNEL_ID
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    caption_text = caption[:1024]

    raw_img_path = os.path.join(MEDIA_DIR, "raw_download_photo.jpg")
    branded_img_path = os.path.join(MEDIA_DIR, "branded_post_photo.jpg")
    has_heavy_watermark = donor_channel.lower() in CHANNELS_WITH_HEAVY_WATERMARKS

    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        img_resp = requests.get(photo_url, headers=headers, timeout=15)
        if img_resp.status_code == 200 and len(img_resp.content) > 500:
            # ПЕРЕВІРКА НА ДУБЛЬ ФОТО ЗА ХЕШЕМ:
            if is_media_duplicate(img_resp.content, photo_url):
                print(f"[АНТИДУБЛЬ]: Це саме фото вже було опубліковане раніше! Відхиляємо дублікат.")
                return False

            with open(raw_img_path, "wb") as f:
                f.write(img_resp.content)

            if has_heavy_watermark:
                final_img = brand_image(raw_img_path, branded_img_path, watermark_text=CHANNEL_USERNAME)
            else:
                final_img = raw_img_path

            with open(final_img, "rb") as f:
                files = {"photo": ("photo.jpg", f, "image/jpeg")}
                data = {
                    "chat_id": target_id,
                    "caption": caption_text,
                    "parse_mode": "HTML"
                }
                resp = requests.post(url, data=data, files=files, timeout=30)
                res = resp.json()
                if not res.get("ok", False):
                    f.seek(0)
                    data["caption"] = strip_html_tags(caption_text)
                    data.pop("parse_mode", None)
                    resp = requests.post(url, data=data, files={"photo": ("photo.jpg", f, "image/jpeg")}, timeout=30)
                    res = resp.json()

                if res.get("ok", False):
                    print(f"[Telegram]: Фото успішно опубліковано!")
                    register_media(img_resp.content, photo_url)
                    return True
                else:
                    print(f"[Telegram]: Помилка відправки фото: {res}")
    except Exception as e:
        print(f"[Telegram]: Збій обробки фото: {e}")

    return send_telegram_message(caption, channel_id=target_id)

def send_telegram_video_media(video_url: str, caption: str, donor_channel: str = "", channel_id: Optional[str] = None) -> bool:
    """Завантажує оригінальне відео, перевіряє дублікати, брендує та публікує."""
    target_id = channel_id or TELEGRAM_CHANNEL_ID
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendVideo"
    caption_text = caption[:1024]

    raw_vid_path = os.path.join(MEDIA_DIR, "raw_download_video.mp4")
    branded_vid_path = os.path.join(MEDIA_DIR, "branded_post_video.mp4")
    has_heavy_watermark = donor_channel.lower() in CHANNELS_WITH_HEAVY_WATERMARKS

    # Перевірка дубля за URL відео
    if is_media_duplicate(b"", video_url):
        print(f"[АНТИДУБЛЬ]: Це саме відео вже було опубліковане раніше! Відхиляємо дубль.")
        return False

    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        vid_resp = requests.get(video_url, headers=headers, timeout=35, stream=True)
        if vid_resp.status_code == 200:
            with open(raw_vid_path, "wb") as f:
                for chunk in vid_resp.iter_content(chunk_size=1024*1024):
                    if chunk:
                        f.write(chunk)

            if os.path.exists(raw_vid_path) and os.path.getsize(raw_vid_path) > 5000:
                if has_heavy_watermark:
                    final_video = brand_video(raw_vid_path, branded_vid_path, watermark_text=CHANNEL_USERNAME)
                else:
                    final_video = raw_vid_path

                with open(final_video, "rb") as f:
                    files = {"video": ("video.mp4", f, "video/mp4")}
                    data = {
                        "chat_id": target_id,
                        "caption": caption_text,
                        "parse_mode": "HTML"
                    }
                    resp = requests.post(url, data=data, files=files, timeout=75)
                    res = resp.json()
                    if not res.get("ok", False):
                        f.seek(0)
                        data["caption"] = strip_html_tags(caption_text)
                        data.pop("parse_mode", None)
                        resp = requests.post(url, data=data, files={"video": ("video.mp4", f, "video/mp4")}, timeout=75)
                        res = resp.json()

                    if res.get("ok", False):
                        print(f"[Telegram]: Відео успішно опубліковано!")
                        register_media(b"", video_url)
                        return True
                    else:
                        print(f"[Telegram]: Помилка надсилання відео: {res}")
    except Exception as e:
        print(f"[Telegram]: Збій обробки відео: {e}")

    return send_telegram_message(caption, channel_id=target_id)

def send_telegram_local_video(video_path: str, caption: str, target_chat_id: str) -> bool:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendVideo"
    if not os.path.exists(video_path):
        return False
    try:
        with open(video_path, "rb") as f:
            files = {"video": (os.path.basename(video_path), f, "video/mp4")}
            data = {
                "chat_id": target_chat_id,
                "caption": caption[:1024],
                "parse_mode": "HTML"
            }
            resp = requests.post(url, data=data, files=files, timeout=90)
            res = resp.json()
            return res.get("ok", False)
    except Exception as e:
        print(f"[Telegram]: Помилка відправки відео адміну: {e}")
        return False
