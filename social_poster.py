import os
import json
import urllib.request
import urllib.parse
from typing import List, Dict, Any, Optional
from config import AYRSHARE_API_KEY, ADMIN_CHAT_ID, CHANNEL_NAME, CHANNEL_USERNAME, CHANNEL_INVITE_LINK
from telegram_poster import send_telegram_video, send_telegram_message

def post_to_all_socials_ayrshare(
    video_url: str,
    title: str,
    description: str,
    platforms: Optional[List[str]] = None
) -> Dict[str, Any]:
    """
    Публікує коротке відео (Shorts/Reels/TikTok) одночасно на всі підключені платформи через Ayrshare API:
    - YouTube Shorts
    - TikTok
    - Instagram Reels
    - Facebook Reels
    - Threads
    """
    if not AYRSHARE_API_KEY or AYRSHARE_API_KEY.startswith("YOUR_"):
        print("AYRSHARE_API_KEY не налаштовано. Пропуск автопостингу через API.")
        return {"status": "skipped", "message": "API key not set"}

    if platforms is None:
        platforms = ["tiktok", "youtube", "instagram", "facebook", "threads"]

    full_caption = (
        f"{title}\n\n"
        f"{description}\n\n"
        f"🔗 Більше оперативних новин у нашому Telegram: {CHANNEL_INVITE_LINK}\n"
        f"#новини #україна #shorts #reels #tiktok #news"
    )

    url = "https://app.ayrshare.com/api/post"
    payload = {
        "post": full_caption,
        "platforms": platforms,
        "mediaUrls": [video_url],
        "shortenLinks": False,
        "youTubeOptions": {
            "title": title[:100],
            "visibility": "public"
        },
        "tiktokOptions": {
            "commentDisabled": False,
            "duetDisabled": False,
            "stitchDisabled": False
        }
    }

    try:
        data = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {AYRSHARE_API_KEY}"
        }
        req = urllib.request.Request(url, data=data, headers=headers)
        with urllib.request.urlopen(req, timeout=45) as resp:
            res = json.loads(resp.read().decode("utf-8"))
            print(f"Результат публікації в соцмережі: {res}")
            return res
    except Exception as e:
        print(f"Помилка публікації через Ayrshare: {e}")
        return {"status": "error", "message": str(e)}

def notify_admin_with_video(video_path: str, title: str, script_text: str):
    """
    Надсилає згенероване відео в особисті повідомлення адміну Telegram із готовим описом,
    тегами та посиланням на канал для швидкої публікації в 1 клік.
    """
    if not ADMIN_CHAT_ID:
        return

    caption = (
        f"🎬 **ЗГЕНЕРОВАНО ВІДЕО ДЛЯ СОЦМЕРЕЖ**\n\n"
        f"📌 **Заголовок:** {title}\n\n"
        f"📝 **Сценарій:**\n{script_text}\n\n"
        f"🔗 **Реклама каналу:** {CHANNEL_USERNAME} ({CHANNEL_INVITE_LINK})\n\n"
        f"Теги: #новини #україна #shorts #reels #tiktok"
    )
    send_telegram_video(video_path, caption=caption, channel_id=ADMIN_CHAT_ID)
