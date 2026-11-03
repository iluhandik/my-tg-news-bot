import time
import argparse
from datetime import datetime
from config import DONOR_CHANNELS, CHECK_INTERVAL_SECONDS, CHANNEL_USERNAME
from database import init_db, compute_hash, is_news_processed, mark_news_processed
from tg_scraper import fetch_channel_posts
from ad_detector import is_advertisement
from consensus import check_cross_donor_consensus
from ai_processor import process_news_with_ai
from telegram_poster import send_telegram_message, send_telegram_photo, send_telegram_video_media
from video_maker import build_short_video
from social_poster import post_video_to_social_networks

MAX_POSTS_PER_RUN = 1

def run_news_cycle():
    """Безперервний аналіз новин з жорстким контролем якості."""
    posts_published = 0

    print(f"\n{'='*55}")
    print(f"[{datetime.now().strftime('%H:%M:%S')}] СКАНИНГ КАНАЛІВ. Ліміт: {MAX_POSTS_PER_RUN} пост")
    print(f"{'='*55}")

    for channel in DONOR_CHANNELS:
        if posts_published >= MAX_POSTS_PER_RUN:
            break

        posts = fetch_channel_posts(channel, limit=2)

        for p in posts:
            if posts_published >= MAX_POSTS_PER_RUN:
                break

            raw_text = p["text"]
            photo_url = p.get("photo_url")
            video_url = p.get("video_url")
            post_id = p.get("post_id", "")

            # 1. АНТИРЕКЛАМА
            if is_advertisement(raw_text):
                continue

            news_hash = compute_hash(channel, raw_text[:120])
            if is_news_processed(news_hash):
                continue

            # 2. ПЕРЕВІРКА РЕЗОНАНСУ (правило 3 донорів)
            consensus_count, matched_donors = check_cross_donor_consensus(channel, raw_text)
            has_multi_donor_consensus = (consensus_count >= 3)

            # 3. AI ОБРОБКА
            ai_data = process_news_with_ai(raw_text)
            if not ai_data:
                mark_news_processed(news_hash, channel, post_id, is_breaking=False, video_generated=False)
                continue

            telegram_post = ai_data.get("telegram_post", "")
            if not telegram_post or len(telegram_post) < 30:
                continue

            is_mega_news = ai_data.get("is_mega_news", False) or has_multi_donor_consensus
            news_score = 10 if has_multi_donor_consensus else ai_data.get("news_score", 5)

            # 4. ПУБЛІКАЦІЯ В КАНАЛ @kropivnit (1 пост)
            posted = False
            if video_url:
                posted = send_telegram_video_media(video_url, caption=telegram_post)
            elif photo_url:
                posted = send_telegram_photo(photo_url, caption=telegram_post)
            else:
                posted = send_telegram_message(telegram_post)

            if posted:
                posts_published += 1
                print(f"✅ УСПІШНО ОПУБЛІКОВАНО В {CHANNEL_USERNAME}: {telegram_post[:60]}...")
            else:
                print(f"❌ Помилка надсилання в Telegram: {channel}/{post_id}")

            # 5. ГЕНЕРАЦІЯ ВІДЕО ДЛЯ СОЦМЕРЕЖ (лише якщо є реальне фото або відео події!)
            video_done = False
            if is_mega_news and posted and (photo_url or video_url):
                headline = ai_data.get("video_headline", "ГОЛОВНІ НОВИНИ")
                script = ai_data.get("video_script", "")
                social_caption = ai_data.get("social_caption", "")

                timestamp = int(time.time())
                video_filename = f"social_viral_{timestamp}.mp4"

                # Передаємо реальне відео або фото
                video_path = build_short_video(
                    headline=headline,
                    script_text=script,
                    image_url=photo_url,
                    source_video_url=video_url,
                    output_filename=video_filename
                )

                if video_path:
                    video_done = True
                    post_video_to_social_networks(
                        video_path=video_path,
                        headline=headline,
                        script_text=script,
                        social_caption=social_caption
                    )

            # Записуємо в базу
            mark_news_processed(news_hash, channel, post_id, is_breaking=is_mega_news, video_generated=video_done)

    print(f"Цикл завершено. Опубліковано: {posts_published}\n")

def main():
    parser = argparse.ArgumentParser(description="Новинний бот для @kropivnit")
    parser.add_argument("--once", action="store_true", help="Один запуск")
    args = parser.parse_args()

    init_db()
    if args.once:
        run_news_cycle()
        return

    while True:
        try:
            run_news_cycle()
        except Exception as e:
            print(f"Помилка в циклі: {e}")
        time.sleep(CHECK_INTERVAL_SECONDS)

if __name__ == "__main__":
    main()
