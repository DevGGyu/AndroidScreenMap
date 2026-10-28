import json
import tempfile
import unittest
from pathlib import Path

from screenmap import config


class ConfigLoading(unittest.TestCase):
    def write(self, obj, name="screenmap.json"):
        p = Path(self.tmp.name) / name
        p.write_text(json.dumps(obj), encoding="utf-8")
        return p

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_defaults_without_any_file(self):
        cfg = config.load(None, self.tmp.name)
        self.assertEqual(cfg["modules"]["app"], "app")
        self.assertIsNone(cfg["deeplink"])
        self.assertIsNone(cfg["perf"])
        self.assertIsNone(cfg["_source"])

    def test_repo_root_screenmap_json_is_picked_up_and_merged(self):
        self.write({"title": "T", "naming": {"usecase": "Interactor$"}, "_comment": "ignored"})
        cfg = config.load(None, self.tmp.name)
        self.assertEqual(cfg["title"], "T")
        self.assertEqual(cfg["naming"]["usecase"], "Interactor$")
        self.assertEqual(cfg["naming"]["repository"], config.DEFAULTS["naming"]["repository"])  # untouched sibling

    def test_unknown_key_is_an_error(self):
        p = self.write({"nmaing": {}}, "bad.json")
        with self.assertRaises(config.ConfigError):
            config.load(p, None)

    def test_repo_names_must_be_a_list_of_strings(self):
        p = self.write({"repo_names": "my-app"}, "rn.json")
        with self.assertRaises(config.ConfigError):
            config.load(p, None)

    def adapters(self, **folders):
        root = Path(self.tmp.name) / "projects"
        for folder, obj in folders.items():
            (root / folder).mkdir(parents=True)
            (root / folder / f"{folder}.screenmap.json").write_text(obj if isinstance(obj, str) else json.dumps(obj), encoding="utf-8")
        return root

    def test_adapter_found_by_repo_names_or_by_folder_name_and_none_otherwise(self):
        warnings = []
        root = self.adapters(notes={"repo_names": ["notes-android", "notes-android-fork"]}, shop={"title": "Shop"}, broken="{not json")
        self.assertEqual(config.adapter_for("notes-android-fork", root, warn=warnings.append), root / "notes" / "notes.screenmap.json")
        self.assertEqual(config.adapter_for("shop", root), root / "shop" / "shop.screenmap.json")
        self.assertIsNone(config.adapter_for("notes-android-clone", root))
        self.assertEqual(len(warnings), 1)  # the broken config is reported, not fatal
        self.assertIn("broken", warnings[0])

    def test_two_adapters_claiming_one_repo_is_an_error(self):
        root = self.adapters(a={"repo_names": ["shop"]}, shop={})
        with self.assertRaises(config.ConfigError):
            config.adapter_for("shop", root)

    def test_deeplink_section_requires_its_core_keys(self):
        p = self.write({"deeplink": {"mapper_file": "x/Mapper.kt"}}, "dl.json")
        with self.assertRaises(config.ConfigError) as cm:
            config.load(p, None)
        self.assertIn("route_file", str(cm.exception))

    def test_comment_keys_inside_sections_are_ignored(self):
        p = self.write({"perf": {"_comment": "c", "start_call": "Trace\\.start\\("}}, "perf.json")
        cfg = config.load(p, None)
        self.assertEqual(cfg["perf"]["const_prefix"], "TRACE_")

    def test_package_from_gradle_namespace_when_manifest_has_none(self):
        app = Path(self.tmp.name) / "app"
        app.mkdir()
        (app / "build.gradle.kts").write_text('android {\n    namespace = "com.example.ns"\n}\n', encoding="utf-8")
        cfg = config.load(None, self.tmp.name)
        self.assertEqual(config.resolve_package(self.tmp.name, cfg, manifest_root=None), "com.example.ns")

    def test_package_missing_everywhere_is_a_clear_error(self):
        cfg = config.load(None, self.tmp.name)
        with self.assertRaises(config.ConfigError):
            config.resolve_package(self.tmp.name, cfg, manifest_root=None)


if __name__ == "__main__":
    unittest.main()
