"""Pictures of the player: image files (.xi, .png, .jpeg) and texts drawn with a bitmap font of the game.

Their pixels are RGBA uint8 arrays, top row first. They are placed on the 3DS top screen (SCREEN, in pixels).
"""
import io
import json

import numpy as np
from PIL import Image

import eleven
import waza_font

SCREEN = (400, 240)
THUMBNAIL_SIZE = (250, 150)
THUMBNAIL_BACKGROUND = (0x1c, 0x1c, 0x1c)

FONTS = {waza_font.NAME: waza_font}
DEFAULT_FONT = waza_font.NAME
DEFAULT_COLOR = "#ffd700"


class ImageError(Exception):
    pass


def decode(name, data):
    """RGBA pixels of an image file: .xi with studio_eleven's reader, the others with Pillow."""
    if name.lower().endswith(".xi"):
        decoded = eleven.imgc().open(bytes(data))
        if decoded is None:
            raise ImageError("not a Level-5 image (.xi)")
        pixels, width, height = decoded[0], decoded[1], decoded[2]
        array = np.asarray(pixels, dtype=np.float32).reshape(height, width, 4)
        return np.clip(array * 255.0 + 0.5, 0, 255).astype(np.uint8)
    try:
        with Image.open(io.BytesIO(bytes(data))) as image:
            return np.asarray(image.convert("RGBA"), dtype=np.uint8).copy()
    except Exception as error:
        raise ImageError(f"not an image ({error})")


def png(rgba):
    stream = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(stream, "PNG")
    return stream.getvalue()


def thumbnail(rgba, size=THUMBNAIL_SIZE):
    """PNG bytes of the card: the whole picture centred on the background of the cards."""
    height, width = rgba.shape[:2]
    scale = min(size[0] * 0.9 / width, size[1] * 0.85 / height)
    fitted = (max(1, int(round(width * scale))), max(1, int(round(height * scale))))
    # Small pictures (icons, pixel art) are enlarged without blur
    resample = Image.NEAREST if scale >= 2 else Image.LANCZOS
    picture = Image.fromarray(rgba, "RGBA").resize(fitted, resample)
    card = Image.new("RGBA", size, THUMBNAIL_BACKGROUND + (255,))
    card.alpha_composite(picture, ((size[0] - fitted[0]) // 2, (size[1] - fitted[1]) // 2))
    return png(np.asarray(card.convert("RGB").convert("RGBA")))


# Texts


def text_spec(text, font=DEFAULT_FONT, color=DEFAULT_COLOR):
    return {"text": str(text), "font": font if font in FONTS else DEFAULT_FONT, "color": normal_color(color)}


def normal_color(color):
    text = str(color or "").strip().lower()
    if not text.startswith("#"):
        text = "#" + text
    try:
        int(text[1:], 16)
    except ValueError:
        return DEFAULT_COLOR
    return text if len(text) == 7 else DEFAULT_COLOR


def rgb(color):
    color = normal_color(color)
    return tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))


def read_text(data):
    """The {"text", "font", "color"} of the file of a text asset."""
    try:
        values = json.loads(bytes(data).decode("utf-8"))
        return text_spec(values.get("text", ""), values.get("font"), values.get("color"))
    except (ValueError, AttributeError) as error:
        raise ImageError(f"not a text asset ({error})")


def text_data(spec):
    return json.dumps(spec, ensure_ascii=False, indent=2).encode("utf-8")


def render_text(spec):
    return FONTS.get(spec["font"], waza_font).render(spec["text"], rgb(spec["color"]))


def missing_characters(spec):
    return FONTS.get(spec["font"], waza_font).missing(spec["text"])


def drawable(spec):
    """True when the font draws at least one character of the text."""
    return any(c not in "\r\n" for c in spec["text"]) and len(set(spec["text"]) - set("\r\n")) > len(missing_characters(spec))


def text_name(spec):
    """File name of a new text asset: its first line, without the characters a file name cannot have."""
    line = spec["text"].strip().split("\n")[0].strip()
    name = "".join(c for c in line if c not in '\\/:*?"<>|\r\t').strip(" .")[:40].strip()
    return (name or "text") + ".text"
