import ast
import re
import unittest

from test_core import PLUGIN


class DistributionTests(unittest.TestCase):
    def test_metadata_matches_registration(self):
        # This distribution deliberately uses only simple top-level YAML scalars.
        metadata = dict(
            line.split(": ", 1)
            for line in (PLUGIN / "metadata.yaml").read_text(encoding="utf-8").splitlines()
            if line and not line.startswith("#")
        )
        tree = ast.parse((PLUGIN / "main.py").read_text(encoding="utf-8"))
        registration = next(
            item
            for node in tree.body if isinstance(node, ast.ClassDef)
            for item in node.decorator_list
            if isinstance(item, ast.Call) and isinstance(item.func, ast.Name)
            and item.func.id == "register"
        )
        values = [ast.literal_eval(arg) for arg in registration.args]
        self.assertEqual(metadata["name"], "astrbot_plugin_turnflow")
        self.assertEqual(metadata["name"], values[0])
        self.assertEqual(metadata["author"], values[1])
        self.assertEqual(metadata["version"], values[3])
        self.assertEqual(metadata["version"], "1.0.0")
        self.assertTrue(metadata["desc"])
        self.assertEqual(
            metadata["repo"], "https://github.com/Yuimi-chaya/astrbot_plugin_turnflow"
        )

    def test_license_and_attribution_are_present(self):
        license_text = (PLUGIN / "LICENSE").read_text(encoding="utf-8")
        self.assertIn("GNU AFFERO GENERAL PUBLIC LICENSE", license_text)
        notices = (PLUGIN / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
        self.assertIn("aliveriver/astrbot_plugin_continuous_message", notices)
        self.assertIn("Zhalslar/astrbot_plugin_parser", notices)
        self.assertIn("2026-09-20", notices)

    def test_readme_local_links_resolve_and_no_images_are_embedded(self):
        for name in ("README.md", "README_EN.md"):
            text = (PLUGIN / name).read_text(encoding="utf-8")
            self.assertNotIn("![", text)
            self.assertNotIn("<img", text.lower())
            self.assertNotIn("MIT License", text)
            self.assertIn("AGPLv3", text)
            for link in re.findall(r"\[[^\]]+\]\(([^)]+)\)", text):
                if "://" not in link and not link.startswith("#"):
                    self.assertTrue((PLUGIN / link.split("#", 1)[0]).is_file(), link)

    def test_old_assets_are_not_distributed(self):
        self.assertFalse((PLUGIN / "logo.png").exists())
        self.assertFalse((PLUGIN / "pic").exists())
        for path in PLUGIN.iterdir():
            self.assertNotIn(path.suffix.lower(), {".png", ".jpg", ".jpeg", ".svg", ".gif"})

    def test_release_runtime_files_have_no_local_paths_or_bom(self):
        for path in PLUGIN.iterdir():
            if path.suffix not in {".py", ".md", ".yaml", ".json", ".txt"}:
                continue
            content = path.read_bytes()
            self.assertFalse(content.startswith(b"\xef\xbb\xbf"), path.name)
            self.assertNotIn(b"\r\n", content, path.name)
            text = content.decode("utf-8")
            self.assertNotIn("C:\\plugin\\", text, path.name)
            self.assertNotIn("C:\\Users\\", text, path.name)


if __name__ == "__main__":
    unittest.main()
