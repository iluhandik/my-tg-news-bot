import json
import urllib.request
import urllib.parse
from typing import Dict, Any
from config import AI_PROVIDER, AI_API_KEY, AI_MODEL, CHANNEL_NAME, CHANNEL_USERNAME, CHANNEL_INVITE_LINK, BREAKING_NEWS_THRESHOLD

SYSTEM_PROMPT = f"""Ти — професійний шеф-редактор новинного Telegram-каналу "{CHANNEL_NAME}" ({CHANNEL_USERNAME}).
Твоє завдання:
1. Прийняти сирий текст новини та повністю очистити його від будь-яких згадок першоджерела (жодних назв сайтів, телеграм-каналів, авторів чи посилань на сторонні медіа).
2. Написати авторський, динамічний і структурований пост для Telegram українською мовою.
3. Оцінити масштаб новини за шкалою від 1 до 10 (де 10 — світова сенсація, надзвичайна подія, історичне рішення).
4. Якщо масштаб новини >= {BREAKING_NEWS_THRESHOLD}, написати вірусний 30-40 секундний сценарій для короткого відео (TikTok / Reels / YouTube Shorts) з потужним гачком (Hook) та обов'язковим закликом підписатися на наш канал.

Формат посту для Telegram:
- Яскравий заголовок з емодзі (наприклад: ⚡️ ТЕРМІНОВО | НАЗВА)
- Суть новини у 2-3 коротких структурованих абзацах або булетах. Тільки сухі факти без "води".
- В кінці посту обов'язковий підпис:
👉 **Підписатися на {CHANNEL_NAME}** ({CHANNEL_INVITE_LINK})
#новини #україна

Твоя відповідь ПОВИННА бути строго у форматі валідного JSON:
{{
  "telegram_post": "Текст готового посту з емодзі та розміткою Markdown",
  "news_score": 8,
  "is_mega_news": true,
  "video_headline": "КОРОТКИЙ ЗАГОЛОВОК ДЛЯ ВІДЕО КАПСОМ (3-5 СЛІВ)",
  "video_script": "Текст дикторської озвучки для відео на 30-40 секунд. Без ремарок, чисто текст для читання голосом. В кінці: 'Більше деталей та ексклюзивної аналітики читайте у нашому телеграм-каналі за посиланням в описі!'"
}}
"""

def process_news_with_ai(title: str, content: str) -> Dict[str, Any]:
    """Відправляє новину в LLM для рерайту та аналізу."""
    user_content = f"Заголовок джерела: {title}\nТекст новини: {content}"
    
    # Якщо ключ не вказано, генеруємо розумний локальний шаблон
    if not AI_API_KEY or AI_API_KEY.startswith("YOUR_"):
        return fallback_process(title, content)
        
    try:
        if AI_PROVIDER.lower() == "openai" or AI_PROVIDER.lower() == "groq":
            api_url = "https://api.openai.com/v1/chat/completions" if AI_PROVIDER.lower() == "openai" else "https://api.groq.com/openai/v1/chat/completions"
            payload = {
                "model": AI_MODEL,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_content}
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0.4
            }
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {AI_API_KEY}"
            }
            req = urllib.request.Request(api_url, data=json.dumps(payload).encode("utf-8"), headers=headers)
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                content_str = result["choices"][0]["message"]["content"]
                return json.loads(content_str)
                
    except Exception as e:
        print(f"Помилка виклику AI API ({AI_PROVIDER}): {e}")
        return fallback_process(title, content)

def fallback_process(title: str, content: str) -> Dict[str, Any]:
    """Резервний процесор без звернення до API (для тестування та надійного запуску)."""
    # Евристична оцінка важливості за ключовими словами
    breaking_words = ["терміново", "війна", "вибух", "ракет", "атака", "договір", "закон", "президент", "єс", "нато", "долар", "наступ", "переговори"]
    is_breaking = any(word in (title + " " + content).lower() for word in breaking_words)
    score = 8 if is_breaking else 5
    
    post = f"⚡️ **{title.upper()}**\n\n{content[:400]}...\n\n👉 **Підписатися на {CHANNEL_NAME}** ({CHANNEL_INVITE_LINK})\n#новини #головне"
    
    video_script = (
        f"Термінова новина! {title}. "
        f"За наявною інформацією, {content[:200]}. "
        f"Слідкуйте за розвитком подій! Більше оперативних подробиць у нашому Telegram-каналі {CHANNEL_USERNAME}. "
        f"Посилання закріплено в описі профілю, підписуйтесь просто зараз!"
    )
    
    return {
        "telegram_post": post,
        "news_score": score,
        "is_mega_news": score >= BREAKING_NEWS_THRESHOLD,
        "video_headline": title[:40].upper(),
        "video_script": video_script
    }
