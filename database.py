import sqlite3
import hashlib
import os

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "news.db")

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS processed_news (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            news_hash TEXT UNIQUE,
            title TEXT,
            url TEXT,
            published_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_breaking INTEGER DEFAULT 0,
            video_generated INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()

def compute_hash(title: str, content: str) -> str:
    norm_text = (title.strip().lower() + "_" + content.strip()[:100].lower()).encode("utf-8")
    return hashlib.md5(norm_text).hexdigest()

def is_news_processed(news_hash: str) -> bool:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM processed_news WHERE news_hash = ?", (news_hash,))
    exists = cursor.fetchone() is not None
    conn.close()
    return exists

def mark_news_processed(news_hash: str, title: str, url: str, is_breaking: bool = False, video_generated: bool = False):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR IGNORE INTO processed_news (news_hash, title, url, is_breaking, video_generated)
        VALUES (?, ?, ?, ?, ?)
    """, (news_hash, title, url, 1 if is_breaking else 0, 1 if video_generated else 0))
    conn.commit()
    conn.close()
