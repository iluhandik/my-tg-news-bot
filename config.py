import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID", "@kropivnit")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID", "")

CHANNEL_NAME = "Оперативні Новини України"
CHANNEL_USERNAME = "@kropivnit"
CHANNEL_INVITE_LINK = "https://t.me/kropivnit"

MAX_NEWS_AGE_HOURS = int(os.getenv("MAX_NEWS_AGE_HOURS", "24"))
CHECK_INTERVAL_SECONDS = int(os.getenv("CHECK_INTERVAL_SECONDS", "45"))

# AI: використовуємо llama-3.1-8b-instant (доступна на всіх 100% безкоштовних акаунтах Groq)
AI_PROVIDER = os.getenv("AI_PROVIDER", "groq")
AI_API_KEY = os.getenv("AI_API_KEY", "")
AI_MODEL = os.getenv("AI_MODEL", "llama-3.1-8b-instant")
BREAKING_NEWS_THRESHOLD = 8

DONOR_CHANNELS = [
    "u_now",
    "uniannet",
    "UaOnlii",
    "insiderUKR",
    "exilenova_plus",
    "supernova_plus",
    "chp_zahid",
    "zsuwar",
    "nefedoov365",
    "inslandd"
]

AYRSHARE_API_KEY = os.getenv("AYRSHARE_API_KEY", "")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MEDIA_DIR = os.path.join(BASE_DIR, "media")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
os.makedirs(MEDIA_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)
