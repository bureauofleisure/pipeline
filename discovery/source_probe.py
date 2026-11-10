"""Score candidate sources on what the weekly harvest would actually get from them.

Reads discovery/source_candidates.json (candidates plus the rubric), fetches each candidate the way
harvest.py does (same link finder, same image finder, same honest user agent), measures real pixel sizes
against the build's gate, and writes discovery/source_probe_report.json with a score out of 100 per
candidate. It changes no other file: adding sources is a separate, deliberate step.

Why: on 2026-11-10 the list held 200 sources, 131 of them portfolios, and 13 produced an item in the Monday
harvest. A source that looks right and yields nothing is not a source. This measures yield before adding.
"""
import io
import json
import os
import re
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import harvest as H          # noqa: E402
import grow_sources as G     # noqa: E402

from PIL import ImageFile    # noqa: E402

INDEX_PATHS = ("", "projects", "work", "portfolio", "hospitality", "interiors", "projects/hospitality", "work/hospitality", "commercial", "hotels")
PER_SOURCE_S = 150           # a candidate that takes longer than this is too slow for the harvest anyway


def size_of(url):
    """Pixel size from the first 256 KB of the file."""
    req = urllib.request.Request(url, headers={"User-Agent": H.UA, "Accept": "image/*", "Range": "bytes=0-262143"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            p = ImageFile.Parser()
            got = 0
            while got < 262144:
                chunk = r.read1(32768)
                if not chunk:
                    break
                got += len(chunk)
                p.feed(chunk)
                if p.image:
                    return p.image.size
    except Exception:  # noqa: BLE001
        return None
    return None


def passes_gate(size):
    """1080 x 1350 after the 4:5 crop, as build_posts.py requires."""
    if not size:
        return False
    w, h = size
    return h >= 1350 and w >= 1080 if w / h >= 0.8 else w >= 1080 and w / 0.8 >= 1350


def probe_pages(links, platform, portfolio, deadline):
    pages = []
    for u in links:
        if time.time() > deadline:
            break
        rec = {"url": u, "photos": 0, "hospitality": False, "sizes": []}
        try:
            page = H.get(u, timeout=15)
            text = H.page_text(page)
            t = re.search(r"<title>(.*?)</title>", page, re.S)
            title = t.group(1) if t else ""
            photos = H.images_from(page, u, gallery=portfolio)
            if portfolio:
                uniq = []
                for ph in photos:
                    fu = H.fullsize(ph["url"], platform)
                    if fu not in uniq:
                        uniq.append(fu)
                urls = uniq[:30]
            else:
                urls = [p["url"] for p in photos]
            rec["photos"] = len(urls)
            rec["hospitality"] = bool(H.KEEP.search(title + " " + text[:1500]))
            # two photos per page: the second and the last, not the logo or the hero banner
            for s in ([urls[1], urls[-1]] if len(urls) >= 3 else urls[:2]):
                sz = size_of(s)
                rec["sizes"].append(list(sz) if sz else None)
                time.sleep(0.2)
        except Exception as e:  # noqa: BLE001
            rec["error"] = f"{type(e).__name__}: {str(e)[:60]}"
        pages.append(rec)
        time.sleep(0.3)
    return pages


def probe(c):
    out = {"name": c["name"], "url": c["url"], "reachable": False}
    deadline = time.time() + PER_SOURCE_S
    if c.get("feed"):
        items = None
        for f in (c["feed"], c["url"].rstrip("/") + "/feed/", c["url"].rstrip("/") + "/rss", c["url"].rstrip("/") + "/?format=rss"):
            try:
                items = H.parse_feed(H.get(f, timeout=15))
                if items:
                    out["feed"] = f
                    break
            except Exception as e:  # noqa: BLE001
                out["error"] = f"{type(e).__name__}: {str(e)[:60]}"
        if not items:
            return out
        out.pop("error", None)
        out["reachable"] = True
        kept = [i for i in items if H.KEEP.search(i["title"] + " " + i["summary"])]
        out["items"], out["kept"] = len(items), len(kept)
        out["pages"] = probe_pages([i["link"] for i in kept[:3]], "", False, deadline)
        out["hospitality_share"] = len(kept) / len(items)
        out["volume_score"] = min(10, len(kept))
    else:
        best, home_body, final = None, "", c["url"]
        for path in INDEX_PATHS:
            if time.time() > deadline:
                break
            entry = c["url"].rstrip("/") + "/" + path
            try:
                req = urllib.request.Request(entry, headers={"User-Agent": H.UA, "Accept": "text/html,*/*"})
                with urllib.request.urlopen(req, timeout=15) as r:
                    fin, body = r.geturl(), r.read(1_500_000).decode("utf-8", "replace")
            except Exception as e:  # noqa: BLE001
                if not path:
                    # the same site often answers only with, or only without, www
                    alt = re.sub(r"^(https?://)www\.", r"\1", c["url"]) if "://www." in c["url"] else c["url"].replace("://", "://www.", 1)
                    if not c.get("_alt"):
                        c = dict(c, url=alt, _alt=True)
                        return probe(c)
                    out["error"] = f"{type(e).__name__}: {str(e)[:60]}"
                    return out
                continue
            if not path:
                home_body, final = body, fin
                # the site must be theirs: a distinctive word of the name in the page, and not a parked domain
                words = [w for w in G.slug(c["name"]).split() if len(w) > 3]
                low = body.lower()
                if G.PARKED.search(fin) or G.PARKED.search(low[:20000]) or (words and not any(w in low for w in words)):
                    out["error"] = "not their site (name not on the page, or a parked domain)"
                    return out
                out["reachable"] = True
            links = H.project_links(body, c["url"])
            if best is None or len(links) > len(best[1]) * 1.5 + 3:   # an index page must clearly beat the homepage
                best = (entry, links)
            time.sleep(0.3)
        out["final"] = final
        out["platform"] = G.platform(home_body)
        out["entry"], links = best
        out["links"] = len(links)
        out["pages"] = probe_pages(links[:5], out["platform"], True, deadline)
        out["hospitality_share"] = (sum(p["hospitality"] for p in out["pages"]) / len(out["pages"])) if out["pages"] else 0
        out["volume_score"] = min(10, round(len(links) / 3))
    pages = out["pages"]
    want = 3 if c.get("feed") else 5
    good = sum(1 for p in pages if p["photos"] >= 4)
    sizes = [s for p in pages for s in p["sizes"]]
    measured = [s for s in sizes if s]
    out["good_pages"] = good
    out["photos_measured"] = len(measured)
    out["photos_passing"] = sum(1 for s in measured if passes_gate(s))
    m = {
        "harvestable": round(20 * good / want, 1),
        "photo_size": round(20 * out["photos_passing"] / len(sizes), 1) if sizes else 0,
        "hospitality": round(15 * out["hospitality_share"], 1),
        "volume": out["volume_score"],
        "lens": c["lens"], "us": c["us"], "mix": c["mix"],
    }
    out["scores"] = m
    out["score"] = round(sum(m.values()), 1)
    # suitable = the harvest would get something usable from it
    out["suitable"] = good >= 1 and out["photos_passing"] >= 1
    return out


def main():
    doc = json.load(open(os.path.join(HERE, "source_candidates.json")))
    have = {re.sub(r"^https?://(www\.)?", "", s["url"]).split("/")[0].lower()
            for s in json.load(open(os.path.join(HERE, "sources.json")))["sources"] if s.get("url")}
    results = []
    for c in doc["candidates"]:
        host = re.sub(r"^https?://(www\.)?", "", c["url"]).split("/")[0].lower()
        if host in have:
            results.append({"name": c["name"], "url": c["url"], "reachable": False, "error": "already a source"})
            continue
        t0 = time.time()
        try:
            r = probe(c)
        except Exception as e:  # noqa: BLE001
            r = {"name": c["name"], "url": c["url"], "reachable": False, "error": f"probe {type(e).__name__}: {str(e)[:80]}"}
        r["seconds"] = round(time.time() - t0)
        results.append(r)
        print(f"{r.get('score', 0):5} {'OK ' if r.get('suitable') else '-- '} {c['name']}: "
              f"{r.get('error') or ''} links={r.get('links', r.get('kept', ''))} good={r.get('good_pages', '')} "
              f"pass={r.get('photos_passing', '')}/{r.get('photos_measured', '')} {r['seconds']}s", flush=True)
    ranked = sorted([r for r in results if r.get("suitable")], key=lambda r: -r["score"])
    json.dump({"probed": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "rubric": doc["rubric"],
               "ranked": [r["name"] for r in ranked], "results": results},
              open(os.path.join(HERE, "source_probe_report.json"), "w"), indent=1, ensure_ascii=False)
    print(f"{len(ranked)} suitable of {len(results)}")


if __name__ == "__main__":
    main()
