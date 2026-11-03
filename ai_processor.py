import json
import re
import html
import requests
from typing import Dict, Any, Optional, List
from config import AI_PROVIDER, AI_API_KEY, BREAKING_NEWS_THRESHOLD

SYSTEM_PROMPT = f"""Ти — суворий фактчекер та редактор всеукраїнського новинного каналу.
Твоє завдання — переписати сиру новину чистою українською мовою, зберігаючи ВИКЛЮЧНО РЕАЛЬНІ ФАКТИ.

ГОЛОВНЕ ПРАВИЛО: КАТЕГОРИЧНА ЗАБОРОНА ВИГАДУВАТИ ТА ФАНТАЗУВАТИ (АНТИФЕЙК):
1. ЖОДНИХ ВИГАДАНИХ ПРІЗВИЩ ТА ПОСАДОВЦІВ:
   - Суворо заборонено вигадувати мерів, чиновників, голів міськрад (наприклад, вигадувати якихось «Олександрів Ковальчуків» у Сімферополі чи інших містах)!
   - Використовуй ТІЛЬКИ ті імена, які прямо названі в оригінальному тексті донора. Якщо імені немає — пиши безособово: «за попередніми даними», «повідомляють місцеві пабліки».
2. ЖОДНИХ ВИГАДАНИХ ЦИТАТ:
   - Блок <blockquote> використовуй ВИКЛЮЧНО тоді, коли в оригіналі Є реальна пряма мова конкретної людини (наприклад: Володимир Зеленський сказав: «...»).
   - Якщо прямої мови в оригіналі НЕМАЄ — КАТЕГОРИЧНО ЗАБОРОНЕНО вигадувати цитати від себе! Просто напиши факти.
3. ТОЧНІСТЬ ДО ОКУПОВАНИХ ТЕРИТОРІЙ:
   - Пам'ятай: Сімферополь, Крим, Донецьк, Луганськ, Мелітополь — тимчасово окуповані. Там немає українських мерів чи міськрад.
4. МОВА ТА СТИЛЬ (БЕЗ КАЛЬОК):
   - 100% грамотна українська мова без суржику та без дослівного копіювання російських фраз.
   - Заголовок: <b>жирним шрифтом</b> з емодзі, звичайними реченнями (БЕЗ КАПСУ!).
   - Тіло: 1-2 короткі правдиві абзаци суто за фактами донора.
   - Жодних підписів у кінці та жодного значка "💬".

Формат відповіді СТРОГО валідний JSON:
{{
  "is_ad": false,
  "telegram_post": "Текст правдивого посту українською мовою без вигадок і без фейкових цитат",
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
    """Автоматично запитує список моделей Groq."""
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
            preferred_order = [
                "openai/gpt-oss-120b",
                "openai/gpt-oss-20b",
                "qwen-2.5-32b",
                "llama-3.3-70b-versatile",
                "llama-3.1-8b-instant"
            ]
            sorted_models = []
            for pref in preferred_order:
                if pref in active_models:
                    sorted_models.append(pref)
            for m in active_models:
                if m not in sorted_models:
                    sorted_models.append(m)

            CACHED_AVAILABLE_MODELS = sorted_models
            return sorted_models
    except Exception:
        pass

    return ["openai/gpt-oss-120b", "llama-3.1-8b-instant"]

def process_news_with_ai(raw_text: str) -> Optional[Dict[str, Any]]:
    """Обробляє новину через AI з нульовою толерантністю до вигадок і фейків."""
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
                    {"role": "user", "content": f"Оброби цей текст новини. Суворо базуйся ТІЛЬКИ на наведених фактах, НЕ вигадуй людей і цитат:\n{cleaned_input}"}
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0.1  # Мінімальна температура, щоб виключити будь-які галюцинації
            }

            resp = requests.post(api_url, json=payload, headers=headers, timeout=25)
            if resp.status_code == 404:
                continue

            if resp.status_code != 200:
                continue

            data = resp.json()
            parsed = json.loads(data["choices"][0]["message"]["content"])

            if parsed.get("is_ad", False):
                return None

            return parsed

        except Exception as e:
            continue

    return None
