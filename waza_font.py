"""Waza Font: the bitmap font of the technique names, dumped from Skill_Creator (TextRenderer, Font2).

The atlas is fonts/waza_font.png; GLYPHS gives, for each character, (top offset, x, y, width, height) in the atlas.
render() follows TextRenderer.RenderImage: the glyphs are tinted by a color matrix (each channel multiplied by the
color) and drawn over each other, LETTER_DISPLACEMENT pixels closer than their width; characters missing from the
font are skipped.

Skill_Creator's table has 72 characters, the atlas has more: the accented letters and symbols are in ATLAS_GLYPHS,
measured on the atlas and set on the baseline of their letter. A text without them renders as Skill_Creator does;
an accent above the first line makes the image taller (Skill_Creator would cut it).
"""
import os

import numpy as np
from PIL import Image

NAME = "Waza Font"
ATLAS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts", "waza_font.png")
LETTER_DISPLACEMENT = 5
LINE_SPACE = 2

GLYPHS = {
    'A': (10, 0, 10, 23, 43),
    'B': (10, 23, 10, 21, 43),
    'C': (10, 44, 10, 23, 43),
    'D': (10, 67, 10, 23, 43),
    'E': (10, 90, 10, 22, 43),
    'F': (10, 112, 10, 22, 43),
    'G': (10, 134, 10, 22, 43),
    'H': (10, 156, 10, 24, 43),
    'I': (8, 180, 7, 15, 46),
    'J': (10, 195, 10, 23, 43),
    'K': (10, 218, 10, 22, 43),
    'L': (10, 240, 10, 18, 43),
    'M': (10, 258, 10, 27, 43),
    'N': (10, 285, 10, 22, 43),
    'O': (10, 307, 10, 23, 43),
    'P': (10, 330, 10, 22, 43),
    'Q': (10, 352, 10, 23, 47),
    'R': (10, 375, 10, 23, 43),
    'S': (10, 398, 10, 21, 43),
    'T': (10, 419, 10, 24, 43),
    'U': (10, 443, 10, 23, 43),
    'V': (10, 466, 10, 22, 43),
    'W': (10, 0, 71, 28, 43),
    'X': (10, 28, 71, 24, 43),
    'Y': (10, 52, 71, 22, 43),
    'Z': (10, 74, 71, 22, 43),
    'a': (12, 178, 133, 20, 41),
    'b': (11, 198, 133, 21, 41),
    'c': (12, 219, 133, 20, 41),
    'd': (11, 239, 133, 24, 41),
    'e': (12, 263, 133, 20, 41),
    'f': (13, 283, 133, 19, 41),
    'g': (13, 302, 134, 21, 46),
    'h': (12, 323, 133, 20, 41),
    'i': (10, 344, 130, 14, 42),
    'j': (12, 358, 133, 19, 48),
    'k': (11, 377, 133, 20, 41),
    'l': (12, 397, 133, 14, 41),
    'm': (14, 411, 133, 26, 41),
    'n': (14, 437, 133, 20, 41),
    'o': (12, 457, 133, 20, 41),
    'p': (12, 477, 133, 22, 46),
    'q': (11, 0, 193, 19, 47),
    'r': (13, 19, 193, 17, 42),
    's': (11, 36, 193, 20, 42),
    't': (11, 56, 193, 18, 42),
    'u': (11, 74, 193, 21, 42),
    'v': (10, 95, 193, 20, 42),
    'w': (10, 115, 193, 25, 42),
    'x': (10, 140, 193, 20, 42),
    'y': (11, 160, 193, 21, 47),
    'z': (11, 181, 193, 21, 42),
    '0': (10, 216, 253, 21, 43),
    '1': (10, 238, 253, 18, 43),
    '2': (10, 257, 253, 21, 43),
    '3': (10, 279, 253, 21, 43),
    '4': (10, 301, 253, 21, 43),
    '5': (10, 323, 253, 21, 43),
    '6': (10, 345, 253, 21, 43),
    '7': (10, 367, 253, 21, 43),
    '8': (10, 389, 253, 21, 43),
    '9': (10, 411, 253, 21, 43),
    ' ': (10, 0, 0, 20, 1),
    '-': (11, 260, 316, 20, 41),
    ',': (12, 348, 316, 14, 45),
    '_': (10, 82, 315, 20, 42),
    '&': (10, 0, 315, 24, 42),
    '.': (10, 247, 315, 12, 42),
    '?': (10, 211, 315, 20, 42),
    '!': (10, 195, 315, 15, 42),
    '@': (10, 125, 375, 62, 43),
    "'": (10, 281, 315, 14, 42),
}

# Measured on the atlas, not in Skill_Creator's table
ATLAS_GLYPHS = {
    'À': (0, 96, 61, 23, 53),
    'Á': (0, 119, 61, 24, 53),
    'Â': (0, 143, 61, 25, 53),
    'Ä': (2, 168, 63, 23, 51),
    'Æ': (10, 191, 71, 29, 43),
    'Ç': (11, 220, 72, 23, 49),
    'È': (0, 243, 61, 21, 53),
    'É': (0, 264, 61, 21, 53),
    'Ê': (0, 285, 61, 21, 53),
    'Ë': (2, 306, 63, 21, 51),
    'Ì': (1, 327, 61, 16, 53),
    'Í': (1, 343, 61, 15, 53),
    'Î': (1, 358, 61, 17, 53),
    'Ï': (3, 375, 63, 17, 51),
    'Ñ': (0, 392, 61, 23, 53),
    '№': (0, 415, 61, 31, 53),
    'Ò': (0, 446, 61, 23, 53),
    'Ó': (0, 469, 61, 24, 53),
    'Ô': (1, 0, 122, 22, 53),
    'Õ': (1, 22, 122, 23, 53),
    'Ö': (3, 45, 124, 23, 51),
    'Ù': (1, 68, 122, 22, 53),
    'Ú': (1, 90, 122, 22, 53),
    'Û': (1, 112, 122, 22, 53),
    'Ü': (3, 134, 124, 22, 51),
    'ß': (10, 156, 132, 21, 43),
    'à': (10, 202, 192, 21, 43),
    'á': (10, 223, 192, 22, 43),
    'â': (10, 245, 192, 22, 43),
    'ä': (11, 267, 193, 23, 42),
    'æ': (19, 290, 201, 27, 34),
    'ç': (19, 317, 201, 19, 41),
    'è': (10, 336, 192, 21, 42),
    'é': (10, 357, 192, 20, 42),
    'ê': (10, 377, 192, 20, 42),
    'ë': (11, 397, 193, 20, 41),
    'ì': (13, 417, 194, 15, 40),
    'í': (13, 432, 194, 14, 40),
    'î': (13, 446, 194, 16, 40),
    'ï': (14, 462, 195, 17, 39),
    'ñ': (13, 479, 193, 21, 40),
    'ò': (10, 27, 253, 20, 43),
    'ó': (10, 47, 253, 20, 43),
    'ô': (10, 67, 253, 20, 43),
    'õ': (10, 87, 253, 20, 43),
    'ö': (11, 107, 254, 20, 42),
    'ù': (11, 127, 254, 21, 42),
    'ú': (11, 148, 254, 22, 42),
    'û': (11, 170, 254, 22, 42),
    'ü': (12, 192, 255, 23, 41),
    '$': (6, 433, 249, 23, 53),
    '%': (11, 456, 254, 25, 44),
    '^': (11, 481, 254, 19, 26),
    '*': (18, 25, 323, 22, 29),
    '(': (10, 47, 315, 17, 43),
    ')': (10, 64, 315, 17, 43),
    'ª': (0, 102, 305, 16, 22),
    'Ã': (0, 118, 305, 24, 53),
    'Œ': (10, 142, 315, 31, 43),
    '¿': (18, 173, 323, 21, 42),
    '·': (23, 232, 328, 14, 17),
    '{': (7, 330, 312, 17, 50),
    '+': (16, 362, 321, 22, 34),
    '°': (4, 384, 309, 15, 22),
    'œ': (18, 399, 323, 26, 34),
    'º': (11, 425, 316, 15, 27),
    '=': (21, 440, 326, 22, 23),
    '[': (10, 462, 315, 20, 43),
    ']': (9, 482, 314, 20, 44),
    ';': (20, 0, 385, 17, 38),
    '/': (11, 17, 376, 20, 44),
    '#': (11, 37, 376, 24, 44),
    '€': (12, 61, 377, 26, 42),
    '¡': (19, 87, 384, 16, 43),
    'ã': (10, 103, 375, 21, 43),
}
# Line height of Skill_Creator (its 72 characters), before ATLAS_GLYPHS join the table
MAX_CHAR_HEIGHT = max(top + height for top, _, _, _, height in GLYPHS.values())
GLYPHS.update(ATLAS_GLYPHS)
GLYPHS["\u2019"] = GLYPHS["'"]     # typographic apostrophe
GLYPHS["\u221e"] = GLYPHS["@"]     # Skill_Creator draws the infinity sign for @

_atlas = None


def atlas():
    """The atlas as float RGBA (0..1), loaded once."""
    global _atlas
    if _atlas is None:
        _atlas = np.asarray(Image.open(ATLAS).convert("RGBA"), dtype=np.float32) / 255.0
    return _atlas


def missing(text):
    """Characters of the text the font does not have (they are not drawn)."""
    return sorted({c for c in text if c not in "\r\n" and c not in GLYPHS})


def _layout(text):
    """(width, height, shift, [(char, x, y)]): y from the top of the line; shift moves everything down for the
    accents that go above the first line."""
    lines = text.replace("\r", "").split("\n")
    placed = []
    width = max(sum(GLYPHS[c][3] - LETTER_DISPLACEMENT for c in line if c in GLYPHS) for line in lines) + LETTER_DISPLACEMENT
    height = len(lines) * MAX_CHAR_HEIGHT + (len(lines) - 1) * LINE_SPACE
    for row, line in enumerate(lines):
        x, y = 0, row * (MAX_CHAR_HEIGHT + LINE_SPACE)
        for char in line:
            if char in GLYPHS:
                placed.append((char, x, y + GLYPHS[char][0]))
                x += GLYPHS[char][3] - LETTER_DISPLACEMENT
    shift = max(0, -min((y for _, _, y in placed), default=0))
    bottom = max((y + GLYPHS[c][4] for c, _, y in placed), default=0)
    return max(width, 1), max(height, bottom) + shift, shift, placed


def size(text):
    width, height, _, _ = _layout(text)
    return width, height


def render(text, color=(255, 215, 0, 255)):
    """(height, width, 4) uint8 RGBA image of the text, top row first."""
    width, height, shift, placed = _layout(text)
    tint = np.asarray(tuple(color) + (255,) * (4 - len(color)), dtype=np.float32)[:4] / 255.0
    canvas = np.zeros((height, width, 4), dtype=np.float32)
    source = atlas()
    for char, x, y in placed:
        _, gx, gy, gw, gh = GLYPHS[char]
        y += shift
        glyph = source[gy:gy + gh, gx:gx + gw] * tint
        region = canvas[y:y + gh, x:x + gw]
        glyph = glyph[:region.shape[0], :region.shape[1]]
        # Source over, not premultiplied (GDI+ Format32bppArgb)
        sa, da = glyph[..., 3:4], region[..., 3:4]
        out_a = sa + da * (1.0 - sa)
        safe = np.where(out_a > 0, out_a, 1.0)
        region[..., :3] = (glyph[..., :3] * sa + region[..., :3] * da * (1.0 - sa)) / safe
        region[..., 3:4] = out_a
    return np.clip(canvas * 255.0 + 0.5, 0, 255).astype(np.uint8)
