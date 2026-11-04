import os
import subprocess
from PIL import Image, ImageFilter

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WATERMARK_FILE = os.path.join(BASE_DIR, 'watermark.png')

def brand_image(input_path: str, output_path: str, watermark_text: str = '') -> str:
    """
    Стирає чужий напис Supernova розмиттям зони по центру та накладає наш фірмовий медіа-бейдж watermark.png.
    """
    try:
        if not os.path.exists(WATERMARK_FILE) or not os.path.exists(input_path):
            return input_path

        base_img = Image.open(input_path).convert('RGB')
        width, height = base_img.size
        wm = Image.open(WATERMARK_FILE).convert('RGBA')

        # 35% ширини кадру
        wm_w = max(200, int(width * 0.35))
        wm_h = int(wm.height * (wm_w / wm.width))
        wm_resized = wm.resize((wm_w, wm_h), Image.Resampling.LANCZOS)

        # 1. СТИРАЄМО ЧУЖУ ВОТЕРМАРКУ (зона в центрі кадру де сидить Supernova)
        erase_w = int(width * 0.55)
        erase_h = int(height * 0.16)
        erase_x = (width - erase_w) // 2
        erase_y = (height - erase_h) // 2

        blur_box = (erase_x, erase_y, erase_x + erase_w, erase_y + erase_h)
        erased_area = base_img.crop(blur_box).filter(ImageFilter.GaussianBlur(18))
        base_img.paste(erased_area, blur_box)

        # 2. СТАВИМО НАШ БЕЙДЖ ТОЧНО НА ЦЕ МІСЦЕ
        pos_x = (width - wm_w) // 2
        pos_y = (height - wm_h) // 2
        base_img.paste(wm_resized, (pos_x, pos_y), mask=wm_resized)

        base_img.save(output_path, 'JPEG', quality=92)
        return output_path
    except Exception as e:
        print(f'[БРЕНДУВАННЯ ФОТО]: {e}')
        return input_path

def brand_video(input_path: str, output_path: str, watermark_text: str = '') -> str:
    """
    Стирає чужий напис Supernova розмиттям по центру відео та накладає наш фірмовий медіа-бейдж watermark.png.
    """
    try:
        if not os.path.exists(WATERMARK_FILE) or not os.path.exists(input_path):
            return input_path

        filter_complex = (
            '[0:v]split[main][crop_in];'
            '[crop_in]crop=w=in_w*0.55:h=in_h*0.16:x=(in_w-out_w)/2:y=(in_h-out_h)/2,boxblur=10:2[blurred];'
            '[main][blurred]overlay=(W-w)/2:(H-h)/2[base];'
            '[1:v][base]scale2ref=w=main_w*0.35:h=-1[wm][base_ref];'
            '[base_ref][wm]overlay=(W-w)/2:(H-h)/2[v]'
        )

        cmd = [
            'ffmpeg', '-y',
            '-i', input_path,
            '-i', WATERMARK_FILE,
            '-filter_complex', filter_complex,
            '-map', '[v]', '-map', '0:a?',
            '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '23',
            '-c:a', 'copy',
            output_path
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90)
        if res.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 1000:
            return output_path
        return input_path
    except Exception as e:
        print(f'[БРЕНДУВАННЯ ВІДЕО]: {e}')
        return input_path
