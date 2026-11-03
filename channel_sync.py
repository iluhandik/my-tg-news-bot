import html
import re
import urllib.request
from typing import List
from topic_filter import register_published_topic

def get_channel_recent_posts(channel_username: str = "kropivnit", limit: int = 20) -> List[str]:
    """
    Зчитує останні дописи безпосередньо з вашого каналу @kropivnit
    та автоматично реєструє їхні назви та географічні назви в базі тем.
    Це гарантує, що якщо ви виклали пост вручну — бот НІКОЛИ не запостить
    цю саму подію з жодного іншого каналу-донора!
    """
    clean_name = channel_username.replace("https://t.me/", "").replace("t.me/", "").replace("@", "").strip("/")
    url = f"https://t.me/s/{clean_name}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept-Language": "uk-UA,uk;q=0.9"
    }

    recent_posts = []
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=12) as resp:
            html_raw = resp.read().decode("utf-8", errors="ignore")

        blocks = html_raw.split('<div class="tgme_widget_message_wrap')
        for block in blocks[1:][-limit:]:
            text_match = re.search(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>\s*<div class="tgme_widget_message_footer', block, re.DOTALL)
            if not text_match:
                continue
            
            raw_text = text_match.group(1)
            raw_text = re.sub(r"<br\s*/?>", " ", raw_text)
            raw_text = re.sub(r"<[^>]+>", "", raw_text)
            clean_text = html.unescape(raw_text).strip()

            if len(clean_text) > 20:
                headline = clean_text.split("\n")[0][:60]
                # Реєструємо кожну новину з вашого каналу в антидублікаторі!
                register_published_topic(headline, clean_text)
                recent_posts.append(clean_text[:120])

        print(f"[СИНХРОНІЗАЦІЯ]: Успішно синхронізовано {len(recent_posts)} останніх постів вашого каналу.")
    except Exception as e:
        print(f"[СИНХРОНІЗАЦІЯ]: Не вдалося зчитати @{clean_name}: {e}")

    return recent_posts
