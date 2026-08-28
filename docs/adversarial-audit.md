# Adversarial audit — 2026-08-26

Scope: Agent interaction, static approval, layout detection, prompt compilation, Provider discovery/routing/execution, local media processing, dependency integrity, secrets, and open-source release hygiene. Paid remote generation was intentionally not invoked.

## Fixed findings

| Severity | Finding | Resolution |
|---|---|---|
| Critical | Provider configuration could be selected but no AI SDK data-plane executor existed. | Added a real Node video Gateway, exact Provider dependencies, a lockfile, offline import checks, and a one-attempt route executor. |
| Critical | Static approval existed only as conversational instructions, so stale approval could be reused after regeneration. | Added atomic job state with SHA-256 binding for the exact image and layout; both Python and Node executors fail closed on mismatch. |
| High | A capability report could inject an unconfigured external Provider or inflate its priority/capabilities. | External Provider identity, driver, priority, model, and capabilities now come from the validated config; config/report/task hashes bind discovery, routing, and execution. |
| High | Custom child adapters inherited the entire Agent environment and could print secrets to the parent log. | Child environments are allowlisted to basic runtime/network variables plus declared Provider credentials; stdout/stderr are suppressed on failure. |
| High | Reusing an output directory could leave old numbered stickers in a new ZIP; ZIP names allowed directory traversal. | Output preflight now refuses stale generated artifacts unless `--overwrite`; archive names must be plain `.zip` basenames. |
| High | `preserve-alpha` silently accepted fully opaque video and mislabeled it as source alpha. | The first extracted frame must contain meaningful alpha in every cell or processing stops. |
| High | Provider JSON schema was permissive and runtime scripts did not enforce it. | Added strict manual runtime validation, duplicate-ID checks, driver-specific requirements, known package/provider mapping, placeholder rejection, and literal-secret-field rejection. |
| Medium | Requested layout received a score bonus that could overturn observed evidence. | Requested layout is now only a tie-breaker; the default candidate set is broader and low confidence remains blocking. |
| Medium | Generic identical motion text was silently used for every cell. | A complete per-cell tile plan is now required unless the caller explicitly opts into generic fallback motions. |
| Medium | Key-pose directories sorted lexicographically (`10` before `2`) and could disagree with the detected grid. | Added natural sorting, required layout/count matching, pose and resource limits, and `layout.json` packaging. |
| Medium | Video/image dimensions, frame count, pose count, FPS, duration, and output size were effectively unbounded. | Added conservative limits and clear override parameters where appropriate. |
| Medium | A second legacy root cutout implementation could diverge from the canonical script. | Replaced it with a compatibility entry point that forwards to `scripts/process_emoji_grid.py`. |
| Medium | No reproducible Node install, Python dependency declaration, CI, security policy, or adversarial integration test existed. | Added `package-lock.json`, `requirements.txt`, GitHub Actions, security/contribution guidance, Node contract tests, and a real FFmpeg-to-WebP offline integration test. |

## Verification performed

- Python unit, adversarial, and FFmpeg integration suite.
- Node Provider import and approval-gate contract suite.
- `npm audit --audit-level=high`.
- Python compilation and Node syntax checks.
- Skill metadata/structure validation.
- Offline probe of all five bundled AI SDK adapters.
- Local configured xAI probe without making a generation request.

## Residual risks and explicit boundaries

- No paid API request was made. Authentication validity, account quota, billing, remote moderation, remote model availability, and actual video quality remain unproven until the user authorizes a real generation.
- Whole-sheet video models can still create cross-cell attention leakage despite strict prompts. Visual review and per-cell regeneration remain necessary.
- Edge-connected color matting is deliberately conservative and is not a replacement for a dedicated video matting model on hair, motion blur, shadows, or textured scenes. Near-black and near-white plates now use a tighter key, and `processing.json` warns `subject-alpha-eaten-by-key` when subject-colored pixels stay weak. Still composite GIFs onto a light background before delivery; binary GIF transparency can hide the same damage on black.
- A custom `command` Adapter is executable code chosen by the user. Environment filtering reduces accidental credential exposure but does not sandbox filesystem or network access.
- Native tool manifests are assertions from the host Agent runtime and cannot be independently proven by this repository.
- Vercel AI SDK video generation remains experimental, so pinned dependency upgrades require contract retesting.
- The repository is released under the MIT License; the copyright holder is `kobingogo`.
- The current Git working tree may contain generated local artifacts. Review the ignore rules and stage only source, tests, documentation, examples, and lockfiles for the initial public commit.
