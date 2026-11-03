import json
import re
import html
import urllib.request
import urllib.parse
from typing import Dict, Any, Optional
from config import AI_PROVIDER, AI_API_KEY, AI_MODEL, BREAKING_NEWS_THRESHOLD

SYSTEM_PROMPT = f"""Ти — головний редактор всеукраїнського новинного каналу.
Твоє завдання — перетворити сиру новину на професійний, якісний пост для Telegram.

СУВОРІ ПРАВИЛА:
1. МОВА: 100% чиста українська мова. Якщо текст російською мовою — повністю і грамотно переклади його на українську. Жодного російського слова!
2. ОЧИЩЕННЯ ВІД ДОНОРІВ:
   - ПОВНІСТЮ видали будь-які хвости, водяні знаки та скорочення донорів: "УС /", "УС", "Инсайдер", "УНИАН", "Supernova", "Труха", "Цензор", "Захід".
   - Видали всі нікнейми (@u_now, @insiderUKR тощо) та всі посилання (t.me/..., http/https).
3. ШРИФТ ТА ОФОРМЛЕННЯ (БЕЗ КАПСУ!):
   - Заголовок пиши ЗВИЧАЙНИМИ РЕЧЕННЯМИ (перша літера велика, решта маленькі). ЗАБОРОНЕНО писати заголовок КАПСОМ!
   - На початку заголовка постав 1 доречний емодзі: ⚡️, 🚨, 🇺🇦, 💥, ⚔️, 💡, 📢.
   - Сам заголовок виділи жирним шрифтом: **Заголовок**.
   - Під заголовком — 1-2 короткі, чіткі абзаци з головною суттю події без води.
4. ЗАБОРОНИ:
   - ЖОДНИХ підписів у кінці (не пиши "Підписатися", "Джерело", посилання).
   - ЖОДНОГО значка "💬" (його ставить тільки автор каналу особисто).
   - Якщо новина виглядає як реклама (казино, заробітки, розіграші, боти, кол-центри) — встанови "is_ad": true.

Формат відповіді СТРОГО валідний JSON:
{{
  "is_ad": false,
  "telegram_post": "Текст якісного посту для Telegram українською мовою без капсу і без підписів",
  "news_score": 7,
  "is_mega_news": false,
  "video_headline": "Короткий заголовок для відео",
  "video_script": "Текст дикторської озвучки для відео (якщо news_score >= {BREAKING_NEWS_THRESHOLD})",
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

def process_news_with_ai(raw_text: str) -> Optional[Dict[str, Any]]:
    """
    Обробляє новину через AI.
    Якщо ключ AI не налаштований або запит не вдався — повертає None,
    щоб НЕ засмічувати канал сирим російським чи невідредагованим текстом!
    """
    cleaned_input = clean_donor_mentions(raw_text)
    if not cleaned_input or len(cleaned_input) < 25:
        return None

    if not AI_API_KEY or AI_API_KEY.startswith("YOUR_"):
        print("[AI ПОМИЛКА]: Ключ AI_API_KEY не вказано в Secrets! Бот не публікує сирий текст без обробки.")
        return None

    try:
        api_url = "https://api.groq.com/openai/v1/chat/completions" if AI_PROVIDER.lower() == "groq" else "https://api.openai.com/v1/chat/completions"
        payload = {
            "model": AI_MODEL,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Оброби цей текст новини:\n{cleaned_input}"}
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.2
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {AI_API_KEY}"
        }
        req = urllib.request.Request(api_url, data=json.dumps(payload).encode("utf-8"), headers=headers)
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            parsed = json.loads(data["choices"][0]["message"]["content"])
            
            # Якщо AI розпізнав це як рекламу — відкидаємо
            if parsed.get("is_ad", False):
                print("[AI ФІЛЬТР]: AI розпізнав цей пост як рекламу/спам і відхилив його.")
                return None
                
            return parsed
    except Exception as e:
        print(f"[AI ПОМИЛКА]: Не вдалося викликати AI ({AI_PROVIDER}): {e}")
        # Ніколи не публікуємо сирий текст, якщо AI не зміг його обробити!
        return None
