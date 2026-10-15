"""
image_maker.py
Модуль створення брендованих графічних карток для Telegram-каналу за допомогою Pillow.
Підтримує автоматичний пошук шрифтів (Linux, macOS, Windows) з повною підтримкою
німецьких умлаутів (ä, ö, ü, ß) та української кирилиці (і, ї, є, ґ).
"""

import os
import sys
import re
import logging
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

def strip_emojis(text: str) -> str:
    """Видаляє емодзі та специфічні піктограми, для яких у TrueType шрифтах немає гліфів."""
    if not text:
        return ""
    emoji_pattern = re.compile(
        r"[\U00010000-\U0010ffff"
        r"\u2600-\u27bf"
        r"\u2300-\u23ff"
        r"\u2190-\u21ff"
        r"]+",
        flags=re.UNICODE
    )
    cleaned = emoji_pattern.sub("", text)
    return re.sub(r"\s+", " ", cleaned).strip()


# Розміри картки
CARD_WIDTH = 1080
CARD_HEIGHT = 1080

# Кольорова палітра артиклів
ARTICLE_COLORS = {
    "der": (56, 189, 248),    # Блакитний (Sky Blue)
    "die": (251, 113, 133),   # Коралово-рожевий (Rose/Coral)
    "das": (52, 211, 153),    # Смарагдово-зелений (Emerald)
}
DEFAULT_ARTICLE_COLOR = (250, 204, 21) # Золотий (Amber)

# Шляхи пошуку системних шрифтів із підтримкою Кирилиці та Умлаутів
FONT_CANDIDATES_BOLD = [
    # Локальні шрифти в проєкті
    "fonts/DejaVuSans-Bold.ttf",
    "fonts/Roboto-Bold.ttf",
    # Linux (GitHub Actions ubuntu-latest)
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    # macOS
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    # Windows
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/seguiemj.ttf",
]

FONT_CANDIDATES_REGULAR = [
    # Локальні шрифти в проєкті
    "fonts/DejaVuSans.ttf",
    "fonts/Roboto-Regular.ttf",
    # Linux (GitHub Actions ubuntu-latest)
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    # macOS
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/Library/Fonts/Arial.ttf",
    # Windows
    "C:/Windows/Fonts/arial.ttf",
]


def load_font(is_bold: bool = False, size: int = 32):
    """
    Завантажує TrueType шрифт із підтримкою кирилиці та німецьких умлаутів.
    Перевіряє системні шляхи Linux/macOS/Windows та повертає найкращий знайдений шрифт.
    """
    candidates = FONT_CANDIDATES_BOLD if is_bold else FONT_CANDIDATES_REGULAR
    
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size=size)
            except Exception:
                continue

    # Спроба завантажити за стандартним системним іменем
    fallback_names = ["DejaVuSans-Bold.ttf" if is_bold else "DejaVuSans.ttf", "Arial.ttf"]
    for name in fallback_names:
        try:
            return ImageFont.truetype(name, size=size)
        except Exception:
            pass

    # Резервний системний шрифт Pillow
    logger.warning("Системні TTF шрифти не знайдено, використовується дефолтний шрифт Pillow.")
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def create_vertical_gradient(width: int, height: int, start_color: tuple, end_color: tuple) -> Image.Image:
    """Створює плавний вертикальний градієнт для фону."""
    base = Image.new("RGB", (width, height), start_color)
    draw = ImageDraw.Draw(base)
    for y in range(height):
        ratio = y / float(height)
        r = int(start_color[0] * (1 - ratio) + end_color[0] * ratio)
        g = int(start_color[1] * (1 - ratio) + end_color[1] * ratio)
        b = int(start_color[2] * (1 - ratio) + end_color[2] * ratio)
        draw.line([(0, y), (width, y)], fill=(r, g, b))
    return base


def wrap_text(text: str, font: ImageFont.ImageFont, max_width: int, draw: ImageDraw.ImageDraw) -> list[str]:
    """Розбиває довгий текст на рядки, щоб він красиво поміщався в межі картки."""
    words = text.split()
    if not words:
        return []

    lines = []
    current_line = words[0]

    for word in words[1:]:
        test_line = f"{current_line} {word}"
        bbox = draw.textbbox((0, 0), test_line, font=font)
        line_width = bbox[2] - bbox[0]
        if line_width <= max_width:
            current_line = test_line
        else:
            lines.append(current_line)
            current_line = word

    lines.append(current_line)
    return lines


def generate_card_image(data: dict, output_path: str = "card.png") -> str:
    """
    Генерує стильну графічну картку високої роздільної здатності (1080x1080).
    Зберігає зображення у файл output_path та повертає шлях до нього.
    """
    # 1. Створення градієнтного фону (глибокий темно-синій преміум стиль)
    img = create_vertical_gradient(
        CARD_WIDTH, CARD_HEIGHT,
        start_color=(11, 19, 43),    # #0B132B
        end_color=(19, 31, 58)       # #131F3A
    )
    draw = ImageDraw.Draw(img)

    # 2. Верхня декоративна акцентна лінія (прапори Німеччини та України)
    # Німецькі кольори (ліва половина)
    draw.rectangle([(0, 0), (CARD_WIDTH // 6, 8)], fill=(20, 20, 20))       # Чорний
    draw.rectangle([(CARD_WIDTH // 6, 0), (CARD_WIDTH // 3, 8)], fill=(220, 38, 38)) # Червоний
    draw.rectangle([(CARD_WIDTH // 3, 0), (CARD_WIDTH // 2, 8)], fill=(245, 158, 11))# Золотий
    # Українські кольори (права половина)
    draw.rectangle([(CARD_WIDTH // 2, 0), (3 * CARD_WIDTH // 4, 8)], fill=(37, 99, 235)) # Синій
    draw.rectangle([(3 * CARD_WIDTH // 4, 0), (CARD_WIDTH, 8)], fill=(250, 204, 21))     # Жовтий

    # 3. Центральна підложка картки (стильний заокруглений прямокутник)
    margin = 50
    card_box = (margin, 50, CARD_WIDTH - margin, CARD_HEIGHT - 60)
    draw.rounded_rectangle(card_box, radius=28, fill=(19, 31, 53), outline=(42, 60, 92), width=2)

    # 4. Шрифти
    font_badge = load_font(is_bold=True, size=24)
    font_level = load_font(is_bold=True, size=22)
    font_word = load_font(is_bold=True, size=64)
    font_transcription = load_font(is_bold=False, size=30)
    font_translation = load_font(is_bold=True, size=38)
    font_example_label = load_font(is_bold=True, size=24)
    font_example_de = load_font(is_bold=True, size=30)
    font_example_ua = load_font(is_bold=False, size=26)
    font_footer = load_font(is_bold=False, size=22)
    font_brand = load_font(is_bold=True, size=24)

    # 5. Хедер картки: бейдж рубрики та рівень
    raw_rubric = data.get("rubric_name", "Німецька щодня")
    rubric_title = strip_emojis(raw_rubric).upper()
    level_text = f"РІВЕНЬ: {strip_emojis(data.get('level', 'A2'))}"

    # Бейдж рубрики (ліворуч) із векторним німецьким міні-прапорцем замість емодзі
    badge_x = margin + 40
    badge_y = 90
    flag_w, flag_h = 24, 18
    text_offset_x = badge_x + 16 + flag_w + 12
    badge_text_bbox = draw.textbbox((text_offset_x, badge_y + 8), rubric_title, font=font_badge)
    badge_rect = (badge_x, badge_y, badge_text_bbox[2] + 20, badge_text_bbox[3] + 10)
    draw.rounded_rectangle(badge_rect, radius=12, fill=(28, 45, 75), outline=(59, 130, 246), width=1)

    # Малюємо міні-прапорець Німеччини геометрично (без емодзі шрифту!)
    flag_x = badge_x + 16
    flag_y = badge_y + 13
    h_stripe = flag_h // 3
    draw.rectangle([(flag_x, flag_y), (flag_x + flag_w, flag_y + h_stripe)], fill=(20, 20, 20))
    draw.rectangle([(flag_x, flag_y + h_stripe), (flag_x + flag_w, flag_y + 2 * h_stripe)], fill=(220, 38, 38))
    draw.rectangle([(flag_x, flag_y + 2 * h_stripe), (flag_x + flag_w, flag_y + flag_h)], fill=(245, 158, 11))
    draw.text((text_offset_x, badge_y + 8), rubric_title, font=font_badge, fill=(226, 232, 240))

    # Бейдж рівня (праворуч)
    level_bbox = draw.textbbox((0, 0), level_text, font=font_level)
    level_w = level_bbox[2] - level_bbox[0]
    level_x = CARD_WIDTH - margin - 40 - level_w - 32
    level_rect = (level_x, badge_y, CARD_WIDTH - margin - 40, badge_y + 44)
    draw.rounded_rectangle(level_rect, radius=12, fill=(30, 58, 138), outline=(96, 165, 250), width=1)
    draw.text((level_x + 16, badge_y + 9), level_text, font=font_level, fill=(191, 219, 254))

    # 6. Головне слово або фраза дня
    word = strip_emojis(data.get("word", "")).strip()
    article = data.get("article", "").lower().strip()
    transcription = strip_emojis(data.get("transcription", "")).strip()
    translation = strip_emojis(data.get("translation", "")).strip()

    # Адаптація розміру шрифту для довгих слів та розмовних фраз
    current_word_font = font_word
    word_lines = wrap_text(word, current_word_font, CARD_WIDTH - 2 * margin - 100, draw)
    if len(word_lines) > 2 or any((draw.textbbox((0, 0), l, font=current_word_font)[2] - draw.textbbox((0, 0), l, font=current_word_font)[0]) > (CARD_WIDTH - 2 * margin - 100) for l in word_lines):
        current_word_font = load_font(is_bold=True, size=46)
        word_lines = wrap_text(word, current_word_font, CARD_WIDTH - 2 * margin - 100, draw)

    # Відмальовуємо слово або фразу
    word_y = 205
    for wl in word_lines:
        draw.text((margin + 40, word_y), wl, font=current_word_font, fill=(255, 255, 255))
        bbox_l = draw.textbbox((0, 0), wl, font=current_word_font)
        word_y += (bbox_l[3] - bbox_l[1]) + 12

    # Транскрипція українськими літерами
    transcription_y = word_y + 8
    draw.text((margin + 40, transcription_y), transcription, font=font_transcription, fill=(148, 163, 184))

    # Переклад українською мовою
    translation_y = transcription_y + 55
    trans_lines = wrap_text(translation, font_translation, CARD_WIDTH - 2 * margin - 80, draw)
    for line in trans_lines:
        draw.text((margin + 40, translation_y), line, font=font_translation, fill=(250, 204, 21)) # Теплий золотий
        translation_y += 48

    # 7. Контейнер для прикладу вживання
    example_box_top = max(translation_y + 40, 540)
    example_box_bottom = example_box_top + 320
    example_rect = (margin + 30, example_box_top, CARD_WIDTH - margin - 30, example_box_bottom)
    draw.rounded_rectangle(example_rect, radius=20, fill=(13, 21, 37), outline=(37, 52, 80), width=2)

    # Заголовок блоку прикладу з вертикальною акцентною лінією замість емодзі 💬
    draw.rectangle([(margin + 55, example_box_top + 26), (margin + 60, example_box_top + 50)], fill=(96, 165, 250))
    draw.text((margin + 72, example_box_top + 25), "ПРИКЛАД У РЕАЛЬНОМУ ЖИТТІ:", font=font_example_label, fill=(96, 165, 250))

    # Німецьке речення (виділене)
    card_example_de = strip_emojis(data.get("card_example_de", ""))
    card_example_ua = strip_emojis(data.get("card_example_ua", ""))

    de_lines = wrap_text(f"»{card_example_de}«", font_example_de, CARD_WIDTH - 2 * margin - 120, draw)
    curr_y = example_box_top + 75
    for line in de_lines:
        draw.text((margin + 55, curr_y), line, font=font_example_de, fill=(241, 245, 249))
        curr_y += 40

    # Український переклад прикладу (тире замість спецсимволів стрілок ↳)
    curr_y += 12
    ua_lines = wrap_text(f"— {card_example_ua}", font_example_ua, CARD_WIDTH - 2 * margin - 120, draw)
    for line in ua_lines:
        draw.text((margin + 55, curr_y), line, font=font_example_ua, fill=(148, 163, 184))
        curr_y += 34

    # 8. Футер картки
    footer_y = CARD_HEIGHT - 125
    draw.line([(margin + 40, footer_y), (CARD_WIDTH - margin - 40, footer_y)], fill=(37, 52, 80), width=1)

    # Текст футера без емодзі
    draw.text((margin + 40, footer_y + 20), "Правильна вимова — у прикріпленому аудіо", font=font_footer, fill=(100, 116, 139))
    brand_text = "Deutsch für Ukrainer"
    brand_bbox = draw.textbbox((0, 0), brand_text, font=font_brand)
    brand_w = brand_bbox[2] - brand_bbox[0]
    draw.text((CARD_WIDTH - margin - 40 - brand_w, footer_y + 18), brand_text, font=font_brand, fill=(203, 213, 225))

    # Збереження файлу
    img.save(output_path, "PNG", quality=95)
    logger.info(f"Графічну картку успішно збережено у {output_path}")
    return output_path


if __name__ == "__main__":
    # Тестова генерація картки
    test_data = {
        "word": "die Mülltrennung",
        "article": "die",
        "transcription": "[ді Мю́льтреннунґ]",
        "translation": "сортування сміття",
        "level": "A1-A2",
        "rubric_name": "Побут та щоденне життя (Alltag)",
        "card_example_de": "In Deutschland ist Mülltrennung Pflicht.",
        "card_example_ua": "У Німеччині сортування сміття є обов'язковим."
    }
    output = generate_card_image(test_data, "test_card.png")
    print(f"Тестова картка створена: {output}")
