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

def run_news_cycle():
    """Безперервний аналіз каналів-донорів у реальному часі."""
    total_processed = 0

    for channel in DONOR_CHANNELS:
        posts = fetch_channel_posts(channel, limit=3)

        for p in posts:
            raw_text = p["text"]
            photo_url = p.get("photo_url")
            video_url = p.get("video_url")
            post_id = p.get("post_id", "")

            # 1. СУВОРИЙ АНТИРЕКЛАМНИЙ ФАЙРВОЛ: миттєво відсікаємо будь-яку рекламу, казино, крипту чи взаємопіар
            if is_advertisement(raw_text):
                continue

            news_hash = compute_hash(channel, raw_text[:120])
            if is_news_processed(news_hash):
                continue

            # 2. ПЕРЕВІРКА РЕЗОНАНСУ: якщо новина вийшла у 2-3+ донорах одночасно — це 99% ТОП-новина!
            consensus_count, matched_donors = check_cross_donor_consensus(channel, raw_text)
            has_multi_donor_consensus = (consensus_count >= 3)

            if has_multi_donor_consensus:
                print(f"🔥 ПІДТВЕРДЖЕНО 3+ ДОНОРАМИ {matched_donors}! ТОП-НОВИНА: {raw_text[:60]}...")
            else:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Нова подія з @{channel}: {raw_text[:60]}...")

            total_processed += 1

            # 3. AI-редагування: переклад на солов'їну, видалення лінків/вотермарок, без підписів
            ai_data = process_news_with_ai(raw_text)
            telegram_post = ai_data.get("telegram_post", "")
            is_mega_news = ai_data.get("is_mega_news", False) or has_multi_donor_consensus
            news_score = 10 if has_multi_donor_consensus else ai_data.get("news_score", 5)

            # 4. Негайний пост у Telegram-канал @kropivnit
            posted = False
            if video_url:
                posted = send_telegram_video_media(video_url, caption=telegram_post)
            elif photo_url:
                posted = send_telegram_photo(photo_url, caption=telegram_post)
            else:
                posted = send_telegram_message(telegram_post)

            print(f"-> Опубліковано в @kropivnit (Резонанс: {consensus_count} донорів, Оцінка: {news_score}/10): {posted}")

            # 5. Якщо новина підтверджена 3 донорами або мега-важлива — монтуємо ролик для соцмереж
            video_done = False
            if is_mega_news:
                headline = ai_data.get("video_headline", "ГОЛОВНІ НОВИНИ УКРАЇНИ")
                script = ai_data.get("video_script", "")
                social_caption = ai_data.get("social_caption", "")

                timestamp = int(time.time())
                video_filename = f"social_viral_{timestamp}.mp4"

                video_path = build_short_video(
                    headline=headline,
                    script_text=script,
                    image_url=photo_url,
                    output_filename=video_filename
                )

                if video_path:
                    video_done = True
                    # Відправляємо ВИКЛЮЧНО в TikTok/Reels/Shorts/Threads/FB (у ТГ-канал не публікується!)
                    post_video_to_social_networks(
                        video_path=video_path,
                        headline=headline,
                        script_text=script,
                        social_caption=social_caption
                    )

            # Фіксуємо пост у базі
            mark_news_processed(news_hash, channel, post_id, is_breaking=is_mega_news, video_generated=video_done)
            time.sleep(3)

def main():
    parser = argparse.ArgumentParser(description="Автономний новинний бот 24/7 для @kropivnit")
    parser.add_argument("--once", action="store_true", help="Один прогін")
    args = parser.parse_args()

    init_db()
    print("=" * 60)
    print(f"🚀 ЦІЛОДОБОВИЙ МОНІТОРИНГ НОВИН 24/7 АКТИВОВАНО")
    print(f"Канал: {CHANNEL_USERNAME} | Інтервал сканування: {CHECK_INTERVAL_SECONDS} секунд")
    print("=" * 60)

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
