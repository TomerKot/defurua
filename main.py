"""
main.py
Головний виконуваний скрипт для автоматизованого Telegram-каналу вивчення німецької мови.

Підтримує 3 публікації на день:
1. morning (09:00 за Києвом / 07:00 UTC) — Слово дня + Картка + Аудіо
2. afternoon (14:00 за Києвом / 12:00 UTC) — Жива розмовна фраза + Картка + Аудіо
3. evening (19:00 за Києвом / 17:00 UTC) — Інтерактивне Quiz-опитування Telegram
"""

import os
import sys
import json
import logging
import argparse
from datetime import datetime, timezone
import requests
from gtts import gTTS
from dotenv import load_dotenv

# Імпортуємо генератори та створення графічних карток
from generator import (
    generate_morning_content,
    generate_afternoon_content,
    generate_evening_quiz,
    generate_musterbrief_content,
    generate_mistake_content,
    generate_dialogue_content,
    load_history
)
from image_maker import generate_card_image

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

HISTORY_FILE = "history.json"
CARD_IMAGE_FILE = "card.png"
AUDIO_FILE = "pronunciation.mp3"
TELEGRAM_CAPTION_LIMIT = 1024


def validate_environment() -> tuple[str, str, str]:
    """Перевіряє наявність секретів у змінних середовища."""
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    gemini_key = os.environ.get("GEMINI_API_KEY")

    missing = []
    if not bot_token:
        missing.append("TELEGRAM_BOT_TOKEN")
    if not chat_id:
        missing.append("TELEGRAM_CHAT_ID")
    if not gemini_key:
        missing.append("GEMINI_API_KEY")

    if missing:
        error_msg = f"❌ Не знайдено обов'язкові змінні середовища: {', '.join(missing)}."
        logger.error(error_msg)
        raise EnvironmentError(error_msg)

    return bot_token, chat_id, gemini_key


CRON_SCHEDULE_MAP = {
    "5 6 * * *": "morning",
    "35 8 * * *": "phrase",
    "5 11 * * *": "mistake",
    "35 13 * * *": "dialogue",
    "5 16 * * *": "letter",
    "35 18 * * *": "quiz",
}

ORDERED_POST_TYPES = ["morning", "phrase", "mistake", "dialogue", "letter", "quiz"]

TYPE_ALIASES = {
    "morning": ["morning", "morning_word"],
    "phrase": ["phrase", "afternoon", "afternoon_phrase"],
    "mistake": ["mistake"],
    "dialogue": ["dialogue"],
    "letter": ["letter", "musterbrief"],
    "quiz": ["quiz", "evening", "evening_quiz"],
}


def determine_post_type(arg_type: str = "auto", cron_expr: str = None) -> str:
    """
    Визначає тип публікації з 6 щоденних форматів:
    1. morning   — 08:05 DE (06:05 UTC) — «Слово дня» (звук УВІМКНЕНО)
    2. phrase    — 10:35 DE (08:35 UTC) — «Жива розмовна фраза» (ТИХИЙ пост)
    3. mistake   — 13:05 DE (11:05 UTC) — «Типова помилка українців» (ТИХИЙ пост)
    4. dialogue  — 15:35 DE (13:35 UTC) — «Живий ситуативний діалог» (ТИХИЙ пост)
    5. letter    — 18:05 DE (16:05 UTC) — «Шаблон листа (Musterbrief)» (ТИХИЙ пост)
    6. quiz      — 20:35 DE (18:35 UTC) — «Вечірній Quiz-батл» (звук УВІМКНЕНО)
    """
    if cron_expr and cron_expr.strip() in CRON_SCHEDULE_MAP:
        matched = CRON_SCHEDULE_MAP[cron_expr.strip()]
        logger.info(f"Визначено тип публікації за розкладом cron '{cron_expr}' -> [{matched.upper()}]")
        return matched

    aliases = {
        "morning": "morning",
        "phrase": "phrase",
        "afternoon": "phrase",
        "mistake": "mistake",
        "dialogue": "dialogue",
        "letter": "letter",
        "musterbrief": "letter",
        "quiz": "quiz",
        "evening": "quiz"
    }
    if arg_type in aliases:
        return aliases[arg_type]

    now = datetime.now(timezone.utc)
    current_minutes = now.hour * 60 + now.minute
    logger.info(f"Режим 'auto': поточний час UTC = {now.strftime('%H:%M')} ({current_minutes} хв)")

    # Розподіл 6 часових вікон за UTC:
    # 06:05 UTC (08:05 DE) -> 05:00 - 07:19 UTC (300..439 хв)
    # 08:35 UTC (10:35 DE) -> 07:20 - 09:49 UTC (440..589 хв)
    # 11:05 UTC (13:05 DE) -> 09:50 - 12:19 UTC (590..739 хв)
    # 13:35 UTC (15:35 DE) -> 12:20 - 14:49 UTC (740..889 хв)
    # 16:05 UTC (18:05 DE) -> 14:50 - 17:19 UTC (890..1039 хв)
    # 18:35 UTC (20:35 DE) -> 17:20 - 04:59 UTC (>= 1040 або < 300 хв)
    if 300 <= current_minutes < 440:
        return "morning"
    elif 440 <= current_minutes < 590:
        return "phrase"
    elif 590 <= current_minutes < 740:
        return "mistake"
    elif 740 <= current_minutes < 890:
        return "dialogue"
    elif 890 <= current_minutes < 1040:
        return "letter"
    else:
        return "quiz"


def get_already_posted_types_today(history: dict, today_str: str = None) -> set:
    """Повертає множину канонічних типів постів, які вже були опубліковані сьогодні."""
    if not today_str:
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    posted = set()
    for item in history.get("history", []):
        d = str(item.get("date", ""))
        t = str(item.get("type", ""))
        if d.startswith(today_str):
            for canonical, aliases in TYPE_ALIASES.items():
                if t in aliases or t == canonical:
                    posted.add(canonical)
    return posted


def create_audio_pronunciation(text: str, output_path: str = AUDIO_FILE) -> str | None:
    """Створює аудіо вимови через gTTS."""
    try:
        logger.info(f"Створення аудіо вимови для: '{text}'...")
        tts = gTTS(text=text, lang="de", slow=False)
        tts.save(output_path)
        logger.info(f"Аудіофайл успішно збережено у {output_path}")
        return output_path
    except Exception as e:
        logger.warning(f"Не вдалося згенерувати аудіо: {e}")
        return None


def send_telegram_media_post(
    bot_token: str,
    chat_id: str,
    image_path: str,
    caption_text: str,
    audio_path: str | None,
    title: str,
    silent: bool = False
):
    """Надсилає фотопост із підписом та аудіо вимови (підтримує тихі сповіщення silent=True)."""
    api_url = f"https://api.telegram.org/bot{bot_token}"

    with open(image_path, "rb") as photo_file:
        if len(caption_text) <= TELEGRAM_CAPTION_LIMIT:
            response = requests.post(
                f"{api_url}/sendPhoto",
                data={
                    "chat_id": chat_id,
                    "caption": caption_text,
                    "parse_mode": "HTML",
                    "disable_notification": silent
                },
                files={"photo": photo_file},
                timeout=30
            )
        else:
            short_caption = f"🇩🇪 <b>{title}</b>\n<i>Повний розбір читайте в повідомленні нижче 👇</i>"
            response = requests.post(
                f"{api_url}/sendPhoto",
                data={
                    "chat_id": chat_id,
                    "caption": short_caption,
                    "parse_mode": "HTML",
                    "disable_notification": silent
                },
                files={"photo": photo_file},
                timeout=30
            )
            requests.post(
                f"{api_url}/sendMessage",
                data={
                    "chat_id": chat_id,
                    "text": caption_text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                    "disable_notification": silent
                },
                timeout=30
            )

    if not response.ok:
        logger.error(f"Помилка надсилання фото: {response.status_code} — {response.text}")
        response.raise_for_status()

    # Надсилання аудіо
    if audio_path and os.path.exists(audio_path):
        try:
            with open(audio_path, "rb") as voice_file:
                voice_caption = f"🗣 <b>Вимова:</b> <i>{title}</i>"
                v_resp = requests.post(
                    f"{api_url}/sendVoice",
                    data={
                        "chat_id": chat_id,
                        "caption": voice_caption,
                        "parse_mode": "HTML",
                        "disable_notification": silent
                    },
                    files={"voice": voice_file},
                    timeout=30
                )
                if not v_resp.ok:
                    voice_file.seek(0)
                    requests.post(
                        f"{api_url}/sendAudio",
                        data={
                            "chat_id": chat_id,
                            "title": title,
                            "performer": "Deutsch für Ukrainer",
                            "caption": voice_caption,
                            "parse_mode": "HTML",
                            "disable_notification": silent
                        },
                        files={"audio": voice_file},
                        timeout=30
                    )
            logger.info("Аудіо вимови успішно опубліковано!")
        except Exception as e:
            logger.warning(f"Не вдалося надіслати аудіо: {e}")


def send_telegram_poll(bot_token: str, chat_id: str, quiz_data: dict, silent: bool = False):
    """
    Надсилає нативне інтерактивне Quiz-опитування в Telegram.
    При натисканні правильної відповіді показується анімація конфеті та пояснення.
    """
    api_url = f"https://api.telegram.org/bot{bot_token}/sendPoll"
    logger.info(f"Публікація Quiz-опитування: {quiz_data['question']}")

    payload = {
        "chat_id": chat_id,
        "question": quiz_data["question"],
        "options": quiz_data["options"],
        "is_anonymous": True,
        "type": "quiz",
        "correct_option_id": quiz_data["correct_option_id"],
        "explanation": quiz_data["explanation"],
        "explanation_parse_mode": "HTML",
        "disable_notification": silent
    }

    resp = requests.post(api_url, json=payload, timeout=30)
    if not resp.ok:
        logger.error(f"Помилка публікації опитування: {resp.status_code} — {resp.text}")
        resp.raise_for_status()
    else:
        logger.info("Quiz-опитування успішно опубліковано в каналі!")


def update_history_file(data: dict, history_file_path: str = HISTORY_FILE):
    """Оновлює history.json новими записами слів, фраз або вікторин."""
    history = load_history(history_file_path)
    post_type = data.get("post_type", "morning")

    if post_type == "morning":
        new_word = data.get("clean_word") or data.get("word")
        if new_word and new_word not in history.get("used_words", []):
            history["used_words"].append(new_word)
        history["history"].append({
            "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "type": "morning_word",
            "word": data.get("word"),
            "translation": data.get("translation"),
            "rubric": data.get("rubric_name")
        })
    elif post_type == "afternoon":
        new_phrase = data.get("phrase") or data.get("word")
        if new_phrase and new_phrase not in history.get("used_phrases", []):
            history.setdefault("used_phrases", []).append(new_phrase)
        history["history"].append({
            "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "type": "afternoon_phrase",
            "phrase": new_phrase,
            "translation": data.get("translation")
        })
    elif post_type == "evening":
        topic = data.get("topic", "Quiz")
        if topic and topic not in history.get("used_quizzes", []):
            history.setdefault("used_quizzes", []).append(topic)
        history["history"].append({
            "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "type": "evening_quiz",
            "question": data.get("question"),
            "topic": topic
        })
    elif post_type in ["musterbrief", "letter"]:
        topic = data.get("topic", "Musterbrief")
        if topic and topic not in history.get("used_letters", []):
            history.setdefault("used_letters", []).append(topic)
        history["history"].append({
            "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "type": "musterbrief",
            "topic": topic,
            "situation": data.get("situation")
        })
    elif post_type == "mistake":
        title = data.get("mistake_title") or data.get("word")
        if title and title not in history.get("used_mistakes", []):
            history.setdefault("used_mistakes", []).append(title)
        history["history"].append({
            "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "type": "mistake",
            "topic": title,
            "correct_phrase": data.get("correct_phrase")
        })
    elif post_type == "dialogue":
        loc = data.get("location") or data.get("topic")
        if loc and loc not in history.get("used_dialogues", []):
            history.setdefault("used_dialogues", []).append(loc)
        history["history"].append({
            "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "type": "dialogue",
            "location": loc,
            "topic": data.get("topic")
        })

    history["last_updated"] = datetime.now().isoformat()
    try:
        with open(history_file_path, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
        
        # Автоматична синхронізація з docs/history.json для Telegram WebApp
        if os.path.exists("docs"):
            with open("docs/history.json", "w", encoding="utf-8") as f_docs:
                json.dump(history, f_docs, ensure_ascii=False, indent=2)
            logger.info("Файл docs/history.json для WebApp успішно синхронізовано!")

        logger.info("Файл history.json успішно збережено!")
    except Exception as e:
        logger.error(f"Помилка запису в {history_file_path}: {e}")


def cleanup_temp_files(*file_paths):
    """Видаляє тимчасові файли."""
    for path in file_paths:
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass


def run_pipeline(post_type: str = "auto", cron_expr: str = None, force: bool = False):
    """Головний цикл запуску публікації одного з 6 щоденних форматів."""
    bot_token, chat_id, gemini_key = validate_environment()
    actual_type = determine_post_type(post_type, cron_expr)

    # Захист від повторної публікації того самого формату в один день
    history = load_history(HISTORY_FILE)
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    already_posted = get_already_posted_types_today(history, today_str)

    if not force and actual_type in already_posted:
        logger.warning(f"Увага: тип [{actual_type.upper()}] вже публікувався сьогодні ({today_str})!")
        current_idx = ORDERED_POST_TYPES.index(actual_type) if actual_type in ORDERED_POST_TYPES else len(ORDERED_POST_TYPES)
        # Шукаємо, чи є сьогодні пропущені слоти, які ще не публікувалися
        missed = [pt for pt in ORDERED_POST_TYPES[:current_idx] if pt not in already_posted]
        if missed:
            actual_type = missed[0]
            logger.info(f"Знайдено пропущений слот раніше сьогодні: перемикаємося на [{actual_type.upper()}].")
        else:
            logger.info(f"Сьогодні формат [{actual_type.upper()}] вже опубліковано і немає пропущених слотів. Пропускаємо запуск, щоб запобігти повторці.")
            return

    # 4 денні публікації надсилаються без звуку (silent), щоб не перевантажувати підписників
    is_silent = actual_type in ["phrase", "afternoon", "mistake", "dialogue", "letter", "musterbrief"]
    logger.info(f"=== Запуск публікації: тип [{actual_type.upper()}], тихий режим={is_silent} ===")

    if actual_type == "morning":
        # 1. 08:05 — «Слово дня» (Alltag & Grammatik) [звук УВІМКНЕНО]
        logger.info("Генерація ранкового слова дня...")
        content_data = generate_morning_content(history_file_path=HISTORY_FILE, api_key=gemini_key)
        title = content_data.get("clean_word") or content_data.get("word")
        card_path = generate_card_image(content_data, output_path=CARD_IMAGE_FILE)
        audio_path = create_audio_pronunciation(content_data.get("audio_text", title), output_path=AUDIO_FILE)
        
        send_telegram_media_post(
            bot_token=bot_token,
            chat_id=chat_id,
            image_path=card_path,
            caption_text=content_data.get("telegram_post", ""),
            audio_path=audio_path,
            title=title,
            silent=is_silent
        )
        update_history_file(content_data, history_file_path=HISTORY_FILE)
        cleanup_temp_files(card_path, audio_path)

    elif actual_type in ["phrase", "afternoon"]:
        # 2. 10:35 — «Жива розмовна фраза дня» [ТИХИЙ]
        logger.info("Генерація розмовної фрази...")
        content_data = generate_afternoon_content(history_file_path=HISTORY_FILE, api_key=gemini_key)
        title = content_data.get("phrase", "Розмовна фраза")
        card_path = generate_card_image(content_data, output_path=CARD_IMAGE_FILE)
        audio_path = create_audio_pronunciation(content_data.get("audio_text", title), output_path=AUDIO_FILE)

        send_telegram_media_post(
            bot_token=bot_token,
            chat_id=chat_id,
            image_path=card_path,
            caption_text=content_data.get("telegram_post", ""),
            audio_path=audio_path,
            title=title,
            silent=is_silent
        )
        update_history_file(content_data, history_file_path=HISTORY_FILE)
        cleanup_temp_files(card_path, audio_path)

    elif actual_type == "mistake":
        # 3. 13:05 — «Типова помилка українців» [ТИХИЙ]
        logger.info("Генерація типової помилки українців...")
        content_data = generate_mistake_content(history_file_path=HISTORY_FILE, api_key=gemini_key)
        title = content_data.get("word", "Типова помилка")
        card_path = generate_card_image(content_data, output_path=CARD_IMAGE_FILE)
        audio_path = create_audio_pronunciation(content_data.get("audio_text", title), output_path=AUDIO_FILE)

        send_telegram_media_post(
            bot_token=bot_token,
            chat_id=chat_id,
            image_path=card_path,
            caption_text=content_data.get("telegram_post", ""),
            audio_path=audio_path,
            title=title,
            silent=is_silent
        )
        update_history_file(content_data, history_file_path=HISTORY_FILE)
        cleanup_temp_files(card_path, audio_path)

    elif actual_type == "dialogue":
        # 4. 15:35 — «Живий ситуативний діалог» [ТИХИЙ]
        logger.info("Генерація живого діалогу...")
        content_data = generate_dialogue_content(history_file_path=HISTORY_FILE, api_key=gemini_key)
        title = content_data.get("word", "Діалог")
        card_path = generate_card_image(content_data, output_path=CARD_IMAGE_FILE)
        audio_path = create_audio_pronunciation(content_data.get("audio_text", title), output_path=AUDIO_FILE)

        send_telegram_media_post(
            bot_token=bot_token,
            chat_id=chat_id,
            image_path=card_path,
            caption_text=content_data.get("telegram_post", ""),
            audio_path=audio_path,
            title=title,
            silent=is_silent
        )
        update_history_file(content_data, history_file_path=HISTORY_FILE)
        cleanup_temp_files(card_path, audio_path)

    elif actual_type in ["letter", "musterbrief"]:
        # 5. 18:05 — «Офіційний лист (Musterbrief)» [ТИХИЙ]
        logger.info("Генерація офіційного листа (Musterbrief)...")
        content_data = generate_musterbrief_content(history_file_path=HISTORY_FILE, api_key=gemini_key)
        title = content_data.get("topic", "Musterbrief")
        card_path = generate_card_image(content_data, output_path=CARD_IMAGE_FILE)
        audio_path = create_audio_pronunciation(content_data.get("audio_text", title), output_path=AUDIO_FILE)

        send_telegram_media_post(
            bot_token=bot_token,
            chat_id=chat_id,
            image_path=card_path,
            caption_text=content_data.get("telegram_post", ""),
            audio_path=audio_path,
            title=title,
            silent=is_silent
        )
        update_history_file(content_data, history_file_path=HISTORY_FILE)
        cleanup_temp_files(card_path, audio_path)

    elif actual_type in ["quiz", "evening"]:
        # 6. 20:35 — «Вечірній Quiz-батл (Poll)» [звук УВІМКНЕНО]
        logger.info("Генерація вечірньої інтерактивної вікторини...")
        quiz_data = generate_evening_quiz(history_file_path=HISTORY_FILE, api_key=gemini_key)
        send_telegram_poll(
            bot_token=bot_token,
            chat_id=chat_id,
            quiz_data=quiz_data,
            silent=is_silent
        )
        update_history_file(quiz_data, history_file_path=HISTORY_FILE)

    logger.info(f"=== Публікацію [{actual_type.upper()}] успішно завершено! ===")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Автоматична публікація постів у Telegram-канал.")
    parser.add_argument(
        "--type",
        dest="post_type",
        choices=["auto", "morning", "phrase", "afternoon", "mistake", "dialogue", "letter", "musterbrief", "quiz", "evening"],
        default="auto",
        help="Тип публікації: morning, phrase, mistake, dialogue, letter, quiz, auto"
    )
    parser.add_argument(
        "--cron",
        dest="cron_expr",
        default=None,
        help="Розклад cron GitHub Actions (наприклад, '5 6 * * *')"
    )
    parser.add_argument(
        "--force",
        dest="force",
        action="store_true",
        help="Примусова публікація без перевірки на вже опубліковані сьогодні пости"
    )
    args = parser.parse_args()

    try:
        run_pipeline(post_type=args.post_type, cron_expr=args.cron_expr, force=args.force)
    except Exception as e:
        logger.critical(f"Критична помилка виконання: {e}", exc_info=True)
        sys.exit(1)
