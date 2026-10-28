"""End-to-end on examples/sample-app: extract → verify → build through the CLI, then assert the chains the sample is built to show."""
import json
import tempfile
import unittest
from pathlib import Path

from screenmap import cli

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "examples" / "sample-app"


class SampleAppEndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls._tmp.name)
        cls.rc = cli.main(["all", str(SAMPLE), "-o", str(cls.out), "--name", "sample"])
        cls.g = json.loads((cls.out / "graph.json").read_text(encoding="utf-8"))
        cls.adj = {}
        for e in cls.g["edges"]:
            cls.adj.setdefault(e["s"], []).append((e["t"], e["kind"], e["conf"]))

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def edges(self, s, kind):
        return sorted(t for t, k, _ in self.adj.get(s, []) if k == kind)

    def test_adapter_command_prints_the_config_path_or_exits_1(self):
        import contextlib
        import io
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "notes").mkdir()
            (Path(d) / "notes" / "notes.screenmap.json").write_text('{"repo_names": ["notes-android"]}', encoding="utf-8")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = cli.main(["adapter", "notes-android", "--projects", d])
            self.assertEqual(rc, 0)
            self.assertEqual(out.getvalue().strip(), str(Path(d) / "notes" / "notes.screenmap.json"))
            with contextlib.redirect_stdout(io.StringIO()) as out2:
                rc = cli.main(["adapter", "other", "--projects", d])
            self.assertEqual((rc, out2.getvalue()), (1, ""))

    def test_pipeline_succeeds_and_writes_html(self):
        self.assertEqual(self.rc, 0)
        html = (self.out / "sample.html").read_text(encoding="utf-8")
        self.assertIn("<title>Notes — Screen Map</title>", html)
        self.assertNotIn("__GRAPH_JSON__", html)
        self.assertFalse((self.out / "sample.artifact.html").exists())

    def test_manifest_hosts_and_sdk_callback_filtering(self):
        hosts = {n["label"] for n in self.g["nodes"] if n["kind"] == "host"}
        self.assertEqual(hosts, {"notes://open"})  # the sdk12345://oauth filter is ignored via ignore_scheme_prefixes
        self.assertEqual(self.edges("host:open", "entry"), ["activity:NoteDetailActivity"])

    def test_deeplink_chain_host_to_activity(self):
        self.assertEqual(self.edges("host:open", "maps"), ["target:NOTE"])
        self.assertEqual(self.edges("target:NOTE", "maps_route"), ["route:NoteDetail"])
        self.assertEqual(self.edges("route:NoteDetail", "routes"), ["activity:NoteDetailActivity"])  # via Launcher → ScreenHelper overloads
        self.assertEqual(self.edges("route:Home", "routes"), ["activity:MainActivity"])
        self.assertEqual(self.edges("route:NoOp", "routes"), [])

    def test_ui_links(self):
        self.assertIn("fragment:com.example.notes.list.ListFragment", self.edges("activity:MainActivity", "hosts"))
        self.assertIn("screen:com.example.notes.list.NoteListScreen", self.edges("activity:MainActivity", "hosts"))
        self.assertEqual(self.edges("activity:MainActivity", "navigates"), ["activity:NoteDetailActivity"])
        self.assertEqual(self.edges("screen:com.example.notes.list.NoteListScreen", "vm"), ["viewmodel:com.example.notes.list.NoteListViewModel"])

    def test_data_chain_reaches_endpoints_through_datasource(self):
        by_label = {n["label"]: n for n in self.g["nodes"]}
        self.assertEqual(by_label["GetNotesUseCase"]["endpoints"], ["endpoint:NoteApi.list"])
        self.assertEqual(by_label["NoteListViewModel"]["endpoints"], ["endpoint:NoteApi.list"])
        self.assertEqual(by_label["GetNoteUseCase"]["endpoints"], ["endpoint:NoteApi.get"])
        self.assertEqual(by_label["POST api/v1/notes"]["path"], "api/v1/notes")  # `$BASE/notes` template resolved

    def test_meta_counts(self):
        self.assertEqual(self.g["meta"]["counts"]["activity"], 3)
        self.assertEqual(self.g["meta"]["counts"]["endpoint"], 3)
        self.assertEqual(self.g["meta"]["stats"]["manifest_activities"], 3)
        self.assertTrue(self.g["meta"]["stats"]["deeplink_files_found"])


if __name__ == "__main__":
    unittest.main()
