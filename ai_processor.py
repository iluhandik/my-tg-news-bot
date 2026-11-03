import json
import re
import html
import requests
from typing import Dict, Any, Optional, List
from config import AI_PROVIDER, AI_API_KEY, BREAKING_NEWS_THRESHOLD

SYSTEM_PROMPT = f"""Ти — професійний український журналіст та шеф-редактор новинного каналу.
Твоє завдання — переписати сиру новину на БЕЗДОГАННУ, ЖИВУ УКРАЇНСЬКУ МОВУ.

КАТЕГОРИЧНІ ПРАВИЛА МОВИ ТА СТИЛЮ (ЗАБОРОНА КАЛЬОК З РОСІЙСЬКОЇ!):
1. ПОВНА ЛІКВІДАЦІЯ РОСІЙСЬКИХ КАЛЬОК ТА СУРЖИКУ:
   - Жодних дослівних кальок:
     * НЕ «Сем'я», а «Сім'я»!
     * НЕ «пожежний вибух», а «пожежа та вибухи» або «масштабне займання»!
     * НЕ «відео розповсюдився», а «відео поширилося» або «на оприлюднених відео» (відео — середній рід, воно)!
     * НЕ «великий вогонь», а «сильне полум'я» або «масштабна пожежа»!
     * НЕ «постраждалих не повідомляють», а «про постраждалих наразі не повідомляється» або «інформація про постраждалих уточнюється»!
     * НЕ «внаслідок удару вразив», а «влучив у...» або «пошкоджено будівлю»!
   - Перебудовуй речення природно для української граматики, не копіюй порядок слів російського тексту.

2. ОФОРМЛЕННЯ ТА HTML-РОЗМІТКА:
   - Заголовок: <b>жирним шрифтом</b> з доречним емодзі (⚡️, 🚨, 💥, 🇺🇦, ⚔️, 📢). Звичайними реченнями (БЕЗ КАПСУ!).
   - Тіло новини: 2 короткі абзаци з фактами. Ключові дані (місто, об'єкт, цифри) виділяй <b>жирним</b>.
   - Цитати: якщо є заява або пряма мова посадовця/експерта — оформлюй через <blockquote>«Цитата...»</blockquote>.
   - ЖОДНИХ підписів у кінці (не пиши "Підписатися", "Джерело", посилання).
   - ЖОДНОГО значка "💬".
   - Видали хвости донорів ("УС /", "Инсайдер", "УНИАН", лінки t.me).

3. ЗРАЗОК ІДЕАЛЬНОЇ РОБОТИ:
   Вхід: "В Одессе в результате ракетного удара горит ТЦ Семья, сильный пожарный взрыв. На видео, что распространился в сети, виден большой огонь. Пострадавших не сообщают."
   Вихід: "⚡️ <b>В Одесі горить торговельний центр «Сім'я» після ворожого удару</b>\n\nУ місті спалахнула масштабна пожежа внаслідок влучання ракети. На оприлюднених у мережі кадрах видно сильне полум'я, густий дим та руйнування будівлі.\n\nЗа даними ДСНС, інформація про постраждалих наразі уточнюється, на місці працюють рятувальники."

Формат відповіді СТРОГО валідний JSON:
{{
  "is_ad": false,
  "telegram_post": "Текст бездоганного українського посту з HTML-тегами <b>, <i> та <blockquote>",
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
    """Автоматично запитує список доступних моделей Groq, надаючи пріоритет розумним великим моделям."""
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
            
            # Сортуємо так, щоб потужніші та грамотніші моделі йшли першими
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
    """Обробляє новину через AI з суворим контролем літературної української мови."""
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
                    {"role": "user", "content": f"Переклади та оформи цю новину живою українською мовою:\n{cleaned_input}"}
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
                return None

            return parsed

        except Exception as e:
            continue

    return None
