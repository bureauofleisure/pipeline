"""Turn a reviewed pick list (discovery/picks/<batch>.json) plus the shortlist harvest into a batch file.
The review writes the editorial fields; this script attaches the harvested photo URLs (deduplicated,
Dezeen 2364 versions first) and validates the cards, so the batch the build receives is complete."""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "build"))
from build_posts import validate_card  # noqa: E402


def photos_for(link, harvest):
    for it in harvest["items"]:
        if it.get("link") == link:
            seen, out = set(), []
            for p in it.get("photos", []):
                u = p["url"]
                if u in seen or u.endswith("-sq.jpg") or "_sq" in u.rsplit("/", 1)[-1]:
                    continue  # square crops are duplicates of gallery images
                seen.add(u)
                out.append(u)
            return out, it.get("credits", [])
    return [], []


PRESS_DOMAINS = ("dezeen.com", "archdaily.com", "wallpaper.com", "architecturaldigest.com", "elledecor.com", "designboom.com",
                 "yellowtrace.com.au", "thespaces.com", "design-milk.com", "sightunseen.com", "hospitalitydesign.com",
                 "sleepermagazine.com", "hoteldesigns.net", "eater.com", "resy.com", "cntraveler.com", "frameweb.com",
                 "dwell.com", "surfacemag.com", "interiordesign.net", "admiddleeast.com", "domusweb.it")
MAX_PRESS_SHARE = 0.25      # at most one post in four may be sourced from a design-press article
MIN_NOT_NEWS_SHARE = 0.40   # at least this share is archive, or published more than a year ago
MIN_US_SHARE = 0.60         # the brief says 80%; the hard floor is 60% so a thin US week still builds
US_CITIES = ("new york", "brooklyn", "austin", "miami", "los angeles", "chicago", "san francisco", "palm springs",
             "new orleans", "nashville", "philadelphia", "boston", "detroit", "portland", "seattle", "savannah", "charleston",
             "houston", "dallas", "denver", "washington", "las vegas", "santa fe", "marfa", "hudson", "montauk", "catskills",
             "narragansett", "newport", "rhode island", "idlewild", "west virginia", "white sulphur springs")


BRING_FROM = "2026-W47"     # batches from here on carry a BRING line on every post (Lewie, 2026-11-16)
BRING_WINDOW = 20           # no line repeats within this many posts


def bring_bank():
    """APPROVED lines in bank order, and every line in the bank (approved, reserved, conditional)."""
    approved, every, section = [], set(), ""
    for raw in open(os.path.join(ROOT, "build", "bring_bank.txt")):
        l = raw.strip()
        if l.startswith("#"):
            section = l.lstrip("# ").split(":")[0].split()[0].upper() if l.lstrip("# ") else section
            continue
        line = l.split("|")[0].strip().upper()
        if not line.startswith("BRING "):
            continue
        every.add(line)
        if section == "APPROVED" and "|" not in l:
            approved.append(line)
    return approved, every


def recent_brings(before_index):
    """BRING lines on the last BRING_WINDOW posts before this index, across every batch file."""
    seen = {}
    for f in os.listdir(os.path.join(ROOT, "batches")):
        if not f.endswith(".json") or not f.startswith("20"):
            continue
        try:
            for q in json.load(open(os.path.join(ROOT, "batches", f)))["posts"]:
                seen[str(q["index"])] = (q.get("bring") or "").strip().upper()
        except Exception:  # noqa: BLE001
            continue
    earlier = sorted(i for i in seen if i < str(before_index))[-BRING_WINDOW:]
    return [seen[i] for i in earlier if seen[i]]


def fill_brings(posts, batch_name, problems):
    """Every post gets a BRING line from the bank. The review chooses it; when a pick has none, an APPROVED
    line not used in the last 20 posts is filled in (rotating through the bank) and the batch says so."""
    if batch_name < BRING_FROM:
        return
    approved, every = bring_bank()
    for n, post in enumerate(posts):
        used = recent_brings(post["index"]) + [(q.get("bring") or "").strip().upper() for q in posts[:n]]
        used = used[-BRING_WINDOW:]
        b = (post.get("bring") or "").strip().upper()
        if b:
            if b not in every:
                problems.append(f"{post['index']}: BRING line not in build/bring_bank.txt: {b}")
            elif b in used:
                problems.append(f"{post['index']}: BRING line used within the last {BRING_WINDOW} posts: {b}")
            continue
        free = [l for l in approved if l not in used]
        if not free:
            problems.append(f"{post['index']}: no APPROVED BRING line left outside the last {BRING_WINDOW} posts")
            continue
        start = int(str(post["index"])) % len(free)
        post["bring"] = free[start]
        print(f"NOTE {post['index']} {post['place']}: no BRING in the pick, filled in: {post['bring']}")


def source_mix_problems(posts, batch_name):
    """The feed must not be a copy of anyone else's. Enforced here so a batch that breaks it cannot build."""
    out = []
    n = len(posts)
    if n == 0 or batch_name < "2026-W43":        # W42 was built before these rules existed
        return out
    def domain(u):
        return u.split("//")[-1].split("/")[0].replace("www.", "").lower()
    press = {}
    for p in posts:
        d = domain(p.get("source_url", ""))
        if any(d.endswith(x) for x in PRESS_DOMAINS):
            press[d] = press.get(d, 0) + 1
    for d, c in press.items():
        if c / n > MAX_PRESS_SHARE + 1e-9:
            out.append(f"source mix: {c} of {n} posts sourced from {d}; the cap is {int(MAX_PRESS_SHARE * 100)}% of a batch. "
                       f"Use the studio or photographer page as source_url, or swap the place.")
    if sum(press.values()) / n > 0.5:
        out.append(f"source mix: {sum(press.values())} of {n} posts come from design press articles; more than half. Lean on portfolio and archive sources.")
    seen_studio, seen_photog = {}, {}
    for p in posts:
        st = (p.get("designer") or "").strip().lower()
        ph = (p.get("credit") or "").strip().lower()
        if st:
            seen_studio[st] = seen_studio.get(st, 0) + 1
        if ph and not ph.startswith("courtesy") and not ph.startswith("licence"):
            seen_photog[ph] = seen_photog.get(ph, 0) + 1
    for k, c in seen_studio.items():
        if c > 1:
            out.append(f"source mix: studio '{k}' appears {c} times; one per batch.")
    # Lewie, 2026-11-02: an archive collection credit (e.g. Gottscho-Schleisner) may appear twice per batch
    archive_credits = {(p.get("credit") or "").strip().lower() for p in posts if p.get("archive")}
    for k, c in seen_photog.items():
        cap = 2 if k in archive_credits else 1
        if c > cap:
            out.append(f"source mix: photographer '{k}' appears {c} times; {cap} per batch.")
    not_news = sum(1 for p in posts if p.get("archive") or p.get("category", "").lower() == "archive"
                   or str(p.get("era", "")).strip() or p.get("published_year") and int(p["published_year"]) <= 2025)
    if not_news / n < MIN_NOT_NEWS_SHARE - 1e-9:
        out.append(f"source mix: only {not_news} of {n} posts are archive or older than a year; need {int(MIN_NOT_NEWS_SHARE * 100)}%. Add archive picks.")
    # `"country": "US"` on a pick settles it; the city list is the fallback (2026-11-09: The Greenbrier, West Virginia, was not counted)
    us = sum(1 for p in posts if str(p.get("country", "")).upper() in ("US", "USA")
             or any(c in (p.get("city", "") + " " + p.get("neighborhood", "")).lower() for c in US_CITIES))
    if us / n < MIN_US_SHARE - 1e-9:
        out.append(f"source mix: only {us} of {n} posts are US; the floor is {int(MIN_US_SHARE * 100)}% (target 80%).")
    return out


def main(batch_name):
    picks = json.load(open(os.path.join(HERE, "picks", batch_name + ".json")))
    harvest = json.load(open(os.path.join(HERE, "harvest", "shortlist.json")))
    posts, problems = [], []
    out_path = os.path.join(ROOT, "batches", batch_name + ".json")
    try:   # photos already in this batch file survive a regeneration when the shortlist no longer lists the page
        prior = {str(q["index"]): q.get("photos", []) for q in json.load(open(out_path))["posts"]}
    except Exception:  # noqa: BLE001
        prior = {}
    for p in picks["posts"]:
        photos, credits = photos_for(p["source_url"], harvest)
        if p.get("photos"):          # the review may hand-pick or reorder
            photos = p["photos"]
        elif not photos and prior.get(str(p["index"])):
            photos = prior[str(p["index"])]   # 2026-11-02: W42 lost 0007 and 0008's photos this way
        if len(photos) < 4:
            problems.append(f"{p['index']} {p['place']}: only {len(photos)} photos found")
        post = {k: p[k] for k in ("index", "place", "neighborhood", "city", "word", "reminds", "card", "credit",
                                  "alt_hint", "source_url") if k in p}
        for k in ("category", "designer", "designer_handle", "venue_handle", "photographer_handle",
                  "source_handle", "bring", "cover", "focal", "discovered_via", "archive", "era", "published_year", "licence", "country",
                  "drop_photos", "rotate", "edited_at"):
            if k in p:
                post[k] = p[k]
        post["photos"] = photos
        bad = validate_card(post)
        if bad:
            problems.append(f"{p['index']}: " + "; ".join(bad))
        posts.append(post)
    fill_brings(posts, batch_name, problems)
    problems += source_mix_problems(posts, batch_name)
    out = out_path
    json.dump({"batch": batch_name, "posts": posts}, open(out, "w"), indent=1, ensure_ascii=False)
    print(f"{len(posts)} posts -> {out}")
    for pr in problems:
        print("PROBLEM", pr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
