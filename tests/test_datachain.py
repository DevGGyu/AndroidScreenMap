"""Data chain (UseCase/ViewModel → Repository → DataSource → Retrofit API → endpoint) on a synthetic repository.

Run from the repository root:  python3 -m unittest discover -s tests -t .

Three naming conventions that real code bases mix are all present:
  - `PromoDataSource` / `PromoDataSourceImpl`             — DataSource without an I prefix
  - `ICatalogDataSource` / `CatalogDataSourceImpl`        — I-prefixed interface, implementation without the I
  - `IFavoriteRepo` / `FavoriteRepoImpl`, `ICatalogRepo`  — repository interfaces outside the `*Repository` convention
  - constructor parameter names that are not `source` (`src`, `svc`), a debug ViewModel injecting the implementation class
  - a DataSource without any API (`ClientIpDataSource`) must stay silent (zero endpoints is correct)
"""
import tempfile
import unittest
from pathlib import Path

from screenmap import config, extract

MANIFEST = (
    '<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="com.example.shop">'
    "<application></application></manifest>"
)

FILES = {
    "app/src/main/AndroidManifest.xml": MANIFEST,
    "data/src/main/java/com/example/shop/data/service/Apis.kt": """package com.example.shop.data.service
interface PromoApi {
    @GET("promo-service/api/coupons")
    suspend fun getCoupons(): Response<Unit>

    @POST("promo-service/api/unused")
    suspend fun neverCalled(): Response<Unit>
}
interface CatalogService {
    @GET("display-service/api/goods")
    suspend fun getByCategory(): Response<Unit>
}
interface FavoriteService {
    @GET("favorite/getyn")
    suspend fun getYn(): Response<Unit>
}
interface AuthTokenService {
    @POST("auth/token")
    suspend fun issue(): Response<Unit>
}
""",
    "data/src/main/java/com/example/shop/data/source/remote/PromoDataSource.kt": """package com.example.shop.data.source.remote
interface PromoDataSource {
    suspend fun getCoupons(): Response<Unit>
}
""",
    "data/src/main/java/com/example/shop/data/source/remote/impl/PromoDataSourceImpl.kt": """package com.example.shop.data.source.remote.impl
class PromoDataSourceImpl
    @Inject
    constructor(
        private val api: PromoApi,
    ) : PromoDataSource {
        override suspend fun getCoupons(): Response<Unit> = api.getCoupons()
    }
""",
    "data/src/main/java/com/example/shop/data/source/remote/display/ICatalogDataSource.kt": """package com.example.shop.data.source.remote.display
interface ICatalogDataSource {
    suspend fun getByCategory(): Response<Unit>
}
""",
    "data/src/main/java/com/example/shop/data/source/remote/impl/display/CatalogDataSourceImpl.kt": """package com.example.shop.data.source.remote.impl.display
class CatalogDataSourceImpl
    @Inject
    constructor(
        private val api: CatalogService,
    ) : ICatalogDataSource {
        override suspend fun getByCategory(): Response<Unit> =
            api.getByCategory()
    }
""",
    "data/src/main/java/com/example/shop/data/datasource/remote/ClientIpDataSource.kt": """package com.example.shop.data.datasource.remote
interface ClientIpDataSource {
    suspend fun ip(): String
}
class ClientIpDataSourceImpl
    @Inject
    constructor() : ClientIpDataSource {
        override suspend fun ip(): String = "0.0.0.0"
    }
""",
    "domain/src/main/java/com/example/shop/domain/repository/PromoRepository.kt": """package com.example.shop.domain.repository
interface PromoRepository {
    fun getCoupons(): Flow<Unit>
}
""",
    "domain/src/main/java/com/example/shop/domain/repository/IFavoriteRepo.kt": """package com.example.shop.domain.repository
interface IFavoriteRepo {
    fun getYn(): Flow<Unit>
}
""",
    "domain/src/main/java/com/example/shop/domain/repository/display/ICatalogRepo.kt": """package com.example.shop.domain.repository.display
interface ICatalogRepo {
    fun getByCategory(): Flow<Unit>
}
""",
    "data/src/main/java/com/example/shop/data/repository/PromoRepositoryImpl.kt": """package com.example.shop.data.repository
class PromoRepositoryImpl
    @Inject
    constructor(
        private val source: PromoDataSource,
    ) : PromoRepository {
        override fun getCoupons(): Flow<Unit> = flow { source.getCoupons() }
    }
""",
    "data/src/main/java/com/example/shop/data/repository/FavoriteRepoImpl.kt": """package com.example.shop.data.repository
class FavoriteRepoImpl
    @Inject
    constructor(
        private val svc: FavoriteService,
    ) : IFavoriteRepo {
        override fun getYn(): Flow<Unit> = flow { svc.getYn() }
    }
""",
    "data/src/main/java/com/example/shop/data/repository/display/CatalogRepoImpl.kt": """package com.example.shop.data.repository.display
class CatalogRepoImpl
    @Inject
    constructor(
        private val src: ICatalogDataSource,
        private val ip: ClientIpDataSource,
    ) : ICatalogRepo {
        override fun getByCategory(): Flow<Unit> = flow { ip.ip(); src.getByCategory() }
    }
""",
    "domain/src/main/java/com/example/shop/domain/usecase/FetchCouponsUseCase.kt": """package com.example.shop.domain.usecase
class FetchCouponsUseCase @Inject constructor(private val repo: PromoRepository) {
    fun run() = repo.getCoupons()
}
""",
    "domain/src/main/java/com/example/shop/domain/usecase/FavoriteUseCase.kt": """package com.example.shop.domain.usecase
class FavoriteUseCase @Inject constructor(private val repo: IFavoriteRepo) {
    fun run() = repo.getYn()
}
""",
    "app/src/main/java/com/example/shop/debug/DebugCatalogViewModel.kt": """package com.example.shop.debug
@HiltViewModel
class DebugCatalogViewModel
    @Inject
    constructor(
        private val catalogRepo: CatalogRepoImpl,
    ) : BaseViewModel() {
        fun load() { catalogRepo.getByCategory() }
    }
""",
}


class DataChain(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        for rel, text in FILES.items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
        graph, _ = extract.build(root, config.load(None, root))
        cls.g = graph
        cls.nodes = {n["id"]: n for n in graph["nodes"]}
        cls.by_label = {n["label"]: n for n in graph["nodes"]}
        cls.stats = graph["meta"]["stats"]

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def eps(self, label):
        return set(self.by_label[label].get("endpoints") or [])

    def test_repository_nodes_cover_both_naming_conventions(self):
        repos = {n["label"] for n in self.g["nodes"] if n["kind"] == "repository"}
        self.assertEqual(repos, {"PromoRepository", "IFavoriteRepo", "ICatalogRepo"})

    def test_usecase_reaches_endpoint_through_datasource_without_i_prefix(self):
        self.assertEqual(self.eps("FetchCouponsUseCase"), {"endpoint:PromoApi.getCoupons"})

    def test_datasource_pass_through_is_method_level_not_whole_api(self):
        self.assertNotIn("endpoint:PromoApi.neverCalled", self.eps("FetchCouponsUseCase"))

    def test_usecase_reaches_endpoint_through_i_repo_interface_calling_api_directly(self):
        self.assertEqual(self.eps("FavoriteUseCase"), {"endpoint:FavoriteService.getYn"})

    def test_debug_viewmodel_injecting_impl_class_and_i_prefixed_datasource_with_odd_param_names(self):
        self.assertEqual(self.eps("DebugCatalogViewModel"), {"endpoint:CatalogService.getByCategory"})

    def test_datasource_without_api_is_silent(self):
        ds = self.stats["datasource"]
        self.assertNotIn("ClientIpDataSourceImpl", ds["impls_without_endpoint"])
        self.assertEqual(ds["impls_with_api"], 2)  # Promo, Catalog — ClientIp has no API
        self.assertEqual(ds["impls_without_endpoint"], [])

    def test_unreferenced_endpoints_stay_unreachable(self):
        reached = {e for n in self.g["nodes"] for e in (n.get("endpoints") or [])}
        ep_ids = {n["id"] for n in self.g["nodes"] if n["kind"] == "endpoint"}
        self.assertEqual(ep_ids - reached, {"endpoint:PromoApi.neverCalled", "endpoint:AuthTokenService.issue"})

    def test_repository_to_endpoint_edge_records_the_datasource_hop(self):
        e = next(e for e in self.g["edges"] if e["kind"] == "calls" and e["t"] == "endpoint:PromoApi.getCoupons"
                 and self.nodes[e["s"]]["kind"] == "repository")
        self.assertTrue(any("PromoDataSource" in v for v in e["via"]), e["via"])


if __name__ == "__main__":
    unittest.main()
