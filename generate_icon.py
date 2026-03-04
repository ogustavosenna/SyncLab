#!/usr/bin/env python3
"""
SyncLab Icon Generator v12
Clean: emojis tight together (no gradient tint) + SyncLab gradient text.
Fix: y descender clipping on SyncLab text.

Run:  python generate_icon.py
"""

from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter


BG = (7, 7, 15)
TEAL = (78, 204, 163)
CYAN = (0, 212, 255)


def lerp_color(c1, c2, t):
    t = max(0, min(1, t))
    return tuple(int(a + (b - a) * t) for a, b in zip(c1, c2))


def create_bg(size):
    img = Image.new("RGBA", (size, size), BG + (255,))
    glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(glow)
    cx, cy = size // 2, size // 2
    for r in range(int(size * 0.44), 0, -2):
        t = r / (size * 0.44)
        alpha = int(12 * (1 - t) ** 2)
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(15, 25, 45, alpha))
    glow = glow.filter(ImageFilter.GaussianBlur(radius=size * 0.05))
    return Image.alpha_composite(img, glow)


def find_emoji_font(target_size):
    path = "C:/Windows/Fonts/seguiemj.ttf"
    if Path(path).exists():
        return ImageFont.truetype(path, target_size)
    return ImageFont.load_default()


def find_bold_font(target_size):
    for path in ["C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/arialbd.ttf"]:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, target_size)
            except Exception:
                continue
    return ImageFont.load_default()


def get_size(draw, text, font):
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def draw_gradient_text(img, text, font, cx, cy):
    """Draw text with teal→cyan gradient, centered at (cx, cy).
    Uses large canvas to avoid clipping descenders (y, g, p, etc.)."""
    tmp_draw = ImageDraw.Draw(img)
    tw, th = get_size(tmp_draw, text, font)

    # Extra padding for descenders and ascenders
    pad = int(th * 0.5)
    canvas_w = tw + pad * 2
    canvas_h = th + pad * 2

    # Render white text on temp canvas (with padding)
    txt = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    td = ImageDraw.Draw(txt)
    td.text((pad, pad), text, font=font, fill=(255, 255, 255, 255))

    # Extract alpha
    _, _, _, alpha = txt.split()

    # Create gradient
    grad = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    for x in range(canvas_w):
        t = x / max(1, canvas_w - 1)
        color = lerp_color(TEAL, CYAN, t)
        for y in range(canvas_h):
            grad.putpixel((x, y), color + (255,))

    # Mask gradient with text alpha
    result = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    result.paste(grad, (0, 0), alpha)

    # Composite centered
    x = cx - canvas_w // 2
    y = cy - canvas_h // 2
    img.alpha_composite(result, (x, y))


def create_icon(size=1024):
    img = create_bg(size)
    draw = ImageDraw.Draw(img)
    cx = size // 2

    # --- Emojis: original colors, very tight ---
    emoji_size = int(size * 0.21)
    font_emoji = find_emoji_font(emoji_size)

    # Render each emoji to measure individually
    emojis = ["🎬", "🎶", "✅"]
    emoji_widths = []
    for em in emojis:
        bbox = draw.textbbox((0, 0), em, font=font_emoji)
        emoji_widths.append(bbox[2] - bbox[0])

    # Very tight negative gap
    gap = int(size * -0.025)
    total_w = sum(emoji_widths) + gap * (len(emojis) - 1)

    # Get height from first emoji
    bbox0 = draw.textbbox((0, 0), emojis[0], font=font_emoji)
    eh = bbox0[3] - bbox0[1]

    emoji_y = int(size * 0.25)
    x_cursor = cx - total_w // 2

    for i, em in enumerate(emojis):
        draw.text((x_cursor, emoji_y), em, font=font_emoji, embedded_color=True)
        x_cursor += emoji_widths[i] + gap

    # --- SyncLab gradient text ---
    text_size = int(size * 0.16)
    font_text = find_bold_font(text_size)

    text_cy = emoji_y + eh + int(size * 0.10)
    draw_gradient_text(img, "SyncLab", font_text, cx, text_cy)

    return img


def save_final(output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    icon_full = create_icon(1024)
    for s, name in [(256, "icon.png"), (512, "icon-512.png"), (1024, "icon-1024.png")]:
        resized = icon_full.resize((s, s), Image.LANCZOS)
        resized.save(str(output_dir / name), "PNG")
        print(f"  PNG {s}: {output_dir / name}")
    ico_sizes = [16, 32, 48, 64, 128, 256]
    ico_images = [icon_full.resize((s, s), Image.LANCZOS) for s in ico_sizes]
    ico_path = output_dir / "icon.ico"
    ico_images[0].save(str(ico_path), format="ICO",
                       sizes=[(s, s) for s in ico_sizes],
                       append_images=ico_images[1:])
    print(f"  ICO: {ico_path}")


if __name__ == "__main__":
    print("Generating SyncLab icon v12...\n")
    img_dir = Path(__file__).parent / "synclab" / "app" / "static" / "img"
    save_final(img_dir)
    print("\nDone!")
