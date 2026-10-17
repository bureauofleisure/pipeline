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


def _stem(url):
    """Filename with size/sequence suffixes stripped, so one project's gallery shares a stem."""
    name = url.split("?")[0].rsplit("/", 1)[-1].lower()
    name = re.sub(r"\.(jpe?g|png|webp)$", "", name)
    name = re.sub(r"(_dezeen)?(_\d{3,4})?(_col|_sq\w*|_hero\d*|_featured|_\d+|-\d+x\d+|-scaled|-\d+)+$", "", name)
    return name


def images_from(page, base):
    """The article's own photos, largest variant of each.
    1. Only the article body (inside <article>, or <main>, else the whole page).
    2. Picks up src, data-src, data-lazy-src, srcset, data-srcset and direct <a href> to an image.
    3. Keeps the largest cluster of filenames sharing a stem (a gallery), plus the og:image's cluster,
       so related-article thumbnails are left behind."""
    body = page
    for tag in ("article", "main"):
        m = re.search(rf"<{tag}[\s>].*?</{tag}>", page, re.S | re.I)
        if m and len(m.group(0)) > 2000:
            body = m.group(0)
            break
    og = None
    m = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)', page)
    if m:
        og = urljoin(base, html.unescape(m.group(1)))
    found = {}

    def add(u, w):
        u = urljoin(base, html.unescape(u.strip()))
        if not re.search(r"\.(jpe?g|png|webp)(\?|$)", u, re.I):
            return
        low = u.lower()
        if any(x in low for x in ("logo", "icon", "avatar", "badge", "sprite", "advert", "/ads/", "gravatar")):
            return
        found[u] = max(found.get(u, 0), w)

    for m in re.finditer(r"<img[^>]+>", body):
        tag = m.group(0)
        for attr in ("srcset", "data-srcset", "data-lazy-srcset"):
            ss = re.search(attr + r'=["\']([^"\']+)', tag)
            if ss:
                for part in ss.group(1).split(","):
                    bits = part.strip().split()
                    if bits:
                        w = int(bits[1][:-1]) if len(bits) > 1 and bits[1][:-1].isdigit() else 0
                        add(bits[0], w)
        for attr in ("data-src", "data-lazy-src", "data-original", "src"):
            sv = re.search(r"\s" + attr + r'=["\']([^"\']+)', tag)
            if sv and not sv.group(1).startswith("data:"):
                wm = re.search(r'\swidth=["\']?(\d+)', tag)
                add(sv.group(1), int(wm.group(1)) if wm else 0)
    for m in re.finditer(r'<a[^>]+href=["\']([^"\']+\.(?:jpe?g|png|webp))["\']', body, re.I):
        add(m.group(1), 0)

    if not found:
        return []
    clusters = {}
    for u in found:
        clusters.setdefault(_stem(u), []).append(u)
    keep = set()
    biggest = max(clusters.values(), key=len)
    if len(biggest) >= 3:
        keep.update(biggest)
    if og:
        keep.update(clusters.get(_stem(og), []))
        keep.add(og)
    if not keep:
        keep = set(found)
    # Dezeen serves a 2364-wide version of every gallery image; prefer it
    out = []
    for u in sorted(keep):
        if "dezeen.com" in u:
            u2 = re.sub(r"_dezeen_(\d{3,4})_", "_dezeen_2364_", u)
            u2 = re.sub(r"-\d{3,4}x\d{3,4}(\.\w+)$", r"\1", u2)
            out.append({"url": u2, "width_hint": 2364 if "_2364_" in u2 else found.get(u, 0)})
        else:
            out.append({"url": re.sub(r"-\d{3,4}x\d{3,4}(\.\w+)$", r"\1", u) if "wp-content" in u else u,
                        "width_hint": found.get(u, 0)})
    return out[:40]


def harvest_links(links, label="shortlist"):
    """Re-harvest a fixed list of article links (used after a review picks its shortlist)."""
    out = []
    for link in links:
        rec = {"link": link}
        try:
            page = get(link)
            text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", page, flags=re.S)
            text = html.unescape(re.sub(r"<[^>]+>", " ", text))
            text = re.sub(r"\s+", " ", text)
            t = re.search(r"<title>(.*?)</title>", page, re.S)
            rec["title"] = html.unescape(t.group(1).strip()) if t else ""
            rec["text"] = text[:6000]
            rec["photos"] = images_from(page, link)
            rec["credits"] = sorted({m.group(1).strip() for m in CREDIT.finditer(text)})[:8]
            rec["warnings"] = sorted({m.group(1).lower() for m in WARN.finditer(text[:2500])})
        except Exception as e:  # noqa: BLE001
            rec["page_error"] = type(e).__name__
        out.append(rec)
        time.sleep(0.4)
    os.makedirs(os.path.join(HERE, "harvest"), exist_ok=True)
    path = os.path.join(HERE, "harvest", f"{label}.json")
    json.dump({"harvested": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "items": out}, open(path, "w"), indent=1)
    print(f"{len(out)} links harvested to {path}")


def main():
    shortlist = os.path.join(HERE, "shortlist.json")
    if os.environ.get("HARVEST_SHORTLIST") and os.path.exists(shortlist):
        harvest_links(json.load(open(shortlist))["links"])
        return
    src = json.load(open(os.path.join(HERE, "sources.json")))["sources"]
    out, pages = [], 0
    for s in src:
        if s.get("disabled") or not s.get("url"):
            continue
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
