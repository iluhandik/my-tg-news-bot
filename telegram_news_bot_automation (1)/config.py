import os
from dotenv import load_dotenv

load_dotenv()

# Telegram Configuration
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID", "@your_channel_username")  # або числовий ID наприклад -1001234567890
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID", "")  # ID адміна для надсилання згенерованих відео на узгодження

# Channel Branding
CHANNEL_NAME = os.getenv("CHANNEL_NAME", "Новини України та Світу")
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "@news_channel")
CHANNEL_INVITE_LINK = os.getenv("CHANNEL_INVITE_LINK", "https://t.me/news_channel")

# AI API Configuration (OpenAI, Gemini або Groq)
AI_PROVIDER = os.getenv("AI_PROVIDER", "openai")  # "openai", "groq", "gemini"
AI_API_KEY = os.getenv("AI_API_KEY", "")
AI_MODEL = os.getenv("AI_MODEL", "gpt-4o-mini")  # або "llama-3.3-70b-versatile" для Groq

# News Aggregation Settings
CHECK_INTERVAL_SECONDS = int(os.getenv("CHECK_INTERVAL_SECONDS", "300"))  # перевірка кожні 5 хвилин
BREAKING_NEWS_THRESHOLD = int(os.getenv("BREAKING_NEWS_THRESHOLD", "8"))  # поріг важливості 1-10 для генерації Shorts/Reels

# RSS Джерела новин (можна додавати будь-які українські та міжнародні RSS стрічки)
RSS_FEEDS = [
    "https://www.pravda.com.ua/rss/",
    "https://suspilne.media/rss/",
    "https://interfax.com.ua/news/last.rss",
    "https://feeds.bbci.co.uk/ukrainian/rss.xml",
    "https://censor.net/rss/news",
]

# Social Media Publishing (Ayrshare або окремі API)
# Ayrshare дозволяє надсилати одне відео одразу в TikTok, YouTube, Instagram, Facebook, Threads
AYRSHARE_API_KEY = os.getenv("AYRSHARE_API_KEY", "")

# YouTube Data API (якщо окремо публікувати на YouTube Shorts)
YOUTUBE_CLIENT_SECRETS_FILE = os.getenv("YOUTUBE_CLIENT_SECRETS_FILE", "client_secrets.json")

# Шляхи до медіа файлів
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MEDIA_DIR = os.path.join(BASE_DIR, "media")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
os.makedirs(MEDIA_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)
