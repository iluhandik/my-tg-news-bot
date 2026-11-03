import json
import re
import html
import requests
from typing import Dict, Any, Optional
from config import AI_PROVIDER, AI_API_KEY, AI_MODEL, BREAKING_NEWS_THRESHOLD

SYSTEM_PROMPT = f"""Ти — головний редактор всеукраїнського новинного Telegram-каналу.
Твоє завдання — перетворити сиру новину на якісний, професійний пост для Telegram.

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

# Список перевірених безкоштовних моделей Groq у порядку пріоритету
GROQ_MODELS = [
    "llama-3.1-8b-instant",
    "llama3-8b-8192",
    "llama-3.3-70b-versatile",
    "mixtral-8x7b-32768"
]

def process_news_with_ai(raw_text: str) -> Optional[Dict[str, Any]]:
    """
    Обробляє новину через AI.
    Якщо обрана модель недоступна на безкоштовному тарифі (404),
    автоматично перемикається на гарантовано доступну llama-3.1-8b-instant.
    """
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

    # Моделі для спроби
    models_to_try = [AI_MODEL] if AI_MODEL else []
    for m in GROQ_MODELS:
        if m not in models_to_try:
            models_to_try.append(m)

    api_url = "https://api.groq.com/openai/v1/chat/completions" if AI_PROVIDER.lower() == "groq" else "https://api.openai.com/v1/chat/completions"

    for model_name in models_to_try:
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
            
            # Якщо модель недоступна (404) — пробуємо наступну в списку!
            if resp.status_code == 404:
                print(f"[AI]: Модель {model_name} недоступна (404). Пробуємо альтернативну...")
                continue

            if resp.status_code != 200:
                print(f"[AI ПОМИЛКА {resp.status_code} на моделі {model_name}]: {resp.text[:150]}")
                continue

            data = resp.json()
            parsed = json.loads(data["choices"][0]["message"]["content"])

            if parsed.get("is_ad", False):
                print("[AI ФІЛЬТР]: AI розпізнав цей пост як рекламу/спам і відхилив його.")
                return None

            print(f"[AI]: Успішно оброблено моделлю {model_name}!")
            return parsed

        except Exception as e:
            print(f"[AI ПОМИЛКА на {model_name}]: {e}")
            continue

    print("[AI ПОМИЛКА]: Жодна з моделей Groq не змогла відповісти.")
    return None
