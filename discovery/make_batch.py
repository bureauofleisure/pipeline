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
                  "source_handle", "bring", "cover", "focal"):
            if k in p:
                post[k] = p[k]
        post["photos"] = photos
        bad = validate_card(post)
        if bad:
            problems.append(f"{p['index']}: " + "; ".join(bad))
        posts.append(post)
    out = os.path.join(ROOT, "batches", batch_name + ".json")
    json.dump({"batch": batch_name, "posts": posts}, open(out, "w"), indent=1, ensure_ascii=False)
    print(f"{len(posts)} posts -> {out}")
    for pr in problems:
        print("PROBLEM", pr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
