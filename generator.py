"""
generator.py
Модуль генерації різноформатного навчального контенту з німецької мови через Google Gemini API.

Підтримує 3 формати публікацій на день:
1. Ранковий пост: «Слово дня» за щотижневим рубрикатором + картка + аудіо
2. Денний пост: «Жива розмовна фраза дня» + міні-діалог у реальних життєвих ситуаціях + картка + аудіо
3. Вечірній пост: «Інтерактивний Quiz-тест» (нативне опитування Telegram poll) з варіантами та поясненням
"""

import os
import json
import logging
from datetime import datetime
import google.generativeai as genai

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Щотижневий рубрикатор для ранкових слів дня (0 = Понеділок, 6 = Неділя)
WEEKLY_RUBRICS = {
    0: {
        "title": "Побут та щоденне життя (Alltag)",
        "emoji": "🛒",
        "description": "Повсякденні побутові ситуації в Німеччині: супермаркети, оренда житла, транспорт, аптека, лікарі.",
        "default_level": "A1-A2"
    },
    1: {
        "title": "Граматика без нудоти (Grammatik)",
        "emoji": "🧩",
        "description": "Практична граматика: артиклі (der/die/das), відмінки Akkusativ/Dativ, порядок слів, модальні дієслова.",
        "default_level": "A2-B1"
    },
    2: {
        "title": "Робота та бюрократія (Bürokratie & Beruf)",
        "emoji": "💼",
        "description": "Jobcenter, Bürgeramt, Krankenkasse, написання резюме (Lebenslauf), пошук роботи та листування з установами.",
        "default_level": "A2-B2"
    },
    3: {
        "title": "Розмовна мова та сленг (Umgangssprache)",
        "emoji": "🗣️",
        "description": "Жива німецька мова: популярні вирази носіїв мови, скорочення, реакції в розмові.",
        "default_level": "A2-B2"
    },
    4: {
        "title": "Типові помилки українців (Typische Fehler)",
        "emoji": "⚠️",
        "description": "Помилки через кальку з української, «хибні друзі перекладача» (bekommen ≠ ставати), різниця kennen vs wissen.",
        "default_level": "A1-B1"
    },
    5: {
        "title": "Ідіоми та менталітет (Redewendungen & Kultur)",
        "emoji": "🇩🇪",
        "description": "Крилаті фрази, ідіоми, звичаї та правила життя в Німеччині (Ruhezeit, Mülltrennung, культура спілкування).",
        "default_level": "A2-B2"
    },
    6: {
        "title": "Практика та міні-тест (Quiz & Wiederholung)",
        "emoji": "🎯",
        "description": "Цікаве слово або корисна фраза з міні-тестом для перевірки знань.",
        "default_level": "A2-B1"
    }
}

# Резервні дані для ранкових постів
FALLBACK_WORDS = {
    0: {
        "word": "das Pfand, -er",
        "article": "das",
        "clean_word": "das Pfand",
        "transcription": "[дас Пфанд]",
        "translation": "застава за тару / пляшки",
        "level": "A1-A2",
        "rubric_name": "Побут та щоденне життя (Alltag)",
        "rubric_emoji": "🛒",
        "card_example_de": "Vergiss nicht, das Pfand abzugeben!",
        "card_example_ua": "Не забудь здати пляшки під заставу!",
        "explanation": "У Німеччині майже на всі пластикові та скляні пляшки діє обов'язкова застава (від 8 до 25 центів). Їх здають у спеціальні автомати (Pfandautomat) у супермаркетах.",
        "examples": [
            {"de": "Wie viel Pfand ist auf dieser Flasche?", "ua": "Скільки застави за цю пляшку?"},
            {"de": "Ich habe die Pfandflaschen zurückgebracht.", "ua": "Я здав заставні пляшки."}
        ],
        "pro_tip": "💡 <b>Лайфхак:</b> Шукайте на етикетці значок пляшки зі стрілкою — це означає повернення 25 центів за пляшку!"
    }
}

# Резервні дані для денних розмовних фраз
FALLBACK_PHRASE = {
    "phrase": "Stimmt so!",
    "transcription": "[штімт зо!]",
    "translation": "Решти не треба! / Залишіть собі!",
    "situation": "У кафе, пекарні, ресторані чи таксі під час оплати",
    "card_example_de": "Hier sind zehn Euro. Stimmt so!",
    "card_example_ua": "Ось десять євро. Решти не треба!",
    "explanation": "Найпопулярніша фраза в Німеччині, коли ви хочете залишити решту як чайові (Trinkgeld). Замість того, щоб чекати здачу, ви просто кажете 'Stimmt so!'.",
    "dialogue": [
        {"speaker": "Офіціант", "de": "Das macht bitte 8 Euro 50.", "ua": "З вас 8 євро 50 центів, будь ласка."},
        {"speaker": "Ви", "de": "Hier sind 10 Euro. Stimmt so!", "ua": "Ось 10 євро. Решти не треба!"}
    ],
    "pro_tip": "💡 <b>Культурний нюанс:</b> У Німеччині чайові зазвичай складають 5–10% і їх оголошують одразу під час оплати, а не залишають мовчки на столі."
}

# Резервні дані для вечірніх тестів-опитувань
FALLBACK_QUIZ = {
    "question": "🇩🇪 Як німецькою сказати офіціанту 'Решти не треба'?",
    "options": [
        "A) Behalten Sie das Geld",
        "B) Stimmt so!",
        "C) Ich brauche kein Geld",
        "D) Kein Problem"
    ],
    "correct_option_id": 1,
    "explanation": "Вираз 'Stimmt so!' — найпоширеніший спосіб залишити чайові офіціанту чи водієві в Німеччині."
}


def load_history(history_file_path: str = "history.json") -> dict:
    """Завантажує історію використаних слів, фраз та квізів."""
    if not os.path.exists(history_file_path):
        return {"used_words": [], "used_phrases": [], "used_quizzes": [], "history": []}
    
    try:
        with open(history_file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, dict):
                return {"used_words": [], "used_phrases": [], "used_quizzes": [], "history": []}
            data.setdefault("used_words", [])
            data.setdefault("used_phrases", [])
            data.setdefault("used_quizzes", [])
            data.setdefault("history", [])
            return data
    except Exception as e:
        logger.warning(f"Не вдалося прочитати {history_file_path}: {e}")
        return {"used_words": [], "used_phrases": [], "used_quizzes": [], "history": []}


def get_gemini_model(api_key: str = None):
    """Ініціалізує та повертає актуальну модель Gemini (gemini-3.8-flash)."""
    if not api_key:
        api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return None
    genai.configure(api_key=api_key)
    # Пріоритетна актуальна модель
    return genai.GenerativeModel(
        model_name="gemini-3.8-flash",
        generation_config={
            "temperature": 0.7,
            "response_mime_type": "application/json",
        }
    )


def clean_json_response(text: str) -> dict:
    """Очищує та парсить відповідь моделі у JSON."""
    raw = text.strip()
    if raw.startswith("```json"):
        raw = raw[7:]
    if raw.startswith("```"):
        raw = raw[3:]
    if raw.endswith("```"):
        raw = raw[:-3]
    return json.loads(raw.strip())


# ==========================================================
# 1. РАНКОВИЙ ПОСТ: СЛОВО ДНЯ
# ==========================================================
def format_morning_post(data: dict) -> str:
    """Форматує ранковий пост для Telegram."""
    lines = [
        f"{data.get('rubric_emoji', '🇩🇪')} <b>Німецька для українців | {data.get('rubric_name', 'Слово дня')}</b>",
        f"🏷️ <i>Рівень: {data.get('level', 'A1-B2')}</i>",
        "",
        f"⭐ <b>{data.get('word', '')}</b> <code>{data.get('transcription', '')}</code>",
        f"👉 <b>Переклад:</b> {data.get('translation', '')}",
        "",
        f"📖 <b>Пояснення:</b>",
        f"{data.get('explanation', '')}",
        "",
        "💬 <b>Приклади вживання:</b>"
    ]
    for ex in data.get("examples", []):
        lines.append(f"🔹 <i>{ex.get('de', '')}</i>")
        lines.append(f"   ↳ {ex.get('ua', '')}")
    if data.get("pro_tip"):
        lines.append("")
        lines.append(data.get("pro_tip"))
    lines.append("")
    lines.append("🎧 <i>Прослухайте правильну вимову у прикріпленому аудіо вище!</i>")
    lines.append("")
    lines.append("#НімецькаМова #СловоДня #DeutschLernen #Німеччина")
    return "\n".join(lines)


def generate_morning_content(history_file_path: str = "history.json", api_key: str = None) -> dict:
    """Генерує ранковий пост (Слово дня)."""
    weekday = datetime.now().weekday()
    rubric = WEEKLY_RUBRICS.get(weekday, WEEKLY_RUBRICS[0])
    history = load_history(history_file_path)
    used_words = history.get("used_words", [])

    model = get_gemini_model(api_key)
    if not model:
        fallback = FALLBACK_WORDS.get(weekday, FALLBACK_WORDS[0]).copy()
        fallback["telegram_post"] = format_morning_post(fallback)
        fallback["audio_text"] = f"{fallback.get('clean_word')}. {fallback.get('card_example_de')}"
        fallback["post_type"] = "morning"
        return fallback

    recent_words_str = ", ".join(used_words[-150:]) if used_words else "немає"
    prompt = f"""
Ти — викладач німецької для українців у Німеччині. Створи сьогоднішній пост «Слово дня».
Рубрика: {rubric['title']} ({rubric['description']}).
Рівень: {rubric['default_level']}.
СУВОРО ЗАБОРОНЕНО використовувати ці слова: {recent_words_str}

Поверни валідний JSON:
{{
  "word": "Слово з артиклем та множиною",
  "article": "der / die / das або пусто",
  "clean_word": "Чисте слово з артиклем для озвучки",
  "transcription": "[транскрипція українськими літерами з наголосом]",
  "translation": "переклад українською",
  "level": "{rubric['default_level']}",
  "rubric_name": "{rubric['title']}",
  "rubric_emoji": "{rubric['emoji']}",
  "card_example_de": "Коротке речення для графічної картки (макс 10-12 слів)",
  "card_example_ua": "Український переклад цього речення",
  "explanation": "Дружнє та практичне пояснення українською мовою (2-3 речення).",
  "examples": [
    {{"de": "Приклад 1", "ua": "Переклад 1"}},
    {{"de": "Приклад 2", "ua": "Переклад 2"}}
  ],
  "pro_tip": "💡 <b>Лайфхак:</b> короткий цікавий факт або порада для життя в Німеччині."
}}
"""
    try:
        resp = model.generate_content(prompt)
        data = clean_json_response(resp.text)
        data.setdefault("rubric_name", rubric["title"])
        data.setdefault("rubric_emoji", rubric["emoji"])
        if not data.get("clean_word"):
            data["clean_word"] = data.get("word", "")
        data["telegram_post"] = format_morning_post(data)
        data["audio_text"] = f"{data.get('clean_word')}. {data.get('card_example_de', '')}".strip()
        data["post_type"] = "morning"
        return data
    except Exception as e:
        logger.error(f"Помилка генерації ранкового слова: {e}")
        fallback = FALLBACK_WORDS.get(weekday, FALLBACK_WORDS[0]).copy()
        fallback["telegram_post"] = format_morning_post(fallback)
        fallback["audio_text"] = f"{fallback.get('clean_word')}. {fallback.get('card_example_de')}"
        fallback["post_type"] = "morning"
        return fallback


# ==========================================================
# 2. ДЕННИЙ ПОСТ: ЖИВА РОЗМОВНА ФРАЗА ТА ДІАЛОГ
# ==========================================================
def format_afternoon_post(data: dict) -> str:
    """Форматує денний пост (розмовна фраза) для Telegram."""
    lines = [
        "🗣️ <b>Німецька щодня | Жива розмовна фраза</b>",
        f"📍 <i>Ситуація: {data.get('situation', 'Щоденне спілкування')}</i>",
        "",
        f"🔥 <b>»{data.get('phrase', '')}«</b> <code>{data.get('transcription', '')}</code>",
        f"👉 <b>Значення:</b> {data.get('translation', '')}",
        "",
        f"💡 <b>Коли та як вживати:</b>",
        f"{data.get('explanation', '')}",
        "",
        "💬 <b>Живий міні-діалог:</b>"
    ]
    for d in data.get("dialogue", []):
        lines.append(f"👤 <b>{d.get('speaker', '')}:</b> <i>{d.get('de', '')}</i>")
        lines.append(f"   ↳ {d.get('ua', '')}")
    if data.get("pro_tip"):
        lines.append("")
        lines.append(data.get("pro_tip"))
    lines.append("")
    lines.append("🎧 <i>Слухайте вимову фрази та діалогу в аудіо вище!</i>")
    lines.append("")
    lines.append("#РозмовнаНімецька #ФразаДня #НімецькийСленг #Німеччина")
    return "\n".join(lines)


def generate_afternoon_content(history_file_path: str = "history.json", api_key: str = None) -> dict:
    """Генерує денний розмовний пост (фраза дня + міні-діалог)."""
    history = load_history(history_file_path)
    used_phrases = history.get("used_phrases", [])

    model = get_gemini_model(api_key)
    if not model:
        fallback = FALLBACK_PHRASE.copy()
        fallback["telegram_post"] = format_afternoon_post(fallback)
        fallback["audio_text"] = f"{fallback.get('phrase')}. {fallback.get('card_example_de')}"
        fallback["post_type"] = "afternoon"
        return fallback

    recent_phrases_str = ", ".join(used_phrases[-100:]) if used_phrases else "немає"
    prompt = f"""
Ти — сучасний викладач німецької. Обери одну дуже популярну, живу розмовну німецьку фразу або сталий вираз, який німці постійно вживають у супермаркеті, транспорті, кав'ярні, на роботі або при знайомстві (рівні A1-B2).
СУВОРО ЗАБОРОНЕНО використовувати ці раніше опубліковані фрази: {recent_phrases_str}

Поверни валідний JSON:
{{
  "phrase": "Німецька розмовна фраза (наприклад: Stimmt so! або Mach dir keine Sorgen!)",
  "transcription": "[транскрипція українськими літерами з наголосом]",
  "translation": "живий точний український переклад",
  "situation": "де саме це говорять (наприклад: У супермаркеті на касі / У кафе)",
  "card_example_de": "Короткий живий приклад для графічної картки (макс 10 слів)",
  "card_example_ua": "Український переклад цього речення",
  "explanation": "Чому так кажуть, як це сприймають німці та які є нюанси (2-3 речення).",
  "dialogue": [
    {{"speaker": "Співрозмовник", "de": "Коротка репліка 1 німецькою", "ua": "Переклад репліки 1"}},
    {{"speaker": "Ви", "de": "Коротка репліка 2 з цією фразою", "ua": "Переклад репліки 2"}}
  ],
  "pro_tip": "💡 <b>Порада:</b> корисний нюанс про інтонацію або культурний контекст."
}}
"""
    try:
        resp = model.generate_content(prompt)
        data = clean_json_response(resp.text)
        data["rubric_name"] = "Розмовна фраза дня"
        data["rubric_emoji"] = "🗣️"
        data["word"] = data.get("phrase", "")
        data["clean_word"] = data.get("phrase", "")
        data["level"] = "Розмовна"
        data["telegram_post"] = format_afternoon_post(data)
        
        # Аудіо: фраза + приклад
        data["audio_text"] = f"{data.get('phrase')}. {data.get('card_example_de', '')}".strip()
        data["post_type"] = "afternoon"
        return data
    except Exception as e:
        logger.error(f"Помилка генерації розмовної фрази: {e}")
        fallback = FALLBACK_PHRASE.copy()
        fallback["telegram_post"] = format_afternoon_post(fallback)
        fallback["audio_text"] = f"{fallback.get('phrase')}. {fallback.get('card_example_de')}"
        fallback["post_type"] = "afternoon"
        return fallback


# ==========================================================
# 3. ВЕЧІРНІЙ ПОСТ: ІНТЕРАКТИВНИЙ QUIZ-ТЕСТ (POLL)
# ==========================================================
def generate_evening_quiz(history_file_path: str = "history.json", api_key: str = None) -> dict:
    """
    Генерує вечірню інтерактивну вікторину для відправки через Telegram sendPoll.
    Telegram Poll має суворі обмеження:
    - question: <= 300 символів
    - options: 2-10 варіантів, кожен <= 100 символів
    - explanation: <= 200 символів!
    """
    history = load_history(history_file_path)
    used_quizzes = history.get("used_quizzes", [])

    model = get_gemini_model(api_key)
    if not model:
        fallback = FALLBACK_QUIZ.copy()
        fallback["post_type"] = "evening"
        return fallback

    recent_topics_str = ", ".join(used_quizzes[-100:]) if used_quizzes else "немає"
    prompt = f"""
Створи захопливе інтерактивне тестове питання (Quiz) з німецької мови для українців (рівні A1-B2).
Це може бути граматика (артиклі der/die/das, Akkusativ/Dativ, прийменники), розмовна фраза або типова помилка.
СУВОРО УНИКАЙ тем: {recent_topics_str}

СУВОРІ ОБМЕЖЕННЯ TELEGRAM POLL:
1. question: довжина до 250 символів. Обов'язково постав емодзі 🇩🇪 на початку.
2. options: рівно 3 або 4 варіанти відповіді (кожен до 80 символів). Варіанти мають бути реалістичними, без префіксів A), B), C).
3. correct_option_id: індекс правильної відповіді (число від 0 до 3).
4. explanation: ДУЖЕ ВАЖЛИВО! Довжина суворо до 190 символів! Чітке пояснення українською мовою, чому саме ця відповідь правильна.

Поверни валідний JSON:
{{
  "topic": "Коротка назва теми (до 5 слів)",
  "question": "🇩🇪 Питання німецькою або про німецьку мову?",
  "options": [
    "Варіант 1",
    "Варіант 2",
    "Варіант 3",
    "Варіант 4"
  ],
  "correct_option_id": 1,
  "explanation": "Чітке коротке пояснення правил (максимум 190 символів!)."
}}
"""
    try:
        resp = model.generate_content(prompt)
        data = clean_json_response(resp.text)
        
        # Перевірка обмежень Telegram Poll
        q = data.get("question", "🇩🇪 Оберіть правильний варіант:")[:290]
        options = [str(opt)[:95] for opt in data.get("options", [])[:4]]
        if len(options) < 2:
            options = ["Варіант 1", "Варіант 2"]
        correct_id = int(data.get("correct_option_id", 0))
        if correct_id >= len(options) or correct_id < 0:
            correct_id = 0
        explanation = str(data.get("explanation", "Правильна відповідь!"))[:195]

        return {
            "topic": data.get("topic", "Німецька вікторина"),
            "question": q,
            "options": options,
            "correct_option_id": correct_id,
            "explanation": explanation,
            "post_type": "evening"
        }
    except Exception as e:
        logger.error(f"Помилка генерації вечірнього тесту: {e}")
        fallback = FALLBACK_QUIZ.copy()
        fallback["post_type"] = "evening"
        return fallback


# Зворотна сумісність
generate_daily_content = generate_morning_content
