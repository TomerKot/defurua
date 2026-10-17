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
from datetime import datetime
import requests
from gtts import gTTS
from dotenv import load_dotenv

# Імпортуємо генератори та створення графічних карток
from generator import (
    generate_morning_content,
    generate_afternoon_content,
    generate_evening_quiz,
    generate_musterbrief_content,
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


def determine_post_type(arg_type: str = "auto") -> str:
    """
    Визначає тип публікації:
    Якщо передано явне значення — використовує його.
    Якщо 'auto' — обирає за часом UTC та днем тижня:
    - 05:00 - 09:59 UTC -> morning (Слово дня)
    - 10:00 - 14:59 UTC:
        • У суботу (день 5) -> letter (Офіційний лист Musterbrief)
        • В інші дні -> afternoon (Розмовна фраза)
    - 15:00 - 04:59 UTC -> evening (Quiz-опитування)
    """
    if arg_type in ["morning", "afternoon", "evening", "letter", "musterbrief"]:
        return arg_type

    utc_hour = datetime.utcnow().hour
    weekday = datetime.utcnow().weekday()
    logger.info(f"Режим 'auto': поточна година UTC = {utc_hour}, день тижня = {weekday}")

    if weekday == 5 and 10 <= utc_hour < 15:
        return "letter"
    elif 5 <= utc_hour < 10:
        return "morning"
    elif 10 <= utc_hour < 15:
        return "afternoon"
    else:
        return "evening"


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


def send_telegram_media_post(bot_token: str, chat_id: str, image_path: str, caption_text: str, audio_path: str | None, title: str):
    """Надсилає фотопост із підписом та аудіо вимови."""
    api_url = f"https://api.telegram.org/bot{bot_token}"

    with open(image_path, "rb") as photo_file:
        if len(caption_text) <= TELEGRAM_CAPTION_LIMIT:
            response = requests.post(
                f"{api_url}/sendPhoto",
                data={
                    "chat_id": chat_id,
                    "caption": caption_text,
                    "parse_mode": "HTML"
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
                    "parse_mode": "HTML"
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
                    "disable_web_page_preview": True
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
                        "parse_mode": "HTML"
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
                            "parse_mode": "HTML"
                        },
                        files={"audio": voice_file},
                        timeout=30
                    )
            logger.info("Аудіо вимови успішно опубліковано!")
        except Exception as e:
            logger.warning(f"Не вдалося надіслати аудіо: {e}")


def send_telegram_poll(bot_token: str, chat_id: str, quiz_data: dict):
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
        "explanation_parse_mode": "HTML"
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


def run_pipeline(post_type: str = "auto"):
    """Головний цикл запуску публікації."""
    bot_token, chat_id, gemini_key = validate_environment()
    actual_type = determine_post_type(post_type)
    logger.info(f"=== Запуск публікації: тип [{actual_type.upper()}] ===")

    if actual_type == "morning":
        # 1. Ранковий пост (Слово дня)
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
            title=title
        )
        update_history_file(content_data, history_file_path=HISTORY_FILE)
        cleanup_temp_files(card_path, audio_path)

    elif actual_type == "afternoon":
        # 2. Денний пост (Розмовна фраза)
        logger.info("Генерація денної розмовної фрази...")
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
            title=title
        )
        update_history_file(content_data, history_file_path=HISTORY_FILE)
        cleanup_temp_files(card_path, audio_path)

    elif actual_type == "evening":
        # 3. Вечірній пост (Інтерактивне Quiz-опитування)
        logger.info("Генерація вечірньої інтерактивної вікторини...")
        quiz_data = generate_evening_quiz(history_file_path=HISTORY_FILE, api_key=gemini_key)
        send_telegram_poll(
            bot_token=bot_token,
            chat_id=chat_id,
            quiz_data=quiz_data
        )
    elif actual_type in ["letter", "musterbrief"]:
        # 4. Спеціальний пост: Офіційний лист (Musterbrief)
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
            title=title
        )
        update_history_file(content_data, history_file_path=HISTORY_FILE)
        cleanup_temp_files(card_path, audio_path)

    logger.info(f"=== Публікацію [{actual_type.upper()}] успішно завершено! ===")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Автоматична публікація постів у Telegram-канал.")
    parser.add_argument(
        "--type",
        dest="post_type",
        choices=["auto", "morning", "afternoon", "evening", "letter", "musterbrief"],
        default="auto",
        help="Тип публікації: morning, afternoon, evening, letter (або musterbrief), auto"
    )
    args = parser.parse_args()

    try:
        run_pipeline(post_type=args.post_type)
    except Exception as e:
        logger.critical(f"Критична помилка виконання: {e}", exc_info=True)
        sys.exit(1)
