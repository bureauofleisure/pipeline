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
PER_SEARCH = 60          # results read per collection search
ITEMS_PER_TARGET = 16    # item pages opened per target (each is one request)
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
DEADLINE = time.time() + 32 * 60   # stop searching in time to save what was found


BLOCKED = [False]   # set when loc.gov answers 429; main stops after keeping what this target found


class RateLimited(Exception):
    """loc.gov answered 429. Its limits (search: 20 a minute; items: 200 a minute) block the client for about an
    hour once exceeded, so retrying only extends the block. Stop, save what was found, try again next run."""


def get(url, search=False):
    # 2026-11-02: searches were paced at 0.6 s (about 100 a minute) and tripped the hour-long block every run.
    gap = 3.5 if search else 0.6
    wait = gap - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    _last[0] = time.time()
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                STATS["rate_limited"] = True
                raise RateLimited(url) from e
            if e.code in (500, 502, 503, 504) and attempt < 2:
                time.sleep(10 * (attempt + 1)); continue
            raise
        except (TimeoutError, urllib.error.URLError, ConnectionError):
            if attempt < 2:
                time.sleep(5 * (attempt + 1)); continue
            raise


def words_for(t):
    src = " ".join([t["place"]] + (t.get("queries") or []))
    return sorted({w for w in re.findall(r"[a-z]{4,}", src.lower()) if w not in STOP})


def search(query):
    """One search across all LoC photos; the collection is checked on each result (three faceted searches per
    query was too slow for loc.gov and timed the job out)."""
    params = {"q": query, "fo": "json", "c": PER_SEARCH, "at": "results"}
    data = get("https://www.loc.gov/photos/?" + urllib.parse.urlencode(params), search=True)
    STATS["searches"] += 1
    res = data.get("results") or []
    STATS["results"] += len(res)
    return res


def collection_of(r):
    parts = " ".join(as_text(x) for x in (r.get("partof") or [])).lower()
    for key, label in COLLECTIONS:
        if key.split("/")[0] in parts:
            return label
    return None


def flat_files(item_json):
    out = []
    for res in item_json.get("resources") or []:
        for group in res.get("files") or []:
            for f in (group if isinstance(group, list) else [group]):
                if isinstance(f, dict) and f.get("url"):
                    out.append(f)
    return out


def iiif_ids(tif_url):
    """A master TIFF such as .../storage-services/master/pnp/gsc/5a23000/5a23400/5a23488u.tif is served full size
    as JPEG by the IIIF image server. Its id is the path with colons; with and without the trailing 'u' are tried."""
    m = re.search(r"/storage-services/(?:master|service)/(.+?)\.tiff?$", tif_url, re.I)
    if not m:
        return []
    base = "service:" + m.group(1).replace("/", ":")
    ids = [base]
    if base.endswith("u"):
        ids.append(base[:-1])
    return ["https://tile.loc.gov/image-services/iiif/" + x for x in ids]


def iiif_resolve(tif_url):
    for root in iiif_ids(tif_url):
        try:
            info = get(root + "/info.json")
        except Exception as e:  # noqa: BLE001
            STATS.setdefault("iiif_misses", []).append(f"{root[-40:]}: {type(e).__name__}")
            continue
        w, h = int(info.get("width") or 0), int(info.get("height") or 0)
        v3 = "3" in str(info.get("@context", ""))
        size = "max" if v3 else "full"
        STATS.setdefault("iiif_ok", 0); STATS["iiif_ok"] += 1
        if STATS.get("iiif_sample") is None:
            STATS["iiif_sample"] = {"id": root, "width": w, "height": h, "context": info.get("@context"),
                                    "maxWidth": info.get("maxWidth"), "maxArea": info.get("maxArea")}
        mw = int(info.get("maxWidth") or 0)
        if mw and mw < w:   # server caps the size: report what we will actually get
            h, w = int(h * mw / w), mw
        return f"{root}/full/{size}/0/default.jpg", w, h
    return None


def best_image(files):
    """The largest JPEG at gate size; failing that, the master TIFF served full size as JPEG through IIIF."""
    def dims(f):
        return int(f.get("width") or 0), int(f.get("height") or 0)
    jpgs = [f for f in files if "jpeg" in (f.get("mimetype") or "") or re.search(r"\.jpe?g($|\?)", f["url"], re.I)]
    jpgs.sort(key=lambda f: dims(f)[0] * dims(f)[1], reverse=True)
    for f in jpgs:
        w, h = dims(f)
        if w >= MIN_WIDE and h >= MIN_TALL:
            return f["url"].split("#")[0], w, h
    tifs = [f for f in files if "tiff" in (f.get("mimetype") or "") or re.search(r"\.tiff?$", f["url"], re.I)]
    for f in tifs[:2]:
        got = iiif_resolve(f["url"]) or tif_header_size(f["url"])
        if got and got[1] >= MIN_WIDE and got[2] >= MIN_TALL:
            return got
        if got:
            STATS["small_masters"] = STATS.get("small_masters", 0) + 1
    return None


def tiff_dims(b):
    """Width and height from the first IFD of a TIFF (tags 256 and 257), no imaging library needed."""
    import struct
    end = {b"II": "<", b"MM": ">"}[b[:2]]
    off = struct.unpack(end + "I", b[4:8])[0]
    n = struct.unpack(end + "H", b[off:off + 2])[0]
    dims = {}
    for i in range(n):
        e = b[off + 2 + 12 * i: off + 14 + 12 * i]
        tag, typ = struct.unpack(end + "HH", e[:4])
        if tag in (256, 257):
            dims[tag] = struct.unpack(end + "H", e[8:10])[0] if typ == 3 else struct.unpack(end + "I", e[8:12])[0]
    return dims[256], dims[257]


def tif_header_size(tif_url):
    """IIIF answers 404 for these ids (checked 2026-10-26), but the master TIFF itself downloads and the build
    opens it (Gottscho TWA Lisbon Lounge, 5411x4299). Read only the first 256 KB to learn its size."""
    try:
        req = urllib.request.Request(tif_url, headers={"User-Agent": UA, "Range": "bytes=0-262143"})
        with urllib.request.urlopen(req, timeout=40) as r:
            head = r.read(262144)
        w, h = tiff_dims(head)
        STATS["tif_header_ok"] = STATS.get("tif_header_ok", 0) + 1
        return tif_url, int(w), int(h)
    except urllib.error.HTTPError as e:
        STATS.setdefault("tif_header_misses", []).append(f"{tif_url[-40:]}: HTTP {e.code}")
        return None
    except Exception as e:  # noqa: BLE001
        STATS.setdefault("tif_header_misses", []).append(f"{tif_url[-40:]}: {type(e).__name__}")
        return None


def as_text(v):
    if isinstance(v, list):
        return " ".join(as_text(x) for x in v)
    return str(v or "")


def candidates_for(t):
    words = words_for(t)
    hits, seen = [], set()
    # one query per target: a runner gets about one loc.gov search before a 429, so spend it on the most
    # specific wording (the target's own query when it has one)
    queries = (t.get("queries") or [])[:1] or [t["place"]]
    for q in queries:
        if time.time() > DEADLINE:
            STATS["out_of_time"] = True; break
        try:
            for r in search(q):
                rid = r.get("id") or r.get("url")
                if not rid or rid in seen:
                    continue
                seen.add(rid)
                label = collection_of(r)
                if not label:
                    STATS["other_collections"] = STATS.get("other_collections", 0) + 1; continue
                hay = (as_text(r.get("title")) + " " + as_text(r.get("description")) + " " + as_text(r.get("subject"))).lower()
                if words and not any(w in hay for w in words):
                    STATS["offtopic"] += 1; continue
                if SKIP_TITLE.search(as_text(r.get("title"))):
                    STATS["type"] += 1; continue
                hits.append((r, label))
        except RateLimited:
            BLOCKED[0] = True
            break
        except Exception as e:  # noqa: BLE001
            STATS["errors"].append(f"search {q}: {type(e).__name__} {str(e)[:100]}")
    out = []
    for r, label in hits[:ITEMS_PER_TARGET]:
        if time.time() > DEADLINE:
            STATS["out_of_time"] = True; break
        rid = r.get("id") or r.get("url")
        # 2026-11-02: www.loc.gov item pages answer 429 to GitHub's shared runners even hours after the last
        # request, while search and tile.loc.gov work. The search result already carries what is needed:
        # the collection (partof), the rights line when given, and image_url, whose service JPEG path maps
        # onto the master TIFF. So no item page is opened; the TIFF header on tile.loc.gov gives the size.
        rights = as_text(r.get("rights_advisory") or r.get("rights"))
        if rights and (RESTRICTED.search(rights) and not re.search(r"no known restrictions", rights, re.I)):
            STATS["rights"] += 1
            STATS["rights_seen"][rights[:80]] = STATS["rights_seen"].get(rights[:80], 0) + 1
            continue
        # all three collections are released for publication by the Library (Gottscho-Schleisner and HABS:
        # no known restrictions; Highsmith: public domain); a result-level restriction above still excludes
        tif = master_from_result(r)
        if not tif:
            STATS["type"] += 1; continue
        got = tif_header_size(tif)
        if not got or got[1] < MIN_WIDE or got[2] < MIN_TALL:
            STATS["size"] += 1; continue
        url, w, h = got
        STATS["items_opened"] += 1
        author = as_text(r.get("contributor") or r.get("contributor_names")).strip()
        out.append({"title": as_text(r.get("title"))[:200], "url": url, "width": w, "height": h,
                    "licence": "No known restrictions (Library of Congress)", "author": author or label.split(",")[0],
                    "collection": label, "date": as_text(r.get("date"))[:40],
                    "source_page": rid, "provider": "Library of Congress"})
    return out


def master_from_result(r):
    """.../storage-services/service/pnp/gsc/5a28000/5a28500/5a28550v.jpg -> .../master/pnp/gsc/.../5a28550u.tif"""
    urls = r.get("image_url") or []
    if isinstance(urls, str):
        urls = [urls]
    for u in urls:
        m = re.search(r"(https://tile\.loc\.gov/storage-services/)service/(pnp/[a-z]+/[\w/]+?/)(\w+?)[a-z]?(?:_\d+px)?\.(?:jpg|gif|tif)", u)
        if m:
            return f"{m.group(1)}master/{m.group(2)}{m.group(3)}u.tif"
    return None


def main():
    targets = json.load(open(os.path.join(HERE, "archive_targets.json")))["targets"]
    results = []
    previous = {}
    try:
        for t in json.load(open(os.path.join(HERE, "archive_candidates.json")))["targets"]:
            if t.get("count"):
                previous[t["place"]] = t
    except Exception:  # noqa: BLE001
        pass
    # Resume where the last run stopped: loc.gov lets a GitHub runner make only a few searches before a 429,
    # so the daily job works through the list a few targets at a time instead of all of them on Monday.
    try:
        cursor = int(json.load(open(os.path.join(HERE, "archive_candidates.json"))).get("next_target", 0)) % max(len(targets), 1)
    except Exception:  # noqa: BLE001
        cursor = 0
    order = list(range(cursor, len(targets))) + list(range(0, cursor))
    budget = int(os.environ.get("ARCHIVE_TARGETS_PER_RUN", "6"))
    next_target = cursor
    tried = 0
    blocked = False
    for pos in order:
        t = targets[pos]
        found = []
        if not blocked and tried < budget:
            tried += 1
            next_target = (pos + 1) % len(targets)
            try:
                found = candidates_for(t)
            except RateLimited:
                BLOCKED[0] = True
            if BLOCKED[0]:
                blocked = True
                next_target = pos if not found else (pos + 1) % len(targets)   # retry an empty one first next time
                STATS["errors"].append(f"rate limited by loc.gov at {t['place']}; stopped searching, kept earlier finds")
        if not found and t["place"] in previous:
            results.append((pos, previous[t["place"]])); continue  # keep last good result rather than wiping it
        uniq, seen = [], set()
        for f in found:
            if f["url"] not in seen:
                seen.add(f["url"]); uniq.append(f)
        results.append((pos, {**{k: t[k] for k in ("place", "city", "era", "notes") if k in t}, "candidates": uniq[:25], "count": len(uniq)}))
        print(f"{t['place']}: {len(uniq)} candidates")
    results = [r for _, r in sorted(results, key=lambda x: x[0])]
    STATS["targets_tried"] = tried
    STATS["rights_seen"] = dict(sorted(STATS["rights_seen"].items(), key=lambda kv: -kv[1])[:20])
    STATS["errors"] = STATS["errors"][:40]
    json.dump({"searched": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "provider": "Library of Congress",
               "collections": [c[1] for c in COLLECTIONS], "stats": STATS, "next_target": next_target, "targets": results},
              open(os.path.join(HERE, "archive_candidates.json"), "w"), indent=1, ensure_ascii=False)
    print(json.dumps({k: v for k, v in STATS.items() if k != "sample_files"}, indent=1)[:1500])


if __name__ == "__main__":
    main()
