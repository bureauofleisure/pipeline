"""Check every source in sources.json: is the site reachable, does the feed parse, how many items.
Writes discovery/feed_report.json and prints a summary. Runs on GitHub Actions, where there are no
permission prompts and the network is open. A broken source is reported, never silently skipped."""
import json
import os
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
UA = "Mozilla/5.0 (compatible; BureauOfLeisureBot/1.0; +https://bureauofleisure.co)"


def get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read(400_000)


def feed_items(body):
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        # lenient: strip to the first well-formed item titles
        import re
        return len(re.findall(rb"<title>", body)) - 1, "lenient"
    items = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")
    return len(items), "ok"


def main():
    src = json.load(open(os.path.join(HERE, "sources.json")))["sources"]
    report = []
    for s in src:
        row = {"name": s["name"], "tier": s["tier"], "category": s["category"], "site": None, "feed": None, "items": 0}
        try:
            code, _ = get(s["url"])
            row["site"] = code
        except Exception as e:  # noqa: BLE001
            row["site"] = f"error: {type(e).__name__}"
        if s.get("feed"):
            try:
                code, body = get(s["feed"])
                n, how = feed_items(body)
                row["feed"] = f"{code} {how}"
                row["items"] = n
            except Exception as e:  # noqa: BLE001
                row["feed"] = f"error: {type(e).__name__}"
        report.append(row)
        time.sleep(0.5)
    json.dump({"checked": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "sources": report},
              open(os.path.join(HERE, "feed_report.json"), "w"), indent=2)
    bad = [r for r in report if (isinstance(r["site"], str) and r["site"].startswith("error")) or
           (r["feed"] and str(r["feed"]).startswith("error"))]
    print(f"{len(report)} sources checked, {len(bad)} with problems")
    for r in bad:
        print("  PROBLEM", r["name"], r["site"], r["feed"])
    sys.exit(0)


if __name__ == "__main__":
    main()
