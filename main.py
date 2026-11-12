import os
import re
import time
import json
import logging
import datetime
import tempfile
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import html
import requests

# ---------------------------------------------------------
# НАЛАШТУВАННЯ ТА ЛОГУВАННЯ
# ---------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger("NEWS_365_BOT")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID", "@kropivnit").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
PORT = int(os.getenv("PORT", "10000"))
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL", "").strip()

# ПАУЗА ТА ЛІМІТИ (стосуються ТІЛЬКИ звичайних новин зі скрапера!):
REGULAR_COOLDOWN = int(os.getenv("REGULAR_COOLDOWN", "180"))
MAX_REGULAR_PER_HOUR = int(os.getenv("MAX_REGULAR_PER_HOUR", "8"))

# ПОВНИЙ СПИСОК КАНАЛІВ-ДОНОРІВ (24 БАЗОВІ + 10 ДОДАНИХ ВАМИ):
DEFAULT_DONORS = [
    # Загальнонаціональні оперативні медіа та офіційні структури:
    "insiderUKR",            # Інсайдер UA
    "voynareal",             # Реальна Війна
    "u_now",                 # Україна Зараз
    "uniannet",              # УНІАН
    "RBC_ua_news",           # РБК-Україна
    "oko_ua",                # Всевидящее ОКО: Україна | Політика
    "suspilnenews",          # Суспільне Новини
    "truexanewsua",          # Труха Україна
    "babel",                 # Бабель
    "lachentyt",             # Лачен пише
    "Tsaplienko",            # Андрій Цаплієнко
    "operativnoZSU",         # Оперативно ЗСУ
    "kpszsu",                # Повітряні Сили ЗСУ
    "DeepStateUA",           # DeepState
    "novynylive",            # Новини.LIVE
    "hromadske_ua",          # Громадське
    "radiosvoboda",          # Радіо Свобода
    "censor_net",            # Цензор.НЕТ
    "espresotv",             # Еспресо
    "focus_ua",              # Фокус
    "znua_live",             # Дзеркало тижня (ZN.UA)
    "interfaxua",            # Інтерфакс-Україна
    "ukrpravda_news",        # Українська правда
    "liganet",               # ЛІГА.net

    # Канали, додані користувачем:
    "exilenova_plus",        # Exilenova+ (фільтруємо дрібниці та не-новини)
    "UaOnlii",               # Україна Online: Новини | Політика
    "ukrinform_1",           # UKR INFORM 🇺🇦 Новини Україна
    "zsuwar",                # Збройні Сили України. Війна з окупантами
    "radnyknews",            # Радник
    "insider_zsy",           # Інсайдер ЗСУ
    "inslandd",              # INVASION (очищаємо від сарказму / меми пропускаємо)
    "nefedovv365",           # 365.nefed
    "xameleon_ua",           # ✙ Хамелеон
    "chp_zahid"              # ЧП Захід | НОВИНИ 24/7
]

DONOR_CHANNELS = [ch.strip().replace("@", "") for ch in os.getenv("DONOR_CHANNELS", "").split(",") if ch.strip()] or DEFAULT_DONORS

SEEN_POSTS_FILE = "seen_posts.txt"
MAX_SEEN_POSTS = 3000

recent_regular_post_timestamps = []
global_published_titles = []

# ---------------------------------------------------------
# ЗЧИТУВАННЯ ВЛАСНОГО КАНАЛУ (ЗАХИСТ ПРИ ПЕРЕЗАПУСКУ НА RENDER)
# ---------------------------------------------------------
def fetch_own_channel_history(channel_username: str) -> list:
    ch = channel_username.replace("@", "").strip()
    url = f"https://t.me/s/{ch}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)"
    }
    history = []
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            texts = re.findall(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', resp.text, re.DOTALL)
            for raw_t in texts:
                clean_t = re.sub(r'<[^>]+>', '', raw_t).strip()
                if clean_t and len(clean_t) > 20:
                    first_line = clean_t.split('\n')[0].replace('*', '').strip()
                    history.append(first_line)
            logger.info(f"Завантажено {len(history)} останніх заголовків із каналу @{ch}")
    except Exception as e:
        logger.warning(f"Не вдалося зчитати історію @{ch}: {e}")
    return history[-30:]

# ---------------------------------------------------------
# ВИЗНАЧЕННЯ ТЕРМІНОВОСТІ (ЕКСКЛЮЗИВ / МЕГА ВАЖЛИВЕ)
# ---------------------------------------------------------
BREAKING_KEYWORDS = [
    'термінов', 'breaking', 'блискавк', 'вибух', 'влучан', 'приліт',
    'масован', 'почався обстріл', 'заява зеленськ', 'путін', 'трамп',
    'ексклюзив', 'важливо!', 'офіційно', 'переговори', 'надзвичайн',
    'повітряні сили повідомляють', 'збито ракету', 'збито літак',
    'атака на', 'загиблих', 'постраждалих'
]

def is_breaking_news(text: str) -> bool:
    t = text.lower()
    return any(k in t for k in BREAKING_KEYWORDS)

# ---------------------------------------------------------
# ФІЛЬТРИ: РЕКЛАМА, ДАЙДЖЕСТИ ТА РАДАРНІ ТРИВОГИ
# ---------------------------------------------------------
STRICT_AD_KEYWORDS = [
    'favbet', 'фавбет', 'cosmolot', 'космолот', 'slotoking', 'слотокінг',
    'онлайн-казино', 'промокод', 'партнерський матеріал', 'на правах реклами',
    'erid:', 'рекламний пост', 'підпишись на наш інший канал'
]

def is_strict_ad(text: str) -> bool:
    t = text.lower()
    return any(kw in t for kw in STRICT_AD_KEYWORDS)

DIGEST_KEYWORDS = [
    'вечірній підсумок', 'ранковий підсумок', 'підсумок дня', 'підсумки дня',
    'підсумки доби', 'дайджест', 'вечірній дайджест', 'ранковий дайджест',
    'нічний дайджест', 'головне за день', 'головне за сьогодні', 'головне на цей час',
    'головне за ніч', 'головне за добу', 'головні новини дня', 'головні події дня',
    'що відомо на цей час', 'коротко про головне', 'коротко за ніч',
    'картина дня', 'топ новин', 'підсумки тижня', 'хроніка дня',
    'вечерний итог', 'итоги дня', 'главное за день', 'утренний дайджест',
    'вечерний дайджест', 'главное к этому часу'
]

def is_digest_post(text: str) -> bool:
    t = text.lower()
    if any(kw in t for kw in DIGEST_KEYWORDS):
        return True

    lines = [line.strip() for line in text.split('\n') if line.strip()]
    bullet_count = 0
    for line in lines:
        if re.match(r'^(?:[•▪▫—–\-*]|📍|📌|❗️|⚡️|\d+[\.\)])\s+[А-ЯҐЄІЇA-Z]', line):
            bullet_count += 1
    if bullet_count >= 3:
        return True

    return False

def is_air_tracking_alert(text: str) -> bool:
    """
    Суворо відсікаємо радарні повідомлення про тривоги, ракети, шахеди та балістику
    як українською, так і російською мовами, якщо немає ПІДТВЕРДЖЕНИХ руйнувань чи жертв.
    """
    t = text.lower()

    impact_markers = [
        'влучан', 'попадан', 'приліт', 'прилет', 'руйнуван', 'разрушен',
        'пожеж', 'пожар', 'постраждал', 'пострадавш', 'загинул', 'погиб',
        'поранен', 'ранен', 'вирва', 'воронк', 'пошкоджен', 'поврежден',
        'розібрали завали', 'разобрали завалы', 'наслідки атаки', 'последствия атаки',
        'було збито', 'было сбито', 'збито 0', 'збито 1', 'збито 2', 'збито 3',
        'збито 4', 'збито 5', 'збито 6', 'збито 7', 'збито 8', 'збито 9',
        'сбито 1', 'сбито 2', 'сбито 3', 'сбито 4', 'сбито 5', 'сбито 6',
        'результати роботи ппо', 'результаты работы пво'
    ]
    if any(m in t for m in impact_markers):
        return False

    short_alerts = [
        'повітряна тривога', 'воздушная тревога', 'красна тревога', 'красная тревога',
        'червона тривога', 'відбій тривоги', 'отбой тревоги', 'загроза балістики',
        'угроза баллистики', 'ракетна небезпека', 'ракетная опасность', 'увага!', 'внимание!',
        'швидкісна ціль', 'скоростная цель', 'загроза застосування', 'угроза применения'
    ]
    if any(m in t for m in short_alerts) and len(t) < 300:
        return True

    target_markers = [
        'шахед', 'бпла', 'дрон', 'безпілотник', 'беспилотник', 'ракета',
        'каб', 'балістик', 'баллистик', 'крилат', 'крылат', 'кинджал', 'кинжал',
        'іскандер', 'искандер', 'повітрян', 'воздушн', 'ціль', 'цель', 'цели',
        'борт', 'ту-95', 'міг-31', 'миг-31', 'авіаці', 'авиаци', 'реактивн'
    ]

    tracking_markers = [
        'курс', 'напрямок', 'направлен', 'вектор', 'повз', 'мимо', 'в бік', 'в сторону',
        'наближаєть', 'приближает', 'підлітає', 'подлетает', 'заходить', 'заходит',
        'фіксуєть', 'фиксирует', 'чисто', 'відбій', 'отбой',
        'загроза', 'угроза', 'рухаєть', 'движет', 'помічено', 'замечен', 'над містом',
        'над городом', 'над ', 'в районі', 'в районе', 'з півдня', 'с юга',
        'з півночі', 'с севера', 'зі сходу', 'с востока', 'з заходу', 'с запада',
        'маневрує', 'маневрирует', 'висота', 'высота', 'локаційно', 'локационно',
        'швидкісна ціль', 'скоростная цель', 'пуск', 'пуски', 'тривога', 'тревога',
        'укритт', 'укрыти', 'не ігноруйте', 'не игнорируйте',
        'лунають вибухи', 'слышны взрывы', 'чутно вибухи', 'атакували', 'атаковали', 'атакують', 'атакуют'
    ]

    has_target = any(m in t for m in target_markers)
    has_tracking = any(m in t for m in tracking_markers)

    if has_target and has_tracking:
        return True

    if len(t) < 250 and re.search(
        r"\b(?:каб(?:и|і|ів)?|шахед(?:и|ів)?|бпла|дрон(?:и|ів)?|ракет(?:а|и)?)\s+(?:на|у\s+бік|в\s+бік|до|в\s+напрямку|над)\b",
        t,
        re.IGNORECASE
    ):
        return True

    return False

# ---------------------------------------------------------
# ОЧИЩЕННЯ ВІД СУРЖИКУ ТА ПАФОСУ
# ---------------------------------------------------------
SURZHYK_MAP = [
    (r'\bрозгорнулися вибухи\b', 'пролунали вибухи'),
    (r'\bвідбулись вибухи\b', 'пролунали вибухи'),
    (r'\bвідбулися вибухи\b', 'пролунали вибухи'),
    (r'\bв конфлікті\b', 'під час війни'),
    (r'\bкрасна тревога\b', 'повітряна тривога'),
    (r'\bкрасная тревога\b', 'повітряна тривога'),
    (r'\bбаллістик', 'балістик'),
    (r'\bбаллістичн', 'балістичн'),
    (r'\bмілітарні сили\b', 'військові'),
    (r'\bзамглавы\b', 'заступник керівника'),
    (r'\bзапасаемся\b', 'запасаємося'),
    (r'\bготовимся\b', 'готуємося'),
    (r'\bдлительной войне\b', 'тривалої війни'),
    (r'\bдлительной войны\b', 'тривалої війни'),
    (r'\bдлительную войну\b', 'тривалу війну'),
    (r'\bвойне\b', 'війні'),
    (r'\bвойны\b', 'війни'),
    (r'\bвойну\b', 'війну'),
    (r'\bвойна\b', 'війна'),
    (r'\bводой\b', 'водою'),
    (r'\bводы\b', 'води'),
    (r'\bводу\b', 'воду'),
    (r'\bв октябр[іе]\b', 'у жовтні'),
    (r'\bв ноябр[іе]\b', 'у листопаді'),
    (r'\bв декабр[іе]\b', 'у грудні'),
    (r'\bв январ[іе]\b', 'у січні'),
    (r'\bв феврал[іе]\b', 'у лютому'),
    (r'\bв март[іе]\b', 'у березні'),
    (r'\bв апр[іе]л[іе]\b', 'у квітні'),
    (r'\bв ма[єе]\b', 'у травні'),
    (r'\bв іюн[іе]\b', 'у червні'),
    (r'\bв іюл[іе]\b', 'у липні'),
    (r'\bв август[іе]\b', 'у серпні'),
    (r'\bв сентябр[іе]\b', 'у вересні'),
    (r'\bГосстат\b', 'Держстат'),
    (r'\bводоотведення\b', 'водовідведення'),
    (r'\bпаливо і смазки\b', 'паливно-мастильні матеріали'),
    (r'\bпаливно-змащувальні\b', 'паливно-мастильні'),
    (r'\bпідросли\b', 'зросли'),
    (r'\bсклала\b', 'становила'),
    (r'\bв даний час\b', 'наразі'),
    (r'\bна протязі\b', 'протягом'),
    (r'\bприймати участь\b', 'брати участь'),
    (r'\bпо крайній мірі\b', 'принаймні'),
    (r'\bслідуючий\b', 'наступний'),
    (r'\bв якості\b', 'як'),
    (r'\bзгідно даних\b', 'за даними'),
    (r'\bводночас з цим\b', 'водночас')
]

def clean_surzhyk_and_russian(text: str) -> str:
    cleaned = text
    for pattern, replacement in SURZHYK_MAP:
        cleaned = re.sub(pattern, replacement, cleaned, flags=re.IGNORECASE)
    return cleaned

DONOR_CHANNEL_TAGS = [
    r"insider\s*ua", r"інсайдер\s*ua", r"инсайдер\s*ua", r"інсайдер", r"инсайдер",
    r"труха\s*украина", r"труха\s*україна", r"труха", r"truexa",
    r"реальная\s*война", r"реальна\s*війна",
    r"украина\s*сейчас", r"україна\s*зараз", r"u_now",
    r"ус:\s*украина", r"ус\s*новини", r"ус",
    r"уніан", r"униан", r"unian",
    r"рбк[- ]украина", r"рбк[- ]україна", r"rbc",
    r"цензор\.нет", r"цензор",
    r"бабель", r"babel", r"лачен", r"lachen",
    r"deepstate", r"оперативно\s*зсу", r"повітряні\s*сили",
    r"exilenova", r"invasion", r"хамелеон", r"чп\s*захід", r"радник"
]
DONOR_TAG_PATTERN = r"(?i)\s*[,—–-]\s*(?:" + "|".join(DONOR_CHANNEL_TAGS) + r")[^\n]*$"

def strip_donor_artifacts(text: str) -> str:
    cleaned = re.sub(r"https?://(?:t|telegram)\.me/[A-Za-z0-9_+/]+", "", text, flags=re.IGNORECASE)
    cleaned = re.sub(r"t\.me/[A-Za-z0-9_+/]+", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"@[A-Za-z0-9_]{3,}", "", cleaned)

    lines = cleaned.splitlines()
    filtered_lines = []
    for l in lines:
        l_str = l.strip().lower()
        if any(w in l_str for w in ["підписатися", "подписаться", "підпишись", "подпишись", "приєднатися", "читати далі", "долучайтесь"]):
            continue
        cleaned_l = re.sub(DONOR_TAG_PATTERN, "", l).rstrip(" ,—–-")
        filtered_lines.append(cleaned_l)
    cleaned = "\n".join(filtered_lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()

def clean_ai_fluff(text: str) -> str:
    fluff_patterns = [
        r'\n+(?:Це свідчить|Це вкотре|Це черговий доказ|Так тримати|Ворог продовжує|Варто зазначити|Слід зазначити|Нагадаємо, що|Використання .* дозволяє).*?$',
        r'\n*(?:Балістичні ракети, що застосовуються|Ракети часто призводять|Це викликає занепокоєння|Постійний моніторинг радіоактивних).*?$'
    ]
    cleaned = text
    for p in fluff_patterns:
        cleaned = re.sub(p, '', cleaned, flags=re.IGNORECASE | re.DOTALL)
    return cleaned.strip()

# ---------------------------------------------------------
# GROQ API: СУЧАСНИЙ ДИНАМІЧНИЙ ТЕЛЕГРАМ-СТИЛЬ
# ---------------------------------------------------------
SYSTEM_PROMPT = """Ти — головний редактор провідного українського новинного Telegram-каналу "NEWS УКРАЇНА 365" (@kropivnit).
Твоє завдання — журналістська фіксація та обробка реальних суспільних подій, новин та інцидентів в Україні для інформування населення (включаючи надзвичайні події, роботу ТЦК, кримінальну хроніку, заяви та фронтові зведення).
Це документальна робота преси. Викладай факти збалансовано, нейтрально та грамотною мовою новин, без пропаганди насильства.

ЗАЛІЗНІ ПРАВИЛА РЕДАКЦІЇ:

1. СУВОРА ФАКТИЧНІСТЬ ТА ЛОГІКА (ЖОДНИХ ВИГАДОК!):
   - Пиши ВИКЛЮЧНО за фактами, які є у вихідному тексті!
   - КАТЕГОРИЧНО ЗАБОРОНЕНО вигадувати час (напр. «22:15»), пожежі, рятувальників, медиків, евакуацію, кількість жертв/постраждалих чи заяви ЗСУ, якщо цього НЕМАЄ в оригіналі!
   - Якщо вихідна новина коротка (1–2 речення) — твій пост ТЕЖ має бути коротким (заголовок + 1 коротке речення). НІКОЛИ не роздувай коротке повідомлення!
   - Уважно читай ролі учасників: не плутай, хто що робив, і не вживай абсурдних фраз.

2. ЗАГОЛОВОК (ОБОВ'ЯЗКОВО В **ЖИРНОМУ** З ЕМОДЗІ):
   - Перший рядок — ЗАВЖДИ жирний заголовок з емодзі: **🤯 Скандал із ТЦК у Нововолинську: місцеві з лопатами відбивали чоловіка**
   - ДЖЕРЕЛО В КІНЦІ ЗАГОЛОВКА (наприклад, `, — Reuters`, `, — Генштаб`, `, — Зеленський`): вказуй ТІЛЬКИ якщо є реальне міжнародне ЗМІ або офіційна особа.
   - КАТЕГОРИЧНО ЗАБОРОНЕНО ставити безглузді приписки (жодних «– ТЦК, Нововолинськ») та заборонено вказувати чужі телеграм-канали («– Труха», «– INSIDER UA»)! Якщо це відео чи побутова сутичка — заголовок закінчується БЕЗ джерела.

3. ТІЛО ПОСТА (1–2 ДИНАМІЧНІ АБЗАЦИ):
   - Чітко й грамотно виклади суть. Обов'язково виділяй **жирним** головні ключові факти (місто, учасників, ключову дію).
   - Жодних матюків, без слів «дивитися до кінця», без посилань на ботів.

4. СУВОРО 100% ДЕРЖАВНА УКРАЇНСЬКА МОВА:
   - Якщо новина російською мовою — ПОВНІСТЮ переклади заголовок і весь текст чистою солов'їною українською. Жодного російського слова («Запасаємося», «Заступниця керівника ОП»).

5. ЗАБОРОНА ЕНЦИКЛОПЕДИЧНОЇ «ВОДИ» ТА ШІ-БАЛАЧОК:
   - НІКОЛИ не вступай у діалог! Твоя відповідь — це ВИКЛЮЧНО готовий пост для каналу. Заборонено писати «Надішліть, будь ласка...» чи «Ось варіант...».
   - ЖОДНИХ загальних філософствувань з Вікіпедії («Балістичні ракети часто призводять до вибухів...», «що викликає занепокоєння»).
   - НІКОЛИ не називай війну «конфліктом»! Це війна, обстріл або атака РФ.
   - Не повторюй заголовок двічі у першому ж реченні.
   - Для коротких блискавок («Вибухи у Києві») роби лаконічний оперативний пост: що чути, де чути, заклик пройти в укриття.

6. ТЕРМІНОЛОГІЯ ВІЙНИ:
   - КАБ — це керована авіаційна бомба (НІКОЛИ не пиши «касетні боєприпаси» замість КАБ!).
   - БпЛА / Шахед — безпілотник / ударний дрон.

7. ЧИСТОТА ВІД ЧУЖОГО СПАМУ:
   - Видаляй назви чужих каналів («УС», «Труха», «Інсайдер»), посилання t.me та чужі заклики підписатися.
"""

PRIMARY_GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b").strip() or "openai/gpt-oss-120b"

# Резервні моделі — вмикаються ТІЛЬКИ якщо основна модель не пропустила новину (429 або цензура)
BACKUP_GROQ_MODELS = [
    "qwen/qwen3.8-27b",
    "llama-3.3-70b-versatile",
    "openai/gpt-oss-20b",
    "qwen/qwen3-32b",
    "llama-3.1-8b-instant",
]

AI_REFUSAL_PATTERNS = [
    r"i(?:'m| am)? sorry",
    r"i cannot",
    r"i can(?:'t|not)",
    r"as an ai",
    r"i am unable",
    r"my apologies",
    r"help with that",
    r"language model",
    r"safety guidelines",
    r"вибачте",
    r"я не можу",
    r"надішліть(?:, будь ласка)?",
    r"надішли(?:, будь ласка)?",
    r"текст новини, яку треба",
    r"я підготую стильний",
    r"ось варіант",
    r"ось рерайт"
]

def is_ai_refusal(text: str) -> bool:
    if not text:
        return True
    t = text.strip()
    t_lower = t.lower()
    for p in AI_REFUSAL_PATTERNS:
        if re.search(r"\b" + p + r"\b", t_lower, re.IGNORECASE):
            return True
    if any(phrase in t_lower for phrase in ["надішліть, будь ласка", "надішліть текст", "я підготую стильний"]):
        return True
    if len(t) < 80 and not (t.startswith("**") or t.startswith("<b>")):
        if any(w in t_lower for w in ["sorry", "cannot", "can't", "unable", "help with that", "language model"]):
            return True
    return False

def has_hallucinated_facts(source_text: str, output_text: str) -> bool:
    """
    Програмна перевірка на вигадані нейромережею факти:
    1) Вигаданий час (HH:MM), якого не було в оригіналі.
    2) Штучне роздування короткого повідомлення у довгу статтю.
    3) Вигадані жертви, пожежники, евакуація чи спростування, яких не було в оригіналі.
    """
    src_l = source_text.lower()
    out_l = output_text.lower()

    out_times = set(re.findall(r"\b\d{1,2}:\d{2}\b", out_l))
    src_times = set(re.findall(r"\b\d{1,2}:\d{2}\b", src_l))
    if out_times - src_times:
        logger.warning(f"Галюцинація часу! Вигадано: {out_times - src_times}")
        return True

    if len(source_text.strip()) < 160 and len(output_text.strip()) > max(360, len(source_text.strip()) * 3 + 120):
        logger.warning(f"Штучне роздування короткої новини ({len(source_text)} -> {len(output_text)} символів)")
        return True

    sensitive_groups = [
        (["евакуац", "эвакуац"], ["евакуац", "эвакуац"]),
        (["пожежник", "пожарн", "рятувальн", "спасател"], ["пожежник", "пожарн", "рятувальн", "спасател", "дснс", "гсчс"]),
        (["медичн", "медик", "швидка", "скорая"], ["медичн", "медик", "швидк", "скорая", "лікар", "врач"]),
        (["загибл", "погиб", "жертв", "смерт"], ["загибл", "загинул", "погиб", "жертв", "смерт", "труп", "200"]),
        (["постраждал", "поранен", "пострадав", "ранен", "травм"], ["постраждал", "поранен", "пострадав", "ранен", "травм", "ушкодж"]),
        (["заперечил", "спростувал", "опроверг"], ["заперечил", "спростувал", "опроверг", "фейк"]),
        (["запалені будівл", "районному сектору"], ["будівл", "здани", "дом", "сектор"]),
        (["касетн", "кассетн"], ["касетн", "кассетн"]),
    ]
    for out_markers, src_markers in sensitive_groups:
        if any(m in out_l for m in out_markers) and not any(m in src_l for m in src_markers):
            logger.warning(f"Галюцинація фактів! Знайдено {out_markers}, яких немає в оригіналі.")
            return True

    return False

model_cooldowns = {}

def _parse_retry_after_seconds(err_text: str) -> int:
    m = re.search(r"try again in\s+(?:(\d+)m)?([\d.]+)s", err_text, re.IGNORECASE)
    if m:
        mins = int(m.group(1) or 0)
        secs = float(m.group(2) or 0)
        return max(30, min(int(mins * 60 + secs), 3600))
    return 120

def call_groq_with_fallback(messages: list, temperature: float = 0.45, max_tokens: int = 2048) -> str:
    if not GROQ_API_KEY:
        logger.error("GROQ_API_KEY не задано!")
        return ""

    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }

    models_to_try = [PRIMARY_GROQ_MODEL] + [m for m in BACKUP_GROQ_MODELS if m != PRIMARY_GROQ_MODEL]

    now = time.time()
    for model_name in models_to_try:
        is_primary = (model_name == PRIMARY_GROQ_MODEL)

        if now < model_cooldowns.get(model_name, 0):
            wait_left = int(model_cooldowns[model_name] - now)
            if is_primary:
                logger.info(f"Основна модель {PRIMARY_GROQ_MODEL} ще чекає завершення ліміту ({wait_left}с). Тимчасово використовуємо резерв...")
            continue

        payload = {
            "model": model_name,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens
        }
        try:
            resp = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers=headers,
                json=payload,
                timeout=25
            )
            if resp.status_code == 200:
                res_json = resp.json()
                content = res_json["choices"][0]["message"]["content"].strip()
                content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
                if is_ai_refusal(content):
                    logger.warning(f"Модель {model_name} видала safety refusal: {content[:100]}. Для цієї новини перемикаюсь на резервну...")
                    continue

                if not is_primary:
                    logger.info(f"Новину успішно оброблено резервною {model_name}. На наступну новину бот автоматично повертається на нашу основну {PRIMARY_GROQ_MODEL}!")
                return content
            elif resp.status_code == 429:
                wait_sec = _parse_retry_after_seconds(resp.text)
                model_cooldowns[model_name] = time.time() + wait_sec
                logger.warning(f"Модель {model_name} вперлася в ліміт (429), пауза {wait_sec}с. Перемикаюсь на резерв...")
                continue
            elif resp.status_code in (400, 404):
                model_cooldowns[model_name] = time.time() + 3600
                logger.warning(f"Модель {model_name} недоступна ({resp.status_code}): {resp.text[:200]}")
                continue
            else:
                logger.error(f"Помилка Groq {model_name} ({resp.status_code}): {resp.text[:300]}")
                continue
        except Exception as e:
            logger.error(f"Виняток Groq {model_name}: {e}")
            continue

    logger.error("Усі моделі Groq наразі вичерпали ліміти або не змогли відповісти.")
    return ""


def has_russian_text(text: str) -> bool:
    t = text.lower()
    if any(ch in t for ch in ["ы", "э", "ё", "ъ"]):
        return True
    ru_markers = [
        r"\bв киеве\b", r"\bв харькове\b", r"\bв одессе\b", r"\bв днепре\b",
        r"\bпопадание\b", r"\bместные сми\b", r"\bсообщают\b", r"\bв результате\b",
        r"\bпроизошел\b", r"\bпроизошла\b", r"\bсейчас\b", r"\bтакже\b", r"\bоднако\b",
        r"\bмид\b", r"\bсша\b", r"\bрасскажет\b", r"\bзаявил\b", r"\bпозицию\b"
    ]
    if any(re.search(p, t) for p in ru_markers):
        return True
    cyrillic_count = len(re.findall(r"[а-яА-ЯёЁ]", t))
    has_ukr_letters = any(ch in t for ch in ["і", "ї", "є", "ґ"])
    if cyrillic_count > 25 and not has_ukr_letters:
        return True
    return False

def _google_translate_ru_to_uk(text: str) -> str:
    try:
        resp = requests.get(
            "https://translate.googleapis.com/translate_a/single",
            params={
                "client": "gtx",
                "sl": "ru",
                "tl": "uk",
                "dt": "t",
                "q": text
            },
            timeout=10
        )
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list) and data and isinstance(data[0], list):
                translated = "".join(part[0] for part in data[0] if part and part[0])
                if translated.strip():
                    return translated.strip()
    except Exception as e:
        logger.warning(f"Помилка резервного перекладу Google Translate: {e}")
    return ""

def translate_to_ukrainian(text: str) -> str:
    if not text.strip():
        return text
    if GROQ_API_KEY:
        messages = [
            {
                "role": "system",
                "content": "Ти — перекладач. Переклади текст чистою українською мовою. Повертай ТІЛЬКИ перекладений текст, без коментарів, без пояснень і без додавання нових фактів."
            },
            {"role": "user", "content": text}
        ]
        translated = call_groq_with_fallback(messages, temperature=0.1, max_tokens=1024)
        if translated and not is_ai_refusal(translated):
            return strip_donor_artifacts(clean_surzhyk_and_russian(translated))

    gt = _google_translate_ru_to_uk(text)
    if gt:
        logger.info("Використано резервний переклад Google Translate (Groq недоступний)")
        return strip_donor_artifacts(clean_surzhyk_and_russian(gt))
    return text

def format_fallback_post(text: str) -> str:
    if has_russian_text(text):
        text = translate_to_ukrainian(text)
    text = clean_surzhyk_and_russian(text)
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    if not lines:
        return ""
    first_line = lines[0]
    body = "\n\n".join(lines[1:])
    starts_with_emoji = bool(re.match(r"^[‼️❗️⚡️🚨🔴👉⏩➡️▶️💥🔥🇺🇦🇷🇺🇺🇸🇪🇺🇬🇧]", first_line))
    header = f"**{first_line}**" if starts_with_emoji else f"**⚡️ {first_line}**"
    return f"{header}\n\n{body}" if body else header

def rewrite_news(raw_text: str, published_titles: list) -> str:
    cleaned_input = strip_donor_artifacts(raw_text)
    is_short = len(cleaned_input) < 160

    recent_titles_str = "\n".join([f"- {t}" for t in published_titles[-30:]]) if published_titles else "(ще немає публікацій)"

    length_instruction = (
        "- Це КОРОТКЕ повідомлення! Напиши ТІЛЬКИ заголовок + 1 коротке речення (або лише заголовок), суто за фактом оригіналу. НЕ роздувай текст і НЕ додавай жодних вигаданих подробиць!"
        if is_short else
        "- Тіло: 1–2 грамотні стислі абзаци з виділенням **жирним** ключових слів, суто за фактами оригіналу."
    )

    user_prompt = f"""СПИСОК ОСТАННІХ ОПУБЛІКОВАНИХ НОВИН:
{recent_titles_str}

НОВА НОВИНА ДЛЯ ОБРОБКИ:
{cleaned_input}

ПРАВИЛА ОБРОБКИ:
1. Якщо це ТА САМА подія, що вже була у списку — напиши тільки DUPLICATE.
2. Якщо це ДАЙДЖЕСТ (підсумок дня/вечора, перелік кількох подій) — напиши SKIP.
3. Якщо це ПОВІТРЯНА ТРИВОГА, загроза балістики/ракет/шахедів, радарне стеження чи попередження про небезпеку БЕЗ фактів реальних влучань, вибухів чи руйнувань — напиши тільки SKIP.
4. Якщо це прихований піар, реклама чи пустий саркастичний мем — напиши SKIP.
5. Якщо це повноцінна новина — ЗРОБИ СТИЛЬНИЙ РЕРАЙТ СУТО ЗА ФАКТАМИ ОРИГІНАЛУ:
   - Рядок 1: **Емодзі + чіткий інформативний заголовок** (джерело вказуй ТІЛЬКИ якщо це Reuters/Генштаб/чиновник; для відео/повідомлень із соцмереж жодних приписок не став!).
   {length_instruction}
   - КАТЕГОРИЧНО ЗАБОРОНЕНО вигадувати час (22:15 тощо), постраждалих, пожежі, евакуацію чи заяви ЗСУ, якщо їх немає в оригіналі!
   - Без матюків, без канцеляризмів та без згадок чужих каналів («УС», «Труха» тощо)."""

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt}
    ]

    content = call_groq_with_fallback(messages, temperature=0.35, max_tokens=2048)
    if not content or is_ai_refusal(content):
        logger.warning("Groq не повернув валідного рерайту або видав відмову. Пропуск.")
        return ""

    if "DUPLICATE" in content.upper()[:15]:
        logger.info("Groq визначив новину як семантичний повтор (DUPLICATE)")
        return ""

    if content.upper() == "SKIP" or "SKIP" in content[:10]:
        logger.info("Groq відхилив новину як дайджест/піар/рекламу/мем/дрібниці (SKIP)")
        return ""

    content = clean_ai_fluff(content)
    content = clean_surzhyk_and_russian(content)
    if has_russian_text(content):
        content = translate_to_ukrainian(content)
    content = strip_donor_artifacts(content)

    if has_hallucinated_facts(cleaned_input, content):
        strict_messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"ПЕРЕКЛАДИ ТА ОФОРМИ ЦЕЙ ТЕКСТ СУВОРО ПО ФАКТАХ, НІЧОГО НЕ ДОДАВАЙ ВІД СЕБЕ (ЖОДНОГО ЧАСУ, ЖЕРТВ, ПОЖЕЖ ЧИ ЕВАКУАЦІЇ):\n\n{cleaned_input}"}
        ]
        retry_content = call_groq_with_fallback(strict_messages, temperature=0.1, max_tokens=1024)
        if not retry_content or is_ai_refusal(retry_content) or has_hallucinated_facts(cleaned_input, retry_content):
            logger.warning("Повторна спроба також містила домисли. Пропускаємо пост, щоб не публікувати фейк.")
            return ""
        content = strip_donor_artifacts(clean_surzhyk_and_russian(clean_ai_fluff(retry_content)))

    return content

def rewrite_news_for_admin(raw_text: str) -> str:
    cleaned_input = strip_donor_artifacts(raw_text)
    if not GROQ_API_KEY:
        return cleaned_input

    is_short = len(cleaned_input) < 160
    length_instruction = (
        "- Це КОРОТКЕ повідомлення! Напиши ТІЛЬКИ заголовок + 1 коротке речення (або лише заголовок) суто за фактом. КАТЕГОРИЧНО ЗАБОРОНЕНО роздувати текст чи вигадувати деталі!"
        if is_short else
        "- Тіло: 1–2 стислі динамічні абзаци суто по суті з **жирним виділенням** ключових слів."
    )

    user_prompt = f"""ТЕРМІНОВЕ ПОВІДОМЛЕННЯ ДЛЯ ПУБЛІКАЦІЇ В КАНАЛ:
{cleaned_input}

ЗАВДАННЯ:
Зроби ГОТОВИЙ Telegram-пост (ніяких балачок, питань чи вступних слів!):
- Рядок 1: **Емодзі + чіткий заголовок**
- Порожній рядок
{length_instruction}
- КАТЕГОРИЧНО ЗАБОРОНЕНО вигадувати час (напр. 22:15), постраждалих, пожежників, евакуацію чи заяви ЗСУ, яких немає в оригіналі!
- Без Вікіпедії, без «води» про занепокоєння і без слова «конфлікт»."""

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt}
    ]
    content = call_groq_with_fallback(messages, temperature=0.35, max_tokens=2048)

    if not content or is_ai_refusal(content) or has_hallucinated_facts(cleaned_input, content):
        strict_messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"ПЕРЕКЛАДИ УКРАЇНСЬКОЮ ТА ОФОРМИ ПО ФАКТАХ ОРИГІНАЛУ, НІЧОГО НЕ ВИГАДУЙ І НЕ ДОДАВАЙ ВІД СЕБЕ:\n\n{cleaned_input}"}
        ]
        retry_content = call_groq_with_fallback(strict_messages, temperature=0.1, max_tokens=1024)
        if retry_content and not is_ai_refusal(retry_content) and not has_hallucinated_facts(cleaned_input, retry_content):
            content = retry_content
        else:
            logger.warning("Модель вигадала факти або відмовилась. Оформлюємо чистий переклад без домислів.")
            return format_fallback_post(cleaned_input)

    content = clean_ai_fluff(content)
    content = clean_surzhyk_and_russian(content)
    if has_russian_text(content):
        content = translate_to_ukrainian(content)
    content = strip_donor_artifacts(content)
    return content

def clean_html_text(raw_html: str) -> str:
    text = re.sub(r'<br\s*/?>', '\n', raw_html, flags=re.IGNORECASE)
    text = re.sub(r'<a\b[^>]*>(.*?)</a>', r'\1', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<[^>]+>', '', text)
    text = html.unescape(text)
    return text.strip()

PUBLISHED_HISTORY_FILE = "published_history.json"
MAX_PUBLISHED_HISTORY = 60

STOP_WORDS = {
    'про', 'що', 'для', 'але', 'або', 'який', 'яка', 'яке', 'які', 'його',
    'її', 'їх', 'було', 'буде', 'були', 'був', 'була', 'коли', 'якщо', 'після',
    'через', 'цього', 'цьому', 'також', 'може', 'можуть', 'тому', 'більш',
    'менш', 'дуже', 'цього', 'року', 'році', 'років', 'сьогодні', 'повідомляє',
    'повідомив', 'заявив', 'заявила', 'заявили', 'словами', 'джерело', 'фото',
    'відео', 'канал', 'підписатися', 'деталі', 'відомо', 'зараз', 'наразі',
    'новини', 'знову', 'такі', 'інші', 'саме', 'лише', 'поки', 'что', 'или',
    'было', 'будет', 'были', 'когда', 'если', 'после', 'этого', 'этому',
    'также', 'может', 'могут', 'более', 'менее', 'очень', 'сообщает', 'сообщил'
}

def stem_ru_ukr(word: str) -> str:
    w = word.lower().strip()
    w = re.sub(r"['’`\-]", "", w)
    if len(w) <= 3:
        return w
    w = w.replace("ы", "и").replace("э", "е").replace("ё", "е").replace("ъ", "")
    w_norm = w.replace("і", "и").replace("ї", "и")

    if "войн" in w_norm or "вийн" in w_norm:
        return "войн"
    if "вод" in w_norm:
        return "вод"
    if "запас" in w_norm:
        return "запас"
    if "готов" in w_norm or "готув" in w_norm:
        return "готов"
    if "тривал" in w_norm or "длител" in w_norm:
        return "тривал"

    endings = [
        "ості", "ости", "остях", "івського", "івському", "ського", "ському",
        "івська", "івське", "івські", "ення", "ання", "инна", "иння",
        "яться", "ються", "ється", "итися", "атися", "уться", "ются", "ется",
        "емся", "етесь", "итесь", "ались", "ились", "елись",
        "ного", "ному", "ними", "них", "ний", "ним", "ною", "ній",
        "ому", "ого", "его", "ему", "ими", "ыми", "ою", "ею", "ей", "ой", "ях", "ах", "ів", "ям", "ам",
        "ами", "ями", "ові", "еві", "єві", "ий", "ій", "ый", "ать", "ить", "еть",
        "а", "е", "є", "и", "і", "о", "у", "ю", "я"
    ]
    for end in endings:
        if w_norm.endswith(end) and len(w_norm) - len(end) >= 3:
            return w_norm[:-len(end)]
    return w_norm

def extract_keywords_stemmed(text: str) -> set:
    words = re.findall(r'[а-яіїєґa-z0-9]{3,}', text.lower())
    stems = set()
    for w in words:
        if w in STOP_WORDS:
            continue
        st = stem_ru_ukr(w)
        if len(st) >= 3 and st not in STOP_WORDS:
            stems.add(st)
    return stems

def is_duplicate_news(new_text: str, published_history: list, threshold: float = 0.35) -> bool:
    new_kw = extract_keywords_stemmed(new_text)
    if len(new_kw) < 3:
        return False

    for item in published_history:
        old_kw = set(item.get("stems", []))
        if not old_kw:
            continue
        common = new_kw & old_kw
        smaller_len = min(len(new_kw), len(old_kw))
        score = len(common) / smaller_len if smaller_len > 0 else 0
        if score >= threshold:
            logger.info(f"Алгоритмічний дублікат! Схожість {score:.2f}, спільні корені: {common}")
            return True
    return False

def load_published_history() -> list:
    if os.path.exists(PUBLISHED_HISTORY_FILE):
        try:
            with open(PUBLISHED_HISTORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
        except Exception as e:
            logger.warning(f"Не вдалося завантажити {PUBLISHED_HISTORY_FILE}: {e}")
    return []

def save_published_history(history: list):
    try:
        with open(PUBLISHED_HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history[-MAX_PUBLISHED_HISTORY:], f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"Не вдалося зберегти {PUBLISHED_HISTORY_FILE}: {e}")

def scrape_channel(channel: str, seen_set: set) -> list:
    url = f"https://t.me/s/{channel}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)"
    }

    try:
        resp = requests.get(url, headers=headers, timeout=12)
        if resp.status_code != 200:
            return []

        html_content = resp.text
        message_chunks = re.findall(
            r'(<div class="tgme_widget_message_wrap[^"]*"[^>]*>.*?)(?=<div class="tgme_widget_message_wrap|$)',
            html_content,
            re.DOTALL
        )

        now = datetime.datetime.now(datetime.timezone.utc)
        results = []

        for chunk in message_chunks[-5:]:
            id_match = re.search(r'data-post="([^"]+)"', chunk)
            if not id_match:
                continue
            post_id = id_match.group(1)

            if post_id in seen_set:
                continue

            time_match = re.search(r'<time[^>]+datetime="([^"]+)"', chunk)
            if time_match:
                try:
                    dt_str = time_match.group(1).replace('Z', '+00:00')
                    post_dt = datetime.datetime.fromisoformat(dt_str)
                    if (now - post_dt).total_seconds() > 24 * 3600:
                        seen_set.add(post_id)
                        continue
                except Exception:
                    pass

            text_match = re.search(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', chunk, re.DOTALL)
            raw_text = clean_html_text(text_match.group(1)) if text_match else ""

            if not raw_text or len(raw_text) < 25:
                seen_set.add(post_id)
                continue

            if is_strict_ad(raw_text):
                logger.info(f"Пропуск явної реклами у {post_id}")
                seen_set.add(post_id)
                continue

            if is_digest_post(raw_text):
                logger.info(f"Пропуск чужого дайджесту/підсумку дня у {post_id}")
                seen_set.add(post_id)
                continue

            if is_air_tracking_alert(raw_text):
                logger.info(f"Пропуск радарного моніторингу цілей у {post_id}")
                seen_set.add(post_id)
                continue

            photo_url = None
            photo_match = re.search(r'tgme_widget_message_photo_wrap[^"]*"[^>]*style="[^"]*background-image:url\([\'"]?(.*?)[\'"]?\)', chunk)
            if photo_match:
                photo_url = photo_match.group(1)

            video_url = None
            video_match = re.search(r'<video[^>]+src="([^"]+)"', chunk)
            if video_match:
                video_url = video_match.group(1)

            results.append({
                "post_id": post_id,
                "text": raw_text,
                "photo_url": photo_url,
                "video_url": video_url
            })

        return results

    except Exception as e:
        logger.error(f"Помилка скрапінгу {channel}: {e}")
        return []

# ---------------------------------------------------------
# ПУБЛІКАЦІЯ В TELEGRAM КАНАЛ
# ---------------------------------------------------------
def markdown_to_telegram_html(text: str) -> str:
    raw = text.strip()
    if not raw:
        return ""

    parts = raw.split("\n", 1)
    header = parts[0].strip()
    body = parts[1].strip() if len(parts) > 1 else ""

    if not (header.startswith("**") and header.endswith("**")) and not (header.startswith("<b>") and header.endswith("</b>")):
        m = re.match(r"^([‼️❗️⚡️🚨🔴👉⏩➡️▶️🇪🇺🇺🇦🇷🇺🇺🇸🇬🇧\s]+)(.*)$", header)
        if m and m.group(2).strip():
            header = f"{m.group(1).strip()} **{m.group(2).strip()}**"
        else:
            header = f"**{header}**"

    full_text = header + ("\n\n" + body if body else "")

    t = full_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    t = re.sub(r"\*\*(.*?)\*\*", r"<b>\1</b>", t)
    t = re.sub(r"\*(.*?)\*", r"<i>\1</i>", t)
    return t

def post_to_telegram(text: str, photo_url: str = None, video_url: str = None) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHANNEL_ID:
        logger.error("TELEGRAM_BOT_TOKEN або TELEGRAM_CHANNEL_ID не задано!")
        return False

    formatted_text = markdown_to_telegram_html(text)
    signature = '\n\n👉 <a href="https://t.me/kropivnit">NEWS УКРАЇНА 365 | Підписатися</a>'
    
    caption = formatted_text + signature
    if len(caption) > 1020:
        max_body_len = 1020 - len(signature)
        formatted_text = formatted_text[:max_body_len].rstrip()
        caption = formatted_text + signature

    base_api = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

    # 1. Відео
    if video_url:
        try:
            v_resp = requests.get(video_url, timeout=20, stream=True)
            if v_resp.status_code == 200:
                with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tf:
                    for chunk in v_resp.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            tf.write(chunk)
                    tmp_path = tf.name

                try:
                    with open(tmp_path, "rb") as vf:
                        res = requests.post(
                            f"{base_api}/sendVideo",
                            data={
                                "chat_id": TELEGRAM_CHANNEL_ID,
                                "caption": caption,
                                "parse_mode": "HTML"
                            },
                            files={"video": vf},
                            timeout=60
                        )
                        if res.status_code == 200:
                            logger.info("Відео успішно опубліковано")
                            return True
                finally:
                    if os.path.exists(tmp_path):
                        os.remove(tmp_path)
        except Exception as e:
            logger.error(f"Помилка відео: {e}")

    # 2. Фото
    if photo_url:
        try:
            p_resp = requests.get(photo_url, timeout=15)
            if p_resp.status_code == 200:
                with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tf:
                    tf.write(p_resp.content)
                    tmp_path = tf.name

                try:
                    with open(tmp_path, "rb") as pf:
                        res = requests.post(
                            f"{base_api}/sendPhoto",
                            data={
                                "chat_id": TELEGRAM_CHANNEL_ID,
                                "caption": caption,
                                "parse_mode": "HTML"
                            },
                            files={"photo": pf},
                            timeout=30
                        )
                        if res.status_code == 200:
                            logger.info("Фото успішно опубліковано")
                            return True
                finally:
                    if os.path.exists(tmp_path):
                        os.remove(tmp_path)
        except Exception as e:
            logger.error(f"Помилка фото: {e}")

    # 3. Текст
    try:
        full_text = formatted_text + signature
        res = requests.post(
            f"{base_api}/sendMessage",
            json={
                "chat_id": TELEGRAM_CHANNEL_ID,
                "text": full_text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
                "link_preview_options": {
                    "is_disabled": True
                }
            },
            timeout=20
        )
        if res.status_code == 200:
            logger.info("Текст успішно опубліковано")
            return True
        else:
            logger.error(f"sendMessage помилка: {res.text}")
            return False
    except Exception as e:
        logger.error(f"Виняток sendMessage: {e}")
        return False

def post_media_group_to_telegram(text: str, media_items: list) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHANNEL_ID or not media_items:
        return False

    signature = '\n\n👉 <a href="https://t.me/kropivnit">NEWS УКРАЇНА 365 | Підписатися</a>'
    if text and text.strip():
        formatted_text = markdown_to_telegram_html(text)
        caption = formatted_text + signature
        if len(caption) > 1020:
            max_body_len = 1020 - len(signature)
            formatted_text = formatted_text[:max_body_len].rstrip()
            caption = formatted_text + signature
    else:
        caption = '👉 <a href="https://t.me/kropivnit">NEWS УКРАЇНА 365 | Підписатися</a>'

    base_api = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

    all_ok = True
    for start in range(0, len(media_items), 10):
        batch = media_items[start:start + 10]
        media_payload = []
        for idx, item in enumerate(batch):
            entry = {
                "type": item["type"],
                "media": item["file_id"]
            }
            if start == 0 and idx == 0:
                entry["caption"] = caption
                entry["parse_mode"] = "HTML"
            media_payload.append(entry)

        try:
            res = requests.post(
                f"{base_api}/sendMediaGroup",
                json={
                    "chat_id": TELEGRAM_CHANNEL_ID,
                    "media": media_payload
                },
                timeout=30
            )
            if res.status_code == 200:
                logger.info(f"Альбом із {len(batch)} медіафайлів успішно опубліковано")
            else:
                logger.error(f"sendMediaGroup помилка: {res.text}")
                all_ok = False
        except Exception as e:
            logger.error(f"Виняток sendMediaGroup: {e}")
            all_ok = False

    return all_ok

media_group_buffer = {}
media_group_lock = threading.Lock()

def _process_and_publish_admin_post(chat_id, raw_text: str, photo_url: str = None, video_url: str = None, media_items: list = None):
    base_api = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

    if chat_id:
        requests.post(f"{base_api}/sendMessage", json={
            "chat_id": chat_id,
            "text": "⏳ Обробляю новину та готую публікацію..."
        })

    rewritten = ""
    if raw_text and raw_text.strip():
        rewritten = rewrite_news_for_admin(raw_text)
        if not rewritten:
            rewritten = strip_donor_artifacts(raw_text)

    if media_items and len(media_items) > 1:
        success = post_media_group_to_telegram(text=rewritten, media_items=media_items)
    else:
        success = post_to_telegram(
            text=rewritten,
            photo_url=photo_url,
            video_url=video_url
        )

    if success:
        first_line = rewritten.split('\n')[0].replace('*', '').strip() if rewritten else "(медіа без тексту)"
        if rewritten:
            global_published_titles.append(first_line)
            h = load_published_history()
            h.append({
                "headline": first_line,
                "stems": list(extract_keywords_stemmed(rewritten + " " + raw_text)),
                "timestamp": time.time()
            })
            save_published_history(h)
            if len(global_published_titles) > 30:
                global_published_titles.pop(0)

        if chat_id:
            requests.post(f"{base_api}/sendMessage", json={
                "chat_id": chat_id,
                "text": f"✅ <b>Новину опубліковано в каналі!</b>\n\n<b>Заголовок:</b> {first_line}",
                "parse_mode": "HTML"
            })
        logger.info(f"Новину від користувача опубліковано: {first_line}")
    else:
        if chat_id:
            requests.post(f"{base_api}/sendMessage", json={
                "chat_id": chat_id,
                "text": "❌ Помилка під час публікації. Перевірте статус бота."
            })

def _flush_media_group(media_group_id: str):
    time.sleep(2.2)
    with media_group_lock:
        group = media_group_buffer.pop(media_group_id, None)
    if not group:
        return

    chat_id = group["chat_id"]
    items = group["items"]
    texts = [t for t in group["texts"] if t.strip()]
    combined_text = "\n\n".join(texts).strip()

    first_photo = next((i.get("photo_url") for i in items if i["type"] == "photo"), None)
    first_video = next((i.get("video_url") for i in items if i["type"] == "video"), None)

    logger.info(f"Зібрано альбом {media_group_id}: {len(items)} медіа, текст: {len(combined_text)} символів")
    _process_and_publish_admin_post(
        chat_id=chat_id,
        raw_text=combined_text,
        photo_url=first_photo,
        video_url=first_video,
        media_items=items
    )

# ---------------------------------------------------------
# ОБРОБКА ПОВІДОМЛЕНЬ ВІД КОРИСТУВАЧА (В ПРИВАТНІ ПОВІДОМЛЕННЯ)
# ---------------------------------------------------------
def handle_incoming_update(update: dict):
    msg = update.get("message")
    if not msg:
        return

    chat_id = msg.get("chat", {}).get("id")
    raw_text = msg.get("text") or msg.get("caption") or ""

    if not raw_text and msg.get("forward_from_chat"):
        raw_text = msg.get("caption") or msg.get("text") or ""

    photo = msg.get("photo")
    video = msg.get("video")
    media_group_id = msg.get("media_group_id")

    photo_url = None
    video_url = None
    media_item = None

    base_api = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
    file_base = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}"

    if photo:
        file_id = photo[-1]["file_id"]
        media_item = {"type": "photo", "file_id": file_id}
        if not media_group_id:
            try:
                res = requests.get(f"{base_api}/getFile?file_id={file_id}", timeout=10).json()
                if res.get("ok"):
                    photo_url = f"{file_base}/{res['result']['file_path']}"
                    media_item["photo_url"] = photo_url
            except Exception as e:
                logger.error(f"Помилка отримання фото від користувача: {e}")

    if video:
        file_id = video["file_id"]
        media_item = {"type": "video", "file_id": file_id}
        if not media_group_id:
            try:
                res = requests.get(f"{base_api}/getFile?file_id={file_id}", timeout=10).json()
                if res.get("ok"):
                    video_url = f"{file_base}/{res['result']['file_path']}"
                    media_item["video_url"] = video_url
            except Exception as e:
                logger.error(f"Помилка отримання відео від користувача: {e}")

    if not raw_text and not media_item:
        return

    if raw_text.strip() == "/start":
        if chat_id:
            requests.post(f"{base_api}/sendMessage", json={
                "chat_id": chat_id,
                "text": "👋 Привіт! Надішли мені новину (текст, фото, відео або альбом), і я відредагую її та опублікую в каналі NEWS УКРАЇНА 365."
            })
        return

    if media_group_id and media_item:
        is_first = False
        with media_group_lock:
            if media_group_id not in media_group_buffer:
                media_group_buffer[media_group_id] = {
                    "chat_id": chat_id,
                    "items": [],
                    "texts": []
                }
                is_first = True
            media_group_buffer[media_group_id]["items"].append(media_item)
            if raw_text.strip():
                media_group_buffer[media_group_id]["texts"].append(raw_text.strip())

        if is_first:
            threading.Thread(target=_flush_media_group, args=(media_group_id,), daemon=True).start()
        return

    logger.info(f"Отримано новину від користувача (chat_id: {chat_id}): {raw_text[:50]}...")
    _process_and_publish_admin_post(
        chat_id=chat_id,
        raw_text=raw_text,
        photo_url=photo_url,
        video_url=video_url
    )

# ---------------------------------------------------------
# TELEGRAM POLLING LOOP (ЯКЩО ВЕБХУК НЕ НАЛАШТОВАНО)
# ---------------------------------------------------------
def telegram_polling_loop():
    logger.info("Запуск Telegram Polling для прийому ручних новин...")
    offset = 0
    base_api = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
    while True:
        try:
            resp = requests.get(f"{base_api}/getUpdates?offset={offset}&timeout=20", timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("ok"):
                    for update in data.get("result", []):
                        offset = update["update_id"] + 1
                        threading.Thread(target=handle_incoming_update, args=(update,), daemon=True).start()
            elif resp.status_code == 409:
                time.sleep(60)
            else:
                time.sleep(5)
        except Exception:
            time.sleep(5)

# ---------------------------------------------------------
# ГОЛОВНИЙ ЦИКЛ БОТА ІЗ РОЗУМНИМ КОНТРОЛЕМ
# ---------------------------------------------------------
def bot_worker_loop():
    global global_published_titles
    logger.info("Запуск бота...")
    seen_set = set()
    last_regular_post_time = 0

    # 1. ЗАХИСТ ВІД ПОВТОРІВ ПІСЛЯ РЕСТАРТУ НА RENDER:
    global_published_titles = fetch_own_channel_history(TELEGRAM_CHANNEL_ID)
    published_history = load_published_history()
    for t in global_published_titles:
        st = extract_keywords_stemmed(t)
        if st and not any(h.get("headline") == t for h in published_history):
            published_history.append({"headline": t, "stems": list(st), "timestamp": time.time()})
    save_published_history(published_history)

    # 2. ЗАХИСТ ВІД СТАРТОВОЇ ЛАВИНИ:
    logger.info(f"Ініціалізація {len(DONOR_CHANNELS)} донорів: фіксуємо старий беклог, щоб не спамити...")
    for ch in DONOR_CHANNELS:
        try:
            url = f"https://t.me/s/{ch}"
            resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
            if resp.status_code == 200:
                post_ids = re.findall(r'data-post="([^"]+)"', resp.text)
                for pid in post_ids:
                    seen_set.add(pid)
        except Exception:
            pass
        time.sleep(0.4)
    logger.info(f"Ініціалізацію завершено. Зафіксовано {len(seen_set)} існуючих постів. Бот чекає свіжих новин!")

    # 3. Реєстрація вебхука або запуск polling для прийому новин від адміна в ПП
    if RENDER_EXTERNAL_URL:
        webhook_url = f"{RENDER_EXTERNAL_URL.rstrip('/')}/webhook"
        try:
            w_res = requests.get(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/setWebhook?url={webhook_url}", timeout=10)
            logger.info(f"Встановлення Webhook на {webhook_url}: {w_res.text}")
        except Exception as e:
            logger.error(f"Помилка setWebhook: {e}")
    else:
        try:
            requests.get(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/deleteWebhook", timeout=10)
        except Exception:
            pass
        threading.Thread(target=telegram_polling_loop, daemon=True).start()

    while True:
        try:
            now = time.time()

            recent_regular_post_timestamps[:] = [t for t in recent_regular_post_timestamps if now - t < 3600]
            regular_limit_reached = len(recent_regular_post_timestamps) >= MAX_REGULAR_PER_HOUR
            posted_in_this_cycle = False

            for channel in DONOR_CHANNELS:
                posts = scrape_channel(channel, seen_set)

                for item in posts:
                    post_id = item["post_id"]
                    raw_text = item["text"]

                    seen_set.add(post_id)

                    is_breaking = is_breaking_news(raw_text)

                    if not is_breaking:
                        if posted_in_this_cycle:
                            continue

                        if regular_limit_reached:
                            logger.info(f"Годинний ліміт звичайних новин ({MAX_REGULAR_PER_HOUR}) вичерпано. Пропуск другорядної новини.")
                            continue

                        time_since_last = time.time() - last_regular_post_time
                        if time_since_last < REGULAR_COOLDOWN:
                            continue

                    # АЛГОРИТМІЧНИЙ ЗАХИСТ ВІД ПОВТОРІВ (ДО ЗАПИТУ В GROQ):
                    if is_duplicate_news(raw_text, published_history):
                        logger.info(f"Алгоритмічний фільтр відхилив повтор новини: {raw_text[:60]}...")
                        seen_set.add(post_id)
                        continue

                    logger.info(f"Обробка поста {post_id} (Терміново: {is_breaking})...")
                    rewritten = rewrite_news(raw_text, global_published_titles)

                    if not rewritten:
                        continue

                    success = post_to_telegram(
                        text=rewritten,
                        photo_url=item.get("photo_url"),
                        video_url=item.get("video_url")
                    )

                    if success:
                        current_t = time.time()
                        first_line = rewritten.split('\n')[0].replace('*', '').strip()
                        global_published_titles.append(first_line)
                        published_history.append({
                            "headline": first_line,
                            "stems": list(extract_keywords_stemmed(rewritten + " " + raw_text)),
                            "timestamp": current_t
                        })
                        save_published_history(published_history)

                        if len(global_published_titles) > 30:
                            global_published_titles.pop(0)

                        if is_breaking:
                            logger.info(f"🚨 МЕГА ВАЖЛИВА НОВИНА ОПУБЛІКОВАНА НЕГАЙНО: {first_line}")
                        else:
                            last_regular_post_time = current_t
                            recent_regular_post_timestamps.append(current_t)
                            posted_in_this_cycle = True
                            logger.info(f"📰 Звичайна новина опублікована: {first_line}. Пауза 3 хв.")
                            break

                time.sleep(0.8)

            time.sleep(25)

        except Exception as e:
            logger.error(f"Помилка в головному циклі: {e}", exc_info=True)
            time.sleep(20)

# ---------------------------------------------------------
# ВЕБ-СЕРВЕР ДЛЯ RENDER (HEALTH CHECK ТА WEBHOOK ДЛЯ ПОВІДОМЛЕНЬ)
# ---------------------------------------------------------
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "application/json")
        self.end_headers()
        response = json.dumps({
            "status": "healthy",
            "service": "NEWS УКРАЇНА 365 Bot",
            "donor_channels_count": len(DONOR_CHANNELS),
            "regular_posts_last_hour": len(recent_regular_post_timestamps)
        })
        self.wfile.write(response.encode("utf-8"))

    def do_POST(self):
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

        try:
            update = json.loads(post_data.decode("utf-8"))
            threading.Thread(target=handle_incoming_update, args=(update,), daemon=True).start()
        except Exception as e:
            logger.error(f"Помилка обробки вебхука від користувача: {e}")

    def log_message(self, format, *args):
        pass

def start_health_server():
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    logger.info(f"Health check та Webhook сервер слухає порт {PORT}")
    server.serve_forever()

def keep_alive_pinger():
    if not RENDER_EXTERNAL_URL:
        return
    logger.info(f"Запуск keep-alive пінгера для: {RENDER_EXTERNAL_URL}")
    while True:
        try:
            time.sleep(600)
            requests.get(RENDER_EXTERNAL_URL, timeout=10)
        except Exception:
            pass

# ---------------------------------------------------------
# ТОЧКА ВХОДУ
# ---------------------------------------------------------
if __name__ == "__main__":
    server_thread = threading.Thread(target=start_health_server, daemon=True)
    server_thread.start()

    pinger_thread = threading.Thread(target=keep_alive_pinger, daemon=True)
    pinger_thread.start()

    bot_worker_loop()
