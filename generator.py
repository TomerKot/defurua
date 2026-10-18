"""
generator.py
Модуль генерації різноформатного навчального контенту з німецької мови через Google Gemini API.

Підтримує 3 формати публікацій на день:
1. Ранковий пост: «Слово дня» за щотижневим рубрикатором + картка + аудіо
2. Денний пост: «Жива розмовна фраза дня» + міні-діалог у реальних життєвих ситуаціях + картка + аудіо
3. Вечірній пост: «Інтерактивний Quiz-тест» (нативне опитування Telegram poll) з варіантами та поясненням
"""

import os
import re
import json
import logging
from datetime import datetime
import google.generativeai as genai

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

def sanitize_ukrainian_text(text: str) -> str:
    """
    Суворий автоматичний санітайзер української мови.
    Виправляє будь-які випадкові русизми, кальки та суржик:
    - 'здачі не треба' -> 'решти не треба'
    - 'здача' -> 'решта', 'без здачі' -> 'без решти'
    - 'білет' -> 'квиток'
    - 'слідуючий' -> 'наступний'
    - 'налічка' -> 'готівка'
    - 'больнічний' -> 'лікарняний'
    """
    if not text:
        return ""
    
    rules = [
        (r'\bЗдачі не треба\b', 'Решти не треба'),
        (r'\bздачі не треба\b', 'решти не треба'),
        (r'\bБез здачі\b', 'Без решти'),
        (r'\bбез здачі\b', 'без решти'),
        (r'\bздача\b', 'решта'),
        (r'\bЗдача\b', 'Решта'),
        (r'\bздачі\b', 'решти'),
        (r'\bЗдачі\b', 'Решти'),
        (r'\bздачу\b', 'решту'),
        (r'\bЗдачу\b', 'Решту'),
        (r'\bздачею\b', 'рештою'),
        (r'\bЗдачею\b', 'Рештою'),
        (r'\bбілет(и|ів|ами|ах|ом|а)?\b', lambda m: 'квитк' + (m.group(1) if m.group(1) else 'ок')),
        (r'\bБілет(и|ів|ами|ах|ом|а)?\b', lambda m: 'Квитк' + (m.group(1) if m.group(1) else 'ок')),
        (r'\bслідуюч([а-я]+)\b', r'наступн\1'),
        (r'\bСлідуюч([а-я]+)\b', r'Наступн\1'),
        (r'\bналічк(а|у|ою|і)\b', r'готівк\1'),
        (r'\bНалічк(а|у|ою|і)\b', r'Готівк\1'),
        (r'\bбольнічн([а-я]+)\b', r'лікарнян\1'),
        (r'\bБольнічн([а-я]+)\b', r'Лікарнян\1'),
    ]

    result = text
    for pattern, repl in rules:
        if callable(repl):
            result = re.sub(pattern, repl, result)
        else:
            result = re.sub(pattern, repl, result)
    return result


def sanitize_content_dict(data: dict) -> dict:
    """Рекурсивно санітизує всі рядкові поля в згенерованому контенті."""
    if not isinstance(data, dict):
        return data
    for k, v in data.items():
        if isinstance(v, str):
            data[k] = sanitize_ukrainian_text(v)
        elif isinstance(v, list):
            data[k] = [
                sanitize_content_dict(item) if isinstance(item, dict)
                else (sanitize_ukrainian_text(item) if isinstance(item, str) else item)
                for item in v
            ]
        elif isinstance(v, dict):
            data[k] = sanitize_content_dict(v)
    return data


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

# Резервні дані для шаблонів офіційних листів (Musterbrief)
FALLBACK_MUSTERBRIEF = {
    "topic": "Розірвання договору (Kündigung des Vertrags)",
    "situation": "Розірвання контракту на спортзал (Fitnessstudio), мобільний зв'язок або інтернет",
    "card_example_de": "Hiermit kündige ich meinen Vertrag fristgerecht zum nächstmöglichen Zeitpunkt.",
    "card_example_ua": "Цим листом я розриваю мій договір у встановлений термін до найближчої можливої дати.",
    "letter_text": """Sehr geehrte Damen und Herren,

hiermit kündige ich meinen oben genannten Vertrag (Vertragsnummer: [Номер договору]) fristgerecht zum nächstmöglichen Zeitpunkt.

Bitte senden Sie mir eine schriftliche Bestätigung der Kündigung unter Angabe des Beendigungszeitpunkts zu.

Mit freundlichen Grüßen,
[Ваше Ім'я та Прізвище]""",
    "translation": """Шановні пані та панове!

Цим листом я розриваю мій вищезазначений договір (номер договору: [Номер договору]) у встановлений термін до найближчої можливої дати.

Будь ласка, надішліть мені письмове підтвердження розірвання із зазначенням дати закінчення договору.

З повагою,
[Ваше Ім'я та Прізвище]""",
    "vocabulary": [
        {"de": "fristgerecht kündigen", "ua": "розірвати договір у встановлений термін"},
        {"de": "zum nächstmöglichen Zeitpunkt", "ua": "до найближчої можливої дати"},
        {"de": "die Kündigungsbestätigung", "ua": "письмове підтвердження розірвання"}
    ],
    "pro_tip": "💡 <b>Порада:</b> Завжди вимагайте письмове підтвердження (Kündigungsbestätigung). Важливі листи краще надсилати як рекомендований лист (Einschreiben Einwurf) через пошту Deutsche Post або факсом із протоколом відправлення (Sendeprotokoll)."
}

# Резервні дані для типових помилок українців
FALLBACK_MISTAKE = {
    "mistake_title": "«Мені холодно» німецькою мовою",
    "wrong_phrase": "❌ Ich bin kalt / Ich habe kalt",
    "correct_phrase": "✅ Mir ist kalt!",
    "level": "A1-A2",
    "card_example_de": "Mir ist so kalt! Mach bitte das Fenster zu.",
    "card_example_ua": "Мені так холодно! Зачини, будь ласка, вікно.",
    "explanation": "В українській мові ми кажемо «мені холодно». Початківці часто буквально перекладають це як «Ich bin kalt» (що для німців звучить як «я бездушна/холодна людина») або «Ich habe kalt». У німецькій мові для фізичних відчуттів завжди використовується давальний відмінок Dativ (кому? — mir).",
    "examples": [
        {"de": "Mir ist kalt. Können wir reingehen?", "ua": "Мені холодно. Можемо зайти всередину?"},
        {"de": "Ist dir auch so warm?", "ua": "Тобі теж так тепло/жарко?"}
    ],
    "pro_tip": "💡 <b>Правило трьох відчуттів:</b> Mir ist kalt (холодно), Mir ist warm (тепло), Mir ist schlecht (погано/нудить). Завжди кажіть 'Mir ist...', а не 'Ich bin...'!"
}

# Резервні дані для ситуативних міні-діалогів
FALLBACK_DIALOGUE = {
    "location": "У пекарні (In der Bäckerei)",
    "topic": "Купівля свіжих булочок та кави",
    "card_example_de": "Zwei Brötchen und einen Kaffee zum Mitnehmen, bitte!",
    "card_example_ua": "Дві булочки та одну каву з собою, будь ласка!",
    "level": "A1-A2",
    "dialogue": [
        {"speaker": "Продавець", "de": "Guten Morgen! Was darf es sein?", "ua": "Доброго ранку! Що для вас?"},
        {"speaker": "Ви", "de": "Guten Morgen! Ich hätte gerne zwei Brötchen und einen Kaffee zum Mitnehmen, bitte.", "ua": "Доброго ранку! Я хотів би дві булочки та одну каву з собою, будь ласка."},
        {"speaker": "Продавець", "de": "Gerne. Das macht vier Euro zwanzig zusammen.", "ua": "Із задоволенням. Разом чотири євро двадцять центів."},
        {"speaker": "Ви", "de": "Hier sind fünf Euro. Stimmt so, danke schön!", "ua": "Ось п'ять євро. Решти не треба, дуже дякую!"}
    ],
    "vocabulary": [
        {"de": "Ich hätte gerne...", "ua": "Я хотів би / хотіла б... (найввічливіша форма замовлення)"},
        {"de": "zum Mitnehmen", "ua": "з собою (на виніс)"},
        {"de": "Stimmt so!", "ua": "Решти не треба! / Залишіть собі!"}
    ],
    "pro_tip": "💡 <b>Культурний лайфхак:</b> У багатьох традиційних німецьких пекарнях часто діє оплата лише готівкою (nur Barzahlung) або карткою від певної суми (ab 5 Euro). Звертайте увагу на таблички на касі!"
}


def load_history(history_file_path: str = "history.json") -> dict:
    """Завантажує історію використаних слів, фраз, квізів, листів, помилок та діалогів."""
    empty_history = {
        "used_words": [],
        "used_phrases": [],
        "used_quizzes": [],
        "used_letters": [],
        "used_mistakes": [],
        "used_dialogues": [],
        "history": []
    }
    if not os.path.exists(history_file_path):
        return empty_history
    
    try:
        with open(history_file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, dict):
                return empty_history
            for key in ["used_words", "used_phrases", "used_quizzes", "used_letters", "used_mistakes", "used_dialogues", "history"]:
                data.setdefault(key, [])
            return data
    except Exception as e:
        logger.warning(f"Не вдалося прочитати {history_file_path}: {e}")
        return empty_history


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
    """Очищує, парсить та суворо санітизує відповідь моделі у JSON від будь-якого суржику."""
    raw = text.strip()
    if raw.startswith("```json"):
        raw = raw[7:]
    if raw.startswith("```"):
        raw = raw[3:]
    if raw.endswith("```"):
        raw = raw[:-3]
    parsed = json.loads(raw.strip())
    return sanitize_content_dict(parsed)


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
Ти — професійний викладач німецької для українців у Німеччині. Створи сьогоднішній пост «Слово дня».
Рубрика: {rubric['title']} ({rubric['description']}).
Рівень: {rubric['default_level']}.
СУВОРО ЗАБОРОНЕНО використовувати ці слова: {recent_words_str}

СУВОРІ ВИМОГИ ДО ЛІТЕРАТУРНОЇ ЧИСТОТИ УКРАЇНСЬКОЇ МОВИ:
- ЖОДНОГО СУРЖИКУ та кальок з російської!
- Тільки питомі українські слова:
  • "решта" / "решти не треба" / "без решти" (СУВОРО ЗАБОРОНЕНО писати "здача" або "здачі не треба"!)
  • "квиток" (НЕ "білет")
  • "лікарняний" (НЕ "больнічний")
  • "готівка" (НЕ "налічка")
  • "наступний" (НЕ "слідуючий")
  • "запис / зустріч" (замість незрозумілих кальок).

Поверни валідний JSON:
{{
  "word": "Слово з артиклем та множиною",
  "article": "der / die / das або пусто",
  "clean_word": "Чисте слово з артиклем для озвучки",
  "transcription": "[транскрипція українськими літерами з наголосом]",
  "translation": "чистий переклад літературною українською",
  "level": "{rubric['default_level']}",
  "rubric_name": "{rubric['title']}",
  "rubric_emoji": "{rubric['emoji']}",
  "card_example_de": "Коротке речення для графічної картки (макс 10-12 слів)",
  "card_example_ua": "Літературний український переклад цього речення",
  "explanation": "Дружнє та практичне пояснення чистою українською мовою (2-3 речення).",
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
Ти — сучасний професійний викладач німецької. Обери одну дуже популярну, живу розмовну німецьку фразу або сталий вираз, який німці постійно вживають у супермаркеті, транспорті, кав'ярні, на роботі або при знайомстві (рівні A1-B2).
СУВОРО ЗАБОРОНЕНО використовувати ці раніше опубліковані фрази: {recent_phrases_str}

СУВОРІ ВИМОГИ ДО ЛІТЕРАТУРНОЇ ЧИСТОТИ УКРАЇНСЬКОЇ МОВИ:
- ЖОДНОГО СУРЖИКУ та кальок з російської!
- Тільки питома літературна українська мова:
  • "Решти не треба!" / "без решти" / "решта" (КАТЕГОРИЧНО ЗАБОРОНЕНО писати "здача" або "здачі не треба"!).
  • "квиток" (НЕ "білет")
  • "лікарняний" (НЕ "больнічний")
  • "готівка" (НЕ "налічка")
  • "наступний" (НЕ "слідуючий")
  • "підходить / влаштовує" (замість русизмів).

Поверни валідний JSON:
{{
  "phrase": "Німецька розмовна фраза (наприклад: Stimmt so! або Mach dir keine Sorgen!)",
  "transcription": "[транскрипція українськими літерами з наголосом]",
  "translation": "живий точний літературний український переклад",
  "situation": "де саме це говорять (наприклад: У супермаркеті на касі / У кафе)",
  "card_example_de": "Короткий живий приклад для графічної картки (макс 10 слів)",
  "card_example_ua": "Літературний український переклад цього речення",
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

СУВОРІ ВИМОГИ ДО ЛІТЕРАТУРНОЇ ЧИСТОТИ УКРАЇНСЬКОЇ МОВИ:
- ЖОДНОГО СУРЖИКУ та кальок з російської! Тільки нормативна українська: "решта", "квиток", "лікарняний", "готівка".

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
  "explanation": "Чітке коротке пояснення правил літературною українською (максимум 190 символів!)."
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


# ==========================================================
# 4. ШАБЛОНИ ОФІЦІЙНИХ ЛИСТІВ (MUSTERBRIEFE)
# ==========================================================
def format_musterbrief_post(data: dict) -> str:
    """Форматує пост із готовим німецьким шаблоном офіційного листа для Telegram."""
    lines = [
        "✉️ <b>Німецька для українців | Офіційний шаблон (Musterbrief)</b>",
        f"📌 <b>Тема:</b> <i>{data.get('topic', 'Шаблон листа')}</i>",
        f"📍 <i>Призначення: {data.get('situation', 'Офіційні установи / сервіси')}</i>",
        "",
        "📋 <b>Текст листа німецькою (натисніть, щоб скопіювати):</b>",
        f"<pre>{data.get('letter_text', '').strip()}</pre>",
        "",
        "📖 <b>Переклад українською:</b>",
        f"<i>{data.get('translation', '').strip()}</i>",
        "",
        "🔑 <b>Ключова лексика:</b>"
    ]
    for v in data.get("vocabulary", []):
        lines.append(f"🔹 <b>{v.get('de', '')}</b> — {v.get('ua', '')}")
    if data.get("pro_tip"):
        lines.append("")
        lines.append(data.get("pro_tip"))
    lines.append("")
    lines.append("#Musterbrief #НімецькіЛисти #Бюрократія #Німеччина")
    return "\n".join(lines)


def generate_musterbrief_content(history_file_path: str = "history.json", api_key: str = None) -> dict:
    """
    Генерує готовий шаблон німецького листа (Musterbrief) для життя в Німеччині:
    - Kündigung (спортзал, мобільний зв'язок, підписка)
    - Terminanfrage (Bürgeramt, Ausländerbehörde, лікар)
    - Krankmeldung (робота, школа, дитячий садок)
    - Adressänderung / Bankverbindung (Jobcenter, Krankenkasse)
    - Ratenzahlung / Fristverlängerung (розстрочка, перенесення дедлайну)
    """
    history = load_history(history_file_path)
    used_letters = history.get("used_letters", [])

    model = get_gemini_model(api_key)
    if not model:
        fallback = FALLBACK_MUSTERBRIEF.copy()
        fallback["telegram_post"] = format_musterbrief_post(fallback)
        fallback["word"] = fallback.get("topic")
        fallback["audio_text"] = fallback.get("card_example_de", "")
        fallback["post_type"] = "musterbrief"
        return fallback

    recent_letters_str = ", ".join(used_letters[-50:]) if used_letters else "немає"
    prompt = f"""
Ти — експерт з німецької офіційної кореспонденції та права для українців у Німеччині.
Створи надзвичайно практичний, грамотний, сучасний зразок офіційного німецького листа (Musterbrief).
Теми можуть стосуватися:
1. Розірвання договору (Kündigung: спортзал, інтернет, зв'язок, страховка).
2. Запит на зустріч/запис (Terminanfrage im Bürgeramt / Ausländerbehörde / Facharzt).
3. Повідомлення про хворобу (Krankmeldung для роботодавця або виправдання відсутності дитини в школі).
4. Зміна банківських реквізитів або адреси проживання (Mitteilung über Umzug / Bankverbindung).
5. Прохання про сплату частинами або відстрочку дедлайну (Ratenzahlung / Fristverlängerung).

СУВОРО УНИКАЙ ВЖЕ ВИКОРИСТАНИХ ТЕМ: {recent_letters_str}

СУВОРІ ВИМОГИ ДО ЛІТЕРАТУРНОЇ ЧИСТОТИ УКРАЇНСЬКОЇ МОВИ:
- Жодного суржику та кальок з російської!

Поверни валідний JSON:
{{
  "topic": "Коротка та чітка тема листа (наприклад: Розірвання контракту на інтернет / Kündigung)",
  "situation": "Коли та для чого використовується цей лист (1 речення)",
  "card_example_de": "Ключова німецька фраза листа (до 10 слів) для графічної картки",
  "card_example_ua": "Літературний український переклад цієї фрази",
  "letter_text": "Повний офіційний текст листа німецькою мовою з чіткими полями у квадратних дужках, наприклад [Ihr Name], [Vertragsnummer], [Datum]. Звернення (Sehr geehrte...), суть, ввічливе закінчення (Mit freundlichen Grüßen).",
  "translation": "Повний український переклад листа абзац за абзацом.",
  "vocabulary": [
    {{"de": "німецький офіційний вираз 1", "ua": "український переклад 1"}},
    {{"de": "німецький офіційний вираз 2", "ua": "український переклад 2"}},
    {{"de": "німецький офіційний вираз 3", "ua": "український переклад 3"}}
  ],
  "pro_tip": "💡 <b>Юридична порада:</b> як правильно надіслати (Einschreiben, дедлайн попередження, отримання Bestätigung)."
}}
"""
    try:
        resp = model.generate_content(prompt)
        data = clean_json_response(resp.text)
        data["rubric_name"] = "Офіційний лист (Musterbrief)"
        data["rubric_emoji"] = "✉️"
        data["word"] = data.get("topic", "Musterbrief")
        data["clean_word"] = data.get("topic", "Musterbrief")
        data["level"] = "Офіційна"
        data["telegram_post"] = format_musterbrief_post(data)
        data["audio_text"] = data.get("card_example_de", "Hiermit kündige ich meinen Vertrag.")
        data["post_type"] = "musterbrief"
        return data
    except Exception as e:
        logger.error(f"Помилка генерації Musterbrief: {e}")
        fallback = FALLBACK_MUSTERBRIEF.copy()
        fallback["telegram_post"] = format_musterbrief_post(fallback)
        fallback["word"] = fallback.get("topic")
        fallback["audio_text"] = fallback.get("card_example_de", "")
        fallback["post_type"] = "musterbrief"
        return fallback



# ==========================================================
# 5. ТИПОВІ ПОМИЛКИ УКРАЇНЦІВ (TYPISCHE FEHLER & FALSCHE FREUNDE)
# ==========================================================
def format_mistake_post(data: dict) -> str:
    """Форматує пост із розбором типової помилки для Telegram."""
    lines = [
        "⚠️ <b>Німецька для українців | Типова помилка</b>",
        f"📌 <b>Тема:</b> <i>{data.get('mistake_title', 'Помилка початківців')}</i>",
        "",
        f"❌ <b>Як часто кажуть помилково:</b>",
        f"<s>{data.get('wrong_phrase', '')}</s>",
        "",
        f"✅ <b>Як правильно сказати:</b>",
        f"<b>{data.get('correct_phrase', '')}</b>",
        "",
        f"💡 <b>Чому виникає помилка:</b>",
        f"{data.get('explanation', '')}",
        "",
        "💬 <b>Приклади правильного вживання:</b>"
    ]
    for ex in data.get("examples", []):
        lines.append(f"• <b>{ex.get('de', '')}</b>\n  ↳ <i>{ex.get('ua', '')}</i>")
    
    if data.get("pro_tip"):
        lines.append("")
        lines.append(data.get("pro_tip"))
    
    lines.append("")
    lines.append("🎧 <i>Слухайте правильну німецьку вимову в аудіо вище!</i>")
    lines.append("")
    lines.append("#ТиповіПомилки #Граматика #НімецькаМова #Німеччина")
    return "\n".join(lines)


def generate_mistake_content(history_file_path: str = "history.json", api_key: str = None) -> dict:
    """Генерує розбір типової помилки або «хибних друзів перекладача» (Falsche Freunde)."""
    history = load_history(history_file_path)
    used_mistakes = history.get("used_mistakes", [])

    model = get_gemini_model(api_key)
    if not model:
        fallback = FALLBACK_MISTAKE.copy()
        fallback["telegram_post"] = format_mistake_post(fallback)
        fallback["word"] = fallback.get("correct_phrase", "Mir ist kalt!")
        fallback["audio_text"] = fallback.get("card_example_de", "")
        fallback["post_type"] = "mistake"
        return fallback

    recent_mistakes_str = ", ".join(used_mistakes[-50:]) if used_mistakes else "немає"
    prompt = f"""
Ти — професійний викладач німецької для україномовних студентів. Обери ОДНУ дуже поширену типову помилку, яку українці роблять у німецькій через кальку з рідної мови, інтерференцію або «хибних друзів перекладача» (Falsche Freunde).

Приклади тем (рівні A1-B2):
- Mir ist kalt (а не Ich bin kalt / Ich habe kalt)
- bekommen (отримувати) vs werden (ставати)
- kennen (людей/міста) vs wissen (факти/інформацію)
- kein (для іменників) vs nicht (для дієслів/прикметників)
- Ich studiere an der Uni (а не Ich lerne an der Uni)
- Das Gerät funktioniert (а не arbeitet)
- Ich fahre mit dem Bus (а не йду транспортом)
- Ich bin verheiratet mit... (Dativ, а не an)
- Ich freue mich auf vs über

СУВОРО УНИКАЙ ВЖЕ ВИКОРИСТАНИХ ТЕМ: {recent_mistakes_str}

СУВОРІ ВИМОГИ ДО ЛІТЕРАТУРНОЇ ЧИСТОТИ УКРАЇНСЬКОЇ МОВИ:
- Жодного суржику та кальок з російської (лише "решта", "квиток", "лікарняний", "готівка", "наступний").

Поверни валідний JSON:
{{
  "mistake_title": "Коротка назва помилки (наприклад: 'Отримувати' чи 'ставати': bekommen vs werden)",
  "wrong_phrase": "❌ Неправильна фраза з типовою помилкою",
  "correct_phrase": "✅ Правильний німецький вираз",
  "level": "A1-A2",
  "card_example_de": "Короткий правильний приклад для графічної картки (до 10 слів)",
  "card_example_ua": "Літературний український переклад цього речення",
  "explanation": "Чітке, дружнє пояснення (2-3 речення): чому виникає плутанина і як правильно запам'ятати.",
  "examples": [
    {{"de": "Правильне речення 1", "ua": "Переклад 1"}},
    {{"de": "Правильне речення 2", "ua": "Переклад 2"}}
  ],
  "pro_tip": "💡 <b>Правило-підказка:</b> легка мнемонічна підказка або лайфхак для запам'ятовування."
}}
"""
    try:
        resp = model.generate_content(prompt)
        data = clean_json_response(resp.text)
        data["rubric_name"] = "Типова помилка"
        data["rubric_emoji"] = "⚠️"
        clean_correct = data.get("correct_phrase", "").replace("✅", "").strip()
        data["word"] = clean_correct or data.get("mistake_title", "Typischer Fehler")
        data["clean_word"] = clean_correct
        data["translation"] = data.get("card_example_ua", "")
        data["telegram_post"] = format_mistake_post(data)
        data["audio_text"] = f"{clean_correct}. {data.get('card_example_de', '')}".strip()
        data["post_type"] = "mistake"
        return data
    except Exception as e:
        logger.error(f"Помилка генерації типової помилки: {e}")
        fallback = FALLBACK_MISTAKE.copy()
        fallback["telegram_post"] = format_mistake_post(fallback)
        fallback["word"] = fallback.get("correct_phrase", "Mir ist kalt!")
        fallback["audio_text"] = fallback.get("card_example_de", "")
        fallback["post_type"] = "mistake"
        return fallback


# ==========================================================
# 6. ЖИВИЙ МІНІ-ДІАЛОГ У РЕАЛЬНІЙ СИТУАЦІЇ
# ==========================================================
def format_dialogue_post(data: dict) -> str:
    """Форматує пост із живим міні-діалогом для Telegram."""
    lines = [
        "🏙️ <b>Німецька для українців | Живий діалог</b>",
        f"📍 <b>Локація:</b> <i>{data.get('location', 'Щоденна ситуація')}</i>",
        f"🎯 <b>Тема:</b> <i>{data.get('topic', 'Розмова')}</i>",
        "",
        "💬 <b>Міні-діалог:</b>"
    ]
    for d in data.get("dialogue", []):
        lines.append(f"👤 <b>{d.get('speaker', '')}:</b> <i>{d.get('de', '')}</i>")
        lines.append(f"   ↳ {d.get('ua', '')}")
    
    lines.append("")
    lines.append("🔑 <b>Корисні фрази з діалогу:</b>")
    for v in data.get("vocabulary", []):
        lines.append(f"🔹 <b>{v.get('de', '')}</b> — {v.get('ua', '')}")
    
    if data.get("pro_tip"):
        lines.append("")
        lines.append(data.get("pro_tip"))
    
    lines.append("")
    lines.append("🎧 <i>Слухайте правильну вимову діалогу в аудіо вище!</i>")
    lines.append("")
    lines.append("#ЖивийДіалог #РозмовнаНімецька #Німеччина #Alltag")
    return "\n".join(lines)


def generate_dialogue_content(history_file_path: str = "history.json", api_key: str = None) -> dict:
    """Генерує практичний міні-діалог у реальній життєвій ситуації в Німеччині."""
    history = load_history(history_file_path)
    used_dialogues = history.get("used_dialogues", [])

    model = get_gemini_model(api_key)
    if not model:
        fallback = FALLBACK_DIALOGUE.copy()
        fallback["telegram_post"] = format_dialogue_post(fallback)
        fallback["word"] = fallback.get("topic", "Діалог")
        fallback["audio_text"] = fallback.get("card_example_de", "")
        fallback["post_type"] = "dialogue"
        return fallback

    recent_dialogues_str = ", ".join(used_dialogues[-50:]) if used_dialogues else "немає"
    prompt = f"""
Ти — викладач розмовної німецької мови для щоденного життя в Німеччині. Створи живий, практичний міні-діалог (рівень A1-B1) у реальній побутовій ситуації в Німеччині.

Локації для вибору:
- В аптеці (In der Apotheke: ліки від застуди, спрей для носа, рецепт)
- У пекарні (In der Bäckerei: замовлення хліба, булочок, кави)
- На касі в супермаркеті (An der Kasse: картка, готівка, чек, пакет, здача пляшок Pfand)
- У лікаря (Beim Arzt: запис на прийом, опис симптомів, лікарняний Krankschreibung)
- У громадському транспорті (Im Zug / Bus: покупка квитка, запізнення, пересадка)
- На пошті Deutsche Post / DHL (відправка посилки, рекомендованого листа Einschreiben)
- У Bürgeramt / Ausländerbehörde (прості розмовні запитання)

СУВОРО УНИКАЙ ВЖЕ ВИКОРИСТАНИХ ТЕМ: {recent_dialogues_str}

СУВОРІ ВИМОГИ ДО ЛІТЕРАТУРНОЇ ЧИСТОТИ УКРАЇНСЬКОЇ МОВИ:
- Жодного суржику та кальок з російської (обов'язково "решти не треба", "квиток", "лікарняний", "готівка").

Поверни валідний JSON:
{{
  "location": "Локація (наприклад: В аптеці / In der Apotheke)",
  "topic": "Тема діалогу (наприклад: Купівля ліків від болю в горлі)",
  "level": "A1-A2",
  "card_example_de": "Ключова фраза або репліка діалогу (до 10 слів) для графічної картки",
  "card_example_ua": "Літературний український переклад цієї фрази",
  "dialogue": [
    {{"speaker": "Фармацевт", "de": "Guten Tag! Wie kann ich Ihnen helfen?", "ua": "Доброго дня! Чим можу допомогти?"}},
    {{"speaker": "Ви", "de": "Репліка 2", "ua": "Переклад 2"}},
    {{"speaker": "Фармацевт", "de": "Репліка 3", "ua": "Переклад 3"}},
    {{"speaker": "Ви", "de": "Репліка 4", "ua": "Переклад 4"}}
  ],
  "vocabulary": [
    {{"de": "німецький вираз 1", "ua": "український переклад 1"}},
    {{"de": "німецький вираз 2", "ua": "український переклад 2"}},
    {{"de": "німецький вираз 3", "ua": "український переклад 3"}}
  ],
  "pro_tip": "💡 <b>Культурна порада:</b> корисний нюанс для цієї ситуації в Німеччині."
}}
"""
    try:
        resp = model.generate_content(prompt)
        data = clean_json_response(resp.text)
        data["rubric_name"] = "Живий діалог"
        data["rubric_emoji"] = "🏙️"
        data["word"] = data.get("topic", "Живий діалог")
        data["clean_word"] = data.get("card_example_de", "")
        data["translation"] = data.get("card_example_ua", "")
        data["telegram_post"] = format_dialogue_post(data)
        
        # Для озвучення: ключова фраза або перші репліки
        dialogue_audio = ". ".join([d.get("de", "") for d in data.get("dialogue", [])[:2]])
        data["audio_text"] = (data.get("card_example_de") or dialogue_audio)[:250]
        data["post_type"] = "dialogue"
        return data
    except Exception as e:
        logger.error(f"Помилка генерації живого діалогу: {e}")
        fallback = FALLBACK_DIALOGUE.copy()
        fallback["telegram_post"] = format_dialogue_post(fallback)
        fallback["word"] = fallback.get("topic", "Діалог")
        fallback["audio_text"] = fallback.get("card_example_de", "")
        fallback["post_type"] = "dialogue"
        return fallback


# Зворотна сумісність
generate_daily_content = generate_morning_content
