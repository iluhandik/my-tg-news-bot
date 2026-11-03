import os
import subprocess
import urllib.request
import textwrap
from typing import Optional
from config import CHANNEL_NAME, CHANNEL_USERNAME, MEDIA_DIR, OUTPUT_DIR

def generate_voiceover(text: str, output_audio_path: str, voice: str = "uk-UA-OstapNeural") -> bool:
    """
    Генерує аудіодоріжку озвучки через edge-tts (нейромережеві голоси Microsoft).
    Якщо edge-tts не встановлено або немає мережі, генерує таймінг-аудіо через ffmpeg.
    """
    try:
        cmd = ["edge-tts", "--voice", voice, "--text", text, "--write-media", output_audio_path]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=40)
        if res.returncode == 0 and os.path.exists(output_audio_path) and os.path.getsize(output_audio_path) > 100:
            return True
    except Exception:
        pass

    # Резервний таймінг-звук відповідної тривалості (наприклад, 2.5 слова на секунду)
    words_count = len(text.split())
    duration = max(6, int(words_count / 2.3))
    cmd_fallback = [
        "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", str(duration),
        "-c:a", "aac", output_audio_path
    ]
    subprocess.run(cmd_fallback, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return os.path.exists(output_audio_path)

def get_audio_duration(audio_path: str) -> float:
    """Визначає точну тривалість аудіодоріжки."""
    try:
        cmd = [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", audio_path
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        dur = float(res.stdout.strip())
        return max(5.0, dur)
    except Exception:
        return 12.0

def download_image(url: str, output_path: str) -> bool:
    """Завантажує зображення новини за посиланням."""
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = resp.read()
            if len(data) > 1000:
                with open(output_path, "wb") as f:
                    f.write(data)
                return True
    except Exception as e:
        print(f"Не вдалося завантажити зображення {url}: {e}")
    return False

def build_short_video(
    headline: str,
    script_text: str,
    image_url: Optional[str] = None,
    output_filename: str = "breaking_news_short.mp4"
) -> str:
    """
    Створює повноцінне вертикальне відео 9:16 (1080x1920) для TikTok / Shorts / Reels:
    1. Озвучка новини.
    2. Розмитий фон або графічний стиль.
    3. Верхній банер '⚡️ ТЕРМІНОВА НОВИНА' + заголовок події.
    4. Нижній банер 'БІЛЬШЕ В TELEGRAM: @канал' + заклик підписатися.
    """
    output_video_path = os.path.join(OUTPUT_DIR, output_filename)
    audio_path = os.path.join(MEDIA_DIR, "temp_voice.m4a")
    image_path = os.path.join(MEDIA_DIR, "temp_news.jpg")

    # 1. Генерація озвучки
    generate_voiceover(script_text, audio_path)
    duration = get_audio_duration(audio_path)

    # 2. Перевірка наявності зображення
    has_image = False
    if image_url:
        has_image = download_image(image_url, image_path)

    # Очищення заголовків для фільтра ffmpeg
    clean_headline = headline.replace(":", "\\:").replace("'", "").replace('"', '').strip()
    headline_lines = textwrap.wrap(clean_headline.upper(), width=22)
    line1 = headline_lines[0] if len(headline_lines) > 0 else "ВАЖЛИВА НОВИНА"
    line2 = headline_lines[1] if len(headline_lines) > 1 else ""

    promo_channel = f"БІЛЬШЕ В TELEGRAM\\: {CHANNEL_USERNAME}".replace("'", "")

    # Опції шрифтів
    font_path = "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf"
    font_arg = f":fontfile={font_path}" if os.path.exists(font_path) else ""

    headline_filter = (
        f"drawtext=text='{line1}'{font_arg}:fontcolor=white:fontsize=56:x=(w-text_w)/2:y=250"
    )
    if line2:
        headline_filter += f",drawtext=text='{line2}'{font_arg}:fontcolor=white:fontsize=56:x=(w-text_w)/2:y=320"

    if has_image:
        filter_complex = (
            f"[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=20:5[bg]; "
            f"[0:v]scale=980:980:force_original_aspect_ratio=decrease[fg]; "
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2[base]; "
            f"[base]drawbox=x=0:y=130:w=1080:h=320:color=red@0.9:t=fill,"
            f"drawtext=text='⚡️ ТЕРМІНОВА НОВИНА'{font_arg}:fontcolor=yellow:fontsize=50:x=(w-text_w)/2:y=160,"
            f"{headline_filter},"
            f"drawbox=x=0:y=h-380:w=1080:h=240:color=black@0.85:t=fill,"
            f"drawtext=text='{promo_channel}'{font_arg}:fontcolor=yellow:fontsize=52:x=(w-text_w)/2:y=h-340,"
            f"drawtext=text='ПОСИЛАННЯ В ШАПЦІ ПРОФІЛЮ 👆'{font_arg}:fontcolor=white:fontsize=40:x=(w-text_w)/2:y=h-240[v]"
        )
        cmd = [
            "ffmpeg", "-y",
            "-loop", "1", "-i", image_path,
            "-i", audio_path,
            "-filter_complex", filter_complex,
            "-map", "[v]", "-map", "1:a",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-t", str(duration),
            "-c:a", "aac", "-b:a", "192k",
            output_video_path
        ]
    else:
        filter_complex = (
            f"drawbox=x=0:y=150:w=1080:h=340:color=red@0.9:t=fill,"
            f"drawtext=text='⚡️ ТЕРМІНОВА НОВИНА'{font_arg}:fontcolor=yellow:fontsize=52:x=(w-text_w)/2:y=180,"
            f"{headline_filter},"
            f"drawbox=x=0:y=h-380:w=1080:h=240:color=black@0.85:t=fill,"
            f"drawtext=text='{promo_channel}'{font_arg}:fontcolor=yellow:fontsize=52:x=(w-text_w)/2:y=h-340,"
            f"drawtext=text='ПОСИЛАННЯ В ШАПЦІ ПРОФІЛЮ 👆'{font_arg}:fontcolor=white:fontsize=40:x=(w-text_w)/2:y=h-240"
        )
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"color=c=0x0f172a:s=1080x1920:d={duration}",
            "-i", audio_path,
            "-vf", filter_complex,
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-t", str(duration),
            "-c:a", "aac", "-b:a", "192k",
            output_video_path
        ]

    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if res.returncode == 0 and os.path.exists(output_video_path):
        return output_video_path
    else:
        print(f"Помилка створення відео: {res.stderr.decode('utf-8', errors='ignore')}")
        return ""
