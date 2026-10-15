"""
create_avatar.py
Створює стильний мінімалістичний векторний аватар (1024x1024) для Telegram-каналу.
"""

import math
from PIL import Image, ImageDraw, ImageFont
from image_maker import load_font, create_vertical_gradient

def generate_channel_avatar(output_path: str = "avatar_badge.png") -> str:
    size = 1024
    center = size // 2
    
    # 1. Створюємо глибокий темно-синій фон
    img = create_vertical_gradient(size, size, (11, 19, 43), (15, 23, 42))
    draw = ImageDraw.Draw(img)
    
    # 2. Зовнішні концентричні кола з легким градієнтом/підсвіткою
    radius = 480
    
    # Прапор Німеччини (ліва півдуга)
    # Прапор України (права півдуга)
    # Тонке стильне кільце
    draw.ellipse(
        [(center - radius, center - radius), (center + radius, center + radius)],
        outline=(30, 41, 59),
        width=16
    )
    
    # Внутрішня підкладка
    inner_r = 450
    draw.ellipse(
        [(center - inner_r, center - inner_r), (center + inner_r, center + inner_r)],
        fill=(19, 31, 53),
        outline=(42, 60, 92),
        width=6
    )
    
    # 3. Декоративні прапорці-стрічки у верхній та нижній частині
    # Німецька стрічка
    de_strip_y = center - 280
    strip_w = 140
    strip_h = 10
    draw.rounded_rectangle([(center - 160, de_strip_y), (center - 160 + strip_w, de_strip_y + strip_h)], radius=5, fill=(245, 158, 11))
    draw.rounded_rectangle([(center - 160, de_strip_y - 12), (center - 160 + strip_w, de_strip_y - 2)], radius=5, fill=(239, 68, 68))
    draw.rounded_rectangle([(center - 160, de_strip_y - 24), (center - 160 + strip_w, de_strip_y - 14)], radius=5, fill=(30, 41, 59))

    # Українська стрічка
    ua_strip_y = de_strip_y
    draw.rounded_rectangle([(center + 20, ua_strip_y - 24), (center + 20 + strip_w, ua_strip_y - 10)], radius=5, fill=(37, 99, 235))
    draw.rounded_rectangle([(center + 20, ua_strip_y - 8), (center + 20 + strip_w, ua_strip_y + 6)], radius=5, fill=(250, 204, 21))

    # 4. Шрифти
    font_main = load_font(is_bold=True, size=150)
    font_arrow = load_font(is_bold=True, size=110)
    font_sub = load_font(is_bold=True, size=46)
    font_sub2 = load_font(is_bold=False, size=32)

    # 5. Центральний текст: DE ➔ UA
    # DE (золотий/білий)
    draw.text((center - 320, center - 130), "DE", font=font_main, fill=(255, 255, 255))
    # Стрілка або місток
    draw.text((center - 85, center - 110), "⇄", font=font_arrow, fill=(56, 189, 248))
    # UA (синьо-жовтий або світлий)
    draw.text((center + 80, center - 130), "UA", font=font_main, fill=(250, 204, 21))

    # Розділювальна лінія
    draw.line([(center - 260, center + 70), (center + 260, center + 70)], fill=(51, 65, 85), width=3)

    # 6. Назва каналу українською
    title_text = "НІМЕЦЬКА ДЛЯ УКРАЇНЦІВ"
    bbox_t = draw.textbbox((0, 0), title_text, font=font_sub)
    tw = bbox_t[2] - bbox_t[0]
    draw.text((center - tw // 2, center + 105), title_text, font=font_sub, fill=(241, 245, 249))

    # 7. Підзаголовок / Слоган
    slogan = "Щоденна практика • Рівні A1-B2"
    bbox_s = draw.textbbox((0, 0), slogan, font=font_sub2)
    sw = bbox_s[2] - bbox_s[0]
    draw.text((center - sw // 2, center + 185), slogan, font=font_sub2, fill=(148, 163, 184))

    # Нижній бейдж "A1 • A2 • B1 • B2"
    badge_rect = [(center - 180, center + 265), (center + 180, center + 325)]
    draw.rounded_rectangle(badge_rect, radius=18, fill=(28, 45, 75), outline=(59, 130, 246), width=2)
    levels = "A1  •  A2  •  B1  •  B2"
    font_levels = load_font(is_bold=True, size=28)
    bbox_l = draw.textbbox((0, 0), levels, font=font_levels)
    lw = bbox_l[2] - bbox_l[0]
    draw.text((center - lw // 2, center + 278), levels, font=font_levels, fill=(191, 219, 254))

    # Збереження
    img.save(output_path, "PNG", quality=95)
    print(f"Аватар успішно збережено у {output_path}")
    return output_path

if __name__ == "__main__":
    generate_channel_avatar()
