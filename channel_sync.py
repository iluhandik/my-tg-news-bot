import html
import re
import urllib.request
from typing import List
from topic_filter import register_published_topic

def sync_user_channel_topics(channel_username: str = "kropivnit", limit: int = 25):
    """
    Зчитує останні дописи безпосередньо з вашого каналу @kropivnit
    та записує їх у базу тем. Якщо ви виклали пост вручну — бот гарантовано
    не викладе цю ж новину повторно з інших донорів!
    """
    clean_name = channel_username.replace("https://t.me/", "").replace("t.me/", "").replace("@", "").strip("/")
    url = f"https://t.me/s/{clean_name}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept-Language": "uk-UA,uk;q=0.9"
    }

    synced_count = 0
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            html_raw = resp.read().decode("utf-8", errors="ignore")

        blocks = html_raw.split('<div class="tgme_widget_message_wrap')
        for block in blocks[1:][-limit:]:
            text_match = re.search(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>\s*<div class="tgme_widget_message_footer', block, re.DOTALL)
            if not text_match:
                continue
            
            raw_text = text_match.group(1)
            raw_text = re.sub(r"<br\s*/?>", "\n", raw_text)
            raw_text = re.sub(r"<[^>]+>", "", raw_text)
            clean_text = html.unescape(raw_text).strip()

            if len(clean_text) > 25:
                headline = clean_text.split("\n")[0][:60]
                register_published_topic(headline, clean_text)
                synced_count += 1

        print(f"[СИНХРОНІЗАЦІЯ]: Успішно синхронізовано {synced_count} останніх постів із вашого каналу @{clean_name}.")
    except Exception as e:
        print(f"[СИНХРОНІЗАЦІЯ]: Не вдалося синхронізувати пости з @{clean_name}: {e}")
