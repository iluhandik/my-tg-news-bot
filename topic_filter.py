import json
import os
import re
import time
from typing import List, Dict

TOPICS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "published_topics.json")
MAX_TOPIC_AGE_HOURS = 12

COMMON_STOP_WORDS = {
    "сьогодні", "зараз", "вчора", "цього", "після", "також", "було", "проти", "може",
    "через", "буде", "тільки", "цьому", "коли", "якщо", "були", "вони", "яких", "який",
    "яка", "яке", "повідомляє", "новини", "року", "потім", "перед", "під", "над", "щоб",
    "стало", "відомо", "заявив", "словами", "джерело", "офіційно", "увага", "терміново",
    "внаслідок", "відповідно", "разом", "перший", "другий", "можуть", "бути", "своїх"
}

def load_topics() -> List[Dict]:
    if os.path.exists(TOPICS_FILE):
        try:
            with open(TOPICS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []

def save_topics(topics: List[Dict]):
    try:
        with open(TOPICS_FILE, "w", encoding="utf-8") as f:
            json.dump(topics, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Помилка збереження тем: {e}")

def extract_stems(text: str) -> List[str]:
    """Витягує сутнісні корені слів для виявлення однакових подій різними словами."""
    clean = re.sub(r"[^\w\s]", " ", text.lower(), flags=re.UNICODE)
    words = clean.split()
    stems = set()
    for w in words:
        if len(w) >= 4 and w not in COMMON_STOP_WORDS:
            stem = w[:5] if len(w) >= 5 else w
            stems.add(stem)
    return list(stems)

def is_topic_already_covered(text: str) -> bool:
    """
    Перевіряє, чи публікувалася вже новина на цю саму тему за останні 12 годин
    (наприклад, одна й та сама атака на Волинь, але написана іншим каналом іншими словами).
    """
    now = time.time()
    min_time = now - (MAX_TOPIC_AGE_HOURS * 3600)
    topics = load_topics()
    current_stems = set(extract_stems(text))

    if len(current_stems) < 2:
        return False

    for t in topics:
        if t.get("timestamp", 0) < min_time:
            continue
        prev_stems = set(t.get("stems", []))
        common = current_stems.intersection(prev_stems)
        # Якщо збігається від 2 ключових слів — це той самий інцидент/подія
        if len(common) >= 2:
            print(f"[АНТИДУБЛЬ ТЕМИ]: Ця новина вже була опублікована ('{t.get('headline', '')[:35]}...'). Спільна тема: {common}. Пропускаємо повтор!")
            return True

    return False

def register_published_topic(headline: str, text: str):
    """Фіксує опубліковану тему, щоб інший донор не зміг запостити її повторно."""
    topics = load_topics()
    stems = extract_stems(headline + " " + text)
    topics.append({
        "headline": headline,
        "stems": stems,
        "timestamp": time.time()
    })
    # Залишаємо тільки останні 200 тем
    if len(topics) > 200:
        topics = topics[-200:]
    save_topics(topics)
