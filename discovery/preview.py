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

from PIL import Image, ImageDraw, ImageOps

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
UA = "Mozilla/5.0 (compatible; BureauOfLeisureBot/1.0; +https://bureauofleisure.co)"   # honest bot UA, same as harvest and build (2026-11-02)
THUMB = 640          # long side of each thumbnail
COLS, ROWS = 3, 2    # photos per sheet
PAD = 24


def gate():
    """The build's own photo gate (size after the 4:5 crop, sharpness, JPEG blockiness), so a contact sheet says
    PASS or FAIL before anything is picked. 2026-11-02: Love Thy Neighbor was picked from sharp-looking Dezeen
    frames that all failed the blockiness check at build time."""
    try:
        sys.path.insert(0, os.path.join(ROOT, "build"))
        sys.path.insert(0, os.path.join(ROOT, "grade"))
        from build_posts import quality  # noqa: E402
        return quality
    except Exception as ex:  # noqa: BLE001
        print("gate unavailable:", ex)
        return None


def fetch(url):
    """Large Library of Congress TIFFs (20 to 90 MB) need a long timeout and a retry; everything else is quick."""
    import time
    loc = "loc.gov" in url
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "image/*,*/*;q=0.8"})
    last = None
    for attempt in range(4 if loc else 2):
        try:
            with urllib.request.urlopen(req, timeout=180 if loc else 60) as r:
                return r.read()
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
                im = ImageOps.exif_transpose(im)
                e.update({"ok": True, "width": im.width, "height": im.height, "bytes": len(data), "format": im.format})
                verdict = ""
                if check:
                    try:
                        passed, why = check(im.convert("RGB"), (0.5, 0.5))
                        e["gate"] = "PASS" if passed else "FAIL: " + why
                        verdict = "  PASS" if passed else "  FAIL " + why.split(" (")[0]
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
