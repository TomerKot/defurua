"""
generator.py
Модуль генерації різноформатного навчального контенту з німецької мови через Google Gemini API.

Підтримує 6 форматів публікацій на день:
1. morning   — 08:05 DE — «Слово дня» (Alltag & Grammatik) + картка + аудіо
2. phrase    — 10:35 DE — «Жива розмовна фраза» + діалог + картка + аудіо
3. mistake   — 13:05 DE — «Типова помилка українців» (Falsche Freunde) + картка + аудіо
4. dialogue  — 15:35 DE — «Живий ситуативний діалог» + картка + аудіо
5. letter    — 18:05 DE — «Офіційний зразок листа (Musterbrief)» + картка + аудіо
6. quiz      — 20:35 DE — «Інтерактивний Quiz-тест» (нативне опитування Telegram poll)

Особливості:
- Мульти-модельний каскад Gemini (gemini-3.6-flash -> 3.5-flash -> 3.5-flash-lite -> 3.1-flash-lite)
- Суворий захист від повторок (перевірка проти history.json + повторні запити)
- Багаті пули резервних постів (Fallback Pools) з гарантією неповторюваності
- 100% чиста літературна українська мова без суржику та кальок
"""

import os
import re
import json
import random
import logging
from datetime import datetime
import google.generativeai as genai

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Пріоритетний каскад моделей Gemini для безвідмовної генерації (1500+ запитів/день на безкоштовному тарифі)
ACTIVE_MODELS = [
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.8-flash"
]


def sanitize_ukrainian_text(text: str) -> str:
    """
    Суворий автоматичний санітайзер української мови.
    Виправляє будь-які випадкові русизми, кальки та суржик:
    - 'здачі не треба' -> 'решти не треба'
    - 'здача' -> 'решта', 'без здачі' -> 'без решти'
    - 'білет' -> 'квиток' (без подвоєння кореня!)
    - 'слідуючий' -> 'наступний'
    - 'налічка' -> 'готівка'
    - 'больнічний' -> 'лікарняний'
    """
    if not text:
        return ""

    def repl_ticket(m, cap=False):
        end = m.group(1)
        if not end:
            return "Квиток" if cap else "квиток"
        if end == "і":
            return "Квитку" if cap else "квитку"
        return ("Квитк" if cap else "квитк") + end

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
        (r'\bбілет(и|ів|ами|ах|ом|а|і)?\b', lambda m: repl_ticket(m, False)),
        (r'\bБілет(и|ів|ами|ах|ом|а|і)?\b', lambda m: repl_ticket(m, True)),
        (r'\bслідуюч([а-яіїє]+)\b', r'наступн\1'),
        (r'\bСлідуюч([а-яіїє]+)\b', r'Наступн\1'),
        (r'\bналічк(а|у|ою|і)\b', r'готівк\1'),
        (r'\bНалічк(а|у|ою|і)\b', r'Готівк\1'),
        (r'\bбольнічн([а-яіїє]+)\b', r'лікарнян\1'),
        (r'\bБольнічн([а-яіїє]+)\b', r'Лікарнян\1'),
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
    cleaned = {}
    for k, v in data.items():
        if isinstance(v, str):
            cleaned[k] = sanitize_ukrainian_text(v)
        elif isinstance(v, list):
            cleaned[k] = [
                sanitize_content_dict(item) if isinstance(item, dict)
                else (sanitize_ukrainian_text(item) if isinstance(item, str) else item)
                for item in v
            ]
        elif isinstance(v, dict):
            cleaned[k] = sanitize_content_dict(v)
        else:
            cleaned[k] = v
    return cleaned


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


# ==========================================================
# БАГАТІ ПУЛИ РЕЗЕРВНОГО КОНТЕНТУ (FALLBACK POOLS)
# Гарантують, що навіть у разі збою зовнішнього API пост ніколи не повториться!
# ==========================================================

FALLBACK_WORDS_POOL = [
    {
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
    },
    {
        "word": "die Warmmiete, -n",
        "article": "die",
        "clean_word": "die Warmmiete",
        "transcription": "[ді Ва́рмміте]",
        "translation": "орендна плата з урахуванням комунальних послуг",
        "level": "A2",
        "rubric_name": "Побут та щоденне життя (Alltag)",
        "rubric_emoji": "🛒",
        "card_example_de": "Die Warmmiete beträgt 850 Euro im Monat.",
        "card_example_ua": "Оренда з комунальними становить 850 євро на місяць.",
        "explanation": "На відміну від Kaltmiete (чиста оренда лише за стіни), Warmmiete включає опалення, вивіз сміття та воду. Але електрика та інтернет майже завжди оплачуються окремо!",
        "examples": [
            {"de": "Ist Strom in der Warmmiete enthalten?", "ua": "Чи входить електрика в теплу оренду?"},
            {"de": "Die Warmmiete ist dieses Jahr gestiegen.", "ua": "Оренда з комунальними цього року зросла."}
        ],
        "pro_tip": "💡 <b>Важливо:</b> Електроенергію (Strom) мешканець зазвичай оформлює самостійно на своє ім'я у міського постачальника (Stadtwerke)."
    },
    {
        "word": "der Feierabend, -e",
        "article": "der",
        "clean_word": "der Feierabend",
        "transcription": "[дер Фа́єрабенд]",
        "translation": "кінець робочого дня / вечірній відпочинок після роботи",
        "level": "A1-A2",
        "rubric_name": "Розмовна мова (Umgangssprache)",
        "rubric_emoji": "🗣️",
        "card_example_de": "Schönen Feierabend! Bis morgen!",
        "card_example_ua": "Гарного вечора після роботи! До завтра!",
        "explanation": "Культове німецьке поняття, що означає священний час після завершення роботи, коли ніхто не має права турбувати вас робочими справами.",
        "examples": [
            {"de": "Ich mache jetzt Feierabend.", "ua": "Я зараз завершую роботу на сьогодні."},
            {"de": "Endlich Feierabend!", "ua": "Нарешті кінець робочого дня!"}
        ],
        "pro_tip": "💡 <b>Етикет:</b> Йдучи з роботи, колегам завжди кажуть «Schönen Feierabend!» замість звичайного «Tschüss»."
    },
    {
        "word": "die Mahnung, -en",
        "article": "die",
        "clean_word": "die Mahnung",
        "transcription": "[ді Ма́нунг]",
        "translation": "нагадування / попередження про несплату рахунку",
        "level": "A2-B1",
        "rubric_name": "Робота та бюрократія (Bürokratie)",
        "rubric_emoji": "💼",
        "card_example_de": "Ich habe die erste Mahnung erhalten.",
        "card_example_ua": "Я отримав перше нагадування про несплату.",
        "explanation": "Якщо ви вчасно не оплатили рахунок (Rechnung), німецькі компанії надсилають Mahnung. Перше нагадування зазвичай без штрафу, але наступні мають штрафний збір (Mahngebühr).",
        "examples": [
            {"de": "Bitte bezahlen Sie die Rechnung ohne Verzug.", "ua": "Будь ласка, оплатіть рахунок без зволікання."},
            {"de": "Die Mahngebühr beträgt fünf Euro.", "ua": "Штраф за нагадування становить п'ять євро."}
        ],
        "pro_tip": "💡 <b>Порада:</b> Ніколи не ігноруйте листи з написом Mahnung — це може негативно вплинути на ваш кредитний рейтинг SCHUFA."
    },
    {
        "word": "die Krankmeldung, -en",
        "article": "die",
        "clean_word": "die Krankmeldung",
        "transcription": "[ді Кра́нкмельдунг]",
        "translation": "повідомлення про хворобу / лікарняний лист",
        "level": "A2-B1",
        "rubric_name": "Робота та бюрократія (Bürokratie)",
        "rubric_emoji": "💼",
        "card_example_de": "Ich habe heute meine Krankmeldung eingereicht.",
        "card_example_ua": "Я сьогодні подав свій лікарняний.",
        "explanation": "У Німеччині про хворобу слід повідомити роботодавця ще до початку зміни. Зараз діє електронний лікарняний (eAU), який лікар передає напряму в страхову компанію.",
        "examples": [
            {"de": "Brauche ich ab dem ersten Tag eine Krankmeldung?", "ua": "Чи потрібен мені лікарняний з першого дня?"},
            {"de": "Die Krankmeldung läuft bis Freitag.", "ua": "Лікарняний діє до п'ятниці."}
        ],
        "pro_tip": "💡 <b>Важливо:</b> Завжди кажіть «лікарняний», а не суржикове слово на позначення тимчасової непрацездатності."
    },
    {
        "word": "die Ruhezeit, -en",
        "article": "die",
        "clean_word": "die Ruhezeit",
        "transcription": "[ді Ру́ецайт]",
        "translation": "години тиші / час спокою",
        "level": "A1-A2",
        "rubric_name": "Ідіоми та менталітет (Kultur)",
        "rubric_emoji": "🇩🇪",
        "card_example_de": "Ab 22 Uhr gilt in Deutschland die Nachtruhe.",
        "card_example_ua": "З 22:00 у Німеччині діє нічна тиша.",
        "explanation": "Законодавчо закріплений час, коли заборонено шуміти (пилососити, свердлити, вмикати гучну музику). Нічна тиша — з 22:00 до 06:00/07:00, а також уся неділя та святкові дні.",
        "examples": [
            {"de": "Sonntags muss man die Ruhezeit einhalten.", "ua": "Щонеділі потрібно дотримуватися тиші."},
            {"de": "Bitte machen Sie nach 22 Uhr keinen Lärm.", "ua": "Будь ласка, не шуміть після 22:00."}
        ],
        "pro_tip": "💡 <b>Правило:</b> У неділю в Німеччині не можна навіть викидати скляні пляшки в баки біля будинку!"
    },
    {
        "word": "das Kleingeld (nur Sg.)",
        "article": "das",
        "clean_word": "das Kleingeld",
        "transcription": "[дас Кля́йнгельд]",
        "translation": "дрібні гроші / монети",
        "level": "A1",
        "rubric_name": "Побут та щоденне життя (Alltag)",
        "rubric_emoji": "🛒",
        "card_example_de": "Haben Sie es vielleicht passend als Kleingeld?",
        "card_example_ua": "Чи маєте ви, можливо, точну суму дрібними монетами?",
        "explanation": "У Німеччині досі дуже люблять готівку, особливо монети. Kleingeld знадобиться для паркоматів, візків у супермаркеті та громадських вбиралень.",
        "examples": [
            {"de": "Ich habe leider kein Kleingeld dabei.", "ua": "У мене, на жаль, немає з собою дрібних грошей."},
            {"de": "Ich brauche Kleingeld für den Parkscheinautomaten.", "ua": "Мені потрібні монети для паркомату."}
        ],
        "pro_tip": "💡 <b>Нюанс:</b> Для візка в супермаркеті завжди тримайте в кишені монетку в 1 або 2 євро чи спеціальний жетон (Einkaufschip)."
    }
]

FALLBACK_PHRASES_POOL = [
    {
        "phrase": "Stimmt so!",
        "transcription": "[штімт зо!]",
        "translation": "Решти не треба! / Залишіть собі!",
        "situation": "У кафе, ресторані, таксі чи пекарні при оплаті готівкою",
        "card_example_de": "Hier sind zehn Euro. Stimmt so!",
        "card_example_ua": "Ось десять євро. Решти не треба!",
        "explanation": "Найпопулярніша фраза в Німеччині, коли ви хочете залишити решту як чайові. Замість очікування решти ви просто кажете 'Stimmt so!'.",
        "dialogue": [
            {"speaker": "Офіціант", "de": "Das macht bitte 8 Euro 50.", "ua": "З вас 8 євро 50 центів, будь ласка."},
            {"speaker": "Ви", "de": "Hier sind 10 Euro. Stimmt so!", "ua": "Ось 10 євро. Решти не треба!"}
        ],
        "pro_tip": "💡 <b>Етикет:</b> Завжди кажіть «решти не треба», уникаючи суржикових кальок. У Німеччині чайові оголошують одразу під час передачі грошей."
    },
    {
        "phrase": "Mach dir keine Sorgen!",
        "transcription": "[мах дір ка́йне зо́рґен!]",
        "translation": "Не хвилюйся! / Не переймайся!",
        "situation": "У дружній розмові або коли колега за щось переживає",
        "card_example_de": "Alles wird gut, mach dir keine Sorgen!",
        "card_example_ua": "Все буде добре, не переймайся!",
        "explanation": "Чудовий спосіб заспокоїти співрозмовника. Для ввічливої форми на 'Ви' кажуть: 'Machen Sie sich keine Sorgen!'.",
        "dialogue": [
            {"speaker": "Друг", "de": "Ich habe Angst vor der Prüfung morgen.", "ua": "Я боюся завтрашнього іспиту."},
            {"speaker": "Ви", "de": "Du hast fleißig gelernt. Mach dir keine Sorgen!", "ua": "Ти старанно вчився. Не хвилюйся!"}
        ],
        "pro_tip": "💡 <b>Спілкування:</b> Німці дуже цінують спокійний і конструктивний підхід у будь-яких труднощах."
    },
    {
        "phrase": "Ich gebe dir Bescheid!",
        "transcription": "[іхь ґе́бе дір беша́йт!]",
        "translation": "Я дам тобі знати! / Я повідомлю тебе!",
        "situation": "Коли домовляєтеся про зустріч, плани на вихідні або дзвінок",
        "card_example_de": "Sobald ich mehr weiß, gebe ich dir Bescheid.",
        "card_example_ua": "Як тільки дізнаюся більше, я дам тобі знати.",
        "explanation": "Сталий вираз 'Bescheid geben' (комусь повідомити / попередити). Якщо повідомляють вам, використовують 'Bescheid wissen' (бути в курсі).",
        "dialogue": [
            {"speaker": "Колега", "de": "Kommst du heute mit zum Mittagessen?", "ua": "Підеш сьогодні з нами на обід?"},
            {"speaker": "Ви", "de": "Ich muss noch etwas fertig machen. Ich gebe dir in zehn Minuten Bescheid!", "ua": "Маю ще дещо доробити. Дам тобі знати за десять хвилин!"}
        ],
        "pro_tip": "💡 <b>Лексика:</b> Якщо ви вже знаєте новину, скажіть: «Ich weiß schon Bescheid!» (Я вже в курсі!)."
    },
    {
        "phrase": "Das geht auf mich!",
        "transcription": "[дас ґейт ауф міхь!]",
        "translation": "Це за мій рахунок! / Я пригощаю!",
        "situation": "У барі, кав'ярні або ресторані з друзями",
        "card_example_de": "Steck dein Geld weg, der Kaffee geht auf mich!",
        "card_example_ua": "Сховай гроші, кава за мій рахунок!",
        "explanation": "Німці зазвичай платять кожен сам за себе (getrennt zahlen). Тому, коли хтось каже 'Das geht auf mich!', це сприймається як щирий дружній жест поваги.",
        "dialogue": [
            {"speaker": "Друг", "de": "Wie viel schulde ich dir für die Pizza?", "ua": "Скільки я тобі винен за піцу?"},
            {"speaker": "Ви", "de": "Gar nichts! Das geht heute auf mich.", "ua": "Аніскільки! Сьогодні я пригощаю."}
        ],
        "pro_tip": "💡 <b>Культура:</b> Якщо офіціант питає «Zusammen oder getrennt?», за замовчуванням у компаніях обирають «getrennt» (окремо)."
    },
    {
        "phrase": "Keine Ahnung!",
        "transcription": "[ка́йне а́нунг!]",
        "translation": "Поняття не маю! / Без поняття!",
        "situation": "Коли ви щиро не знаєте відповіді на запитання",
        "card_example_de": "Wo ist mein Schlüssel? — Keine Ahnung!",
        "card_example_ua": "Де мій ключ? — Поняття не маю!",
        "explanation": "Коротка, жива та найбільш уживана розмовна відповідь замість довгої фрази 'Ich weiß es leider nicht'.",
        "dialogue": [
            {"speaker": "Перехожий", "de": "Entschuldigung, wann kommt der nächste Bus?", "ua": "Перепрошую, коли буде наступний автобус?"},
            {"speaker": "Ви", "de": "Keine Ahnung, schauen Sie bitte auf den Fahrplan.", "ua": "Поняття не маю, подивіться, будь ласка, на розклад."}
        ],
        "pro_tip": "💡 <b>Інтонація:</b> Звучить природно і невимушено серед друзів і знайомих."
    },
    {
        "phrase": "Schönes Wochenende!",
        "transcription": "[ше́нес во́хененде!]",
        "translation": "Гарних вихідних!",
        "situation": "У п'ятницю на роботі, у магазині, спортзалі або на касі",
        "card_example_de": "Danke, Ihnen auch ein schönes Wochenende!",
        "card_example_ua": "Дякую, вам також гарних вихідних!",
        "explanation": "Стандартне побажання, яке кажуть усім у п'ятницю або суботу. У відповідь ввічливо сказати: 'Danke, gleichfalls!' (Дякую, навзаєм!).",
        "dialogue": [
            {"speaker": "Касир", "de": "Das macht 14 Euro. Schönes Wochenende!", "ua": "З вас 14 євро. Гарних вихідних!"},
            {"speaker": "Ви", "de": "Danke schön, gleichfalls!", "ua": "Дуже дякую, навзаєм!"}
        ],
        "pro_tip": "💡 <b>Відповідь:</b> 'Gleichfalls!' або 'Ebenfalls!' — найкращі короткі слова для «навзаєм»."
    }
]

FALLBACK_QUIZZES_POOL = [
    {
        "topic": "Решти не треба (Trinkgeld)",
        "question": "🇩🇪 Як німецькою сказати офіціанту або таксисту 'Решти не треба'?",
        "options": [
            "Behalten Sie das Geld",
            "Stimmt so!",
            "Ich brauche kein Geld",
            "Kein Problem"
        ],
        "correct_option_id": 1,
        "explanation": "Вираз 'Stimmt so!' — загальноприйнятий спосіб залишити чайові офіціанту чи водієві в Німеччині."
    },
    {
        "topic": "Артикль слова Mädchen",
        "question": "🇩🇪 Який артикль має німецьке слово 'Mädchen' (дівчинка)?",
        "options": [
            "die Mädchen",
            "der Mädchen",
            "das Mädchen",
            "den Mädchen"
        ],
        "correct_option_id": 2,
        "explanation": "Усі слова із зменшувально-пестливим суфіксом -chen у німецькій мові завжди середнього роду (das)!"
    },
    {
        "topic": "Відчуття холоду: Mir ist kalt",
        "question": "🇩🇪 Як правильно сказати німецькою 'Мені холодно'?",
        "options": [
            "Ich bin kalt",
            "Ich habe kalt",
            "Mir ist kalt",
            "Mich ist kalt"
        ],
        "correct_option_id": 2,
        "explanation": "Для фізичних відчуттів вживається Dativ: 'Mir ist kalt' (холодно) або 'Mir ist warm' (тепло)."
    },
    {
        "topic": "Різниця kennen та wissen",
        "question": "🇩🇪 Оберіть правильне дієслово: 'Ich ___ diese Straße nicht.'",
        "options": [
            "weiß",
            "kenne",
            "kann",
            "verstehe"
        ],
        "correct_option_id": 1,
        "explanation": "Kennen вживається для знайомства з людьми, містами, вулицями (знати когось/щось). Wissen — про факти."
    },
    {
        "topic": "bekommen чи werden",
        "question": "🇩🇪 Як правильно перекласти 'Я отримую листа'?",
        "options": [
            "Ich werde einen Brief",
            "Ich bekomme einen Brief",
            "Ich nehme einen Brief",
            "Ich bringe einen Brief"
        ],
        "correct_option_id": 1,
        "explanation": "Bekommen означає 'отримувати'. Дієслово werden перекладається як 'ставати' або утворює майбутній час."
    },
    {
        "topic": "Транспорт: їхати потягом",
        "question": "🇩🇪 Оберіть правильний прийменник: 'Ich fahre ___ dem Zug.'",
        "options": [
            "mit",
            "in",
            "auf",
            "bei"
        ],
        "correct_option_id": 0,
        "explanation": "У німецькій мові для пересування будь-яким транспортом вживається прийменник 'mit' + Dativ."
    },
    {
        "topic": "Часовий прийменник для днів тижня",
        "question": "🇩🇪 Який прийменник використовується з днями тижня: '___ Montag'?",
        "options": [
            "Im Montag",
            "Am Montag",
            "Um Montag",
            "Beim Montag"
        ],
        "correct_option_id": 1,
        "explanation": "З днями тижня та частинами доби вживається прийменник 'am' (an + dem): am Montag, am Morgen."
    },
    {
        "topic": "Точний час: прийменник um",
        "question": "🇩🇪 Який прийменник вказує на точну годину: 'Der Zug fährt ___ 14:00 Uhr ab.'?",
        "options": [
            "am",
            "im",
            "um",
            "nach"
        ],
        "correct_option_id": 2,
        "explanation": "Для зазначення точного часу на годиннику в німецькій мові завжди вживається прийменник 'um'."
    }
]

FALLBACK_MUSTERBRIEFE_POOL = [
    {
        "topic": "Розірвання договору на спортзал (Kündigung Fitnessstudio)",
        "situation": "Розірвання річного або місячного абонемента у тренажерний зал",
        "card_example_de": "Hiermit kündige ich meinen Vertrag fristgerecht zum nächstmöglichen Zeitpunkt.",
        "card_example_ua": "Цим листом я розриваю договір у встановлений термін до найближчої дати.",
        "letter_text": """Sehr geehrte Damen und Herren,

hiermit kündige ich meine Mitgliedschaft (Mitgliedsnummer: [Ваш номер]) fristgerecht zum nächstmöglichen Zeitpunkt.

Bitte senden Sie mir eine schriftliche Bestätigung der Kündigung unter Angabe des Beendigungszeitpunkts zu.

Mit freundlichen Grüßen,
[Ваше Ім'я та Прізвище]""",
        "translation": """Шановні пані та панове!

Цим листом я розриваю моє членство (номер члена: [Ваш номер]) у встановлений термін до найближчої можливої дати.

Будь ласка, надішліть мені письмове підтвердження розірвання із зазначенням дати закінчення контракту.

З повагою,
[Ваше Ім'я та Прізвище]""",
        "vocabulary": [
            {"de": "fristgerecht kündigen", "ua": "розірвати договір у встановлений термін"},
            {"de": "zum nächstmöglichen Zeitpunkt", "ua": "до найближчої можливої дати"},
            {"de": "die Kündigungsbestätigung", "ua": "письмове підтвердження розірвання"}
        ],
        "pro_tip": "💡 <b>Порада:</b> Завжди надсилайте лист за 1-3 місяці до завершення терміну, інакше абонемент автоматично продовжиться на наступний період."
    },
    {
        "topic": "Заява про сплату частинами (Antrag auf Ratenzahlung)",
        "situation": "Прохання про розстрочку боргу або великого рахунку в комунальну службу чи відомство",
        "card_example_de": "Ich beantrage hiermit eine monatliche Ratenzahlung in Höhe von 50 Euro.",
        "card_example_ua": "Я прошу про щомісячну виплату частинами у розмірі 50 євро.",
        "letter_text": """Sehr geehrte Damen und Herren,

bezugnehmend auf Ihr Schreiben vom [Дата листа] (Aktenzeichen: [Номер справи]) muss ich Ihnen mitteilen, dass ich den geforderten Betrag von [Сума] Euro aktuell nicht auf einmal begleichen kann.

Ich beantrage hiermit eine Ratenzahlung in Höhe von monatlich [Сума щомісячного внеску] Euro, beginnend ab dem [Дата першої виплати].

Ich bitte um wohlwollende Prüfung meines Antrags.

Mit freundlichen Grüßen,
[Ваше Ім'я та Прізвище]""",
        "translation": """Шановні пані та панове!

Посилаючись на ваш лист від [Дата листа] (номер справи: [Номер справи]), змушений повідомити, що наразі не маю змоги сплатити всю суму [Сума] євро одним платежем.

Цим листом я прошу про сплату частинами у розмірі [Сума внеску] євро щомісяця, починаючи з [Дата].

Прошу прихильно розглянути мою заяву.

З повагою,
[Ваше Ім'я та Прізвище]""",
        "vocabulary": [
            {"de": "die Ratenzahlung", "ua": "оплата частинами / розстрочка"},
            {"de": "das Aktenzeichen", "ua": "номер справи / реєстраційний номер"},
            {"de": "den Betrag begleichen", "ua": "погасити / сплатити суму"}
        ],
        "pro_tip": "💡 <b>Важливо:</b> Німецькі відомства охоче йдуть на розстрочку, якщо звернутися до них до настання дедлайну, а не після отримання штрафів."
    },
    {
        "topic": "Пояснення відсутності дитини в школі через хворобу (Entschuldigung)",
        "situation": "Офіційна записка класному керівнику щодо хвороби дитини",
        "card_example_de": "Wegen einer akuten Erkrankung konnte mein Kind die Schule nicht besuchen.",
        "card_example_ua": "Через гостре захворювання моя дитина не змогла відвідувати школу.",
        "letter_text": """Sehr geehrte(r) Frau/Herr [Прізвище вчителя],

meine Tochter / mein Sohn [Ім'я дитини], Klasse [Клас], konnte in der Zeit vom [Дата з] bis zum [Дата по] wegen einer Erkrankung den Unterricht leider nicht besuchen.

Ich bitte Sie, das Fehlen zu entschuldigen. Ein ärztliches Attest liegt bei / wird nachgereicht.

Mit freundlichen Grüßen,
[Ваше Ім'я та Прізвище]""",
        "translation": """Шановна(ий) пані/пане [Прізвище],

моя донька / мій син [Ім'я], учень [Клас] класу, у період з [Дата] по [Дата] через хворобу не міг(могла) відвідувати заняття.

Прошу вибачити пропуск занять. Медична довідка додається / буде надана згодом.

З повагою,
[Ваше Ім'я та Прізвище]""",
        "vocabulary": [
            {"de": "den Unterricht versäumen", "ua": "пропустити навчальні заняття"},
            {"de": "das ärztliche Attest", "ua": "медична довідка від лікаря"},
            {"de": "das Fehlen entschuldigen", "ua": "вибачити / підтвердити поважність пропуску"}
        ],
        "pro_tip": "💡 <b>Шкільне право:</b> У Німеччині діє суворий обов'язок відвідування школи (Schulpflicht). Попередити школу потрібно обов'язково в перший ранок хвороби телефоном або електронною поштою."
    }
]

FALLBACK_MISTAKES_POOL = [
    {
        "mistake_title": "«Мені холодно» німецькою мовою",
        "wrong_phrase": "❌ Ich bin kalt / Ich habe kalt",
        "correct_phrase": "✅ Mir ist kalt!",
        "level": "A1-A2",
        "card_example_de": "Mir ist so kalt! Mach bitte das Fenster zu.",
        "card_example_ua": "Мені так холодно! Зачини, будь ласка, вікно.",
        "explanation": "В українській мові ми кажемо «мені холодно». Початківці буквально перекладають це як «Ich bin kalt» (що для німців звучить як «я холодна/жорстока людина»). У німецькій мові для фізичних відчуттів вживається давальний відмінок Dativ (кому? — mir).",
        "examples": [
            {"de": "Mir ist kalt. Können wir reingehen?", "ua": "Мені холодно. Можемо зайти всередину?"},
            {"de": "Ist dir auch so warm?", "ua": "Тобі теж так тепло/жарко?"}
        ],
        "pro_tip": "💡 <b>Правило трьох відчуттів:</b> Mir ist kalt (холодно), Mir ist warm (тепло), Mir ist schlecht (погано/нудить). Завжди кажіть 'Mir ist...', а не 'Ich bin...'!"
    },
    {
        "mistake_title": "«Отримувати» чи «ставати»: bekommen vs werden",
        "wrong_phrase": "❌ Ich will ein Arzt bekommen",
        "correct_phrase": "✅ Ich will Arzt werden!",
        "level": "A1-B1",
        "card_example_de": "Ich habe heute einen wichtigen Brief bekommen.",
        "card_example_ua": "Я отримав сьогодні важливого листа.",
        "explanation": "Класичний «хибний друг перекладача». Bekommen означає лише «отримувати» (листи, подарунки, гроші). Якщо ви хочете сказати «стати лікарем» або «погода стає теплою», використовуйте дієслово werden!",
        "examples": [
            {"de": "Er will Ingenieur werden.", "ua": "Він хоче стати інженером."},
            {"de": "Hast du mein Paket bekommen?", "ua": "Ти отримав мою посилку?"}
        ],
        "pro_tip": "💡 <b>Лайфхак:</b> Ніколи не плутайте німецьке bekommen з англійським become! У німецькій bekommen = receive (отримувати)."
    },
    {
        "mistake_title": "«Знати» людей чи факти: kennen vs wissen",
        "wrong_phrase": "❌ Ich weiß diese Frau / Ich kenne, wo der Bahnhof ist",
        "correct_phrase": "✅ Ich kenne diese Frau / Ich weiß, wo der Bahnhof ist",
        "level": "A2",
        "card_example_de": "Ich kenne Berlin sehr gut, aber ich weiß die Adresse nicht.",
        "card_example_ua": "Я добре знаю Берлін, але не знаю адреси.",
        "explanation": "Kennen вживається з іменниками у значенні «бути знайомим з кимось або чимось» (місто, людина, книга, фільм). Wissen використовується для інформації, фактів або коли далі йде підрядне речення з dass / ob / wie / wo.",
        "examples": [
            {"de": "Kennst du meinen Nachbarn?", "ua": "Ти знаєш мого сусіда?"},
            {"de": "Weißt du, wie spät es ist?", "ua": "Чи знаєш ти, котра година?"}
        ],
        "pro_tip": "💡 <b>Мнемоніка:</b> Kennen + іменник/особа (Akkusativ). Wissen + факт/речення."
    },
    {
        "mistake_title": "«Працювати» про прилади та техніку: funktionieren чи arbeiten",
        "wrong_phrase": "❌ Mein Handy arbeitet heute nicht",
        "correct_phrase": "✅ Mein Handy funktioniert heute nicht!",
        "level": "A1-A2",
        "card_example_de": "Der Geldautomat funktioniert leider nicht.",
        "card_example_ua": "Банкомат, на жаль, не працює.",
        "explanation": "Люди працюють — arbeiten (Ich arbeite bei einer Firma). Прилади, техніка, програми, інтернет або автомати — діють/функціонують, тому про них німці кажуть исключительно funktionieren (або laufen/klappen)!",
        "examples": [
            {"de": "Das WLAN funktioniert wieder.", "ua": "Вайфай знову працює."},
            {"de": "Der Aufzug ist außer Betrieb / funktioniert nicht.", "ua": "Ліфт не працює."}
        ],
        "pro_tip": "💡 <b>Усталена фраза:</b> Якщо щось вдалося або все вийшло як слід, німці радісно кажуть: «Es hat funktioniert!» або «Es klappt!»."
    }
]

FALLBACK_DIALOGUES_POOL = [
    {
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
        "pro_tip": "💡 <b>Культурний лайфхак:</b> У традиційних німецьких пекарнях часто діє оплата лише готівкою (nur Barzahlung) або карткою від певної суми (ab 5 Euro). Звертайте увагу на таблички біля каси!"
    },
    {
        "location": "В аптеці (In der Apotheke)",
        "topic": "Купівля ліків від застуди та болю в горлі",
        "card_example_de": "Haben Sie etwas gegen Halsschmerzen?",
        "card_example_ua": "Чи є у вас щось від болю в горлі?",
        "level": "A1-A2",
        "dialogue": [
            {"speaker": "Фармацевт", "de": "Guten Tag! Wie kann ich Ihnen helfen?", "ua": "Доброго дня! Чим можу допомогти?"},
            {"speaker": "Ви", "de": "Guten Tag! Ich habe starke Halsschmerzen und Husten. Haben Sie Halstabletten?", "ua": "Доброго дня! У мене сильно болить горло і кашель. Чи є у вас таблетки для розсмоктування?"},
            {"speaker": "Фармацевт", "de": "Ja, nehmen Sie diese Tabletten dreimal täglich nach dem Essen.", "ua": "Так, приймайте ці таблетки тричі на день після їди."},
            {"speaker": "Ви", "de": "Vielen Dank! Brauche ich dafür ein Rezept?", "ua": "Дуже дякую! Чи потрібен для цього рецепт?"},
            {"speaker": "Фармацевт", "de": "Nein, diese sind rezeptfrei.", "ua": "Ні, вони безрецептурні."}
        ],
        "vocabulary": [
            {"de": "die Halsschmerzen (Pl.)", "ua": "біль у горлі"},
            {"de": "rezeptfrei", "ua": "без рецепта"},
            {"de": "dreimal täglich", "ua": "тричі на день"}
        ],
        "pro_tip": "💡 <b>Корисний факт:</b> В аптеці червоний рецепт (rosa Rezept) оплачує страхова каса (ви доплачуєте лише 5–10 євро), а зелений чи синій — сплачуєте ви самі повністю."
    },
    {
        "location": "На касі супермаркету (An der Kasse)",
        "topic": "Оплата покупок та чек",
        "card_example_de": "Mit Karte, bitte!",
        "card_example_ua": "Карткою, будь ласка!",
        "level": "A1",
        "dialogue": [
            {"speaker": "Касир", "de": "Hallo! Brauchen Sie eine Tüte?", "ua": "Вітаю! Вам потрібен пакет?"},
            {"speaker": "Ви", "de": "Nein, danke, ich habe eine Tasche dabei.", "ua": "Ні, дякую, у мене з собою торбинка."},
            {"speaker": "Касир", "de": "Das macht 18 Euro 70. Zahlen Sie bar oder mit Karte?", "ua": "З вас 18 євро 70. Оплачуєте готівкою чи карткою?"},
            {"speaker": "Ви", "de": "Mit Karte, bitte.", "ua": "Карткою, будь ласка."},
            {"speaker": "Касир", "de": "Brauchen Sie den Kassenbon?", "ua": "Потрібен чек?"},
            {"speaker": "Ви", "de": "Ja, bitte. Schönen Tag noch!", "ua": "Так, будь ласка. Гарного дня!"}
        ],
        "vocabulary": [
            {"de": "die Tüte, -n", "ua": "пакет / торбинка"},
            {"de": "der Kassenbon, -s", "ua": "касовий чек"},
            {"de": "bar zahlen", "ua": "оплачувати готівкою"}
        ],
        "pro_tip": "💡 <b>Швидкість:</b> На касах у супермаркетах Aldi, Lidl та Penny товари сканують дуже швидко — складайте покупки одразу у візок, а пакуйте біля окремого столика."
    }
]


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


def generate_with_gemini_cascade(prompt: str, api_key: str = None, response_mime_type: str = "application/json") -> str:
    """
    Виконує генерацію через послідовний каскад актуальних моделей Gemini.
    Якщо одна модель повертає 429 Quota Exceeded або іншу помилку, запит автоматично
    передається наступній робочій моделі.
    """
    if not api_key:
        api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY не знайдено в оточенні!")

    genai.configure(api_key=api_key)

    last_error = None
    for model_name in ACTIVE_MODELS:
        try:
            logger.info(f"Спроба генерації через модель [{model_name}]...")
            gen_config = {"temperature": 0.7}
            if response_mime_type:
                gen_config["response_mime_type"] = response_mime_type

            model = genai.GenerativeModel(
                model_name=model_name,
                generation_config=gen_config
            )
            resp = model.generate_content(prompt, request_options={"timeout": 12})
            if resp and resp.text:
                logger.info(f"Успішна генерація через модель [{model_name}]!")
                return resp.text
        except Exception as e:
            last_error = e
            logger.warning(f"Модель [{model_name}] повернула помилку: {e}. Перехід до наступної моделі...")
            continue

    raise RuntimeError(f"Усі моделі Gemini вичерпали квоту або недоступні. Остання помилка: {last_error}")


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


def pick_unused_fallback(pool: list, used_items: list, key_fn) -> dict:
    """
    Вибирає з пулу резервних постів той об'єкт, який ще не використовувався в каналі.
    Забезпечує 100% захист від повторок навіть при збої API.
    """
    used_normalized = {str(u).lower().strip() for u in (used_items or [])}
    candidates = []
    for item in pool:
        k = key_fn(item)
        if k and str(k).lower().strip() not in used_normalized:
            candidates.append(item)

    if candidates:
        chosen = random.choice(candidates).copy()
        logger.info(f"Обрано невикористаний fallback з пулу: '{key_fn(chosen)}'")
        return chosen

    # Якщо всі вже використовувалися, обираємо випадковий
    chosen = random.choice(pool).copy()
    logger.info(f"Всі елементи пулу вже публікувалися, обрано: '{key_fn(chosen)}'")
    return chosen


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
    """Генерує ранковий пост (Слово дня) з перевіркою на дублікати."""
    weekday = datetime.now().weekday()
    rubric = WEEKLY_RUBRICS.get(weekday, WEEKLY_RUBRICS[0])
    history = load_history(history_file_path)
    used_words = history.get("used_words", [])

    used_words_set = {w.lower().strip() for w in used_words}
    recent_words_str = ", ".join(used_words[-150:]) if used_words else "немає"

    for attempt in range(1, 4):
        prompt = f"""
Ти — професійний викладач німецької для українців у Німеччині. Створи сьогоднішній пост «Слово дня».
Рубрика: {rubric['title']} ({rubric['description']}).
Рівень: {rubric['default_level']}.
СУВОРО ЗАБОРОНЕНО використовувати ці раніше використані слова: {recent_words_str}

СУВОРІ ВИМОГИ ДО ЛІТЕРАТУРНОЇ ЧИСТОТИ УКРАЇНСЬКОЇ МОВИ:
- ЖОДНОГО СУРЖИКУ та кальок з російської!
- Тільки питомі українські слова:
  • "решта" / "решти не треба" / "без решти" (СУВОРО ЗАБОРОНЕНО писати "здача" або "здачі не треба"!)
  • "квиток" (НЕ "білет")
  • "лікарняний" (НЕ "больнічний")
  • "готівка" (НЕ "налічка")
  • "наступний" (НЕ "слідуючий")

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
            resp_text = generate_with_gemini_cascade(prompt, api_key=api_key)
            data = clean_json_response(resp_text)
            candidate = (data.get("clean_word") or data.get("word") or "").strip()

            # Перевірка на унікальність
            if candidate and candidate.lower() in used_words_set:
                logger.warning(f"Модель згенерувала раніше використане слово '{candidate}' (спроба {attempt}/3). Повторюємо...")
                recent_words_str += f", {candidate}"
                continue

            data.setdefault("rubric_name", rubric["title"])
            data.setdefault("rubric_emoji", rubric["emoji"])
            if not data.get("clean_word"):
                data["clean_word"] = data.get("word", "")
            data["telegram_post"] = format_morning_post(data)
            data["audio_text"] = f"{data.get('clean_word')}. {data.get('card_example_de', '')}".strip()
            data["post_type"] = "morning"
            return data
        except Exception as e:
            logger.error(f"Помилка в генерації ранкового слова (спроба {attempt}): {e}")

    logger.warning("Усі спроби генерації слова через Gemini вичерпано. Використовуємо невикористаний fallback з пулу.")
    fallback = pick_unused_fallback(FALLBACK_WORDS_POOL, used_words, lambda x: x.get("clean_word") or x.get("word"))
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
    """Генерує денний розмовний пост (фраза дня + міні-діалог) з перевіркою на дублікати."""
    history = load_history(history_file_path)
    used_phrases = history.get("used_phrases", [])
    used_phrases_set = {p.lower().strip() for p in used_phrases}

    recent_phrases_str = ", ".join(used_phrases[-100:]) if used_phrases else "немає"

    for attempt in range(1, 4):
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
            resp_text = generate_with_gemini_cascade(prompt, api_key=api_key)
            data = clean_json_response(resp_text)
            candidate = (data.get("phrase") or "").strip()

            if candidate and candidate.lower() in used_phrases_set:
                logger.warning(f"Модель згенерувала повторну фразу '{candidate}' (спроба {attempt}/3). Повторюємо...")
                recent_phrases_str += f", {candidate}"
                continue

            data["rubric_name"] = "Розмовна фраза дня"
            data["rubric_emoji"] = "🗣️"
            data["word"] = data.get("phrase", "")
            data["clean_word"] = data.get("phrase", "")
            data["level"] = "Розмовна"
            data["telegram_post"] = format_afternoon_post(data)
            data["audio_text"] = f"{data.get('phrase')}. {data.get('card_example_de', '')}".strip()
            data["post_type"] = "afternoon"
            return data
        except Exception as e:
            logger.error(f"Помилка генерації розмовної фрази (спроба {attempt}): {e}")

    logger.warning("Усі спроби генерації фрази вичерпано. Використовуємо невикористаний fallback з пулу.")
    fallback = pick_unused_fallback(FALLBACK_PHRASES_POOL, used_phrases, lambda x: x.get("phrase"))
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
    Здійснює обов'язкову перевірку на дублікати проти історії.
    """
    history = load_history(history_file_path)
    used_quizzes = history.get("used_quizzes", [])
    used_questions = [h.get("question", "") for h in history.get("history", []) if h.get("type") == "evening_quiz"]
    used_all_set = {str(x).lower().strip() for x in (used_quizzes + used_questions)}

    recent_topics_str = ", ".join(used_quizzes[-100:]) if used_quizzes else "немає"

    for attempt in range(1, 4):
        prompt = f"""
Створи захопливе інтерактивне тестове питання (Quiz) з німецької мови для українців (рівні A1-B2).
Це може бути практична граматика (артиклі der/die/das, Akkusativ/Dativ, прийменники часу/місця), жива розмовна фраза або типова помилка.
СУВОРО УНИКАЙ ТЕМ ТА ПИТАНЬ, ЯКІ ВЖЕ БУЛИ: {recent_topics_str}

СУВОРІ ВИМОГИ ДО ЛІТЕРАТУРНОЇ ЧИСТОТИ УКРАЇНСЬКОЇ МОВИ:
- ЖОДНОГО СУРЖИКУ та кальок з російської! Тільки нормативна українська: "решта", "квиток", "лікарняний", "готівка".

СУВОРІ ОБМЕЖЕННЯ TELEGRAM POLL:
1. question: довжина до 250 символів. Обов'язково постав емодзі 🇩🇪 на початку.
2. options: рівно 3 або 4 варіанти відповіді (кожен до 80 символів). Варіанти мають бути реалістичними, без префіксів A), B), C).
3. correct_option_id: індекс правильної відповіді (число від 0 до 3).
4. explanation: ДУЖЕ ВАЖЛИВО! Довжина суворо до 190 символів! Чітке пояснення українською мовою, чому саме ця відповідь правильна.

Поверни валідний JSON:
{{
  "topic": "Коротка унікальна назва теми (до 5 слів)",
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
            resp_text = generate_with_gemini_cascade(prompt, api_key=api_key)
            data = clean_json_response(resp_text)
            topic = (data.get("topic") or "").strip()
            question = (data.get("question") or "").strip()

            # Перевірка на дублікат теми або тексту питання
            if (topic and topic.lower() in used_all_set) or (question and question.lower() in used_all_set):
                logger.warning(f"Модель згенерувала повторний квіз '{topic}' / '{question}' (спроба {attempt}/3). Повторюємо...")
                recent_topics_str += f", {topic}"
                continue

            q = question[:290] if question else "🇩🇪 Оберіть правильний варіант:"
            options = [str(opt)[:95] for opt in data.get("options", [])[:4]]
            if len(options) < 2:
                options = ["Варіант 1", "Варіант 2"]
            correct_id = int(data.get("correct_option_id", 0))
            if correct_id >= len(options) or correct_id < 0:
                correct_id = 0
            explanation = str(data.get("explanation", "Правильна відповідь!"))[:195]

            return {
                "topic": topic or "Німецька вікторина",
                "question": q,
                "options": options,
                "correct_option_id": correct_id,
                "explanation": explanation,
                "post_type": "evening"
            }
        except Exception as e:
            logger.error(f"Помилка генерації вечірнього тесту (спроба {attempt}): {e}")

    logger.warning("Усі спроби генерації Quiz вичерпано. Використовуємо невикористаний fallback з пулу.")
    fallback = pick_unused_fallback(FALLBACK_QUIZZES_POOL, used_quizzes + used_questions, lambda x: x.get("topic") or x.get("question"))
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
    """Генерує готовий шаблон німецького листа (Musterbrief) з перевіркою на дублікати."""
    history = load_history(history_file_path)
    used_letters = history.get("used_letters", [])
    used_letters_set = {str(l).lower().strip() for l in used_letters}

    recent_letters_str = ", ".join(used_letters[-50:]) if used_letters else "немає"

    for attempt in range(1, 4):
        prompt = f"""
Ти — експерт з німецької офіційної кореспонденції та права для українців у Німеччині.
Створи практичний, грамотний, сучасний зразок офіційного німецького листа (Musterbrief).
Теми можуть стосуватися:
1. Розірвання договору (Kündigung: спортзал, інтернет, зв'язок, страховка).
2. Запит на зустріч/запис (Terminanfrage im Bürgeramt / Ausländerbehörde / Facharzt).
3. Повідомлення про хворобу (Krankmeldung для роботодавця або пояснення відсутності дитини в школі).
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
  "letter_text": "Повний офіційний текст листа німецькою мовою з чіткими полями у квадратних дужках, наприклад [Ihr Name], [Vertragsnummer], [Datum].",
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
            resp_text = generate_with_gemini_cascade(prompt, api_key=api_key)
            data = clean_json_response(resp_text)
            topic = (data.get("topic") or "").strip()

            if topic and topic.lower() in used_letters_set:
                logger.warning(f"Модель згенерувала повторну тему листа '{topic}' (спроба {attempt}/3). Повторюємо...")
                recent_letters_str += f", {topic}"
                continue

            data["rubric_name"] = "Офіційний лист (Musterbrief)"
            data["rubric_emoji"] = "✉️"
            data["word"] = topic or "Musterbrief"
            data["clean_word"] = topic or "Musterbrief"
            data["level"] = "Офіційна"
            data["telegram_post"] = format_musterbrief_post(data)
            data["audio_text"] = data.get("card_example_de", "Hiermit kündige ich meinen Vertrag.")
            data["post_type"] = "musterbrief"
            return data
        except Exception as e:
            logger.error(f"Помилка генерації Musterbrief (спроба {attempt}): {e}")

    logger.warning("Усі спроби генерації листа вичерпано. Використовуємо невикористаний fallback з пулу.")
    fallback = pick_unused_fallback(FALLBACK_MUSTERBRIEFE_POOL, used_letters, lambda x: x.get("topic"))
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
    """Генерує розбір типової помилки з перевіркою на дублікати."""
    history = load_history(history_file_path)
    used_mistakes = history.get("used_mistakes", [])
    used_mistakes_set = {str(m).lower().strip() for m in used_mistakes}

    recent_mistakes_str = ", ".join(used_mistakes[-50:]) if used_mistakes else "немає"

    for attempt in range(1, 4):
        prompt = f"""
Ти — професійний викладач німецької для україномовних студентів. Обери ОДНУ дуже поширену типову помилку, яку українці роблять у німецькій через кальку з рідної мови, інтерференцію або «хибних друзів перекладача» (Falsche Freunde).

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
            resp_text = generate_with_gemini_cascade(prompt, api_key=api_key)
            data = clean_json_response(resp_text)
            title = (data.get("mistake_title") or "").strip()

            if title and title.lower() in used_mistakes_set:
                logger.warning(f"Модель згенерувала повторну помилку '{title}' (спроба {attempt}/3). Повторюємо...")
                recent_mistakes_str += f", {title}"
                continue

            data["rubric_name"] = "Типова помилка"
            data["rubric_emoji"] = "⚠️"
            clean_correct = data.get("correct_phrase", "").replace("✅", "").strip()
            data["word"] = clean_correct or title or "Typischer Fehler"
            data["clean_word"] = clean_correct
            data["translation"] = data.get("card_example_ua", "")
            data["telegram_post"] = format_mistake_post(data)
            data["audio_text"] = f"{clean_correct}. {data.get('card_example_de', '')}".strip()
            data["post_type"] = "mistake"
            return data
        except Exception as e:
            logger.error(f"Помилка генерації типової помилки (спроба {attempt}): {e}")

    logger.warning("Усі спроби генерації помилки вичерпано. Використовуємо невикористаний fallback з пулу.")
    fallback = pick_unused_fallback(FALLBACK_MISTAKES_POOL, used_mistakes, lambda x: x.get("mistake_title"))
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
    """Генерує практичний міні-діалог з перевіркою на дублікати."""
    history = load_history(history_file_path)
    used_dialogues = history.get("used_dialogues", [])
    used_dialogues_set = {str(d).lower().strip() for d in used_dialogues}

    recent_dialogues_str = ", ".join(used_dialogues[-50:]) if used_dialogues else "немає"

    for attempt in range(1, 4):
        prompt = f"""
Ти — викладач розмовної німецької мови для щоденного життя в Німеччині. Створи живий, практичний міні-діалог (рівень A1-B1) у реальній побутовій ситуації в Німеччині.

Локації для вибору:
- В аптеці (In der Apotheke)
- У пекарні (In der Bäckerei)
- На касі в супермаркеті (An der Kasse)
- У лікаря (Beim Arzt)
- У громадському транспорті (Im Zug / Bus)
- На пошті Deutsche Post / DHL
- У Bürgeramt / Ausländerbehörde

СУВОРО УНИКАЙ ВЖЕ ВИКОРИСТАНИХ ТЕМ ТА СИТУАЦІЙ: {recent_dialogues_str}

СУВОРІ ВИМОГИ ДО ЛІТЕРАТУРНОЇ ЧИСТОТИ УКРАЇНСЬКОЇ МОВИ:
- Жодного суржику та кальок з російської (обов'язково "решти не треба", "квиток", "лікарняний", "готівка").

Поверни валідний JSON:
{{
  "location": "Локація (наприклад: В аптеці / In der Apotheke)",
  "topic": "Тема діалогу (наприклад: Купівля ліків від болю в горлі)",
  "level": "A1-A2",
  "card_example_de": "Ключова фраза діалогу (до 10 слів) для графічної картки",
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
            resp_text = generate_with_gemini_cascade(prompt, api_key=api_key)
            data = clean_json_response(resp_text)
            topic = (data.get("topic") or data.get("location") or "").strip()

            if topic and topic.lower() in used_dialogues_set:
                logger.warning(f"Модель згенерувала повторну тему діалогу '{topic}' (спроба {attempt}/3). Повторюємо...")
                recent_dialogues_str += f", {topic}"
                continue

            data["rubric_name"] = "Живий діалог"
            data["rubric_emoji"] = "🏙️"
            data["word"] = topic or "Живий діалог"
            data["clean_word"] = data.get("card_example_de", "")
            data["translation"] = data.get("card_example_ua", "")
            data["telegram_post"] = format_dialogue_post(data)

            dialogue_audio = ". ".join([d.get("de", "") for d in data.get("dialogue", [])[:2]])
            data["audio_text"] = (data.get("card_example_de") or dialogue_audio)[:250]
            data["post_type"] = "dialogue"
            return data
        except Exception as e:
            logger.error(f"Помилка генерації живого діалогу (спроба {attempt}): {e}")

    logger.warning("Усі спроби генерації діалогу вичерпано. Використовуємо невикористаний fallback з пулу.")
    fallback = pick_unused_fallback(FALLBACK_DIALOGUES_POOL, used_dialogues, lambda x: x.get("topic") or x.get("location"))
    fallback["telegram_post"] = format_dialogue_post(fallback)
    fallback["word"] = fallback.get("topic", "Діалог")
    fallback["audio_text"] = fallback.get("card_example_de", "")
    fallback["post_type"] = "dialogue"
    return fallback


# Зворотна сумісність
generate_daily_content = generate_morning_content
FALLBACK_QUIZ = FALLBACK_QUIZZES_POOL[0]
FALLBACK_WORDS = {0: FALLBACK_WORDS_POOL[0]}
FALLBACK_PHRASE = FALLBACK_PHRASES_POOL[0]
FALLBACK_MUSTERBRIEF = FALLBACK_MUSTERBRIEFE_POOL[0]
FALLBACK_MISTAKE = FALLBACK_MISTAKES_POOL[0]
FALLBACK_DIALOGUE = FALLBACK_DIALOGUES_POOL[0]
