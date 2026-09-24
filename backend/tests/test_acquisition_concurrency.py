from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest
import rasterio
from fastapi.testclient import TestClient
from rasterio.transform import from_origin

from app import imagery
from app.config import settings
from app.db import connection, initialize_database
from app.exceptions import AssetAcquisitionError
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
        item_datetime: datetime | None = None,
        assets: dict[str, MockAsset] | None = None,
        geometry: dict | None = None,
    ) -> None:
        self.id = item_id
        self.collection_id = "sentinel-2-l2a"
        self.datetime = item_datetime or datetime(2024, 3, 28, 10, 0, tzinfo=UTC)
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
def isolated_env(tmp_path: Path):
    original_db = settings.database_path
    original_data_dir = settings.imagery_data_dir
    original_concurrency = settings.acquisition_concurrency
    object.__setattr__(settings, "database_path", tmp_path / "test_concurrency.db")
    object.__setattr__(settings, "imagery_data_dir", tmp_path / "imagery")
    initialize_database()
    yield
    object.__setattr__(settings, "database_path", original_db)
    object.__setattr__(settings, "imagery_data_dir", original_data_dir)
    object.__setattr__(settings, "acquisition_concurrency", original_concurrency)


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


def setup_mock_rasters(tmp_path: Path, items: list[MockItem], monkeypatch: pytest.MonkeyPatch, cloud_map: dict[str, int] | None = None):
    cloud_map = cloud_map or {}
    url_to_path: dict[str, Path] = {}
    for item in items:
        # SCL class: 4 is vegetation (usable), 8 is cloud (unusable)
        scl_class = cloud_map.get(item.id, 4)
        scl_arr = np.full((20, 20), scl_class, dtype=np.uint8)
        red_arr = np.full((20, 20), 1000, dtype=np.uint16)
        nir_arr = np.full((20, 20), 2500, dtype=np.uint16)
        green_arr = np.full((20, 20), 800, dtype=np.uint16)
        blue_arr = np.full((20, 20), 600, dtype=np.uint16)

        scl_p = tmp_path / f"{item.id}_scl.tif"
        red_p = tmp_path / f"{item.id}_red.tif"
        nir_p = tmp_path / f"{item.id}_nir.tif"
        green_p = tmp_path / f"{item.id}_green.tif"
        blue_p = tmp_path / f"{item.id}_blue.tif"

        write_test_geotiff(scl_p, scl_arr)
        write_test_geotiff(red_p, red_arr)
        write_test_geotiff(nir_p, nir_arr)
        write_test_geotiff(green_p, green_arr)
        write_test_geotiff(blue_p, blue_arr)

        url_to_path[item.assets["scl"].href] = scl_p
        url_to_path[item.assets["red"].href] = red_p
        url_to_path[item.assets["nir"].href] = nir_p
        url_to_path[item.assets["green"].href] = green_p
        url_to_path[item.assets["blue"].href] = blue_p

    orig_open = rasterio.open
    def mocked_open(uri, *args, **kwargs):
        if str(uri) in url_to_path:
            return orig_open(url_to_path[str(uri)], *args, **kwargs)
        return orig_open(uri, *args, **kwargs)

    monkeypatch.setattr(rasterio, "open", mocked_open)
    monkeypatch.setattr(imagery.pystac_client.Client, "open", lambda *args, **kwargs: MockCatalog(items))


def test_concurrency_result_equivalence(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Verify that concurrency=3 and concurrency=1 produce equivalent scientific results."""
    # 4 items: 2 usable (different cloud covers), 2 cloudy (SCL class 8)
    items = [
        MockItem("S2_ITEM_A", cloud_cover=15.0, item_datetime=datetime(2024, 2, 1, 10, 0, tzinfo=UTC)),
        MockItem("S2_ITEM_B", cloud_cover=5.0, item_datetime=datetime(2024, 2, 10, 10, 0, tzinfo=UTC)),
        MockItem("S2_ITEM_C", cloud_cover=90.0, item_datetime=datetime(2024, 2, 20, 10, 0, tzinfo=UTC)),
        MockItem("S2_ITEM_D", cloud_cover=95.0, item_datetime=datetime(2024, 3, 1, 10, 0, tzinfo=UTC)),
    ]
    cloud_map = {"S2_ITEM_C": 8, "S2_ITEM_D": 8}
    setup_mock_rasters(tmp_path, items, monkeypatch, cloud_map)

    # Save AOI
    resp = client.post("/api/v1/aoi", json=AOI_KARNATAKA)
    assert resp.status_code == 200
    aoi_id = resp.json()["aoi_id"]

    req = ImageryAcquisitionRequest(
        aoi_id=aoi_id,
        start_datetime=datetime(2024, 1, 1, 0, 0, tzinfo=UTC),
        end_datetime=datetime(2024, 3, 31, 23, 59, tzinfo=UTC),
    )

    # 1. Run with concurrency=1 (sequential baseline)
    object.__setattr__(settings, "acquisition_concurrency", 1)
    res_seq = imagery.acquire_imagery(req)

    # 2. Re-run on clean isolated environment with concurrency=3
    db_concur = tmp_path / "test_concur.db"
    imagery_concur = tmp_path / "imagery_concur"
    object.__setattr__(settings, "database_path", db_concur)
    object.__setattr__(settings, "imagery_data_dir", imagery_concur)
    object.__setattr__(settings, "acquisition_concurrency", 3)
    initialize_database()

    resp = client.post("/api/v1/aoi", json=AOI_KARNATAKA)
    assert resp.status_code == 200
    aoi_id_concur = resp.json()["aoi_id"]

    req_concur = ImageryAcquisitionRequest(
        aoi_id=aoi_id_concur,
        start_datetime=datetime(2024, 1, 1, 0, 0, tzinfo=UTC),
        end_datetime=datetime(2024, 3, 31, 23, 59, tzinfo=UTC),
    )

    res_concur = imagery.acquire_imagery(req_concur)

    # Scientific equivalence checks
    assert res_seq.item_id == res_concur.item_id
    assert res_seq.observation_state == res_concur.observation_state
    assert res_seq.quality_reason == res_concur.quality_reason
    assert res_seq.quality_metrics["usable_percentage"] == res_concur.quality_metrics["usable_percentage"]
    assert res_seq.acquisition_datetime == res_concur.acquisition_datetime


def test_concurrent_streaming_progress_monotonicity(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Verify that under concurrency=3:
    - Events arrive with monotonic current (0 <= current <= total)
    - Every item reaches a terminal state
    - Complete event is emitted once at current == total."""
    items = [
        MockItem(f"S2_SCENE_{i}", cloud_cover=float(i * 10), item_datetime=datetime(2024, 2, i + 1, 10, 0, tzinfo=UTC))
        for i in range(5)
    ]
    cloud_map = {"S2_SCENE_3": 8, "S2_SCENE_4": 8}
    setup_mock_rasters(tmp_path, items, monkeypatch, cloud_map)

    resp = client.post("/api/v1/aoi", json=AOI_KARNATAKA)
    aoi_id = resp.json()["aoi_id"]

    object.__setattr__(settings, "acquisition_concurrency", 3)
    req = ImageryAcquisitionRequest(
        aoi_id=aoi_id,
        start_datetime=datetime(2024, 1, 1, 0, 0, tzinfo=UTC),
        end_datetime=datetime(2024, 3, 31, 23, 59, tzinfo=UTC),
    )

    events = list(imagery.acquire_imagery_events(req))
    assert len(events) >= 6  # 1 discovered + 5 per-item progress + 1 complete

    assert events[0]["type"] == "progress"
    assert events[0]["state"] == "discovered"
    assert events[0]["current"] == 0
    assert events[0]["total"] == 5

    # Check monotonicity of progress events
    current_val = 0
    progress_item_ids: set[str] = set()
    for ev in events[1:-1]:
        assert ev["type"] == "progress"
        assert ev["total"] == 5
        assert ev["current"] >= current_val
        assert ev["current"] <= 5
        current_val = ev["current"]
        assert ev["item_id"] is not None
        progress_item_ids.add(ev["item_id"])

    assert progress_item_ids == {f"S2_SCENE_{i}" for i in range(5)}
    assert current_val == 5

    # Final event
    final_ev = events[-1]
    assert final_ev["type"] == "complete"
    assert final_ev["current"] == 5
    assert final_ev["total"] == 5
    assert final_ev["result"]["observation_state"] == "usable"


def test_concurrent_worker_failure_containment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Verify that if one worker encounters an AssetAcquisitionError:
    - That item is recorded as failed in SQLite
    - Other workers proceed and complete successfully
    - Usable items remain usable in the final result."""
    items = [
        MockItem("S2_GOOD_1", cloud_cover=10.0, item_datetime=datetime(2024, 2, 1, 10, 0, tzinfo=UTC)),
        MockItem("S2_BAD_2", cloud_cover=10.0, item_datetime=datetime(2024, 2, 5, 10, 0, tzinfo=UTC)),
        MockItem("S2_GOOD_3", cloud_cover=10.0, item_datetime=datetime(2024, 2, 10, 10, 0, tzinfo=UTC)),
    ]
    setup_mock_rasters(tmp_path, items, monkeypatch)

    # Monkeypatch _prepare_raster to raise AssetAcquisitionError for S2_BAD_2
    orig_prepare = imagery._prepare_raster
    def failing_prepare(item, aoi_id, aoi):
        if item.id == "S2_BAD_2":
            raise AssetAcquisitionError("Simulated remote asset download failure")
        return orig_prepare(item, aoi_id, aoi)

    monkeypatch.setattr(imagery, "_prepare_raster", failing_prepare)

    resp = client.post("/api/v1/aoi", json=AOI_KARNATAKA)
    aoi_id = resp.json()["aoi_id"]

    object.__setattr__(settings, "acquisition_concurrency", 3)
    req = ImageryAcquisitionRequest(
        aoi_id=aoi_id,
        start_datetime=datetime(2024, 1, 1, 0, 0, tzinfo=UTC),
        end_datetime=datetime(2024, 3, 31, 23, 59, tzinfo=UTC),
    )

    result = imagery.acquire_imagery(req)
    assert result.observation_state == "usable"
    assert result.item_id in ("S2_GOOD_1", "S2_GOOD_3")

    # Check database persistence
    with connection() as db:
        rows = db.execute("SELECT item_id, observation_state, quality_reason FROM imagery_acquisitions ORDER BY id").fetchall()
        states_by_item = {r[0]: (r[1], r[2]) for r in rows}

        assert states_by_item["S2_GOOD_1"][0] == "usable"
        assert states_by_item["S2_GOOD_3"][0] == "usable"
        assert states_by_item["S2_BAD_2"][0] == "failed"
        assert "Simulated remote asset download failure" in states_by_item["S2_BAD_2"][1]
