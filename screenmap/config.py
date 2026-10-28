"""Project configuration: generic defaults + an optional JSON file (`screenmap.json` in the repo root, or --config).

Everything the extractor assumes about a code base lives here, so the same core can read different apps.
Keys starting with `_` are ignored (use them for comments). `python3 -m screenmap config --dump` prints the merged result.
"""
import copy
import json
import re
from pathlib import Path

DEFAULTS = {
    "title": "Screen Map",
    "package": None,  # application id / root package. null → read from the manifest `package` attr, then `namespace` in the app module's build.gradle(.kts)
    "modules": {"app": "app", "domain": "domain", "data": "data"},
    "manifest": None,  # null → <app module>/src/main/AndroidManifest.xml
    "source_roots": ["src/main/java", "src/main/kotlin"],
    "exclude_dirs": ["buildSrc", "build", "test", "androidTest", ".gradle", "node_modules"],
    "ignore_scheme_prefixes": [],  # manifest intent-filters whose schemes all start with one of these are not app deeplinks (SDK callbacks)
    "naming": {
        "usecase": "Use[cC]ase$",          # regex on the class name (domain or app module)
        "repository": "Repo(?:sitory)?$",  # regex on domain-module interface names (`FooRepository`, `IFooRepo`)
        "datasource": "DataSource$",       # regex on data-module interface names
        "viewmodel_suffix": "ViewModel",
        "fragment_suffix": "Fragment",
        "screen_suffix": "Screen",         # `@Composable fun XxxScreen(`
        "activity_suffixes": ["Activity"],  # class-name suffixes recognised in `X.createIntent(`-style navigation calls
        "debug_path": "/debug/",           # files under this path segment are debug-only UI (hidden by default)
    },
    # Optional: a hand-written routing layer (host → target constant → route → navigator → launcher → helper → screen).
    # null → skipped; manifest host → receiving activity `entry` edges are still produced.
    "deeplink": None,
    # Optional: Firebase Performance style instrumentation detection. null → skipped.
    "perf": None,
    # Optional: endpoints declared as constants instead of Retrofit annotations (legacy apps: `static final Url X = new Url("/path")`).
    # null → skipped. Classes that reference a constant get a direct `calls` edge to it — no ViewModel/UseCase layer required.
    "endpoints": None,
    # Repository folder names this config belongs to. Lets `adapter_for()` (the one-click launcher) find a config kept in
    # projects/<adapter>/ when the adapter folder is not named like the repository folder (clones, forks).
    "repo_names": [],
}

DEEPLINK_KEYS = {
    "mapper_file": None,               # path suffix of the file that maps host → target and target → route
    "host_fn": None,                   # fun that switches on the host constant and sets the target
    "target_fn": None,                 # fun that switches on the target constant and returns a route
    "target_param": None,              # named argument that carries the target in host_fn (e.g. `linkTarget = Const.X`)
    "host_const_object": None,         # object holding the host constants referenced in host_fn's `when`
    "route_file": None,                # path suffix of the sealed route class file
    "route_class": None,               # the sealed base class name (its subclasses become `route:` nodes)
    "route_skip": [],                  # other classes in that file to ignore
    "navigator_file": None,            # path suffix of the navigator
    "navigate_fn": None,               # fun whose `when (route)` calls launcher methods
    "navigate_helpers": [],            # private helpers inside the navigator whose bodies also call the launcher
    "launcher_file": None,             # path suffix of the launcher (override fun per destination)
    "launcher_var": "launcher",        # receiver name used in the navigator (`launcher.openX()`)
    "helper_file": None,               # path suffix of the static helper object with many overloads
    "helper_object": None,             # its object name as used at call sites (`Helper.startX(`)
    "generic_helpers": ["startActivity"],  # helper funs that take the class as an argument (call site already names the destination)
    "launcher_extensions": [],         # [{"call": regex, "file": path suffix, "fn": name}] extension funs the launcher delegates to
    "engine_refs": [],                 # identifiers whose presence in a UI file means "uses the routing engine"
    "engine_dirs": [],                 # app-package-relative dirs that *are* the engine (skipped when attributing navigation)
    "dynamic_hosts": [],               # hosts whose target is a runtime parameter → dynamic edges to every target
    "when_indent": 16,                 # indentation (spaces) of `when` arms inside host_fn/target_fn/navigate_fn (ktlint style)
    "engine_desc": "",                 # description shown on the engine node
    "external_desc": "Intent.ACTION_VIEW → external app / browser",
}

PERF_KEYS = {
    "start_call": None,   # regex of the call that starts a trace, e.g. "\\bPerfTrace\\s*\\.\\s*(start|startFromTap)\\s*\\("
    "const_prefix": "TRACE_",  # `const val TRACE_X = "name"` constants give the trace names
    "kinds": {"_view_ready": "view_ready", "_load": "load"},  # trace-name suffix → kind (anything else: "other")
}


ENDPOINT_KEYS = {
    "decl": None,  # regex with named groups `name` and `path`, matched in every source file; the enclosing class becomes the `api` node
    "verb_near": None,  # optional regex with group 1 = HTTP verb, searched in the 160 chars before a reference (e.g. "\\.(get|post)\\(\\s*$")
}


class ConfigError(Exception):
    pass


def _strip_comments(d):
    if isinstance(d, dict):
        return {k: _strip_comments(v) for k, v in d.items() if not str(k).startswith("_")}
    return d


def _merge(base, over, path=""):
    out = copy.deepcopy(base)
    for k, v in over.items():
        if k not in base:
            raise ConfigError(f"unknown config key: {path}{k}")
        if isinstance(base[k], dict) and isinstance(v, dict):
            out[k] = _merge(base[k], v, path + k + ".")
        else:
            out[k] = copy.deepcopy(v)
    return out


def load(config_path=None, repo=None):
    """Merged config dict. Looks for --config, else <repo>/screenmap.json, else defaults only."""
    raw = {}
    p = None
    if config_path:
        p = Path(config_path).expanduser()
    elif repo and (Path(repo) / "screenmap.json").exists():
        p = Path(repo) / "screenmap.json"
    if p:
        try:
            raw = _strip_comments(json.loads(p.read_text(encoding="utf-8")))
        except json.JSONDecodeError as e:
            raise ConfigError(f"{p}: invalid JSON — {e}")
    base = copy.deepcopy(DEFAULTS)
    cfg = _merge(base, {k: v for k, v in raw.items() if k not in ("deeplink", "perf", "endpoints")})
    cfg["deeplink"] = _merge(DEEPLINK_KEYS, raw["deeplink"], "deeplink.") if raw.get("deeplink") else None
    cfg["perf"] = _merge(PERF_KEYS, raw["perf"], "perf.") if raw.get("perf") else None
    cfg["endpoints"] = _merge(ENDPOINT_KEYS, raw["endpoints"], "endpoints.") if raw.get("endpoints") else None
    cfg["_source"] = str(p) if p else None
    if cfg["deeplink"]:
        missing = [k for k in ("mapper_file", "host_fn", "target_fn", "target_param", "route_file", "route_class",
                               "navigator_file", "navigate_fn", "launcher_file") if not cfg["deeplink"].get(k)]
        if missing:
            raise ConfigError("deeplink section needs: " + ", ".join(missing))
    if cfg["perf"] and not cfg["perf"].get("start_call"):
        raise ConfigError("perf section needs: start_call")
    if cfg["endpoints"]:
        decl = cfg["endpoints"].get("decl") or ""
        if "(?P<name>" not in decl or "(?P<path>" not in decl:
            raise ConfigError("endpoints.decl needs a regex with named groups `name` and `path`")
    if not isinstance(cfg["repo_names"], list) or not all(isinstance(x, str) for x in cfg["repo_names"]):
        raise ConfigError("repo_names must be a list of repository folder names")
    return cfg


def adapter_for(repo_name, projects_dir, warn=None):
    """Path of the adapter config for a repository folder name, or None.
    Scans <projects_dir>/*/*.screenmap.json; a config matches when its folder is named `repo_name` or its `repo_names`
    lists it. Two matching adapters → ConfigError. An unreadable config is skipped (reported through `warn(msg)`)."""
    found = []
    for p in sorted(Path(projects_dir).glob("*/*.screenmap.json")):
        try:
            names = json.loads(p.read_text(encoding="utf-8")).get("repo_names") or []
        except (OSError, ValueError, AttributeError) as e:
            if warn:
                warn(f"adapter config skipped: {p}: {e}")
            continue
        if p.parent.name == repo_name or (isinstance(names, list) and repo_name in names):
            found.append(p)
    if len(found) > 1:
        raise ConfigError(f"{repo_name}: more than one adapter claims it: " + ", ".join(str(x) for x in found))
    return found[0] if found else None


def resolve_package(repo, cfg, manifest_root=None):
    """Root package: config → manifest `package` attr → `namespace` in the app module's build.gradle(.kts)."""
    if cfg.get("package"):
        return cfg["package"]
    if manifest_root is not None and manifest_root.get("package"):
        return manifest_root.get("package")
    app = Path(repo) / cfg["modules"]["app"]
    for name in ("build.gradle.kts", "build.gradle"):
        gp = app / name
        if gp.exists():
            m = re.search(r"namespace\s*[=(]?\s*['\"]([\w.]+)['\"]", gp.read_text(encoding="utf-8", errors="replace"))
            if m:
                return m.group(1)
    raise ConfigError("cannot determine the app package — set \"package\" in screenmap.json")


def dump(cfg):
    return json.dumps({k: v for k, v in cfg.items() if not k.startswith("_")}, ensure_ascii=False, indent=2)
