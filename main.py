import time
import argparse
from datetime import datetime
from config import DONOR_CHANNELS, CHECK_INTERVAL_SECONDS, CHANNEL_USERNAME
from database import init_db, compute_hash, is_news_processed, mark_news_processed
from tg_scraper import fetch_channel_posts
from channel_sync import sync_user_channel_topics
from ad_detector import is_advertisement
from consensus import check_cross_donor_consensus
from topic_filter import is_topic_already_covered, register_published_topic
from ai_processor import process_news_with_ai
from telegram_poster import send_telegram_message, send_telegram_photo, send_telegram_video_media
from video_maker import build_short_video
from social_poster import post_video_to_social_networks

# Пауза між публікаціями новин (1 хвилина як просив користувач)
DELAY_BETWEEN_POSTS_SECONDS = 60

def run_news_cycle():
    """Аналіз каналів-донорів із синхронізацією з власним каналом та паузою в 1 хвилину."""
    posts_published = 0

    print(f"\n{'='*55}")
    print(f"[{datetime.now().strftime('%H:%M:%S')}] СКАНИНГ КАНАЛІВ. Пауза між постами: {DELAY_BETWEEN_POSTS_SECONDS} сек")
    print(f"{'='*55}")

    # 1. СИНХРОНІЗАЦІЯ З ВАШИМ КАНАЛОМ @kropivnit:
    # Зчитуємо останні пости з вашого каналу, щоб бот знав, що ви вже виставили вручну,
    # і НІКОЛИ не запостив це саме з інших донорів!
    sync_user_channel_topics(CHANNEL_USERNAME, limit=25)

    for channel in DONOR_CHANNELS:
        posts = fetch_channel_posts(channel, limit=3)

        for p in posts:
            raw_text = p["text"]
            photo_url = p.get("photo_url")
            video_url = p.get("video_url")
            post_id = p.get("post_id", "")

            # 2. АНТИРЕКЛАМА ТА ФІЛЬТР РУТИННИХ ТРИВОГ БЕЗ ПОДІЙ:
            if is_advertisement(raw_text):
                continue

            # 3. ТЕХНІЧНИЙ ДУБЛЬ
            news_hash = compute_hash(channel, raw_text[:120])
            if is_news_processed(news_hash):
                continue

            # 4. СМИСЛОВИЙ АНТИДУБЛЬ (перевіряє і базу, і синхронізовані пости вашого каналу!)
            if is_topic_already_covered(raw_text):
                mark_news_processed(news_hash, channel, post_id, is_breaking=False, video_generated=False)
                continue

            # 5. ПЕРЕВІРКА РЕЗОНАНСУ
            consensus_count, matched_donors = check_cross_donor_consensus(channel, raw_text)
            has_multi_donor_consensus = (consensus_count >= 3)

            # 6. AI ОБРОБКА ТА ФІЛЬТРАЦІЯ
            ai_data = process_news_with_ai(raw_text)
            if not ai_data:
                mark_news_processed(news_hash, channel, post_id, is_breaking=False, video_generated=False)
                continue

            telegram_post = ai_data.get("telegram_post", "")
            if not telegram_post or len(telegram_post) < 25:
                continue

            is_mega_news = ai_data.get("is_mega_news", False) or has_multi_donor_consensus
            news_score = 10 if has_multi_donor_consensus else ai_data.get("news_score", 5)

            # Якщо це вже не перший пост за цикл — робимо паузу рівно 1 хвилину!
            if posts_published > 0:
                print(f"[ПАУЗА]: Очікуємо {DELAY_BETWEEN_POSTS_SECONDS} секунд перед наступним постом...")
                time.sleep(DELAY_BETWEEN_POSTS_SECONDS)

            # 7. ПУБЛІКАЦІЯ В КАНАЛ @kropivnit
            posted = False
            if video_url:
                posted = send_telegram_video_media(video_url, caption=telegram_post)
            elif photo_url:
                posted = send_telegram_photo(photo_url, caption=telegram_post)
            else:
                posted = send_telegram_message(telegram_post)

            if posted:
                posts_published += 1
                headline_first_line = telegram_post.split("\n")[0]
                register_published_topic(headline_first_line, raw_text)
                print(f"✅ ОПУБЛІКОВАНО В {CHANNEL_USERNAME} (Оцінка: {news_score}/10): {headline_first_line[:50]}...")
            else:
                print(f"❌ Помилка надсилання в Telegram: {channel}/{post_id}")

            # 8. ВІДЕО ДЛЯ СОЦМЕРЕЖ (якщо мега-новина або 3 донори)
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
