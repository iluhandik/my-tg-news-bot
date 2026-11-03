import json
import re
import html
import requests
from typing import Dict, Any, Optional, List
from config import AI_PROVIDER, AI_API_KEY, BREAKING_NEWS_THRESHOLD

SYSTEM_PROMPT = f"""Ти — головний редактор всеукраїнського новинного Telegram-каналу.
Твоє завдання — відібрати тільки РЕАЛЬНІ важливі події та оформити їх у виразний, красивий та структурований пост для Telegram із використанням HTML-розмітки.

ПРАВИЛА ВИРАЗНОГО РЕДАГУВАННЯ ТЕКСТУ:
1. ГОЛОВНА НОВИНА (ЗАГОЛОВОК):
   - Завжди виділяй <b>жирним шрифтом</b> (тег <b>...</b>).
   - На початку постав 1 влучний емодзі: ⚡️, 🚨, 🇺🇦, 💥, ⚔️, 💡, 📢.
   - Заголовок пиши звичайними реченнями, ЗАБОРОНЕНО писати КАПСОМ!
   - Приклад: ⚡️ <b>США оголосили про новий пакет допомоги на $800 млн</b>
2. ОСНОВНИЙ ТЕКСТ:
   - 1-2 чіткі абзаци з фактами.
   - Головні акценти, ключові цифри чи важливі деталі виділяй <b>жирним</b>.
3. ЦИТАТИ ТА ПРЯМА МОВА:
   - Якщо в пості є пряма мова або заява посадовця (Зеленський, Сирський, Шмигаль, Генштаб, Білий дім тощо), ОБОВ'ЯЗКОВО оформи її як цитату Telegram через тег <blockquote>...</blockquote>:
     Приклад:
     <blockquote>«Ми повинні нарощувати власне виробництво дронів та далекобійних ракет», — заявив президент.</blockquote>
4. ЗАБОРОНИ:
   - ЖОДНИХ підписів у кінці (не пиши "Підписатися", "Джерело", посилання).
   - ЖОДНОГО значка "💬" (його ставить тільки автор каналу особисто).
   - Якщо це рутинна тривога без подій або спам — встанови "is_ad": true!
5. МОВА: 100% чиста українська мова.

Формат відповіді СТРОГО валідний JSON:
{{
  "is_ad": false,
  "telegram_post": "Текст виразного посту для Telegram з HTML тегами <b>, <i> та <blockquote>",
  "news_score": 7,
  "is_mega_news": false,
  "video_headline": "Короткий заголовок для відео",
  "video_script": "Текст дикторської озвучки для відео",
  "social_caption": "Опис для соцмереж"
}}
"""

def clean_donor_mentions(text: str) -> str:
    """Видаляє HTML сутності, посилання та назви сторонніх каналів."""
    text = html.unescape(text)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"t\.me/\S+", "", text)
    text = re.sub(r"@[a-zA-Z0-9_]+", "", text)
    text = re.sub(r"(?i)\b(ус\s*/?|украина сейчас|инсайдер|униан|supernova|труха)\b", "", text)
    return text.strip()

CACHED_AVAILABLE_MODELS: List[str] = []

def get_available_models(api_key: str, headers: dict) -> List[str]:
    """Автоматично запитує список моделей, доступних для вашого акаунта Groq."""
    global CACHED_AVAILABLE_MODELS
    if CACHED_AVAILABLE_MODELS:
        return CACHED_AVAILABLE_MODELS

    try:
        url = "https://api.groq.com/openai/v1/models"
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code == 200:
            data = resp.json().get("data", [])
            active_models = [
                m["id"] for m in data 
                if not any(x in m["id"].lower() for x in ["whisper", "guard", "vision", "embed"])
            ]
            CACHED_AVAILABLE_MODELS = active_models
            return active_models
    except Exception:
        pass

    return ["llama-3.1-8b-instant", "llama-3.3-70b-versatile", "gemma2-9b-it"]

def process_news_with_ai(raw_text: str) -> Optional[Dict[str, Any]]:
    """Обробляє новину через AI з виразним HTML-форматуванням під Telegram."""
    cleaned_input = clean_donor_mentions(raw_text)
    if not cleaned_input or len(cleaned_input) < 25:
        return None

    api_key = (AI_API_KEY or "").strip()
    if not api_key or api_key.startswith("YOUR_"):
        print("[AI ПОМИЛКА]: Ключ AI_API_KEY не вказано в Secrets!")
        return None

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    available_models = get_available_models(api_key, headers)
    if not available_models:
        return None

    api_url = "https://api.groq.com/openai/v1/chat/completions"

    for model_name in available_models:
        try:
            payload = {
                "model": model_name,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": f"Оброби цей текст новини та зроби виразне оформлення з жирним шрифтом і цитатами:\n{cleaned_input}"}
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0.2
            }

            resp = requests.post(api_url, json=payload, headers=headers, timeout=25)
            if resp.status_code == 404:
                continue

            if resp.status_code != 200:
                continue

            data = resp.json()
            parsed = json.loads(data["choices"][0]["message"]["content"])

            if parsed.get("is_ad", False):
                print("[AI ФІЛЬТР]: AI розпізнав цей пост як спам/рутинну тривогу і відхилив його.")
                return None

            return parsed

        except Exception as e:
            continue

    return None
