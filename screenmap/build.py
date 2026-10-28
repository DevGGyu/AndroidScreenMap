"""graph.json + code.json (+ optional perf json) + viewer template → one self-contained HTML file."""
import html
import json
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parent / "viewer.template.html"
PLACEHOLDERS = ("__TITLE__", "__GRAPH_JSON__", "__CODE_JSON__", "__PERF_JSON__", "__SCRAPE_UI__")


def _inline(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def render(outdir, title, perf_path=None, name="screen_map", artifact=False, scrape_ui=False, template=None):
    """Write <outdir>/<name>.html (and <name>.artifact.html — a fragment without the document skeleton — when `artifact`).
    Returns the list of written paths."""
    outdir = Path(outdir)
    tpl = Path(template or TEMPLATE).read_text(encoding="utf-8")
    for ph in PLACEHOLDERS:
        assert ph in tpl, f"template is missing {ph}"
    graph = json.loads((outdir / "graph.json").read_text(encoding="utf-8"))
    code = json.loads((outdir / "code.json").read_text(encoding="utf-8"))
    perf = json.loads(Path(perf_path).read_text(encoding="utf-8")) if perf_path and Path(perf_path).exists() else None
    page = (tpl.replace("__TITLE__", html.escape(title))
            .replace("__GRAPH_JSON__", _inline(graph))
            .replace("__CODE_JSON__", _inline(code))
            .replace("__PERF_JSON__", _inline(perf))
            .replace("__SCRAPE_UI__", "1" if scrape_ui else "0"))
    head, body = page.split("<!--HEAD-END-->", 1)
    written = []
    out = outdir / f"{name}.html"
    out.write_text(
        '<!doctype html>\n<html lang="ko">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">\n'
        + head + "</head>\n<body>\n" + body + "\n</body>\n</html>\n",
        encoding="utf-8",
    )
    written.append(out)
    if artifact:
        art = outdir / f"{name}.artifact.html"
        art.write_text(page, encoding="utf-8")
        written.append(art)
    return written, perf
