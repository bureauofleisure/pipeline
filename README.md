# Bureau of Leisure pipeline

Finds places, gathers and grades photos, builds carousels and hosts the finished slides for the Bureau of Leisure Instagram account.

## How it works
- `batches/*.json`: one file per week, each listing the posts (place, card, photo links). Adding one triggers the build.
- `grade/`: Grade v1.0, locked. Only the font paths differ from the original.
- `build/`: validates the card copy, rejects any photo that is too small (the 4:5 crop must be at least 1080 x 1350, nothing is ever enlarged), too soft or over-compressed, drops near-duplicates and tone outliers, benches a post with fewer than 4 good photos, uses the named cover (or the best-scoring photo) and builds the slides.
- `docs/` (built, never committed to main): finished slides, published to the `gh-pages` branch and served by GitHub Pages at public links the Instagram publisher can fetch. `manifest.json` there lists every post. Posts older than 120 days are pruned.
- `publisher.gs`: the Google Apps Script that posts approved rows from the sheet to Instagram. Kept here as the reference copy.
- `build/word_bank.txt`: the approved IN A WORD list. The build benches any card using a word that is not on it.
