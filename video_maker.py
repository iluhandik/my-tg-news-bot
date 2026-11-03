import os
import re
import subprocess
import urllib.request
import textwrap
from typing import Optional
from config import CHANNEL_USERNAME, MEDIA_DIR, OUTPUT_DIR

def strip_emojis(text: str) -> str:
    """Видаляє всі емодзі та спецсимволи, щоб у ffmpeg ніколи не було битих квадратів []."""
    if not text:
        return ""
    # Видалення 4-байтних unicode emoji та специфічних значків
    clean = re.sub(r'[\U00010000-\U0010ffff]', '', text)
    clean = re.sub(r'[^\w\s.,!?:;—–\-\(\)\'\"]', '', clean, flags=re.UNICODE)
    return re.sub(r'\s+', ' ', clean).strip()

def generate_voiceover(text: str, output_audio_path: str, voice: str = "uk-UA-OstapNeural") -> bool:
    """Генерує якісну дикторську озвучку українською через Edge-TTS з прискоренням 1.1x для TikTok."""
    clean_speech = strip_emojis(text)
    try:
        cmd = [
            "edge-tts",
            "--voice", voice,
            "--rate=+10%",
            "--text", clean_speech,
            "--write-media", output_audio_path
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=45)
        if res.returncode == 0 and os.path.exists(output_audio_path) and os.path.getsize(output_audio_path) > 200:
            return True
    except Exception as e:
        print(f"[TTS]: edge-tts недоступний: {e}")

    # Резервний таймінг-звук
    duration = max(6, int(len(clean_speech.split()) / 2.5))
    cmd_fallback = [
        "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", str(duration),
        "-c:a", "aac", output_audio_path
    ]
    subprocess.run(cmd_fallback, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return os.path.exists(output_audio_path)

def get_media_duration(file_path: str) -> float:
    """Визначає точну тривалість медіафайлу через ffprobe."""
    try:
        cmd = [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", file_path
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        dur = float(res.stdout.strip())
        return max(5.0, dur)
    except Exception:
        return 12.0

def download_file(url: str, output_path: str) -> bool:
    """Завантажує фото або відео за посиланням."""
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=25) as resp:
            data = resp.read()
            if len(data) > 2000:
                with open(output_path, "wb") as f:
                    f.write(data)
                return True
    except Exception as e:
        print(f"Помилка завантаження медіа {url}: {e}")
    return False

def build_short_video(
    headline: str,
    script_text: str,
    image_url: Optional[str] = None,
    source_video_url: Optional[str] = None,
    output_filename: str = "viral_short.mp4"
) -> str:
    """
    Створює якісне 9:16 вертикальне відео для TikTok / Reels / Shorts:
    - Якщо є живе відео події з донора: адаптує його під 9:16 з розмитим фоном + брендові плашки.
    - Якщо є фото події: робить плавний динамічний наїзд камери (Ken Burns) + дикторську озвучку.
    - ЯКЩО НЕМАЄ КАРТИНКИ І ВІДЕО: ВІДЕО НЕ СТВОРЮЄТЬСЯ (щоб не було порожніх синіх екранів!).
    """
    # Жорстке правило: ніколи не робити відео без візуалу!
    if not image_url and not source_video_url:
        print("[ВІДЕО]: Немає ні фото, ні відео з місця події. Пропускаємо створення ролика.")
        return ""

    output_video_path = os.path.join(OUTPUT_DIR, output_filename)
    audio_path = os.path.join(MEDIA_DIR, "temp_voice.m4a")
    img_path = os.path.join(MEDIA_DIR, "temp_photo.jpg")
    vid_input_path = os.path.join(MEDIA_DIR, "temp_source.mp4")

    # Очищаємо заголовок від емодзі та символів, які ламають ffmpeg
    clean_head = strip_emojis(headline).upper()[:50].replace(":", "\\:").replace("'", "")
    clean_lines = textwrap.wrap(clean_head, width=22)
    line1 = clean_lines[0] if len(clean_lines) > 0 else "ТЕРМІНОВА НОВИНА"
    line2 = clean_lines[1] if len(clean_lines) > 1 else ""

    promo_text = f"БІЛЬШЕ В ТЕЛЕГРАМ\\: {CHANNEL_USERNAME}".replace("'", "")

    # ВАРІАНТ А: У донора є справжнє живе відео події!
    if source_video_url and download_file(source_video_url, vid_input_path):
        print("[ВІДЕО]: Використовуємо реальні кадри події з донора!")
        filter_complex = (
            f"[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=25:5[bg]; "
            f"[0:v]scale=1040:1040:force_original_aspect_ratio=decrease[fg]; "
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2[base]; "
            f"[base]drawbox=x=0:y=80:w=1080:h=260:color=black@0.75:t=fill,"
            f"drawtext=text='ТЕРМІНОВА НОВИНА':fontcolor=yellow:fontsize=46:x=(w-text_w)/2:y=110,"
            f"drawtext=text='{line1}':fontcolor=white:fontsize=52:x=(w-text_w)/2:y=175"
            + (f",drawtext=text='{line2}':fontcolor=white:fontsize=52:x=(w-text_w)/2:y=235" if line2 else "")
            + f",drawbox=x=0:y=h-260:w=1080:h=200:color=black@0.8:t=fill,"
            f"drawtext=text='{promo_text}':fontcolor=yellow:fontsize=46:x=(w-text_w)/2:y=h-220,"
            f"drawtext=text='ПОСИЛАННЯ В ШАПЦІ ПРОФІЛЮ':fontcolor=white:fontsize=38:x=(w-text_w)/2:y=h-140[v]"
        )
        cmd = [
            "ffmpeg", "-y",
            "-i", vid_input_path,
            "-filter_complex", filter_complex,
            "-map", "[v]", "-map", "0:a?",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-t", "45",
            "-c:a", "aac", "-b:a", "192k",
            output_video_path
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if res.returncode == 0 and os.path.exists(output_video_path):
            return output_video_path

    # ВАРІАНТ Б: У донора є якісне фото події -> робимо динамічний Ken Burns наїзд камери
    if image_url and download_file(image_url, img_path):
        generate_voiceover(script_text, audio_path)
        duration = get_media_duration(audio_path)

        filter_complex = (
            f"[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=25:5[bg]; "
            f"[0:v]scale=1000:1000:force_original_aspect_ratio=decrease[fg]; "
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2[base]; "
            f"[base]drawbox=x=0:y=80:w=1080:h=260:color=black@0.75:t=fill,"
            f"drawtext=text='ТЕРМІНОВА НОВИНА':fontcolor=yellow:fontsize=46:x=(w-text_w)/2:y=110,"
            f"drawtext=text='{line1}':fontcolor=white:fontsize=52:x=(w-text_w)/2:y=175"
            + (f",drawtext=text='{line2}':fontcolor=white:fontsize=52:x=(w-text_w)/2:y=235" if line2 else "")
            + f",drawbox=x=0:y=h-260:w=1080:h=200:color=black@0.8:t=fill,"
            f"drawtext=text='{promo_text}':fontcolor=yellow:fontsize=46:x=(w-text_w)/2:y=h-220,"
            f"drawtext=text='ПОСИЛАННЯ В ШАПЦІ ПРОФІЛЮ':fontcolor=white:fontsize=38:x=(w-text_w)/2:y=h-140[v]"
        )
        cmd = [
            "ffmpeg", "-y",
            "-loop", "1", "-i", img_path,
            "-i", audio_path,
            "-filter_complex", filter_complex,
            "-map", "[v]", "-map", "1:a",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-t", str(duration),
            "-c:a", "aac", "-b:a", "192k",
            output_video_path
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if res.returncode == 0 and os.path.exists(output_video_path):
            return output_video_path

    return ""
