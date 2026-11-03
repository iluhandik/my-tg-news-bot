import time
import argparse
from datetime import datetime
from config import DONOR_CHANNELS, CHECK_INTERVAL_SECONDS, CHANNEL_USERNAME
from database import init_db, compute_hash, is_news_processed, mark_news_processed
from tg_scraper import fetch_channel_posts
from channel_sync import get_channel_recent_posts
from ad_detector import is_advertisement
from consensus import check_cross_donor_consensus
from topic_filter import is_topic_already_covered, register_published_topic
from ai_processor import process_news_with_ai
from telegram_poster import send_telegram_message, send_telegram_photo, send_telegram_video_media
from video_maker import build_short_video
from social_poster import post_video_to_social_networks

# Рівномірний темп: макс 2 пости за запуск, пауза 90 секунд
MAX_POSTS_PER_RUN = 2
DELAY_BETWEEN_POSTS_SECONDS = 90

def run_news_cycle():
    """Аналіз донорів з вибірковим брендуванням та захистом від дублів."""
    posts_published = 0

    print(f"\n{'='*55}")
    print(f"[{datetime.now().strftime('%H:%M:%S')}] СКАНИНГ КАНАЛІВ. Ліміт: {MAX_POSTS_PER_RUN} пости за запуск")
    print(f"{'='*55}")

    # 1. Читаємо актуальний вміст вашого каналу
    recent_channel_posts = get_channel_recent_posts(CHANNEL_USERNAME, limit=15)
    print(f"[СИНХРОНІЗАЦІЯ]: Зчитано {len(recent_channel_posts)} останніх постів із @{CHANNEL_USERNAME}.")

    for channel in DONOR_CHANNELS:
        if posts_published >= MAX_POSTS_PER_RUN:
            print(f"[ТЕМП]: Досягнуто ліміту ({posts_published} пости). Пауза до наступного запуску.")
            break

        posts = fetch_channel_posts(channel, limit=3)

        for p in posts:
            if posts_published >= MAX_POSTS_PER_RUN:
                break

            raw_text = p["text"]
            photo_url = p.get("photo_url")
            video_url = p.get("video_url")
            post_id = p.get("post_id", "")

            # 2. АНТИРЕКЛАМА
            if is_advertisement(raw_text):
                continue

            # 3. ТЕХНІЧНИЙ ДУБЛЬ
            news_hash = compute_hash(channel, raw_text[:120])
            if is_news_processed(news_hash):
                continue

            # 4. СМИСЛОВИЙ АНТИДУБЛЬ (за основами слів)
            if is_topic_already_covered(raw_text):
                mark_news_processed(news_hash, channel, post_id, is_breaking=False, video_generated=False)
                continue

            # 5. РЕЗОНАНС (правило 3 донорів)
            consensus_count, matched_donors = check_cross_donor_consensus(channel, raw_text)
            has_multi_donor_consensus = (consensus_count >= 3)

            # 6. ШІ-ОБРОБКА (перевірка проти вашого каналу @kropivnit)
            ai_data = process_news_with_ai(raw_text, recent_channel_posts=recent_channel_posts)
            if not ai_data:
                mark_news_processed(news_hash, channel, post_id, is_breaking=False, video_generated=False)
                continue

            telegram_post = ai_data.get("telegram_post", "")
            if not telegram_post or len(telegram_post) < 25:
                continue

            is_mega_news = ai_data.get("is_mega_news", False) or has_multi_donor_consensus
            news_score = 10 if has_multi_donor_consensus else ai_data.get("news_score", 5)

            # Пауза між публікаціями
            if posts_published > 0:
                print(f"[ПАУЗА]: Очікуємо {DELAY_BETWEEN_POSTS_SECONDS} сек перед наступним постом...")
                time.sleep(DELAY_BETWEEN_POSTS_SECONDS)

            # 7. ПУБЛІКАЦІЯ В КАНАЛ @kropivnit (з вибірковим брендуванням медіа!)
            posted = False
            if video_url:
                posted = send_telegram_video_media(video_url, caption=telegram_post, donor_channel=channel)
            elif photo_url:
                posted = send_telegram_photo(photo_url, caption=telegram_post, donor_channel=channel)
            else:
                posted = send_telegram_message(telegram_post)

            if posted:
                posts_published += 1
                headline_first_line = telegram_post.split("\n")[0]
                register_published_topic(headline_first_line, raw_text)
                recent_channel_posts.insert(0, headline_first_line)
                print(f"✅ ОПУБЛІКОВАНО В {CHANNEL_USERNAME} (Оцінка: {news_score}/10): {headline_first_line[:50]}...")
            else:
                print(f"❌ Помилка надсилання в Telegram: {channel}/{post_id}")

            # 8. ВІДЕО ДЛЯ СОЦМЕРЕЖ (якщо мега-подія з медіа)
            video_done = False
            if is_mega_news and posted and (photo_url or video_url):
                headline = ai_data.get("video_headline", "ГОЛОВНІ НОВИНИ")
                script = ai_data.get("video_script", "")
                social_caption = ai_data.get("social_caption", "")

                timestamp = int(time.time())
                video_filename = f"social_viral_{timestamp}.mp4"

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

            # Фіксуємо пост у базі
            mark_news_processed(news_hash, channel, post_id, is_breaking=is_mega_news, video_generated=video_done)

    print(f"\nЦикл завершено. Опубліковано свіжих новин: {posts_published}\n")

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
