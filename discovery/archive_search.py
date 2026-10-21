"""Archive photographs for the nostalgic half of the feed, from professional collections only.

Wikimedia Commons and open Flickr were dropped on 2026-10-21: they are mostly visitors' phone and wide-angle
snapshots, and every post built from them was killed. Licence filters cannot tell a snapshot from a real
photograph; the collection can. So this searches only Library of Congress collections made by working
architectural and documentary photographers (view cameras, true verticals, controlled light):

  - Gottscho-Schleisner Collection (gsc): New York interiors, restaurants, bars, hotels, 1930s to 1970s
  - Carol M. Highsmith Archive (highsm): large-format and high-resolution digital, donated to the public domain
  - Historic American Buildings Survey (hhh): measured large-format survey photographs

Reads discovery/archive_targets.json, keeps items with no known publication restrictions and a full-size file
that clears the gate, records photographer, date and the LoC item page for the credit line.
Writes discovery/archive_candidates.json (same shape as before). Nothing is picked here; the weekly review reads it.
"""
import json
import os
import re
import time
import urllib.parse
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
UA = "BureauOfLeisureBot/1.1 (https://bureauofleisure.co; lewie@flow.agency)"
MIN_WIDE, MIN_TALL = 1800, 1350
PER_SEARCH = 25          # results read per collection search
ITEMS_PER_TARGET = 12    # item pages opened per target (each is one request)
COLLECTIONS = [
    ("gottscho-schleisner collection", "Gottscho-Schleisner Collection, Library of Congress"),
    ("carol m. highsmith archive", "Carol M. Highsmith Archive, Library of Congress"),
    ("historic american buildings survey/historic american engineering record/historic american landscapes survey",
     "Historic American Buildings Survey, Library of Congress"),
]
RESTRICTED = re.compile(r"(rights status not evaluated|restricted|permission|copyright (?:held|owned)|may be protected)", re.I)
FREE = re.compile(r"(no known restrictions|public domain|no known copyright)", re.I)
SKIP_TITLE = re.compile(r"\b(plan|elevation|section|drawing|map|sketch|logo|menu|poster|detail of hardware|measured)\b", re.I)
STOP = {"hotel", "interior", "lobby", "beach", "pool", "miami", "york", "angeles", "the", "and", "bar", "restaurant", "new"}
STATS = {"searches": 0, "results": 0, "items_opened": 0, "offtopic": 0, "rights": 0, "size": 0, "type": 0,
         "errors": [], "rights_seen": {}, "sample_files": None}
_last = [0.0]


def get(url):
    # loc.gov asks for gentle clients: about 2 requests a second keeps well under its limits
    wait = 0.6 - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    _last[0] = time.time()
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 2:
                time.sleep(10 * (attempt + 1)); continue
            raise


def words_for(t):
    src = " ".join([t["place"]] + (t.get("queries") or []))
    return sorted({w for w in re.findall(r"[a-z]{4,}", src.lower()) if w not in STOP})


def search(query, partof):
    params = {"q": query, "fa": "partof:" + partof, "fo": "json", "c": PER_SEARCH, "at": "results"}
    data = get("https://www.loc.gov/photos/?" + urllib.parse.urlencode(params))
    STATS["searches"] += 1
    res = data.get("results") or []
    STATS["results"] += len(res)
    return res


def flat_files(item_json):
    out = []
    for res in item_json.get("resources") or []:
        for group in res.get("files") or []:
            for f in (group if isinstance(group, list) else [group]):
                if isinstance(f, dict) and f.get("url"):
                    out.append(f)
    return out


def iiif_full(tif_url):
    """tile.loc.gov/storage-services/service/pnp/gsc/5a00000/5a00100/5a00123u.tif
       -> tile.loc.gov/image-services/iiif/service:pnp:gsc:5a00000:5a00100:5a00123u/full/pct:100/0/default.jpg"""
    m = re.search(r"/storage-services/(service/[^?#]+?)\.tiff?$", tif_url, re.I)
    if not m:
        return ""
    return "https://tile.loc.gov/image-services/iiif/" + m.group(1).replace("/", ":") + "/full/pct:100/0/default.jpg"


def best_image(files):
    """The largest JPEG at gate size; failing that, the master TIFF served as a full-size JPEG through IIIF."""
    def dims(f):
        return int(f.get("width") or 0), int(f.get("height") or 0)
    jpgs = [f for f in files if "jpeg" in (f.get("mimetype") or "") or re.search(r"\.jpe?g($|\?)", f["url"], re.I)]
    jpgs = [f for f in jpgs if "/iiif/" not in f["url"] or "/full/" in f["url"]]
    jpgs.sort(key=lambda f: dims(f)[0] * dims(f)[1], reverse=True)
    for f in jpgs:
        w, h = dims(f)
        if w >= MIN_WIDE and h >= MIN_TALL:
            return f["url"].split("#")[0], w, h
    tifs = [f for f in files if "tiff" in (f.get("mimetype") or "") or re.search(r"\.tiff?$", f["url"], re.I)]
    tifs.sort(key=lambda f: dims(f)[0] * dims(f)[1], reverse=True)
    for f in tifs:
        w, h = dims(f)
        url = iiif_full(f["url"])
        if url and w >= MIN_WIDE and h >= MIN_TALL:
            return url, w, h
    return None


def as_text(v):
    if isinstance(v, list):
        return " ".join(as_text(x) for x in v)
    return str(v or "")


def candidates_for(t):
    words = words_for(t)
    hits, seen = [], set()
    for q in t.get("queries") or [t["place"]]:
        for partof, label in COLLECTIONS:
            try:
                for r in search(q, partof):
                    rid = r.get("id") or r.get("url")
                    if not rid or rid in seen:
                        continue
                    seen.add(rid)
                    hay = (as_text(r.get("title")) + " " + as_text(r.get("description")) + " " + as_text(r.get("subject"))).lower()
                    if words and not any(w in hay for w in words):
                        STATS["offtopic"] += 1; continue
                    if SKIP_TITLE.search(as_text(r.get("title"))):
                        STATS["type"] += 1; continue
                    hits.append((r, label))
            except Exception as e:  # noqa: BLE001
                STATS["errors"].append(f"search {q} / {partof[:20]}: {type(e).__name__} {str(e)[:100]}")
    out = []
    for r, label in hits[:ITEMS_PER_TARGET]:
        rid = r.get("id") or r.get("url")
        try:
            item = get(rid.rstrip("/") + "/?fo=json")
            STATS["items_opened"] += 1
        except Exception as e:  # noqa: BLE001
            STATS["errors"].append(f"item {rid}: {type(e).__name__}"); continue
        meta = item.get("item") or {}
        rights = as_text(meta.get("rights_advisory") or item.get("rights_advisory") or meta.get("rights"))
        if RESTRICTED.search(rights) or not FREE.search(rights):
            STATS["rights"] += 1
            key = rights[:80] or "(none)"
            STATS["rights_seen"][key] = STATS["rights_seen"].get(key, 0) + 1
            continue
        files = flat_files(item)
        if STATS["sample_files"] is None and files:
            STATS["sample_files"] = {"item": rid, "files": [{k: f.get(k) for k in ("url", "mimetype", "width", "height")} for f in files[:8]]}
        pick = best_image(files)
        if not pick:
            STATS["size"] += 1; continue
        url, w, h = pick
        author = as_text(meta.get("contributor_names") or meta.get("creator") or r.get("contributor")).strip()
        out.append({"title": as_text(meta.get("title") or r.get("title"))[:200], "url": url, "width": w, "height": h,
                    "licence": "No known restrictions (Library of Congress)", "author": author or label.split(",")[0],
                    "collection": label, "date": as_text(meta.get("date") or r.get("date"))[:40],
                    "source_page": rid, "provider": "Library of Congress"})
    return out


def main():
    targets = json.load(open(os.path.join(HERE, "archive_targets.json")))["targets"]
    results = []
    for t in targets:
        found = candidates_for(t)
        uniq, seen = [], set()
        for f in found:
            if f["url"] not in seen:
                seen.add(f["url"]); uniq.append(f)
        results.append({**{k: t[k] for k in ("place", "city", "era", "notes") if k in t}, "candidates": uniq[:25], "count": len(uniq)})
        print(f"{t['place']}: {len(uniq)} candidates")
    STATS["rights_seen"] = dict(sorted(STATS["rights_seen"].items(), key=lambda kv: -kv[1])[:20])
    STATS["errors"] = STATS["errors"][:40]
    json.dump({"searched": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "provider": "Library of Congress",
               "collections": [c[1] for c in COLLECTIONS], "stats": STATS, "targets": results},
              open(os.path.join(HERE, "archive_candidates.json"), "w"), indent=1, ensure_ascii=False)
    print(json.dumps({k: v for k, v in STATS.items() if k != "sample_files"}, indent=1)[:1500])


if __name__ == "__main__":
    main()
