"""
generator.py
Модуль генерації навчального контенту з німецької мови через Google Gemini API.
Враховує щотижневий рубрикатор та історію вже опублікованих слів, щоб уникнути дублікатів.
"""

import os
import json
import logging
from datetime import datetime
import google.generativeai as genai

# Налаштування логування
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Щотижневий рубрикатор за днями тижня (0 = Понеділок, 6 = Неділя)
WEEKLY_RUBRICS = {
    0: {
        "title": "Побут та щоденне життя (Alltag)",
        "emoji": "🛒",
        "description": "Повсякденні побутові ситуації в Німеччині: супермаркети, оренда житла, транспорт, аптека, візити до лікаря.",
        "default_level": "A1-A2"
    },
    1: {
        "title": "Граматика без нудоти (Grammatik)",
        "emoji": "🧩",
        "description": "Практична граматика: артиклі (der/die/das), відмінки Akkusativ/Dativ, порядок слів, модальні дієслова на реальних прикладах.",
        "default_level": "A2-B1"
    },
    2: {
        "title": "Робота та бюрократія (Bürokratie & Beruf)",
        "emoji": "💼",
        "description": "Jobcenter, Bürgeramt, Krankenkasse, написання резюме (Lebenslauf), пошук роботи та спілкування з офіційними установами.",
        "default_level": "A2-B2"
    },
    3: {
        "title": "Розмовна мова та сленг (Umgangssprache)",
        "emoji": "🗣️",
        "description": "Жива німецька мова: сленг, популярні вирази носіїв мови, скорочення, реакції в розмові, яких не вчать у старих підручниках.",
        "default_level": "A2-B2"
    },
    4: {
        "title": "Типові помилки українців (Typische Fehler)",
        "emoji": "⚠️",
        "description": "Помилки через прямий переклад з української, «хибні друзі перекладача» (bekommen ≠ ставати), різниця kennen vs wissen тощо.",
        "default_level": "A1-B1"
    },
    5: {
        "title": "Ідіоми та менталітет (Redewendungen & Kultur)",
        "emoji": "🇩🇪",
        "description": "Крилаті фрази, німецькі ідіоми, звичаї та правила життя в Німеччині (Ruhezeit, Mülltrennung, культура пунктуальності).",
        "default_level": "A2-B2"
    },
    6: {
        "title": "Практика та міні-тест (Quiz & Wiederholung)",
        "emoji": "🎯",
        "description": "Цікаве слово або корисна фраза з обов'язковим інтерактивним міні-тестом для підписників для перевірки знань.",
        "default_level": "A2-B1"
    }
}

# Резервні пости на випадок збоїв API або вичерпання квот
FALLBACK_CONTENT = {
    0: {
        "word": "die Mülltrennung",
        "article": "die",
        "clean_word": "die Mülltrennung",
        "transcription": "[ді Мю́льтреннунґ]",
        "translation": "сортування сміття",
        "level": "A1-A2",
        "rubric_name": "Побут та щоденне життя (Alltag)",
        "rubric_emoji": "🛒",
        "card_example_de": "In Deutschland ist Mülltrennung Pflicht.",
        "card_example_ua": "У Німеччині сортування сміття є обов'язковим.",
        "explanation": "Роздільний збір сміття — одна з головних побутових звичок у Німеччині. Жовтий контейнер/мішок (Gelber Sack) — для пластику та упаковки, синій — для паперу, коричневий (Biomüll) — для органіки, а чорний (Restmüll) — для несортованих залишків.",
        "examples": [
            {"de": "Wohin gehört dieser Karton? — In die blaue Tonne.", "ua": "Куди викидати цю коробку? — У синій бак."},
            {"de": "Wir müssen den Müll richtig trennen.", "ua": "Ми маємо правильно сортувати сміття."}
        ],
        "pro_tip": "💡 <b>Лайфхак:</b> Скляні пляшки без застави (Pfand) викидають у спеціальні вуличні контейнери за кольором: біле, зелене та коричневе скло. Робити це в неділю та вночі суворо заборонено через Ruhezeit!",
        "quiz": {
            "question": "Куди в Німеччині викидають пластикові упаковки від йогурту?",
            "options": ["A) У синій контейнер", "B) У жовтий контейнер (Gelbe Tonne)", "C) У біовідходи"],
            "correct_answer": "B"
        }
    },
    1: {
        "word": "der Feierabend",
        "article": "der",
        "clean_word": "der Feierabend",
        "transcription": "[дер Фа́єрабент]",
        "translation": "кінець робочого дня / вечірній відпочинок",
        "level": "A1-A2",
        "rubric_name": "Граматика без нудоти (Grammatik)",
        "rubric_emoji": "🧩",
        "card_example_de": "Schönen Feierabend allerseits!",
        "card_example_ua": "Гарного відпочинку після роботи всім!",
        "explanation": "Культове німецьке слово, що складається з 'feiern' (святкувати) та 'der Abend' (вечір). Воно означає момент, коли робота завершена і починається особистий вільний час.",
        "examples": [
            {"de": "Ich mache jetzt Feierabend.", "ua": "Я завершую роботу на сьогодні."},
            {"de": "Um wie viel Uhr hast du Feierabend?", "ua": "О котрій годині у тебе закінчується робота?"}
        ],
        "pro_tip": "💡 <b>Граматична деталь:</b> Вираз 'Feierabend machen' означає 'завершувати роботу/робочий день'.",
        "quiz": {
            "question": "Що кажуть колегам, коли йдуть додому з роботи?",
            "options": ["A) Guten Feiertag!", "B) Schöne Pause!", "C) Schönen Feierabend!"],
            "correct_answer": "C"
        }
    }
}


def get_current_rubric(target_date: datetime = None) -> tuple[int, dict]:
    """
    Повертає рубрику за днем тижня.
    0 - Понеділок, 6 - Неділя.
    """
    if target_date is None:
        target_date = datetime.now()
    weekday = target_date.weekday()
    return weekday, WEEKLY_RUBRICS.get(weekday, WEEKLY_RUBRICS[0])


def load_history(history_file_path: str = "history.json") -> dict:
    """Завантажує файл історії використаних слів."""
    if not os.path.exists(history_file_path):
        return {"used_words": [], "history": []}
    
    try:
        with open(history_file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, dict):
                return {"used_words": [], "history": []}
            data.setdefault("used_words", [])
            data.setdefault("history", [])
            return data
    except Exception as e:
        logger.warning(f"Не вдалося коректно прочитати {history_file_path}: {e}. Використовуємо порожню історію.")
        return {"used_words": [], "history": []}


def format_telegram_post(data: dict) -> str:
    """
    Форматує готовий текст Telegram-посту з HTML-розміткою.
    """
    word = data.get("word", "")
    transcription = data.get("transcription", "")
    translation = data.get("translation", "")
    level = data.get("level", "A1-B2")
    rubric_name = data.get("rubric_name", "Німецька щодня")
    rubric_emoji = data.get("rubric_emoji", "🇩🇪")
    explanation = data.get("explanation", "")
    examples = data.get("examples", [])
    pro_tip = data.get("pro_tip", "")
    quiz = data.get("quiz")

    # Формуємо красивий структурований HTML-допис
    lines = [
        f"{rubric_emoji} <b>Німецька для українців | {rubric_name}</b>",
        f"🏷️ <i>Рівень: {level}</i>",
        "",
        f"⭐ <b>{word}</b> <code>{transcription}</code>",
        f"👉 <b>Переклад:</b> {translation}",
        "",
        f"📖 <b>Пояснення та контекст:</b>",
        f"{explanation}",
        "",
        "💬 <b>Приклади вживання:</b>"
    ]

    for ex in examples:
        de_ex = ex.get("de", "").strip()
        ua_ex = ex.get("ua", "").strip()
        lines.append(f"🔹 <i>{de_ex}</i>")
        lines.append(f"   ↳ {ua_ex}")

    if pro_tip:
        lines.append("")
        lines.append(pro_tip)

    if quiz and isinstance(quiz, dict) and quiz.get("question"):
        lines.append("")
        lines.append(f"🧠 <b>Міні-тест на закріплення:</b>")
        lines.append(f"❓ {quiz.get('question')}")
        for opt in quiz.get("options", []):
            lines.append(f"  • {opt}")
        lines.append("<i>(Свою відповідь пишіть або обирайте в коментарях!)</i>")

    lines.append("")
    lines.append("🎧 <i>Прослухайте правильну вимову у прикріпленому аудіо вище!</i>")
    lines.append("")
    lines.append("#НімецькаМова #СловоДня #DeutschLernen #Німеччина #УкраїнціВНімеччині")

    return "\n".join(lines)


def generate_daily_content(history_file_path: str = "history.json", api_key: str = None) -> dict:
    """
    Генерує навчальний контент на сьогодні через Google Gemini API.
    Якщо ключ відсутній або виникла помилка, повертає якісний резервний пост.
    """
    weekday, rubric = get_current_rubric()
    history = load_history(history_file_path)
    used_words = history.get("used_words", [])

    if not api_key:
        api_key = os.environ.get("GEMINI_API_KEY")

    if not api_key:
        logger.warning("GEMINI_API_KEY не знайдено в змінних середовища. Використовуємо якісний резервний пост.")
        fallback = FALLBACK_CONTENT.get(weekday, FALLBACK_CONTENT[0]).copy()
        fallback["telegram_post"] = format_telegram_post(fallback)
        fallback["audio_text"] = f"{fallback.get('clean_word')}. {fallback.get('card_example_de')}"
        return fallback

    logger.info(f"Генерація контенту через Gemini 1.5 Flash для рубрики: {rubric['title']} (день: {weekday})...")

    # Формуємо список виключень (останні 150 слів)
    recent_words_str = ", ".join(used_words[-150:]) if used_words else "немає (це перша генерація)"

    prompt = f"""
Ти — професійний викладач німецької мови для українців, які проживають у Німеччині.
Створи сьогоднішній щоденний навчальний матеріал для Telegram-каналу.

СЬОГОДНІШНЯ РУБРИКА:
Назва: {rubric['title']}
Тематика: {rubric['description']}
Орієнтовний рівень: {rubric['default_level']}

ВАЖЛИВІ ПРАВИЛА:
1. Цільова аудиторія — українці, які адаптуються до життя та роботи в Німеччині. Матеріал має бути живим, практичним, без сухої академічної теорії.
2. СУВОРА ЗАБОРОНА: НЕ ВИКОРИСТОВУЙ жодне з цих уже раніше опублікованих слів чи виразів:
{recent_words_str}
3. Обери актуальне, часто вживане німецьке слово, сталий вираз або конструкцію, що ідеально відповідає рубриці.
4. Якщо це іменник — обов'язково вкажи артикль (der, die, das) та закінчення множини (наприклад: "die Anmeldung, -en").
5. Транскрипція українськими літерами має передавати правильне німецьке звучання (наприклад: "[ді Анме́льдунґ]").
6. Приклади мають бути живими реченнями, які дійсно щодня говорять у Німеччині.

Формат відповіді: виключно валідний JSON-об'єкт із такою точною структурою:
{{
  "word": "Німецьке слово або фраза з артиклем та множиною",
  "article": "der / die / das або пустий рядок, якщо це дієслово/фраза",
  "clean_word": "Чисте німецьке слово з артиклем без позначок множини для озвучення (наприклад: die Anmeldung)",
  "transcription": "[транскрипція українськими літерами з наголосом]",
  "translation": "чіткий переклад українською мовою",
  "level": "{rubric['default_level']}",
  "rubric_name": "{rubric['title']}",
  "rubric_emoji": "{rubric['emoji']}",
  "card_example_de": "Одне коротке яскраве німецьке речення для графічної картки (макс 10-12 слів)",
  "card_example_ua": "Український переклад цього речення для картки",
  "explanation": "Зрозуміле та дружнє пояснення українською мовою: чому це важливо, нюанси значення або як це працює в Німеччині (3-4 речення).",
  "examples": [
    {{"de": "Німецьке речення 1", "ua": "Український переклад 1"}},
    {{"de": "Німецьке речення 2", "ua": "Український переклад 2"}}
  ],
  "pro_tip": "💡 <b>Корисна порада / Лайфхак:</b> короткий цікавий факт, лайфхак чи порада щодо вживання або життя в Німеччині.",
  "quiz": {{"question": "Питання для підписників", "options": ["A) Варіант 1", "B) Варіант 2", "C) Варіант 3"], "correct_answer": "A"}}
}}
Відповідай ТІЛЬКИ чистим JSON без зайвих слів чи маркерів markdown.
"""

    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(
            model_name="gemini-1.5-flash",
            generation_config={
                "temperature": 0.7,
                "response_mime_type": "application/json",
            }
        )

        response = model.generate_content(prompt)
        content_text = response.text.strip()

        # Очищення від можливих блоків ```json ... ```
        if content_text.startswith("```json"):
            content_text = content_text[7:]
        if content_text.startswith("```"):
            content_text = content_text[3:]
        if content_text.endswith("```"):
            content_text = content_text[:-3]

        data = json.loads(content_text.strip())

        # Перевірка та заповнення обов'язкових полів
        data.setdefault("rubric_name", rubric["title"])
        data.setdefault("rubric_emoji", rubric["emoji"])
        if not data.get("clean_word"):
            data["clean_word"] = data.get("word", "")

        # Створення тексту для Telegram та аудіо
        data["telegram_post"] = format_telegram_post(data)
        
        # Текст для озвучки: слово + приклад
        example_de = data.get("card_example_de", "")
        clean_word = data.get("clean_word", "")
        data["audio_text"] = f"{clean_word}. {example_de}".strip()

        logger.info(f"Успішно згенеровано слово дня: '{data.get('word')}'")
        return data

    except Exception as e:
        logger.error(f"Помилка під час виклику Gemini API: {e}. Використовуємо резервний пост.")
        fallback = FALLBACK_CONTENT.get(weekday, FALLBACK_CONTENT[0]).copy()
        fallback["telegram_post"] = format_telegram_post(fallback)
        fallback["audio_text"] = f"{fallback.get('clean_word')}. {fallback.get('card_example_de')}"
        return fallback


if __name__ == "__main__":
    # Тестовий запуск генератора локально
    print("Тестування генератора...")
    result = generate_daily_content()
    print("Згенероване слово:", result.get("word"))
    print("Переклад:", result.get("translation"))
    print("\nТекст посту:\n", result.get("telegram_post"))
