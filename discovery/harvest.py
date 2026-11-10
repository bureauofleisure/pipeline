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
CREDIT = re.compile(r"(?:photograph(?:y|s|er|ed)?|photos?|images?)\s*(?:is|are|was|were)?\s*(?:by|:|courtesy of|©)\s*([A-Z][\w.'&\- ]{2,50}?)(?=[,.;]|\s+(?:unless|and|for|at|in|the)\b)", re.I)
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


def _balanced(page, tag):
    """Every outermost <tag>...</tag> block, matched by depth. A lazy regex stops at the first closing tag, and
    Dezeen nests a 'Read next' teaser <article> inside the article's own header: from 2026-11-02 that cut every
    Dezeen article down to its byline (median 121 characters), so cards had no facts to draw on."""
    out, depth, start = [], 0, 0
    for m in re.finditer(rf"<(/?){tag}\b[^>]*>", page, re.I):
        if not m.group(1):
            if depth == 0:
                start = m.start()
            depth += 1
        elif depth:
            depth -= 1
            if depth == 0:
                out.append(page[start:m.end()])
    return out


def _strip_nested(block, tag):
    """The block without the <tag> blocks nested inside it (teasers and 'read next' cards)."""
    m = re.match(rf"<{tag}\b[^>]*>", block, re.I)
    if not m:
        return block
    inner = block[m.end():]
    for nested in _balanced(inner, tag):
        inner = inner.replace(nested, " ")
    return block[:m.end()] + inner


def article_html(page):
    """The article body (the largest <article>, or <main>), else the whole page. Navigation, newsletter boxes and
    'read next' rails sit outside it; reading them is how every Wallpaper item got a 'coming soon' warning
    and every Dezeen item the designer 'Aedas opens'."""
    for tag in ("article", "main"):
        blocks = [b for b in _balanced(page, tag) if len(b) > 2000]
        if blocks:
            return max(blocks, key=len)
    return page


def _text_of(body):
    body = re.sub(r"<(script|style|nav|aside|footer|header|form)\b.*?</\1>", " ", body, flags=re.S | re.I)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", body))).strip()


MIN_ARTICLE_TEXT = 600   # under this the block found was a header or a teaser, not the article


def page_text(page):
    """The article's words. Tries the article block with nested teasers removed, then <main>, then the whole
    page, and keeps the first that reads like an article. A thin result is better widened than trusted."""
    best = ""
    for tag in ("article", "main"):
        blocks = [b for b in _balanced(page, tag) if len(b) > 2000]
        if not blocks:
            continue
        text = _text_of(_strip_nested(max(blocks, key=len), tag))
        if len(text) >= MIN_ARTICLE_TEXT:
            return text
        best = max(best, text, key=len)
    whole = _text_of(page)
    return whole if len(whole) > len(best) else best


def full_text(page):
    """Whole-page text, for photo credits only: Dezeen prints 'Photography by' in the header, outside <article>."""
    t = re.sub(r"<script.*?</script>|<style.*?</style>", " ", page, flags=re.S)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", t)))


def images_from(page, base):
    """The article's own photos, largest variant of each.
    1. Only the article body (inside <article>, or <main>, else the whole page).
    2. Picks up src, data-src, data-lazy-src, srcset, data-srcset and direct <a href> to an image.
    3. Keeps the largest cluster of filenames sharing a stem (a gallery), plus the og:image's cluster,
       so related-article thumbnails are left behind."""
    body = article_html(page)
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


def _exists(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA}, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def expand_dezeen(photos):
    """Dezeen lazy-loads its galleries; the page exposes only the hero and one or two frames. Probe the
    numbered 2364 sequence. Hosts are static-<year>.dezeen.com since 2026 (the old pattern matched only
    static.dezeen.com, so nothing was ever expanded), and the slug is taken from hero/sq/col names alike."""
    stems = {}
    for p in photos:
        m = re.match(r"(https://static[-\w]*\.dezeen\.com/uploads/\d{4}/\d{2}/.+?)_dezeen(?:_\d{3,4})?_(?:col_\d+|hero\w*|sq\w*)\.jpg$", p["url"])
        if m:
            stems[m.group(1) + "_dezeen_2364_col_"] = True
    known = {p["url"] for p in photos}
    extra = []
    for stem in stems:
        misses = 0
        for n in range(0, 30):
            u = f"{stem}{n}.jpg"
            if u in known:
                misses = 0; continue
            if _exists(u):
                extra.append({"url": u, "width_hint": 2364}); known.add(u); misses = 0
            else:
                misses += 1
                if misses >= 3:
                    break
            time.sleep(0.15)
    # gallery frames first, the hero (usually a crop of one of them) last
    # 2026-11-09: seven articles failed with AttributeError here, on a "_col_" name with no number after it
    def col(p):
        m = re.search(r"_col_(\d+)", p["url"])
        return int(m.group(1)) if m else None
    gallery = [p for p in photos + extra if col(p) is not None]
    return sorted(gallery, key=col) + [p for p in photos if col(p) is None]


def harvest_links(links, label="shortlist"):
    """Re-harvest a fixed list of article links (used after a review picks its shortlist)."""
    out = []
    for link in links:
        rec = {"link": link}
        try:
            page = get(link)
            text = page_text(page)
            t = re.search(r"<title>(.*?)</title>", page, re.S)
            rec["title"] = html.unescape(t.group(1).strip()) if t else ""
            rec["text"] = text[:8000]
            photos = images_from(page, link)
            seen, uniq = set(), []
            for p in photos:
                if p["url"] not in seen:
                    seen.add(p["url"]); uniq.append(p)
            rec["photos"] = expand_dezeen(uniq) if "dezeen.com" in link else uniq
            rec["credits"] = sorted({m.group(1).strip() for m in CREDIT.finditer(full_text(page))})[:8]
            rec["warnings"] = sorted({m.group(1).lower() for m in WARN.finditer(text[:4000])})
            # internal links, so a review can walk from a studio's index page to its project pages
            rec["links"] = sorted({urljoin(link, html.unescape(m.group(1)))
                                   for m in re.finditer(r'href=["\']([^"\'#?]+)', page)
                                   if not m.group(1).startswith(("mailto:", "tel:", "javascript:"))})[:300]
        except Exception as e:  # noqa: BLE001
            rec["page_error"] = f"{type(e).__name__}: {str(e)[:80]}".rstrip(": ")
        rec.setdefault("photos", [])
        out.append(rec)
        time.sleep(0.4)
    os.makedirs(os.path.join(HERE, "harvest"), exist_ok=True)
    path = os.path.join(HERE, "harvest", f"{label}.json")
    json.dump({"harvested": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "items": out}, open(path, "w"), indent=1)
    print(f"{len(out)} links harvested to {path}")


# ---------------------------------------------------------------- portfolio sources (no feed)
def fullsize(url, platform):
    """Rewrite a thumbnail URL to the platform's largest public rendition."""
    if "squarespace" in url or platform == "squarespace":
        return re.sub(r"\?.*$", "", url) + "?format=2500w"
    if platform == "wordpress" or "wp-content" in url:
        return re.sub(r"-\d{3,4}x\d{3,4}(\.\w+)$", r"\1", url)
    if platform == "wix" or "wixstatic" in url:
        return re.sub(r"/v1/fill/[^/]+/", "/v1/fill/w_2500,h_2500,al_c,q_90/", url)
    if "format.com" in url or platform == "format":
        return re.sub(r"/(?:w_|h_)?\d{3,4}(?=/|$)", "/w_2500", url)
    return url


def portfolio_harvest(sources, seen_path, per_source=6):
    """Walk photographer and studio sites: homepage -> project pages -> full-size images.
    Only pages never seen before are fetched, so a site costs a few requests a week, not hundreds."""
    seen = set(json.load(open(seen_path))) if os.path.exists(seen_path) else set()
    out, fetched = [], 0
    for s in sources:
        if s.get("feed") or s.get("disabled") or not s.get("url") or s.get("category") not in ("photographer", "studio"):
            continue
        try:
            home = get(s["url"])
        except Exception as e:  # noqa: BLE001
            out.append({"source": s["name"], "error": f"home {type(e).__name__}"})
            continue
        base = s["url"]
        links = []
        for m in re.finditer(r'href=["\']([^"\'#?]+)', home):
            u = urljoin(base, html.unescape(m.group(1)))
            if not u.startswith(base.rstrip("/")):
                continue
            path = u[len(base.rstrip("/")):].strip("/")
            if not path or path.count("/") > 2:
                continue
            if re.search(r"^(about|contact|info|shop|cart|press|news|blog|journal|privacy|terms|legal|cookie|careers|jobs|search|tag|category|page|feed|wp-|xmlrpc|cdn-cgi|collections|products|account|login|\.well-known|sitemap)", path, re.I):
                continue
            if u not in seen and u not in links:
                links.append(u)
        for u in links[:per_source]:
            fetched += 1
            rec = {"source": s["name"], "tier": s.get("tier", 1), "category": s["category"], "link": u, "title": "", "date": "", "summary": ""}
            try:
                page = get(u)
                t = re.search(r"<title>(.*?)</title>", page, re.S)
                rec["title"] = html.unescape(t.group(1).strip()) if t else u
                text = page_text(page)
                rec["text"] = text[:4000]
                photos, uniq = images_from(page, u), {}
                for ph in photos:
                    fu = fullsize(ph["url"], s.get("platform", ""))
                    uniq[fu] = max(uniq.get(fu, 0), ph["width_hint"])
                rec["photos"] = [{"url": k, "width_hint": v or 2500} for k, v in uniq.items()][:30]
                rec["credits"] = [s["name"]] if s["category"] == "photographer" else sorted({m.group(1).strip() for m in CREDIT.finditer(full_text(page))})[:5]
                rec["designers"] = [s["name"]] if s["category"] == "studio" else []
                rec["warnings"] = sorted({m.group(1).lower() for m in WARN.finditer(text[:3000])})
                rec["hospitality"] = bool(KEEP.search(rec["title"] + " " + text[:1500]))
            except Exception as e:  # noqa: BLE001
                rec["page_error"] = f"{type(e).__name__}: {str(e)[:80]}".rstrip(": ")
            seen.add(u)
            if rec.get("photos") and len(rec["photos"]) >= 3:
                out.append(rec)       # pages without a gallery are not projects
            time.sleep(0.4)
    json.dump(sorted(seen), open(seen_path, "w"))
    print(f"portfolio: {fetched} new project pages from {sum(1 for s in sources if s.get('category') in ('photographer','studio') and not s.get('disabled') and not s.get('feed'))} sites")
    return out


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
                    text = page_text(page)
                    rec["text"] = text[:6000]
                    rec["photos"] = images_from(page, it["link"])
                    if "dezeen.com" in it["link"]:
                        rec["photos"] = expand_dezeen(rec["photos"])
                    rec["credits"] = sorted({m.group(1).strip() for m in CREDIT.finditer(full_text(page))})[:5]
                    rec["designers"] = sorted({m.group(1).strip() for m in DESIGN.finditer(text)})[:5]
                    rec["warnings"] = sorted({m.group(1).lower() for m in WARN.finditer(text[:4000])})
                except Exception as e:  # noqa: BLE001
                    rec["page_error"] = f"{type(e).__name__}: {str(e)[:80]}".rstrip(": ")
                time.sleep(0.4)
            out.append(rec)
    out += portfolio_harvest(src, os.path.join(HERE, "seen_pages.json"))
    os.makedirs(os.path.join(HERE, "harvest"), exist_ok=True)
    path = os.path.join(HERE, "harvest", time.strftime("%Y-%m-%d") + ".json")
    json.dump({"harvested": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "pages_fetched": pages, "items": out},
              open(path, "w"), indent=1)
    n = sum(1 for r in out if "title" in r)
    print(f"{n} items kept, {pages} pages fetched, {sum(1 for r in out if 'error' in r)} feed errors")
    # what this run produced, for the daily heartbeat; the workflow goes red on problems after the save
    import health
    health.write("harvest", *health.harvest_health(out, src))
    sys.exit(0)


if __name__ == "__main__":
    main()
