import json
import re
import html
import requests
from typing import Dict, Any, Optional, List
from config import AI_PROVIDER, AI_API_KEY, BREAKING_NEWS_THRESHOLD

SYSTEM_PROMPT = f"""Ти — головний редактор всеукраїнського новинного Telegram-каналу.
Твоє завдання — перетворити важливу новину на виразний, грамотний та структурований пост для Telegram.

ЯКІ НОВИНИ Є ВАЖЛИВИМИ ТА ПОВИННІ ПУБЛІКУВАТИСЯ (ОБОВ'ЯЗКОВО ПРОПУСКАЙ):
- Ситуація на фронті, бойові дії, зведення Генштабу, контратаки, знищення ворожої техніки.
- Ракетні та дронові удари, робота ППО, наслідки обстрілів, вибухи, збиття цілей.
- Енергетика: відключення світла, стан енергомережі, блекаути, ремонти.
- Державні рішення: закони, заяви Зеленського, Сирського, уряду, допомога від США та ЄС.
- Важливі надзвичайні події та резонансні світові новини, що впливають на Україну.
*ВАЖЛИВО: Не будь надмірно прискіпливим! Якщо подія дійсно відбулася і цікава українцям — обов'язково публікуй її!*

ЩО КАТЕГОРИЧНО ВІДКИДАТИ:
1. ДУБЛІКАТИ: якщо новина про цю подію вже є в списку останніх постів каналу — встанови "is_duplicate": true.
2. РЕКЛАМУ ТА СПАМ: казино, кол-центри, заробітки, розіграші — встанови "is_ad": true.
3. РУТИННІ СИРЕНИ: сповіщення про сирену без фактів прильотів/збиттів ("просто тривога в області").
4. МЕМИ ТА БЕЗГЛУЗДИЙ СПАМ (як пости про лайки чи фанатів).

ПРАВИЛА ОФОРМЛЕННЯ:
1. МОВА: 100% чиста солов'їна мова. Жодних кальок: не «Сем'я», а «Сім'я»; не «пожежний вибух», а «масштабна пожежа»; не «відео розповсюдився», а «відео поширилося».
2. ЗАГОЛОВОК: <b>жирним шрифтом</b> з 1 емодзі на початку. Звичайними реченнями, БЕЗ КАПСУ!
3. ТІЛО: 1-2 чіткі абзаци з фактами.
4. ЦИТАТИ: якщо в оригіналі реально є пряма мова конкретної особи — оформи через <blockquote>...</blockquote>.
5. АНТИФЕЙК: працюй суто за фактами донора, КАТЕГОРИЧНО ЗАБОРОНЕНО вигадувати мерів, людей чи цитати!
6. ЖОДНИХ підписів у кінці та жодного значка "💬".

Формат відповіді СТРОГО валідний JSON:
{{
  "is_duplicate": false,
  "is_ad": false,
  "telegram_post": "Текст якісного українського посту з HTML-тегами <b>, <i> та <blockquote>",
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
    text = re.sub(r"(?i)\b(ус\s*/?|украина сейчас|инсайдер|униан|supernova|труха|нефедов)\b", "", text)
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

def process_news_with_ai(raw_text: str, recent_channel_posts: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
    """Обробляє новину через AI з балансом між фільтрацією сміття та пропуском реальних новин."""
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

    recent_context = ""
    if recent_channel_posts:
        recent_context = "ОСЬ ОСТАННІ ПОСТИ, ЯКІ ВЖЕ Є В КАНАЛІ КОРИСТУВАЧА:\n" + "\n".join([f"- {p}" for p in recent_channel_posts[:12]]) + "\n\n"

    user_prompt = f"{recent_context}НОВА ПОДІЯ ВІД ДОНОРА ДЛЯ АНАЛІЗУ:\n{cleaned_input}"
    api_url = "https://api.groq.com/openai/v1/chat/completions"

    for model_name in available_models:
        try:
            payload = {
                "model": model_name,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt}
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0.1
            }

            resp = requests.post(api_url, json=payload, headers=headers, timeout=25)
            if resp.status_code == 404:
                continue

            if resp.status_code != 200:
                continue

            data = resp.json()
            parsed = json.loads(data["choices"][0]["message"]["content"])

            if parsed.get("is_duplicate", False):
                print(f"[AI ДУБЛЬ]: Новина про цю подію вже є в каналі. Пропускаємо повтор!")
                return None

            if parsed.get("is_ad", False):
                print("[AI ФІЛЬТР]: AI розпізнав цей пост як спам/нерелевантний і відхилив його.")
                return None

            return parsed

        except Exception as e:
            continue

    return None
