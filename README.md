# Bureau of Leisure pipeline

An Instagram index of beautifully designed places, run almost entirely by machines. One person approves.

## The loop, every week
1. **Monday 8:17am NY, GitHub Actions: `Check sources`.** Pings all 98 sources, writes `discovery/feed_report.json`.
2. **Monday 8:23am NY, GitHub Actions: `Harvest sources`.** Pulls every feed, keeps hospitality items, fetches each article,
   extracts the article's own photos (largest versions), credits and render warnings. Writes `discovery/harvest/<date>.json`.
3. **Monday 9:10am NY, Claude scheduled task: `Bureau of Leisure weekly review`.** Reads `discovery/LENS.md`, scores the
   harvest, shortlists, re-harvests the shortlist (full galleries + credits), writes `discovery/picks/<week>.json` with the
   cards, runs `discovery/make_batch.py`, pushes `batches/<week>.json`, waits for the build, emails Lewie the result.
4. **On push, GitHub Actions: `Build posts`.** Fetches photos, quality gate (no enlarging, min 1080x1350 after crop, sharpness,
   blockiness), near-duplicate drop, Grade v1.0, set matching, carousel v2 layout, publishes slides + `manifest.json` to the
   `gh-pages` branch. Prunes posts older than 120 days.
5. **Every 6 hours, Apps Script `importBatch`.** Reads the manifest, adds new posts to the Posts tab as DRAFT with every column
   filled, refreshes DRAFT rows if a rebuild changed them, lists benched posts in Candidates, emails when new rows arrive.
6. **Lewie** opens the sheet, looks at the slides, sets Status to APPROVED (or KILLED). That is the only human step.
7. **Daily 7am, Apps Script `dailyCheck`.** Gives approved rows the next free slot, alerts if the queue is thin or stalled.
8. **Hourly, Apps Script `publishDue`.** Posts the next approved carousel through the Instagram API. Weekly token refresh.

## Where things live
- `discovery/sources.json` the source list (tiers, categories, feeds). `disabled: true` keeps a dead one on record.
- `discovery/LENS.md` the rulebook the weekly review applies.
- `discovery/harvest.py`, `check_feeds.py`, `probe.py` the Actions scripts.
- `discovery/picks/` what the review chose and why. `batches/` what the build consumes.
- `build/` the build, carousel layout, word bank, BRING bank, the public link page (`site/`).
- `grade/` Grade v1.0, locked.
- `brand/` the profile seal (`avatar.py`).
- `publisher.gs` the Apps Script bound to the Google Sheet (v0.5).

## Settings that must be true
- GitHub Pages: source branch `gh-pages`, folder `/ (root)`.
- Apps Script: timezone America/New_York; Script Properties IG_USER_ID, IG_TOKEN, GRAPH_HOST, optional POST_HOUR, ALERT_EMAIL, MANIFEST_URL.
- Instagram: professional account, app with instagram_business_basic + instagram_business_content_publish.
