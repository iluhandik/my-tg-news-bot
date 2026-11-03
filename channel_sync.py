import html
import re
import urllib.request
from typing import List

def get_channel_recent_posts(channel_username: str = "kropivnit", limit: int = 15) -> List[str]:
    """
    Зчитує останні опубліковані пости безпосередньо з вашого каналу @kropivnit.
    Повертає список коротких описів останніх новин каналу для ШІ-перевірки дублікатів.
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
                recent_posts.append(clean_text[:120])

    except Exception as e:
        print(f"[СИНХРОНІЗАЦІЯ]: Помилка читання каналу @{clean_name}: {e}")

    return recent_posts
