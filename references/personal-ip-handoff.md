# Personal IP → motion-sticker-pack handoff

`import_personal_handoff.py` is a small, local-only receiving adapter. It
accepts an `IP_HANDOFF` JSON object from `personal-ip-studio`, verifies the
identity boundary, and creates motion job metadata. It does not generate,
edit, inspect, or copy any image, and it never uses the original photo.

## Source-of-truth boundaries

`personal-ip-studio` is the identity source of truth: the approved card,
identity version, and approved anchor belong there. `motion-sticker-pack` is
the derived job source of truth: its `handoff.json` records the received
contract and its `character.json` is the local job manifest for downstream
static-prompt work. A motion job may derive prompts and media from the
approved anchor, but it must not edit the personal card or promote a new
identity.

There are two independent approval gates:

1. personal approval: `identity_status` must be `approved`; this adapter also
   verifies the card/anchor paths and the anchor content hash;
2. motion static approval: after import, the normal motion workflow still
   requires explicit approval of the exact static sheet before video work.

Import success is not static-sheet approval and is not permission to generate
an image or video.

## `IP_HANDOFF` v2 contract

The required fields are:

```json
{
  "type": "IP_HANDOFF",
  "protocol": "ip-handoff/v2",
  "source_skill": "personal-ip-studio",
  "target_skill": "motion-sticker-pack",
  "id": "character-slug",
  "identity_status": "approved",
  "identity_version": 3,
  "card": "/absolute/path/to/character.md",
  "anchor": "/absolute/path/to/character-anchor.png",
  "anchor_sha256": "64 lowercase or uppercase hex characters",
  "skin_id": "toy",
  "source_kind": "approved-anchor",
  "source_policy": "approved-anchor-only",
  "rendering_policy": "preserve-source-appearance",
  "motion_style_mode": "preserve-source-appearance",
  "motion_style_id": "custom",
  "motion_style_prompt": "Preserve the approved anchor appearance.",
  "reaction_overlays": {"glad": "开心"},
  "requested_reactions": ["glad"],
  "original_photo_policy": "do-not-use",
  "derivative_kind": "animated-sticker-pack",
  "derivative_owner": "motion-sticker-pack",
  "derivative_status": "handoff_ready"
}
```

`type` is an optional marker accepted for compatibility; the protocol, source,
target, id, status, policies, paths, version, skin, and hash are not optional.
`skin_id` must be one of `toy`, `wash`, `doodle`, `ink`, or `flat`. `style` and
`reactions` are legacy-compatible aliases; canonical `motion_style_*`,
`reaction_overlays`, and `requested_reactions` are resolved into `character.json`
and included in the JSON CLI result for later static-prompt compilation.
Unknown extension fields are accepted and preserved in `handoff.json`; they are
not silently interpreted as identity fields.

The importer rejects credential-shaped keys (`api_key`, `token`, `password`,
`private_key`, and related names) and recognizable bearer/API-key/JWT/PEM
values anywhere in the handoff. Keep credentials out of paths, style text,
reaction text, and extension fields too.

## Version, hash, and stale rules

- The card and anchor must be absolute, existing regular files. The declared
  `anchor_sha256` must match the bytes on disk. `--anchor` is an optional
  explicit re-check and must resolve to the same declared anchor path.
- `identity_version` plus the verified anchor hash identify the exact personal
  identity received by the job. A later personal edit must produce a new
  version and a new handoff.
- Re-importing the same version/hash is idempotent for identity metadata. An
  existing `character.json` is merged only by filling absent keys; existing
  keys, nested values, arrays, and local extensions are never overwritten.
- If an existing job manifest or `handoff.json` carries a different version or
  anchor hash, import fails with stable `stale-job` output. Resolve the stale
  job explicitly instead of mixing two identities.

## CLI and output

Run from this skill directory or pass an absolute script path:

```bash
python scripts/import_personal_handoff.py HANDOFF.json --work-dir /path/to/job
python scripts/import_personal_handoff.py HANDOFF.json --work-dir /path/to/job --anchor /absolute/anchor.png
```

Success and failure are both one-line JSON objects. A successful import writes
only `<work-dir>/handoff.json` and `<work-dir>/character.json` (creating the
directory when necessary), and returns `resolved.style` and
`resolved.reactions`. Failure returns `{"ok":false,...}` and a stable non-zero
exit code. No original photo is copied; the result explicitly reports
`original_photo_used: false` and `generated: false`.
