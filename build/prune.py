"""Delete hosted posts built more than N days ago. Instagram keeps its own copy once posted,
and GitHub Pages has a 1 GB site limit, so old slides must not pile up."""
import glob, json, os, shutil, sys, time
days = int(sys.argv[1]) if len(sys.argv) > 1 else 120
root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "posts")
cutoff = time.time() - days * 86400
for pj in glob.glob(os.path.join(root, "*", "post.json")):
    built = json.load(open(pj)).get("built")
    if built and time.mktime(time.strptime(built, "%Y-%m-%d")) < cutoff:
        shutil.rmtree(os.path.dirname(pj))
        print("pruned", os.path.basename(os.path.dirname(pj)))
