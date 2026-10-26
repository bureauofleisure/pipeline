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
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
THUMB = 640          # long side of each thumbnail
COLS, ROWS = 3, 2    # photos per sheet
PAD = 24


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "image/*,*/*;q=0.8", "Referer": url.split("/", 3)[0] + "//" + url.split("/", 3)[2] + "/"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def main():
    req_path = os.path.join(HERE, "preview_request.json")
    out_dir = os.path.join(ROOT, "previews")
    os.makedirs(out_dir, exist_ok=True)
    for f in os.listdir(out_dir):
        os.remove(os.path.join(out_dir, f))
    request = json.load(open(req_path))
    report = {"sets": []}
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
                t = im.convert("RGB")
                t.thumbnail((THUMB, THUMB))
                thumbs.append((i, t, f"{im.width}x{im.height}"))
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
