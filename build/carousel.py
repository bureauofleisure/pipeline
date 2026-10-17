"""Bureau of Leisure carousel layout v2.

Slide 1  cover: the best photo. Brand + index top left, place + IN A WORD on a cream label bottom left.
Slide 2  second chance: next photo with REMINDS US OF on a cream label (Instagram often re-shows slide 2).
Slides 3..n-1  photos with a small brand + index stamp.
Last slide  the index card on cream.
Every photo slide carries the photo credit top right.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "grade"))
import numpy as np  # noqa: E402
from PIL import Image, ImageDraw, ImageFilter  # noqa: E402
from atlas_grade import _font, _fit, card_slide, label, CREAM, INK, MARGIN  # noqa: E402


def _backing(img, box):
    """Darken the area behind small cream text in proportion to how bright it is, so the stamp and the
    credit read on a white ceiling as well as on a dark bar. Grade v1.0's own backing is left untouched."""
    x0, y0, x1, y1 = [int(v) for v in box]
    region = np.asarray(img.crop((x0, y0, x1, y1)).convert("L")).astype(np.float32)
    mean = float(region.mean()) if region.size else 0.0
    if mean < 70:
        return img
    strength = min(0.72, 0.22 + (mean - 70) / 185 * 0.5)
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((x0 - 14, y0 - 9, x1 + 14, y1 + 9), 10, fill=int(255 * strength))
    mask = mask.filter(ImageFilter.GaussianBlur(9))
    dark = Image.new("RGB", img.size, (12, 10, 9))
    return Image.composite(dark, img, mask)

BRAND = "BUREAU OF LEISURE"
LAYOUT_VERSION = "v2.1"   # bump when a layout change should re-render every post


def _top_line(img, left_text, credit, size=22):
    img = img.copy()
    fs, fc = _font(False, size), _font(False, 18)
    img = _backing(img, (MARGIN, MARGIN, MARGIN + fs.getlength(left_text), MARGIN + size + 4))
    cx = None
    credit = credit or "UNCREDITED"
    if credit:
        ct = f"PHOTO: {credit.upper()}"
        cw = fc.getlength(ct)
        cx = img.size[0] - MARGIN - cw
        img = _backing(img, (cx, MARGIN + 3, cx + cw, MARGIN + 25))
    d = ImageDraw.Draw(img)
    d.text((MARGIN, MARGIN), left_text, font=fs, fill=CREAM)
    if credit:
        d.text((cx, MARGIN + 3), ct, font=fc, fill=CREAM)
    return img


def cover_slide(img, index_no, place_line, word, credit):
    img = _top_line(img, f"{BRAND} NO. {index_no}", credit, size=24)
    max_w = img.size[0] - 2 * MARGIN - 48
    # the place line shrinks, then wraps, so a long name never runs off the slide
    lines, f1 = _fit(place_line.upper(), True, 38, max_w, 28)
    f2 = _font(False, 28)
    l2 = f"IN A WORD: {word.upper()}"
    asc, desc = f1.getmetrics()
    lh = asc + desc + 4
    w = max(max(f1.getlength(l) for l in lines), f2.getlength(l2)) + 48
    h = lh * len(lines) + 10 + 28 + 40
    x0, y0 = MARGIN, img.size[1] - MARGIN - h
    d = ImageDraw.Draw(img)
    d.rectangle((x0, y0, x0 + w, y0 + h), fill=CREAM)
    y = y0 + 18
    for l in lines:
        d.text((x0 + 24, y), l, font=f1, fill=INK)
        y += lh
    d.text((x0 + 24, y + 6), l2, font=f2, fill=INK)
    return img


def reminds_slide(img, index_no, reminds, credit):
    img = _top_line(img, f"{BRAND} NO. {index_no}", credit, size=20)
    return label(img, f"REMINDS US OF: {reminds}", size=36)


def photo_slide(img, index_no, credit):
    return _top_line(img, f"{BRAND} NO. {index_no}", credit, size=20)


def build(photos, index_no, place_line, word, reminds, card_lines, credit):
    """photos: graded images, best first. Returns the slide list."""
    slides = [cover_slide(photos[0], index_no, place_line, word, credit)]
    rest = photos[1:]
    if rest and reminds:
        slides.append(reminds_slide(rest[0], index_no, reminds, credit))
        rest = rest[1:]
    slides += [photo_slide(p, index_no, credit) for p in rest]
    slides.append(card_slide(card_lines))
    return slides
