# Repository maintenance

## Media policy

Git contains only compact, reviewable style evidence under `gallery/styles/`: one static PNG, one animated GIF, layout, route, and processing report per verified style. Full sticker packs, promotional renders, raw videos, ZIPs, and character workspaces must not be committed.

CI enforces:

- README and README.en remain between 150 and 220 lines;
- `examples/`, `promo-v020/`, and `works/` are not tracked;
- compact tracked media stays below 12MiB;
- the CI badge and release gate remain present;
- 12–16 style entries pass the real-nine-cell evidence validator.

## Legacy gallery archive

The former tracked `examples/` and `promo-v020/` trees are preserved in:

- Release: [v0.2.0](https://github.com/kobingogo/motion-sticker-pack/releases/tag/v0.2.0)
- Asset: [motion-sticker-pack-legacy-gallery-2026-09.zip](https://github.com/kobingogo/motion-sticker-pack/releases/download/v0.2.0/motion-sticker-pack-legacy-gallery-2026-09.zip)
- Size: 218,353,710 bytes
- SHA-256: `6befbb8e2ee29aa34981e5586cb988d93052190a90c70cfac92244db0696c178`

The archive was built with `git archive` from commit `0702d22`, so it contains exactly the tracked versions.

## Releases

Official tags/releases are created only through `.github/workflows/release.yml`. The workflow checks out `main`, runs the complete Python/Node/policy suite, validates SemVer, and only then creates the tag and GitHub Release from that verified commit. A failed verification job cannot reach the publish job.

Protect `main` with required `ci / test (3.10)` and `ci / test (3.12)` checks. Direct releases and hand-created version tags are outside the supported process.

## History-slimming evaluation

Before this cleanup, loose Git objects occupied roughly 448MiB and historical blobs included several 8–21MiB videos plus many multi-megabyte WebPs. Removing files from the current tree improves fresh checkout HEAD size but does not remove historical objects.

Options:

1. **No rewrite** — safest for existing forks and tags; historical clone remains large.
2. **Git LFS migration** — keeps file history semantics but requires LFS storage/bandwidth and rewrites affected commits.
3. **`git filter-repo` purge** — smallest repository, but rewrites every affected commit/tag and requires coordinated force-push plus fork re-cloning.

Recommendation: keep the current no-rewrite cleanup for V0.3, measure fresh clone and pack size after normal server GC, then schedule a separately announced `git filter-repo` migration only if the packed clone remains above 150MiB. Preserve a mirror bundle and the legacy Release asset before any rewrite. Do not perform history rewriting as part of a feature release.
