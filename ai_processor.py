import json
import re
import html
import requests
from typing import Dict, Any, Optional, List
from config import AI_PROVIDER, AI_API_KEY, BREAKING_NEWS_THRESHOLD

SYSTEM_PROMPT = f"""Ти — головний редактор всеукраїнського новинного Telegram-каналу.
Твоє завдання — відібрати тільки РЕАЛЬНІ важливі новини та перетворити їх на якісний пост для Telegram.

СУВОРІ ПРАВИЛА:
1. МОВА: 100% чиста українська мова. Якщо текст російською — повністю переклади. Жодного російського слова!
2. ОЧИЩЕННЯ ВІД ДОНОРІВ:
   - ПОВНІСТЮ видали водяні знаки та скорочення донорів: "УС /", "УС", "Инсайдер", "УНИАН", "Supernova", "Труха", "Цензор", "Захід".
   - Видали всі нікнейми (@...) та всі посилання (t.me/..., http/https).
3. ЗАБОРОНА РУТИННИХ ТРИВОГ ТА БЕЗЗМІСТОВНОГО СПАМУ:
   - КАТЕГОРИЧНО ЗАБОРОНЕНО публікувати рутинні сповіщення про тривоги без наслідків ("жовта тривога", "загроза БПЛА в Києві/областях", "перебувайте в укриттях" без фактів збиттів чи вибухів).
   - КАТЕГОРИЧНО ЗАБОРОНЕНО публікувати меми, випадкові коментарі, підрахунки лайків/фанатів чи нісенітниці (як спам про ЯНОС).
   - Якщо новина є рутинною тривогою без подій або спамом — встанови "is_ad": true!
4. ШРИФТ ТА ОФОРМЛЕННЯ (БЕЗ КАПСУ!):
   - Заголовок пиши ЗВИЧАЙНИМИ РЕЧЕННЯМИ (перша літера велика, решта маленькі). ЗАБОРОНЕНО писати заголовок КАПСОМ!
   - На початку заголовка постав 1 доречний емодзі: ⚡️, 🚨, 🇺🇦, 💥, ⚔️, 💡, 📢.
   - Сам заголовок виділи жирним шрифтом: **Заголовок**.
   - Під заголовком — 1-2 короткі, чіткі абзаци з головною суттю події без води.
5. ЗАБОРОНИ:
   - ЖОДНИХ підписів у кінці (не пиши "Підписатися", "Джерело", посилання).
   - ЖОДНОГО значка "💬" (його ставить тільки автор каналу особисто).

Формат відповіді СТРОГО валідний JSON:
{{
  "is_ad": false,
  "telegram_post": "Текст якісного посту для Telegram українською мовою без капсу і без підписів",
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
    """Обробляє новину через AI з суворим контролем змісту."""
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
                    {"role": "user", "content": f"Оброби цей текст новини:\n{cleaned_input}"}
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

            # Якщо AI розпізнав це як рекламу, безглуздий спам або рутинну тривогу без подій — відхиляємо!
            if parsed.get("is_ad", False):
                print("[AI ФІЛЬТР]: AI розпізнав цей пост як спам/рутинну тривогу і відхилив його.")
                return None

            return parsed

        except Exception as e:
            continue

    return None
