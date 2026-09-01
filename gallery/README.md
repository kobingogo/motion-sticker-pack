# Verified style gallery

This directory contains compact evidence, not full sticker packs. Every listed
style includes one 240×240 static PNG, one real animated GIF, the detected
3×3 layout, the selected generation route, and the processing report from the
same nine-cell run.

Some retained v0.2 evidence was encoded at 6fps; all new local routes use the
canonical V0.3 profile of 240×240 at 8fps.

Run:

```bash
python3 scripts/style_selector.py --format markdown
python3 scripts/style_selector.py --style clay-cute
python3 scripts/style_selector.py --verify-only
```

Full legacy case packs and promotional renders are distributed as the
`motion-sticker-pack-legacy-gallery-2026-09.zip` GitHub Release asset. The
compact evidence here stays in Git so preset claims remain reviewable in CI.
