"""The originals archive.

Every photograph that passes the quality gate is kept, untouched, at the highest resolution the source
served, in an S3-compatible bucket (Cloudflare R2 by default: no egress fees, first 10 GB free). The 4:5
Instagram slides are derivatives; the originals are what a book, a PDF or a print would be made from.

Layout in the bucket:
  originals/<index>/<index>-<nn>.<ext>   the untouched source file, nn = position in the batch (1-based)
  originals/<index>/<index>.json         sidecar: place, city, source article, credit, licence, and per photo
                                         the source URL, pixel size, byte size, sha1, whether it made a slide
  originals/index.jsonl                  one line per post, appended, so the whole archive can be listed
                                         without walking the bucket

Configuration (GitHub Actions secrets, read as environment variables):
  ARCHIVE_ENDPOINT   e.g. https://<accountid>.r2.cloudflarestorage.com
  ARCHIVE_BUCKET     e.g. bureau-originals
  ARCHIVE_KEY_ID / ARCHIVE_SECRET
When any is missing the build still runs and records archive: "not configured" on the post, so nothing is
silently lost and the gap is visible in the manifest.
"""
import hashlib
import io
import json
import os
import time

ENV = {k: os.environ.get(k, "") for k in ("ARCHIVE_ENDPOINT", "ARCHIVE_BUCKET", "ARCHIVE_KEY_ID", "ARCHIVE_SECRET")}


def configured():
    return all(ENV.values())


def _client():
    import boto3  # imported lazily so the build runs without it when the archive is not configured
    return boto3.client("s3", endpoint_url=ENV["ARCHIVE_ENDPOINT"], aws_access_key_id=ENV["ARCHIVE_KEY_ID"],
                        aws_secret_access_key=ENV["ARCHIVE_SECRET"], region_name="auto")


def _ext(content_type, url):
    if "png" in content_type or url.lower().split("?")[0].endswith(".png"):
        return "png"
    if "tif" in content_type or url.lower().split("?")[0].endswith((".tif", ".tiff")):
        return "tif"
    if "webp" in content_type or url.lower().split("?")[0].endswith(".webp"):
        return "webp"
    return "jpg"


def store(index, meta, photos):
    """photos: list of dicts {n, url, bytes, content_type, width, height, used_as_slide}.
    Returns a short status string for the manifest. Never raises: an archive failure must not stop a post."""
    if not configured():
        return "not configured"
    try:
        s3 = _client()
        bucket = ENV["ARCHIVE_BUCKET"]
        existing = set()
        for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=f"originals/{index}/"):
            for o in page.get("Contents", []):
                existing.add(o["Key"])
        side = {"index": index, "stored": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **meta, "photos": []}
        uploaded = 0
        for ph in photos:
            ext = _ext(ph.get("content_type", ""), ph["url"])
            key = f"originals/{index}/{index}-{ph['n']:02d}.{ext}"
            sha = hashlib.sha1(ph["bytes"]).hexdigest()
            if key not in existing:
                s3.put_object(Bucket=bucket, Key=key, Body=ph["bytes"],
                              ContentType=ph.get("content_type") or f"image/{'jpeg' if ext == 'jpg' else ext}",
                              Metadata={"source": ph["url"][:1000], "sha1": sha, "index": index})
                uploaded += 1
            side["photos"].append({"file": key.rsplit("/", 1)[-1], "source": ph["url"], "width": ph["width"], "height": ph["height"],
                                   "bytes": len(ph["bytes"]), "sha1": sha, "used_as_slide": ph.get("used_as_slide", False)})
        s3.put_object(Bucket=bucket, Key=f"originals/{index}/{index}.json", Body=json.dumps(side, indent=1, ensure_ascii=False).encode(),
                      ContentType="application/json")
        # append to the running index
        line = json.dumps({"index": index, "place": meta.get("place"), "city": meta.get("city"), "photos": len(side["photos"]),
                           "bytes": sum(p["bytes"] for p in side["photos"]), "stored": side["stored"]}, ensure_ascii=False)
        try:
            cur = s3.get_object(Bucket=bucket, Key="originals/index.jsonl")["Body"].read().decode()
        except Exception:  # noqa: BLE001
            cur = ""
        if f'"index": "{index}"' not in cur:
            s3.put_object(Bucket=bucket, Key="originals/index.jsonl", Body=(cur + line + "\n").encode(), ContentType="application/x-ndjson")
        mb = sum(len(p["bytes"]) for p in photos) / 1e6
        return f"{len(photos)} originals ({mb:.1f} MB), {uploaded} new"
    except Exception as e:  # noqa: BLE001
        return f"archive failed: {type(e).__name__}: {str(e)[:120]}"


def delete(index):
    """Remove a post's originals (used by the janitor for KILLED posts)."""
    if not configured():
        return "not configured"
    s3 = _client()
    bucket = ENV["ARCHIVE_BUCKET"]
    keys = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=f"originals/{index}/"):
        keys += [{"Key": o["Key"]} for o in page.get("Contents", [])]
    if keys:
        s3.delete_objects(Bucket=bucket, Delete={"Objects": keys})
    return f"deleted {len(keys)} objects"
