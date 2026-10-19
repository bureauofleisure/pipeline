"""Build every post in batches/*.json into finished, hosted slides.

For each post:
  1. validate the card copy (7 lines, all caps, ends on IN A WORD, word from the bank)
  2. download the source photos; drop near-duplicates
  3. grade each with Grade v1.0 and check it; drop any that HOLD
  4. bench the post if fewer than MIN_PHOTOS pass, or if the set does not match in tone
  5. rank the survivors (sharpness, exposure, a calm bottom-left for the cover label),
     unless the batch names a cover
  6. build the slides and export them to docs/posts/<index>/NN.jpg
  7. write docs/posts/<index>/post.json (with alt text), and docs/manifest.json

docs/ is published to GitHub Pages, so every slide gets a public URL that Instagram can fetch.
A post is rebuilt only when its batch entry changes, so re-runs are cheap and safe.
One bad post never stops the others: every failure is caught and recorded.
"""
import glob
import shutil
import hashlib
import io
import math
import json
import os
import re
import sys
import time
import urllib.request

import cv2
import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "grade"))
sys.path.insert(0, HERE)
from atlas_grade import grade, check, check_carousel, export, _crop_45, GRADE_VERSION, HoldForReview  # noqa: E402
import carousel  # noqa: E402
import archive  # noqa: E402

MIN_PHOTOS = 4
MAX_PHOTOS = 6
# How far the photos in one carousel may differ in brightness (L*) and warmth (b*).
# Set wider than the grade module's defaults because one venue has dark bars and bright rooms.
# To be validated on the first real same-venue set.
SET_L_SPREAD = 25.0
SET_B_SPREAD = 12.0
# Quality gate, before grading. No enlarging at all: the 4:5 crop must already be at least
# 1080 x 1350. Soft or heavily compressed photos are rejected too.
MIN_CROP = (1080, 1350)
MIN_SHARPNESS = 60.0       # Laplacian variance at 1080 wide; sharp interiors score well over 100
MAX_BLOCKINESS = 1.35      # JPEG 8-pixel block edges relative to neighbouring pixels
BASE_URL = os.environ.get("PAGES_BASE_URL", "https://bureauofleisure.github.io/pipeline")
UA = "Mozilla/5.0 (compatible; BureauOfLeisureBot/1.0; +https://bureauofleisure.co)"
WORD_BANK = {w.strip().upper() for w in open(os.path.join(ROOT, "build", "word_bank.txt")) if w.strip()}


# ---------------------------------------------------------------- copy
def validate_card(p):
    """Returns a list of problems. Empty means the card is clean."""
    card = p.get("card") or []
    problems = []
    if len(card) != 7:
        problems.append(f"card has {len(card)} lines, needs 7")
    for i, line in enumerate(card, 1):
        if line != line.upper():
            problems.append(f"card line {i} is not all caps")
        if "[" in line or "]" in line:
            problems.append(f"card line {i} still has a placeholder")
        if len(line) > 60:
            problems.append(f"card line {i} is over 60 characters")
    if card:
        if not card[0].startswith(f"{carousel.BRAND} NO. {p.get('index', '')}"):
            problems.append("card line 1 must be the brand and index")
        if not card[-1].startswith("IN A WORD: "):
            problems.append("last card line must be IN A WORD")
        else:
            word = card[-1].replace("IN A WORD: ", "").strip(" .")
            if word != p.get("word", "").upper():
                problems.append("IN A WORD does not match the word field")
            if WORD_BANK and word not in WORD_BANK:
                problems.append(f"'{word}' is not in the word bank")
    reminds = p.get("reminds", "")
    if reminds and len(reminds.split()) > 7:
        problems.append("REMINDS US OF is over six words")
    return problems


# ---------------------------------------------------------------- photos
def fetch(src):
    """Returns (image, original bytes, content type). The bytes are kept untouched for the originals archive."""
    if src.startswith("http"):
        req = urllib.request.Request(src, headers={"User-Agent": UA, "Accept": "image/*"})
        last = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    data = r.read()
                    ctype = r.headers.get("Content-Type", "").split(";")[0].strip()
                    return Image.open(io.BytesIO(data)).convert("RGB"), data, ctype
            except Exception as e:  # noqa: BLE001
                last = e
                time.sleep(2 * (attempt + 1))
        raise last
    data = open(os.path.join(ROOT, src), "rb").read()
    return Image.open(io.BytesIO(data)).convert("RGB"), data, ""


def ahash(img):
    g = np.asarray(img.convert("L").resize((16, 16), Image.LANCZOS), dtype=np.float32)
    return (g > g.mean()).flatten()


def is_duplicate(h, seen, max_bits=12):
    return any(int((h != s).sum()) <= max_bits for s in seen)


BED_WORDS = re.compile(r"bed(room)?s?\b|guest-?rooms?|suites?\b|sleep|king-?room|queen-?room|bunk|pillow", re.I)


def looks_like_bedroom(src, alt=""):
    """Beds never make the cut. Cheap filter on the filename and any caption the harvest kept."""
    name = src.split("?")[0].rsplit("/", 1)[-1]
    return bool(BED_WORDS.search(name) or BED_WORDS.search(alt or ""))


def measure_keystone(img):
    """Keystone from the grade's lean measurement, so the build and the grade agree on what is tilt."""
    from atlas_grade import measure_lean
    # measure_lean reports converging-upward as negative; fix_keystone expects positive for a narrow top
    return -measure_lean(img)[1]


def fix_keystone(img, deg):
    """Pull the verticals back to vertical and crop away the empty wedges."""
    w, h = img.size
    dx = math.tan(math.radians(abs(deg))) * h / 2.0
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    if deg > 0:   # top narrower than it should be: push the top corners outward
        dst = np.float32([[-dx, 0], [w + dx, 0], [w, h], [0, h]])
    else:         # bottom narrower
        dst = np.float32([[0, 0], [w, 0], [w + dx, h], [-dx, h]])
    m = cv2.getPerspectiveTransform(src, dst)
    out = cv2.warpPerspective(np.asarray(img.convert("RGB")), m, (w, h), flags=cv2.INTER_LANCZOS4, borderMode=cv2.BORDER_REFLECT)
    crop = int(dx) + 2
    return Image.fromarray(out).crop((crop, 0, w - crop, h))


KEYSTONE_MIN, KEYSTONE_MAX = 0.8, 3.0   # gentle correction only; stronger wide-angle lean is left as shot, never benched


def quality(raw, focal):
    """Returns (ok, reason). Checks the 4:5 crop at output size, never enlarged."""
    crop = _crop_45(raw, focal)
    if crop.size[0] < MIN_CROP[0] or crop.size[1] < MIN_CROP[1]:
        return False, f"too small ({crop.size[0]}x{crop.size[1]} after crop, need {MIN_CROP[0]}x{MIN_CROP[1]})"
    g = np.asarray(crop.resize(MIN_CROP, Image.LANCZOS).convert("L"), dtype=np.float32)
    sharp = cv2.Laplacian(g, cv2.CV_32F).var()
    if sharp < MIN_SHARPNESS:
        return False, f"too soft (sharpness {sharp:.0f}, need {MIN_SHARPNESS:.0f})"
    # blockiness: differences across 8-pixel JPEG block boundaries vs inside blocks
    gs = np.asarray(crop.convert("L"), dtype=np.float32)
    dx = np.abs(np.diff(gs, axis=1))
    at = dx[:, 7::8].mean()
    inside = np.delete(dx, np.s_[7::8], axis=1).mean()
    block = at / max(inside, 1e-6)
    if block > MAX_BLOCKINESS:
        return False, f"compression artifacts (blockiness {block:.2f})"
    return True, ""


def score(img):
    """Higher is better. Measured on the ungraded photo so grain does not count as sharpness."""
    g = np.asarray(img.convert("L"), dtype=np.float32)
    sharp = min(cv2.Laplacian(g, cv2.CV_32F).var() / 400.0, 1.0)
    expo = 1.0 - min(abs(float(np.median(g)) / 255 * 100 - 45) / 45, 1.0)
    h, w = g.shape
    corner = g[int(h * 0.78):, : int(w * 0.6)]
    calm = 1.0 - min(float(corner.std()) / 64.0, 1.0)
    return round(0.45 * sharp + 0.25 * expo + 0.30 * calm, 4)


def alt_text(p):
    return f"{p['place']}, {p['neighborhood']}, {p['city']}. {p.get('alt_hint', 'Interior view.')}"[:1000]


def entry_hash(p):
    return hashlib.sha1((json.dumps(p, sort_keys=True) + GRADE_VERSION + carousel.LAYOUT_VERSION).encode()).hexdigest()[:12]


# ---------------------------------------------------------------- build
def build_post(p, out_root):
    idx = p["index"]
    out_dir = os.path.join(out_root, "posts", idx)
    done = os.path.join(out_dir, "post.json")
    h = entry_hash(p)
    if os.path.exists(done):
        prev = json.load(open(done))
        if prev.get("entry_hash") == h:
            return prev           # unchanged since last build
    result = {"index": idx, "place": p["place"], "status": "BUILT", "notes": [],
              "grade": GRADE_VERSION, "entry_hash": h, "built": time.strftime("%Y-%m-%d"),
              # everything the sheet needs, passed straight through from the batch file
              "edited_at": p.get("edited_at", ""),      # set by the edits job when it applies a request
              "meta": {k: p.get(k, "") for k in ("neighborhood", "city", "category", "word", "reminds", "credit",
                                                  "designer", "designer_handle", "venue_handle",
                                                  "photographer_handle", "source_handle", "source_url", "bring")}}

    problems = validate_card(p)
    if problems:
        result["status"] = "BENCHED"
        result["notes"] = ["copy: " + x for x in problems]
        return finish(result, out_dir, done)

    graded, seen, originals = [], [], []
    focal = tuple(p.get("focal", (0.5, 0.5)))
    drop = {int(n) for n in p.get("drop_photos", [])}          # 1-based, set by an edit
    manual_rotate = {int(k): float(v) for k, v in (p.get("rotate") or {}).items()}
    for i, src in enumerate(p["photos"]):
        if i + 1 in drop:
            result["notes"].append(f"photo {i + 1} dropped by edit")
            continue
        if looks_like_bedroom(src):
            result["notes"].append(f"photo {i + 1} skipped: looks like a bedroom")
            continue
        try:
            raw, raw_bytes, raw_type = fetch(src)
        except Exception as e:  # noqa: BLE001  unreachable, blocked, not an image
            result["notes"].append(f"photo {i + 1} could not be fetched ({type(e).__name__})")
            continue
        if i + 1 in manual_rotate and manual_rotate[i + 1]:
            raw = raw.rotate(-manual_rotate[i + 1], resample=Image.BICUBIC, expand=False)
            result["notes"].append(f"photo {i + 1} rotated {manual_rotate[i + 1]:+.1f} deg by edit")
        # straighten first (true rotation only), so a rotated wide-angle shot shows its lean clearly
        from atlas_grade import measure_lean, _rotate_fill
        rot0, _ = measure_lean(raw)
        if 0.3 <= abs(rot0) <= 8.0:
            raw = _rotate_fill(raw, rot0)
            result["notes"].append(f"photo {i + 1} straightened {rot0:+.1f} deg")
        ks = measure_keystone(raw)
        if KEYSTONE_MIN <= abs(ks) <= KEYSTONE_MAX:
            raw = fix_keystone(raw, ks)
            result["notes"].append(f"photo {i + 1} keystone corrected {ks:+.1f} deg")
        elif abs(ks) > KEYSTONE_MAX:
            result["notes"].append(f"photo {i + 1} wide-angle lean {ks:+.1f} deg left as shot")
        hs = ahash(raw)
        if is_duplicate(hs, seen):
            result["notes"].append(f"photo {i + 1} is a near-duplicate, dropped")
            continue
        seen.append(hs)
        ok, why = quality(raw, focal)
        if not ok:
            result["notes"].append(f"photo {i + 1} rejected: {why}")
            continue
        try:
            img = grade(raw, focal=focal, seed=i)
        except HoldForReview as e:
            result["notes"].append(f"photo {i + 1} held: {e}")
            continue
        rep = check(img)
        if rep["status"] != "PASS":
            result["notes"].append(f"photo {i + 1} {rep['status']}")
            continue
        pre = _crop_45(raw, focal).resize((1080, 1350), Image.LANCZOS)
        graded.append({"i": i, "score": score(pre), "img": img, "report": rep})
        # every photo that passes the gate is archived at source resolution, used in the carousel or not
        originals.append({"n": i + 1, "url": src, "bytes": raw_bytes, "content_type": raw_type,
                          "width": raw.size[0], "height": raw.size[1], "used_as_slide": False})

    if len(graded) < MIN_PHOTOS:
        result["status"] = "BENCHED"
        result["notes"].append(f"only {len(graded)} photos passed, need {MIN_PHOTOS}")
        return finish(result, out_dir, done)

    cover = p.get("cover")            # 1-based index into p["photos"], set by the weekly review
    graded.sort(key=lambda g: g["score"], reverse=True)
    if cover:
        chosen = [g for g in graded if g["i"] == cover - 1]
        if chosen:
            graded = chosen + [g for g in graded if g["i"] != cover - 1]
        else:
            result["notes"].append(f"named cover (photo {cover}) did not pass; best scoring photo used")
    graded = graded[:MAX_PHOTOS]

    # the set must hang together in tone: drop the odd one out (never the cover) until it does
    while check_carousel([g["report"] for g in graded], SET_L_SPREAD, SET_B_SPREAD) != "PASS" and len(graded) > MIN_PHOTOS:
        Ls = [g["report"]["median_L"] for g in graded]
        bs = [g["report"]["warmth_b"] for g in graded]
        mL, mb = float(np.median(Ls)), float(np.median(bs))
        dist = [abs(L - mL) / SET_L_SPREAD + abs(b - mb) / SET_B_SPREAD for L, b in zip(Ls, bs)]
        dist[0] = -1                       # keep the cover
        drop = int(np.argmax(dist))
        result["notes"].append(f"photo {graded[drop]['i'] + 1} dropped: did not match the set")
        graded.pop(drop)
    match = check_carousel([g["report"] for g in graded], SET_L_SPREAD, SET_B_SPREAD)
    if match != "PASS":
        result["status"] = "BENCHED"
        result["notes"].append(match)
        return finish(result, out_dir, done)

    photos = [g["img"] for g in graded]
    place_line = f"{p['place']}. {p['neighborhood']}."
    slides = carousel.build(photos, idx, place_line, p["word"], p.get("reminds", ""), p["card"], p.get("credit", ""))
    os.makedirs(out_dir, exist_ok=True)
    urls = []
    for n, s in enumerate(slides, 1):
        name = f"{n:02d}.jpg"
        export(s, os.path.join(out_dir, name))
        urls.append(f"{BASE_URL}/posts/{idx}/{name}")
    result["slides"] = urls
    used = {g["i"] + 1 for g in graded}
    for o in originals:
        o["used_as_slide"] = o["n"] in used
    result["archive"] = archive.store(idx, {"place": p["place"], "neighborhood": p.get("neighborhood", ""), "city": p.get("city", ""),
                                            "designer": p.get("designer", ""), "credit": p.get("credit", ""),
                                            "source_url": p.get("source_url", ""), "licence": p.get("licence", "")}, originals)
    result["cover_source_photo"] = graded[0]["i"] + 1
    # which source photo (1-based, as listed in the batch) sits behind each photo slide, so a review can name a cover
    result["slide_sources"] = [g["i"] + 1 for g in graded]
    result["caption_card"] = "\n".join(p["card"])
    result["alt_text"] = alt_text(p)
    return finish(result, out_dir, done)


def finish(result, out_dir, done):
    os.makedirs(out_dir, exist_ok=True)
    json.dump(result, open(done, "w"), indent=2)
    return result


def main():
    out_root = os.path.join(ROOT, "docs")
    os.makedirs(out_root, exist_ok=True)
    open(os.path.join(out_root, ".nojekyll"), "w").close()
    # the public link page and the profile seal live at the site root, next to the posts
    site = os.path.join(os.path.dirname(os.path.abspath(__file__)), "site")
    shutil.copy(os.path.join(site, "index.html"), os.path.join(out_root, "index.html"))
    shutil.copy(os.path.join(ROOT, "brand", "avatar.jpg"), os.path.join(out_root, "avatar.jpg"))
    posts = []
    for path in sorted(glob.glob(os.path.join(ROOT, "batches", "*.json"))):
        try:
            batch = json.load(open(path))["posts"]
        except Exception as e:  # noqa: BLE001
            print(f"SKIPPED {os.path.basename(path)}: cannot read ({e})")
            continue
        for p in batch:
            try:
                posts.append(build_post(p, out_root))
            except Exception as e:  # noqa: BLE001  one bad post must not stop the rest
                idx = p.get("index", "?")
                r = {"index": idx, "place": p.get("place", "?"), "status": "ERROR",
                     "notes": [f"{type(e).__name__}: {e}"], "entry_hash": entry_hash(p)}
                posts.append(finish(r, os.path.join(out_root, "posts", str(idx)),
                                    os.path.join(out_root, "posts", str(idx), "post.json")))
    json.dump({"generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "posts": posts},
              open(os.path.join(out_root, "manifest.json"), "w"), indent=2)
    for r in posts:
        print(r["index"], r["status"], len(r.get("slides", [])), "slides", "; ".join(r["notes"]))


if __name__ == "__main__":
    main()
