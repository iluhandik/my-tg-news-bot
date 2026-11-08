# --- AI REWRITE & JOURNALISTIC FACT GUARD ---
def rewrite_news_with_groq(text, model_name):
    if not GROQ_API_KEY or not text or len(text.strip()) < 30:
        return None

    lower_raw = text.lower()

    # 1. ФІЛЬТР НЕКРОЛОГІВ МИНУЛИХ РОКІВ ТА ХВИЛИН МОВЧАННЯ
    memorial_words = [
        "хвилина мовчання", "пам'яті геро", "пам'яті загибл", "вічна пам'ять",
        "загинув у 2022", "загинув у 2023", "загинув 2022", "загинув 2023",
        "загинув 2024", "загинула в 2022", "загинула в 2023", "поховали на кладовищі"
    ]
    if any(m in lower_raw for m in memorial_words):
        logger.info("Skipping past years memorial/obituary.")
        return "SKIP"

    # 2. ФІЛЬТР СИТУАТИВНОГО РАДАРНОГО СПАМУ (ТРИВОГИ НА 5 ХВИЛИН)
    radar_words = [
        "летять на захід", "летять на схід", "курсом на", "вектор руху",
        "повітряна тривога в області", "загроза балістики", "ракета в напрямку",
        "шахеди повз", "бпла курсом", "чисто по областях", "відбій тривоги"
    ]
    # Залишаємо тільки підсумкові зведення (де є слова 'збито' або 'наслідки')
    if any(r in lower_raw for r in radar_words) and not any(k in lower_raw for k in ["збито", "відбито", "наслідки", "знищено"]):
        logger.info("Skipping transient flight radar alert.")
        return "SKIP"

    # 3. ФІЛЬТР МІСЬКОЇ КОМУНАЛКИ
    local_fluff_keywords = [
        "дтек ввів", "дтек запровадив", "екстрені відключення", "стабілізаційні відключення",
        "графіки відключень", "ремонт водопроводу", "ремонт мосту", "перекрили рух",
        "на південному мосту", "на північному мосту"
    ]
    if any(k in lower_raw for k in local_fluff_keywords) and "масований обстріл" not in lower_raw:
        logger.info("Skipping local municipal/utility fluff.")
        return "SKIP"

    system_prompt = (
        "ТИ — ГОЛОВНИЙ РЕДАКТОР УКРАЇНСЬКОГО ТЕЛЕГРАМ-КАНАЛУ «NEWS УКРАЇНА 365» (@kropivnit).\n\n"
        "СУВОРІ РЕДАКЦІЙНІ ПРАВИЛА:\n"
        "1. ПРАВДА ПРО УДАРИ ПО РФ: Якщо ЗСУ або дрони уразили ціль у РФ — новина ТІЛЬКИ про успішний удар ЗСУ! Заборонено цитувати версії ворога як факт.\n"
        "2. ТОЧНІСТЬ МІСЦЯ: Місто в заголовку зобов'язане збігатися з місцем безпосередньої події.\n"
        "3. НЕКРОЛОГИ МИНУЛИХ РОКІВ І РАДАРНИЙ СПАМ: Якщо новина — це стара згадка про загибель воїна у 2022-2024 рр. або хвилинне попередження про проліт дрона — пиши SKIP.\n"
        "4. КАТЕГОРИЧНО ЗАБОРОНЕНО писати коментарі, примітки чи від першої особи.\n"
        "5. Чиста українська: «ДСНС» (не «ГСЧС»), «вирва» (не «воронка»), «величезна» (не «огромна»).\n\n"
        "ФОРМАТ ПОСТА:\n"
        "⚡ <b>Короткий влучний заголовок</b>\n\n"
        "1-2 фактичні абзаци. Ключові дані виділяй <b>жирним</b>.\n"
        "Цитати: «Текст», — <b>Прізвище</b>.\n\n"
        "ВІДПОВІДЬ: ТІЛЬКИ ГОТОВИЙ ПОСТ АБО СЛОВО SKIP."
    )

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Ось вихідна новина:\n\n{text}"}
        ],
        "temperature": 0.15,
        "max_tokens": 800
    }

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=25)
        if resp.status_code != 200:
            return None
        raw_out = resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.error(f"Groq call exception: {e}")
        return None

    raw_out = re.sub(r'<think>.*?</think>', '', raw_out, flags=re.DOTALL).strip()

    if raw_out.upper() == "SKIP" or "SKIP" in raw_out[:10]:
        return "SKIP"

    out = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', raw_out)
    out = re.sub(r'__([\s\S]*?)__', r'<i>\1</i>', out)
    out = out.replace("<blockquote>", "").replace("</blockquote>", "")

    valid_endings = ('.', '!', '?', '»', '"', '</b>', '</i>', ')')
    if not out.rstrip().endswith(valid_endings):
        last_punct = max(out.rfind('.'), out.rfind('!'), out.rfind('?'), out.rfind('»'))
        if last_punct > 120:
            out = out[:last_punct+1].strip()
        else:
            return None

    ru_fixes = {
        r'\bГСЧС\b': 'ДСНС',
        r'\bгсчс\b': 'ДСНС',
        r'\bогромн(а|у|е|і|их|им|ому)\b': r'величезн\1',
        r'\bворонку\b': 'вирву',
        r'\bворонка\b': 'вирва',
        r'\bворонки\b': 'вирви',
        r'\bукраїнські зсу\b': 'ЗСУ',
        r'\bтолп[а-я]*': 'натовп',
        r'\bпозор[а-я]*': 'ганьба',
        r'\bу даний момент\b': 'наразі',
        r'\bв даний час\b': 'наразі',
        r'\bВСУ\b': 'ЗСУ'
    }
    for r_pattern, replacement in ru_fixes.items():
        out = re.sub(r_pattern, replacement, out, flags=re.IGNORECASE)

    return out
