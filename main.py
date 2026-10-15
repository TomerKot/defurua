"""
main.py
Головний виконуваний скрипт для автоматизованого Telegram-каналу вивчення німецької мови.
Об'єднує:
1. Генерацію контенту через Gemini 1.5 Flash (generator.py)
2. Створення брендованої графічної картки (image_maker.py)
3. Озвучення німецького слова та прикладу через gTTS
4. Публікацію картки, тексту та аудіо у Telegram-канал
5. Оновлення історії слів у history.json
"""

import os
import sys
import json
import logging
from datetime import datetime
import requests
from gtts import gTTS
from dotenv import load_dotenv

# Імпортуємо власні модулі проєкту
from generator import generate_daily_content, load_history
from image_maker import generate_card_image

# Завантажуємо локальний файл .env (якщо він існує для тестування)
load_dotenv()

# Налаштування логування
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# Константи файлів
HISTORY_FILE = "history.json"
CARD_IMAGE_FILE = "card.png"
AUDIO_FILE = "pronunciation.mp3"
TELEGRAM_CAPTION_LIMIT = 1024


def validate_environment() -> tuple[str, str, str]:
    """
    Перевіряє наявність необхідних змінних середовища.
    У разі відсутності виводить зрозумілі інструкції українською мовою.
    """
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
        error_msg = (
            f"❌ ПОМИЛКА: Не знайдено обов'язкові змінні середовища: {', '.join(missing)}.\n"
            "Будь ласка, додайте їх у GitHub Secrets репозиторію або у локальний файл .env!"
        )
        logger.error(error_msg)
        raise EnvironmentError(error_msg)

    return bot_token, chat_id, gemini_key


def create_audio_pronunciation(text: str, output_path: str = AUDIO_FILE) -> str | None:
    """
    Створює аудіофайл із правильною німецькою вимовою за допомогою gTTS.
    """
    try:
        logger.info(f"Створення аудіо вимови для тексту: '{text}' (мова: de)...")
        tts = gTTS(text=text, lang="de", slow=False)
        tts.save(output_path)
        logger.info(f"Аудіофайл успішно збережено у {output_path}")
        return output_path
    except Exception as e:
        logger.warning(f"Не вдалося згенерувати аудіо через gTTS: {e}. Продовжуємо без аудіо.")
        return None


def send_telegram_post(bot_token: str, chat_id: str, image_path: str, caption_text: str, audio_path: str | None, word_title: str):
    """
    Надсилає навчальний пост у Telegram-канал:
    - Зображення-картку разом із форматованим описом
    - Голосове повідомлення / аудіо з правильною вимовою
    """
    api_url = f"https://api.telegram.org/bot{bot_token}"

    # 1. Надсилання фотокартки
    logger.info("Публікація графічної картки з описом у Telegram...")
    with open(image_path, "rb") as photo_file:
        # Перевірка ліміту довжини підпису фотографії в Telegram (1024 символи)
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
            # Якщо опис довший за 1024 символи: надсилаємо фото з коротким підписом + окреме повне повідомлення
            short_caption = f"🇩🇪 <b>Слово дня: {word_title}</b>\n<i>Детальний розбір та приклад читайте в повідомленні нижче 👇</i>"
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
            # Надсилаємо повний текст
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
        logger.error(f"Помилка надсилання фото в Telegram: {response.status_code} — {response.text}")
        response.raise_for_status()
    else:
        logger.info("Фотопост успішно опубліковано у каналі!")

    # 2. Надсилання аудіо вимови
    if audio_path and os.path.exists(audio_path):
        logger.info("Надсилання аудіофайлу вимови у канал...")
        try:
            with open(audio_path, "rb") as voice_file:
                voice_caption = f"🗣 <b>Вимова:</b> <i>{word_title}</i>"
                voice_resp = requests.post(
                    f"{api_url}/sendVoice",
                    data={
                        "chat_id": chat_id,
                        "caption": voice_caption,
                        "parse_mode": "HTML"
                    },
                    files={"voice": voice_file},
                    timeout=30
                )
                if not voice_resp.ok:
                    logger.warning(f"sendVoice повернув помилку ({voice_resp.status_code}), спроба через sendAudio...")
                    voice_file.seek(0)
                    requests.post(
                        f"{api_url}/sendAudio",
                        data={
                            "chat_id": chat_id,
                            "title": word_title,
                            "performer": "Deutsch für Ukrainer",
                            "caption": voice_caption,
                            "parse_mode": "HTML"
                        },
                        files={"audio": voice_file},
                        timeout=30
                    )
            logger.info("Аудіо вимови успішно опубліковано!")
        except Exception as e:
            logger.warning(f"Не вдалося надіслати аудіо у Telegram: {e}")


def update_history_file(data: dict, history_file_path: str = HISTORY_FILE):
    """
    Зберігає нове слово у файл history.json для запобігання дублюванню.
    """
    history = load_history(history_file_path)
    used_words = history.get("used_words", [])
    posts_history = history.get("history", [])

    new_word = data.get("clean_word") or data.get("word")
    if new_word and new_word not in used_words:
        used_words.append(new_word)

    # Запис нового поста
    posts_history.append({
        "date": datetime.now().strftime("%Y-%m-%d"),
        "word": data.get("word"),
        "translation": data.get("translation"),
        "rubric": data.get("rubric_name"),
        "level": data.get("level")
    })

    history["used_words"] = used_words
    history["history"] = posts_history
    history["last_updated"] = datetime.now().isoformat()

    try:
        with open(history_file_path, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
        logger.info(f"Історію успішно оновлено! Додано слово: '{new_word}'. Усього слів: {len(used_words)}")
    except Exception as e:
        logger.error(f"Помилка запису в {history_file_path}: {e}")


def cleanup_temp_files(*file_paths):
    """Видаляє тимчасові файли після завершення роботи."""
    for path in file_paths:
        if path and os.path.exists(path):
            try:
                os.remove(path)
                logger.info(f"Тимчасовий файл {path} видалено.")
            except Exception as e:
                logger.debug(f"Не вдалося видалити {path}: {e}")


def run_pipeline():
    """
    Головна функція повного циклу щоденної публікації.
    """
    logger.info("=== Запуск щоденного пайплайну публікації уроку німецької ===")
    
    # 1. Перевірка змінних середовища
    bot_token, chat_id, gemini_key = validate_environment()

    # 2. Генерація контенту через Gemini
    logger.info("Крок 1/5: Генерація навчального контенту...")
    content_data = generate_daily_content(history_file_path=HISTORY_FILE, api_key=gemini_key)
    word_title = content_data.get("clean_word") or content_data.get("word")

    # 3. Створення графічної картки
    logger.info("Крок 2/5: Створення брендованої картки з Pillow...")
    card_path = generate_card_image(content_data, output_path=CARD_IMAGE_FILE)

    # 4. Створення аудіофайлу вимови через gTTS
    logger.info("Крок 3/5: Генерація аудіо вимови через gTTS...")
    audio_text = content_data.get("audio_text", word_title)
    audio_path = create_audio_pronunciation(audio_text, output_path=AUDIO_FILE)

    # 5. Публікація в Telegram
    logger.info("Крок 4/5: Публікація в Telegram-канал...")
    telegram_caption = content_data.get("telegram_post", "")
    send_telegram_post(
        bot_token=bot_token,
        chat_id=chat_id,
        image_path=card_path,
        caption_text=telegram_caption,
        audio_path=audio_path,
        word_title=word_title
    )

    # 6. Оновлення history.json
    logger.info("Крок 5/5: Оновлення історії використаних слів...")
    update_history_file(content_data, history_file_path=HISTORY_FILE)

    # 7. Очищення тимчасових файлів (щоб git залишався чистим)
    cleanup_temp_files(card_path, audio_path)

    logger.info("=== Щоденну публікацію успішно завершено! ===")


if __name__ == "__main__":
    try:
        run_pipeline()
    except Exception as e:
        logger.critical(f"Критична помилка виконання пайплайну: {e}", exc_info=True)
        sys.exit(1)
