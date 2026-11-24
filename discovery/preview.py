"""Contact sheets for the weekly review, so every photo is seen before it is picked.

The review runs in a sandbox that cannot reach photo hosts. GitHub Actions can. This script reads
discovery/preview_request.json ({"sets": [{"id": "<slug>", "urls": [...]}]}), downloads each photo,
records its real pixel size, and writes numbered contact sheets to previews/<slug>-<n>.jpg plus
previews/report.json (per-url status, width, height). The review reads the sheets back through git.
Nothing here touches picks, batches or the build.
"""
import io
import json
import os
import sys
import urllib.request

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
UA = "Mozilla/5.0 (compatible; BureauOfLeisureBot/1.0; +https://bureauofleisure.co)"   # honest bot UA, same as harvest and build (2026-11-02)
THUMB = 640          # long side of each thumbnail
COLS, ROWS = 3, 2    # photos per sheet
PAD = 24


def gate():
    """The build's own per-photo path, so a contact sheet says PASS or FAIL before anything is picked.
    2026-11-02: Love Thy Neighbor was picked from sharp-looking Dezeen frames that all failed the blockiness
    check at build time. 2026-11-24: the sheet now runs every step the build runs on one photo (film-edge trim
    for Library of Congress scans, straighten, keystone, size/sharpness/blockiness, grade, then the grade's
    tone check), on the image exactly as the build opens it (no EXIF turn). W48 had three sets pass preview
    and bench on tone (0044 and 0046 too saturated, 0041 brightness) and one (0048) pass preview and fail
    blockiness at build. A grade HOLD prints as FAIL, so "never list a FAIL photo" covers it."""
    try:
        sys.path.insert(0, os.path.join(ROOT, "build"))
        sys.path.insert(0, os.path.join(ROOT, "grade"))
        import build_posts as b  # noqa: E402
        from atlas_grade import measure_lean, _rotate_fill, grade, check, HoldForReview  # noqa: E402
    except Exception as ex:  # noqa: BLE001
        print("gate unavailable:", ex)
        return None

    def verdict(raw, url, seed=0):
        """(passed, reason) for one photo, the way build_posts.build_post judges it (no edits, centre focal)."""
        focal = (0.5, 0.5)
        if "loc.gov" in url and "highsm" not in url:
            raw, _ = b.trim_scan_border(raw)
        rot0, _ = measure_lean(raw)
        if 0.3 <= abs(rot0) <= 8.0:
            raw = _rotate_fill(raw, rot0)
        ks = b.measure_keystone(raw)
        if b.KEYSTONE_MIN <= abs(ks) <= b.KEYSTONE_MAX:
            raw = b.fix_keystone(raw, ks)
        ok, why = b.quality(raw, focal)
        if not ok:
            return False, why
        try:
            img = grade(raw, focal=focal, seed=seed)
        except HoldForReview as e:
            return False, f"held by the grade: {e}"
        rep = check(img)
        if rep["status"] != "PASS":
            return False, rep["status"].replace("HOLD: ", "grade HOLD: ")
        return True, ""
    return verdict


def fetch(url):
    """Large Library of Congress TIFFs (20 to 90 MB) need a long timeout and a retry; everything else is quick."""
    import time
    loc = "loc.gov" in url
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "image/*"})   # same request as the build
    last = None
    for attempt in range(4 if loc else 2):
        try:
            # a limit on the whole file, not just each read: a trickling download once held a job for 30 minutes
            t0, limit, chunks = time.time(), (240 if loc else 60), []
            with urllib.request.urlopen(req, timeout=60 if loc else 30) as r:
                while True:
                    if time.time() - t0 > limit:
                        raise TimeoutError(f"download passed {limit}s")
                    chunk = r.read1(1 << 18)
                    if not chunk:
                        break
                    chunks.append(chunk)
            return b"".join(chunks)
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(15 * (attempt + 1) if loc else 3)
    raise last

def main():
    req_path = os.path.join(HERE, "preview_request.json")
    out_dir = os.path.join(ROOT, "previews")
    os.makedirs(out_dir, exist_ok=True)
    for f in os.listdir(out_dir):
        os.remove(os.path.join(out_dir, f))
    request = json.load(open(req_path))
    report = {"sets": []}
    check = gate()
    for s in request.get("sets", []):
        sid = s["id"]
        entries, thumbs = [], []
        for i, url in enumerate(s.get("urls", []), 1):
            e = {"n": i, "url": url}
            try:
                data = fetch(url)
                im = Image.open(io.BytesIO(data))
                im.load()
                fmt = im.format
                # the build opens photos without applying EXIF orientation; judge (and show) them the same way
                im = im.convert("RGB")
                e.update({"ok": True, "width": im.width, "height": im.height, "bytes": len(data), "format": fmt})
                verdict = ""
                if check:
                    try:
                        passed, why = check(im, url, seed=i - 1)
                        e["gate"] = "PASS" if passed else "FAIL: " + why
                        verdict = "  PASS" if passed else "  FAIL " + why.split(" (")[0][:40]
                    except Exception as ex:  # noqa: BLE001
                        e["gate"] = f"gate error: {type(ex).__name__}"
                t = im.convert("RGB")
                t.thumbnail((THUMB, THUMB))
                thumbs.append((i, t, f"{im.width}x{im.height}{verdict}"))
            except Exception as ex:  # noqa: BLE001
                e.update({"ok": False, "error": f"{type(ex).__name__}: {str(ex)[:120]}"})
                t = Image.new("RGB", (THUMB // 2, THUMB // 2), (60, 60, 60))
                thumbs.append((i, t, "FAILED"))
            entries.append(e)
            print(sid, i, e.get("width"), e.get("height"), e.get("error", ""))
        sheets = []
        per = COLS * ROWS
        for k in range(0, len(thumbs), per):
            chunk = thumbs[k:k + per]
            rows = (len(chunk) + COLS - 1) // COLS
            W = COLS * (THUMB + PAD) + PAD
            H = rows * (THUMB + PAD + 30) + PAD
            sheet = Image.new("RGB", (W, H), (245, 243, 238))
            d = ImageDraw.Draw(sheet)
            for j, (n, t, label) in enumerate(chunk):
                cx = PAD + (j % COLS) * (THUMB + PAD)
                cy = PAD + (j // COLS) * (THUMB + PAD + 30)
                ox = cx + (THUMB - t.width) // 2
                oy = cy + (THUMB - t.height) // 2
                sheet.paste(t, (ox, oy))
                d.rectangle([cx, cy + THUMB + 2, cx + THUMB, cy + THUMB + 28], fill=(30, 30, 30))
                d.text((cx + 8, cy + THUMB + 8), f"{sid}  #{n}  {label}", fill=(255, 255, 255))
            name = f"{sid}-{k // per + 1}.jpg"
            sheet.save(os.path.join(out_dir, name), "JPEG", quality=82)
            sheets.append(name)
        report["sets"].append({"id": sid, "photos": entries, "sheets": sheets})
    json.dump(report, open(os.path.join(out_dir, "report.json"), "w"), indent=1)
    print(f"{len(report['sets'])} sets previewed")


if __name__ == "__main__":
    sys.exit(main())
