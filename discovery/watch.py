"""The watch list: places we want but cannot yet photograph to standard.

`watch.py add <manifest.json>`  after a build: every post benched only for photo reasons (too small,
                                 too few passed) goes on the list with its place, studio, photographer
                                 and source, unless a person already decided otherwise.
`watch.py check`                 weekly: for each watched place, look again at the pages where full-size
                                 photos would appear (the venue site, the studio's site, the photographer's
                                 site, any source we have for them) and count images that could pass the
                                 gate. Writes watch_report.json. READY items have four or more.

Nobody is emailed. The system waits, and looks again every Monday.
"""
import json
import os
import re
import sys
import time
import html
from urllib.parse import urljoin

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import harvest as hv  # noqa: E402

WATCH = os.path.join(HERE, "watchlist.json")
PHOTO_REASON = re.compile(r"too small|only \d+ photos? passed|could not be fetched", re.I)
MIN_WIDE = 1800


def load():
    return json.load(open(WATCH)) if os.path.exists(WATCH) else {"items": []}


def save(d):
    json.dump(d, open(WATCH, "w"), indent=1, ensure_ascii=False)


def batch_entry(index):
    import glob
    for path in glob.glob(os.path.join(ROOT, "batches", "*.json")):
        for p in json.load(open(path)).get("posts", []):
            if str(p.get("index")) == str(index):
                return p
    return {}


def add(manifest_path):
    m = json.load(open(manifest_path))
    d = load()
    have = {it["index"] for it in d["items"]}
    added = 0
    for p in m.get("posts", []):
        if p.get("status") != "BENCHED" or p["index"] in have:
            continue
        notes = " ; ".join(p.get("notes", []))
        if not PHOTO_REASON.search(notes):
            continue
        b = batch_entry(p["index"])
        meta = p.get("meta", {})
        d["items"].append({
            "index": p["index"], "place": p["place"], "city": meta.get("city", b.get("city", "")),
            "designer": meta.get("designer", b.get("designer", "")), "credit": meta.get("credit", b.get("credit", "")),
            "source_url": meta.get("source_url", b.get("source_url", "")),
            "pages": [], "added": time.strftime("%Y-%m-%d"), "reason": notes[:300], "status": "WATCHING", "last_check": None, "ready_photos": 0,
        })
        added += 1
    save(d)
    print(f"watch add: {added} new, {len(d['items'])} watching")


def pages_for(item, sources):
    """Where full-size photos of this place would turn up."""
    pages = list(item.get("pages", []))
    names = [item.get("designer", ""), item.get("credit", "")]
    for s in sources:
        if s.get("disabled") or not s.get("url"):
            continue
        for n in names:
            if n and hv_slug(n) and hv_slug(n) in hv_slug(s["name"]) or (n and hv_slug(s["name"]) in hv_slug(n)):
                pages.append(s["url"])
    if item.get("source_url"):
        pages.append(item["source_url"])
    return list(dict.fromkeys(pages))


def hv_slug(n):
    return re.sub(r"[^a-z0-9]", "", n.lower())


def place_words(item):
    return [w for w in re.findall(r"[a-z]{4,}", item["place"].lower()) if w not in ("hotel", "bar", "restaurant", "cafe", "the", "club", "house")]


def check():
    d = load()
    sources = json.load(open(os.path.join(HERE, "sources.json")))["sources"]
    report = []
    for it in d["items"]:
        if it["status"] in ("DONE", "DROPPED"):
            continue
        words = place_words(it)
        found = {}
        for page_url in pages_for(it, sources)[:8]:
            try:
                page = hv.get(page_url)
            except Exception:  # noqa: BLE001
                continue
            # follow one level of internal links that mention the place
            hits = [page_url]
            for m in re.finditer(r'href=["\']([^"\'#?]+)', page):
                u = urljoin(page_url, html.unescape(m.group(1)))
                if any(w in u.lower() for w in words) and u not in hits:
                    hits.append(u)
            for u in hits[:4]:
                try:
                    body = page if u == page_url else hv.get(u)
                except Exception:  # noqa: BLE001
                    continue
                low = body.lower()
                if not any(w in low for w in words):
                    continue
                for ph in hv.images_from(body, u):
                    fu = hv.fullsize(ph["url"], "")
                    w = ph["width_hint"] or (2500 if "format=2500w" in fu else 0)
                    if w >= MIN_WIDE:
                        found[fu] = w
                time.sleep(0.3)
        it["last_check"] = time.strftime("%Y-%m-%d")
        it["ready_photos"] = len(found)
        it["candidate_photos"] = sorted(found)[:20]
        if len(found) >= 4:
            it["status"] = "READY"
        report.append({"index": it["index"], "place": it["place"], "status": it["status"], "ready_photos": len(found)})
    save(d)
    json.dump({"checked": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "items": report},
              open(os.path.join(HERE, "watch_report.json"), "w"), indent=1)
    for r in report:
        print("watch:", r["index"], r["place"], r["status"], r["ready_photos"])


if __name__ == "__main__":
    if sys.argv[1] == "add":
        add(sys.argv[2])
    elif sys.argv[1] == "check":
        check()
