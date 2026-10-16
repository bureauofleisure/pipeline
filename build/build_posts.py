"""Build every post in batches/*.json into finished, hosted slides.

For each post:
  1. download the source photos (or read local paths, for tests)
  2. grade each with Grade v1.0 and check it; drop any that HOLD
  3. bench the post if fewer than MIN_PHOTOS pass
  4. rank the survivors (sharpness, exposure, a calm bottom-left for the cover label)
  5. build the slides and export them to docs/posts/<index>/NN.jpg
  6. write docs/posts/<index>/post.json, and docs/manifest.json listing every post

docs/ is served by GitHub Pages, so every slide gets a public URL that Instagram can fetch.
A post whose slides already exist is skipped, so re-runs are cheap and safe.
"""
import glob
import io
import json
import os
import sys
import urllib.request

import cv2
import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "grade"))
sys.path.insert(0, HERE)
from atlas_grade import grade, check, export, GRADE_VERSION, HoldForReview  # noqa: E402
import carousel  # noqa: E402

MIN_PHOTOS = 4
MAX_PHOTOS = 6
BASE_URL = os.environ.get("PAGES_BASE_URL", "https://bureauofleisure.github.io/pipeline")
UA = "Mozilla/5.0 (compatible; BureauOfLeisureBot/1.0)"


def fetch(src):
    if src.startswith("http"):
        req = urllib.request.Request(src, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=30) as r:
            return Image.open(io.BytesIO(r.read())).convert("RGB")
    return Image.open(os.path.join(ROOT, src)).convert("RGB")


def score(img):
    """Higher is better. Sharp, well exposed, and a calm bottom-left for the label."""
    g = np.asarray(img.convert("L"), dtype=np.float32)
    sharp = min(cv2.Laplacian(g, cv2.CV_32F).var() / 400.0, 1.0)
    expo = 1.0 - min(abs(float(np.median(g)) / 255 * 100 - 45) / 45, 1.0)
    h, w = g.shape
    corner = g[int(h * 0.78):, : int(w * 0.6)]
    calm = 1.0 - min(float(corner.std()) / 64.0, 1.0)
    return round(0.45 * sharp + 0.25 * expo + 0.30 * calm, 4)


def build_post(p, out_root):
    idx = p["index"]
    out_dir = os.path.join(out_root, "posts", idx)
    done = os.path.join(out_dir, "post.json")
    if os.path.exists(done):
        return json.load(open(done))
    result = {"index": idx, "place": p["place"], "status": "BUILT", "notes": [], "grade": GRADE_VERSION}
    graded = []
    for i, src in enumerate(p["photos"]):
        try:
            img = grade(fetch(src), focal=tuple(p.get("focal", (0.5, 0.5))), seed=i)
        except HoldForReview as e:
            result["notes"].append(f"photo {i + 1} held: {e}")
            continue
        except Exception as e:  # unreachable, blocked, not an image
            result["notes"].append(f"photo {i + 1} failed: {type(e).__name__}")
            continue
        rep = check(img)
        if rep["status"] != "PASS":
            result["notes"].append(f"photo {i + 1} {rep['status']}")
            continue
        graded.append((score(img), img))
    if len(graded) < MIN_PHOTOS:
        result["status"] = "BENCHED"
        result["notes"].append(f"only {len(graded)} photos passed, need {MIN_PHOTOS}")
        os.makedirs(out_dir, exist_ok=True)
        json.dump(result, open(done, "w"), indent=2)
        return result
    graded.sort(key=lambda t: t[0], reverse=True)
    photos = [img for _, img in graded[:MAX_PHOTOS]]
    place_line = f"{p['place']}. {p['neighborhood']}."
    slides = carousel.build(photos, idx, place_line, p["word"], p.get("reminds", ""), p["card"], p.get("credit", ""))
    os.makedirs(out_dir, exist_ok=True)
    urls = []
    for n, s in enumerate(slides, 1):
        name = f"{n:02d}.jpg"
        export(s, os.path.join(out_dir, name))
        urls.append(f"{BASE_URL}/posts/{idx}/{name}")
    result["slides"] = urls
    result["caption_card"] = "\n".join(p["card"])
    json.dump(result, open(done, "w"), indent=2)
    return result


def main():
    out_root = os.path.join(ROOT, "docs")
    posts = []
    for f in sorted(glob.glob(os.path.join(ROOT, "batches", "*.json"))):
        for p in json.load(open(f))["posts"]:
            posts.append(build_post(p, out_root))
    manifest = {"posts": posts}
    json.dump(manifest, open(os.path.join(out_root, "manifest.json"), "w"), indent=2)
    for r in posts:
        print(r["index"], r["status"], len(r.get("slides", [])), "slides", "; ".join(r["notes"]))


if __name__ == "__main__":
    main()
