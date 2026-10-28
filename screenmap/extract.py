"""Source → graph. Reads an Android (Kotlin, MVVM + Clean Architecture) repository and emits
`graph.json` (nodes, edges, meta) and `code.json` (source snippets per node). Read-only.

Node kinds: activity · fragment · screen (Compose) · viewmodel · usecase · repository · api (Retrofit interface)
            endpoint · host (deeplink scheme host) · target · route · engine · external · shared
Edge kinds: entry (host → receiving activity) · maps (host → target) · maps_route (target → route) · routes (route → activity)
            navigates (UI → activity) · hosts (activity/fragment → fragment/screen) · vm (UI → viewmodel)
            injects (→ usecase/repository/api) · calls (repository → endpoint) · uses (→ engine)
Edge conf: parsed (read from code) · heuristic (package proximity) · dynamic (decided by a runtime parameter)
"""
import datetime
import re
import subprocess
import textwrap
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

from .config import resolve_package
from .kotlin import (collect_files, const_value, ctor_params, extend_up, fn_body, fn_spans, line_of, match_close,
                     member_spans, resolve_consts, split_arms, split_top, strip_source)

ANDROID_NS = "{http://schemas.android.com/apk/res/android}"


class Graph:
    def __init__(self):
        self.nodes = {}
        self.edges = {}

    def node(self, nid, kind, label, **kw):
        if nid not in self.nodes:
            self.nodes[nid] = {"id": nid, "kind": kind, "label": label}
        self.nodes[nid].update({k: v for k, v in kw.items() if v is not None})
        return nid

    def edge(self, s, t, kind, conf="parsed", via=None, **kw):
        if s == t or s not in self.nodes or t not in self.nodes:
            return
        key = (s, t, kind)
        e = self.edges.setdefault(key, {"s": s, "t": t, "kind": kind, "conf": conf, "via": []})
        if conf == "parsed":
            e["conf"] = "parsed"
        if via and via not in e["via"]:
            e["via"].append(via)
        for k, v in kw.items():
            if isinstance(v, list):
                e.setdefault(k, [])
                e[k] = sorted(set(e[k]) | set(v))
            else:
                e[k] = v


def build(repo, cfg, roles=None, ref=None):
    """Return (graph dict, code dict). `roles`: {ActivityName: {"role": str, "docs": [str]}} shown on activity nodes.
    `ref`: {"branch", "commit", "repo"} to record in meta when `repo` is an archive copy without .git."""
    repo = Path(repo)
    roles = roles or {}
    nm = cfg["naming"]
    mods = cfg["modules"]
    dl = cfg.get("deeplink")
    pf = cfg.get("perf")
    vm_suf, fr_suf, sc_suf = nm["viewmodel_suffix"], nm["fragment_suffix"], nm["screen_suffix"]
    uc_re, repo_re, ds_re = re.compile(nm["usecase"]), re.compile(nm["repository"]), re.compile(nm["datasource"])

    g = Graph()
    files = collect_files(repo, set(cfg["exclude_dirs"]))
    by_name = defaultdict(list)  # simple name → ClassInfo[]
    for f in files:
        for ci in f.classes:
            by_name[ci.name].append(ci)
    per_obj, glob = resolve_consts(files)
    stats = {"files": len(files), "warnings": []}
    snip_extra = {}  # node id → [code snippets] collected while parsing (target/host `when` arms)

    def ancestors(ci, seen=None):
        seen = seen if seen is not None else set()
        for s in ci.supers:
            if s not in seen:
                seen.add(s)
                for c2 in by_name.get(s, []):
                    ancestors(c2, seen)
        return seen

    def is_debug(rel):
        return nm["debug_path"] in "/" + rel

    def find_file(suffix):
        return next((f for f in files if suffix and f.rel.endswith(suffix)), None) if suffix else None

    # ── manifest: activities · hosts ──
    manifest_path = repo / (cfg["manifest"] or f"{mods['app']}/src/main/AndroidManifest.xml")
    manifest = ET.parse(manifest_path).getroot()
    mpkg = resolve_package(repo, cfg, manifest)
    app_el = manifest.find("application")
    activity_fq = {}
    hosts = {}  # host → {"schemes": {...}, "activity": simpleName, "browsable": bool}
    ignore_schemes = tuple(cfg["ignore_scheme_prefixes"])
    for a in app_el.findall("activity"):
        raw = a.get(ANDROID_NS + "name")
        fq = (mpkg + raw) if raw.startswith(".") else raw
        simple = fq.split("$")[0].split(".")[-1]
        activity_fq[simple] = fq
        for flt in a.findall("intent-filter"):
            schemes = {d.get(ANDROID_NS + "scheme") for d in flt.findall("data") if d.get(ANDROID_NS + "scheme")}
            hs = {d.get(ANDROID_NS + "host") for d in flt.findall("data") if d.get(ANDROID_NS + "host")}
            cats = {c.get(ANDROID_NS + "name") for c in flt.findall("category")}
            browsable = "android.intent.category.BROWSABLE" in cats
            if ignore_schemes and schemes and all(s.startswith(ignore_schemes) for s in schemes):
                continue  # third-party SDK callback, not an app deeplink
            for h in hs:
                hosts.setdefault(h, {"schemes": set(), "activity": simple, "browsable": browsable})
                hosts[h]["schemes"] |= {s for s in schemes if not s.startswith(ignore_schemes)} if ignore_schemes else schemes
            if any(x.get(ANDROID_NS + "name") == "android.intent.action.MAIN" for x in flt.findall("action")):
                activity_fq.setdefault("__launcher__", simple)
    launcher_activity = activity_fq.pop("__launcher__", None)
    stats["manifest_activities"] = len(activity_fq)

    # app source base: "<app>/<src root>/<package path>/" — package-relative depth drives the proximity heuristics
    src_root = next((r for r in cfg["source_roots"] if (repo / mods["app"] / r).is_dir()), cfg["source_roots"][0])
    app_base = f"{mods['app']}/{src_root}/{mpkg.replace('.', '/')}/"
    base_depth = len(app_base.rstrip("/").split("/"))

    # ── activity nodes ──
    for simple, fq in sorted(activity_fq.items()):
        cands = [c for c in by_name.get(simple, []) if c.kind == "class"]
        ci = next((c for c in cands if c.fqcn == fq), cands[0] if cands else None)
        ext = ci is None
        ui = None
        if ci:
            f = next(x for x in files if x.rel == ci.file)
            body = f.text[ci.body[0]:ci.body[1] + 1]
            has_c = "setContent" in body or "ComposeView" in body
            has_x = bool(re.search(r"setContentView|ViewBinding|DataBindingUtil|inflate\(", f.text))
            ui = "Hybrid" if has_c and has_x else "Compose" if has_c else "XML" if has_x else None
        g.node(
            f"activity:{simple}", "activity", simple,
            fqcn=fq, file=ci.file if ci else None, line=ci.line if ci else None,
            module=ci.module if ci else "external", ui=ui, external=ext or None,
            debug=is_debug(ci.file) if ci else None,
            role=roles.get(simple, {}).get("role"), docs=roles.get(simple, {}).get("docs"),
            launcher=True if simple == launcher_activity else None,
        )
    activity_ids = {s: f"activity:{s}" for s in activity_fq}

    # ── fragments ──
    fragment_ids = {}
    for f in files:
        for ci in f.classes:
            if ci.kind != "class" or "abstract" in ci.mods:
                continue
            anc = ancestors(ci) | ci.supers
            if any(a.endswith(fr_suf) for a in anc) and ci.name not in activity_fq:
                nid = f"fragment:{ci.fqcn}"
                g.node(nid, "fragment", ci.name, fqcn=ci.fqcn, file=ci.file, line=ci.line, module=ci.module,
                       debug=is_debug(ci.file) or None, dialog=any("Dialog" in a or "BottomSheet" in a for a in anc) or None)
                fragment_ids.setdefault(ci.name, nid)

    # ── Compose screens ──
    screen_ids = {}
    screen_re = re.compile(r"@Composable\s+(?:@\w+(?:\([^)]*\))?\s+)*(?:(?:internal|public)\s+)?fun\s+(\w+" + re.escape(sc_suf) + r")\s*\(")
    for f in files:
        for m in screen_re.finditer(f.text):
            name = m.group(1)
            nid = f"screen:{f.pkg}.{name}"
            g.node(nid, "screen", name, file=f.rel, line=line_of(f.text, m.start()), module=f.module, ui="Compose",
                   debug=is_debug(f.rel) or None)
            screen_ids.setdefault(name, nid)

    # ── ViewModels ──
    vm_ids = {}
    for f in files:
        for ci in f.classes:
            if ci.kind != "class" or "abstract" in ci.mods:
                continue
            if "HiltViewModel" in ci.annos or (ci.name.endswith(vm_suf) and "ViewModel" in (ancestors(ci) | ci.supers)):
                nid = f"viewmodel:{ci.fqcn}"
                g.node(nid, "viewmodel", ci.name, fqcn=ci.fqcn, file=ci.file, line=ci.line, module=ci.module,
                       debug=is_debug(ci.file) or None)
                vm_ids.setdefault(ci.name, nid)

    # ── domain/data: UseCase · Repository · API · endpoint ──
    uc_ids, repo_ids, api_ids, impl_of = {}, {}, {}, {}
    for f in files:
        for ci in f.classes:
            if ci.kind == "class" and uc_re.search(ci.name) and f.module in (mods["domain"], mods["app"]):
                nid = f"usecase:{ci.fqcn}"
                g.node(nid, "usecase", ci.name, fqcn=ci.fqcn, file=ci.file, line=ci.line, module=ci.module)
                uc_ids.setdefault(ci.name, nid)
            elif ci.kind == "interface" and repo_re.search(ci.name) and f.module == mods["domain"]:
                nid = f"repository:{ci.fqcn}"
                g.node(nid, "repository", ci.name, fqcn=ci.fqcn, file=ci.file, line=ci.line, module=ci.module)
                repo_ids.setdefault(ci.name, nid)
    for f in files:
        for ci in f.classes:
            if ci.kind == "class" and f.module == mods["data"]:
                for s in ci.supers:
                    if s in repo_ids:
                        impl_of[s] = ci

    endpoint_re = re.compile(r"@(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS|HTTP)\s*\(")
    api_methods = defaultdict(dict)  # apiName → {funName: endpointId}
    endpoint_total = 0
    for f in files:
        if not endpoint_re.search(f.text):
            continue
        for ci in f.classes:
            if ci.kind != "interface":
                continue
            seg_s, seg_e = ci.body
            found = []
            for m in endpoint_re.finditer(f.text, seg_s, seg_e):
                p = m.end() - 1
                e = match_close(f.skel, p, "(", ")")
                args = f.text[p + 1:e]
                fm = re.compile(r"(?:suspend\s+)?fun\s+(\w+)\s*\(").search(f.skel, e, min(e + 600, seg_e))
                if not fm:
                    continue
                pe = match_close(f.skel, fm.end() - 1, "(", ")")
                params = f.text[fm.end():pe]
                rest = f.text[pe + 1:pe + 200].split("\n")[0]
                ret = rest.split(":", 1)[1].strip().rstrip("{=").strip() if ":" in rest else ""
                verb = m.group(1)
                first = split_top(args)[0] if args.strip() else ""
                if verb == "HTTP":
                    pm = re.search(r"method\s*=\s*\"(\w+)\"", args)
                    verb = pm.group(1) if pm else "HTTP"
                    pp = re.search(r"path\s*=\s*([^,)]+)", args)
                    first = pp.group(1) if pp else ""
                path = const_value(first, per_obj, glob)
                unresolved = path is None
                if path is None:
                    path = first.strip() or "(path 없음)"
                found.append((verb, path, unresolved, fm.group(1), params.strip(), ret, line_of(f.text, m.start())))
            if not found:
                continue
            aid = f"api:{ci.name}"
            g.node(aid, "api", ci.name, fqcn=ci.fqcn, file=ci.file, line=ci.line, module=ci.module)
            api_ids[ci.name] = aid
            for verb, path, unres, fun, params, ret, ln in found:
                eid = f"endpoint:{ci.name}.{fun}"
                g.node(eid, "endpoint", f"{verb} {path}", verb=verb, path=path, fun=fun, api=ci.name,
                       params=re.sub(r"\s+", " ", params)[:300], ret=ret[:160], file=ci.file, line=ln,
                       unresolved=unres or None, module=ci.module)
                g.edge(aid, eid, "calls", via=None)
                api_methods[ci.name][fun] = eid
                endpoint_total += 1

    # ── endpoints declared as constants (optional; legacy apps without Retrofit) — the enclosing class is the `api` node ──
    ec = cfg.get("endpoints")
    const_eps = {}  # constant name → endpoint id
    if ec:
        decl_re = re.compile(ec["decl"])
        for f in files:
            for m in decl_re.finditer(f.text):
                owner = None
                for ci in f.classes:
                    if ci.start <= m.start() < ci.end and (owner is None or ci.start > owner.start):
                        owner = ci
                oname = owner.name if owner else Path(f.rel).stem
                aid = f"api:{oname}"
                g.node(aid, "api", oname, fqcn=owner.fqcn if owner else None, file=f.rel, line=owner.line if owner else 1, module=f.module)
                api_ids.setdefault(oname, aid)
                name, path = m.group("name"), m.group("path")
                eid = f"endpoint:{oname}.{name}"
                g.node(eid, "endpoint", path, verb="", path=path, fun=name, api=oname, file=f.rel,
                       line=line_of(f.text, m.start()), module=f.module, const=True)
                g.edge(aid, eid, "calls")
                api_methods[oname][name] = eid
                const_eps[name] = eid
                endpoint_total += 1
        stats["endpoint_consts"] = len(const_eps)
    stats["endpoints"] = endpoint_total

    # Repository → endpoint (`api.fun(` calls inside each implementation method)
    repo_methods = defaultdict(lambda: defaultdict(set))  # repoName → method → {endpointId}

    def api_param_map(ci):
        return {pn: pt for pn, pt in ctor_params(ci.ctor) if pt in api_ids}

    def fun_segments(f, ci):
        """Split a class body at each `fun` → (name, text of that function's region)."""
        body = f.text[ci.body[0]:ci.body[1] + 1]
        marks = [(m.start(), m.group(1)) for m in re.finditer(r"\bfun\s+(\w+)\s*\(", body)]
        for idx, (pos, fun) in enumerate(marks):
            yield fun, body[pos:marks[idx + 1][0] if idx + 1 < len(marks) else len(body)]

    # DataSource layer: data-module interface → implementation (by supertypes, not by name) → endpoints per method.
    # When a repository calls `source.m(` the endpoints of `ds_methods[interface][m]` are credited to it.
    ds_ifaces = {ci.name for f in files for ci in f.classes if ci.kind == "interface" and f.module == mods["data"] and ds_re.search(ci.name)}
    ds_methods = defaultdict(lambda: defaultdict(set))
    ds_impls, ds_no_endpoint = 0, []
    for f in files:
        for ci in f.classes:
            if ci.kind != "class" or f.module != mods["data"]:
                continue
            dss = ci.supers & ds_ifaces
            pmap = api_param_map(ci) if dss else {}
            if not pmap:
                continue  # implementations without an API (Room, utilities) legitimately reach no endpoint
            ds_impls += 1
            mapped = 0
            for fun, seg in fun_segments(f, ci):
                for am in re.finditer(r"\b(\w+)\.(\w+)\s*\(", seg):
                    pn, mn = am.group(1), am.group(2)
                    if pn in pmap and mn in api_methods.get(pmap[pn], {}):
                        for ds in dss:
                            ds_methods[ds][fun].add(api_methods[pmap[pn]][mn])
                        mapped += 1
            if not mapped:
                ds_no_endpoint.append(ci.name)
    stats["datasource"] = {"interfaces": len(ds_ifaces), "impls_with_api": ds_impls, "impls_without_endpoint": ds_no_endpoint}

    for rname, impl in impl_of.items():
        f = next(x for x in files if x.rel == impl.file)
        pmap = api_param_map(impl)
        dpmap = {pn: pt for pn, pt in ctor_params(impl.ctor) if pt in ds_ifaces}
        for fun, seg in fun_segments(f, impl):
            for am in re.finditer(r"\b(\w+)\.(\w+)\s*\(", seg):
                pn, mn = am.group(1), am.group(2)
                if pn in pmap and mn in api_methods.get(pmap[pn], {}):
                    eid = api_methods[pmap[pn]][mn]
                    repo_methods[rname][fun].add(eid)
                    g.edge(repo_ids[rname], eid, "calls", via=fun)
                elif pn in dpmap:
                    for eid in ds_methods.get(dpmap[pn], {}).get(mn, ()):
                        repo_methods[rname][fun].add(eid)
                        g.edge(repo_ids[rname], eid, "calls", via=f"{fun} · {dpmap[pn]}")
        if impl.name:
            g.nodes[repo_ids[rname]]["impl"] = impl.name
            g.nodes[repo_ids[rname]]["impl_file"] = impl.file

    # constructor-injection edges (VM / UseCase / fragment / activity → UseCase / Repository / API)
    repo_by_impl = {impl.name: repo_ids[r] for r, impl in impl_of.items()}  # consumers that inject the implementation class directly

    def inject_edges(src_id, ci, f):
        body = f.text[ci.body[0]:ci.body[1] + 1]
        for pn, pt in ctor_params(ci.ctor):
            tgt = uc_ids.get(pt) or repo_ids.get(pt) or repo_by_impl.get(pt) or api_ids.get(pt)
            if not tgt:
                continue
            used = sorted({m.group(1) for m in re.finditer(r"\b" + re.escape(pn) + r"\s*\.\s*(\w+)\s*\(", body)})
            g.edge(src_id, tgt, "injects", methods=used)

    for f in files:
        for ci in f.classes:
            for pref in ("viewmodel", "usecase", "fragment"):
                nid = f"{pref}:{ci.fqcn}"
                if nid in g.nodes:
                    inject_edges(nid, ci, f)
    for name, nid in activity_ids.items():
        for c in by_name.get(name, []):
            if c.kind == "class":
                f = next(x for x in files if x.rel == c.file)
                inject_edges(nid, c, f)
                break

    # endpoints reachable from each VM / UseCase (narrowed to the methods actually called)
    def reach(src_id, depth=0, seen=None):
        seen = seen or set()
        res = set()
        for (s, t, k), e in g.edges.items():
            if s != src_id or k != "injects" or (s, t) in seen:
                continue
            seen.add((s, t))
            tk = g.nodes[t]["kind"]
            if tk == "repository":
                rn = g.nodes[t]["label"]
                ms = e.get("methods") or list(repo_methods[rn].keys())
                for mname in ms:
                    res |= repo_methods[rn].get(mname, set())
            elif tk == "api":
                an = g.nodes[t]["label"]
                for mname in (e.get("methods") or []):
                    if mname in api_methods[an]:
                        res.add(api_methods[an][mname])
            elif tk == "usecase" and depth < 3:
                res |= reach(t, depth + 1, seen)
        return res

    for nid, nd in g.nodes.items():
        if nd["kind"] in ("viewmodel", "usecase", "fragment", "activity"):
            r = reach(nid)
            if r:
                nd["endpoints"] = sorted(r)

    # ── deeplink layer (optional, project-specific routing code) ──
    helper_f = mapper_f = navi_f = launcher_f = route_f = None
    route_class = helper_object = launcher_var = None
    if dl:
        helper_f, mapper_f = find_file(dl["helper_file"]), find_file(dl["mapper_file"])
        navi_f, launcher_f, route_f = find_file(dl["navigator_file"]), find_file(dl["launcher_file"]), find_file(dl["route_file"])
        route_class, helper_object, launcher_var = dl["route_class"], dl["helper_object"], dl["launcher_var"]
        stats["deeplink_files_found"] = all([helper_f or not dl["helper_file"], mapper_f, navi_f, launcher_f, route_f])
        g.node("engine", "engine", "딥링크 엔진", desc=dl["engine_desc"] or None, file=mapper_f.rel if mapper_f else None)

    # host nodes
    for h, info in sorted(hosts.items()):
        schemes = sorted(info["schemes"])
        hid = f"host:{h}"
        g.node(hid, "host", f"{'/'.join(schemes)}://{h}", host=h, schemes=schemes, browsable=info["browsable"])
        g.edge(hid, activity_ids.get(info["activity"], "") or "", "entry")

    ind = " {%d}" % dl["when_indent"] if dl else ""
    mapper_stem = Path(dl["mapper_file"]).stem if dl else ""
    navi_stem = Path(dl["navigator_file"]).stem if dl else ""

    if route_f:
        for ci in route_f.classes:
            if ci.name == route_class or ci.name in dl["route_skip"]:
                continue
            g.node(f"route:{ci.name}", "route", ci.name, file=ci.file, line=ci.line)

    route_name_re = re.compile(re.escape(route_class) + r"\.(\w+)") if route_class else None
    if mapper_f:
        helper_fns = {}
        for m in re.finditer(r"private\s+fun\s+(\w+)\s*\(", mapper_f.skel):
            helper_fns[m.group(1)] = fn_body(mapper_f, m.group(1))
        # target constant → route (`when` arms of target_fn)
        mb = fn_body(mapper_f, dl["target_fn"])
        mb0 = (fn_spans(mapper_f, dl["target_fn"]) or [(0, 0)])[0][0]
        arm_re = re.compile(r"^" + ind + r"(\S[^\n]*?)\s*->", re.M)
        for label, arm, s_, e_ in split_arms(mb, arm_re, with_pos=True):
            if label == "else":
                continue
            tvals = []
            for part in [x for x in re.split(r",\s*", label) if x.strip()]:
                v = const_value(part.strip(), per_obj, glob)
                if v:
                    tvals.append(v)
            for tv in tvals:
                snip_extra.setdefault(f"target:{tv}", []).append({
                    "t": f"{mapper_stem}.{dl['target_fn']} 분기", "f": mapper_f.rel, "l": line_of(mapper_f.raw, mb0 + s_),
                    "s": textwrap.dedent(mapper_f.raw[mb0 + s_:mb0 + e_].rstrip())})
            routes = set(route_name_re.findall(arm))
            for hn, hb in helper_fns.items():
                if re.search(r"\b" + hn + r"\s*\(", arm):
                    routes |= set(route_name_re.findall(hb))
            for tv in tvals:
                tid = f"target:{tv}"
                g.node(tid, "target", tv)
                for r in sorted(routes):
                    if f"route:{r}" in g.nodes:
                        g.edge(tid, f"route:{r}", "maps_route")
        # host → target (`when` arms of host_fn)
        cb = fn_body(mapper_f, dl["host_fn"])
        cb0 = (fn_spans(mapper_f, dl["host_fn"]) or [(0, 0)])[0][0]
        hco = re.escape(dl["host_const_object"]) + r"\.\w+" if dl["host_const_object"] else r"[\w.]+"
        arm2 = re.compile(r"^" + ind + r"(" + hco + r"|else)\s*->", re.M)
        for label, arm, s_, e_ in split_arms(cb, arm2, with_pos=True):
            hv = const_value(label, per_obj, glob) if label != "else" else None
            if not hv or f"host:{hv}" not in g.nodes:
                continue
            snip_extra.setdefault(f"host:{hv}", []).append({
                "t": f"{mapper_stem}.{dl['host_fn']} 분기", "f": mapper_f.rel, "l": line_of(mapper_f.raw, cb0 + s_),
                "s": textwrap.dedent(mapper_f.raw[cb0 + s_:cb0 + e_].rstrip())})
            tgts = re.findall(re.escape(dl["target_param"]) + r"\s*=\s*([^,\n)]+)", arm)
            dyn = False
            for t in tgts:
                tv = const_value(t, per_obj, glob)
                if tv:
                    g.node(f"target:{tv}", "target", tv)
                    g.edge(f"host:{hv}", f"target:{tv}", "maps")
                else:
                    dyn = True
            if dyn:
                g.nodes[f"host:{hv}"]["dynamic_target"] = True
        for h in dl["dynamic_hosts"]:  # the target is a query parameter → decided at runtime
            if f"host:{h}" in g.nodes:
                g.nodes[f"host:{h}"]["dynamic_target"] = True
        for hid, nd in list(g.nodes.items()):
            if nd["kind"] == "host" and nd.get("dynamic_target"):
                for tid, td in g.nodes.items():
                    if td["kind"] == "target":
                        g.edge(hid, tid, "maps", conf="dynamic")

    # navigation patterns that count as "opens activity X" — comparisons like `cls == X::class` do not
    act_alt = "|".join(re.escape(s) for s in nm["activity_suffixes"])
    nav_pats = [
        re.compile(r"\bIntent\(\s*[\w.@<>]+\s*,\s*(\w+)::class"),
        re.compile(r"\bstartActivity\w*\(\s*[\w.@<>]+\s*,\s*(\w+)::class"),
        re.compile(r"\bstartActivity<(\w+)>"),
        re.compile(r"\b(\w+(?:" + act_alt + r"))\s*\.\s*(?:createIntent|newIntent|start\w*)\s*\("),
    ]
    frag_ref_re = re.compile(r"\b(\w+" + re.escape(fr_suf) + r")\b")

    def class_refs(text):
        acts = set()
        for pat in nav_pats:
            acts |= {m for m in pat.findall(text) if m in activity_ids}
        frs = {m for m in frag_ref_re.findall(text) if m in fragment_ids}
        return acts, frs

    # the helper object has many overloads; pick those compatible with the call-site arguments
    helper_ovs = defaultdict(list)  # fun name → [{"params": [(name, has default)], "body", "direct": {Activity}}]
    if helper_f:
        for m in re.finditer(r"\bfun\s+(\w+)\s*\(", helper_f.skel):
            pe = match_close(helper_f.skel, m.end() - 1, "(", ")")
            params = []
            for part in split_top(helper_f.text[m.end():pe]):
                pm = re.match(r"\s*(?:@\w+(?:\([^)]*\))?\s*)*(\w+)\s*:", part)
                if pm:
                    params.append((pm.group(1), "=" in part))
            k = pe + 1
            n_ = len(helper_f.skel)
            while k < n_ and helper_f.skel[k] not in "{=":
                k += 1
            body = ""
            if k < n_ and helper_f.skel[k] == "{":
                body = helper_f.text[k:match_close(helper_f.skel, k, "{", "}") + 1]
            elif k < n_:
                body = helper_f.text[k:k + 800]
            helper_ovs[m.group(1)].append({"params": params, "body": body, "direct": class_refs(body)[0]})

    def calls_with_args(text, prefix=r""):
        """`prefix NAME(args)` calls in text → [(NAME, args)]."""
        skel = strip_source(text)[1]
        out = []
        for m in re.finditer(prefix + r"\b(\w+)\s*\(", skel):
            e = match_close(skel, m.end() - 1, "(", ")")
            out.append((m.group(1), text[m.end():e]))
        return out

    def compatible(ov, args):
        parts = split_top(args) if args.strip() else []
        named = [re.match(r"\s*(\w+)\s*=(?!=)", x) for x in parts]
        names = {mm.group(1) for mm in named if mm}
        positional = sum(1 for mm in named if not mm)
        pnames = {pn for pn, _ in ov["params"]}
        required = sum(1 for _, dflt in ov["params"] if not dflt)
        return names <= pnames and positional <= len(ov["params"]) and positional + len(names) >= required

    generic_helpers = set(dl["generic_helpers"]) if dl else set()

    def helper_call_acts(name, args, seen=None):
        seen = seen if seen is not None else set()
        ovs = helper_ovs.get(name, [])
        if not ovs or name in generic_helpers:
            return set()
        sel = [o for o in ovs if compatible(o, args)] or ovs
        res = set()
        for o in sel:
            if id(o) in seen:
                continue
            seen.add(id(o))
            res |= o["direct"]
            for n2, a2 in calls_with_args(o["body"]):
                if n2 in helper_ovs and n2 != name:
                    res |= helper_call_acts(n2, a2, seen)
        return res

    def helper_acts_in(text):
        if not helper_object:
            return set()
        res = set()
        for n, a in calls_with_args(text, prefix=re.escape(helper_object) + r"\."):
            res |= helper_call_acts(n, a)
        return res

    launcher_targets = {}
    if launcher_f:
        for m in re.finditer(r"override\s+fun\s+(\w+)\s*\(", launcher_f.skel):
            name = m.group(1)
            body = fn_body(launcher_f, name)
            acts, frs = class_refs(body)
            acts |= helper_acts_in(body)
            for ext_cfg in dl["launcher_extensions"]:
                if re.search(ext_cfg["call"], body):
                    ext = find_file(ext_cfg["file"])
                    if ext:
                        acts |= class_refs(fn_body(ext, ext_cfg["fn"]))[0]
            ext_view = "ACTION_VIEW" in body
            launcher_targets[name] = (acts, frs, ext_view)
    if dl:
        g.node("external:browser", "external", "외부 앱/브라우저", desc=dl["external_desc"] or None)
    if navi_f:
        nb = fn_body(navi_f, dl["navigate_fn"])
        helper_arm_bodies = {n: fn_body(navi_f, n) for n in dl["navigate_helpers"]}
        arm_re = re.compile(r"^" + ind + r"(?:is\s+)?" + re.escape(route_class) + r"\.(\w+)\s*(?:->|\{)", re.M)
        launch_re = re.compile(re.escape(launcher_var) + r"\.(\w+)\s*\(")
        for label, arm in split_arms(nb, arm_re):
            rid = f"route:{label}"
            if rid not in g.nodes:
                continue
            calls = set(launch_re.findall(arm))
            for hn, hb in helper_arm_bodies.items():
                if re.search(r"\b" + hn + r"\s*\(", arm):
                    calls |= set(launch_re.findall(hb))
            for c in sorted(calls):
                acts, frs, ev = launcher_targets.get(c, (set(), set(), False))
                for a in sorted(acts):
                    g.edge(rid, activity_ids[a], "routes", via=c)
                for fr in sorted(frs):
                    g.edge(rid, fragment_ids[fr], "routes", via=c)
                if ev:
                    g.edge(rid, "external:browser", "routes", via=c)
    if dl:  # the engine resolves every target, whichever entry point was used
        for tid, td in g.nodes.items():
            if td["kind"] == "target":
                g.edge("engine", tid, "maps", conf="dynamic")

    # ── per-file UI links: hosts / vm ──
    ui_by_file = defaultdict(list)
    for f in files:
        for ci in f.classes:
            for pref in ("activity", "fragment"):
                nid = f"fragment:{ci.fqcn}" if pref == "fragment" else activity_ids.get(ci.name)
                if nid and nid in g.nodes and g.nodes[nid].get("file") == f.rel:
                    ui_by_file[f.rel].append(nid)
    for nid, nd in g.nodes.items():
        if nd["kind"] == "screen":
            ui_by_file[nd["file"]].append(nid)

    engine_re = re.compile(r"\b(?:" + "|".join(re.escape(x) for x in dl["engine_refs"]) + r")\b") if dl and dl["engine_refs"] else None
    vm_ref_re = re.compile(r"\b(\w+" + re.escape(vm_suf) + r")\b")
    screen_call_re = re.compile(r"\b(\w+" + re.escape(sc_suf) + r")\s*\(")
    rel_to_file = {f.rel: f for f in files}
    for rel, srcs in ui_by_file.items():
        f = rel_to_file[rel]
        activities_here = [s for s in srcs if g.nodes[s]["kind"] == "activity"]
        frag_here = [s for s in srcs if g.nodes[s]["kind"] == "fragment"]
        screens_here = [s for s in srcs if g.nodes[s]["kind"] == "screen"]
        txt = f.text
        host_srcs = activities_here + frag_here
        if host_srcs:
            for fm in set(frag_ref_re.findall(txt)):
                if fm in fragment_ids and fragment_ids[fm] not in host_srcs:
                    for s in host_srcs:
                        g.edge(s, fragment_ids[fm], "hosts")
            for sm in set(screen_call_re.findall(txt)):
                if sm in screen_ids and screen_ids[sm] not in screens_here:
                    for s in host_srcs:
                        g.edge(s, screen_ids[sm], "hosts")
        for vm in set(vm_ref_re.findall(txt)):
            if vm not in vm_ids:
                continue
            owners = []
            for s in activities_here + frag_here:
                owners.append(s)
            for s in screens_here:
                nm_ = g.nodes[s]["label"]
                sig = re.search(r"fun\s+" + nm_ + r"\s*\(", txt)
                if sig and vm in txt[sig.start():sig.start() + 900]:
                    owners.append(s)
            if not owners:
                owners = screens_here
            for s in owners:
                g.edge(s, vm_ids[vm], "vm")

    # ── navigates / uses: per file. Files without a UI class (shared components) → proximity heuristic → shared group ──
    containers_all = [n for n, nd in g.nodes.items() if nd["kind"] in ("activity", "fragment") and nd.get("file")]

    def owners_for(rel):
        """(owner nodes, conf, note)"""
        parsed = [n for n in ui_by_file.get(rel, [])]
        prim = ([n for n in parsed if g.nodes[n]["kind"] == "activity"]
                or [n for n in parsed if g.nodes[n]["kind"] == "fragment"]
                or [n for n in parsed if g.nodes[n]["kind"] == "screen"])
        if prim:
            return prim, "parsed", None
        segs = rel.split("/")
        best, best_len = [], 0
        for c in containers_all:
            csegs = g.nodes[c]["file"].split("/")
            common = 0
            for x, y in zip(segs[:-1], csegs[:-1]):
                if x != y:
                    break
                common += 1
            if common > best_len:
                best, best_len = [c], common
            elif common == best_len:
                best.append(c)
        if best_len >= base_depth + 1 and best:  # at least the feature package below the app root matches
            acts_ = [c for c in best if g.nodes[c]["kind"] == "activity"]
            return (acts_ or best), "heuristic", f"{rel.split('/')[-1]} (패키지 근접 추정)"
        top = segs[base_depth] if len(segs) > base_depth + 1 else segs[-2]
        gid = f"shared:{top}"
        g.node(gid, "shared", f"공유: {top}", note="화면에 귀속되지 않는 공용 코드(특정 화면으로 특정 불가)")
        return [gid], "heuristic", f"{rel.split('/')[-1]} (공용 코드)"

    engine_files = {x.rel for x in (navi_f, mapper_f, launcher_f) if x}
    engine_dirs = [app_base + d for d in dl["engine_dirs"]] if dl else []
    default_via = f"{helper_object}/Intent" if helper_object else "Intent"
    for f in files:
        if f.module != mods["app"]:
            continue
        txt = f.text
        acts, _ = class_refs(txt)
        acts |= helper_acts_in(txt)
        uses_engine = bool(engine_re and engine_re.search(txt)) and f.rel not in engine_files
        if not acts and not uses_engine:
            continue
        if any(f.rel.startswith(d) for d in engine_dirs) or (helper_f and f.rel == helper_f.rel):
            continue  # the routing engine itself — route → activity is already parsed exactly
        owners, conf, why = owners_for(f.rel)
        for o in owners:
            for a in acts:
                g.edge(o, activity_ids[a], "navigates", conf=conf, via=why or default_via)
            if uses_engine:
                g.edge(o, "engine", "uses", conf=conf, via=why)

    # ── constant endpoints: a class that names a constant calls it directly (no ViewModel/UseCase layer in between) ──
    # Pass 1 scans every app class for constant names. A non-screen class that names constants is a *hub* (a helper that
    # wraps the network call); its members are split so that `Hub.member(` is worth exactly that member's constants.
    # Pass 2 adds those hub-derived endpoints to the callers, then emits the edges for direct and hub references alike.
    if const_eps:
        name_re = re.compile(r"\b(" + "|".join(re.escape(x) for x in sorted(const_eps, key=len, reverse=True)) + r")\b")
        verb_re = re.compile(ec["verb_near"]) if ec.get("verb_near") else None
        scanned = []  # (file, class, skeleton body, {constant name: verbs}, owner node or None)
        hub_methods = defaultdict(lambda: defaultdict(dict))  # hub class → member → {endpoint id: verbs}
        for f in files:
            if f.module != mods["app"]:
                continue
            for ci in f.classes:
                if ci.kind != "class" or f"api:{ci.name}" in g.nodes:
                    continue
                body = f.skel[ci.body[0]:ci.body[1] + 1]  # skeleton: string contents blanked, comments gone
                hits, refs = defaultdict(set), []
                for m in name_re.finditer(body):
                    before = re.sub(r"\w+\.$", "", body[max(0, m.start() - 160):m.start()])  # drop the `Owner.` qualifier
                    vm_ = verb_re.search(before) if verb_re else None
                    verbs = {vm_.group(1).upper()} if vm_ else set()
                    hits[m.group(1)] |= verbs
                    refs.append((m.start(), m.group(1), verbs))
                own = f"fragment:{ci.fqcn}" if f"fragment:{ci.fqcn}" in g.nodes else activity_ids.get(ci.name)
                scanned.append((f, ci, body, hits, own))
                if hits and not own:
                    for mname, s, e in member_spans(body, 0, len(body)):
                        for pos, name, verbs in refs:
                            if s <= pos < e:
                                hub_methods[ci.name][mname].setdefault(const_eps[name], set()).update(verbs)
        stats["endpoint_hubs"] = {h: len(ms) for h, ms in sorted(hub_methods.items())}
        sub_of = defaultdict(list)  # class name → direct subclasses in the app module
        for f in files:
            if f.module == mods["app"]:
                for ci in f.classes:
                    for s in ci.supers:
                        sub_of[s].append(ci)

        def screen_heirs(name, seen):
            """Screen nodes among the descendants of class `name` — an abstract base screen's calls belong to them."""
            res = []
            for c in sub_of.get(name, []):
                if c.name in seen:
                    continue
                seen.add(c.name)
                nid = f"fragment:{c.fqcn}" if f"fragment:{c.fqcn}" in g.nodes else activity_ids.get(c.name)
                res += [nid] if nid else []
                res += screen_heirs(c.name, seen)
            return res
        hub_re = re.compile(r"\b(" + "|".join(re.escape(h) for h in sorted(hub_methods, key=len, reverse=True))
                            + r")\s*\.\s*(?:\w+\s*\(\s*\)\s*\.\s*)*(\w+)\s*\(") if hub_methods else None  # Hub.m( / Hub.getInstance().m(
        for f, ci, body, hits, own in scanned:
            hub_hits, hub_via = defaultdict(set), defaultdict(list)  # endpoint id → verbs / → ["Hub.member"]
            if hub_re:
                for hm in hub_re.finditer(body):
                    hub, member = hm.group(1), hm.group(2)
                    for eid, verbs in hub_methods[hub].get(member, {}).items():
                        hub_hits[eid] |= verbs
                        if f"{hub}.{member}" not in hub_via[eid]:
                            hub_via[eid].append(f"{hub}.{member}")
            if not hits and not hub_hits:
                continue
            heirs = [] if own else screen_heirs(ci.name, set())
            if own:
                owners, conf, why = [own], "parsed", None
            elif heirs:
                owners, conf, why = sorted(set(heirs)), "parsed", f"{ci.name} (상속)"
            else:
                owners, conf, why = owners_for(f.rel)
            for o in owners:
                for name, verbs in hits.items():
                    g.edge(o, const_eps[name], "calls", conf=conf, via=why, methods=sorted(verbs))
                for eid, verbs in hub_hits.items():
                    g.edge(o, eid, "calls", conf=conf, via=why, methods=sorted(verbs))
                    for v in hub_via[eid]:
                        g.edge(o, eid, "calls", conf=conf, via=v)
                g.nodes[o]["endpoints"] = sorted(set(g.nodes[o].get("endpoints", [])) | {const_eps[x] for x in hits} | set(hub_hits))

    # screens not called from any UI file → nearest container by package
    hosted = {e["t"] for e in g.edges.values() if e["kind"] == "hosts"}
    containers = [n for n, nd in g.nodes.items() if nd["kind"] in ("activity", "fragment") and nd.get("file")]
    for nid, nd in g.nodes.items():
        if nd["kind"] != "screen" or nid in hosted:
            continue
        sdir = nd["file"].rsplit("/", 1)[0]
        best, best_len = [], 0
        for c in containers:
            cdir = g.nodes[c]["file"].rsplit("/", 1)[0]
            common = 0
            for a, b in zip(sdir.split("/"), cdir.split("/")):
                if a != b:
                    break
                common += 1
            if common > best_len + 0:
                best, best_len = [c], common
            elif common == best_len and common > 0:
                best.append(c)
        if len(best) == 1 and best_len >= base_depth:  # must share at least the app root package
            g.edge(best[0], nid, "hosts", conf="heuristic")

    # ── performance instrumentation coverage (optional): trace constants + start call → the file's UI node ──
    if pf:
        trace_const_re = re.compile(r'const\s+val\s+' + re.escape(pf["const_prefix"]) + r'\w+\s*=\s*"([a-z][a-z0-9_]*)"')
        start_re = re.compile(pf["start_call"])
        kinds_map = pf["kinds"]
        stats["perf_traces"] = {}
        for f in files:
            if f.module != mods["app"] or not start_re.search(f.text):
                continue
            names = sorted(set(trace_const_re.findall(f.text)))
            if not names:
                continue
            owners = ui_by_file.get(f.rel, [])
            prim = ([n for n in owners if g.nodes[n]["kind"] == "activity"]
                    or [n for n in owners if g.nodes[n]["kind"] == "fragment"]
                    or [n for n in owners if g.nodes[n]["kind"] == "screen"])
            for nid in prim:
                nd = g.nodes[nid]
                nd["traces"] = sorted(set(nd.get("traces", [])) | set(names))
                nd["trace_kinds"] = {t: next((k for suf, k in kinds_map.items() if t.endswith(suf)), "other") for t in nd["traces"]}
            for t in names:
                stats["perf_traces"][t] = prim[0] if prim else None

    # ── code snippets shown on click ──
    code = {}

    def add(nid, title, f, a, b, line=None):
        code.setdefault(nid, []).append({"t": title, "f": f.rel, "l": line_of(f.raw, a), "s": f.raw[a:b].rstrip()})

    file_by_rel = {f.rel: f for f in files}

    def class_snip(nid, ci, title):
        f = file_by_rel[ci.file]
        add(nid, title, f, extend_up(f, ci.start), ci.end + 1)

    for nid, nd in g.nodes.items():
        k = nd["kind"]
        if k in ("activity", "fragment", "viewmodel", "usecase", "repository", "api") and nd.get("fqcn"):
            ci = next((c for c in by_name.get(nd["label"], []) if c.fqcn == nd["fqcn"]), None)
            if ci:
                class_snip(nid, ci, "인터페이스" if ci.kind == "interface" else "클래스")
        if k == "repository":
            impl = impl_of.get(nd["label"])
            if impl:
                class_snip(nid, impl, "구현체 " + impl.name)
        elif k == "route" and route_f:
            ci = next((c for c in route_f.classes if c.name == nd["label"]), None)
            if ci:
                class_snip(nid, ci, f"{route_class} 정의")
        elif k == "screen" and nd.get("file") in file_by_rel:
            f = file_by_rel[nd["file"]]
            off = sum(len(x) + 1 for x in f.raw.split("\n")[:nd["line"] - 1])
            spans = [sp for sp in fn_spans(f, nd["label"]) if sp[0] >= off]
            if spans:
                add(nid, "Composable 함수", f, extend_up(f, off), spans[0][1], line=nd["line"])
        elif k == "endpoint" and nd.get("const") and nd.get("file") in file_by_rel:
            f = file_by_rel[nd["file"]]
            off = sum(len(x) + 1 for x in f.raw.split("\n")[:nd["line"] - 1])
            nl = f.raw.find("\n", off)
            add(nid, "엔드포인트 상수", f, extend_up(f, off), nl if nl > 0 else len(f.raw), line=nd["line"])
        elif k == "endpoint" and nd.get("file") in file_by_rel:
            f = file_by_rel[nd["file"]]
            off = sum(len(x) + 1 for x in f.raw.split("\n")[:nd["line"] - 1])
            fm = re.compile(r"\bfun\s+" + re.escape(nd["fun"]) + r"\s*\(").search(f.skel, off)
            if fm:
                pe = match_close(f.skel, fm.end() - 1, "(", ")")
                nl = f.raw.find("\n", pe)
                add(nid, "Retrofit 선언", f, extend_up(f, off), nl if nl > 0 else pe + 1, line=nd["line"])
        elif k == "engine" and dl:
            for ff, fn, title in ((mapper_f, dl["target_fn"], f"target → {route_class} ({mapper_stem})"),
                                  (navi_f, dl["navigate_fn"], f"{route_class} → 화면 실행 ({navi_stem})")):
                if ff:
                    sp = fn_spans(ff, fn)
                    if sp:
                        hdr = ff.raw.rfind("\n", 0, ff.skel.rfind("fun " + fn, 0, sp[0][0]))
                        add(nid, title, ff, extend_up(ff, hdr + 1), sp[0][1])
    for nid, lst in snip_extra.items():
        code.setdefault(nid, []).extend(lst)
    stats["code_snippets"] = sum(len(v) for v in code.values())
    stats["code_bytes"] = sum(len(x["s"].encode()) for v in code.values() for x in v)

    # ── meta ──
    def git(*a):
        try:
            return subprocess.check_output(["git", "-C", str(repo), *a], text=True, stderr=subprocess.DEVNULL).strip()
        except Exception:  # noqa: BLE001
            return ""
    ref = ref or {}
    meta = {
        "repo": ref.get("repo") or repo.name, "branch": ref.get("branch") or git("branch", "--show-current"),
        "commit": ref.get("commit") or git("rev-parse", "--short", "HEAD"),
        "dirty": False if ref.get("commit") else bool(git("status", "--porcelain")),
        "generated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "stats": stats,
    }
    nodes = sorted(g.nodes.values(), key=lambda n: (n["kind"], n["id"]))
    edges = sorted(g.edges.values(), key=lambda e: (e["kind"], e["s"], e["t"]))
    kinds = defaultdict(int)
    for n in nodes:
        kinds[n["kind"]] += 1
    meta["counts"] = dict(kinds)
    return {"meta": meta, "nodes": nodes, "edges": edges}, code
