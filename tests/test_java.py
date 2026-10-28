"""Java sources and endpoint constants (legacy apps without Retrofit / ViewModel / UseCase) on a synthetic repository.

Run from the repository root:  python3 -m unittest discover -s tests -t .

Shapes that the real legacy code base mixes:
  - Java `class X extends Base<T> implements I` — the supertype must be read without a Kotlin `:` header
  - endpoint constants `public static final Url X = new Url("/path")` declared in one Java class (`endpoints.decl` config)
  - a Java screen referencing a constant qualified (`Url.X`) and another bare, through `import static`
  - a Kotlin screen referencing a constant → the same direct `calls` edge
  - a reference inside a comment or a string literal must not count
  - `endpoints.verb_near` names the HTTP verb when the reference sits inside `.post(`
  - a *hub* (helper class that wraps the call): `Hub.member(` and `Hub.getInstance().member(` credit the caller with
    exactly that member's constants — not the whole hub — even when the member holds an anonymous listener class
  - a Kotlin hub whose members live in a `companion object`
  - an abstract base screen's calls belong to the screens that extend it, not to package neighbours
"""
import json
import tempfile
import unittest
from pathlib import Path

from screenmap import config, extract

MANIFEST = (
    '<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="com.example.depot">'
    '<application><activity android:name=".ui.MainActivity"/></application></manifest>'
)

CONFIG = {
    "endpoints": {
        "decl": "static\\s+final\\s+Url\\s+(?P<name>\\w+)\\s*=\\s*new\\s+Url\\(\\s*\"(?P<path>[^\"]+)\"",
        "verb_near": "\\.(get|post)\\(\\s*$",
    }
}

FILES = {
    "app/src/main/AndroidManifest.xml": MANIFEST,
    "app/src/main/java/com/example/depot/net/Url.java": """package com.example.depot.net;
public class Url {
    public static final Url LOGIN_URL = new Url("/mobile/login.json");
    public static final Url ITEMS_URL = new Url("/mobile/items.json");
    public static final Url UNUSED_URL = new Url("/mobile/unused.json");
    // public static final Url OLD_URL = new Url("/mobile/old.json");
    private final String path;
    public Url(String path) { this.path = path; }
}
""",
    "app/src/main/java/com/example/depot/ui/MainActivity.java": """package com.example.depot.ui;
import com.example.depot.net.Url;
public class MainActivity extends AppCompatActivity {
    void login() {
        Net.getInstance().post(Url.LOGIN_URL, body, listener);
        // Url.UNUSED_URL is only mentioned in this comment
        String s = "UNUSED_URL";
    }
}
""",
    "app/src/main/java/com/example/depot/ui/BaseFragment.java": """package com.example.depot.ui;
public abstract class BaseFragment<T> extends Fragment implements Scannable {
}
""",
    "app/src/main/java/com/example/depot/ui/stock/ItemsFragment.java": """package com.example.depot.ui.stock;
import static com.example.depot.net.Url.ITEMS_URL;
public class ItemsFragment extends BaseFragment<Item>
        implements Refreshable {
    void load() {
        Net.getInstance().get(ITEMS_URL, listener);
    }
}
""",
    "app/src/main/java/com/example/depot/ui/stock/DetailFragment.kt": """package com.example.depot.ui.stock
class DetailFragment : BaseFragment<Item>() {
    fun load() {
        val url = Url.appendParameters(Url.ITEMS_URL, Url.Parameter("id", id))
        Net.getInstance().get(url, listener)
    }
}
""",
    # hub 1: static factory methods; `items` keeps its constant AFTER an anonymous listener class with its own methods
    "app/src/main/java/com/example/depot/net/Loader.java": """package com.example.depot.net;
public class Loader {
    public static Loader items(Context ctx) {
        Listener l = new Listener() {
            @Override public void onSuccess(Response r) { show(r); }
            @Override public void onFail(String m) { toast(m); }
        };
        return new Loader(ctx, Url.appendParameters(Url.ITEMS_URL), l);
    }
    public static Loader unused(Context ctx) throws IOException {
        return new Loader(ctx, Url.UNUSED_URL, null);
    }
    private Loader(Context ctx, Url u, Listener l) { }
}
""",
    # hub 2: singleton reached through getInstance()
    "app/src/main/java/com/example/depot/net/Session.java": """package com.example.depot.net;
public class Session {
    private static Session sInstance;
    public static Session getInstance() { return sInstance; }
    public void login(String id, Listener l) {
        Net.getInstance().post(Url.LOGIN_URL, id, l);
    }
    public void refresh(Listener l) {
        Net.getInstance().get(Url.ITEMS_URL, l);
    }
}
""",
    # hub 3: Kotlin, members in a companion object
    "app/src/main/java/com/example/depot/net/Fetch.kt": """package com.example.depot.net
class Fetch {
    companion object {
        fun items(ctx: Context): Fetch = Fetch(ctx, Url.ITEMS_URL)
        fun login(ctx: Context): Fetch = Fetch(ctx, Url.LOGIN_URL)
    }
}
""",
    # screens that reach the network only through hubs
    "app/src/main/java/com/example/depot/ui/load/ListFragment.java": """package com.example.depot.ui.load;
import com.example.depot.net.Loader;
public class ListFragment extends BaseFragment<Item> {
    void load() {
        Loader c = Loader.items(getActivity());
        Session.getInstance().login(mId, mListener);
    }
}
""",
    "app/src/main/java/com/example/depot/ui/load/PickFragment.kt": """package com.example.depot.ui.load
class PickFragment : BaseFragment<Item>() {
    fun load() {
        val f = Fetch.login(requireContext())
    }
}
""",
    # abstract base screen calling a hub: ChildAFragment / ChildBFragment extend it, NeighbourFragment only shares the package
    "app/src/main/java/com/example/depot/ui/pack/PackBaseFragment.java": """package com.example.depot.ui.pack;
public abstract class PackBaseFragment extends BaseFragment<Item> {
    void refresh() {
        Session.getInstance().refresh(mListener);
    }
}
""",
    "app/src/main/java/com/example/depot/ui/pack/ChildAFragment.java": """package com.example.depot.ui.pack;
public class ChildAFragment extends PackBaseFragment {
}
""",
    "app/src/main/java/com/example/depot/ui/pack/ChildBFragment.kt": """package com.example.depot.ui.pack
class ChildBFragment : PackBaseFragment() {
}
""",
    "app/src/main/java/com/example/depot/ui/pack/NeighbourFragment.java": """package com.example.depot.ui.pack;
public class NeighbourFragment extends BaseFragment<Item> {
}
""",
}


class JavaAndConstants(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        for rel, text in FILES.items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
        (root / "screenmap.json").write_text(json.dumps(CONFIG), encoding="utf-8")
        graph, code = extract.build(root, config.load(None, root))
        cls.g, cls.code = graph, code
        cls.nodes = {n["id"]: n for n in graph["nodes"]}
        cls.edges = {(e["s"], e["t"], e["kind"]): e for e in graph["edges"]}

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_java_fragment_with_generic_supertype_and_interface_is_a_screen(self):
        frs = {n["label"] for n in self.g["nodes"] if n["kind"] == "fragment"}
        self.assertEqual(frs, {"ItemsFragment", "DetailFragment", "ListFragment", "PickFragment",
                               "ChildAFragment", "ChildBFragment", "NeighbourFragment"})  # the abstract bases are not screens

    def calls_from(self, nid):
        return {t: e for (s, t, k), e in self.edges.items() if s == nid and k == "calls"}

    def test_hub_member_credits_the_caller_with_that_member_only_even_past_an_inner_listener(self):
        got = self.calls_from("fragment:com.example.depot.ui.load.ListFragment")
        self.assertEqual(set(got), {"endpoint:Url.ITEMS_URL", "endpoint:Url.LOGIN_URL"})  # not UNUSED_URL (Loader.unused)
        self.assertEqual(got["endpoint:Url.ITEMS_URL"]["via"], ["Loader.items"])
        self.assertEqual(got["endpoint:Url.ITEMS_URL"]["conf"], "parsed")
        self.assertEqual(got["endpoint:Url.LOGIN_URL"]["via"], ["Session.login"])  # through getInstance()
        self.assertEqual(got["endpoint:Url.LOGIN_URL"]["methods"], ["POST"])  # the verb seen inside the hub member
        self.assertEqual(self.nodes["fragment:com.example.depot.ui.load.ListFragment"]["endpoints"],
                         ["endpoint:Url.ITEMS_URL", "endpoint:Url.LOGIN_URL"])
        self.assertEqual(self.g["meta"]["stats"]["endpoint_hubs"], {"Fetch": 2, "Loader": 2, "Session": 2})

    def test_kotlin_companion_object_hub(self):
        got = self.calls_from("fragment:com.example.depot.ui.load.PickFragment")
        self.assertEqual(set(got), {"endpoint:Url.LOGIN_URL"})
        self.assertEqual(got["endpoint:Url.LOGIN_URL"]["via"], ["Fetch.login"])

    def test_abstract_base_screen_calls_belong_to_its_heirs_not_to_package_neighbours(self):
        for heir in ("fragment:com.example.depot.ui.pack.ChildAFragment", "fragment:com.example.depot.ui.pack.ChildBFragment"):
            got = self.calls_from(heir)
            self.assertEqual(set(got), {"endpoint:Url.ITEMS_URL"}, heir)
            self.assertEqual(got["endpoint:Url.ITEMS_URL"]["conf"], "parsed")
            self.assertEqual(got["endpoint:Url.ITEMS_URL"]["via"], ["PackBaseFragment (상속)", "Session.refresh"])
        self.assertEqual(self.calls_from("fragment:com.example.depot.ui.pack.NeighbourFragment"), {})
        self.assertNotIn("endpoints", self.nodes["fragment:com.example.depot.ui.pack.NeighbourFragment"])

    def test_constants_become_endpoints_under_their_class_and_commented_out_ones_do_not(self):
        eps = {n["id"]: n["label"] for n in self.g["nodes"] if n["kind"] == "endpoint"}
        self.assertEqual(eps, {"endpoint:Url.LOGIN_URL": "/mobile/login.json", "endpoint:Url.ITEMS_URL": "/mobile/items.json",
                               "endpoint:Url.UNUSED_URL": "/mobile/unused.json"})
        self.assertIn(("api:Url", "endpoint:Url.ITEMS_URL", "calls"), self.edges)
        self.assertEqual(self.g["meta"]["stats"]["endpoint_consts"], 3)

    def test_java_activity_calls_qualified_constant_with_verb_and_ignores_comment_and_string(self):
        e = self.edges.get(("activity:MainActivity", "endpoint:Url.LOGIN_URL", "calls"))
        self.assertIsNotNone(e)
        self.assertEqual(e["conf"], "parsed")
        self.assertEqual(e["methods"], ["POST"])
        self.assertNotIn(("activity:MainActivity", "endpoint:Url.UNUSED_URL", "calls"), self.edges)
        self.assertEqual(self.nodes["activity:MainActivity"]["endpoints"], ["endpoint:Url.LOGIN_URL"])

    def test_static_import_bare_name_and_kotlin_qualified_name_both_reach_the_constant(self):
        self.assertIn(("fragment:com.example.depot.ui.stock.ItemsFragment", "endpoint:Url.ITEMS_URL", "calls"), self.edges)
        self.assertIn(("fragment:com.example.depot.ui.stock.DetailFragment", "endpoint:Url.ITEMS_URL", "calls"), self.edges)

    def test_constant_endpoint_has_its_declaration_line_as_code_snippet(self):
        snips = self.code["endpoint:Url.ITEMS_URL"]
        self.assertEqual(len(snips), 1)
        self.assertIn('new Url("/mobile/items.json")', snips[0]["s"])
        self.assertEqual(snips[0]["l"], 4)


if __name__ == "__main__":
    unittest.main()
