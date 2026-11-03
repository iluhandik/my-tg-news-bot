import json
import urllib.request
import urllib.parse
import os
from typing import Optional
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID, ADMIN_CHAT_ID

def send_telegram_message(text: str, channel_id: Optional[str] = None) -> bool:
    """Надсилає текстове повідомлення у вказаний канал або чат."""
    target_id = channel_id or TELEGRAM_CHANNEL_ID
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": target_id,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": False
    }
    try:
        data = urllib.parse.urlencode(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=15) as resp:
            res = json.loads(resp.read().decode("utf-8"))
            return res.get("ok", False)
    except Exception as e:
        print(f"Помилка надсилання повідомлення в Telegram: {e}")
        return False

def send_telegram_photo(photo_url: str, caption: str, channel_id: Optional[str] = None) -> bool:
    """Надсилає фото з підписом у канал."""
    target_id = channel_id or TELEGRAM_CHANNEL_ID
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    payload = {
        "chat_id": target_id,
        "photo": photo_url,
        "caption": caption[:1024],  # обмеження Telegram для підпису до фото
        "parse_mode": "Markdown"
    }
    try:
        data = urllib.parse.urlencode(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=20) as resp:
            res = json.loads(resp.read().decode("utf-8"))
            return res.get("ok", False)
    except Exception as e:
        print(f"Помилка надсилання фото в Telegram (спробуємо як звичайний текст): {e}")
        return send_telegram_message(caption, channel_id=target_id)

def send_telegram_video(video_path: str, caption: str, channel_id: Optional[str] = None) -> bool:
    """Надсилає відеофайл у Telegram через multipart/form-data."""
    target_id = channel_id or TELEGRAM_CHANNEL_ID
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendVideo"
    
    if not os.path.exists(video_path):
        print(f"Файл відео не знайдено: {video_path}")
        return False
        
    try:
        boundary = "----WebKitFormBoundary7MA4YWxkTrZu0gW"
        body = bytearray()
        
        # chat_id
        body.extend(f"--{boundary}\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n{target_id}\r\n".encode("utf-8"))
        # caption
        body.extend(f"--{boundary}\r\nContent-Disposition: form-data; name=\"caption\"\r\n\r\n{caption[:1024]}\r\n".encode("utf-8"))
        # parse_mode
        body.extend(f"--{boundary}\r\nContent-Disposition: form-data; name=\"parse_mode\"\r\n\r\nMarkdown\r\n".encode("utf-8"))
        # video file
        filename = os.path.basename(video_path)
        body.extend(f"--{boundary}\r\nContent-Disposition: form-data; name=\"video\"; filename=\"{filename}\"\r\nContent-Type: video/mp4\r\n\r\n".encode("utf-8"))
        
        with open(video_path, "rb") as f:
            body.extend(f.read())
            
        body.extend(f"\r\n--{boundary}--\r\n".encode("utf-8"))
        
        req = urllib.request.Request(url, data=bytes(body))
        req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
        
        with urllib.request.urlopen(req, timeout=60) as resp:
            res = json.loads(resp.read().decode("utf-8"))
            return res.get("ok", False)
    except Exception as e:
        print(f"Помилка завантаження відео в Telegram: {e}")
        return False
