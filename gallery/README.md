# Verified style gallery

This directory contains compact evidence, not full sticker packs. Every listed
style includes one original 240×240 static PNG and one original 240×240 animated GIF,
the detected 3×3 layout, the selected generation route, the processing report,
and a compact `provenance.json` record from the same nine-cell run. The complete
240×240 GIF and lossless Animated WebP files are distributed as the versioned
`motion-sticker-pack-gallery-v{version}.zip` Release asset; their hashes remain
in `release-manifest.json` and each style's provenance record.

The static PNG is a representative cell from each source case, not a
controlled same-character comparison across styles. Use it to verify that the
route produced a real nine-cell output; do not treat the gallery as a quality
ranking of the presets.

Some retained v0.2 evidence was encoded at 6fps; all new local routes use the
canonical V0.3 profile of 240×240 at 8fps.

Legacy cases are marked `legacy-evidence-partial` in `provenance.json` when
their original approval state or artifact manifest was not preserved. The
record lists that gap explicitly; a route/task hash is never presented as a
user approval hash. New cases should emit `audited-complete` provenance from
the same approved workflow.

Run:

```bash
python3 scripts/style_selector.py --format markdown
python3 scripts/style_selector.py --style clay-cute
python3 scripts/style_selector.py --verify-only
```

Full legacy case packs and promotional renders are distributed as the
`motion-sticker-pack-legacy-gallery-2026-09.zip` GitHub Release asset. The
compact evidence and original GIF previews here stay in Git so preset claims
remain reviewable in CI.
