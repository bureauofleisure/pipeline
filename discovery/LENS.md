# The Bureau of Leisure lens

What the weekly discovery job applies to every candidate before anything reaches the sheet.

## What we feature
Beautifully designed places: architecture, interiors and design across hospitality first (hotels, bars,
restaurants, cafes, clubs, baths, pools), then homes, gardens and beyond. Leaning new but nostalgic.
"New" means new to most people, so a place published in 2016 counts. Roughly half the feed is classics
(see "The classics half"). 80% US (New York, then Austin, Miami, Los Angeles), 20% abroad.

## The classics half (Lewie, 2026-11-09)
The older half of the feed is defined by the age of the place, not the age of the photograph.
- A classic is a place that has been open about 25 years or more and is open today, with the rooms it is
  known for intact. The Polo Lounge, Musso and Frank, Bemelmans, the Greenbrier, a 1950s diner still trading.
- Its photographs can come from any professional source that passes the gate, in this order of preference:
  1. current photographs: the venue's own press or gallery page, the portfolio of the photographer who shot
     it, the studio that restored it, or design press coverage of it;
  2. a professional archive (Library of Congress: Gottscho-Schleisner, Highsmith, HABS).
  The Library of Congress is one source for the classics half, not the source. In three weeks of searching it
  gave two usable places (The Dunes Club, The Greenbrier); most of its hospitality sets are one to three
  frames or show rooms that are gone.
- Current photographs of a place still trading show rooms that exist, so the survival rule is met by the
  photographs themselves. Photographs more than ten years old, from any source, still need the room by room
  check (rule of 2026-11-02 below).
- Pick fields: `era` is the year or decade the place opened and the card states it; that is what counts a
  post toward the classics share. `archive: true` is only for photographs from an archive collection (it
  turns on the film-edge trim and the two-per-collection allowance). A classic shot by a living photographer
  carries `era` and a normal `credit`, and the one-per-photographer rule applies as usual.
- `discovery/archive_targets.json` is the classics list. For each place look for current photographs first
  and the archives second. Add 3 to 5 places a week, chosen because good current photographs exist.
- Everything else holds: no outreach, public originals only, every photo viewed, no weak post to fill a share.

## Score, out of 25. Under 17 is dropped before anyone sees it.
- Design quality /5: would a demanding interiors editor run it?
- Era feel /5: does it belong to another decade, or feel like it could?
- New to most /5: how few of our readers already know it.
- Shareability /5: would someone send it to a named friend?
- Photo access /5: are there 4 to 6 full-size, sharp, credited originals from a tier 1 source?

## Hard exclusions
- Renders, visualisations, concepts, unbuilt or "coming soon" projects.
- AI-generated or AI-altered images (see below).
- Chains and franchises, unless a single location is a genuine design piece.
- Logos, signage or branding as the subject of the shot.
- Places that have closed.
- Stock photography, lifestyle shots with models, food close-ups. The subject is the room.
- Beds. No bedrooms, no guest rooms, no suites, ever. A hotel is shown through its lobby, bar, restaurant, pool,
  corridor, bathhouse, terrace. If a hotel's only good photos are of beds, it is not a post.
- Watermarked images.
- Any photo under 1080 x 1350 after the 4:5 crop. Nothing is ever enlarged.

## AI-generated content: the test
The feed is full of invented hotel lobbies. A candidate passes only if ALL of these hold:
1. The venue verifiably exists: a street address, a working website or booking page, and at least one
   independent mention (press, maps listing, the designer's portfolio). No address, no post.
2. The photos come from a tier 1 source (the studio, the photographer, a press room) or a design press
   article that names the studio and the photographer. An image with no provenance is treated as AI.
3. Text signals: the page does not say render, visualisation, concept, AI, Midjourney, generated,
   imagined, proposed, speculative.
4. Visual signals, checked by the reviewing model: no impossible geometry, no melted or nonsense text,
   no perfectly symmetrical "too clean" rooms, no duplicated objects, no light sources that do not match
   the shadows. Any one of these benches the post with the reason recorded.
5. Where the file carries content credentials (C2PA) or EXIF data, read them. A generator's signature is
   an automatic exclusion. A camera make is a point in favour, never proof on its own.
Anything that fails is benched with "possible AI or render" so the pattern can be reviewed.

## Credits
- Photographer named on every slide when the source names one. If no name exists anywhere, the slides carry
  no credit line at all and the caption ends with "DM for credit." Credits are added later when they turn up.
- Designer named when known. Venue handle always.
- Every photo's source URL is recorded in the batch file, so there is always an answer to "where did this come from".

## Copy
- The card: 7 lines, all caps, facts only, ends on IN A WORD from the bank. No word reused within 20 posts.
- REMINDS US OF: one specific, nameable reference, six words max.
- BRING: taken word for word from build/bring_bank.txt (APPROVED, or RESERVED/CONDITIONAL when the condition holds). Never written fresh. No line repeats within 20 posts. It prints on the index card slide and in the caption, on its own line just above IN A WORD (the build adds it; the card in the pick stays 7 lines). A line not in the bank is left off and noted.
- One place per post. Never a listicle.

## Source mix: the feed is nobody's copy
Enforced by make_batch.py; a batch that breaks these does not build.
- No single publication supplies more than 25% of a batch, and design press as a whole never more than half.
  Press is where we hear about a place; the studio, photographer, venue or archive page is where the photos and
  the source_url come from. Record the article as `discovered_via`.
- One post per studio and one per photographer in any batch.
  2026-11-02 (Lewie): an archive collection credit (for example Gottscho-Schleisner, Library of Congress) may appear
  in up to two posts per batch. Built in: make_batch.py.
- At least 40% of a batch is a classic (`era` set), an archive post, or published more than a year ago ("new" means new to most, not new this week).
- US floor 60%, target 80%. New York first, then Austin, Miami, Los Angeles.
- We take at most two of the fifteen or so hospitality interiors the press runs each week, chosen on era and
  shareability, which are not their criteria. Same raw material, different eye, different photos, different words.

## The bank and the batch (Lewie, 2026-11-09)
- Nothing posts publicly until 30 posts are banked (approved, or held from a dry run). The publisher enforces
  it (LAUNCH_BANK) and the morning heartbeat email reports the count.
- Launch cadence is four posts a week (Sunday, Monday, Wednesday, Friday), rising to daily and then three a
  day only when the bank keeps pace. One post a day needs seven approvals a week; about a third of built posts
  are killed, so that is ten or more built.
- Until the bank holds 30, a weekly batch aims for 10 to 12 built posts. A batch of four does not move the
  bank. If the week cannot produce ten that meet the lens, say so in the report with the reason (too few
  candidates, photos failing the gate, source mix) so the bottleneck is known; never pad.

## Kill reasons (from 2026-11-09)
Every kill on the review page carries one reason, stored in `discovery/statuses.json` as `kill_reason`
("CODE @time: optional note"):
- PHOTOS: the photographs are not good enough. The place may return with better ones (watch list).
- PLACE: the place is not special enough. Do not bring it back; raise the bar on design quality.
- ERA: no nostalgia, wrong feel. Score era feel harder on similar places.
- KNOWN: too well known. Score new-to-most harder; skip the obvious name in a city.
- GONE: closed, or changed since the photographs.
- OTHER: read the note.
The weekly review reads every kill reason before scoring, counts them over the last eight weeks, and says in
its report which reason leads and what it changed in response. A reason that repeats three times is a
pattern: the edits job writes it up as a rule under "Learned from edits". Kills made before 2026-11-09 carry
no reason; read their notes instead.

## Straight lines
Wide-angle photographs are welcome. The grade separates true rotation (the camera was tilted sideways) from
wide-angle lean (walls converging because the camera pointed up or the lens is wide). Rotation up to 8 degrees
is corrected automatically; beyond 8 the photo is held. Lean up to 3 degrees is gently corrected; stronger lean
is left as shot and never benched. Anything that still looks off is caught on the review page and sent back
with an Edits note.

## Edits
A reviewer can send a post back with a note instead of killing it (Status EDITS). The daily edits job reads the
note, translates it into pick fields (drop_photos, rotate, cover, word, card lines, photos list), sets edited_at,
rebuilds, and the post returns to DRAFT marked "Edited" for a second look. Small tilts and keystone are corrected
automatically in the build; an edit note about a crooked slide usually means drop that photo or rotate it by hand.

## Photo order (from 2026-10-30)
Lewie reorders photos by dragging them on the review page. Every reorder is saved to
`batches/orders/photo_order.json`: `orders` is what the build applies (source photo numbers, cover first),
`history` records each change with the order it replaced. Treat history as taste: before naming a `cover` or
an order for a new pick, read it and look at which photo Lewie moved to the front and what he pushed back.
Pick the cover he would. Never edit or remove his saved orders; the build ignores one on its own if the
photo list it was made against changes.

## Source yield
Every source gets a running yield: candidates found, candidates that built, candidates approved.
Sources that keep producing rejects are checked less often. Sources that keep producing approvals are
mined deeper into their archive.

## Learned from edits
Every Edits note and every kill reason is feedback on the system, not only on one post. The daily edits job
turns each note into a general rule and adds it here, dated, with the post it came from. The weekly review
and the edits job read this section every run and obey it as strictly as the rest of the lens. Rules that a
machine can check are also built into the code (named below) so they hold without anyone remembering them.
Lewie can strike a rule by saying so; nothing here is removed otherwise.

- 2026-10-21 (Nota Tavern 0005, Cassette 0010, Hommali 0003): no two slides of the same view. A photo that repeats
  another from a closer or further position, or a slight reframe, is a replicate: keep the stronger one. Each
  slide must show something the others do not (a different room, angle, detail or time of day). When picking
  photos, prefer sets that move through the place. Built in: build_posts.py same-view check (ORB match,
  100+ homography inliers = replicate, the later photo is dropped).
- 2026-10-21 (all Commons archive posts killed): archive photographs come only from professional collections
  made with proper cameras (Library of Congress: Gottscho-Schleisner, Carol M. Highsmith, HABS). Never
  Wikimedia Commons, open Flickr or any user-upload site, whatever the licence. Built in: archive_search.py
  searches only those collections.
- 2026-10-21 (Minnibar 0011): every slide shows the place as somewhere to be: a room, a bar, seats, a table in
  its setting. Never a slide that is only a wall, a light fixture, a texture or an empty corner. Details earn a
  slide only when the room is still readable around them.
- 2026-10-21 (TWA Hotel 0014): a great venue with weak photos is not a post. If the available photos are
  snapshots, or mostly floor, carpet, concrete or empty space, the place waits on the watch list until
  professional, composed photographs are found (studio, photographer, venue press kit or a professional
  archive). Never fill a strong place with whatever photos exist.
- 2026-10-23 (TWA Hotel 0014, second pass): no photo goes into a pick unseen. Whoever picks photos opens every
  one and judges it as a slide before listing it; a file title, an author name or a collection name is not a
  look. If the photos cannot be fetched and viewed in the run, the place waits for a run that can see them.
  (The 0014 set was chosen by Commons title and never viewed, which is how eleven frames of floor, carpet and
  concrete reached the review page.)
- 2026-10-27 (Peridot 0009, Cassette 0010): a post carries only the slides of its latest build, and the review
  page and the publisher see exactly those slides. When a rebuild shortens a post (a replicate dropped, a photo
  held), the slide files it no longer uses must go and the sheet's slide links must be refreshed even on rows
  already approved; otherwise the previous card stays on as an extra last slide and the card shows twice. Built in
  (2026-10-30): build_posts.py clears old slide files and versions slide links; importBatch refreshes links on every open row.
- 2026-11-02 (TWA Flight Center 0023): an archive post shows rooms that still exist and can be visited as photographed. If the place was gutted, rebuilt or redesigned since the photographs (TWA's 1962 Lisbon Lounge and London Club are gone; the terminal is now the TWA Hotel), the archive photos describe a place that is closed and the post does not make the cut. Before picking an archive set, confirm from a source that the rooms shown survive today, room by room.
