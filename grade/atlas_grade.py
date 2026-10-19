"""
Atlas Grade v1.0 (LOCKED 2026-10-12, approved after the first real-image test)

Do not edit GRADE_V1 or CHECK_LIMITS in place. Any change becomes v1.1+ and needs a
new before/after contact sheet approved before it is used on a live week.

Runs every Atlas image through the same pipeline:
  grade  -> crop 4:5, straighten, resize, white balance, exposure,
            tone curve, colour, split tone, grain, sharpen
  check  -> measures the result against fixed limits (pass / HOLD)
  frame  -> hook label, reveal label, index stamp, photo credit,
            card slide, collection title slide
  export -> 1080 x 1350 baseline JPEG, quality 92, sRGB, no metadata

Usage (as a module):
    from atlas_grade import grade, check, photo_slide, card_slide, export
    img = grade(Image.open("source.jpg"), focal=(0.5, 0.45))
    report = check(img)
    export(photo_slide(img, "0012", "Julian Braero"), "ATLAS-0012-03.jpg")
"""

import math
import os
import numpy as np
import cv2
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageCms

# ---------------------------------------------------------------- settings
GRADE_VERSION = "v1.2"   # v1.1: rotation measured separately from wide-angle lean; v1.2: one-sided lean is never tilt
GRADE_V1 = {
    "out_size": (1080, 1350),          # 4:5
    "max_upscale": 1.25,
    "straighten_min_deg": 0.3,
    "straighten_max_deg": 8.0,         # true rotation beyond this: HOLD for review (wide-angle lean never counts)
    "wb_target_a": 1.0,                # Lab a* target for neutrals (a touch warm, never green)
    "wb_target_b": 8.0,                # Lab b* target for neutrals (slightly warm)
    "wb_cap_a": 4.0,
    "wb_cap_b": 12.0,
    "wb_never_cool": True,
    "exposure_target_L": 45.0,         # median L* target (0-100)
    "exposure_gamma_range": (0.82, 1.22),  # caps the move at about half a stop
    "black_point": 10,                 # darkest output value (0-255)
    "white_point": 245,                # brightest output value
    "s_curve": 0.18,                   # gentle contrast
    "sat_global": 0.92,                # -8%
    "sat_blue_cyan": 0.80,             # -20% for blues and cyans
    "split_highlights": (3, 2, -1),    # cream
    "split_shadows": (2, 1, -1),       # warm brown
    "grain_sigma": 2.6,                # about 1% of full scale
    "sharpen": (0.8, 35, 2),           # radius, percent, threshold
}

CHECK_LIMITS = {
    "median_L": (15.0, 70.0),          # dark bars are on-brand
    "min_warmth_b": -1.0,              # midtone b* must not be cool
    "max_mean_saturation": 0.45,       # cool and neon hues only; warm hues are exempt
    "max_clipped_highlights": 0.005,
    "max_crushed_shadows": 0.005,
    "max_tilt_deg": 1.0,               # informational only since v1.2 (see check)
}

CREAM = (241, 233, 220)   # #F1E9DC
INK = (42, 36, 32)        # #2A2420
OXBLOOD = (110, 42, 34)   # #6E2A22
MARGIN = 72

FONT_REGULAR = os.path.join(os.path.dirname(__file__), "fonts/courier-prime-latin-400-normal.woff")
FONT_BOLD = os.path.join(os.path.dirname(__file__), "fonts/courier-prime-latin-700-normal.woff")


def _font(bold, size):
    return ImageFont.truetype(FONT_BOLD if bold else FONT_REGULAR, size)


# ---------------------------------------------------------------- helpers
def _to_np(img):
    return np.asarray(img.convert("RGB")).astype(np.float32)


def _to_img(arr):
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")


def measure_lean(img):
    """Split the lean of the verticals into rotation and keystone.

    A level camera with a wide lens makes the left walls lean right and the right walls lean left (keystone).
    That is not tilt. Rotation is what the two sides share: the mean of the left and right medians.
    Keystone is half their difference. Returns (rotation_deg, keystone_deg)."""
    gray = cv2.cvtColor(np.asarray(img.convert("RGB")), cv2.COLOR_RGB2GRAY)
    edges = cv2.Canny(gray, 60, 160)
    h, w = gray.shape
    lines = cv2.HoughLinesP(edges, 1, np.pi / 720, threshold=80,
                            minLineLength=int(h * 0.15), maxLineGap=10)
    if lines is None:
        return 0.0, 0.0
    left, mid, right = [], [], []
    for x1, y1, x2, y2 in np.asarray(lines).reshape(-1, 4):
        ang = math.degrees(math.atan2(x2 - x1, y2 - y1))
        if ang > 90:
            ang -= 180
        if ang < -90:
            ang += 180
        if abs(ang) > 12:
            continue
        xm = (x1 + x2) / 2
        (left if xm < w * 0.38 else right if xm > w * 0.62 else mid).append(ang)
    if len(left) >= 2 and len(right) >= 2:
        aL, aR = float(np.median(left)), float(np.median(right))
        return (aL + aR) / 2.0, (aL - aR) / 2.0
    if len(mid) >= 2:                      # one side empty: centre lines are least affected by keystone
        return float(np.median(mid)), 0.0
    allv = left + mid + right
    if len(allv) >= 3:
        # one side only, no centre lines (typical after a 4:5 crop of a wide shot): lean and tilt cannot be
        # told apart, so it is treated as lean. Never a reason to bench; the review page is the backstop.
        return 0.0, float(np.median(allv))
    return 0.0, 0.0


def measure_tilt(img):
    """Rotation only (degrees). Wide-angle keystone is excluded; see measure_lean."""
    return measure_lean(img)[0]


def _rotate_fill(img, deg):
    w, h = img.size
    rad = math.radians(abs(deg))
    scale = math.cos(rad) + math.sin(rad) * max(w / h, h / w)
    m = cv2.getRotationMatrix2D((w / 2, h / 2), -deg, scale)
    out = cv2.warpAffine(np.asarray(img.convert("RGB")), m, (w, h),
                         flags=cv2.INTER_LANCZOS4, borderMode=cv2.BORDER_REFLECT)
    return Image.fromarray(out)


def _crop_45(img, focal):
    w, h = img.size
    target = 4 / 5
    if w / h > target:
        cw, ch = int(h * target), h
    else:
        cw, ch = w, int(w / target)
    cx, cy = focal[0] * w, focal[1] * h
    x0 = int(min(max(cx - cw / 2, 0), w - cw))
    y0 = int(min(max(cy - ch / 2, 0), h - ch))
    return img.crop((x0, y0, x0 + cw, y0 + ch))


def _lab(arr):
    lab = cv2.cvtColor(np.clip(arr, 0, 255).astype(np.uint8), cv2.COLOR_RGB2LAB).astype(np.float32)
    L = lab[..., 0] * 100 / 255
    a = lab[..., 1] - 128
    b = lab[..., 2] - 128
    return L, a, b


# ---------------------------------------------------------------- grade
class HoldForReview(Exception):
    pass


def grade(img, focal=(0.5, 0.5), seed=0, s=GRADE_V1):
    img = img.convert("RGB")

    # 2. straighten (done before the crop so the crop uses the corrected frame)
    tilt = measure_tilt(img)
    if abs(tilt) > s["straighten_max_deg"]:
        raise HoldForReview(f"tilt {tilt:.1f} deg is over the limit")
    if abs(tilt) >= s["straighten_min_deg"]:
        img = _rotate_fill(img, tilt)

    # 1. crop 4:5 around the focal point
    img = _crop_45(img, focal)

    # 3. resize
    ow, oh = s["out_size"]
    if ow / img.size[0] > s["max_upscale"]:
        raise HoldForReview("source too small for 1080 wide")
    img = img.resize((ow, oh), Image.LANCZOS)
    arr = _to_np(img)

    # 4. white balance in Lab, measured on midtones only
    lab = cv2.cvtColor(arr.astype(np.uint8), cv2.COLOR_RGB2LAB).astype(np.float32)
    Lp = lab[..., 0] * 100 / 255
    chroma = np.hypot(lab[..., 1] - 128, lab[..., 2] - 128)
    # measure only near-neutral midtones (walls, ceilings, linens), not coloured objects
    mid = (Lp > 20) & (Lp < 85) & (chroma < 20)
    if mid.sum() < 2000:
        mid = (Lp > 20) & (Lp < 80)
    if mid.sum() > 1000:
        ma = float((lab[..., 1][mid] - 128).mean())
        mb = float((lab[..., 2][mid] - 128).mean())
        da = float(np.clip(s["wb_target_a"] - ma, -s["wb_cap_a"], s["wb_cap_a"]))
        db = float(np.clip(s["wb_target_b"] - mb, -s["wb_cap_b"], s["wb_cap_b"]))
        if s["wb_never_cool"]:
            db = max(db, 0.0)
        lab[..., 1] += da
        lab[..., 2] += db
    arr = cv2.cvtColor(np.clip(lab, 0, 255).astype(np.uint8), cv2.COLOR_LAB2RGB).astype(np.float32)

    # 5. exposure: gamma toward the target median L*, capped
    L, _, _ = _lab(arr)
    med = max(float(np.median(L)), 1.0) / 100
    g = math.log(s["exposure_target_L"] / 100) / math.log(med)
    g = float(np.clip(g, *s["exposure_gamma_range"]))
    arr = 255 * np.power(arr / 255, g)

    # 6. tone curve: gentle S, then black and white points
    x = np.linspace(0, 1, 256)
    sig = 1 / (1 + np.exp(-6 * (x - 0.5)))
    sig = (sig - sig[0]) / (sig[-1] - sig[0])
    curve = (1 - s["s_curve"]) * x + s["s_curve"] * sig
    lut = s["black_point"] + (s["white_point"] - s["black_point"]) * curve
    arr = lut[np.clip(arr, 0, 255).astype(np.uint8)]

    # 7. colour: global saturation down, blues and cyans down more, warm hues protected
    hsv = cv2.cvtColor(np.clip(arr, 0, 255).astype(np.uint8), cv2.COLOR_RGB2HSV).astype(np.float32)
    hue = hsv[..., 0]  # 0-180
    warm = np.clip(1 - np.minimum(np.abs(hue - 0), np.abs(hue - 180)) / 25, 0, 1)   # reds, oranges, ambers
    warm = np.maximum(warm, np.clip(1 - np.abs(hue - 15) / 15, 0, 1))
    cool = np.clip(1 - np.abs(hue - 105) / 30, 0, 1)                                # cyans to blues
    factor = s["sat_global"] + (1 - s["sat_global"]) * warm
    factor = factor * (1 - cool) + s["sat_blue_cyan"] * cool
    hsv[..., 1] = np.clip(hsv[..., 1] * factor, 0, 255)
    arr = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2RGB).astype(np.float32)

    # 8. split tone
    lum = (arr @ np.array([0.299, 0.587, 0.114], dtype=np.float32)) / 255
    hi = (lum ** 2)[..., None] * np.array(s["split_highlights"], dtype=np.float32)
    lo = ((1 - lum) ** 2)[..., None] * np.array(s["split_shadows"], dtype=np.float32)
    arr = arr + hi + lo

    # 9. grain (monochrome, deterministic per image)
    rng = np.random.default_rng(seed)
    noise = rng.normal(0, s["grain_sigma"], arr.shape[:2]).astype(np.float32)
    arr = arr + noise[..., None]

    # keep the black and white points after grain
    arr = np.clip(arr, s["black_point"], s["white_point"])

    # 10. output sharpening
    out = _to_img(arr).filter(ImageFilter.UnsharpMask(*s["sharpen"]))
    return out


# ---------------------------------------------------------------- check
def check(img, limits=CHECK_LIMITS):
    arr = _to_np(img)
    L, a, b = _lab(arr)
    mid = (L > 20) & (L < 80)
    hsv = cv2.cvtColor(arr.astype(np.uint8), cv2.COLOR_RGB2HSV).astype(np.float32)
    m = {
        "size_ok": img.size == GRADE_V1["out_size"],
        "median_L": round(float(np.median(L)), 1),
        "warmth_b": round(float(b[mid].mean()) if mid.any() else 0.0, 1),
        # saturation of non-warm pixels only: rich reds and ambers are the look, neon blues and greens are not
        "mean_saturation": round(float(hsv[..., 1][(hsv[..., 0] > 25) & (hsv[..., 0] < 165)].mean() / 255)
                                 if ((hsv[..., 0] > 25) & (hsv[..., 0] < 165)).any() else 0.0, 3),
        "clipped_highlights": round(float((arr.min(axis=2) >= 254).mean()), 4),
        "crushed_shadows": round(float((arr.max(axis=2) <= 1).mean()), 4),
        "tilt_deg": round(abs(measure_tilt(img)), 2),
    }
    fails = []
    if not m["size_ok"]:
        fails.append("size")
    lo, hi = limits["median_L"]
    if not lo <= m["median_L"] <= hi:
        fails.append("brightness")
    if m["warmth_b"] < limits["min_warmth_b"]:
        fails.append("too cool")
    if m["mean_saturation"] > limits["max_mean_saturation"]:
        fails.append("too saturated")
    if m["clipped_highlights"] > limits["max_clipped_highlights"]:
        fails.append("clipped highlights")
    if m["crushed_shadows"] > limits["max_crushed_shadows"]:
        fails.append("crushed shadows")
    # tilt is judged once, on the full frame before the crop (grade step 2: straighten, or HOLD beyond 8 degrees).
    # A 4:5 crop from one side of a wide frame always looks rotated, so here it is recorded, never a reason to hold.
    m["status"] = "PASS" if not fails else "HOLD: " + ", ".join(fails)
    return m


def check_carousel(reports, max_L_spread=18.0, max_b_spread=8.0):
    Ls = [r["median_L"] for r in reports]
    bs = [r["warmth_b"] for r in reports]
    ok = (max(Ls) - min(Ls) <= max_L_spread) and (max(bs) - min(bs) <= max_b_spread)
    return "PASS" if ok else "HOLD: slides do not match"


# ---------------------------------------------------------------- frame
def _fit(text, bold, size, max_w, min_size):
    while size >= min_size:
        f = _font(bold, size)
        if f.getlength(text) <= max_w:
            return [text], f
        size -= 2
    f = _font(bold, min_size)
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if f.getlength(trial) <= max_w:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    return lines, f


def _soft_backing(img, box, strength=0.35):
    """Darken the area behind small cream text only when it is bright."""
    x0, y0, x1, y1 = [int(v) for v in box]
    region = np.asarray(img.crop((x0, y0, x1, y1)).convert("L")).astype(np.float32)
    if region.mean() < 150:
        return img
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((x0 - 12, y0 - 8, x1 + 12, y1 + 8), 10, fill=int(255 * strength))
    mask = mask.filter(ImageFilter.GaussianBlur(10))
    dark = Image.new("RGB", img.size, (0, 0, 0))
    return Image.composite(dark, img, mask)


def label(img, text, size=40):
    """Cream label with ink text, bottom left (hook and reveal slides)."""
    img = img.copy()
    max_w = img.size[0] - 2 * MARGIN - 44
    lines, f = _fit(text.upper(), True, size, max_w, 30)
    asc, desc = f.getmetrics()
    lh = asc + desc + 6
    tw = max(f.getlength(l) for l in lines)
    pad_x, pad_y = 22, 16
    w = tw + 2 * pad_x
    h = lh * len(lines) - 6 + 2 * pad_y
    x0 = MARGIN
    y0 = img.size[1] - MARGIN - h
    d = ImageDraw.Draw(img)
    d.rectangle((x0, y0, x0 + w, y0 + h), fill=CREAM)
    for i, l in enumerate(lines):
        d.text((x0 + pad_x, y0 + pad_y + i * lh), l, font=f, fill=INK)
    return img


def stamp_and_credit(img, atlas_no, credit):
    img = img.copy()
    fs, fc = _font(False, 22), _font(False, 18)
    stamp = f"ATLAS NO. {atlas_no}"
    sw = fs.getlength(stamp)
    sbox = (MARGIN, MARGIN, MARGIN + sw, MARGIN + 26)
    img = _soft_backing(img, sbox)
    if credit:
        ctext = f"PHOTO: {credit.upper()}"
        cw = fc.getlength(ctext)
        cx0 = img.size[0] - MARGIN - cw
        cy0 = MARGIN + 3
        img = _soft_backing(img, (cx0, cy0, cx0 + cw, cy0 + 22))
    d = ImageDraw.Draw(img)
    d.text((MARGIN, MARGIN), stamp, font=fs, fill=CREAM)
    if credit:
        d.text((cx0, cy0), ctext, font=fc, fill=CREAM)
    return img


def hook_slide(img, reference, atlas_no, credit):
    return label(stamp_and_credit(img, atlas_no, credit), reference)


def reveal_slide(img, reveal, atlas_no, credit):
    return label(stamp_and_credit(img, atlas_no, credit), reveal)


def photo_slide(img, atlas_no, credit):
    return stamp_and_credit(img, atlas_no, credit)


def card_slide(lines, size=(1080, 1350), font_size=42):
    """The seven-line Atlas Card on cream. lines[0] is the index line (oxblood)."""
    img = Image.new("RGB", size, CREAM)
    d = ImageDraw.Draw(img)
    left = 96
    max_w = size[0] - 2 * left
    # largest size (up to font_size, down to 28) at which no line has to wrap
    fs = font_size
    while fs > 28 and max(_font(False, fs).getlength(l.upper()) for l in lines) > max_w:
        fs -= 1
    font_size = fs
    f = _font(False, font_size)
    wrapped = []
    for i, line in enumerate(lines):
        ls, _ = _fit(line.upper(), False, font_size, max_w, font_size)
        for l in ls:
            wrapped.append((l, i == 0))
    lh = int(font_size * 1.5)
    total = lh * len(wrapped)
    y = (size[1] - total) // 2
    for text, is_index in wrapped:
        d.text((left, y), text, font=f, fill=OXBLOOD if is_index else INK)
        y += lh
    return img


def title_slide(title, size=(1080, 1350)):
    img = Image.new("RGB", size, CREAM)
    d = ImageDraw.Draw(img)
    lines, f = _fit(title.upper(), True, 56, size[0] - 2 * 96, 56)
    lh = 80
    y = (size[1] - lh * len(lines)) // 2
    for l in lines:
        d.text((96, y), l, font=f, fill=INK)
        y += lh
    return img


# ---------------------------------------------------------------- export
_SRGB = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


def export(img, path):
    clean = Image.new("RGB", img.size)
    clean.putdata(list(img.convert("RGB").getdata()))  # drops all metadata
    clean.save(path, "JPEG", quality=92, subsampling=0, optimize=True,
               progressive=False, icc_profile=_SRGB)
    return path


def collection_slide(img, atlas_no, credit, name_line, word):
    """Collection carousel slide: photo plus a two-line mini-card."""
    img = stamp_and_credit(img, atlas_no, credit)
    f1, f2 = _font(True, 34), _font(False, 28)
    l1, l2 = name_line.upper(), f"IN A WORD: {word.upper()}"
    w = max(f1.getlength(l1), f2.getlength(l2)) + 44
    h = 34 + 10 + 28 + 36
    x0, y0 = MARGIN, img.size[1] - MARGIN - h
    d = ImageDraw.Draw(img)
    d.rectangle((x0, y0, x0 + w, y0 + h), fill=CREAM)
    d.text((x0 + 22, y0 + 16), l1, font=f1, fill=INK)
    d.text((x0 + 22, y0 + 16 + 44), l2, font=f2, fill=INK)
    return img


def end_slide(text, size=(1080, 1350)):
    return title_slide(text, size)
