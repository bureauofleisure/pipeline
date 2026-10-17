"""Harvest: pull every feed in sources.json, keep the hospitality-looking items, fetch each article
and extract what the lens needs: title, link, date, summary, candidate photos (largest versions),
designer and photographer credits, and any render/AI warning words.

Writes discovery/harvest/<date>.json. Runs on GitHub Actions (open network, no prompts).
Nothing here decides anything: scoring happens in the weekly review, with this file as its input.
"""
import html
import json
import os
import re
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from urllib.parse import urljoin

HERE = os.path.dirname(os.path.abspath(__file__))
UA = "Mozilla/5.0 (compatible; BureauOfLeisureBot/1.0; +https://bureauofleisure.co)"
PER_SOURCE = int(os.environ.get("HARVEST_PER_SOURCE", "12"))
MAX_PAGES = int(os.environ.get("HARVEST_MAX_PAGES", "220"))

KEEP = re.compile(r"\b(hotel|hotels|inn|resort|lodge|guesthouse|bar|bars|cocktail|pub|tavern|restaurant|"
                  r"bistro|brasserie|trattoria|osteria|diner|cafe|café|coffee|bakery|club|members|lounge|"
                  r"bathhouse|bath|baths|banya|sauna|spa|pool|lido|supper|speakeasy|wine|listening|"
                  r"hospitality|interiors?|dining)\b", re.I)
WARN = re.compile(r"\b(render|rendering|renders|visuali[sz]ation|visuali[sz]ed|concept|unbuilt|proposal|"
                  r"proposed|speculative|midjourney|ai[- ]generated|generated|imagined|coming soon|"
                  r"will open|set to open|planned)\b", re.I)
CREDIT = re.compile(r"(?:photograph(?:y|s|er|ed)?|photos?|images?)\s*(?:by|:|courtesy of|©)\s*([A-Z][\w.'&\- ]{2,60})", re.I)
DESIGN = re.compile(r"(?:designed by|design(?:er)?s?:|interiors? by|architects?:|by)\s+([A-Z][\w.'&\- ]{2,60}?)(?:[,.]|\s+(?:for|at|in)\b)", re.I)


def get(url, timeout=25, limit=1_500_000):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(limit).decode("utf-8", "replace")


def parse_feed(body):
    items = []
    try:
        root = ET.fromstring(body.encode("utf-8", "replace"))
        for it in root.findall(".//item"):
            items.append({"title": (it.findtext("title") or "").strip(),
                          "link": (it.findtext("link") or "").strip(),
                          "date": (it.findtext("pubDate") or "").strip(),
                          "summary": re.sub(r"<[^>]+>", " ", it.findtext("description") or "")[:600]})
        ns = "{http://www.w3.org/2005/Atom}"
        for it in root.findall(f".//{ns}entry"):
            link = it.find(f"{ns}link")
            items.append({"title": (it.findtext(f"{ns}title") or "").strip(),
                          "link": (link.get("href") if link is not None else "").strip(),
                          "date": (it.findtext(f"{ns}updated") or "").strip(),
                          "summary": re.sub(r"<[^>]+>", " ", it.findtext(f"{ns}summary") or "")[:600]})
    except ET.ParseError:
        for m in re.finditer(r"<item>(.*?)</item>", body, re.S):
            chunk = m.group(1)
            t = re.search(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", chunk, re.S)
            l = re.search(r"<link>(.*?)</link>", chunk, re.S)
            d = re.search(r"<pubDate>(.*?)</pubDate>", chunk, re.S)
            items.append({"title": html.unescape(t.group(1).strip()) if t else "", "link": l.group(1).strip() if l else "",
                          "date": d.group(1).strip() if d else "", "summary": ""})
    return [i for i in items if i["link"]]


def images_from(page, base):
    """Candidate photos, largest variant of each, with any width hint."""
    found = {}
    for m in re.finditer(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)', page):
        found.setdefault(urljoin(base, html.unescape(m.group(1))), 0)
    for m in re.finditer(r'<img[^>]+>', page):
        tag = m.group(0)
        srcset = re.search(r'srcset=["\']([^"\']+)', tag)
        best, best_w = None, 0
        if srcset:
            for part in srcset.group(1).split(","):
                bits = part.strip().split()
                if not bits:
                    continue
                w = int(bits[1][:-1]) if len(bits) > 1 and bits[1].endswith("w") and bits[1][:-1].isdigit() else 0
                if w >= best_w:
                    best, best_w = bits[0], w
        if not best:
            src = re.search(r'\ssrc=["\']([^"\']+)', tag)
            best = src.group(1) if src else None
            wm = re.search(r'\swidth=["\']?(\d+)', tag)
            best_w = int(wm.group(1)) if wm else 0
        if best and not best.startswith("data:"):
            u = urljoin(base, html.unescape(best))
            if re.search(r"\.(jpe?g|png|webp)(\?|$)", u, re.I) and "logo" not in u.lower() and "icon" not in u.lower():
                found[u] = max(found.get(u, 0), best_w)
    return [{"url": u, "width_hint": w} for u, w in found.items()][:40]


def main():
    src = json.load(open(os.path.join(HERE, "sources.json")))["sources"]
    out, pages = [], 0
    for s in src:
        if not s.get("feed"):
            continue
        try:
            items = parse_feed(get(s["feed"]))
        except Exception as e:  # noqa: BLE001
            out.append({"source": s["name"], "error": f"feed {type(e).__name__}"})
            continue
        kept = [i for i in items if KEEP.search(i["title"] + " " + i["summary"])][:PER_SOURCE]
        for it in kept:
            rec = {"source": s["name"], "tier": s["tier"], "category": s["category"], **it}
            if pages < MAX_PAGES:
                pages += 1
                try:
                    page = get(it["link"])
                    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", page, flags=re.S)
                    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
                    text = re.sub(r"\s+", " ", text)
                    rec["text"] = text[:3000]
                    rec["photos"] = images_from(page, it["link"])
                    rec["credits"] = sorted({m.group(1).strip() for m in CREDIT.finditer(text)})[:5]
                    rec["designers"] = sorted({m.group(1).strip() for m in DESIGN.finditer(text)})[:5]
                    rec["warnings"] = sorted({m.group(1).lower() for m in WARN.finditer(text[:1500])})
                except Exception as e:  # noqa: BLE001
                    rec["page_error"] = type(e).__name__
                time.sleep(0.4)
            out.append(rec)
    os.makedirs(os.path.join(HERE, "harvest"), exist_ok=True)
    path = os.path.join(HERE, "harvest", time.strftime("%Y-%m-%d") + ".json")
    json.dump({"harvested": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "pages_fetched": pages, "items": out},
              open(path, "w"), indent=1)
    n = sum(1 for r in out if "title" in r)
    print(f"{n} items kept, {pages} pages fetched, {sum(1 for r in out if 'error' in r)} feed errors")
    sys.exit(0)


if __name__ == "__main__":
    main()
