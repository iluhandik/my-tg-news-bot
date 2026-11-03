import os
import subprocess
from PIL import Image, ImageDraw, ImageFont

def brand_image(input_path: str, output_path: str, watermark_text: str = "@kropivnit") -> str:
    """
    Накладає стильний, акуратний напівпрозорий бейдж каналу на фото.
    Виглядає професійно й ненав'язливо, брендуючи контент під ваш канал.
    """
    try:
        if not os.path.exists(input_path):
            return input_path

        img = Image.open(input_path).convert("RGBA")
        width, height = img.size

        # Створюємо прозорий шар для бейджа
        overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        # Розраховуємо розмір бейджа залежно від роздільної здатності фото
        badge_w = max(180, int(width * 0.22))
        badge_h = max(42, int(height * 0.06))
        margin = max(20, int(width * 0.03))

        x1 = width - badge_w - margin
        y1 = margin
        x2 = x1 + badge_w
        y2 = y1 + badge_h

        # Стильна заокруглена темна плашка з тонким синім контуром
        draw.rounded_rectangle(
            [x1, y1, x2, y2],
            radius=8,
            fill=(15, 23, 42, 200),
            outline=(56, 189, 248, 160),
            width=2
        )

        # Текст @kropivnit
        font_size = max(18, int(badge_h * 0.45))
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf", font_size)
        except Exception:
            font = ImageFont.load_default()

        # Центруємо напис всередині бейджа
        bbox = draw.textbbox((0, 0), watermark_text, font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        text_x = x1 + (badge_w - text_w) // 2
        text_y = y1 + (badge_h - text_h) // 2 - 2

        draw.text((text_x, text_y), watermark_text, fill=(255, 255, 255, 240), font=font)

        final_img = Image.alpha_composite(img, overlay).convert("RGB")
        final_img.save(output_path, "JPEG", quality=92)
        return output_path
    except Exception as e:
        print(f"[БРЕНДУВАННЯ ФОТО]: Не вдалося накласти плашку: {e}")
        return input_path

def brand_video(input_path: str, output_path: str, watermark_text: str = "@kropivnit") -> str:
    """
    Накладає акуратний брендовий вотермарк каналу у кутку відео за допомогою FFmpeg.
    """
    try:
        if not os.path.exists(input_path):
            return input_path

        # Фільтр drawtext для накладання акуратної плашки з назвою каналу
        filter_str = (
            f"drawbox=x=w-240-20:y=20:w=240:h=50:color=black@0.65:t=fill,"
            f"drawtext=text='{watermark_text}':fontcolor=white@0.9:fontsize=26:x=w-240-20+(240-text_w)/2:y=20+(50-text_h)/2"
        )

        cmd = [
            "ffmpeg", "-y",
            "-i", input_path,
            "-vf", filter_str,
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
            "-c:a", "copy",
            output_path
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90)
        if res.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 1000:
            return output_path
        else:
            return input_path
    except Exception as e:
        print(f"[БРЕНДУВАННЯ ВІДЕО]: Не вдалося накласти вотермарк: {e}")
        return input_path
