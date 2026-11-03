import json
import urllib.request
import urllib.parse
from typing import List, Dict, Any, Optional
from config import AYRSHARE_API_KEY, ADMIN_CHAT_ID, CHANNEL_NAME, CHANNEL_USERNAME, CHANNEL_INVITE_LINK
from telegram_poster import send_telegram_local_video

def post_video_to_social_networks(
    video_path: str,
    headline: str,
    script_text: str,
    social_caption: str
):
    """
    Публікує згенероване відео ВИКЛЮЧНО в зовнішні соцмережі (TikTok, Reels, Shorts, Threads, FB).
    У ваш Telegram-канал це відео НЕ потрапляє!
    """
    print(f"\n[Соцмережі] Обробка відео для зовнішніх мереж: {headline}")

    # 1. Якщо налаштовано ключ автопостингу (Ayrshare API)
    if AYRSHARE_API_KEY and not AYRSHARE_API_KEY.startswith("YOUR_"):
        try:
            url = "https://app.ayrshare.com/api/post"
            payload = {
                "post": social_caption,
                "platforms": ["tiktok", "youtube", "instagram", "facebook", "threads"],
                "mediaUrls": [video_path]
            }
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {AYRSHARE_API_KEY}"}
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                res = json.loads(resp.read().decode("utf-8"))
                print(f"[Ayrshare] Результат публікації: {res}")
        except Exception as e:
            print(f"[Ayrshare] Помилка автопостингу: {e}")

    # 2. Одночасно надсилаємо готове відео ВАМ в особисті повідомлення (Admin DM)
    # Щоб ви могли в 1 клік зберегти його і викласти в TikTok/Instagram руками
    if ADMIN_CHAT_ID:
        admin_text = (
            f"🎬 **ГОТОВЕ ВІДЕО ДЛЯ СОЦМЕРЕЖ (TikTok / Shorts / Reels / Threads)**\n\n"
            f"📌 **Заголовок:** {headline}\n\n"
            f"📝 **Готовий текст для опису ролика (скопіюйте):**\n"
            f"_{social_caption}_\n\n"
            f"⚠️ *У ваш Telegram-канал це відео НЕ публікується, воно створене суто для залучення підписників із соцмереж.*"
        )
        send_telegram_local_video(video_path, caption=admin_text, target_chat_id=ADMIN_CHAT_ID)
        print(f"[Admin] Відео надіслано в особисті повідомлення адміну ({ADMIN_CHAT_ID})")
