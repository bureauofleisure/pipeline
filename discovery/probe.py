"""Try each candidate address for the broken sources and report which ones answer.
Runs on GitHub Actions. Writes discovery/probe_report.json. Changes nothing itself."""
import json
import os
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


def probe(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            body = r.read(200_000)
            kind = "feed" if b"<rss" in body[:3000] or b"<feed" in body[:3000] else "html"
            return {"status": r.status, "final": r.geturl(), "kind": kind, "bytes": len(body)}
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {str(e)[:80]}"}


def main():
    cands = json.load(open(os.path.join(HERE, "probe_candidates.json")))
    out = {}
    for name, urls in cands.items():
        out[name] = [{"url": u, **probe(u)} for u in urls]
        time.sleep(0.3)
    json.dump({"probed": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "results": out},
              open(os.path.join(HERE, "probe_report.json"), "w"), indent=1)
    for name, rows in out.items():
        ok = [r for r in rows if "status" in r]
        print(name, "->", (ok[0]["final"] + " " + ok[0]["kind"]) if ok else "NOTHING ANSWERED")


if __name__ == "__main__":
    main()
