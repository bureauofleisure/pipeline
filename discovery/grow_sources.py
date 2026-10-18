"""Grow the source list from the credits the harvest finds.

Every photographer credited and every studio named in a harvested article is a tier-1 source we do not
have yet. This job resolves each name to a website, verifies the site is really theirs (the name appears
on the page), works out what platform it runs on (which tells the harvester how to get the full-size
image), and adds it to sources.json. Unresolved names are kept in roster.json so they are not retried
every week and a person can fill in a domain by hand if they ever want to.

Runs on GitHub Actions after the weekly harvest. Changes nothing a person decided: names already in
sources.json (by hand or by this job) are left alone.
"""
import glob
import json
import os
import re
import time
import unicodedata
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
MAX_NEW_PER_RUN = int(os.environ.get("GROW_MAX", "40"))

# words that mean a "credit" is not a person or studio
NOT_A_NAME = re.compile(r"\b(courtesy|supplied|dezeen|unless|stated|the|hotel|hotels|resort|group|hospitality|"
                        r"press|instagram|getty|shutterstock|alamy|unsplash|stock|inc|llc|ltd|corporation|"
                        r"architects? and|design and|photo|photos|photography|images?)\b", re.I)
PLATFORMS = (("squarespace", "squarespace"), ("format.com", "format"), ("cargo.site", "cargo"), ("cargocollective", "cargo"),
             ("adobe portfolio", "adobe-portfolio"), ("myportfolio", "adobe-portfolio"), ("wp-content", "wordpress"),
             ("wixstatic", "wix"), ("pixieset", "pixieset"), ("photoshelter", "photoshelter"), ("webflow", "webflow"),
             ("semplice", "semplice"), ("readymag", "readymag"))


NATIONALITY = {"british","italian","spanish","french","swedish","danish","japanese","mexican","canadian","american",
               "australian","chilean","dutch","german","portuguese","brazilian","norwegian","finnish","belgian","swiss",
               "austrian","greek","turkish","korean","chinese","indian","local","london","toronto","paris","milan",
               "melbourne","sydney","copenhagen","berlin","madrid","barcelona","lisbon","tokyo","mexico","brooklyn"}
DESCRIPTOR = {"architect","architects","studio","practice","firm","designer","designers","photographer","architecture",
              "interior","interiors","design","based","and","hospitality","collective"}
GENERIC = re.compile(r"^(?:a |an |the )?(?:design|designer|architect|studio|practice|thoughtful design|designers and studios)s?$", re.I)


def clean(name):
    """'Toronto-based Studio Paolo Ferrari' -> 'Studio Paolo Ferrari'; 'Swedish studio Stylt Trampoli' -> 'Stylt Trampoli';
    'Hoshino Resorts 10' -> 'Hoshino Resorts'. Lower-case descriptor words are stripped; capitalised ones are the name."""
    n = re.sub(r"\s+\d+$", "", name.strip(" ."))
    n = re.sub(r"\s+(?:It|On|At|In|The|And)$", "", n)
    words = n.split()
    while words:
        w = words[0]
        bare = w.lower().rstrip(",")
        if bare.endswith("-based") or bare in NATIONALITY or bare in ("a", "an", "the") or (w[0].islower() and bare in DESCRIPTOR):
            words.pop(0)
            continue
        # "New York-based" / "South Florida-based": two words ending in -based
        if len(words) > 1 and words[1].lower().endswith("-based"):
            words = words[2:]
            continue
        break
    return " ".join(words).strip()


def slug(name):
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9 ]", "", s.lower()).strip()
    return s


def candidates(name, kind):
    """Plausible domains for a person or a studio, most likely first."""
    s = slug(name)
    parts = s.split()
    if not parts:
        return []
    joined = "".join(parts)
    dashed = "-".join(parts)
    out = []
    for host in (joined, dashed):
        for tld in (".com", ".studio", ".co", ".net", ".design", ".nyc", ".co.uk", ".ca", ".it", ".fr", ".es", ".de", ".mx", ".com.au"):
            out.append(f"https://www.{host}{tld}/")
            out.append(f"https://{host}{tld}/")
    if kind == "photographer":
        out = [f"https://www.{joined}photo.com/", f"https://www.{joined}photography.com/", f"https://{joined}.photo/"] + out
    else:
        if "studio" not in s:
            out = [f"https://www.{joined}studio.com/", f"https://www.studio{joined}.com/", f"https://{joined}.studio/"] + out
    # keep it bounded
    return out[:24]


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,*/*"})
    with urllib.request.urlopen(req, timeout=12) as r:
        return r.geturl(), r.read(300_000).decode("utf-8", "replace")


def verify(name, body):
    """The page must mention the name (surname at least) in its title, meta or first visible text."""
    low = body.lower()
    parts = slug(name).split()
    surname = parts[-1] if parts else ""
    head = low[:60_000]
    return bool(surname) and len(surname) > 2 and surname in head


def platform(body):
    low = body.lower()
    for needle, label in PLATFORMS:
        if needle in low:
            return label
    return "unknown"


def resolve(name, kind):
    for url in candidates(name, kind):
        try:
            final, body = fetch(url)
        except Exception:  # noqa: BLE001
            continue
        if verify(name, body):
            return {"url": final, "platform": platform(body)}
        time.sleep(0.2)
    return None


def collect_names():
    """Credited photographers and named studios across every harvest file, with how often each appears."""
    photographers, studios = {}, {}
    for path in glob.glob(os.path.join(HERE, "harvest", "*.json")):
        try:
            items = json.load(open(path)).get("items", [])
        except Exception:  # noqa: BLE001
            continue
        for it in items:
            for c in it.get("credits") or []:
                c = clean(c)
                if 5 <= len(c) <= 40 and 2 <= len(c.split()) <= 3 and not NOT_A_NAME.search(c) and not GENERIC.match(c):
                    photographers[c] = photographers.get(c, 0) + 1
            for d in it.get("designers") or []:
                d = clean(d)
                if 4 <= len(d) <= 40 and len(d.split()) <= 4 and not GENERIC.match(d) \
                        and re.search(r"\b(studio|architects?|design|atelier|office|workshop|partners|associates)\b", d, re.I) \
                        and not NOT_A_NAME.search(d.replace("Design", "").replace("Architects", "")):
                    studios[d] = studios.get(d, 0) + 1
    # the reviewed pick files carry verified names: count them double
    for path in glob.glob(os.path.join(HERE, "picks", "*.json")):
        try:
            posts = json.load(open(path)).get("posts", [])
        except Exception:  # noqa: BLE001
            continue
        for p in posts:
            c = clean(p.get("credit") or "")
            if c and not c.lower().startswith("courtesy") and 2 <= len(c.split()) <= 3:
                photographers[c] = photographers.get(c, 0) + 2
            for d in re.split(r"\s+(?:with|and)\s+", p.get("designer") or ""):
                d = clean(d)
                if d and len(d.split()) <= 4 and not GENERIC.match(d):
                    studios[d] = studios.get(d, 0) + 2
    return photographers, studios


def main():
    src_path = os.path.join(HERE, "sources.json")
    data = json.load(open(src_path))
    known = {slug(s["name"]) for s in data["sources"]}
    roster_path = os.path.join(HERE, "roster.json")
    roster = json.load(open(roster_path)) if os.path.exists(roster_path) else {"photographers": {}, "studios": {}}

    photographers, studios = collect_names()
    added, tried = [], 0
    for kind, names, bucket in (("photographer", photographers, "photographers"), ("studio", studios, "studios")):
        for name, count in sorted(names.items(), key=lambda kv: -kv[1]):
            key = slug(name)
            if key in known or key in roster[bucket]:
                # already a source, or already tried: just keep the count fresh
                if key in roster[bucket]:
                    roster[bucket][key]["seen"] = count
                continue
            if tried >= MAX_NEW_PER_RUN:
                break
            tried += 1
            hit = resolve(name, kind)
            roster[bucket][key] = {"name": name, "seen": count, "resolved": bool(hit),
                                   "url": hit["url"] if hit else None, "platform": hit["platform"] if hit else None,
                                   "checked": time.strftime("%Y-%m-%d")}
            if hit:
                data["sources"].append({"name": name, "category": kind, "tier": 1, "region": "global",
                                        "url": hit["url"], "feed": None, "archive": True, "auto": True,
                                        "platform": hit["platform"],
                                        "notes": f"Added automatically from {count} credit(s) in the harvest, {time.strftime('%Y-%m-%d')}."})
                known.add(key)
                added.append(f"{name} -> {hit['url']} ({hit['platform']})")
    json.dump(data, open(src_path, "w"), indent=1, ensure_ascii=False)
    json.dump(roster, open(roster_path, "w"), indent=1, ensure_ascii=False)
    print(f"{len(photographers)} photographers and {len(studios)} studios seen; {tried} looked up; {len(added)} added")
    for a in added:
        print("  +", a)
    unresolved = [v["name"] for b in roster.values() for v in b.values() if not v["resolved"]]
    print(f"{len(unresolved)} unresolved (kept in roster.json): {', '.join(unresolved[:15])}")


if __name__ == "__main__":
    main()
