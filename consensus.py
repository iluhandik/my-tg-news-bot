import time
import re
from typing import List, Dict, Tuple

# Буфер останніх новин за останні 6 годин: list of {channel, text, keywords, timestamp}
RECENT_TOPICS_BUFFER: List[Dict] = []
BUFFER_MAX_AGE_SECONDS = 6 * 3600  # 6 годин

STOP_WORDS = {
    "цього", "після", "також", "було", "проти", "може", "через", "буде", "тільки",
    "цьому", "коли", "якщо", "були", "вони", "яких", "який", "яка", "яке", "повідомляє",
    "новини", "сьогодні", "зараз", "року", "потім", "перед", "під", "над", "щоб"
}

def extract_keywords(text: str) -> set:
    """Витягує ключові сутнісні слова довжиною від 4 літер для порівняння теми."""
    words = re.findall(r"[а-яіїєґa-z]{4,}", text.lower())
    return {w for w in words if w not in STOP_WORDS}

def check_cross_donor_consensus(channel: str, text: str) -> Tuple[int, List[str]]:
    """
    Перевіряє, скільки інших каналів-донорів вже опублікували новину на цю саму тему.
    Повертає: (кількість каналів, список каналів)
    """
    global RECENT_TOPICS_BUFFER
    now = time.time()
    # Очищаємо застарілі новини з буфера
    RECENT_TOPICS_BUFFER = [item for item in RECENT_TOPICS_BUFFER if now - item["timestamp"] < BUFFER_MAX_AGE_SECONDS]

    current_keywords = extract_keywords(text)
    if len(current_keywords) < 3:
        return (1, [channel])

    matched_channels = {channel}

    for item in RECENT_TOPICS_BUFFER:
        if item["channel"] == channel:
            continue
        common_words = current_keywords.intersection(item["keywords"])
        # Якщо збігається від 3-4 ключових слів — це одна й та сама новина!
        if len(common_words) >= 3:
            matched_channels.add(item["channel"])

    # Додаємо поточну новину в буфер
    RECENT_TOPICS_BUFFER.append({
        "channel": channel,
        "text": text,
        "keywords": current_keywords,
        "timestamp": now
    })

    return (len(matched_channels), list(matched_channels))
