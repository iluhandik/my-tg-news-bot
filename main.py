import os
import re
import sys
import time
import html
import json
import hashlib
import subprocess
import textwrap
import urllib
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from typing import List, Dict, Optional, Tuple, Set
import requests
from PIL import Image, ImageFilter
from dotenv import load_dotenv

load_dotenv()

# ==================== НАЛАШТУВАННЯ ====================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID", "@kropivnit").strip()
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID", "").strip()

CHANNEL_NAME = "NEWS УКРАЇНА 365"
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "").strip() or (TELEGRAM_CHANNEL_ID if TELEGRAM_CHANNEL_ID.startswith("@") else "@kropivnit")
CHANNEL_INVITE_LINK = os.getenv("CHANNEL_INVITE_LINK", f"https://t.me/{CHANNEL_USERNAME.lstrip('@')}").strip()

AI_PROVIDER = os.getenv("AI_PROVIDER", "groq").strip().lower()
AI_API_KEY = os.getenv("AI_API_KEY", "").strip()
AI_MODEL = os.getenv("AI_MODEL", "llama-3.3-70b-versatile").strip()
BREAKING_NEWS_THRESHOLD = 8

# Розширений список надійних оперативних джерел без сміття:
DONOR_CHANNELS = [
    "u_now",              # Україна Сейчас (швидка всеукраїнська стрічка)
    "uniannet",           # УНІАН (офіційне інформагентство)
    "insiderUKR",         # Інсайдер UA (оперативні факти)
    "UaOnlii",            # Україна Online (головні події)
    "operativnoZSU",      # Оперативний ЗСУ (офіційні зведення, фронт)
    "suspilnenews",       # Суспільне Новини (найвищий стандарт верифікації фактів)
    "rbc_ukraine",        # РБК-Україна (економіка, держава, політика)
    "ssternenko",         # Стерненко (FPV, збори, зведення по ворогу)
    "DeepStateUA",        # DeepState (підтверджені мапи та зміни на лінії фронту)
    "nefedoov365",        # Нефедов (залишено за вашим бажанням)
    "inslandd",           # Лачен пише
    "zsuwar",             # Військові новини
    "supernova_plus",     # Supernova+ (суто бойові кадри)
    "chp_zahid"           # ЧП Захід (тільки загальнонаціональні події)
]

CHANNELS_WITH_HEAVY_WATERMARKS = {
    "supernova_plus",
    "exilenova_plus"
}

# Збільшено пропускну здатність для активного наповнення каналу:
MAX_POSTS_PER_RUN = 8
DELAY_BETWEEN_POSTS = 15
MAX_POST_AGE_HOURS = 4.0

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MEDIA_DIR = os.path.join(BASE_DIR, "media")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
TOPICS_FILE = os.path.join(BASE_DIR, "published_topics.json")
MEDIA_HASH_FILE = os.path.join(BASE_DIR, "media_hashes.json")
PROCESSED_IDS_FILE = os.path.join(BASE_DIR, "processed_ids.json")
WATERMARK_FILE = os.path.join(BASE_DIR, "watermark.png")

os.makedirs(MEDIA_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ==================== АВТОВИПРАВЛЕННЯ СУРЖИКУ ТА ПОМИЛОК ====================
CALQUE_REPLACEMENTS = [
    (r"(?i)\bзапорі[зс]хсталь\b", "Запоріжсталь"),
    (r"(?i)\bзапорожсталь\b", "Запоріжсталь"),
    (r"(?i)\bкриворі[зс]хсталь\b", "Криворіжсталь"),
    (r"(?i)\bбалл[іи]стич([а-яіїєґ]*)\b", r"балістич\1"),
    (r"(?i)\bбаллістик([а-яіїєґ]*)\b", r"балістик\1"),
    (r"(?i)\bминулого року фірма\b", "минулого року підприємство"),
    (r"(?i)\bслежк([а-яіїєґ]*)\b", "стеження"),
    (r"(?i)\bтенево([а-яіїєґ]*)\b", r"тіньово\1"),
    (r"(?i)\bтеневи([а-яіїєґ]*)\b", r"тіньови\1"),
    (r"(?i)\bвведення в обладнання\b", "введення в експлуатацію"),
    (r"(?i)\bпожежний вибух\b", "пожежа та вибухи"),
    (r"(?i)\bсем\'я\b", "сім'я"),
    (r"(?i)\bрозповсюдився\b", "поширився"),
    (r"(?i)\bгорящ([а-яіїєґ]*)\b", r"палаюч\1"),
    (r"(?i)\bявляется\b", "є"),
    (r"(?i)\bявляются\b", "є"),
    (r"(?i)\bданный\b", "цей"),
    (r"(?i)\bданного\b", "цього"),
    (r"(?i)\bв настоящее время\b", "наразі"),
    (r"(?i)\bбольшой огонь\b", "сильне полум'я"),
]

def fix_ukrainian_calques(text: str) -> str:
    text = re.sub(r"\bвсу\b", "ЗСУ", text, flags=re.IGNORECASE)
    text = re.sub(r"\bвійськові сили україни\b", "Збройні Сили України", text, flags=re.IGNORECASE)
    text = re.sub(r"\bвоенные силы украины\b", "Збройні Сили України", text, flags=re.IGNORECASE)

    def fix_moment(m):
        orig = m.group(0)
        return "Наразі" if orig[0].isupper() else "наразі"
    text = re.sub(r"\b(у|в|на)\s+дан(и|н)й\s+момент\b", fix_moment, text, flags=re.IGNORECASE)

    def fix_borivska(m):
        orig = m.group(0)
        suf = m.group(1) if m.lastindex else ""
        root = "Борівськ" if orig[0].isupper() else "борівськ"
        return root + suf

    def fix_andriivka(m):
        orig = m.group(0)
        suf = m.group(1) if m.lastindex else ""
        root = "Андріївк" if orig[0].isupper() else "андріївк"
        return root + suf

    text = re.sub(r"\bборовськ([а-яіїєґ]*)\b", fix_borivska, text, flags=re.IGNORECASE)
    text = re.sub(r"\bборовск([а-яіїєґ]*)\b", fix_borivska, text, flags=re.IGNORECASE)
    text = re.sub(r"\bандреївк([а-яіїєґ]*)\b", fix_andriivka, text, flags=re.IGNORECASE)
    text = re.sub(r"\bандреевк([а-яіїєґ]*)\b", fix_andriivka, text, flags=re.IGNORECASE)

    for pattern, replacement in CALQUE_REPLACEMENTS:
        text = re.sub(pattern, replacement, text)
    return text

# ==================== ФІЛЬТРИ СПАМУ ТА МЕМІВ ====================
COMMON_STOP_WORDS = {
    "сьогодні", "зараз", "вчора", "цього", "після", "також", "було", "проти", "може",
    "через", "буде", "тільки", "цьому", "коли", "якщо", "були", "вони", "яких", "який",
    "яка", "яке", "повідомляє", "новини", "року", "потім", "перед", "під", "над", "щоб",
    "стало", "відомо", "заявив", "словами", "джерело", "офіційно", "увага", "терміново",
    "внаслідок", "відповідно", "разом", "перший", "другий", "можуть", "бути", "своїх"
}

AD_PATTERNS = [
    r"(?i)\b(erid|реклама|спонсор|партнерський)\b",
    r"(?i)(кол-центр|колцентр|офіс|шахрайськ.*офіс|100\s*000\s*грн|винагород|зароби.*на.*викритті)",
    r"(?i)(подпишись|підпишись|подписаться|підписатися)\s*(на|в)?\s*(канал|нас|резерв|чат|джерело)",
    r"(?i)(казино|ставки|1win|parimatch|слоти|гемблінг|бонус|промокод|виграш|бездеп|беттинг)",
    r"(?i)(крипта|криптовалют|сигнали|трейдинг|btc|eth|usdt|заробіток|пасивний дохід|схема)",
    r"(?i)(переходьте за посиланням|тисни сюди|дивіться тут|продовження за лінком|подробиці за лінком)",
    r"(?i)(прислать новость|надіслати новину|зв'язок з адміном|наш бот|замовити рекламу)",
    r"(?i)(знижк|акці|розпродаж|купити зі знижкою|встигни забрати)",
    r"(?i)t\.me/\+[a-zA-Z0-9_-]+",
    r"(?i)\b(лайків|лайки|коментарів|фанатів|противників|просвітів|харчок|ізонів|янос)\b",
]

TRASH_AND_SARCASTIC_PATTERNS = [
    r"(?i)\b(родная гавань|у вас как|будни.*в крыму|все немного в шоке|проснулись без|залезла на крышу)\b",
    r"(?i)\b(девочки|доброго дня всем|смотрите что прислали|жалуются на свет|сидеть без света)\b",
    r"(?i)\b(psa\b|trump is still|still your president|мем\b|меми\b|мемчик|рофл|тролінг|жарт\b|анекдот)\b",
    r"(?i)\b(картинка дня|фото дня|кадр дня|рубрика|на ніч|перед сном|вечірній чат)\b",
    r"(?i)\b(трамп не є президентом|зайняв джо байден|20 січня 2021|документальних підтверджень|дезінформацією)\b",
]

POLITICAL_SMEAR_PATTERNS = [
    r"(?i)\b(що\s+їх\s+зв\'язує|что\s+их\s+связывает|що\s+зв\'язує.*між\s+собою|что\s+связывает.*между\s+собой)\b",
    r"(?i)\b(патріарх[ом]?\s+кирил.*філатов|патриарх[ом]?\s+кирилл.*филатов|філатов.*кирил|филатов.*кирилл)\b",
    r"(?i)\b(російський\s+слід|российский\s+след|злили\s+компромат|слили\s+компромат|хто\s+стоїть\s+за|кто\s+стоит\s+за)\b",
    r"(?i)\b(політичн[аий]\s+заказух|политическ[аий]\s+заказ|брудн[ийа]\s+піар|черный\s+пиар|новий\s+скандал\s+із|підкилимн[іа]\s+ігр)\b",
    r"(?i)\b(дивіться\s+до\s+кінця|смотрите\s+до\s+конца|що\s+ховається\s+за\s+цією\s+історією|что\s+скрывается\s+за\s+этой\s+историей)\b",
    r"(?i)\b(російський\s+політик.*філатов|российский\s+политик.*филатов)\b",
]

LOCAL_MUNICIPAL_PATTERNS = [
    r"(?i)\b(тестуванн[яі]\s+систем[и]?\s+сповіщен|тестуватимуть\s+систему|навчальн[іа]\s+тривог|перевірк[аи]\s+сирен|звучатимуть\s+сирени)\b",
    r"(?i)\b(графік\s+за\s+районами|дрогобицький|золочівський|самбірський|стрийський|яворівський|шептицький)\s+район",
    r"(?i)\b(львів\'яни|кияни|одесити|харків\'яни|дніпряни),\s*(з|у|на|сьогодні|завтра|увага)",
    r"(?i)\b(планов[іе]\s+ремонт[и]?\s+мереж|відключенн[яі]\s+газу\s+у|ремонт\s+водоканал|перекритт[яі]\s+руху\s+на\s+вулиц)\b",
]

ROUTINE_ALERT_TRIGGERS = [
    r"(?i)\b(жовт[аі]\s+тривог[аи]|повітрян[аі]\s+тривог[аи]|оголошен[оа]\s+тривог|загроз[аи]\s+бпла|загроз[аи]\s+шахед|пройдіть\s+в\s+укриття|відбій\s+тривоги)\b",
    r"(?i)\b(активність\s+ворожої\s+авіації|загроза\s+застосування\s+балістики|зліт\s+міг|тривог[аи]\s+в\s+областях|тривог[аи]\s+в\s+києві)\b",
    r"(?i)\b(фіксується\s+рух\s+бпла|група\s+шахедів\s+курсом|рух\s+безпілотників|напрямок\s+руху\s+бпла|шахеди\s+над\s+містом)\b",
    r"(?i)\b(шахед[и]?\s+на\s+київ|бпла\s+на\s+київ|дрон[и]?\s+в\s+напрямку|залишайтеся\s+в\s+безпечних|уникаючи\s+вікон)\b",
]

REAL_EVENT_MARKERS = [
    "пролунав вибух", "пролунали вибухи", "збито", "успішно збили", "влучання в",
    "приліт по", "масштабна пожежа", "руйнування", "поранені", "загиблі",
    "пряме влучання", "пошкоджено будинок", "пошкоджено підприємство", "ракета впала", "дрон поцілив"
]

def is_unwanted_content(channel: str, text: str) -> bool:
    if not text or len(text.strip()) < 20:
        return True
    clean = html.unescape(text).lower()

    for pattern in AD_PATTERNS:
        if re.search(pattern, clean):
            return True

    for pattern in TRASH_AND_SARCASTIC_PATTERNS:
        if re.search(pattern, clean):
            print(f"[ФІЛЬТР]: Відхилено треш/мем: {clean[:45]}...")
            return True

    for pattern in POLITICAL_SMEAR_PATTERNS:
        if re.search(pattern, clean):
            print(f"[ФІЛЬТР]: Відхилено політичний вкид/джинсу: {clean[:45]}...")
            return True

    for pattern in LOCAL_MUNICIPAL_PATTERNS:
        if re.search(pattern, clean):
            print(f"[ФІЛЬТР]: Відхилено локальне муніципальне оголошення: {clean[:45]}...")
            return True

    if channel.lower() in ["nefedoov365", "inslandd"]:
        if any(w in clean for w in ["мем", "рофл", "жарт", "😂", "🤣", "psa", "trump is"]):
            print(f"[ФІЛЬТР {channel}]: Відхилено розважальний мем/жарт.")
            return True

    if channel.lower() == "chp_zahid":
        national_keywords = ["удар", "ракета", "шахед", "дрон", "вибух", "ппо", "блекаут", "світл", "енерг", "кордон", "загинул", "пожеж"]
        if not any(k in clean for k in national_keywords):
            print("[ФІЛЬТР chp_zahid]: Пропускаємо локальну дрібницю.")
            return True

    if channel.lower() == "supernova_plus":
        combat_terms = ["зсу", "дрон", "удар", "вибух", "знищен", "уражен", "бавовн", "ппо", "ракета", "артилер", "фронт", "наступ"]
        if not any(t in clean for t in combat_terms):
            print("[ФІЛЬТР Supernova]: Пропускаємо побутовий пост.")
            return True

    is_routine = any(re.search(pat, clean) for pat in ROUTINE_ALERT_TRIGGERS)
    if is_routine:
        has_real_event = any(m in clean for m in REAL_EVENT_MARKERS)
        if not has_real_event:
            return True

    return False

# ==================== ВОТЕРМАРКА ====================
def apply_watermark_to_image(img_path: str, output_path: str) -> str:
    try:
        if not os.path.exists(WATERMARK_FILE) or not os.path.exists(img_path):
            return img_path

        base_img = Image.open(img_path).convert("RGB")
        width, height = base_img.size
        wm = Image.open(WATERMARK_FILE).convert("RGBA")

        wm_w = max(200, int(width * 0.35))
        wm_h = int(wm.height * (wm_w / wm.width))
        wm_resized = wm.resize((wm_w, wm_h), Image.Resampling.LANCZOS)

        erase_w = int(width * 0.55)
        erase_h = int(height * 0.16)
        erase_x = (width - erase_w) // 2
        erase_y = (height - erase_h) // 2

        blur_box = (erase_x, erase_y, erase_x + erase_w, erase_y + erase_h)
        erased_area = base_img.crop(blur_box).filter(ImageFilter.GaussianBlur(18))
        base_img.paste(erased_area, blur_box)

        pos_x = (width - wm_w) // 2
        pos_y = (height - wm_h) // 2
        base_img.paste(wm_resized, (pos_x, pos_y), mask=wm_resized)

        base_img.save(output_path, "JPEG", quality=92)
        return output_path
    except Exception as e:
        print(f"[ВОТЕРМАРКА ФОТО]: Помилка: {e}")
        return img_path

def apply_watermark_to_video(video_path: str, output_path: str) -> str:
    try:
        if not os.path.exists(WATERMARK_FILE) or not os.path.exists(video_path):
            return video_path

        filter_complex = (
            "[0:v]split[main][crop_in];"
            "[crop_in]crop=w=in_w*0.55:h=in_h*0.16:x=(in_w-out_w)/2:y=(in_h-out_h)/2,boxblur=10:2[blurred];"
            "[main][blurred]overlay=(W-w)/2:(H-h)/2[base];"
            "[1:v][base]scale2ref=w=main_w*0.35:h=-1[wm][base_ref];"
            "[base_ref][wm]overlay=(W-w)/2:(H-h)/2[v]"
        )

        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-i", WATERMARK_FILE,
            "-filter_complex", filter_complex,
            "-map", "[v]", "-map", "0:a?",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
            "-c:a", "copy",
            output_path
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90)
        if res.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 1000:
            return output_path
        return video_path
    except Exception as e:
        print(f"[ВОТЕРМАРКА ВІДЕО]: {e}")
        return video_path

# ==================== ЗБЕРЕЖЕННЯ ТА АНТИДУБЛІКАТ ====================
def load_json(path: str) -> list:
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []

def save_json(path: str, data: list):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

ACRONYMS = {
    "сбу", "набу", "зсу", "ппо", "тес", "аес", "дбр", "сап", "вакс", "гур",
    "рф", "сша", "оон", "єс", "fpv", "бпла", "нгу", "мвс", "огп"
}

def extract_entities_and_stems(text: str) -> Tuple[Set[str], Set[str]]:
    caps_raw = set(re.findall(r"\b[А-ЯІЇЄҐA-Z]{2,}\b|\b[А-ЯІЇЄҐA-Z][а-яіїєґa-z]{2,}\b", text))
    capitals_norm = set()
    for c in caps_raw:
        cl = c.lower()
        if cl not in COMMON_STOP_WORDS:
            capitals_norm.add(cl[:5] if len(cl) >= 5 else cl)

    clean = re.sub(r"[^\w\s]", " ", text.lower(), flags=re.UNICODE)
    stems = set()
    for w in clean.split():
        if w in ACRONYMS:
            stems.add(w)
            capitals_norm.add(w)
        elif len(w) >= 3 and w not in COMMON_STOP_WORDS:
            stems.add(w[:5] if len(w) >= 5 else w)

    return capitals_norm, stems

SEEDED_TOPICS = [
    {"capitals": ["поклад", "сбу", "набу"], "stems": ["поклад", "сбу", "набу", "звіль", "фігур", "мексик"], "headline": "Поклад звільнив співробітників СБУ фігурантів НАБУ"},
    {"capitals": ["лиман", "штурм", "робот"], "stems": ["лиман", "штурм", "робот", "позиц", "відби"], "headline": "Штурм роботів під Лиманом"},
    {"capitals": ["жовта", "тривога", "києвом"], "stems": ["жовт", "тривог", "києв", "загроз", "бпла"], "headline": "Жовта тривога загроза БПЛА Київ"},
    {"capitals": ["трамп", "байден"], "stems": ["трамп", "байден", "прези", "білог", "докум"], "headline": "Трамп не є президентом дезінформація"},
    {"capitals": ["fpv", "ждуни", "електропередач"], "stems": ["ждуни", "елект", "ліні", "дрони", "висок", "дріт"], "headline": "FPV дрони ждуни на лініях електропередач"},
    {"capitals": ["маск", "ілон", "республік"], "stems": ["маск", "ілон", "витра", "кампан", "вибор", "респуб", "парті"], "headline": "Ілон Маск витратив гроші на вибори"},
    {"capitals": ["донец", "дрони", "фенік", "адиге", "ozon"], "stems": ["донец", "телек", "інфра", "фенік", "київст", "адиге", "ozon"], "headline": "Донецьк Фенікс та Адигейськ Ozon"},
    {"capitals": ["філат", "кирил"], "stems": ["філат", "кирил", "росі", "патрі"], "headline": "Філатов та Кирил"},
    {"capitals": ["борів", "андрі", "зелен"], "stems": ["борів", "андрі", "звіль", "зелен"], "headline": "Борівська Андріївка та Зелений Гай"},
    {"capitals": ["боров", "андре", "зелен"], "stems": ["боров", "андре", "звіль", "зелен"], "headline": "Боровская Андреевка"},
    {"capitals": ["гвард", "крим", "аерод"], "stems": ["гвард", "аерод", "крим", "альфа"], "headline": "Аеродром Гвардійське в Криму"},
    {"capitals": ["житом", "укреп"], "stems": ["житом", "світл", "вимкн", "укреп"], "headline": "Житомиробленерго та відключення"},
    {"capitals": ["креме", "світл"], "stems": ["креме", "світл", "приль", "тес"], "headline": "Кременчуцька ТЕС та Світловодськ"},
    {"capitals": ["vanta", "stark"], "stems": ["vanta", "stark", "кораб", "стеж"], "headline": "Німецький безпілотний корабель Vanta 24"},
    {"capitals": ["родна", "гаван"], "stems": ["родна", "гаван", "свєта", "води"], "headline": "Родная гавань Крим"},
]

def load_cloud_state_from_telegram() -> dict:
    if not TELEGRAM_BOT_TOKEN or not ADMIN_CHAT_ID:
        return {}
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getChat?chat_id={ADMIN_CHAT_ID}"
        r = requests.get(url, timeout=10).json()
        if r.get("ok"):
            pinned = r.get("result", {}).get("pinned_message", {})
            text = pinned.get("text", "")
            if "#BOT_STATE#" in text:
                raw_json = text.split("#BOT_STATE#")[1].strip()
                state = json.loads(raw_json)
                print(f"[ХМАРНА БАЗА TELEGRAM]: Успішно відновлено {len(state.get('topics', []))} тем з хмари Telegram!")
                return state
    except Exception as e:
        print(f"[ХМАРНА БАЗА TELEGRAM]: Помилка читання: {e}")
    return {}

def save_cloud_state_to_telegram(state: dict):
    if not TELEGRAM_BOT_TOKEN or not ADMIN_CHAT_ID:
        return
    try:
        url_get = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getChat?chat_id={ADMIN_CHAT_ID}"
        r = requests.get(url_get, timeout=10).json()
        pinned_id = None
        if r.get("ok"):
            pinned = r.get("result", {}).get("pinned_message", {})
            if "#BOT_STATE#" in pinned.get("text", ""):
                pinned_id = pinned.get("message_id")

        payload_text = f"📌 [ХМАРНА БАЗА АНТИДУБЛІКАТІВ]\n#BOT_STATE#\n{json.dumps(state, ensure_ascii=False)}"

        if pinned_id:
            edit_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageText"
            requests.post(edit_url, json={"chat_id": ADMIN_CHAT_ID, "message_id": pinned_id, "text": payload_text[:4096]}, timeout=10)
        else:
            send_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            res = requests.post(send_url, json={"chat_id": ADMIN_CHAT_ID, "text": payload_text[:4096]}, timeout=10).json()
            if res.get("ok"):
                new_msg_id = res["result"]["message_id"]
                pin_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/pinChatMessage"
                requests.post(pin_url, json={"chat_id": ADMIN_CHAT_ID, "message_id": new_msg_id, "disable_notification": True}, timeout=10)
    except Exception as e:
        print(f"[ХМАРНА БАЗА TELEGRAM]: Помилка збереження: {e}")

def is_post_id_processed(post_id: str) -> bool:
    ids = set(load_json(PROCESSED_IDS_FILE))
    return post_id in ids

def register_post_id(post_id: str):
    if not post_id: return
    ids = load_json(PROCESSED_IDS_FILE)
    if post_id not in ids:
        ids.append(post_id)
        save_json(PROCESSED_IDS_FILE, ids[-600:])

def is_topic_already_covered(text: str) -> bool:
    topics = SEEDED_TOPICS + load_json(TOPICS_FILE)
    cur_caps, cur_stems = extract_entities_and_stems(text)
    now = time.time()
    min_time = now - (48 * 3600)

    for t in topics:
        if "timestamp" in t and t["timestamp"] < min_time:
            continue
        prev_caps = set(t.get("capitals", []))
        prev_stems = set(t.get("stems", []))

        common_caps = cur_caps.intersection(prev_caps)
        if len(common_caps) >= 2:
            print(f"[АНТИДУБЛЬ ГЕО]: Збіг сутностей {common_caps} з постом '{t.get('headline', '')[:35]}...'. ВІДХИЛЕНО!")
            return True

        common_stems = cur_stems.intersection(prev_stems)
        if len(common_stems) >= 3:
            print(f"[АНТИДУБЛЬ СЕНСУ]: Збіг слів {common_stems} з постом '{t.get('headline', '')[:35]}...'. ВІДХИЛЕНО!")
            return True

        if cur_stems and prev_stems:
            overlap = len(common_stems) / len(cur_stems.union(prev_stems))
            if overlap >= 0.28:
                print(f"[АНТИДУБЛЬ JACCARD]: Схожість {overlap:.2f} з постом '{t.get('headline', '')[:35]}...'. ВІДХИЛЕНО!")
                return True
    return False

def register_published_topic(headline: str, text: str):
    topics = load_json(TOPICS_FILE)
    caps, stems = extract_entities_and_stems(headline + " " + text)
    topics.append({
        "headline": headline,
        "capitals": list(caps),
        "stems": list(stems),
        "timestamp": time.time()
    })
    save_json(TOPICS_FILE, topics[-350:])

def is_media_duplicate(media_bytes: bytes, media_url: str = "") -> bool:
    if not media_bytes and not media_url:
        return False
    history = load_json(MEDIA_HASH_FILE)
    c_hash = hashlib.md5(media_bytes).hexdigest() if media_bytes else ""
    u_hash = hashlib.md5(media_url.encode("utf-8")).hexdigest() if media_url else ""
    now = time.time()
    min_time = now - (48 * 3600)

    for item in history:
        if item.get("timestamp", 0) < min_time:
            continue
        if c_hash and item.get("content_hash") == c_hash:
            print("[АНТИДУБЛЬ МЕДІА]: Збіг фото/відео за цифровим відбитком! ВІДХИЛЕНО!")
            return True
        if u_hash and item.get("url_hash") == u_hash:
            print("[АНТИДУБЛЬ МЕДІА]: Збіг посилання на медіафайл! ВІДХИЛЕНО!")
            return True
    return False

def register_media(media_bytes: bytes, media_url: str = ""):
    history = load_json(MEDIA_HASH_FILE)
    c_hash = hashlib.md5(media_bytes).hexdigest() if media_bytes else ""
    u_hash = hashlib.md5(media_url.encode("utf-8")).hexdigest() if media_url else ""
    history.append({
        "content_hash": c_hash,
        "url_hash": u_hash,
        "timestamp": time.time()
    })
    save_json(MEDIA_HASH_FILE, history[-350:])

# ==================== ЧИТАННЯ ВЛАСНОГО КАНАЛУ ====================
def sync_channel_history(channel_username: str) -> List[str]:
    recent_texts = []
    seen_texts = set()

    if TELEGRAM_BOT_TOKEN:
        try:
            upd_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates?offset=-50&limit=50&allowed_updates=%5B%22channel_post%22%2C%22edited_channel_post%22%5D"
            resp = requests.get(upd_url, timeout=12).json()
            if resp.get("ok"):
                for item in resp.get("result", []):
                    post = item.get("channel_post") or item.get("edited_channel_post") or item.get("message")
                    if post:
                        t = (post.get("text") or post.get("caption") or "").strip()
                        if t and len(t) > 20 and t not in seen_texts:
                            seen_texts.add(t)
                            head = t.splitlines()[0][:60]
                            register_published_topic(head, t)
                            recent_texts.append(t[:140])
                if recent_texts:
                    print(f"[API СИНХРОНІЗАЦІЯ]: Зчитано {len(recent_texts)} постів вашого каналу через Bot API!")
        except Exception as e:
            print(f"[API СИНХРОНІЗАЦІЯ]: {e}")

    actual_username = channel_username
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHANNEL_ID:
        try:
            chat_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getChat?chat_id={TELEGRAM_CHANNEL_ID}"
            c_resp = requests.get(chat_url, timeout=10).json()
            if c_resp.get("ok"):
                u = c_resp.get("result", {}).get("username")
                if u:
                    actual_username = f"@{u}"
                pinned = c_resp.get("result", {}).get("pinned_message", {})
                p_text = (pinned.get("text") or pinned.get("caption") or "").strip()
                if p_text and p_text not in seen_texts:
                    seen_texts.add(p_text)
                    head = p_text.splitlines()[0][:60]
                    register_published_topic(head, p_text)
                    recent_texts.append(p_text[:140])
        except Exception:
            pass

    clean_name = actual_username.replace("https://t.me/", "").replace("t.me/", "").replace("@", "").strip("/")
    if clean_name:
        url = f"https://t.me/s/{clean_name}"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36"}
        try:
            r = requests.get(url, headers=headers, timeout=12)
            if r.status_code == 200:
                html_raw = r.text
                blocks = html_raw.split('<div class="tgme_widget_message_wrap')
                for b in blocks[1:][-30:]:
                    m = re.search(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>\s*<div class="tgme_widget_message_footer', b, re.DOTALL)
                    if not m:
                        continue
                    clean = html.unescape(re.sub(r"<[^>]+>", " ", m.group(1))).strip()
                    if len(clean) > 20 and clean not in seen_texts:
                        seen_texts.add(clean)
                        head = clean.splitlines()[0][:60]
                        register_published_topic(head, clean)
                        recent_texts.append(clean[:140])
                print(f"[СИНХРОНІЗАЦІЯ]: Зчитано додатково {len(blocks[1:])} постів із вебу @{clean_name}.")
        except Exception as e:
            print(f"[СИНХРОНІЗАЦІЯ]: Помилка веб-читання @{clean_name}: {e}")

    return recent_texts

# ==================== ШІ-ОБРОБКА (АНТИФЕЙК + АНТИГАЛЮЦИНАЦІЯ) ====================
SYSTEM_PROMPT = """Ти — професійний український журналіст та суворий військовий фактчекер новинного каналу.
Твоє завдання — перекласти та відредагувати новину ЧИСТОЮ СОЛОВ'ЇНОЮ МОВОЮ строго за фактами.

ТИПОВИЙ ЗРАЗОК ІДЕАЛЬНОГО ПОСТУ КАНАЛУ:
💥 Дрони атакували телекомунікаційну інфраструктуру у Донецьку

Під удар потрапив окупаційний оператор «Фенікс», який працює на вкраденому після окупації обладнанні «Київстару». Зафіксовано щонайменше три влучання.

Тим часом у Адигейську горить Ozon, ймовірно, внаслідок атаки.

ОСНОВНИЙ ПРІОРИТЕТ:
- Головний акцент — на подіях в Україні (фронт, енергетика, обстріли, важливі закони).
- Світові новини публікуй тільки якщо вони вагомі (допомога партнерів, санкції, геополітика).

СУВОРІ ПРАВИЛА:
1. АНТИДУБЛІКАТ:
   - Тобі надано список останніх публікацій каналу. Якщо ця сама подія ВЖЕ є в каналі — негайно встанови "is_duplicate": true!
2. КАТЕГОРИЧНА ЗАБОРОНА ВИГАДУВАТИ ЦИФРИ, ЛОКАЦІЇ ТА ЛЮДЕЙ:
   - СУВОРО ЗАБОРОНЕНО додавати будь-які числа втрат, поранених, загиблих, полонених чи техніки, якщо їх НЕМАЄ в тексті донора!
   - Не вигадуй села, райони, мерів чи посадовців!
   - Блок <blockquote> використовуй ТІЛЬКИ тоді, коли в тексті донора Є реальна цитата конкретної особи. Немає цитати — не став <blockquote>!
3. ВІЙСЬКОВИЙ ФАКТЧЕКІНГ ПІДРОЗДІЛІВ:
   - 3-тя окрема штурмова бригада (3 ОШБр), ЗСУ, ГУР, ССО, Азов — це ЗАХИСНИКИ УКРАЇНИ! Категорично заборонено писати, що українські бригади атакували чи штурмували «українські позиції»!
   - Якщо наші воїни йдуть на штурм — вони штурмують ВОРОЖІ позиції окупантів.
4. ВСЕУКРАЇНСЬКИЙ МАСШТАБ ТА ЗАБОРОНА РУТИННИХ СИРЕН:
   - Канал публікує ВИКЛЮЧНО НОВИНИ ПРО РЕАЛЬНІ ФАКТИЧНІ ПОДІЇ ТА НАСЛІДКИ!
   - КАТЕГОРИЧНО ЗАБОРОНЕНО публікувати рутинні тривоги, застереження про дрони чи заклики сидіти в укриттях, якщо НЕМАЄ факту влучання, збиття чи руйнувань!
   - Відкидай локальні комунальні оголошення (тестування сирен, ремонт труб, графіки води чи газу).
5. ПОЛІТИЧНИЙ ВКИД ТА ЧОРНИЙ ПІАР:
   - КАТЕГОРИЧНО ЗАБОРОНЕНО публікувати замовний компромат, плітки про мерів чи політиків без офіційних пресрелізів правоохоронних органів (СБУ, ДБР, НАБУ)!
6. БЕЗДОГАННА МОВА ТА ПРАВОПИС:
   - Тільки чиста солов'їна мова. Жодних кальок: ЗСУ (не ВСУ), Збройні Сили України (не Військові сили), наразі (не у даний момент).
   - Власні назви: Запоріжсталь (не Запорізхсталь!), балістичний/балістика (з 1 буквою «л»), підприємство/комбінат (не «фірма»).
7. ОФОРМЛЕННЯ:
   - Заголовок <b>жирним</b> з 1 емодзі. Звичайними реченнями, БЕЗ КАПСУ!
   - 1-2 короткі фактичні абзаци.
   - ЖОДНИХ підписів у кінці, жодного значка "💬".

Формат відповіді СТРОГО JSON:
{
  "is_duplicate": false,
  "is_ad": false,
  "telegram_post": "Текст якісного посту з тегами <b>, <i> та <blockquote>",
  "news_score": 7,
  "is_mega_news": false,
  "video_headline": "Заголовок для відео",
  "video_script": "Текст дикторської озвучки"
}
"""

def clean_donor_mentions(text: str) -> str:
    text = html.unescape(text)
    text = re.sub(r"https?://\S+|t\.me/\S+|@[a-zA-Z0-9_]+", "", text)
    text = re.sub(r"(?i)\b(ус\s*/?|украина сейчас|инсайдер|униан|supernova|труха|нефедов)\b", "", text).strip()
    return text

def process_news_with_ai(raw_text: str, recent_channel_posts: List[str]) -> Optional[Dict]:
    cleaned = clean_donor_mentions(raw_text)
    if len(cleaned) < 25:
        return None

    api_key = AI_API_KEY.strip()
    if not api_key:
        print("[AI]: AI_API_KEY не налаштовано!")
        return None

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0 Safari/537.36",
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    recent_context = ""
    if recent_channel_posts:
        recent_context = "ОСЬ ОСТАННІ ПОСТИ В КАНАЛІ КОРИСТУВАЧА:\n" + "\n".join([f"- {p}" for p in recent_channel_posts[:25]]) + "\n\n"

    user_prompt = f"{recent_context}НОВА НОВИНА ДЛЯ ОБРОБКИ (перевір на дубль та переклади чистою українською строго за фактами без вигадування цифр):\n{cleaned}"

    models_to_try = [
        "llama-3.3-70b-versatile",
        "qwen/qwen-2.5-72b-instruct",
        "openai/gpt-oss-120b",
        "meta-llama/llama-3.3-70b-instruct",
        "llama-3.1-70b-versatile",
        "mixtral-8x7b-32768",
        "gemma2-9b-it",
        "llama-3.1-8b-instant"
    ]
    try:
        r = requests.get("https://api.groq.com/openai/v1/models", headers=headers, timeout=8)
        if r.status_code == 200:
            active = [m["id"] for m in r.json().get("data", []) if not any(x in m["id"].lower() for x in ["whisper", "guard", "vision", "embed"])]
            if active:
                sorted_models = [m for m in models_to_try if m in active]
                for m in active:
                    if m not in sorted_models:
                        sorted_models.append(m)
                models_to_try = sorted_models
    except Exception:
        pass

    for model in models_to_try:
        try:
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt}
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0.0
            }
            resp = requests.post("https://api.groq.com/openai/v1/chat/completions", json=payload, headers=headers, timeout=25)
            if resp.status_code != 200:
                continue
            data = resp.json()
            parsed = json.loads(data["choices"][0]["message"]["content"])
            if parsed.get("is_duplicate", False):
                print("[AI ДУБЛЬ]: AI розпізнав цю подію як повтор. ВІДХИЛЕНО!")
                return None
            if parsed.get("is_ad", False):
                return None

            if "telegram_post" in parsed:
                parsed["telegram_post"] = fix_ukrainian_calques(parsed["telegram_post"])

            return parsed
        except Exception:
            continue
    return None

# ==================== ВІРУСНІ ВІДЕО ДЛЯ СОЦМЕРЕЖ ====================
def build_short_video(headline: str, script_text: str, image_url: Optional[str] = None, source_video_url: Optional[str] = None, output_filename: str = "viral_short.mp4") -> str:
    if not image_url and not source_video_url:
        return ""

    out_path = os.path.join(OUTPUT_DIR, output_filename)
    audio_path = os.path.join(MEDIA_DIR, "temp_voice.m4a")
    clean_speech = re.sub(r'[\U00010000-\U0010ffff]', '', script_text).strip()

    try:
        cmd = ["edge-tts", "--voice", "uk-UA-OstapNeural", "--rate=+10%", "--text", clean_speech, "--write-media", audio_path]
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=40)
    except Exception:
        pass

    if not os.path.exists(audio_path):
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", "12", "-c:a", "aac", audio_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    dur = 12.0
    try:
        res = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", audio_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        dur = max(5.0, float(res.stdout.strip()))
    except Exception:
        pass

    head_clean = re.sub(r'[\U00010000-\U0010ffff]', '', headline).upper()[:50].replace(":", " - ").replace("'", "").replace('"', '').strip()
    lines = textwrap.wrap(head_clean, width=22)
    l1 = lines[0] if len(lines) > 0 else "ГОЛОВНІ НОВИНИ"
    l2 = lines[1] if len(lines) > 1 else ""

    header_draw = (
        f"drawbox=x=40:y=200:w=1000:h=280:color=black@0.85:t=fill,"
        f"drawbox=x=40:y=200:w=1000:h=8:color=red:t=fill,"
        f"drawtext=text='ТЕРМІНОВА НОВИНА':fontcolor=yellow:fontsize=42:x=(w-text_w)/2:y=230,"
        f"drawtext=text='{l1}':fontcolor=white:fontsize=46:x=(w-text_w)/2:y=300"
        + (f",drawtext=text='{l2}':fontcolor=white:fontsize=46:x=(w-text_w)/2:y=370" if l2 else "")
    )
    cta_draw = (
        f",drawbox=x=60:y=1460:w=960:h=150:color=black@0.85:t=fill,"
        f"drawbox=x=60:y=1460:w=960:h=6:color=yellow:t=fill,"
        f"drawtext=text='БІЛЬШЕ В ТЕЛЕГРАМ - {CHANNEL_USERNAME}':fontcolor=yellow:fontsize=42:x=(w-text_w)/2:y=1495,"
        f"drawtext=text='Посилання закріплено в шапці профілю':fontcolor=white:fontsize=34:x=(w-text_w)/2:y=1555"
    )

    if source_video_url:
        temp_src = os.path.join(MEDIA_DIR, "temp_src.mp4")
        r = requests.get(source_video_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
        if r.status_code == 200:
            with open(temp_src, "wb") as f: f.write(r.content)
            fc = (
                f"[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=25:5[bg];"
                f"[0:v]scale=1000:880:force_original_aspect_ratio=decrease[fg];"
                f"[bg][fg]overlay=(W-w)/2:520+(900-h)/2[base];"
                f"[base]{header_draw}{cta_draw}[v]"
            )
            cmd = ["ffmpeg", "-y", "-i", temp_src, "-filter_complex", fc, "-map", "[v]", "-map", "0:a?", "-c:v", "libx264", "-preset", "ultrafast", "-t", "45", "-c:a", "aac", out_path]
            subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if os.path.exists(out_path): return out_path

    if image_url:
        temp_img = os.path.join(MEDIA_DIR, "temp_pic.jpg")
        r = requests.get(image_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        if r.status_code == 200:
            with open(temp_img, "wb") as f: f.write(r.content)
            fc = (
                f"[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=25:5[bg];"
                f"[0:v]scale=1000:880:force_original_aspect_ratio=decrease[fg];"
                f"[bg][fg]overlay=(W-w)/2:520+(900-h)/2[base];"
                f"[base]{header_draw}{cta_draw}[v]"
            )
            cmd = ["ffmpeg", "-y", "-loop", "1", "-i", temp_img, "-i", audio_path, "-filter_complex", fc, "-map", "[v]", "-map", "1:a", "-c:v", "libx264", "-preset", "ultrafast", "-t", str(dur), "-c:a", "aac", out_path]
            subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if os.path.exists(out_path): return out_path
    return ""

def send_admin_social_video(video_path: str, headline: str, caption: str):
    if not ADMIN_CHAT_ID: return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendVideo"
    msg = f"🎬 <b>ВІДЕО ДЛЯ TIKTOK / SHORTS / REELS</b>\n\n📌 <b>{headline}</b>\n\n📝 <i>Опис:</i>\n{caption}\n\n🔗 <i>Реклама: {CHANNEL_USERNAME}</i>"
    try:
        with open(video_path, "rb") as f:
            requests.post(url, data={"chat_id": ADMIN_CHAT_ID, "caption": msg[:1024], "parse_mode": "HTML"}, files={"video": ("video.mp4", f, "video/mp4")}, timeout=90)
    except Exception as e:
        print(f"[Admin Video]: {e}")

# ==================== ПУБЛІКАЦІЯ В TELEGRAM ====================
def send_telegram_post(post_text: str, photo_url: Optional[str] = None, video_url: Optional[str] = None, donor: str = "") -> bool:
    url_base = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
    caption = post_text[:1024]
    has_heavy_wm = donor.lower() in CHANNELS_WITH_HEAVY_WATERMARKS

    # 1. Відео
    if video_url:
        temp_vid = os.path.join(MEDIA_DIR, "temp_vid.mp4")
        branded_vid = os.path.join(MEDIA_DIR, "branded_vid.mp4")
        try:
            r = requests.get(video_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=60, stream=True)
            if r.status_code == 200:
                with open(temp_vid, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1024*1024):
                        if chunk: f.write(chunk)

                with open(temp_vid, "rb") as vf:
                    video_sample = vf.read(2 * 1024 * 1024)

                if is_media_duplicate(video_sample, video_url):
                    print(f"[АНТИДУБЛЬ ВІДЕО]: Відео з @{donor} вже публікувалося раніше! ВІДХИЛЕНО!")
                    return False

                if has_heavy_wm and os.path.exists(WATERMARK_FILE):
                    final_video = apply_watermark_to_video(temp_vid, branded_vid)
                    print(f"[Telegram]: Замінено чужий вотермарк з @{donor} на медіа-бейдж {CHANNEL_NAME}.")
                else:
                    final_video = temp_vid
                    print(f"[Telegram]: Чисте відео з @{donor}, публікуємо без вотермарки.")

                with open(final_video, "rb") as f:
                    resp = requests.post(f"{url_base}/sendVideo", data={"chat_id": TELEGRAM_CHANNEL_ID, "caption": caption, "parse_mode": "HTML"}, files={"video": ("video.mp4", f, "video/mp4")}, timeout=90)
                    if resp.json().get("ok", False):
                        register_media(video_sample, video_url)
                        return True
                    else:
                        print(f"[Telegram]: Збій sendVideo ({resp.text[:80]}). Пробуємо оригінал...")
                        if final_video != temp_vid and os.path.exists(temp_vid):
                            with open(temp_vid, "rb") as f_orig:
                                r2 = requests.post(f"{url_base}/sendVideo", data={"chat_id": TELEGRAM_CHANNEL_ID, "caption": caption, "parse_mode": "HTML"}, files={"video": ("video.mp4", f_orig, "video/mp4")}, timeout=90)
                                if r2.json().get("ok", False):
                                    register_media(video_sample, video_url)
                                    return True
        except Exception as e:
            print(f"[Telegram]: Помилка відправки відео: {e}")
        return False

    # 2. Фото
    if photo_url:
        try:
            r = requests.get(photo_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
            if r.status_code == 200 and len(r.content) > 500:
                if is_media_duplicate(r.content, photo_url):
                    return False
                temp_img = os.path.join(MEDIA_DIR, "temp_img.jpg")
                branded_img = os.path.join(MEDIA_DIR, "branded_img.jpg")
                with open(temp_img, "wb") as f:
                    f.write(r.content)

                if has_heavy_wm and os.path.exists(WATERMARK_FILE):
                    final_img = apply_watermark_to_image(temp_img, branded_img)
                    print(f"[Telegram]: Замінено чужий вотермарк з @{donor} на медіа-бейдж {CHANNEL_NAME}.")
                else:
                    final_img = temp_img
                    print(f"[Telegram]: Чисте фото з @{donor}, публікуємо без вотермарки.")

                with open(final_img, "rb") as f:
                    resp = requests.post(f"{url_base}/sendPhoto", data={"chat_id": TELEGRAM_CHANNEL_ID, "caption": caption, "parse_mode": "HTML"}, files={"photo": ("photo.jpg", f, "image/jpeg")}, timeout=30)
                    if resp.json().get("ok", False):
                        register_media(r.content, photo_url)
                        return True
        except Exception as e:
            print(f"[Telegram]: Помилка відправки фото: {e}")

    # 3. Текст
    try:
        resp = requests.post(f"{url_base}/sendMessage", data={"chat_id": TELEGRAM_CHANNEL_ID, "text": post_text, "parse_mode": "HTML", "disable_web_page_preview": True}, timeout=20)
        return resp.json().get("ok", False)
    except Exception as e:
        print(f"[Telegram]: Помилка надсилання тексту: {e}")
        return False

# ==================== ПАРСЕР ====================
def fetch_posts(channel: str) -> List[Dict]:
    url = f"https://t.me/s/{channel}"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0 Safari/537.36"}
    posts = []
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            html_raw = resp.read().decode("utf-8", errors="ignore")
        blocks = html_raw.split('<div class="tgme_widget_message_wrap')
        for b in blocks[1:][-4:]:
            p_id = re.search(r'data-post="([^"]+)"', b)
            if not p_id: continue
            post_id = p_id.group(1)

            if is_post_id_processed(post_id):
                continue

            time_match = re.search(r'<time[^>]+datetime="([^"]+)"', b)
            if time_match:
                try:
                    dt = datetime.fromisoformat(time_match.group(1).replace("Z", "+00:00"))
                    diff_hours = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
                    if diff_hours > MAX_POST_AGE_HOURS:
                        continue
                except Exception:
                    pass

            t_match = re.search(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>\s*<div class="tgme_widget_message_footer', b, re.DOTALL)
            raw_text = html.unescape(re.sub(r"<[^>]+>", " ", t_match.group(1))).strip() if t_match else ""

            unescaped = html.unescape(b)
            p_match = re.search(r'background-image:\s*url\([\'"]?(https?://[^\'")\s]+)[\'"]?\)', unescaped)
            photo_url = p_match.group(1) if p_match else None
            v_match = re.search(r'<video[^>]+src=[\'"](https?://[^\'"]+)[\'"]', unescaped)
            video_url = v_match.group(1) if v_match else None

            if video_url:
                photo_url = None

            if raw_text or photo_url or video_url:
                posts.append({
                    "channel": channel,
                    "text": raw_text,
                    "photo_url": photo_url,
                    "video_url": video_url,
                    "post_id": post_id
                })
    except Exception as e:
        print(f"[ПАРСЕР]: Помилка читання @{channel}: {e}")
    return posts

# ==================== ГОЛОВНИЙ ЦИКЛ ====================
def run():
    print(f"\n{'='*55}")
    print(f"[{datetime.now().strftime('%H:%M:%S')}] СКАНИНГ КАНАЛІВ. Балансування донорів, фільтр сміття та заміна вотермарок.")
    print(f"{'='*55}")

    cloud = load_cloud_state_from_telegram()
    if cloud:
        if cloud.get("topics"):
            cur_t = load_json(TOPICS_FILE)
            cur_t_heads = {x.get("headline") for x in cur_t}
            for ct in cloud["topics"]:
                if ct.get("headline") not in cur_t_heads:
                    cur_t.append(ct)
            save_json(TOPICS_FILE, cur_t[-400:])

        if cloud.get("media"):
            cur_m = load_json(MEDIA_HASH_FILE)
            cur_m_hashes = {x.get("content_hash") for x in cur_m}
            for cm in cloud["media"]:
                if cm.get("content_hash") and cm.get("content_hash") not in cur_m_hashes:
                    cur_m.append(cm)
            save_json(MEDIA_HASH_FILE, cur_m[-400:])

        if cloud.get("ids"):
            cur_ids = set(load_json(PROCESSED_IDS_FILE))
            cur_ids.update(cloud["ids"])
            save_json(PROCESSED_IDS_FILE, list(cur_ids)[-600:])

    recent_channel_posts = sync_channel_history(CHANNEL_USERNAME)
    published_count = 0
    channel_posts_used = set()

    candidates_by_channel = {}
    for ch in DONOR_CHANNELS:
        posts = fetch_posts(ch)
        if posts:
            candidates_by_channel[ch] = posts

    interleaved_candidates = []
    max_len = max((len(p) for p in candidates_by_channel.values()), default=0)
    for idx in range(max_len):
        for ch in DONOR_CHANNELS:
            if ch in candidates_by_channel and idx < len(candidates_by_channel[ch]):
                interleaved_candidates.append(candidates_by_channel[ch][idx])

    print(f"[ПУЛ КАНДИДАТІВ]: Зібрано {len(interleaved_candidates)} свіжих постів з {len(candidates_by_channel)} каналів.")

    for p in interleaved_candidates:
        if published_count >= MAX_POSTS_PER_RUN:
            break

        channel = p["channel"]

        if channel in channel_posts_used and len(candidates_by_channel) > 1:
            continue

        text = p["text"]
        photo = p["photo_url"]
        video = p["video_url"]
        post_id = p.get("post_id", "")

        if is_unwanted_content(channel, text):
            register_post_id(post_id)
            continue

        if is_topic_already_covered(text):
            register_post_id(post_id)
            continue

        if video and is_media_duplicate(b"", video):
            register_post_id(post_id)
            continue

        ai = process_news_with_ai(text, recent_channel_posts)
        if not ai:
            register_post_id(post_id)
            continue

        post_text = ai.get("telegram_post", "")
        if len(post_text) < 25:
            register_post_id(post_id)
            continue

        if published_count > 0:
            print(f"[ПАУЗА]: Очікуємо {DELAY_BETWEEN_POSTS} сек перед наступним постом...")
            time.sleep(DELAY_BETWEEN_POSTS)

        posted = send_telegram_post(post_text, photo_url=photo, video_url=video, donor=channel)
        if posted:
            published_count += 1
            channel_posts_used.add(channel)
            head = post_text.splitlines()[0][:60] if post_text.splitlines() else post_text[:60]
            register_published_topic(head, text)
            register_post_id(post_id)
            recent_channel_posts.insert(0, head)
            print(f"✅ УСПІШНО ОПУБЛІКОВАНО ({published_count}/{MAX_POSTS_PER_RUN}) з @{channel}: {head[:50]}...")

            try:
                score = ai.get("news_score", 5)
                is_mega = ai.get("is_mega_news", False)
                has_combat = any(w in text.lower() for w in ["знищен", "удар", "збит", "дрон", "ракета", "бавовн", "вибух", "прильот"])
                is_top_news = (is_mega and score >= 8) or (score >= 9) or (video and has_combat and score >= 8)

                if is_top_news and (photo or video):
                    v_head = ai.get("video_headline", "ГОЛОВНІ НОВИНИ")
                    v_script = ai.get("video_script", "")
                    v_caption = f"{v_head}\n\nОперативні деталі в Telegram: {CHANNEL_INVITE_LINK}\n#новини #україна #shorts #reels #tiktok"
                    v_file = f"viral_{int(time.time())}.mp4"
                    created_video = build_short_video(v_head, v_script, image_url=photo, source_video_url=video, output_filename=v_file)
                    if created_video:
                        send_admin_social_video(created_video, v_head, v_caption)
                        print("🎬 Змонтовано та надіслано адміну ролик для соцмереж!")
                else:
                    print(f"[ВІДЕО СОЦМЕРЕЖІ]: Пропущено (новина має оцінку {score}/10).")
            except Exception as ve:
                print(f"[ВІДЕО СОЦМЕРЕЖІ]: Помилка створення чи надсилання відео: {ve}")

    try:
        active_state = {
            "topics": load_json(TOPICS_FILE)[-35:],
            "media": load_json(MEDIA_HASH_FILE)[-35:],
            "ids": load_json(PROCESSED_IDS_FILE)[-100:],
            "last_sync": time.time()
        }
        save_cloud_state_to_telegram(active_state)
    except Exception as se:
        print(f"[ХМАРА ЗБЕРЕЖЕННЯ]: {se}")

    print(f"\n[ЗАВЕРШЕННЯ]: Опубліковано свіжих якісних новин: {published_count}. Чекаємо наступного запуску.")

if __name__ == "__main__":
    run()
