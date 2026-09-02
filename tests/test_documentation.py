from __future__ import annotations

import json
import re
import unittest
from pathlib import Path
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")


class DocumentationTests(unittest.TestCase):
    def test_local_markdown_links_resolve(self) -> None:
        for document in [
            ROOT / "README.md",
            ROOT / "README.en.md",
            ROOT / "SKILL.md",
            *sorted((ROOT / "references").glob("*.md")),
        ]:
            text = document.read_text(encoding="utf-8")
            for raw in LINK.findall(text):
                target = raw.split("#", 1)[0].strip()
                if not target or "://" in target or target.startswith("mailto:"):
                    continue
                resolved = (document.parent / unquote(target)).resolve()
                with self.subTest(document=document.name, target=target):
                    self.assertTrue(resolved.exists(), f"broken local link: {document} -> {target}")

    def test_style_table_embeds_one_expression_image_per_verified_style(self) -> None:
        gallery = json.loads((ROOT / "gallery" / "index.json").read_text(encoding="utf-8"))
        styles = gallery["styles"]
        for document in (ROOT / "README.md", ROOT / "README.en.md"):
            text = document.read_text(encoding="utf-8")
            if document.name == "README.md":
                exploration = json.loads(
                    (ROOT / "works" / "方块角色" / "style-exploration" / "manifest.json").read_text(
                        encoding="utf-8"
                    )
                )
                image_refs = re.findall(
                    r'<img src="(docs/assets/style-exploration/fox/[^\"]+\.png)"', text
                )
                self.assertEqual(len(image_refs), len(exploration["styles"]))
                for entry in exploration["styles"]:
                    with self.subTest(document=document.name, style=entry["id"]):
                        row = rf'^\| `{re.escape(entry["id"])}`(?:[^|]*)\|'
                        self.assertRegex(text, re.compile(row, re.MULTILINE))
                        self.assertIn(
                            f'<img src="docs/assets/style-exploration/fox/{entry["file"]}"',
                            text,
                        )
                continue
            image_refs = re.findall(r'<img src="(gallery/styles/[^\"]+/static\.png)"', text)
            self.assertEqual(len(image_refs), len(styles), f"{document.name} must show every style image in the table")
            for entry in styles:
                expected = f'gallery/styles/{entry["gallery"]}/static.png'
                with self.subTest(document=document.name, style=entry["id"]):
                    row = rf'^\| `{re.escape(entry["id"])}`(?:[^|]*)\|'
                    self.assertRegex(text, re.compile(row, re.MULTILINE))
                    self.assertIn(f'<img src="{expected}"', text)


if __name__ == "__main__":
    unittest.main()
