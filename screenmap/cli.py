"""Command line: extract · build · verify · all · config."""
import argparse
import json
import sys
from pathlib import Path

from . import __version__, build as build_mod, config as config_mod, extract as extract_mod, verify as verify_mod


def _ref(a):
    return {"branch": a.branch, "commit": a.commit, "repo": a.repo_name} if a.commit else None


def _load_roles(path):
    return json.loads(Path(path).read_text(encoding="utf-8")) if path else None


def cmd_extract(a):
    cfg = config_mod.load(a.config, a.repo)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    graph, code = extract_mod.build(a.repo, cfg, roles=_load_roles(a.roles), ref=_ref(a))
    (out / "graph.json").write_text(json.dumps(graph, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "code.json").write_text(json.dumps(code, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"extract: nodes={len(graph['nodes'])} edges={len(graph['edges'])} counts={graph['meta']['counts']} → {out}/graph.json, code.json")
    return 0


def cmd_build(a):
    cfg = config_mod.load(a.config, None)
    title = a.title or cfg["title"]
    written, perf = build_mod.render(a.out, title, perf_path=a.perf, name=a.name, artifact=a.artifact, scrape_ui=a.scrape_ui)
    for w in written:
        print(f"build: wrote {w} ({w.stat().st_size // 1024} KB; perf={'none' if perf is None else perf['meta'].get('fetched_at', '?')})")
    return 0


def cmd_verify(a):
    cfg = config_mod.load(a.config, a.repo)
    graph_path = Path(a.out) / "graph.json"
    lines, fails = verify_mod.run(graph_path, a.repo, cfg, a.controls)
    print("\n".join(lines))
    print("FAILS =", fails)
    return 1 if fails else 0


def cmd_all(a):
    if not a.config:  # build has no repo argument — pin the config extract found (<repo>/screenmap.json) so title etc. carry over
        a.config = config_mod.load(None, a.repo)["_source"]
    rc = cmd_extract(a)
    if rc:
        return rc
    rc = cmd_verify(a)
    if rc and not a.build_anyway:
        print("verify failed — not building (use --build-anyway to override)")
        return rc
    return cmd_build(a) or rc


def cmd_config(a):
    cfg = config_mod.load(a.config, a.repo)
    print(config_mod.dump(cfg))
    return 0


def cmd_adapter(a):
    p = config_mod.adapter_for(a.repo_name, a.projects, warn=lambda m: print(m, file=sys.stderr))
    if not p:
        return 1
    print(p)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="screenmap", description="Screen · deeplink · API map of an Android (Kotlin) app as one HTML file.")
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common_repo(p):
        p.add_argument("repo", help="repository root (or an exported copy — see --branch/--commit)")
        p.add_argument("--config", help="screenmap.json (default: <repo>/screenmap.json if present, else built-in defaults)")

    def common_out(p):
        p.add_argument("-o", "--out", default="out", help="output directory (graph.json, code.json, html) [out]")

    def extract_args(p):
        p.add_argument("--roles", help="JSON {ActivityName: {role, docs[]}} shown on activity nodes")
        p.add_argument("--branch", help="branch to record in meta (for an archive copy without .git)")
        p.add_argument("--commit", help="commit to record in meta (with --branch; marks the source as clean)")
        p.add_argument("--repo-name", help="repository name to record in meta")

    def build_args(p):
        p.add_argument("--title", help="page title (default: config title)")
        p.add_argument("--name", default="screen_map", help="output file name without extension [screen_map]")
        p.add_argument("--perf", help="performance overlay JSON (see README)")
        p.add_argument("--artifact", action="store_true", help="also write <name>.artifact.html (no document skeleton)")
        p.add_argument("--scrape-ui", action="store_true", help="show the 'fetch performance' button (needs a local server that implements /scrape)")

    def verify_args(p):
        p.add_argument("--controls", help="JSON list of project-specific controls (see verify.py docstring)")

    p = sub.add_parser("extract", help="repository → graph.json + code.json")
    common_repo(p); common_out(p); extract_args(p); p.set_defaults(fn=cmd_extract)
    p = sub.add_parser("build", help="graph.json + code.json → html")
    p.add_argument("out", nargs="?", default="out", help="directory holding graph.json/code.json [out]")
    p.add_argument("--config"); build_args(p); p.set_defaults(fn=cmd_build)
    p = sub.add_parser("verify", help="sanity checks + optional controls")
    common_repo(p); common_out(p); verify_args(p); p.set_defaults(fn=cmd_verify)
    p = sub.add_parser("all", help="extract → verify → build")
    common_repo(p); common_out(p); extract_args(p); verify_args(p); build_args(p)
    p.add_argument("--build-anyway", action="store_true", help="build even when verify fails")
    p.set_defaults(fn=cmd_all)
    p = sub.add_parser("config", help="print the merged configuration")
    p.add_argument("repo", nargs="?", default=None); p.add_argument("--config"); p.set_defaults(fn=cmd_config)
    p = sub.add_parser("adapter", help="print the adapter config path for a repository folder name (exit 1 when none)")
    p.add_argument("repo_name", help="basename of the repository folder")
    p.add_argument("--projects", default="projects", help="directory holding <adapter>/<name>.screenmap.json [projects]")
    p.set_defaults(fn=cmd_adapter)

    a = ap.parse_args(argv)
    try:
        return a.fn(a)
    except config_mod.ConfigError as e:
        print("config error:", e, file=sys.stderr)
        return 2
