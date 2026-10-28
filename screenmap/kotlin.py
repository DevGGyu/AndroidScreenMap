"""Lightweight Kotlin source reader — no compiler, no LSP.

Regex + bracket matching over a "skeleton" of the file (comments and string contents blanked
out, so offsets stay identical to the original). Good enough for the shapes this tool needs:
class/interface declarations with their constructors and supertypes, `const val` expressions,
function bodies, `when` arms and call sites. Not a Kotlin parser.
"""
import re
from pathlib import Path


# ───────────────────────── source preprocessing ─────────────────────────
def strip_source(src):
    """Return (comment-free text, skeleton). The skeleton also blanks string contents.
    Both keep the exact length and newline positions of the original."""
    n = len(src)
    out_c, out_s = [], []
    i = 0
    blank = lambda s: "".join(ch if ch == "\n" else " " for ch in s)  # noqa: E731
    while i < n:
        c = src[i]
        nx = src[i + 1] if i + 1 < n else ""
        if c == "/" and nx == "/":
            j = src.find("\n", i)
            j = n if j < 0 else j
            out_c.append(" " * (j - i))
            out_s.append(" " * (j - i))
            i = j
        elif c == "/" and nx == "*":
            j = src.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out_c.append(blank(src[i:j]))
            out_s.append(blank(src[i:j]))
            i = j
        elif src.startswith('"""', i):
            j = src.find('"""', i + 3)
            j = n if j < 0 else j + 3
            seg = src[i:j]
            out_c.append(seg)
            out_s.append('"""' + blank(seg[3:-3]) + '"""' if len(seg) >= 6 else blank(seg))
            i = j
        elif c == '"':
            j = i + 1
            while j < n and src[j] != '"' and src[j] != "\n":
                if src[j] == "\\":
                    j += 1
                j += 1
            j = min(j + 1, n)
            seg = src[i:j]
            out_c.append(seg)
            out_s.append('"' + " " * (len(seg) - 2) + '"' if len(seg) >= 2 else blank(seg))
            i = j
        elif c == "'":
            m = re.compile(r"'(?:\\.[^']{0,5}|[^'\\])'").match(src, i)
            if m:
                out_c.append(m.group(0))
                out_s.append(" " * len(m.group(0)))
                i = m.end()
            else:
                out_c.append(c)
                out_s.append(c)
                i += 1
        else:
            out_c.append(c)
            out_s.append(c)
            i += 1
    return "".join(out_c), "".join(out_s)


def match_close(skel, pos, open_ch, close_ch):
    """Index of the bracket matching skel[pos] == open_ch; len(skel)-1 if unbalanced."""
    depth = 0
    for i in range(pos, len(skel)):
        ch = skel[i]
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return i
    return len(skel) - 1


def split_top(text, sep=","):
    """Split on `sep` at bracket depth 0."""
    parts, depth, cur = [], 0, []
    for ch in text:
        if ch in "(<[{":
            depth += 1
        elif ch in ")>]}":
            depth -= 1
        if ch == sep and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    if "".join(cur).strip():
        parts.append("".join(cur))
    return parts


def line_of(text, pos):
    return text.count("\n", 0, pos) + 1


# ───────────────────────── file / class index ─────────────────────────
DECL = re.compile(
    r"^[ \t]*(?:@[\w.]+(?:\([^)]*\))?[ \t]*)*"
    r"((?:(?:public|internal|private|protected|abstract|open|sealed|data|enum|annotation|inner|value|expect|actual|static|final|strictfp)\s+)*)"
    r"(class|interface|object)\s+(\w+)",
    re.M,
)
CTOR_KW = re.compile(r"(?:(?:@\w+(?:\([^)]*\))?|private|internal|protected|public)\s+)*constructor\s*(?=\()")


class ClassInfo:
    __slots__ = ("name", "kind", "mods", "annos", "ctor", "supers", "body", "file", "line", "pkg", "module",
                 "start", "end", "fqcn")


class SrcFile:
    def __init__(self, path, rel, text, skel):
        self.path, self.rel, self.text, self.skel = path, rel, text, skel
        self.pkg = ""
        m = re.search(r"^package\s+([\w.]+)", skel, re.M)
        if m:
            self.pkg = m.group(1)
        self.module = rel.split("/")[0]
        self.lang = "java" if rel.endswith(".java") else "kotlin"
        self.classes = []
        self.raw = text


def parse_classes(f):
    out = []
    skel, text = f.skel, f.text
    for m in DECL.finditer(skel):
        ci = ClassInfo()
        ci.name, ci.kind = m.group(3), m.group(2)
        ci.mods = set(m.group(1).split())
        line_start = skel.rfind("\n", 0, m.start()) + 1
        # annotations on the same line and on the lines directly above
        pre = skel[max(0, line_start - 400):m.start()] + skel[m.start():m.start(1) if m.group(1) else m.end()]
        annos = set(re.findall(r"@(\w+)", pre.split("\n\n")[-1]))
        annos |= set(re.findall(r"@(\w+)", skel[line_start:m.end()]))
        ci.annos = annos
        i = m.end()
        n = len(skel)
        j = i
        while j < n and skel[j] in " \t\r\n":
            j += 1
        if j < n and skel[j] == "<":
            j = match_close(skel, j, "<", ">") + 1
        k = j
        while k < n and skel[k] in " \t\r\n":
            k += 1
        ci.ctor = ""
        sup = ""
        if f.lang == "java":
            # `class X<T> extends A<B> implements C, D {` — no primary constructor; the header ends at `{` (or `;`)
            depth, p = 0, k
            while p < n:
                ch = skel[p]
                if ch in "(<":
                    depth += 1
                elif ch in ")>":
                    depth -= 1
                elif ch in "{;" and depth == 0:
                    break
                p += 1
            sup = re.sub(r"\b(?:extends|implements|permits)\b", " ", text[k:p])
            k = p
        else:
            km = CTOR_KW.match(skel, k)
            if km:
                k = km.end()
            if k < n and skel[k] == "(":
                e = match_close(skel, k, "(", ")")
                ci.ctor = text[k + 1:e]
                j = e + 1
            k = j
            while k < n and skel[k] in " \t\r\n":
                k += 1
            if k < n and skel[k] == ":":
                depth, p = 0, k + 1
                while p < n:
                    ch = skel[p]
                    if ch in "(<":
                        depth += 1
                    elif ch in ")>":
                        depth -= 1
                    elif ch == "{" and depth == 0:
                        break
                    elif ch == "\n" and depth == 0:
                        nxt = skel[p + 1:p + 200].lstrip()
                        prev = skel[k + 1:p].strip()
                        # `class X :` with a line break right after the colon — supertypes continue on the next line
                        if prev and not (prev.endswith(",") or nxt.startswith(",") or nxt.startswith("{")):
                            break
                    p += 1
                sup = text[k + 1:p]
                k = p
        hdr_end = k
        ci.supers = set(re.findall(r"\b([A-Z]\w*)", sup))
        while k < n and skel[k] in " \t\r\n":
            k += 1
        if k < n and skel[k] == "{":
            e = match_close(skel, k, "{", "}")
            ci.body = (k, e)
        else:
            ci.body = (hdr_end, hdr_end)  # a body-less declaration owns only its own header
        ci.start, ci.end = m.start(), ci.body[1]
        ci.file, ci.line, ci.pkg, ci.module = f.rel, line_of(text, m.start()), f.pkg, f.module
        ci.fqcn = (f.pkg + "." if f.pkg else "") + ci.name
        out.append(ci)
    f.classes = out


def ctor_params(ctor_text):
    """[(param name, simple type name)] of a primary constructor."""
    res = []
    for part in split_top(ctor_text):
        part = re.sub(r"@\w+(?:\([^)]*\))?\s*", "", part).strip()
        mm = re.match(r"(?:(?:private|public|internal|protected|override|val|var)\s+)*(\w+)\s*:\s*([\w.]+)", part)
        if mm:
            res.append((mm.group(1), mm.group(2).split(".")[-1]))
    return res


def read_file(path, rel):
    raw = Path(path).read_text(encoding="utf-8", errors="replace")
    text, skel = strip_source(raw)
    f = SrcFile(path, rel, text, skel)
    f.raw = raw  # original with comments — offsets identical to text/skel
    parse_classes(f)
    return f


def collect_files(repo, exclude_parts):
    """All `.kt` and `.java` files under `<module>/src/main/`, skipping excluded directory names."""
    files = []
    for p in sorted(list(Path(repo).rglob("*.kt")) + list(Path(repo).rglob("*.java"))):
        rel = p.relative_to(repo).as_posix()
        parts = rel.split("/")
        if any(x in exclude_parts for x in parts[:-1]) or "/src/main/" not in "/" + rel:
            continue
        files.append(read_file(p, rel))
    return files


# ───────────────────────── constants ─────────────────────────
def resolve_consts(files):
    """Collect `const val X = <expr>` per owning object/file → ({owner: {X: expr}}, {X: expr | None if ambiguous})."""
    from collections import defaultdict
    per_obj = defaultdict(dict)
    glob = {}
    pat = re.compile(r"const\s+val\s+(\w+)\s*(?::\s*\w+)?\s*=\s*([^\n]+)")
    for f in files:
        for m in pat.finditer(f.text):
            owner = ""
            best = None
            for ci in f.classes:
                if ci.start <= m.start() < ci.end and (best is None or ci.start > best.start):
                    best = ci
            if best is not None:
                owner = best.name
            expr = m.group(2).strip()
            per_obj[owner][m.group(1)] = expr
            glob[m.group(1)] = None if m.group(1) in glob and glob[m.group(1)] != expr else expr
    return per_obj, glob


def _split_plus(expr):
    parts, cur, q = [], [], False
    i = 0
    while i < len(expr):
        ch = expr[i]
        if ch == "\\" and q:
            cur.append(ch + expr[i + 1:i + 2])
            i += 2
            continue
        if ch == '"':
            q = not q
        if ch == "+" and not q:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
        i += 1
    parts.append("".join(cur))
    return [p.strip() for p in parts]


def const_value(expr, per_obj, glob, owner="", depth=0):
    """Evaluate a constant expression to a string: templates ($X), aliases (X = Y), concatenation (+).
    None when it cannot be fully resolved."""
    if depth > 8:
        return None
    expr = expr.strip()
    out = []
    for part in _split_plus(expr):
        m = re.fullmatch(r'"([^"]*)"', part)
        if m:
            lit, ok = m.group(1), True

            def sub(mm):
                nonlocal ok
                name = mm.group(1) or mm.group(2)
                v = const_value(name, per_obj, glob, owner, depth + 1)
                if v is None:
                    ok = False
                    return ""
                return v
            lit = re.sub(r"\$\{(\w+)\}|\$(\w+)", sub, lit)
            if not ok:
                return None
            out.append(lit)
            continue
        m = re.fullmatch(r"(?:(\w+)\.)?(\w+)(\.name)?", part)
        if not m:
            return None
        obj, name, dot_name = m.group(1), m.group(2), m.group(3)
        if dot_name and obj:  # Enum.ENTRY.name → ENTRY
            out.append(name)
            continue
        raw = None
        new_owner = owner
        if obj and obj in per_obj and name in per_obj[obj]:
            raw, new_owner = per_obj[obj][name], obj
        elif not obj and name in per_obj.get(owner, {}):
            raw = per_obj[owner][name]
        elif name in glob and glob[name] is not None:
            raw = glob[name]
            new_owner = next((o for o, d in per_obj.items() if d.get(name) == raw), owner)
        if raw is None:
            return None
        v = const_value(raw, per_obj, glob, new_owner, depth + 1)
        if v is None:
            return None
        out.append(v)
    return "".join(out)


# ───────────────────────── functions and `when` arms ─────────────────────────
def split_arms(body, arm_re, with_pos=False):
    """Split `body` at matches of arm_re (label in group 1) → [(label, arm body)] or, with_pos, (label, body, start, end)."""
    marks = [(m.start(), m.end(), m.group(1)) for m in arm_re.finditer(body)]
    arms = []
    for idx, (s, e, label) in enumerate(marks):
        end = marks[idx + 1][0] if idx + 1 < len(marks) else len(body)
        arms.append((label.strip(), body[e:end], s, end) if with_pos else (label.strip(), body[e:end]))
    return arms


def fn_spans(f, name):
    """Body offsets [(start, end)] of every `fun name(` in the file — one per overload.
    Start is the `{` or `=`, end is one past the last character."""
    spans = []
    n = len(f.skel)
    for m in re.finditer(r"\bfun\s+(?:[\w<>.?]+\.)?" + re.escape(name) + r"\s*\(", f.skel):
        p = m.end() - 1
        e = match_close(f.skel, p, "(", ")")
        k = e + 1
        while k < n and f.skel[k] in " \t\r\n":
            k += 1
        while k < n and f.skel[k] not in "{=\n":  # skip the return type
            k += 1
        while k < n and f.skel[k] in " \t\r\n":
            k += 1
        if k < n and f.skel[k] == "{":
            spans.append((k, match_close(f.skel, k, "{", "}") + 1))
        elif k < n and f.skel[k] == "=":
            br = f.skel.find("{", k)
            nl = f.skel.find("\n\n", k)
            if br > 0 and (nl < 0 or br < nl + 400):
                spans.append((k, match_close(f.skel, br, "{", "}") + 1))
            else:
                spans.append((k, nl if nl > 0 else k + 600))
    return spans


def fn_body(f, name):
    """Comment-free body text of `fun name(`; overloads are concatenated."""
    return "\n".join(f.text[a:b] for a, b in fn_spans(f, name))


def member_spans(skel, start, end):
    """Top-level members of the class body skel[start:end] (`{` … `}`) → [(name, start, end)] in source order.
    A member is Kotlin `fun name(` or Java `name(...) [throws …] {` at the body's own depth; lambdas, anonymous
    classes and nested classes stay inside the member that contains them (`new Listener() { onSuccess(...) {…} }`
    does not start a member). A `companion object { … }` block is descended into. An expression-bodied Kotlin function
    ends at the end of its line, stepping over `(…)` / `{…}` that span lines. Field initializers and static blocks are no member."""
    spans = []
    head_re = re.compile(r"(?<![\w.@])(?:fun\s+(?:[\w<>.?]+\.)?)?(\w+)\s*\(")
    n = min(end, len(skel))
    i = start + 1 if start < n and skel[start] == "{" else start
    while i < n:
        m = head_re.search(skel, i, n)
        b = skel.find("{", i, n)
        if m is None and b < 0:
            break
        if b >= 0 and (m is None or b < m.start()):  # a block without a call header: nested class, init, static {}
            close = match_close(skel, b, "{", "}")
            if re.match(r"\s*(?:\w+\s+)*object\b[^=]*$", skel[i:b].rsplit("\n", 1)[-1]):  # `companion object {`, not `= object : X {`
                spans.extend(member_spans(skel, b, close + 1))
            i = close + 1
            continue
        lead = skel[i:m.start()].rstrip()
        if lead and (lead[-1] in "=:.(,?" or re.search(r"\b(?:new|return|throw|else)$", lead)):
            i = m.end()  # a call inside a field initializer, `new X(`, or `object : Base(`
            continue
        p = m.end() - 1
        e = match_close(skel, p, "(", ")")
        k = e + 1
        while k < n and skel[k] not in "{=;\n":  # Java `throws …`, Kotlin `: ReturnType`
            k += 1
        if k < n and skel[k] == "{":
            body_end = match_close(skel, k, "{", "}") + 1
        elif k < n and skel[k] == "=":  # expression body: to the end of the line, stepping over (…) and {…} that span lines
            body_end = k + 1
            while body_end < n and skel[body_end] != "\n":
                if skel[body_end] == "(":
                    body_end = match_close(skel, body_end, "(", ")")
                elif skel[body_end] == "{":
                    body_end = match_close(skel, body_end, "{", "}") + 1
                    break
                body_end += 1
        else:
            body_end = k  # abstract / interface member: header only
        spans.append((m.group(1), m.start(), body_end))
        i = max(body_end, e + 1)
    return spans


def extend_up(f, pos):
    """From the start of pos's line, walk up over contiguous annotation/comment lines; return that offset."""
    start = f.raw.rfind("\n", 0, pos) + 1
    while start > 0:
        prev_end = start - 1
        prev_start = f.raw.rfind("\n", 0, prev_end) + 1
        line = f.raw[prev_start:prev_end].strip()
        if line.startswith(("@", "/*", "*", "//")):
            start = prev_start
        else:
            break
    return start
