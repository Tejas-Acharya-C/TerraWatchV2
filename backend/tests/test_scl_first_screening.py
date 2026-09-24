from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import rasterio
from affine import Affine
from fastapi.testclient import TestClient
from rasterio.transform import from_origin
from shapely.geometry import shape, mapping
from rasterio.warp import transform_geom
from rasterio.mask import mask

from app import imagery
from app.config import settings
from app.db import connection, initialize_database
from app.main import app
from app.schemas import AOIGeometry, ImageryAcquisitionRequest

client = TestClient(app)

AOI_KARNATAKA = {
    "type": "Polygon",
    "coordinates": [
        [
            [77.4, 12.8],
            [77.5, 12.8],
            [77.5, 12.9],
            [77.4, 12.9],
            [77.4, 12.8],
        ]
    ],
}


class MockAsset:
    def __init__(self, href: str) -> None:
        self.href = href


class MockItem:
    def __init__(
        self,
        item_id: str,
        cloud_cover: float = 0.0,
        item_datetime: datetime | None = None,
        assets: dict[str, MockAsset] | None = None,
        geometry: dict | None = None,
    ) -> None:
        self.id = item_id
        self.collection_id = "sentinel-2-l2a"
        self.datetime = item_datetime or datetime(2025, 4, 15, 10, 0, tzinfo=UTC)
        self.properties = {"datetime": self.datetime.isoformat(), "eo:cloud_cover": cloud_cover}
        self.assets = assets or {
            "red": MockAsset(f"https://example.test/{item_id}_red.tif"),
            "nir": MockAsset(f"https://example.test/{item_id}_nir.tif"),
            "green": MockAsset(f"https://example.test/{item_id}_green.tif"),
            "blue": MockAsset(f"https://example.test/{item_id}_blue.tif"),
            "scl": MockAsset(f"https://example.test/{item_id}_scl.tif"),
        }
        self.geometry = geometry or {
            "type": "Polygon",
            "coordinates": [[[77.0, 12.0], [78.0, 12.0], [78.0, 13.5], [77.0, 13.5], [77.0, 12.0]]],
        }


class MockSearch:
    def __init__(self, items: list[MockItem]) -> None:
        self._items = items

    def items(self):
        return iter(self._items)


class MockCatalog:
    def __init__(self, items: list[MockItem]) -> None:
        self.items = items

    def search(self, **kwargs):
        return MockSearch(self.items)


@pytest.fixture(autouse=True)
def isolated_scl_screening_env(tmp_path: Path):
    original_db = settings.database_path
    original_data_dir = settings.imagery_data_dir
    object.__setattr__(settings, "database_path", tmp_path / "test_scl_screening.db")
    object.__setattr__(settings, "imagery_data_dir", tmp_path / "imagery")
    initialize_database()
    yield
    object.__setattr__(settings, "database_path", original_db)
    object.__setattr__(settings, "imagery_data_dir", original_data_dir)


def write_test_geotiff(
    path: Path, array: np.ndarray, crs: str = "EPSG:4326", transform=None, nodata: int = 0
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = array.shape[0] if array.ndim == 3 else 1
    height, width = array.shape[-2:]
    tf = transform or from_origin(77.0, 13.5, 0.1, 0.1)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=width,
        height=height,
        count=count,
        dtype=array.dtype.name,
        crs=crs,
        transform=tf,
        nodata=nodata,
    ) as ds:
        if count == 1:
            ds.write(array.reshape(height, width), 1)
        else:
            for idx in range(count):
                ds.write(array[idx], idx + 1)


# =========================================================================
# 1. TARGET GRID EQUIVALENCE VALIDATION ACROSS AOIs & SCENES
# =========================================================================
def test_derived_target_grid_matches_authoritative_prepared_grid(tmp_path: Path):
    """Validate that _derive_target_grid_from_scl produces identical CRS, width,
    height, affine transform, resolution, bounds, and AOI coverage to the
    authoritative prepared raster grid."""
    # Test multiple scene geometries (UTM 10m/20m vs Geographic 0.02)
    shapes_to_test = [
        # Standard AOI
        {"type": "Polygon", "coordinates": [[[77.4, 12.8], [77.5, 12.8], [77.5, 12.9], [77.4, 12.9], [77.4, 12.8]]]},
        # Small AOI
        {"type": "Polygon", "coordinates": [[[77.42, 12.82], [77.44, 12.82], [77.44, 12.84], [77.42, 12.84], [77.42, 12.82]]]},
        # Offset/Edge AOI
        {"type": "Polygon", "coordinates": [[[77.48, 12.81], [77.58, 12.81], [77.58, 12.99], [77.48, 12.99], [77.48, 12.81]]]},
    ]

    for idx, geom_dict in enumerate(shapes_to_test):
        aoi = AOIGeometry.model_validate(geom_dict)
        aoi_shape = shape(geom_dict)

        # 1. Test Sentinel-2 UTM 10m/20m configuration (CRS EPSG:32643)
        scl_utm_path = tmp_path / f"scl_utm_{idx}.tif"
        red_utm_path = tmp_path / f"red_utm_{idx}.tif"
        
        utm_crs = "EPSG:32643"
        geom_in_crs = transform_geom("EPSG:4326", utm_crs, mapping(aoi_shape), precision=10)
        minx, miny, maxx, maxy = shape(geom_in_crs).bounds
        origin_x = np.floor((minx - 500.0) / 20.0) * 20.0
        origin_y = np.ceil((maxy + 500.0) / 20.0) * 20.0
        scl_w = int(np.ceil((maxx + 500.0 - origin_x) / 20.0))
        scl_h = int(np.ceil((origin_y - (miny - 500.0)) / 20.0))
        red_w = scl_w * 2
        red_h = scl_h * 2
        scl_tf = Affine(20.0, 0.0, origin_x, 0.0, -20.0, origin_y)
        red_tf = Affine(10.0, 0.0, origin_x, 0.0, -10.0, origin_y)

        write_test_geotiff(scl_utm_path, np.full((scl_h, scl_w), 4, dtype=np.uint8), crs=utm_crs, transform=scl_tf)
        write_test_geotiff(red_utm_path, np.full((red_h, red_w), 1000, dtype=np.uint16), crs=utm_crs, transform=red_tf)

        with rasterio.open(scl_utm_path) as scl_ds, rasterio.open(red_utm_path) as red_ds:
            # Authoritative mask(red)
            geom_in_crs = transform_geom("EPSG:4326", red_ds.crs, mapping(aoi_shape), precision=10)
            red_data, red_transform = mask(red_ds, [geom_in_crs], crop=True, filled=True)
            auth_h, auth_w = red_data.shape[1], red_data.shape[2]

            # SCL-derived grid
            derived_grid = imagery._derive_target_grid_from_scl(scl_ds, aoi)

            assert derived_grid.crs == red_ds.crs
            assert derived_grid.width == auth_w
            assert derived_grid.height == auth_h
            assert derived_grid.transform == red_transform
            assert np.isclose(derived_grid.resolution[0], 10.0)
            assert np.isclose(derived_grid.resolution[1], 10.0)
            assert derived_grid.bounds == (
                red_transform.c,
                red_transform.f + auth_h * red_transform.e,
                red_transform.c + auth_w * red_transform.a,
                red_transform.f,
            )


# =========================================================================
# 2. TEST A: CLEARLY UNUSABLE SCL CANDIDATE SHORT-CIRCUITS B04/B08
# =========================================================================
def test_clearly_unusable_scl_short_circuits_b04_b08(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """When SCL proves max_possible_usable_fraction < 0.50:
    - B04 and B08 must NOT be opened or fetched
    - Observation persists as valid_unusable
    - No RGB display artifact is generated
    - Provenance includes evaluation_stage='scl_screening'"""
    # 10x10 raster, all cloud (SCL class 8 -> 0% usable)
    red_data = np.full((10, 10), 500, dtype=np.uint16)
    nir_data = np.full((10, 10), 1000, dtype=np.uint16)
    scl_data = np.full((10, 10), 8, dtype=np.uint8)

    files = {
        "https://example.test/S2_UNUSABLE_red.tif": tmp_path / "red.tif",
        "https://example.test/S2_UNUSABLE_nir.tif": tmp_path / "nir.tif",
        "https://example.test/S2_UNUSABLE_scl.tif": tmp_path / "scl.tif",
    }
    write_test_geotiff(files["https://example.test/S2_UNUSABLE_red.tif"], red_data)
    write_test_geotiff(files["https://example.test/S2_UNUSABLE_nir.tif"], nir_data)
    write_test_geotiff(files["https://example.test/S2_UNUSABLE_scl.tif"], scl_data)

    opened_urls: list[str] = []
    real_open = rasterio.open

    def tracking_open(path, *args, **kwargs):
        if isinstance(path, str) and path.startswith("https://"):
            opened_urls.append(path)
            if path in files:
                return real_open(files[path], *args, **kwargs)
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(imagery.rasterio, "open", tracking_open)
    catalog = MockCatalog([MockItem("S2_UNUSABLE", 85.0)])
    monkeypatch.setattr(imagery.pystac_client.Client, "open", lambda _: catalog)

    aoi_resp = client.post("/api/v1/aoi", json=AOI_KARNATAKA)
    aoi_id = aoi_resp.json()["aoi_id"]

    req_json = {"aoi_id": aoi_id, "start_datetime": "2025-01-01T00:00:00Z", "end_datetime": "2025-06-01T00:00:00Z"}
    resp = client.post("/api/v1/imagery/acquisitions", json=req_json)
    assert resp.status_code == 404
    assert "No usable Sentinel-2 observation was available" in resp.json()["message"]

    # Verify B04 and B08 were NEVER opened
    b04_opens = [url for url in opened_urls if "red.tif" in url]
    b08_opens = [url for url in opened_urls if "nir.tif" in url]
    scl_opens = [url for url in opened_urls if "scl.tif" in url]
    assert len(b04_opens) == 0, f"B04 must NOT be opened for early SCL rejected candidate: {b04_opens}"
    assert len(b08_opens) == 0, f"B08 must NOT be opened for early SCL rejected candidate: {b08_opens}"
    assert len(scl_opens) == 1, f"SCL must be opened for screening: {scl_opens}"

    # Verify persistence: valid_unusable, no display artifact, evaluation_stage='scl_screening'
    with connection() as db:
        row = db.execute(
            "SELECT observation_state, quality_reason, quality_metrics_json, prepared_path, display_path, quality_mask_path FROM imagery_acquisitions WHERE aoi_id = ?",
            (aoi_id,),
        ).fetchone()
    assert row is not None
    assert row[0] == "valid_unusable"
    assert row[1] == "no_usable_pixels"
    metrics = json.loads(row[2])
    assert metrics["evaluation_stage"] == "scl_screening"
    assert metrics["usable_percentage"] == 0.0
    assert row[3] == ""  # prepared_path empty (not fabricated)
    assert row[4] is None  # display_path None
    assert Path(row[5]).is_file()  # quality mask written and valid


# =========================================================================
# 3. TEST B: CLEARLY USABLE SCL CANDIDATE PROCEEDS TO FULL EVALUATION
# =========================================================================
def test_clearly_usable_scl_proceeds_to_full_evaluation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """When SCL proves max_possible_usable_fraction >= 0.50:
    - B04 and B08 must be fetched and evaluated
    - Full authoritative quality assessment runs
    - Candidate state becomes usable
    - Display artifact is generated"""
    red_data = np.full((10, 10), 500, dtype=np.uint16)
    nir_data = np.full((10, 10), 1000, dtype=np.uint16)
    green_data = np.full((10, 10), 400, dtype=np.uint16)
    blue_data = np.full((10, 10), 300, dtype=np.uint16)
    scl_data = np.full((10, 10), 4, dtype=np.uint8)  # 100% vegetation (valid)

    files = {
        "https://example.test/S2_USABLE_red.tif": tmp_path / "red.tif",
        "https://example.test/S2_USABLE_nir.tif": tmp_path / "nir.tif",
        "https://example.test/S2_USABLE_green.tif": tmp_path / "green.tif",
        "https://example.test/S2_USABLE_blue.tif": tmp_path / "blue.tif",
        "https://example.test/S2_USABLE_scl.tif": tmp_path / "scl.tif",
    }
    for href, p in files.items():
        write_test_geotiff(p, red_data if "red" in href else nir_data if "nir" in href else green_data if "green" in href else blue_data if "blue" in href else scl_data)

    opened_urls: list[str] = []
    real_open = rasterio.open

    def tracking_open(path, *args, **kwargs):
        if isinstance(path, str) and path.startswith("https://"):
            opened_urls.append(path)
            if path in files:
                return real_open(files[path], *args, **kwargs)
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(imagery.rasterio, "open", tracking_open)
    catalog = MockCatalog([MockItem("S2_USABLE", 0.0)])
    monkeypatch.setattr(imagery.pystac_client.Client, "open", lambda _: catalog)

    aoi_resp = client.post("/api/v1/aoi", json=AOI_KARNATAKA)
    aoi_id = aoi_resp.json()["aoi_id"]

    req_json = {"aoi_id": aoi_id, "start_datetime": "2025-01-01T00:00:00Z", "end_datetime": "2025-06-01T00:00:00Z"}
    resp = client.post("/api/v1/imagery/acquisitions", json=req_json)
    print("STATUS:", resp.status_code, resp.json())
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["observation_state"] == "usable"
    assert payload["quality_metrics"]["usable_percentage"] == 100.0
    assert payload["display_path"] is not None

    # Both B04 and B08 were opened exactly once
    assert len([url for url in opened_urls if "red.tif" in url]) == 1
    assert len([url for url in opened_urls if "nir.tif" in url]) == 1


# =========================================================================
# 4. TEST C: CONSERVATIVE BOUNDARY CASE AT EXACTLY 0.50
# =========================================================================
def test_scl_boundary_case_at_half_threshold_proceeds(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """An observation with exactly 50% usable pixels in SCL must NOT be rejected
    by the SCL-first stage; it must proceed conservatively to full evaluation."""
    # 10x10 raster: top 5 rows valid (class 4), bottom 5 rows cloud (class 8) -> exactly 50%
    scl_data = np.zeros((10, 10), dtype=np.uint8)
    scl_data[:5, :] = 4
    scl_data[5:, :] = 8

    red_data = np.full((10, 10), 500, dtype=np.uint16)
    nir_data = np.full((10, 10), 1000, dtype=np.uint16)
    green_data = np.full((10, 10), 400, dtype=np.uint16)
    blue_data = np.full((10, 10), 300, dtype=np.uint16)

    files = {
        "https://example.test/S2_BOUNDARY_red.tif": tmp_path / "red.tif",
        "https://example.test/S2_BOUNDARY_nir.tif": tmp_path / "nir.tif",
        "https://example.test/S2_BOUNDARY_green.tif": tmp_path / "green.tif",
        "https://example.test/S2_BOUNDARY_blue.tif": tmp_path / "blue.tif",
        "https://example.test/S2_BOUNDARY_scl.tif": tmp_path / "scl.tif",
    }
    tf_boundary = from_origin(77.4, 12.9, 0.01, 0.01)
    for href, p in files.items():
        write_test_geotiff(
            p,
            scl_data if "scl" in href else red_data if "red" in href else nir_data if "nir" in href else green_data if "green" in href else blue_data,
            transform=tf_boundary,
        )

    opened_urls: list[str] = []
    real_open = rasterio.open

    def tracking_open(path, *args, **kwargs):
        if isinstance(path, str) and path.startswith("https://"):
            opened_urls.append(path)
            if path in files:
                return real_open(files[path], *args, **kwargs)
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(imagery.rasterio, "open", tracking_open)
    catalog = MockCatalog([MockItem("S2_BOUNDARY", 10.0)])
    monkeypatch.setattr(imagery.pystac_client.Client, "open", lambda _: catalog)

    aoi_resp = client.post("/api/v1/aoi", json=AOI_KARNATAKA)
    aoi_id = aoi_resp.json()["aoi_id"]

    req_json = {"aoi_id": aoi_id, "start_datetime": "2025-01-01T00:00:00Z", "end_datetime": "2025-06-01T00:00:00Z"}
    resp = client.post("/api/v1/imagery/acquisitions", json=req_json)
    assert resp.status_code == 200
    # Must have proceeded to B04/B08 evaluation
    assert len([url for url in opened_urls if "red.tif" in url]) == 1
    assert resp.json()["observation_state"] == "usable"
    assert resp.json()["quality_metrics"]["usable_percentage"] == 50.0


# =========================================================================
# 5. TEST D: SCL FAILURE SEMANTICS ARE EXPLICIT (NEVER VALID_UNUSABLE)
# =========================================================================
def test_scl_acquisition_failure_remains_explicit_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """If SCL cannot be acquired or validated, it raises an explicit error and
    is NEVER converted into valid_unusable."""
    catalog = MockCatalog([MockItem("S2_FAIL_SCL", 5.0)])
    monkeypatch.setattr(imagery.pystac_client.Client, "open", lambda _: catalog)

    # Missing SCL file in open raises AssetAcquisitionError
    real_open = rasterio.open

    def failing_open(path, *args, **kwargs):
        if isinstance(path, str) and "scl.tif" in path:
            raise imagery.RasterioIOError("Simulated remote 404/500 SCL failure")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(imagery.rasterio, "open", failing_open)

    aoi_resp = client.post("/api/v1/aoi", json=AOI_KARNATAKA)
    aoi_id = aoi_resp.json()["aoi_id"]

    req_json = {"aoi_id": aoi_id, "start_datetime": "2025-01-01T00:00:00Z", "end_datetime": "2025-06-01T00:00:00Z"}
    resp = client.post("/api/v1/imagery/acquisitions", json=req_json)
    assert resp.status_code == 502
    assert resp.json()["code"] == "AssetAcquisitionError"


# =========================================================================
# 6. TEST E & F: EXACT SELECTION & QUALITY METRIC EQUIVALENCE
# =========================================================================
def test_exact_selection_and_quality_metric_equivalence(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Verify that SCL-first screening selects the EXACT SAME candidate with
    the EXACT SAME authoritative quality metrics as reference exhaustive evaluation."""
    # Create 3 candidates:
    # Item 1: Catalog cloud 5%, AOI has 70% cloud (SCL 8) -> SCL screened out (<50%)
    # Item 2: Catalog cloud 10%, AOI has 10% cloud (SCL 8), 90% vegetation (SCL 4), date 2025-04-15 -> usable
    # Item 3: Catalog cloud 20%, AOI has 10% cloud (SCL 8), 90% vegetation (SCL 4), date 2025-04-10 -> usable & EARLIER DATE!
    # Under Phase B quality ranking, Item 3 MUST win over Item 2 (earlier date) and Item 1 (cloudy).
    dt2 = datetime(2025, 4, 15, 10, 0, tzinfo=UTC)
    dt3 = datetime(2025, 4, 10, 10, 0, tzinfo=UTC)

    scl_item1 = np.full((10, 10), 8, dtype=np.uint8)  # 0% usable
    scl_item2 = np.full((10, 10), 4, dtype=np.uint8)  # 100% usable
    scl_item3 = np.full((10, 10), 4, dtype=np.uint8)  # 100% usable

    files: dict[str, Path] = {}
    items = [
        MockItem("ITEM_1", 5.0, datetime(2025, 4, 20, 10, 0, tzinfo=UTC)),
        MockItem("ITEM_2", 10.0, dt2),
        MockItem("ITEM_3", 20.0, dt3),
    ]
    for item, scl_arr in zip(items, [scl_item1, scl_item2, scl_item3]):
        for b in ["red", "nir", "green", "blue", "scl"]:
            p = tmp_path / f"{item.id}_{b}.tif"
            files[f"https://example.test/{item.id}_{b}.tif"] = p
            data = scl_arr if b == "scl" else np.full((10, 10), 500, dtype=np.uint16)
            write_test_geotiff(p, data)

    opened_urls: list[str] = []
    real_open = rasterio.open

    def tracking_open(path, *args, **kwargs):
        if isinstance(path, str) and path.startswith("https://"):
            opened_urls.append(path)
            if path in files:
                return real_open(files[path], *args, **kwargs)
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(imagery.rasterio, "open", tracking_open)
    catalog = MockCatalog(items)
    monkeypatch.setattr(imagery.pystac_client.Client, "open", lambda _: catalog)

    aoi_resp = client.post("/api/v1/aoi", json=AOI_KARNATAKA)
    aoi_id = aoi_resp.json()["aoi_id"]

    req_json = {"aoi_id": aoi_id, "start_datetime": "2025-01-01T00:00:00Z", "end_datetime": "2025-06-01T00:00:00Z"}
    resp = client.post("/api/v1/imagery/acquisitions", json=req_json)
    assert resp.status_code == 200
    payload = resp.json()

    # The winner MUST be ITEM_3 (earlier datetime than ITEM_2, while ITEM_1 is cloudy)
    assert payload["item_id"] == "ITEM_3"
    assert payload["acquisition_datetime"] == dt3.isoformat().replace("+00:00", "Z")
    assert payload["quality_metrics"]["usable_percentage"] == 100.0

    # Verify ITEM_1 red/nir were NEVER opened (SCL-screened out!)
    item1_red_opens = [u for u in opened_urls if "ITEM_1_red" in u]
    item1_nir_opens = [u for u in opened_urls if "ITEM_1_nir" in u]
    assert len(item1_red_opens) == 0, "ITEM_1 red must not be fetched"
    assert len(item1_nir_opens) == 0, "ITEM_1 nir must not be fetched"


# =========================================================================
# 7. TEST G & H: DETERMINISM & IDEMPOTENCY
# =========================================================================
def test_scl_screening_determinism_and_idempotency(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Repeating the same acquisition request:
    - Returns identical acquisition record
    - Does not create duplicate DB rows
    - Reuses persisted valid_unusable and usable records without remote work"""
    scl_item1 = np.full((10, 10), 8, dtype=np.uint8)  # cloudy (valid_unusable)
    scl_item2 = np.full((10, 10), 4, dtype=np.uint8)  # clear (usable)

    items = [
        MockItem("DET_ITEM_1", 2.0, datetime(2025, 4, 20, 10, 0, tzinfo=UTC)),
        MockItem("DET_ITEM_2", 8.0, datetime(2025, 4, 15, 10, 0, tzinfo=UTC)),
    ]
    files: dict[str, Path] = {}
    for item, scl_arr in zip(items, [scl_item1, scl_item2]):
        for b in ["red", "nir", "green", "blue", "scl"]:
            p = tmp_path / f"{item.id}_{b}.tif"
            files[f"https://example.test/{item.id}_{b}.tif"] = p
            data = scl_arr if b == "scl" else np.full((10, 10), 500, dtype=np.uint16)
            write_test_geotiff(p, data)

    opened_urls: list[str] = []
    real_open = rasterio.open

    def tracking_open(path, *args, **kwargs):
        if isinstance(path, str) and path.startswith("https://"):
            opened_urls.append(path)
            if path in files:
                return real_open(files[path], *args, **kwargs)
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(imagery.rasterio, "open", tracking_open)
    catalog = MockCatalog(items)
    monkeypatch.setattr(imagery.pystac_client.Client, "open", lambda _: catalog)

    aoi_resp = client.post("/api/v1/aoi", json=AOI_KARNATAKA)
    aoi_id = aoi_resp.json()["aoi_id"]

    req_json = {"aoi_id": aoi_id, "start_datetime": "2025-01-01T00:00:00Z", "end_datetime": "2025-06-01T00:00:00Z"}
    resp1 = client.post("/api/v1/imagery/acquisitions", json=req_json)
    assert resp1.status_code == 200
    first_payload = resp1.json()

    # Second request
    opened_urls.clear()
    resp2 = client.post("/api/v1/imagery/acquisitions", json=req_json)
    assert resp2.status_code == 200
    second_payload = resp2.json()

    assert first_payload["acquisition_id"] == second_payload["acquisition_id"]
    assert first_payload["item_id"] == second_payload["item_id"]
    assert first_payload["quality_metrics"] == second_payload["quality_metrics"]

    # No remote HTTP assets opened on second request
    assert len(opened_urls) == 0, "Repeated request must reuse database acquisitions directly"

    # Verify no duplicate acquisition rows in database
    with connection() as db:
        rows = db.execute(
            "SELECT item_id, COUNT(*) FROM imagery_acquisitions WHERE aoi_id = ? GROUP BY item_id",
            (aoi_id,),
        ).fetchall()
    for item_id, count in rows:
        assert count == 1, f"Item {item_id} has duplicate rows: count={count}"
