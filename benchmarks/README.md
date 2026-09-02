# Reproducible benchmark

`cases.json` is the public contract for the production benchmark proposed in
the competitive analysis. It deliberately separates cases that can be
reproduced with local fixtures from cases that still require a real image or
provider run. A case must not be marked `verified` until its input, route,
processing report, and artifact-manifest lineage are stored together.

Run the deterministic coverage first:

```bash
python3 scripts/check_benchmark.py
python3 -m unittest discover -s tests -v
```

Real-run cases must be recorded with the same flat fields as a normal delivery:
`input_type`, `route_id`, `model`, `approval_sha256`, `native_frame_count`,
`successful_cells`, `withheld_cells`, `output_size_bytes`, and the manifest
artifact id. Credentials, signed URLs, and raw personal images never belong
in this benchmark directory.
