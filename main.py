import time
import sys
import argparse
from datetime import datetime
from config import RSS_FEEDS, CHECK_INTERVAL_SECONDS, CHANNEL_NAME, CHANNEL_USERNAME
from database import init_db, compute_hash, is_news_processed, mark_news_processed
from parser import fetch_rss_feed
from ai_processor import process_news_with_ai
from telegram_poster import send_telegram_message, send_telegram_photo, send_telegram_video
from video_maker import build_short_video
from social_poster import post_to_all_socials_ayrshare, notify_admin_with_video

def run_news_cycle():
    """Один цикл перевірки новинних стрічок та публікації."""
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Початок перевірки джерел новин...")
    total_new = 0

    for feed_url in RSS_FEEDS:
        print(f"Сканування: {feed_url}")
        articles = fetch_rss_feed(feed_url)

        for article in articles:
            title = article["title"]
            content = article["content"]
            image_url = article.get("image_url")
            link = article.get("link", "")

            news_hash = compute_hash(title, content)

            if is_news_processed(news_hash):
                continue

            print(f"\n--> Знайдено нову новину: {title[:70]}...")
            total_new += 1

            # 1. AI-рерайт та очищення від згадок першоджерел
            ai_data = process_news_with_ai(title, content)
            telegram_post = ai_data.get("telegram_post", "")
            is_mega_news = ai_data.get("is_mega_news", False)
            news_score = ai_data.get("news_score", 5)

            # 2. Публікація в Telegram-канал
            posted = False
            if image_url:
                posted = send_telegram_photo(image_url, caption=telegram_post)
            else:
                posted = send_telegram_message(telegram_post)

            print(f"Опубліковано в Telegram (Оцінка важливості: {news_score}/10): {posted}")

            # 3. Якщо це мега-новина — створюємо коротке відео для Shorts/Reels/TikTok
            video_generated = False
            if is_mega_news:
                print(f"🔥 УВАГА: Мега-новина ({news_score}/10)! Запуск генерації вертикального відео...")
                headline = ai_data.get("video_headline", title[:40])
                script = ai_data.get("video_script", content[:300])

                timestamp = int(time.time())
                video_filename = f"short_{timestamp}.mp4"
                video_path = build_short_video(
                    headline=headline,
                    script_text=script,
                    image_url=image_url,
                    output_filename=video_filename
                )

                if video_path:
                    video_generated = True
                    print(f"Відео успішно створено: {video_path}")
                    
                    # Надсилаємо відео у свій Telegram-канал
                    video_caption = f"⚡️ **ВІДЕО: {headline}**\n\n{script[:300]}...\n\n👉 Підпишись: {CHANNEL_USERNAME}"
                    send_telegram_video(video_path, caption=video_caption)

                    # Сповіщаємо адміна
                    notify_admin_with_video(video_path, title=headline, script_text=script)

                    # Публікація в соцмережі (TikTok, YouTube Shorts, Reels, Threads)
                    # (за наявності публічного URL або через API)
                    # post_to_all_socials_ayrshare(...)

            # Позначаємо новину як оброблену в БД
            mark_news_processed(news_hash, title, link, is_breaking=is_mega_news, video_generated=video_generated)

            # Пауза між публікаціями щоб не спамити у канал
            time.sleep(5)

    print(f"Цикл завершено. Оброблено нових публікацій: {total_new}\n")

def main():
    parser = argparse.ArgumentParser(description="Автоматичний новинний бот та генератор відео для Telegram і соцмереж.")
    parser.add_argument("--once", action="store_true", help="Виконати перевірку один раз і завершити роботу.")
    args = parser.parse_args()

    init_db()
    print("=" * 60)
    print(f"🚀 СИСТЕМА АВТОМАТИЗАЦІЇ НОВИН ТА ВІДЕО ЗАПУЩЕНА")
    print(f"Канал: {CHANNEL_NAME} ({CHANNEL_USERNAME})")
    print(f"Інтервал перевірки: {CHECK_INTERVAL_SECONDS} секунд")
    print("=" * 60)

    if args.once:
        run_news_cycle()
        return

    while True:
        try:
            run_news_cycle()
        except Exception as e:
            print(f"Критична помилка в циклі: {e}")
        
        time.sleep(CHECK_INTERVAL_SECONDS)

if __name__ == "__main__":
    main()
