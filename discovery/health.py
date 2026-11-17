"""Job health: what each scheduled job produced, written where the daily heartbeat can read it.

A green run is not proof of output. On 2026-11-02 the archive search ran green for days and found nothing;
on 2026-11-09 the harvest ran green and saved nothing, and every Dezeen article arrived as a byline. Each
job now writes discovery/health/<job>.json when it finishes:

  {"job": ..., "at": UTC time, "ok": true/false, "stats": {...}, "problems": ["plain sentence", ...]}

The publisher's 7am heartbeat email (publisher.gs heartbeat_) reads these files and reports any job that is
late or has problems. `python discovery/health.py check <job>` exits 1 when the job recorded problems, so a
workflow can go red after its output is safely saved.
"""
import json
import os
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DIR = os.path.join(HERE, "health")


def write(job, stats, problems):
    os.makedirs(DIR, exist_ok=True)
    rec = {"job": job, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "ok": not problems,
           "stats": stats, "problems": problems}
    json.dump(rec, open(os.path.join(DIR, job + ".json"), "w"), indent=1, ensure_ascii=False)
    for p in problems:
        print(f"HEALTH PROBLEM ({job}): {p}")
    return rec


# ---------------------------------------------------------------- harvest
MIN_ITEMS = 60            # a normal Monday keeps about 200
MAX_FEED_ERROR_SHARE = 0.30
MAX_PAGE_ERROR_SHARE = 0.15
THIN_TEXT = 400           # characters; an article this short is a byline, not an article
PRESS = ("publication", "press", "magazine", "guide")


def harvest_health(items, sources):
    """items: the harvest's records. sources: sources.json's list. Returns (stats, problems)."""
    feeds = [s for s in sources if s.get("feed") and not s.get("disabled") and s.get("url")]
    feed_names = {s["name"] for s in feeds}
    kept = [r for r in items if "title" in r]
    feed_errors = [r["source"] for r in items if "error" in r and str(r["error"]).startswith("feed")]
    fetched = [r for r in kept if "text" in r or "page_error" in r]
    page_errors = [r for r in fetched if "page_error" in r]
    problems = []
    if len(kept) < MIN_ITEMS:
        problems.append(f"only {len(kept)} items kept (a normal week is about 200)")
    if feeds and len(feed_errors) / len(feeds) > MAX_FEED_ERROR_SHARE:
        problems.append(f"{len(feed_errors)} of {len(feeds)} feeds could not be read")
    if fetched and len(page_errors) / len(fetched) > MAX_PAGE_ERROR_SHARE:
        kinds = {}
        for r in page_errors:
            kinds[r["page_error"]] = kinds.get(r["page_error"], 0) + 1
        top = sorted(kinds.items(), key=lambda kv: -kv[1])[0]
        problems.append(f"{len(page_errors)} of {len(fetched)} article pages failed (most common: {top[0]}, {top[1]} times)")
    # per feed source with enough pages to judge: are the articles arriving with their words and photos?
    by = {}
    for r in kept:
        if r.get("source") in feed_names and "text" in r:
            by.setdefault(r["source"], []).append(r)
    thin, per_source = [], {}
    for name, rs in sorted(by.items()):
        text = int(statistics.median(len(r.get("text", "")) for r in rs))
        photos = int(statistics.median(len(r.get("photos", [])) for r in rs))
        per_source[name] = {"pages": len(rs), "median_text": text, "median_photos": photos}
        if len(rs) >= 5 and text < THIN_TEXT:
            thin.append(f"{name} ({text} characters)")
    if thin:
        problems.append("article text is not coming through for: " + ", ".join(thin) +
                        ". Cards for these places have no facts to draw on; the text extraction in harvest.py needs a look.")
    stats = {"items": len(kept), "pages_fetched": len(fetched), "page_errors": len(page_errors),
             "feeds": len(feeds), "feed_errors": sorted(set(feed_errors)), "with_4_photos": sum(1 for r in kept if len(r.get("photos", [])) >= 4),
             "sources": per_source}
    return stats, problems


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "check":
        path = os.path.join(DIR, sys.argv[2] + ".json")
        try:
            rec = json.load(open(path))
        except Exception as e:  # noqa: BLE001
            print(f"::error::no health record for {sys.argv[2]} ({e})")
            return 1
        for p in rec.get("problems", []):
            print(f"::error::{sys.argv[2]}: {p}")
        return 1 if rec.get("problems") else 0
    if len(sys.argv) == 3 and sys.argv[1] == "harvest":    # re-check a saved harvest file by hand
        items = json.load(open(sys.argv[2]))["items"]
        sources = json.load(open(os.path.join(HERE, "sources.json")))["sources"]
        stats, problems = harvest_health(items, sources)
        print(json.dumps({"stats": {k: v for k, v in stats.items() if k != "sources"}, "problems": problems}, indent=1))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
