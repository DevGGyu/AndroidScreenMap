"""Regression tests for the Kotlin reader (screenmap/kotlin.py) — synthetic inputs only.

Run from the repository root:  python3 -m unittest discover -s tests -t .
Each case pins a bug that was found on a real code base; every fix was checked to fail when reverted.
"""
import unittest

from screenmap import kotlin as ex


def mk(text, rel="data/src/main/java/x/Foo.kt"):
    """Inline Kotlin → parsed SrcFile (same path as collect_files)."""
    clean, skel = ex.strip_source(text)
    f = ex.SrcFile(None, rel, clean, skel)
    f.raw = text
    ex.parse_classes(f)
    return f


def cls(f, name):
    return next(c for c in f.classes if c.name == name)


class ConstValueTemplate(unittest.TestCase):
    """`"$BASE/x"` templates resolve recursively; an unresolvable variable yields None, never a string with `$` left in."""

    SRC = (
        "package x\n"
        "object ApiConst {\n"
        '    const val BASE = "goods-service/api/v1"\n'
        '    const val SIDOS = "$BASE/sidos"\n'
        '    const val BROKEN = "$NOWHERE/x"\n'
        "}\n"
    )

    def setUp(self):
        self.per_obj, self.glob = ex.resolve_consts([mk(self.SRC)])

    def test_template_resolves_recursively(self):
        self.assertEqual(ex.const_value("ApiConst.SIDOS", self.per_obj, self.glob), "goods-service/api/v1/sidos")

    def test_undefined_template_variable_gives_none_not_dollar_string(self):
        self.assertIsNone(ex.const_value("ApiConst.BROKEN", self.per_obj, self.glob))

    def test_plus_concatenation_and_literal(self):
        self.assertEqual(ex.const_value('ApiConst.BASE + "/a" + "/b"', self.per_obj, self.glob), "goods-service/api/v1/a/b")

    def test_alias_cycle_terminates_with_none(self):
        po, gl = ex.resolve_consts([mk("package x\nobject C {\n    const val A = B\n    const val B = A\n}\n")])
        self.assertIsNone(ex.const_value("C.A", po, gl))


class BodylessAnnotationOwnership(unittest.TestCase):
    """A body-less `annotation class` owns only its header; the `const val` right below belongs to the enclosing object."""

    SRC = (
        "package x\n"
        "object ProtocolConst {\n"
        "    annotation class LinkTarget\n"
        '    const val LINK = "weblink"\n'
        "}\n"
    )

    def test_const_after_bodyless_annotation_belongs_to_enclosing_object(self):
        per_obj, _ = ex.resolve_consts([mk(self.SRC)])
        self.assertEqual(per_obj["ProtocolConst"]["LINK"], '"weblink"')
        self.assertNotIn("LINK", per_obj.get("LinkTarget", {}))

    def test_bodyless_declaration_body_span_is_empty(self):
        a, b = cls(mk(self.SRC), "LinkTarget").body
        self.assertEqual(a, b)


class ExpressionBodyFunction(unittest.TestCase):
    """`fun f() =\\n when {…}` extends to the matching `}`, not to the first blank line."""

    SRC = (
        "package x\n"
        "class Mapper {\n"
        "    fun createLinkData(x: String) =\n"
        "        when (x) {\n"
        '            "a" -> 1\n'
        "\n"
        '            "b" -> 2\n'
        "            else -> 0\n"
        "        }\n"
        "\n"
        "    fun other() = 1\n"
        "\n"
        "    fun third() = 3\n"
        "}\n"
    )

    def test_when_arms_after_blank_line_are_inside_the_body(self):
        body = ex.fn_body(mk(self.SRC), "createLinkData")
        self.assertIn('"b" -> 2', body)
        self.assertIn("else -> 0", body)

    def test_body_does_not_run_into_next_function(self):
        self.assertNotIn("fun other", ex.fn_body(mk(self.SRC), "createLinkData"))

    def test_one_line_expression_body_ends_at_blank_line(self):
        # Known limit: the last one-liner of a class with no blank line after it reads up to 600 chars (the closing `}` comes along).
        self.assertEqual(ex.fn_body(mk(self.SRC), "other").strip(), "= 1")

    def test_block_body_and_overloads_are_all_collected(self):
        spans = ex.fn_spans(mk("package x\nclass H {\n    fun go(a: Int) { one() }\n    fun go(a: String) { two() }\n}\n"), "go")
        self.assertEqual(len(spans), 2)


class SupertypeAfterColonNewline(unittest.TestCase):
    """`class X :` with the supertypes on the next line (ktlint style) — supertypes and body must still be read."""

    SRC = (
        "package x\n"
        "class ProductDetailActivity :\n"
        "    BaseActivity<ActivityProductBinding>(R.layout.x),\n"
        "    Listener {\n"
        "    fun a() = 1\n"
        "    fun b() = 2\n"
        "}\n"
    )

    def test_supers_on_following_lines_are_read(self):
        self.assertEqual(cls(mk(self.SRC), "ProductDetailActivity").supers & {"BaseActivity", "Listener"}, {"BaseActivity", "Listener"})

    def test_body_spans_the_whole_class_not_four_lines(self):
        f = mk(self.SRC)
        a, b = cls(f, "ProductDetailActivity").body
        self.assertIn("fun a()", f.text[a:b + 1])
        self.assertIn("fun b()", f.text[a:b + 1])

    def test_ordinary_one_line_header_still_works(self):
        self.assertEqual(cls(mk("package x\nclass Y : Base(), Marker {\n    fun z() {}\n}\n"), "Y").supers, {"Base", "Marker"})

    def test_header_without_body_ends_at_line_break(self):
        f = mk("package x\nclass Y : Base()\nclass Z : Other()\n")
        self.assertEqual(cls(f, "Y").supers, {"Base"})
        self.assertEqual(cls(f, "Z").supers, {"Other"})


class SourceStripping(unittest.TestCase):
    """Everything else relies on this: blanking comments and strings keeps offsets identical."""

    def test_length_and_newlines_preserved(self):
        src = 'val a = "x // not comment" // real\n/* block\n comment */ val b = 1\n'
        clean, skel = ex.strip_source(src)
        self.assertEqual(len(clean), len(src))
        self.assertEqual(len(skel), len(src))
        self.assertEqual(clean.count("\n"), src.count("\n"))

    def test_comment_markers_inside_strings_survive_in_text_but_not_in_skeleton(self):
        clean, skel = ex.strip_source('val u = "http://a/b"\n')
        self.assertIn("http://a/b", clean)
        self.assertNotIn("http", skel)

    def test_braces_in_strings_do_not_confuse_matching(self):
        f = mk('package x\nclass A {\n    val s = "}}}"\n    fun f() {}\n}\nclass B\n')
        a, b = cls(f, "A").body
        self.assertIn("fun f()", f.text[a:b + 1])


if __name__ == "__main__":
    unittest.main()
