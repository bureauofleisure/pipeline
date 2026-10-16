# Bureau of Leisure pipeline

Finds places, gathers and grades photos, builds carousels and hosts the finished slides for the Bureau of Leisure Instagram account.

## How it works
- `batches/*.json`: one file per week, each listing the posts (place, card, photo links). Adding one triggers the build.
- `grade/`: Grade v1.0, locked. Only the font paths differ from the original.
- `build/`: grades every photo, drops any that fail the checks, benches a post with fewer than 4 good photos, picks the best as the cover and builds the slides.
- `docs/`: finished slides, served by GitHub Pages at public links the Instagram publisher can fetch. `docs/manifest.json` lists every post.
