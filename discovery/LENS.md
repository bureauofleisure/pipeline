# The Bureau of Leisure lens

What the weekly discovery job applies to every candidate before anything reaches the sheet.

## What we feature
Beautifully designed places: architecture, interiors and design across hospitality first (hotels, bars,
restaurants, cafes, clubs, baths, pools), then homes, gardens and beyond. Leaning new but nostalgic.
"New" means new to most people, so a place published in 2016 counts. Roughly half the feed comes from
the archive. 80% US (New York, then Austin, Miami, Los Angeles), 20% abroad.

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
- BRING: taken word for word from build/bring_bank.txt (APPROVED, or RESERVED/CONDITIONAL when the condition holds). Never written fresh. No line repeats within 20 posts.
- One place per post. Never a listicle.

## Source mix: the feed is nobody's copy
Enforced by make_batch.py; a batch that breaks these does not build.
- No single publication supplies more than 25% of a batch, and design press as a whole never more than half.
  Press is where we hear about a place; the studio, photographer, venue or archive page is where the photos and
  the source_url come from. Record the article as `discovered_via`.
- One post per studio and one per photographer in any batch.
- At least 40% of a batch is archive or published more than a year ago ("new" means new to most, not new this week).
- US floor 60%, target 80%. New York first, then Austin, Miami, Los Angeles.
- We take at most two of the fifteen or so hospitality interiors the press runs each week, chosen on era and
  shareability, which are not their criteria. Same raw material, different eye, different photos, different words.

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

- 2026-10-21 (Nota Tavern 0005, Cassette 0010): no two slides of the same view. A photo that repeats
  another from a closer or further position, or a slight reframe, is a replicate: keep the stronger one. Each
  slide must show something the others do not (a different room, angle, detail or time of day). When picking
  photos, prefer sets that move through the place. Built in: build_posts.py same-view check (ORB match,
  100+ homography inliers = replicate, the later photo is dropped).
- 2026-10-21 (all Commons archive posts killed): archive photographs come only from professional collections
  made with proper cameras (Library of Congress: Gottscho-Schleisner, Carol M. Highsmith, HABS). Never
  Wikimedia Commons, open Flickr or any user-upload site, whatever the licence. Built in: archive_search.py
  searches only those collections.
