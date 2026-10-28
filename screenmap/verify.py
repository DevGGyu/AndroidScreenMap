"""Sanity checks on graph.json against independent evidence from the repository, plus optional
project-specific controls (known chains that must exist). A failing check means the extractor is wrong
for this code base — not the documentation.

Controls file (JSON list), each item one of:
  {"exists": "host:gdslink"}
  {"path": ["host:x", "activity:Y"], "kinds": ["entry", "maps", "maps_route", "routes"], "via": ["target:T"]}
  {"only": ["route:R", "routes", ["activity:Y"]]}           # R's `routes` edges go to exactly these targets
  {"reaches": ["CouponUseCase", "endpoint:Api.fun"]}         # node label → endpoint in its `endpoints`
  {"edge": ["activity:A", "navigates", "activity:B"]}        # the edge exists (any conf)
Every item may carry "name" for the report line.
"""
import json
import re
import subprocess
from collections import deque
from pathlib import Path


class Verifier:
    def __init__(self, graph, repo, cfg):
        self.g, self.repo, self.cfg = graph, Path(repo), cfg
        self.adj = {}
        for e in graph["edges"]:
            self.adj.setdefault(e["s"], []).append((e["t"], e["kind"], e["conf"]))
        self.ids = {n["id"]: n for n in graph["nodes"]}
        self.lines, self.fails = [], 0

    def check(self, name, ok, detail=""):
        self.lines.append(("PASS" if ok else "FAIL") + " - " + name + (" " + detail if detail else ""))
        self.fails += 0 if ok else 1

    def info(self, text):
        self.lines.append("INFO - " + text)

    def path(self, src, dst, allow=None):
        q, prev = deque([src]), {src: None}
        while q:
            x = q.popleft()
            if x == dst:
                out = []
                while x:
                    out.append(x)
                    x = prev[x]
                return out[::-1]
            for t, k, c in self.adj.get(x, []):
                if (allow is None or k in allow) and t not in prev:
                    prev[t] = x
                    q.append(t)
        return None

    def sh(self, cmd):
        return subprocess.run(["bash", "-c", f"cd '{self.repo}' && {cmd}"], capture_output=True, text=True).stdout.strip()

    # ── generic checks ──
    def generic(self):
        g, cfg = self.g, self.cfg
        nodes = g["nodes"]
        ep = [n for n in nodes if n["kind"] == "endpoint"]
        grep = self.sh("grep -rhE '@(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS|HTTP)\\(' --include='*.kt' . | grep -v '/test/' | wc -l")
        self.check("endpoints = independent grep count", len([n for n in ep if not n.get("const")]) == int(grep or 0),
                   f"(graph {len([n for n in ep if not n.get('const')])} / grep {grep})")
        if cfg.get("endpoints"):  # constant endpoints: count the declaration regex over comment-stripped sources
            from .kotlin import strip_source
            decl, n = re.compile(cfg["endpoints"]["decl"]), 0
            for p in list(self.repo.rglob("*.java")) + list(self.repo.rglob("*.kt")):
                rel = "/" + p.relative_to(self.repo).as_posix()
                if "/src/main/" in rel and "/test/" not in rel and "/build/" not in rel:
                    n += len(decl.findall(strip_source(p.read_text(encoding="utf-8", errors="replace"))[0]))
            ce = [x for x in ep if x.get("const")]
            self.check("constant endpoints = declaration count over sources", len(ce) == n, f"(graph {len(ce)} / sources {n})")
        unres = [n["label"] for n in ep if n.get("unresolved")]
        self.check("no unresolved endpoint path constants", not unres, str(unres[:5]))
        mf_path = self.repo / (cfg["manifest"] or f"{cfg['modules']['app']}/src/main/AndroidManifest.xml")
        mf = len(re.findall(r"<activity\b", mf_path.read_text(encoding="utf-8", errors="replace")))
        acts = [n for n in nodes if n["kind"] == "activity"]
        self.check("activities = manifest <activity> count", len(acts) == mf, f"(graph {len(acts)} / manifest {mf})")
        hosts = [n for n in nodes if n["kind"] == "host"]
        bad = [h["label"] for h in hosts if not any(k == "entry" for _, k, _ in self.adj.get(h["id"], []))]
        self.check("every deeplink host has an entry edge to its receiving activity", not bad, str(bad[:5]))
        ds = g["meta"]["stats"].get("datasource") or {}
        self.check("every DataSource implementation that injects an API reaches ≥1 endpoint", not ds.get("impls_without_endpoint"),
                   str(ds.get("impls_without_endpoint")))
        if cfg.get("deeplink"):
            self.check("deeplink files found (mapper, route, navigator, launcher)", bool(g["meta"]["stats"].get("deeplink_files_found")))
            routes = [n for n in nodes if n["kind"] == "route"]
            routed = [r for r in routes if any(k == "routes" for _, k, _ in self.adj.get(r["id"], []))]
            self.info(f"routes with a resolved destination: {len(routed)}/{len(routes)}")
        reached = {e for n in nodes if n["kind"] in ("viewmodel", "usecase", "fragment", "activity") for e in (n.get("endpoints") or [])}
        unreach = sorted(n["label"] for n in ep if n["id"] not in reached)
        self.info(f"endpoints not reached from any ViewModel/UseCase: {len(unreach)}/{len(ep)} {unreach[:12]}")
        orph = [n["label"] for n in nodes if n["kind"] in ("activity", "fragment", "screen") and n["id"] not in self.adj
                and not any(n["id"] == t for lst in self.adj.values() for t, _, _ in lst)]
        self.info(f"UI nodes with no edge at all: {len(orph)} {orph[:12]}")

    # ── declarative controls ──
    def controls(self, items):
        for it in items:
            name = it.get("name")
            if "exists" in it:
                self.check(name or f"exists {it['exists']}", it["exists"] in self.ids)
            elif "path" in it:
                a, b = it["path"]
                p = self.path(a, b, set(it["kinds"]) if it.get("kinds") else None)
                ok = bool(p) and all(v in p for v in it.get("via", []))
                self.check(name or f"path {a} → {b}", ok, " > ".join(p or []))
            elif "only" in it:
                s, kind, targets = it["only"]
                got = sorted(t for t, k, _ in self.adj.get(s, []) if k == kind)
                self.check(name or f"{s} --{kind}--> only {targets}", got == sorted(targets), str(got))
            elif "reaches" in it:
                label, ep = it["reaches"]
                ok = any(ep in (n.get("endpoints") or []) for n in self.g["nodes"] if n["label"] == label)
                self.check(name or f"{label} reaches {ep}", ok)
            elif "edge" in it:
                s, kind, t = it["edge"]
                self.check(name or f"edge {s} --{kind}--> {t}", any(x == t and k == kind for x, k, _ in self.adj.get(s, [])))
            else:
                self.check(name or f"unknown control {it}", False)


def run(graph_path, repo, cfg, controls_path=None):
    graph = json.loads(Path(graph_path).read_text(encoding="utf-8"))
    v = Verifier(graph, repo, cfg)
    v.generic()
    if controls_path:
        v.controls(json.loads(Path(controls_path).read_text(encoding="utf-8")))
    return v.lines, v.fails
