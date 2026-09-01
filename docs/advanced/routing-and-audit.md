# Routing and audit

## Approval boundary

Generated static sheets stop at a human review gate. `job-state.json` binds the approved image and detected layout by SHA-256. Any replacement of either file invalidates approval before a provider call.

## Capability discovery

`probe_video_capabilities.py` records callable tools, local processing, credentials readiness, and implementation readiness. It does not claim remote quota or service health. `route_video_provider.py` then creates an immutable route plan with:

- eligible and rejected providers;
- the selected numbered attempts;
- fallback mode;
- billable-attempt warning;
- config, capability, task, and route hashes.

## Attempt ledger

`attempt-ledger.json` is the live idempotency record:

- an attempt is claimed once;
- submission success records the remote request id immediately;
- an interrupted submission becomes `uncertain`;
- an uncertain attempt cannot be replayed implicitly;
- explicit resume continues the same provider request when supported;
- local QC rejection overwrites any optimistic provider success state.

The live ledger is intentionally not hash-recorded while it is changing. Terminal snapshots are recorded after execution.

## Artifact manifest

`artifact-manifest.json` is append-only lineage. IDs bind SHA-256, absolute path, and filename so identical content in different directories is not conflated. Current files can be verified with:

```bash
python3 scripts/artifact_manifest.py verify \
  --manifest works/<character>/artifact-manifest.json
```

The keypose plan, per-cell prompt, generated pose sheets, normalized poses, rendered outputs, processing report, and final bundle all participate in the same manifest when `--manifest` is supplied.

## Output transaction

Primary output scripts reject input/output overlap. With `--overwrite`, the previous directory is moved to a same-parent backup and a recovery journal is written. Commit removes the backup; exceptions restore it. A later run can recover an abandoned transaction after confirming its owner process is dead.

## Delivery

`assemble_delivery.py` collects numbered PNG/WebP/GIF media and audit files into one directory and one ZIP. Use `--cleanup-media-dir` only when the media and delivery directories are separate siblings. Lossless Animated WebP is the preferred format; GIF is the compatibility format.

See [output-contract.md](../../references/output-contract.md) for the normative package and QC fields.
