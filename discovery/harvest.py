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
MAX_PAGES = int(os.environ.get("HARVEST_MAX_PAGES", "340"))   # 2026-11-10: raised with the new feeds, so late feeds still get their pages

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


_FORMAT_ID = re.compile(r"/([0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12})/\d+/\d+/[^/?]+\.(?:jpe?g|png|webp)", re.I)


def _photo_key(url):
    """One key per photograph, whatever size or crop of it the page links.
    2026-11-10: Format portfolio sites (Luke Hayes, Evan Sung) list every srcset size of each photo as a
    signed URL plus cropped creatorcdn thumbnails, so one project arrived as 11 entries of 2 photos and
    Evan Sung's 4 pages as 120 entries with 82 repeats. Eater (Vox WordPress) links one image three times
    with different crop= query strings."""
    m = _FORMAT_ID.search(url)
    if m:
        return "format:" + m.group(1).lower()
    if "wp-content" in url:
        return url.split("?")[0]
    return url


def _dedupe(found):
    """{url: width} -> {url: width} with one entry per _photo_key: the widest rendition, and an uncropped
    one over a crop of the same width. Order of first appearance is kept."""
    best = {}
    for u, w in found.items():
        k = _photo_key(u)
        cropped = bool(re.search(r"[?&]crop=(?!0%2C0%2C100%2C100)", u)) or "format.creatorcdn.com" in u
        rank = (w, not cropped)
        if k not in best or rank > best[k][1]:
            best[k] = (u, rank)
    keep = {u for u, _ in best.values()}
    return {u: w for u, w in found.items() if u in keep}


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


def images_from(page, base, gallery=False):
    """The article's own photos, largest variant of each.
    gallery=True is for portfolio project pages (2026-11-10). Their photos rarely share a filename stem, so
    the cluster rule in step 3 kept one or two photos of a twenty-photo project and the page was thrown away
    as "not a project": 131 portfolio sources, 13 producing. For those pages every photo in the body is
    kept, except thumbnails that link to another page of the site (other projects) and anything known to
    be small.
    1. Only the article body (inside <article>, or <main>, else the whole page).
    2. Picks up src, data-src, data-lazy-src, srcset, data-srcset and direct <a href> to an image.
    3. Keeps the largest cluster of filenames sharing a stem (a gallery), plus the og:image's cluster,
       so related-article thumbnails are left behind."""
    if gallery == "strict":
        gallery = True
    elif gallery:
        # Try the strict reading first (thumbnails that link elsewhere removed). Some sites wrap every
        # gallery photo in a link, which leaves nothing; for those fall back to the article rule, which is
        # what this function did before 2026-11-10. Whichever finds a real gallery (4 or more) wins.
        strict = images_from(page, base, gallery="strict")
        if len(strict) >= 4:
            return strict
        loose = images_from(page, base)
        return loose if len(loose) > len(strict) else strict
    body = article_html(page)
    if gallery:
        # a photo wrapped in a link to another page is a doorway to a different project, not this one
        body = re.sub(r'<a\b[^>]*href=["\'](?![^"\']+\.(?:jpe?g|png|webp)\b)[^"\'#][^"\']*["\'][^>]*>(?:(?!</a>).)*?<img\b(?:(?!</a>).)*?</a>',
                      " ", body, flags=re.S | re.I)
        # a thumbnail that links to its own full-size file: keep the link, drop the thumbnail
        body = re.sub(r'(<a\b[^>]*href=["\'][^"\']+\.(?:jpe?g|png|webp)["\'][^>]*>)(?:(?!</a>).)*?</a>', r"\1</a>", body, flags=re.S | re.I)
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
    if gallery:
        found = _dedupe(found)
        return [{"url": u, "width_hint": w} for u, w in found.items() if not 0 < w < 600][:60]
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
    out = {}
    for u in sorted(keep):
        if "dezeen.com" in u:
            u2 = re.sub(r"_dezeen_(\d{3,4})_", "_dezeen_2364_", u)
            u2 = re.sub(r"-(?:\d{3,4}x\d{3,4}|scaled)(\.\w+)$", r"\1", u2)
            w = 2364 if "_2364_" in u2 else found.get(u, 0)
        else:
            u2 = re.sub(r"-\d{3,4}x\d{3,4}(\.\w+)$", r"\1", u) if "wp-content" in u else u
            w = found.get(u, 0)
        # one entry per photo: several sizes of one image rewrite to the same address, and counting each
        # filled the 40-photo cap with repeats (2026-11-10: a Dezeen gallery arrived as 40 entries, 18 photos)
        out[u2] = max(out.get(u2, 0), w)
    out = _dedupe(out)
    return [{"url": u, "width_hint": w} for u, w in out.items()][:40]


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


SKIP_PATH = re.compile(r"^(about|contact|info|shop|cart|press|news|blog|journal|privacy|terms|legal|cookie|careers|jobs|search|tag|category|page|feed|wp-|xmlrpc|cdn-cgi|collections|products|account|login|\\.well-known|sitemap)", re.I)


# A section of a portfolio: a page that lists several projects (Hotels, Hospitality, Portraits, Selected Works,
# Our Team). 2026-11-11: a quarter of portfolio items were these, each mixing photos of several places. They are
# not projects: the harvest reads the project links on them instead of keeping them as items.
SECTION = re.compile(r"^(hotels?|restaurants?|bars?|cafes?|clubs?|hospitality|residential|residences?|interiors?|our-interiors|"
                     r"global-interiors|commercial|retail|airport|other|projects?|projects-page|portfolio|work|works|selected|"
                     r"selected-works|team|our-team|people|studio|portraits?|still-life|travel|editorial|love-list|covers?|"
                     r"gallery|photography|awards|talks|index|en|fr|de|es|it|home|accueil|design|archive|news|clients)(-[0-9ivx]+)*$", re.I)


def _bare(u):
    return re.sub(r"^https?://(www\.)?", "", u).rstrip("/").lower()


def is_section(url, source=None):
    """True for a page that lists projects rather than showing one."""
    path = _bare(url).split("/", 1)[1] if "/" in _bare(url) else ""
    last = path.rsplit("/", 1)[-1]
    if not path or SECTION.match(last):
        return True
    if source and _bare(url) in (_bare(source.get("url", "")), _bare(source.get("index") or "")):
        return True
    return False


def project_links(home, base, seen=()):
    """Links on a portfolio page that could be project pages: same site, at most three levels deep, not
    housekeeping, not seen before. Used by the weekly harvest and by source_probe.py, so a source is judged
    on exactly what the harvest would fetch from it."""
    links = []
    seen_bare = {_bare(x) for x in seen}
    bare = lambda x: re.sub(r"^https?://(www\.)?", "", x)   # noqa: E731  same site with or without www or https
    root = bare(base.rstrip("/"))
    for m in re.finditer(r'href=["\']([^"\'#?]+)', home):
        u = urljoin(base, html.unescape(m.group(1)))
        if not bare(u).startswith(root):
            continue
        path = bare(u)[len(root):].strip("/")
        if not path or path.count("/") > 2:
            continue
        if SKIP_PATH.search(path) or re.search(r"\.(?:css|js|png|jpe?g|svg|ico|pdf|xml|json|woff2?)$", path, re.I):
            continue
        # one address per page: the same project reached with and without www is one page
        u = base.rstrip("/").split("://")[0] + "://" + base.split("://", 1)[1].split("/")[0] + "/" + path
        if _bare(u) in seen_bare or any(_bare(u) == _bare(x) for x in links):
            continue
        links.append(u)
    return links


def portfolio_harvest(sources, seen_path, per_source=int(os.environ.get("HARVEST_PORTFOLIO_PAGES") or 10)):
    """Walk photographer and studio sites: homepage -> project pages -> full-size images.
    Only pages never seen before are fetched, so a site costs a few requests a week, not hundreds.
    A source may name an `index` (the page that lists its projects, when the homepage does not) and a
    `per_run` (pages to take each week; new sources get more so their back catalogue arrives in weeks)."""
    seen = set(json.load(open(seen_path))) if os.path.exists(seen_path) else set()
    out, fetched = [], 0
    for s in sources:
        if s.get("feed") or s.get("disabled") or not s.get("url") or s.get("category") not in ("photographer", "studio"):
            continue
        try:
            home = get(s.get("index") or s["url"])
        except Exception as e:  # noqa: BLE001
            out.append({"source": s["name"], "error": f"home {type(e).__name__}"})
            continue
        base = s["url"]
        links = project_links(home, base, seen)
        per_source_now = int(s.get("per_run") or per_source)
        # sections (Hotels, Hospitality, Selected Works) are read for their project links, never kept as items;
        # they are not marked seen, because new projects appear on them
        sections_read, taken, i = 0, 0, 0
        while i < len(links) and taken < per_source_now:
            u = links[i]
            i += 1
            if is_section(u, s):
                if sections_read < 4:
                    sections_read += 1
                    try:
                        more = project_links(get(u), base, seen)
                        links.extend(x for x in more if _bare(x) not in {_bare(y) for y in links})
                    except Exception:  # noqa: BLE001
                        pass
                    time.sleep(0.4)
                continue
            taken += 1
            fetched += 1
            rec = {"source": s["name"], "tier": s.get("tier", 1), "category": s["category"], "link": u, "title": "", "date": "", "summary": ""}
            try:
                page = get(u)
                t = re.search(r"<title>(.*?)</title>", page, re.S)
                rec["title"] = html.unescape(t.group(1).strip()) if t else u
                text = page_text(page)
                rec["text"] = text[:4000]
                photos, uniq = images_from(page, u, gallery=True), {}
                for ph in photos:
                    fu = fullsize(ph["url"], s.get("platform", ""))
                    uniq[fu] = max(uniq.get(fu, 0), ph["width_hint"])
                uniq = _dedupe(uniq)
                rec["photos"] = [{"url": k, "width_hint": v or 2500} for k, v in uniq.items()][:30]
                rec["credits"] = [s["name"]] if s["category"] == "photographer" else sorted({m.group(1).strip() for m in CREDIT.finditer(full_text(page))})[:5]
                rec["designers"] = [s["name"]] if s["category"] == "studio" else []
                rec["warnings"] = sorted({m.group(1).lower() for m in WARN.finditer(text[:3000])})
                rec["hospitality"] = bool(KEEP.search(rec["title"] + " " + text[:1500]))
            except Exception as e:  # noqa: BLE001
                rec["page_error"] = f"{type(e).__name__}: {str(e)[:80]}".rstrip(": ")
            seen.add(u)
            if s.get("hospitality_only") and not rec.get("hospitality"):
                pass                  # a source that mostly shoots other things: only its hotels, bars and restaurants are kept
            elif rec.get("photos") and len(rec["photos"]) >= 3:
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
