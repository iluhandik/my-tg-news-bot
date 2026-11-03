import json
import re
import urllib.request
import urllib.parse
from typing import Dict, Any
from config import AI_PROVIDER, AI_API_KEY, AI_MODEL, CHANNEL_INVITE_LINK, BREAKING_NEWS_THRESHOLD

SYSTEM_PROMPT = f"""Ти — професійний редактор всеукраїнського новинного Telegram-каналу.
Канал висвітлює головні оперативні новини України: ситуація на фронті, ракетні/дронові небезпеки, енергетика, важливі державні рішення та міжнародні події.

Суворі правила оформлення постів:
1. ПОВНІСТЮ ОЧИСТИТИ текст від будь-яких згадок першоджерел:
   - Видалити всі назви каналів ("Инсайдер", "Украина Сейчас", "УНИАН", "Supernova", "Труха", "Цензор", "Захід" тощо).
   - Видалити всі нікнейми з собачкою (@...).
   - Видалити всі посилання (t.me/..., http, https).
   - Видалити всі заклики підписатися та рекламу.
2. ПЕРЕКЛАСТИ НА ЧИСТУ УКРАЇНСЬКУ МОВУ (без російських слів, кальок і суржику).
3. НІЯКИХ ПІДПИСІВ У КІНЦІ ПОСТУ:
   - НЕ додавай жодних підписів типу "👉 Підписатися", "Джерело", "Стежте за новинами". Пост має закінчуватися сухими фактами новини!
4. ЗАБОРОНА ЗНАЧКА "💬":
   - Значок "💬" використовує особисто автор каналу для своїх думок. НЕ використовуй його у новинах.
5. СТРУКТУРА ПОСТУ:
   - Влучний емодзі на початку (⚡️, 🚨, 🇺🇦, 💥, ⚔️, 📢) + сильний, інформативний заголовок жирним шрифтом.
   - 2-3 короткі, чіткі абзаци з головними фактами без зайвої води.
6. ВІДЕО ДЛЯ СОЦМЕРЕЖ (TIKTOK / REELS / SHORTS):
   - Якщо новина масштабна (оцінка >= {BREAKING_NEWS_THRESHOLD}), підготувати динамічний 30-секундний сценарій для вертикального відео.
   - У сценарії для соцмереж заклик підписатися на Telegram залишається ("Більше деталей у нашому Telegram-каналі за посиланням у шапці профілю!").

Формат відповіді СТРОГО валідний JSON:
{{
  "telegram_post": "Готовий текст посту для Telegram українською мовою з Markdown (БЕЗ підпису в кінці)",
  "news_score": 8,
  "is_mega_news": true,
  "video_headline": "КОРОТКИЙ ЗАГОЛОВОК КАПСОМ ДЛЯ ВІДЕО (3-5 СЛІВ)",
  "video_script": "Текст дикторської озвучки для Shorts/Reels/TikTok",
  "social_caption": "Опис для відео в TikTok/Reels/Shorts із закликом перейти в Telegram"
}}
"""

def clean_donor_mentions(text: str) -> str:
    """Видаляє посилання та назви сторонніх каналів."""
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"t\.me/\S+", "", text)
    text = re.sub(r"@[a-zA-Z0-9_]+", "", text)
    text = re.sub(r"(?i)(подписаться|підписатися|прислать новость|надіслати новину|инсайдер|украина сейчас|униан|supernova|труха)\b[^\n]*", "", text)
    return text.strip()

def process_news_with_ai(raw_text: str) -> Dict[str, Any]:
    """Обробляє та адаптує новину через AI."""
    cleaned_input = clean_donor_mentions(raw_text)
    if not cleaned_input:
        cleaned_input = raw_text

    if not AI_API_KEY or AI_API_KEY.startswith("YOUR_"):
        return fallback_process(cleaned_input)

    try:
        api_url = "https://api.groq.com/openai/v1/chat/completions" if AI_PROVIDER.lower() == "groq" else "https://api.openai.com/v1/chat/completions"
        payload = {
            "model": AI_MODEL,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Сирий текст новини:\n{cleaned_input}"}
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.3
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {AI_API_KEY}"
        }
        req = urllib.request.Request(api_url, data=json.dumps(payload).encode("utf-8"), headers=headers)
        with urllib.request.urlopen(req, timeout=30) as resp:
            res = json.loads(resp.read().decode("utf-8"))
            return json.loads(res["choices"][0]["message"]["content"])
    except Exception as e:
        print(f"Помилка виклику AI ({AI_PROVIDER}): {e}")
        return fallback_process(cleaned_input)

def fallback_process(text: str) -> Dict[str, Any]:
    """Резервна обробка без підпису."""
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    first_line = lines[0][:80] if lines else "ВАЖЛИВА НОВИНА"
    body = "\n\n".join(lines[1:3]) if len(lines) > 1 else text[:300]
    
    is_breaking = any(w in text.lower() for w in ["вибух", "війна", "терміново", "удар", "наступ", "атака", "ракета", "зсу", "дрон"])
    score = 8 if is_breaking else 5

    post = f"⚡️ **{first_line.upper()}**\n\n{body}"

    script = (
        f"Термінова новина! {first_line}. "
        f"Більше оперативних деталей та відео без цензури — у нашому Telegram-каналі. "
        f"Посилання закріплено в описі профілю, переходьте та підписуйтесь!"
    )

    return {
        "telegram_post": post,
        "news_score": score,
        "is_mega_news": score >= BREAKING_NEWS_THRESHOLD,
        "video_headline": first_line[:40].upper(),
        "video_script": script,
        "social_caption": f"{first_line}\n\nБільше деталей у Telegram: {CHANNEL_INVITE_LINK}\n#новини #україна #shorts #reels #tiktok"
    }
