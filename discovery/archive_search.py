"""Archive photographs from the commons, for the nostalgic half of the feed.

Reads discovery/archive_targets.json (places worth an archive post), asks Wikimedia Commons for images of
each, keeps only files whose licence allows reuse (public domain, CC0, CC BY, CC BY-SA) and whose width
clears the gate, and records the licence, author and source page so the credit line can be exact.
Flickr Commons is searched too when a FLICKR_KEY secret is present.

Writes discovery/archive_candidates.json. Nothing is picked here; the weekly review reads it.
"""
import json
import os
import re
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
UA = "BureauOfLeisureBot/1.0 (https://bureauofleisure.co; hello@bureauofleisure.co)"
OK_LICENCE = re.compile(r"(public domain|\bpd\b|pd-|cc0|cc[ -]?by(?:[ -]?sa)?\b|no known copyright|no restrictions)", re.I)
BAD_LICENCE = re.compile(r"(\bnc\b|\bnd\b|non-?commercial|no ?derivative|fair use|all rights reserved|copyrighted)", re.I)
MIN_WIDE = 1800
STATS = {"hits": 0, "licence": 0, "size": 0, "type": 0, "errors": [], "licences_seen": {}}


def api(params):
    url = "https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.load(r)


IIPROPS = {"prop": "imageinfo", "iiprop": "url|size|extmetadata|user", "iiextmetadatafilter": "LicenseShortName|License|Artist|DateTimeOriginal|ImageDescription"}


def _pages(params):
    data = api({"action": "query", "format": "json", **IIPROPS, **params})
    return list(data.get("query", {}).get("pages", {}).values())


def commons(query, limit=40, words=None):
    """Three routes, merged: the Commons category named after the place (where curated sets live), its
    subcategories, and a full-text search restricted to bitmaps. Hits must mention the place words."""
    pages = []
    cat = "Category:" + query
    try:
        pages += _pages({"generator": "categorymembers", "gcmtitle": cat, "gcmtype": "file", "gcmlimit": limit})
        subs = api({"action": "query", "format": "json", "list": "categorymembers", "cmtitle": cat, "cmtype": "subcat", "cmlimit": 10})
        for sc in subs.get("query", {}).get("categorymembers", [])[:6]:
            pages += _pages({"generator": "categorymembers", "gcmtitle": sc["title"], "gcmtype": "file", "gcmlimit": limit})
    except Exception as e:  # noqa: BLE001
        STATS["errors"].append(f"category {cat}: {type(e).__name__}")
    pages += _pages({"generator": "search", "gsrsearch": f"{query} filetype:bitmap", "gsrnamespace": 6, "gsrlimit": limit})
    words = words or [w for w in re.findall(r"[a-z]{4,}", query.lower()) if w not in ("hotel", "interior", "lobby", "beach", "pool", "miami", "york", "angeles")]
    out = []
    STATS["hits"] += len(pages)
    seen_titles = set()
    if len(STATS.setdefault("samples", [])) < 3 and pages:
        pg = dict(pages[0]); ii0 = (pg.get("imageinfo") or [{}])[0]
        STATS["samples"].append({"title": pg.get("title"), "keys": sorted(pg.keys()), "ii_keys": sorted(ii0.keys()),
                                 "url": ii0.get("url"), "width": ii0.get("width"), "height": ii0.get("height"),
                                 "licence": (ii0.get("extmetadata") or {}).get("LicenseShortName")})
    for page in pages:
        if page.get("title") in seen_titles:
            continue
        seen_titles.add(page.get("title"))
        ii = (page.get("imageinfo") or [{}])[0]
        meta = ii.get("extmetadata", {})
        desc = re.sub(r"<[^>]+>", "", meta.get("ImageDescription", {}).get("value", "")).lower()
        hay = (page.get("title", "") + " " + desc).lower()
        if words and not any(w in hay for w in words):
            STATS["offtopic"] = STATS.get("offtopic", 0) + 1
            continue
        lic = (meta.get("LicenseShortName", {}).get("value") or meta.get("License", {}).get("value") or "").strip()
        if not lic or BAD_LICENCE.search(lic) or not OK_LICENCE.search(lic):
            STATS["licence"] += 1
            STATS["licences_seen"][lic or "(none)"] = STATS["licences_seen"].get(lic or "(none)", 0) + 1
            continue
        if (ii.get("width") or 0) < MIN_WIDE or (ii.get("height") or 0) < 1350:
            STATS["size"] += 1
            continue
        clean_url = (ii.get("url") or "").split("?")[0]
        if not re.search(r"\.(jpe?g|png|tiff?)$", clean_url, re.I) or re.search(r"logo|map|plan|poster|menu|ticket|stamp", page["title"], re.I):
            STATS["type"] += 1
            continue
        ii["url"] = clean_url
        author = re.sub(r"<[^>]+>", "", meta.get("Artist", {}).get("value", "")).strip()
        out.append({"title": page["title"].replace("File:", ""), "url": ii["url"], "width": ii["width"], "height": ii["height"],
                    "licence": lic, "author": author or ii.get("user", ""),
                    "date": re.sub(r"<[^>]+>", "", meta.get("DateTimeOriginal", {}).get("value", ""))[:40],
                    "source_page": ii.get("descriptionurl", ""), "provider": "Wikimedia Commons"})
    return out


def flickr(query, key, limit=30):
    params = {"method": "flickr.photos.search", "api_key": key, "text": query, "license": "1,2,4,5,7,9,10",
              "extras": "url_o,license,owner_name,date_taken,o_dims", "per_page": limit, "format": "json", "nojsoncallback": 1,
              "content_type": 1, "sort": "relevance"}
    req = urllib.request.Request("https://api.flickr.com/services/rest/?" + urllib.parse.urlencode(params), headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=25) as r:
        data = json.load(r)
    names = {"1": "CC BY-NC-SA", "2": "CC BY-NC", "4": "CC BY", "5": "CC BY-SA", "7": "No known copyright", "9": "CC0", "10": "PD"}
    out = []
    for p in data.get("photos", {}).get("photo", []):
        lic = names.get(str(p.get("license")), "")
        if not p.get("url_o") or BAD_LICENCE.search(lic) or not OK_LICENCE.search(lic):
            continue
        if int(p.get("width_o", 0)) < MIN_WIDE or int(p.get("height_o", 0)) < 1350:
            continue
        out.append({"title": p.get("title", ""), "url": p["url_o"], "width": int(p["width_o"]), "height": int(p["height_o"]),
                    "licence": lic, "author": p.get("ownername", ""), "date": (p.get("datetaken") or "")[:10],
                    "source_page": f"https://www.flickr.com/photos/{p['owner']}/{p['id']}", "provider": "Flickr"})
    return out


def main():
    targets = json.load(open(os.path.join(HERE, "archive_targets.json")))["targets"]
    key = os.environ.get("FLICKR_KEY", "")
    results = []
    for t in targets:
        found = []
        for q in t.get("queries") or [t["place"]]:
            try:
                found += commons(q)
            except Exception as e:  # noqa: BLE001
                STATS["errors"].append(f"commons {q}: {type(e).__name__} {str(e)[:120]}")
                print("commons error", q, type(e).__name__)
            if key:
                try:
                    found += flickr(q, key)
                except Exception as e:  # noqa: BLE001
                    print("flickr error", q, type(e).__name__)
            time.sleep(0.5)
        seen, uniq = set(), []
        for f in found:
            if f["url"] not in seen:
                seen.add(f["url"]); uniq.append(f)
        results.append({**{k: t[k] for k in ("place", "city", "era", "notes") if k in t}, "candidates": uniq[:25], "count": len(uniq)})
        print(f"{t['place']}: {len(uniq)} licensed candidates")
    STATS["licences_seen"] = dict(sorted(STATS["licences_seen"].items(), key=lambda kv: -kv[1])[:30])
    json.dump({"searched": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "flickr": bool(key), "stats": STATS, "targets": results},
              open(os.path.join(HERE, "archive_candidates.json"), "w"), indent=1, ensure_ascii=False)


if __name__ == "__main__":
    main()
