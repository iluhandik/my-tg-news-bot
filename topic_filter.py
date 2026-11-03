import json
import os
import re
import time
import hashlib
from typing import List, Dict, Set, Tuple

TOPICS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "published_topics.json")
MEDIA_HASH_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "media_hashes.json")
MAX_HISTORY_HOURS = 24

COMMON_STOP_WORDS = {
    "сьогодні", "зараз", "вчора", "цього", "після", "також", "було", "проти", "може",
    "через", "буде", "тільки", "цьому", "коли", "якщо", "були", "вони", "яких", "який",
    "яка", "яке", "повідомляє", "новини", "року", "потім", "перед", "під", "над", "щоб",
    "стало", "відомо", "заявив", "словами", "джерело", "офіційно", "увага", "терміново",
    "внаслідок", "відповідно", "разом", "перший", "другий", "можуть", "бути", "своїх"
}

def load_json_file(path: str) -> List[Dict]:
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []

def save_json_file(path: str, data: list):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Помилка запису в {path}: {e}")

# ==================== 1. ПЕРЕВІРКА ДУБЛІВ ЗА ФОТО/ВІДЕО ====================

def is_media_duplicate(media_content: bytes, media_url: str = "") -> bool:
    """
    Перевіряє, чи публікувалося вже це конкретне фото або відео сьогодні.
    Якщо два донори виклали однаковий пост про Боровську Андріївку з тією ж картою —
    новий пост блокується ще до виклику нейромережі!
    """
    if not media_content and not media_url:
        return False

    now = time.time()
    min_time = now - (MAX_HISTORY_HOURS * 3600)
    history = load_json_file(MEDIA_HASH_FILE)

    content_hash = hashlib.md5(media_content).hexdigest() if media_content else ""
    url_hash = hashlib.md5(media_url.encode("utf-8")).hexdigest() if media_url else ""

    for item in history:
        if item.get("timestamp", 0) < min_time:
            continue
        if content_hash and item.get("content_hash") == content_hash:
            print("[АНТИДУБЛЬ МЕДІА]: Знайдено 100% збіг фото/карти події з раніше опублікованою! Дубль відхилено.")
            return True
        if url_hash and item.get("url_hash") == url_hash:
            print("[АНТИДУБЛЬ МЕДІА]: Збіг посилання на медіафайл! Дубль відхилено.")
            return True

    return False

def register_media(media_content: bytes, media_url: str = ""):
    """Записує хеш фото або відео в історію."""
    history = load_json_file(MEDIA_HASH_FILE)
    content_hash = hashlib.md5(media_content).hexdigest() if media_content else ""
    url_hash = hashlib.md5(media_url.encode("utf-8")).hexdigest() if media_url else ""

    history.append({
        "content_hash": content_hash,
        "url_hash": url_hash,
        "timestamp": time.time()
    })
    if len(history) > 300:
        history = history[-300:]
    save_json_file(MEDIA_HASH_FILE, history)

# ==================== 2. ПЕРЕВІРКА ДУБЛІВ ЗА ГЕОГРАФІЄЮ ТА СЕНСОМ ====================

def extract_entities_and_stems(text: str) -> Tuple[Set[str], Set[str]]:
    """
    Витягує:
    1. Власні назви з великої літери (населені пункти, прізвища, об'єкти: Боровська, Андріївка, Зелений, Гай, Сімферополь).
    2. Корені ключових слів.
    """
    # 1. Слова з великої літери від 3 літер
    capitals = set(re.findall(r"\b[А-ЯІЇЄҐ][а-яіїєґ]{2,}", text))
    capitals = {c.lower()[:5] for c in capitals if c.lower() not in COMMON_STOP_WORDS}

    # 2. Усі значущі корені слів
    clean = re.sub(r"[^\w\s]", " ", text.lower(), flags=re.UNICODE)
    words = clean.split()
    stems = set()
    for w in words:
        if len(w) >= 4 and w not in COMMON_STOP_WORDS:
            stems.add(w[:5] if len(w) >= 5 else w)

    return capitals, stems

def is_topic_already_covered(text: str) -> bool:
    """
    Надійний антидублікат:
    - Блокує новину, якщо збігаються 2 або більше власних назв / локацій (наприклад: "боров" + "андрі" або "зелен" + "гай").
    - Блокує новину, якщо збігається 3 або більше ключових коренів слів.
    """
    now = time.time()
    min_time = now - (MAX_HISTORY_HOURS * 3600)
    topics = load_json_file(TOPICS_FILE)

    current_capitals, current_stems = extract_entities_and_stems(text)

    for t in topics:
        if t.get("timestamp", 0) < min_time:
            continue

        prev_capitals = set(t.get("capitals", []))
        prev_stems = set(t.get("stems", []))

        # Перевірка 1: Збіг географічних назв чи локацій
        common_places = current_capitals.intersection(prev_capitals)
        if len(common_places) >= 2:
            print(f"[АНТИДУБЛЬ ГЕО]: Збіг населених пунктів/об'єктів {common_places} з постом '{t.get('headline', '')[:35]}...'. ДУБЛЬ ВІДХИЛЕНО!")
            return True

        # Перевірка 2: Збіг за ключовими основами слів
        common_stems = current_stems.intersection(prev_stems)
        if len(common_stems) >= 3:
            print(f"[АНТИДУБЛЬ СЕНСУ]: Збіг слів {common_stems} з постом '{t.get('headline', '')[:35]}...'. ДУБЛЬ ВІДХИЛЕНО!")
            return True

    return False

def register_published_topic(headline: str, text: str):
    """Фіксує опубліковану тему та власні назви в базі."""
    topics = load_json_file(TOPICS_FILE)
    capitals, stems = extract_entities_and_stems(headline + " " + text)

    topics.append({
        "headline": headline,
        "capitals": list(capitals),
        "stems": list(stems),
        "timestamp": time.time()
    })
    if len(topics) > 300:
        topics = topics[-300:]
    save_json_file(TOPICS_FILE, topics)
