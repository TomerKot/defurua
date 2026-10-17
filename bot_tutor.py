"""
bot_tutor.py
Персональний AI-репетитор німецької мови в особистих повідомленнях Telegram (@DeLernenUaBot).

Можливості:
1. /start, /help, /menu — головне меню, опис та налаштування кнопки WebApp
2. /quiz — персональний інтерактивний квіз-тест (нативне Telegram Poll опитування з конфеті)
3. /word — випадкове або свіже німецьке слово з транскрипцією, прикладом та аудіо
4. /letter — офіційний шаблон листа (Musterbrief) для німецьких інстанцій
5. /webapp — посилання та кнопка для відкриття веб-додатка флеш-карток
6. Будь-яке текстове запитання:
   - AI-пояснення через Google Gemini (gemini-3.8-flash) літературною українською мовою
   - Автоматичне голосове повідомлення (Voice note) з бездоганною німецькою вимовою
"""

import os
import sys
import time
import json
import random
import logging
import tempfile
import signal
from typing import Optional
import requests
from dotenv import load_dotenv
from gtts import gTTS
import google.generativeai as genai

from generator import (
    sanitize_ukrainian_text,
    generate_evening_quiz,
    generate_morning_content,
    generate_musterbrief_content,
    FALLBACK_QUIZ,
    FALLBACK_WORD,
    FALLBACK_MUSTERBRIEF
)

# Завантаження змінних середовища
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("TutorBot")

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
HISTORY_FILE = "history.json"
WEBAPP_URL = "https://tomerkot.github.io/defurua/"
CHANNEL_USERNAME = "defurualernen"

if not BOT_TOKEN:
    logger.critical("TELEGRAM_BOT_TOKEN не задано в .env!")
    sys.exit(1)

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# Налаштування Gemini AI
ai_model = None
if GEMINI_KEY:
    try:
        genai.configure(api_key=GEMINI_KEY)
        ai_model = genai.GenerativeModel(
            model_name="gemini-3.8-flash",
            generation_config={"temperature": 0.7}
        )
        logger.info("Google Gemini (gemini-3.8-flash) успішно ініціалізовано!")
    except Exception as e:
        logger.error(f"Помилка ініціалізації Gemini: {e}")


# ==========================================================
# ДОПОМІЖНІ ФУНКЦІЇ TELEGRAM API
# ==========================================================
def tg_request(method: str, data: dict = None, files: dict = None, timeout: int = 35) -> Optional[dict]:
    """Виконує HTTP-запит до Telegram Bot API."""
    url = f"{TELEGRAM_API}/{method}"
    try:
        resp = requests.post(url, data=data, files=files, timeout=timeout)
        if not resp.ok:
            logger.warning(f"Telegram API [{method}] помилка: {resp.status_code} - {resp.text}")
            return None
        return resp.json()
    except Exception as e:
        logger.error(f"Помилка з'єднання з Telegram API [{method}]: {e}")
        return None


def send_chat_action(chat_id: int, action: str = "typing"):
    """Відображає статус дії бота в чаті (typing, record_voice тощо)."""
    tg_request("sendChatAction", {"chat_id": chat_id, "action": action})


def send_message(chat_id: int, text: str, reply_markup: dict = None, parse_mode: str = "HTML") -> Optional[dict]:
    """Надсилає текстове повідомлення в чат."""
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True
    }
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    return tg_request("sendMessage", payload)


def send_voice_audio(chat_id: int, text_to_speak: str, caption: str = "🗣 <b>Вимова німецькою:</b>"):
    """Генерує німецьку вимову через gTTS та надсилає голосове повідомлення."""
    if not text_to_speak or len(text_to_speak.strip()) == 0:
        return

    clean_text = text_to_speak.strip().replace("\n", " ")[:300]
    temp_file = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as fp:
            temp_file = fp.name

        tts = gTTS(text=clean_text, lang="de", slow=False)
        tts.save(temp_file)

        with open(temp_file, "rb") as voice_fp:
            tg_request(
                "sendVoice",
                data={
                    "chat_id": chat_id,
                    "caption": caption,
                    "parse_mode": "HTML"
                },
                files={"voice": voice_fp}
            )
    except Exception as e:
        logger.warning(f"Не вдалося згенерувати або надіслати голосове аудіо: {e}")
    finally:
        if temp_file and os.path.exists(temp_file):
            try:
                os.remove(temp_file)
            except OSError:
                pass


def send_quiz_poll(chat_id: int, quiz_data: dict) -> Optional[dict]:
    """Надсилає нативне опитування Telegram Poll у режимі quiz."""
    clean_question = sanitize_ukrainian_text(quiz_data.get("question", "Опитування"))
    options = [sanitize_ukrainian_text(opt) for opt in quiz_data.get("options", [])]
    correct_id = quiz_data.get("correct_option_id", 0)
    explanation = sanitize_ukrainian_text(quiz_data.get("explanation", ""))[:200]

    payload = {
        "chat_id": chat_id,
        "question": clean_question[:300],
        "options": json.dumps(options[:10]),
        "is_anonymous": False,
        "type": "quiz",
        "correct_option_id": correct_id,
        "explanation": explanation,
        "explanation_parse_mode": "HTML"
    }
    return tg_request("sendPoll", payload)


def setup_bot_interface():
    """Налаштовує команди та постійну кнопку меню WebApp для всіх користувачів."""
    commands = [
        {"command": "start", "description": "Головне меню та привітання"},
        {"command": "quiz", "description": "Пройти інтерактивний тест"},
        {"command": "word", "description": "Випадкове німецьке слово з аудіо"},
        {"command": "letter", "description": "Шаблон офіційного листа (Musterbrief)"},
        {"command": "webapp", "description": "Відкрити картки та тренажер"}
    ]
    tg_request("setMyCommands", {"commands": json.dumps(commands)})

    menu = {
        "type": "web_app",
        "text": "📱 Вчити німецьку",
        "web_app": {"url": WEBAPP_URL}
    }
    tg_request("setChatMenuButton", {"menu_button": json.dumps(menu)})
    logger.info("Інтерфейс бота (команди та кнопка меню) успішно оновлено!")


# ==========================================================
# КЛАВІАТУРИ ДЛЯ ВЗАЄМОДІЇ
# ==========================================================
def get_main_keyboard() -> dict:
    """Головна інлайн-клавіатура швидкого доступу."""
    return {
        "inline_keyboard": [
            [
                {"text": "📱 Відкрити картки (WebApp)", "web_app": {"url": WEBAPP_URL}}
            ],
            [
                {"text": "🧠 Пройти тест", "callback_data": "cmd_quiz"},
                {"text": "📖 Слово дня", "callback_data": "cmd_word"}
            ],
            [
                {"text": "✉️ Шаблон листа", "callback_data": "cmd_letter"},
                {"text": "📢 Наш канал", "url": f"https://t.me/{CHANNEL_USERNAME}"}
            ]
        ]
    }


def get_quick_reply_keyboard() -> dict:
    """Компактна клавіатура після відповіді AI."""
    return {
        "inline_keyboard": [
            [
                {"text": "🧠 Ще тест", "callback_data": "cmd_quiz"},
                {"text": "📖 Інше слово", "callback_data": "cmd_word"}
            ],
            [
                {"text": "📱 Тренажер карток", "web_app": {"url": WEBAPP_URL}}
            ]
        ]
    }


# ==========================================================
# ОБРОБНИКИ КОМАНД
# ==========================================================
def handle_start(chat_id: int, user_first_name: str = "друже"):
    """Обробляє команду /start."""
    welcome_text = (
        f"Привіт, <b>{user_first_name}</b>! 👋\n\n"
        f"Я твій персональний AI-репетитор з німецької мови від каналу "
        f"<b>Deutsch für Ukrainer</b> (<b>@{CHANNEL_USERNAME}</b>).\n\n"
        f"🇩🇪 <b>Як я можу тобі допомогти:</b>\n\n"
        f"• 💬 <b>Запитуй будь-що:</b> напиши мені будь-яке запитання з німецької "
        f"(наприклад: <i>«Як сказати в аптеці, що болить горло?»</i>, "
        f"<i>«Поясни різницю між kennen і wissen»</i> чи <i>«Що таке Anmeldebestätigung?»</i>) — "
        f"я дам докладне пояснення та обов'язково надішлю <b>аудіо з правильною німецькою вимовою</b>!\n\n"
        f"• 🧠 <b>/quiz</b> — персональний швидкий тест-опитування для перевірки знань.\n"
        f"• 📖 <b>/word</b> — корисне німецьке слово з граматикою, перекладом та аудіо.\n"
        f"• ✉️ <b>/letter</b> — готовий німецький шаблон листа (Musterbrief) для офіційних установ.\n"
        f"• 📱 <b>/webapp</b> — інтерактивний тренажер карток та пошук за базою слів.\n\n"
        f"<i>Обирай дію нижче або просто пиши своє запитання в чат 👇</i>"
    )
    send_message(chat_id, welcome_text, reply_markup=get_main_keyboard())


def handle_quiz_command(chat_id: int):
    """Генерує та надсилає квіз-опитування."""
    send_chat_action(chat_id, "typing")
    try:
        quiz = generate_evening_quiz(history_file_path=HISTORY_FILE, api_key=GEMINI_KEY)
    except Exception as e:
        logger.error(f"Помилка створення квізу: {e}")
        quiz = FALLBACK_QUIZ

    send_quiz_poll(chat_id, quiz)


def handle_word_command(chat_id: int):
    """Надсилає випадкове або нове слово з аудіо вимовою."""
    send_chat_action(chat_id, "typing")

    word_data = None
    # Спробуємо підтягнути випадкове слово з історії
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                history = json.load(f)
                all_entries = history.get("history", [])
                word_entries = [e for e in all_entries if e.get("type") in ["morning_word", "morning"]]
                if word_entries:
                    chosen = random.choice(word_entries)
                    word_data = {
                        "word": chosen.get("word", "das Leben"),
                        "translation": chosen.get("translation", "життя"),
                        "rubric_name": "Слово з бази",
                        "rubric_emoji": "📖",
                        "transcription": "",
                        "grammar_note": "Зверніть увагу на рід та артикль.",
                        "examples": [
                            {"de": f"{chosen.get('word')} ist wichtig.", "ua": "Це важливо в щоденному житті."}
                        ],
                        "pro_tip": "Повторюйте нові слова вголос разом з аудіо!"
                    }
        except Exception as e:
            logger.warning(f"Помилка читання history.json: {e}")

    # Якщо в історії ще порожньо, генеруємо свіже слово
    if not word_data:
        try:
            word_data = generate_morning_content(history_file_path=HISTORY_FILE, api_key=GEMINI_KEY)
        except Exception as e:
            logger.error(f"Помилка генерації слова: {e}")
            word_data = FALLBACK_WORD

    word = word_data.get("word", "")
    transcription = f" <i>{word_data.get('transcription')}</i>" if word_data.get("transcription") else ""
    translation = word_data.get("translation", "")
    grammar = word_data.get("grammar_note", "")
    pro_tip = word_data.get("pro_tip", "")

    examples_text = ""
    for ex in word_data.get("examples", [])[:2]:
        examples_text += f"• <b>{ex.get('de')}</b>\n  ↳ {ex.get('ua')}\n"

    message = (
        f"{word_data.get('rubric_emoji', '🇩🇪')} <b>{word_data.get('rubric_name', 'Слово')}</b>\n\n"
        f"📌 <b>{word}</b>{transcription} — <b>{translation}</b>\n\n"
    )
    if grammar:
        message += f"🔍 <b>Граматика:</b> {grammar}\n\n"
    if examples_text:
        message += f"💬 <b>Приклади вживання:</b>\n{examples_text}\n"
    if pro_tip:
        message += f"💡 <b>Порада:</b> {pro_tip}\n"

    send_message(chat_id, sanitize_ukrainian_text(message), reply_markup=get_quick_reply_keyboard())
    send_voice_audio(chat_id, word, caption=f"🗣 <b>Вимова:</b> <i>{word}</i>")


def handle_letter_command(chat_id: int):
    """Надсилає офіційний шаблон німецького листа (Musterbrief)."""
    send_chat_action(chat_id, "typing")
    try:
        letter = generate_musterbrief_content(history_file_path=HISTORY_FILE, api_key=GEMINI_KEY)
    except Exception as e:
        logger.error(f"Помилка генерації Musterbrief: {e}")
        letter = FALLBACK_MUSTERBRIEF

    post_text = letter.get("telegram_post", "")
    if post_text:
        send_message(chat_id, post_text, reply_markup=get_quick_reply_keyboard())
    else:
        # Резервне форматування
        txt = (
            f"✉️ <b>Musterbrief: {letter.get('topic', 'Офіційний лист')}</b>\n\n"
            f"📍 <b>Ситуація:</b> {letter.get('situation', '')}\n\n"
            f"📄 <b>Текст листа:</b>\n<pre>{letter.get('letter_text', '')}</pre>\n\n"
            f"🇺🇦 <b>Переклад:</b>\n{letter.get('translation', '')}\n\n"
            f"{letter.get('pro_tip', '')}"
        )
        send_message(chat_id, sanitize_ukrainian_text(txt), reply_markup=get_quick_reply_keyboard())

    audio_phrase = letter.get("audio_text") or letter.get("card_example_de", "")
    if audio_phrase:
        send_voice_audio(chat_id, audio_phrase, caption=f"🗣 <b>Ключовий вираз листа:</b> <i>{audio_phrase}</i>")


def handle_webapp_command(chat_id: int):
    """Надсилає кнопку швидкого відкриття веб-додатка."""
    text = (
        "📱 <b>Інтерактивний тренажер німецької мови</b>\n\n"
        "У нашому веб-додатку ви можете:\n"
        "• 🔄 Гортати та тренувати інтерактивні 3D флеш-картки\n"
        "• 🔊 Слухати вимову кожного слова носієм мови\n"
        "• 🔍 Миттєво знаходити потрібні слова за пошуком чи фільтрами (A1, A2, B1)\n"
        "• 🧠 Проходити тренажерні тести прямо в Telegram!\n\n"
        "Натисніть кнопку нижче, щоб відкрити 👇"
    )
    keyboard = {
        "inline_keyboard": [
            [
                {"text": "🚀 Відкрити тренажер (WebApp)", "web_app": {"url": WEBAPP_URL}}
            ]
        ]
    }
    send_message(chat_id, text, reply_markup=keyboard)


# ==========================================================
# AI РЕПЕТИТОР ДЛЯ ДОКЛАДНИХ ВІДПОВІДЕЙ ТА ГОЛОСУ
# ==========================================================
def answer_user_query_with_ai(chat_id: int, user_query: str):
    """Відповідає на будь-яке запитання користувача через Gemini та надсилає озвучку."""
    if not ai_model:
        send_message(
            chat_id,
            "Вибачте, сервіс AI зараз тимчасово недоступний. "
            "Спробуйте команди /quiz, /word або відкрийте /webapp!"
        )
        return

    send_chat_action(chat_id, "typing")

    prompt = f"""
Ти — доброзичливий, терплячий та професійний персональний викладач німецької мови для українців, які перебувають у Німеччині.

Запитання користувача:
"{user_query}"

СУВОРІ ПРАВИЛА:
1. Мова твоєї відповіді — БЕЗДОГАННА, чиста, сучасна літературна українська мова.
   КАТЕГОРИЧНО заборонено вживати будь-який суржик чи русизми!
   (Обов'язково: 'решти не треба' замість 'здачі', 'лікарняний' замість 'больнічний', 'квиток' замість 'білет', 'готівка' замість 'налічка', 'наступний' замість 'слідуючий').
2. Структура відповіді:
   - Коротка, чітка і зрозуміла відповідь на суть запитання.
   - Німецькі слова та фрази виділяй жирним шрифтом, а транскрипцію українськими літерами подавай у квадратних дужках [ось так].
   - Наведи життєвий приклад речення або міні-діалогу (у контексті Німеччини: Bürgeramt, супермаркет, робота, лікар тощо).
   - Короткий практичний лайфхак або граматичне застереження.
3. Форматування: використовуй лише базові теги HTML (<b>, <i>, <code>). НЕ використовуй Markdown (ніяких зірочок **).
4. В самому кінці відповіді, окремим рядком напиши:
AUDIO_PHRASE: <найважливіша ключова німецька фраза або речення для озвучення>
"""

    try:
        response = ai_model.generate_content(prompt)
        full_text = response.text.strip()

        # Виділяємо AUDIO_PHRASE
        audio_to_speak = ""
        lines = full_text.splitlines()
        clean_lines = []

        for line in lines:
            if line.startswith("AUDIO_PHRASE:"):
                audio_to_speak = line.replace("AUDIO_PHRASE:", "").strip()
            else:
                clean_lines.append(line)

        final_text = "\n".join(clean_lines).strip()
        final_text = sanitize_ukrainian_text(final_text)

        # Надсилаємо текстову відповідь
        send_message(chat_id, final_text, reply_markup=get_quick_reply_keyboard())

        # Якщо знайдено фразу для аудіо — генеруємо німецьку вимову
        if audio_to_speak:
            send_chat_action(chat_id, "record_voice")
            send_voice_audio(chat_id, audio_to_speak, caption=f"🗣 <b>Вимова:</b> <i>{audio_to_speak}</i>")

    except Exception as e:
        logger.error(f"Помилка генерації відповіді Gemini: {e}")
        error_msg = (
            "Вибачте, виникла невелика затримка у зв'язку з AI. "
            "Спробуйте переформулювати запитання або скористайтеся швидкими командами: /quiz, /word або /letter."
        )
        send_message(chat_id, error_msg, reply_markup=get_main_keyboard())


# ==========================================================
# ОБРОБКА ВХІДНИХ ОНОВЛЕНЬ TELEGRAM (LONG POLLING)
# ==========================================================
def process_update(update: dict):
    """Маршрутизує та обробляє одне вхідне оновлення від Telegram."""
    # 1. Обробка натискання інлайн-кнопок (Callback queries)
    if "callback_query" in update:
        cq = update["callback_query"]
        cq_id = cq.get("id")
        from_user = cq.get("from", {})
        chat_id = cq.get("message", {}).get("chat", {}).get("id")
        data = cq.get("data", "")

        # Відповідаємо на callback_query, щоб зняти індикатор завантаження
        tg_request("answerCallbackQuery", {"callback_query_id": cq_id})

        if not chat_id:
            return

        if data == "cmd_quiz":
            handle_quiz_command(chat_id)
        elif data == "cmd_word":
            handle_word_command(chat_id)
        elif data == "cmd_letter":
            handle_letter_command(chat_id)
        elif data == "cmd_menu":
            handle_start(chat_id, from_user.get("first_name", "друже"))
        return

    # 2. Обробка звичайних текстових повідомлень
    if "message" in update:
        msg = update["message"]
        chat = msg.get("chat", {})
        chat_id = chat.get("id")
        text = msg.get("text", "").strip()
        user_first_name = msg.get("from", {}).get("first_name", "друже")

        if not chat_id or not text:
            return

        # Лише приватні повідомлення з користувачами
        if chat.get("type") != "private":
            return

        logger.info(f"Отримано повідомлення від {chat_id} ({user_first_name}): '{text[:60]}'")

        # Обробка команд
        if text.startswith("/start") or text.startswith("/help") or text.startswith("/menu"):
            handle_start(chat_id, user_first_name)
        elif text.startswith("/quiz"):
            handle_quiz_command(chat_id)
        elif text.startswith("/word"):
            handle_word_command(chat_id)
        elif text.startswith("/letter"):
            handle_letter_command(chat_id)
        elif text.startswith("/webapp"):
            handle_webapp_command(chat_id)
        else:
            # Будь-яке текстове запитання -> персональний AI репетитор
            answer_user_query_with_ai(chat_id, text)


# ==========================================================
# ГОЛОВНИЙ ЦИКЛ LONG POLLING
# ==========================================================
is_running = True

def signal_handler(signum, frame):
    """Обробник сигналів зупинки."""
    global is_running
    logger.info("Отримано сигнал зупинки. Завершуємо роботу...")
    is_running = False

def run_tutor_bot():
    """Запускає long-polling цикл бота-репетитора."""
    global is_running
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    logger.info("Запуск Telegram AI Tutor Bot (@DeLernenUaBot)...")
    setup_bot_interface()

    last_offset = 0

    while is_running:
        try:
            params = {
                "offset": last_offset,
                "timeout": 20,
                "allowed_updates": json.dumps(["message", "callback_query"])
            }
            resp = requests.get(f"{TELEGRAM_API}/getUpdates", params=params, timeout=30)

            if not resp.ok:
                logger.warning(f"Помилка getUpdates: {resp.status_code} — очікування 5 секунд...")
                time.sleep(5)
                continue

            data = resp.json()
            if not data.get("ok"):
                logger.warning(f"Неуспішна відповідь: {data}")
                time.sleep(5)
                continue

            updates = data.get("result", [])
            for u in updates:
                last_offset = u["update_id"] + 1
                try:
                    process_update(u)
                except Exception as ex:
                    logger.error(f"Помилка обробки оновлення {u.get('update_id')}: {ex}")

        except requests.exceptions.RequestException as net_err:
            logger.warning(f"Мережевий збій (getUpdates): {net_err}. Повтор за 5 секунд...")
            time.sleep(5)
        except Exception as e:
            logger.error(f"Непередбачена помилка в циклі: {e}")
            time.sleep(3)

    logger.info("AI Tutor Bot успішно зупинено.")


if __name__ == "__main__":
    run_tutor_bot()
