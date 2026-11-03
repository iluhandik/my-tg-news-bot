import re
import time
import urllib.request
import urllib.parse
from datetime import datetime
from typing import List, Dict, Optional
from config import MAX_NEWS_AGE_HOURS

def clean_html_tags(text: str) -> str:
    """Очищає HTML розмітку."""
    if not text:
        return ""
    text = re.sub(r"<br\s*/?>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&nbsp;", " ").replace("&quot;", '"').replace("&apos;", "'").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return text.strip()

def parse_post_timestamp(block: str) -> Optional[float]:
    """Витягує точний час публікації з тегу <time datetime='...'>."""
    time_match = re.search(r'<time[^>]+datetime="([^"]+)"', block)
    if not time_match:
        return None
    try:
        dt_str = time_match.group(1).replace("Z", "+00:00")
        dt = datetime.fromisoformat(dt_str)
        return dt.timestamp()
    except Exception:
        return None

def fetch_channel_posts(channel_name: str, limit: int = 4) -> List[Dict]:
    """
    Парсить найсвіжіші публікації з публічного Telegram-каналу через t.me/s/{channel_name}.
    Ігнорує старі архівні новини (старші за 24 години).
    """
    posts = []
    clean_username = channel_name.replace("https://t.me/", "").replace("t.me/", "").replace("@", "").strip("/")
    if clean_username.startswith("+") or "joinchat" in clean_username:
        return posts

    url = f"https://t.me/s/{clean_username}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept-Language": "uk-UA,uk;q=0.9,en-US;q=0.8,en;q=0.7"
    }

    current_ts = time.time()
    max_age_seconds = MAX_NEWS_AGE_HOURS * 3600

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="ignore")

        raw_messages = html.split('<div class="tgme_widget_message_wrap')
        for block in raw_messages[1:]:
            # Перевірка свіжості новини: відсікаємо тижневу/місячну давнину
            post_timestamp = parse_post_timestamp(block)
            if post_timestamp and (current_ts - post_timestamp > max_age_seconds):
                continue

            post_id_match = re.search(r'data-post="([^"]+)"', block)
            if not post_id_match:
                continue
            post_id = post_id_match.group(1)

            text_match = re.search(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>\s*<div class="tgme_widget_message_footer', block, re.DOTALL)
            raw_text = text_match.group(1) if text_match else ""
            clean_text = clean_html_tags(raw_text)

            photo_match = re.search(r'<a[^>]+class="[^"]*tgme_widget_message_photo_wrap[^"]*"[^>]+style="background-image:url\(\'([^\']+)\'\)"', block)
            photo_url = photo_match.group(1) if photo_match else None

            video_url = None
            vid_match = re.search(r'<video[^>]+src="([^"]+)"', block)
            if vid_match:
                video_url = vid_match.group(1)

            if not clean_text and not photo_url and not video_url:
                continue

            posts.append({
                "post_id": post_id,
                "channel": clean_username,
                "text": clean_text,
                "photo_url": photo_url,
                "video_url": video_url,
                "timestamp": post_timestamp or current_ts,
                "source_url": f"https://t.me/{post_id}"
            })

    except Exception as e:
        print(f"Помилка при читанні каналу {clean_username}: {e}")

    return posts[-limit:]
