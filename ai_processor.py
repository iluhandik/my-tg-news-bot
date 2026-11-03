import json
import re
import html
import requests
from typing import Dict, Any, Optional
from config import AI_PROVIDER, AI_API_KEY, AI_MODEL, BREAKING_NEWS_THRESHOLD

SYSTEM_PROMPT = f"""Ти — головний редактор всеукраїнського новинного Telegram-каналу.
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
    Обробляє новину через AI за допомогою бібліотеки requests з правильним User-Agent
    (щоб Cloudflare на api.groq.com не блокував запит помилкою 403 Forbidden).
    """
    cleaned_input = clean_donor_mentions(raw_text)
    if not cleaned_input or len(cleaned_input) < 25:
        return None

    api_key = (AI_API_KEY or "").strip()
    if not api_key or api_key.startswith("YOUR_"):
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
        
        # Обов'язковий User-Agent щоб захист Cloudflare на серверах Groq не відбивав запит як 403 Forbidden
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }

        resp = requests.post(api_url, json=payload, headers=headers, timeout=30)
        
        if resp.status_code != 200:
            print(f"[AI ПОМИЛКА {resp.status_code}]: {resp.text[:200]}")
            return None

        data = resp.json()
        parsed = json.loads(data["choices"][0]["message"]["content"])
        
        if parsed.get("is_ad", False):
            print("[AI ФІЛЬТР]: AI розпізнав цей пост як рекламу/спам і відхилив його.")
            return None
            
        return parsed

    except Exception as e:
        print(f"[AI ПОМИЛКА]: Не вдалося викликати AI ({AI_PROVIDER}): {e}")
        return None
