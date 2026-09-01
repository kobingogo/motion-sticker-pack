# Character workspaces

Generated assets belong under `works/<character-slug>/`, not in a shared `work/` folder and not in the skill root. This folder is gitignored except for this README. Character images, videos, and packs are private local artifacts.

```text
works/
├── 小黑猫/
│   ├── character.json
│   ├── reference.png
│   ├── static-sheet.png
│   ├── static-prompt.json
│   ├── layout.json
│   ├── layout-overlay.png
│   ├── job-state.json
│   ├── tile-plan.json
│   ├── prompts.json
│   ├── video-providers.json
│   ├── video-task.json
│   ├── capabilities.json
│   ├── route.json
│   ├── attempt-ledger.json
│   ├── artifact-manifest.json
│   ├── raw-video/
│   └── output/
└── Elon-Musk/
    └── ...
```

Create the directory with:

```bash
python3 scripts/character_workspace.py --name '小黑猫'
```

Then write every later artifact into the printed `work_dir`.
