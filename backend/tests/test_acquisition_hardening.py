from __future__ import annotations

import io
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import rasterio
from fastapi.testclient import TestClient
from rasterio.enums import Resampling
from rasterio.transform import from_origin
from rasterio.warp import reproject

from app import imagery
from app.config import settings
from app.db import connection, initialize_database
from app.main import app
from app.schemas import AOIGeometry, ImageryAcquisitionRequest

client = TestClient(app)

AOI_KARNATAKA = {
    "type": "Polygon",
    "coordinates": [[[77.4, 12.8], [77.5, 12.8], [77.5, 12.9], [77.4, 12.9], [77.4, 12.8]]],
}


class MockAsset:
    def __init__(self, href: str) -> None:
        self.href = href


class MockItem:
    def __init__(
        self,
        item_id: str,
        cloud_cover: float = 0.0,
        assets: dict[str, MockAsset] | None = None,
        geometry: dict | None = None,
    ) -> None:
        self.id = item_id
        self.collection_id = "sentinel-2-l2a"
        self.datetime = datetime(2025, 4, 15, 10, 0, tzinfo=UTC)
        self.properties = {"datetime": self.datetime.isoformat(), "eo:cloud_cover": cloud_cover}
        self.assets = assets or {
            "red": MockAsset("https://example.test/red.tif"),
            "nir": MockAsset("https://example.test/nir.tif"),
            "green": MockAsset("https://example.test/green.tif"),
            "blue": MockAsset("https://example.test/blue.tif"),
            "scl": MockAsset("https://example.test/scl.tif"),
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
def isolated_hardening_env(tmp_path: Path):
    original_db = settings.database_path
    original_data_dir = settings.imagery_data_dir
    object.__setattr__(settings, "database_path", tmp_path / "test_hardening.db")
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
    tf = transform or from_origin(77.3, 13.0, 0.02, 0.02)
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
# FIX 1: SCL EQUIVALENCE — EMPIRICAL COMPARISON AGAINST FULL-TILE REFERENCE
# =========================================================================
def test_scl_windowed_ingestion_empirical_equivalence(tmp_path: Path):
    """Verify AOI-windowed SCL produces equivalent target SCL values, quality masks,
    usable/invalid/cloud/shadow pixel counts, and quality metrics compared to full-tile reference.
    Includes edge pixels and various Phase B classes."""
    # Create synthetic prepared raster (target grid: 6x6)
    prepared_path = tmp_path / "prepared_target.tif"
    prepared_data = np.ones((2, 6, 6), dtype=np.uint16) * 1000
    prepared_tf = from_origin(77.4, 12.9, 0.0166666666666667, 0.0166666666666667)
    write_test_geotiff(prepared_path, prepared_data, transform=prepared_tf)

    # Create synthetic source SCL raster (larger: 12x12) covering prepared bounds plus edges
    # Inject various Phase B classes across center and edges:
    # 4: vegetation (valid), 5: non-vegetated (valid), 6: water (valid)
    # 8: cloud high (cloud), 9: cloud med (cloud), 3: shadow (shadow), 0: nodata (invalid)
    scl_source_path = tmp_path / "scl_source.tif"
    scl_source_data = np.full((12, 12), 4, dtype=np.uint8)
    scl_source_data[0, :] = 0  # top edge invalid
    scl_source_data[-1, :] = 8  # bottom edge cloud
    scl_source_data[:, 0] = 3  # left edge shadow
    scl_source_data[:, -1] = 5  # right edge valid non-vegetated
    scl_source_data[4, 4] = 8  # center cloud
    scl_source_data[5, 5] = 9  # center cloud
    scl_source_data[2, 3] = 3  # shadow
    scl_source_data[3, 2] = 0  # nodata
    scl_source_tf = from_origin(77.35, 12.95, 0.0166666666666667, 0.0166666666666667)
    write_test_geotiff(scl_source_path, scl_source_data, transform=scl_source_tf)

    # 1. REFERENCE IMPLEMENTATION: Full-tile SCL read & reproject
    with rasterio.open(scl_source_path) as src, rasterio.open(prepared_path) as prep:
        full_dest = np.zeros((prep.height, prep.width), dtype=np.uint8)
        reproject(
            src.read(1),
            full_dest,
            src_transform=src.transform,
            src_crs=src.crs,
            src_nodata=0,
            dst_transform=prep.transform,
            dst_crs=prep.crs,
            dst_nodata=0,
            resampling=Resampling.nearest,
        )

    # Write reference mask
    ref_mask_path = tmp_path / "ref_mask.tif"
    with rasterio.open(prepared_path) as prep:
        prof = prep.profile.copy()
        prof.update(driver="GTiff", count=1, dtype="uint8", nodata=0)
        with rasterio.open(ref_mask_path, "w", **prof) as dst:
            dst.write(full_dest, 1)

    ref_assessment = imagery._assess_quality(prepared_path, ref_mask_path)

    # 2. NEW IMPLEMENTATION: Windowed _write_quality_mask
    item = SimpleNamespace(assets={"scl": MockAsset("https://example.test/scl.tif")})
    real_open = rasterio.open

    def mocked_open(path, *args, **kwargs):
        if str(path) == "https://example.test/scl.tif":
            return real_open(scl_source_path, *args, **kwargs)
        return real_open(path, *args, **kwargs)

    original_open = imagery.rasterio.open
    imagery.rasterio.open = mocked_open
    try:
        windowed_mask_path = imagery._write_quality_mask(item, prepared_path)
    finally:
        imagery.rasterio.open = original_open

    with rasterio.open(windowed_mask_path) as ds:
        windowed_dest = ds.read(1)

    windowed_assessment = imagery._assess_quality(prepared_path, windowed_mask_path)

    # Empirically verify equivalence
    assert np.array_equal(windowed_dest, full_dest), "AOI-target SCL values must be equivalent to full-tile reference"
    assert windowed_assessment.state == ref_assessment.state
    assert windowed_assessment.reason == ref_assessment.reason
    assert windowed_assessment.metrics["usable_pixels"] == ref_assessment.metrics["usable_pixels"]
    assert windowed_assessment.metrics["invalid_pixels"] == ref_assessment.metrics["invalid_pixels"]
    assert windowed_assessment.metrics["cloud_pixels"] == ref_assessment.metrics["cloud_pixels"]
    assert windowed_assessment.metrics["shadow_pixels"] == ref_assessment.metrics["shadow_pixels"]
    assert windowed_assessment.metrics["usable_percentage"] == ref_assessment.metrics["usable_percentage"]


# =========================================================================
# FIX 2: B04 REUSE — PROVE NO DUPLICATE REMOTE B04 RETRIEVAL
# =========================================================================
def test_b04_reuse_eliminates_duplicate_remote_fetch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Verify _prepare_display_raster reuses the already-prepared local B04 raster
    and does NOT open or fetch the remote B04 URL again."""
    y, x = np.mgrid[0:10, 0:10]
    red_data = (500 + x * 150 + y * 50).astype(np.uint16)
    green_data = (400 + x * 100 + y * 70).astype(np.uint16)
    blue_data = (300 + x * 80 + y * 60).astype(np.uint16)
    nir_data = (1000 + x * 200 + y * 100).astype(np.uint16)
    scl_data = np.full((10, 10), 4, dtype=np.uint8)

    files = {
        "https://example.test/red.tif": tmp_path / "red.tif",
        "https://example.test/green.tif": tmp_path / "green.tif",
        "https://example.test/blue.tif": tmp_path / "blue.tif",
        "https://example.test/nir.tif": tmp_path / "nir.tif",
        "https://example.test/scl.tif": tmp_path / "scl.tif",
    }
    write_test_geotiff(files["https://example.test/red.tif"], red_data)
    write_test_geotiff(files["https://example.test/green.tif"], green_data)
    write_test_geotiff(files["https://example.test/blue.tif"], blue_data)
    write_test_geotiff(files["https://example.test/nir.tif"], nir_data)
    write_test_geotiff(files["https://example.test/scl.tif"], scl_data)

    opened_urls: list[str] = []
    real_open = rasterio.open

    def tracking_open(path, *args, **kwargs):
        if isinstance(path, str) and path.startswith("https://"):
            opened_urls.append(path)
            if path in files:
                return real_open(files[path], *args, **kwargs)
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(imagery.rasterio, "open", tracking_open)

    catalog = MockCatalog([MockItem("S2_B04_REUSE_TEST", 0.0)])
    monkeypatch.setattr(imagery.pystac_client.Client, "open", lambda _: catalog)

    aoi_resp = client.post("/api/v1/aoi", json=AOI_KARNATAKA)
    aoi_id = aoi_resp.json()["aoi_id"]

    opened_urls.clear()
    acq_resp = client.post(
        "/api/v1/imagery/acquisitions",
        json={"aoi_id": aoi_id, "start_datetime": "2025-01-01T00:00:00Z", "end_datetime": "2025-06-01T00:00:00Z"},
    )
    assert acq_resp.status_code == 200

    # Count opens of remote red asset:
    red_opens = [url for url in opened_urls if "red.tif" in url]
    assert len(red_opens) == 1, (
        f"Remote red URL must be opened exactly once (in _prepare_raster), "
        f"not duplicated during display generation. Opened: {red_opens}"
    )
    # Green and blue should also be opened exactly once for display
    green_opens = [url for url in opened_urls if "green.tif" in url]
    blue_opens = [url for url in opened_urls if "blue.tif" in url]
    assert len(green_opens) == 1
    assert len(blue_opens) == 1


# =========================================================================
# FIX 3 & 5A: REPEAT REQUESTS DO NOT REDO WORK — USABLE OBSERVATIONS
# =========================================================================
def test_repeat_usable_request_reuses_persisted_acquisition(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """A repeated identical request for an existing usable observation reuses the persisted
    acquisition without reopening remote assets or regenerating artifacts."""
    y, x = np.mgrid[0:10, 0:10]
    red_data = (500 + x * 100).astype(np.uint16)
    nir_data = (1000 + x * 100).astype(np.uint16)
    green_data = (400 + x * 100).astype(np.uint16)
    blue_data = (300 + x * 100).astype(np.uint16)
    scl_data = np.full((10, 10), 4, dtype=np.uint8)

    files = {
        "https://example.test/red.tif": tmp_path / "red.tif",
        "https://example.test/green.tif": tmp_path / "green.tif",
        "https://example.test/blue.tif": tmp_path / "blue.tif",
        "https://example.test/nir.tif": tmp_path / "nir.tif",
        "https://example.test/scl.tif": tmp_path / "scl.tif",
    }
    for url, path in files.items():
        data = (
            red_data if "red" in url else nir_data if "nir" in url else green_data if "green" in url else blue_data if "blue" in url else scl_data
        )
        write_test_geotiff(path, data)

    opened_urls: list[str] = []
    real_open = rasterio.open

    def tracking_open(path, *args, **kwargs):
        if isinstance(path, str) and path.startswith("https://"):
            opened_urls.append(path)
            if path in files:
                return real_open(files[path], *args, **kwargs)
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(imagery.rasterio, "open", tracking_open)

    catalog = MockCatalog([MockItem("S2_REPEAT_USABLE", 0.0)])
    monkeypatch.setattr(imagery.pystac_client.Client, "open", lambda _: catalog)

    aoi_resp = client.post("/api/v1/aoi", json=AOI_KARNATAKA)
    aoi_id = aoi_resp.json()["aoi_id"]

    # First request: acquires from remote
    req_json = {"aoi_id": aoi_id, "start_datetime": "2025-01-01T00:00:00Z", "end_datetime": "2025-06-01T00:00:00Z"}
    resp1 = client.post("/api/v1/imagery/acquisitions", json=req_json)
    assert resp1.status_code == 200
    data1 = resp1.json()
    assert data1["observation_state"] == "usable"

    # Verify remote assets were opened on first request
    assert len(opened_urls) > 0
    opened_urls.clear()

    # Second request: must reuse persisted observation with zero remote asset downloads
    resp2 = client.post("/api/v1/imagery/acquisitions", json=req_json)
    assert resp2.status_code == 200
    data2 = resp2.json()

    # Remote assets must NOT have been opened on the second request
    assert len(opened_urls) == 0, f"Expected 0 remote asset opens on repeat request, got {opened_urls}"
    # Verify exact identity and provenance preservation
    assert data2["acquisition_id"] == data1["acquisition_id"]
    assert data2["item_id"] == data1["item_id"]
    assert data2["observation_state"] == "usable"
    assert data2["quality_metrics"] == data1["quality_metrics"]
    assert data2["prepared_path"] == data1["prepared_path"]
    assert data2["display_path"] == data1["display_path"]


# =========================================================================
# FIX 3 & 5B: REPEAT REQUESTS DO NOT REDO WORK — VALID_UNUSABLE OBSERVATIONS
# =========================================================================
def test_repeat_valid_unusable_request_reuses_persisted_acquisition(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """A cloudy observation assessed as valid_unusable is correctly cached and reused on subsequent
    requests without re-downloading remote assets or re-assessing quality.
    Also verifies display generation was skipped for valid_unusable."""
    y, x = np.mgrid[0:10, 0:10]
    red_data = (500 + x * 100).astype(np.uint16)
    nir_data = (1000 + x * 100).astype(np.uint16)
    # SCL: 8 = cloud (100% cloudy) -> valid_unusable
    scl_data = np.full((10, 10), 8, dtype=np.uint8)

    files = {
        "https://example.test/red.tif": tmp_path / "red.tif",
        "https://example.test/nir.tif": tmp_path / "nir.tif",
        "https://example.test/scl.tif": tmp_path / "scl.tif",
        "https://example.test/green.tif": tmp_path / "green.tif",
        "https://example.test/blue.tif": tmp_path / "blue.tif",
    }
    write_test_geotiff(files["https://example.test/red.tif"], red_data)
    write_test_geotiff(files["https://example.test/nir.tif"], nir_data)
    write_test_geotiff(files["https://example.test/scl.tif"], scl_data)
    write_test_geotiff(files["https://example.test/green.tif"], red_data)
    write_test_geotiff(files["https://example.test/blue.tif"], red_data)

    opened_urls: list[str] = []
    real_open = rasterio.open

    def tracking_open(path, *args, **kwargs):
        if isinstance(path, str) and path.startswith("https://"):
            opened_urls.append(path)
            if path in files:
                return real_open(files[path], *args, **kwargs)
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(imagery.rasterio, "open", tracking_open)

    catalog = MockCatalog([MockItem("S2_REPEAT_UNUSABLE", 80.0)])
    monkeypatch.setattr(imagery.pystac_client.Client, "open", lambda _: catalog)

    aoi_resp = client.post("/api/v1/aoi", json=AOI_KARNATAKA)
    aoi_id = aoi_resp.json()["aoi_id"]

    # First request: processes cloudy observation -> fails with NoSuitableImageryError (valid_unusable persisted)
    req_json = {"aoi_id": aoi_id, "start_datetime": "2025-01-01T00:00:00Z", "end_datetime": "2025-06-01T00:00:00Z"}
    resp1 = client.post("/api/v1/imagery/acquisitions", json=req_json)
    assert resp1.status_code == 404
    assert "No usable Sentinel-2 observation was available" in resp1.json()["message"]

    # Check that row in DB is valid_unusable and display_path is None (display generation skipped)
    with connection() as db:
        row = db.execute(
            "SELECT id, observation_state, quality_reason, quality_metrics_json, display_path FROM imagery_acquisitions WHERE aoi_id = ?",
            (aoi_id,),
        ).fetchone()
    assert row is not None
    assert row[1] == "valid_unusable"
    assert row[2] == "no_usable_pixels"
    assert row[4] is None, "Display artifact must NOT be generated for valid_unusable observations"
    initial_metrics = row[3]
    initial_id = row[0]

    # Verify remote assets were accessed on first run, then clear log
    assert len(opened_urls) > 0
    opened_urls.clear()

    # Second identical request: must reuse persisted valid_unusable record without re-downloading
    resp2 = client.post("/api/v1/imagery/acquisitions", json=req_json)
    assert resp2.status_code == 404
    assert "No usable Sentinel-2 observation was available" in resp2.json()["message"]

    # Remote assets must NOT have been opened on the second request
    assert len(opened_urls) == 0, f"Expected 0 remote opens on repeated valid_unusable request, got {opened_urls}"

    # Verify DB record was NOT mutated or recalculated
    with connection() as db:
        row2 = db.execute(
            "SELECT id, observation_state, quality_reason, quality_metrics_json, display_path FROM imagery_acquisitions WHERE aoi_id = ?",
            (aoi_id,),
        ).fetchone()
    assert row2[0] == initial_id
    assert row2[1] == "valid_unusable"
    assert row2[2] == "no_usable_pixels"
    assert row2[3] == initial_metrics


# =========================================================================
# FIX 5C: REPAIR BEHAVIOR PRESERVED FOR CORRUPTED / MISSING ARTIFACTS
# =========================================================================
def test_valid_unusable_missing_artifact_triggers_repair(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """If an artifact of a persisted valid_unusable observation is deleted/missing,
    the existing repair/re-acquisition path re-acquires and rebuilds the required artifacts."""
    y, x = np.mgrid[0:10, 0:10]
    red_data = (500 + x * 100).astype(np.uint16)
    nir_data = (1000 + x * 100).astype(np.uint16)
    scl_data = np.full((10, 10), 8, dtype=np.uint8)

    files = {
        "https://example.test/red.tif": tmp_path / "red.tif",
        "https://example.test/nir.tif": tmp_path / "nir.tif",
        "https://example.test/scl.tif": tmp_path / "scl.tif",
    }
    write_test_geotiff(files["https://example.test/red.tif"], red_data)
    write_test_geotiff(files["https://example.test/nir.tif"], nir_data)
    write_test_geotiff(files["https://example.test/scl.tif"], scl_data)

    opened_urls: list[str] = []
    real_open = rasterio.open

    def tracking_open(path, *args, **kwargs):
        if isinstance(path, str) and path.startswith("https://"):
            opened_urls.append(path)
            if path in files:
                return real_open(files[path], *args, **kwargs)
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(imagery.rasterio, "open", tracking_open)
    catalog = MockCatalog([MockItem("S2_REPAIR_TEST", 80.0)])
    monkeypatch.setattr(imagery.pystac_client.Client, "open", lambda _: catalog)

    aoi_resp = client.post("/api/v1/aoi", json=AOI_KARNATAKA)
    aoi_id = aoi_resp.json()["aoi_id"]

    req_json = {"aoi_id": aoi_id, "start_datetime": "2025-01-01T00:00:00Z", "end_datetime": "2025-06-01T00:00:00Z"}
    client.post("/api/v1/imagery/acquisitions", json=req_json)

    with connection() as db:
        row = db.execute(
            "SELECT prepared_path, quality_mask_path FROM imagery_acquisitions WHERE aoi_id = ?", (aoi_id,)
        ).fetchone()
    target_artifact = Path(row[0]) if (row[0] and Path(row[0]).is_file()) else Path(row[1])
    assert target_artifact.is_file()

    # Intentionally corrupt / delete the artifact to simulate data loss
    target_artifact.unlink()
    assert not target_artifact.is_file()

    opened_urls.clear()
    # Now run acquisition again: missing artifact must trigger repair/re-acquisition
    resp_repaired = client.post("/api/v1/imagery/acquisitions", json=req_json)
    assert resp_repaired.status_code == 404

    # Confirm repair re-accessed the remote assets and restored the missing artifact
    assert len(opened_urls) > 0, "Repair path must re-access assets when artifacts are missing"
    assert target_artifact.is_file(), "Repair path must have restored the missing artifact file"
