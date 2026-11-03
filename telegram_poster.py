import json
import urllib.request
import urllib.parse
import os
import requests
from typing import Optional
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID, MEDIA_DIR

def send_telegram_message(text: str, channel_id: Optional[str] = None) -> bool:
    """Надсилає текстове повідомлення в канал."""
    target_id = channel_id or TELEGRAM_CHANNEL_ID
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": target_id,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True
    }
    try:
        resp = requests.post(url, data=payload, timeout=20)
        res = resp.json()
        if not res.get("ok", False):
            # Якщо збій Markdown — пробуємо надіслати чистим текстом
            payload.pop("parse_mode", None)
            resp = requests.post(url, data=payload, timeout=20)
            res = resp.json()
        return res.get("ok", False)
    except Exception as e:
        print(f"[Telegram]: Помилка надсилання тексту: {e}")
        return False

def send_telegram_photo(photo_url: str, caption: str, channel_id: Optional[str] = None) -> bool:
    """
    Надсилає фото з текстом у канал:
    1. Спочатку пробує передати прямий URL фотографії в Telegram.
    2. Якщо Telegram не зміг завантажити за URL — скрипт сам завантажує фото і відправляє файлом!
    """
    target_id = channel_id or TELEGRAM_CHANNEL_ID
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    caption_text = caption[:1024]

    # Спроба 1: Відправка за URL
    try:
        payload = {
            "chat_id": target_id,
            "photo": photo_url,
            "caption": caption_text,
            "parse_mode": "Markdown"
        }
        resp = requests.post(url, data=payload, timeout=20)
        res = resp.json()
        if res.get("ok", False):
            print(f"[Telegram]: Фото успішно надіслано за прямим посиланням.")
            return True
        else:
            print(f"[Telegram]: Відправка за URL не вдалася ({res.get('description')}). Завантажуємо файл локально...")
    except Exception as e:
        print(f"[Telegram]: Збій відправки за URL: {e}. Пробуємо локальне завантаження...")

    # Спроба 2: Завантажуємо фото та надсилаємо файлом (multipart)
    temp_img_path = os.path.join(MEDIA_DIR, "temp_post_photo.jpg")
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        img_resp = requests.get(photo_url, headers=headers, timeout=15)
        if img_resp.status_code == 200 and len(img_resp.content) > 500:
            with open(temp_img_path, "wb") as f:
                f.write(img_resp.content)

            with open(temp_img_path, "rb") as f:
                files = {"photo": ("photo.jpg", f, "image/jpeg")}
                data = {
                    "chat_id": target_id,
                    "caption": caption_text,
                    "parse_mode": "Markdown"
                }
                resp = requests.post(url, data=data, files=files, timeout=30)
                res = resp.json()
                if not res.get("ok", False):
                    # Пробуємо без Markdown, якщо була помилка форматування
                    f.seek(0)
                    data.pop("parse_mode", None)
                    resp = requests.post(url, data=data, files={"photo": ("photo.jpg", f, "image/jpeg")}, timeout=30)
                    res = resp.json()

                if res.get("ok", False):
                    print(f"[Telegram]: Фото успішно завантажено файлом і опубліковано!")
                    return True
                else:
                    print(f"[Telegram]: Помилка відправки файлу фото: {res}")
    except Exception as e:
        print(f"[Telegram]: Збій завантаження фото файлом: {e}")

    # Спроба 3: Якщо фото взагалі недоступне — публікуємо як звичайний текст
    print("[Telegram]: Фото недоступне, публікуємо як текст.")
    return send_telegram_message(caption, channel_id=target_id)

def send_telegram_video_media(video_url: str, caption: str, channel_id: Optional[str] = None) -> bool:
    """
    Надсилає оригінальне відео новини в канал:
    1. Пробує відправити відео за URL.
    2. Якщо Telegram не може стягнути URL — завантажує MP4 на сервер і передає файлом.
    """
    target_id = channel_id or TELEGRAM_CHANNEL_ID
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendVideo"
    caption_text = caption[:1024]

    # Спроба 1: за URL
    try:
        payload = {
            "chat_id": target_id,
            "video": video_url,
            "caption": caption_text,
            "parse_mode": "Markdown"
        }
        resp = requests.post(url, data=payload, timeout=25)
        res = resp.json()
        if res.get("ok", False):
            print(f"[Telegram]: Відео успішно опубліковано за прямим посиланням.")
            return True
        else:
            print(f"[Telegram]: Не вдалося надіслати відео за URL ({res.get('description')}). Завантажуємо локально...")
    except Exception as e:
        print(f"[Telegram]: Збій надсилання відео за URL: {e}")

    # Спроба 2: завантажуємо MP4 і шлемо як файл
    temp_vid_path = os.path.join(MEDIA_DIR, "temp_post_video.mp4")
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        vid_resp = requests.get(video_url, headers=headers, timeout=30, stream=True)
        if vid_resp.status_code == 200:
            with open(temp_vid_path, "wb") as f:
                for chunk in vid_resp.iter_content(chunk_size=1024*1024):
                    if chunk:
                        f.write(chunk)

            if os.path.exists(temp_vid_path) and os.path.getsize(temp_vid_path) > 5000:
                with open(temp_vid_path, "rb") as f:
                    files = {"video": ("video.mp4", f, "video/mp4")}
                    data = {
                        "chat_id": target_id,
                        "caption": caption_text,
                        "parse_mode": "Markdown"
                    }
                    resp = requests.post(url, data=data, files=files, timeout=60)
                    res = resp.json()
                    if res.get("ok", False):
                        print(f"[Telegram]: Відео успішно завантажено файлом і опубліковано!")
                        return True
                    else:
                        print(f"[Telegram]: Помилка надсилання відеофайлу: {res}")
    except Exception as e:
        print(f"[Telegram]: Збій завантаження відеофайлу: {e}")

    # Резерв: відправляємо текстом
    return send_telegram_message(caption, channel_id=target_id)

def send_telegram_local_video(video_path: str, caption: str, target_chat_id: str) -> bool:
    """Надсилає локально згенероване відео адміну для соцмереж."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendVideo"
    if not os.path.exists(video_path):
        return False
    try:
        with open(video_path, "rb") as f:
            files = {"video": (os.path.basename(video_path), f, "video/mp4")}
            data = {
                "chat_id": target_chat_id,
                "caption": caption[:1024],
                "parse_mode": "Markdown"
            }
            resp = requests.post(url, data=data, files=files, timeout=90)
            res = resp.json()
            return res.get("ok", False)
    except Exception as e:
        print(f"[Telegram]: Помилка відправки відео адміну: {e}")
        return False
