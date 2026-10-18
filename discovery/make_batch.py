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
             "houston", "dallas", "denver", "washington", "las vegas", "santa fe", "marfa", "hudson", "montauk", "catskills")


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
    for k, c in seen_photog.items():
        if c > 1:
            out.append(f"source mix: photographer '{k}' appears {c} times; one per batch.")
    not_news = sum(1 for p in posts if p.get("archive") or p.get("category", "").lower() == "archive"
                   or str(p.get("era", "")).strip() or p.get("published_year") and int(p["published_year"]) <= 2025)
    if not_news / n < MIN_NOT_NEWS_SHARE - 1e-9:
        out.append(f"source mix: only {not_news} of {n} posts are archive or older than a year; need {int(MIN_NOT_NEWS_SHARE * 100)}%. Add archive picks.")
    us = sum(1 for p in posts if any(c in (p.get("city", "") + " " + p.get("neighborhood", "")).lower() for c in US_CITIES))
    if us / n < MIN_US_SHARE - 1e-9:
        out.append(f"source mix: only {us} of {n} posts are US; the floor is {int(MIN_US_SHARE * 100)}% (target 80%).")
    return out


def main(batch_name):
    picks = json.load(open(os.path.join(HERE, "picks", batch_name + ".json")))
    harvest = json.load(open(os.path.join(HERE, "harvest", "shortlist.json")))
    posts, problems = [], []
    for p in picks["posts"]:
        photos, credits = photos_for(p["source_url"], harvest)
        if p.get("photos"):          # the review may hand-pick or reorder
            photos = p["photos"]
        if len(photos) < 4:
            problems.append(f"{p['index']} {p['place']}: only {len(photos)} photos found")
        post = {k: p[k] for k in ("index", "place", "neighborhood", "city", "word", "reminds", "card", "credit",
                                  "alt_hint", "source_url") if k in p}
        for k in ("category", "designer", "designer_handle", "venue_handle", "photographer_handle",
                  "source_handle", "bring", "cover", "focal", "discovered_via", "archive", "era", "published_year", "licence",
                  "drop_photos", "rotate", "edited_at"):
            if k in p:
                post[k] = p[k]
        post["photos"] = photos
        bad = validate_card(post)
        if bad:
            problems.append(f"{p['index']}: " + "; ".join(bad))
        posts.append(post)
    problems += source_mix_problems(posts, batch_name)
    out = os.path.join(ROOT, "batches", batch_name + ".json")
    json.dump({"batch": batch_name, "posts": posts}, open(out, "w"), indent=1, ensure_ascii=False)
    print(f"{len(posts)} posts -> {out}")
    for pr in problems:
        print("PROBLEM", pr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
