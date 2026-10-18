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
OK_LICENCE = re.compile(r"\b(pd|public domain|cc0|cc-by(?:-sa)?(?:-[0-9.]+)?|cc by(?: sa)?)\b", re.I)
BAD_LICENCE = re.compile(r"\b(nc|nd|non-?commercial|no derivatives|fair use|copyright(?:ed)?\b(?! free))", re.I)
MIN_WIDE = 1800


def api(params):
    url = "https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.load(r)


def commons(query, limit=30):
    data = api({"action": "query", "format": "json", "generator": "search", "gsrsearch": query, "gsrnamespace": 6,
                "gsrlimit": limit, "prop": "imageinfo", "iiprop": "url|size|extmetadata|user", "iiurlwidth": 2500})
    out = []
    for page in data.get("query", {}).get("pages", {}).values():
        ii = (page.get("imageinfo") or [{}])[0]
        meta = ii.get("extmetadata", {})
        lic = (meta.get("LicenseShortName", {}).get("value") or meta.get("License", {}).get("value") or "").strip()
        if not lic or BAD_LICENCE.search(lic) or not OK_LICENCE.search(lic):
            continue
        if (ii.get("width") or 0) < MIN_WIDE or (ii.get("height") or 0) < 1350:
            continue
        if not re.search(r"\.(jpe?g|png|tiff?)$", ii.get("url", ""), re.I):
            continue
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
    json.dump({"searched": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "flickr": bool(key), "targets": results},
              open(os.path.join(HERE, "archive_candidates.json"), "w"), indent=1, ensure_ascii=False)


if __name__ == "__main__":
    main()
