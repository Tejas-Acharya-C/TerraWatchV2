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
from app.main import app

client = TestClient(app)

AOI = {
    "type": "Polygon",
    "coordinates": [[[77.4, 12.8], [77.5, 12.8], [77.5, 12.9], [77.4, 12.9], [77.4, 12.8]]],
}
RED_HREF = "https://example.test/red.tif"
NIR_HREF = "https://example.test/nir.tif"
SCL_HREF = "https://example.test/scl.tif"


class FakeAsset:
    def __init__(self, href: str) -> None:
        self.href = href


class FakeItem:
    def __init__(self, item_id: str, cloud_cover: float, assets: dict[str, FakeAsset] | None = None, geometry: dict | None = None) -> None:
        self.id = item_id
        self.collection_id = "sentinel-2-l2a"
        self.datetime = datetime(2024, 3, 28, 10, 0, tzinfo=UTC)
        self.properties = {"datetime": self.datetime.isoformat(), "eo:cloud_cover": cloud_cover}
        self.assets = assets or {
            "red": FakeAsset(RED_HREF),
            "nir": FakeAsset(NIR_HREF),
            "scl": FakeAsset(SCL_HREF),
        }
        self.geometry = geometry or {"type": "Polygon", "coordinates": [[[77.0, 12.0], [78.0, 12.0], [78.0, 13.5], [77.0, 13.5], [77.0, 12.0]]]}


class FakeSearch:
    def __init__(self, items: list[FakeItem]) -> None:
        self._items = items

    def items(self):
        return iter(self._items)


class FakeCatalog:
    def __init__(self, items: list[FakeItem]) -> None:
        self.items = items
        self.search_kwargs = None

    def search(self, **kwargs):
        self.search_kwargs = kwargs
        return FakeSearch(self.items)


@pytest.fixture(autouse=True)
def isolated_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    original_database = settings.database_path
    original_data_dir = settings.imagery_data_dir
    object.__setattr__(settings, "database_path", tmp_path / "test.db")
    object.__setattr__(settings, "imagery_data_dir", tmp_path / "imagery")
    initialize_database()
    yield
    object.__setattr__(settings, "database_path", original_database)
    object.__setattr__(settings, "imagery_data_dir", original_data_dir)


def save_aoi() -> int:
    response = client.post("/api/v1/aoi", json=AOI)
    assert response.status_code == 200
    return response.json()["aoi_id"]


def write_test_asset(path: Path, value: int) -> None:
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=10,
        height=10,
        count=1,
        dtype="uint16",
        crs="EPSG:4326",
        transform=from_origin(77, 13.5, 0.1, 0.1),
        nodata=0,
    ) as dataset:
        dataset.write(np.full((10, 10), value, dtype=np.uint16), 1)


def write_test_scl(path: Path) -> None:
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=10,
        height=10,
        count=1,
        dtype="uint8",
        crs="EPSG:4326",
        transform=from_origin(77, 13.5, 0.1, 0.1),
        nodata=0,
    ) as dataset:
        dataset.write(np.full((10, 10), 4, dtype=np.uint8), 1)


def patch_catalog_and_assets(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, items: list[FakeItem]):
    catalog = FakeCatalog(items)
    monkeypatch.setattr(imagery.pystac_client.Client, "open", lambda _: catalog)
    red_path = tmp_path / "red.tif"
    nir_path = tmp_path / "nir.tif"
    scl_path = tmp_path / "scl.tif"
    write_test_asset(red_path, 10)
    write_test_asset(nir_path, 20)
    write_test_scl(scl_path)
    real_open = imagery.rasterio.open

    def open_asset(path, *args, **kwargs):
        if path == RED_HREF:
            path = red_path
        elif path == NIR_HREF:
            path = nir_path
        elif path == SCL_HREF:
            path = scl_path
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(imagery.rasterio, "open", open_asset)
    return catalog


def test_streaming_acquisition_emits_progress_and_complete(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    aoi_id = save_aoi()
    items = [FakeItem("item_one", 10.0), FakeItem("item_two", 5.0)]
    patch_catalog_and_assets(monkeypatch, tmp_path, items)

    response = client.post(
        "/api/v1/imagery/acquisitions?stream=true",
        json={"aoi_id": aoi_id, "start_datetime": "2024-01-01T00:00:00Z", "end_datetime": "2024-04-01T00:00:00Z"},
    )
    assert response.status_code == 200
    assert "application/x-ndjson" in response.headers["content-type"]

    lines = [line for line in response.text.strip().split("\n") if line.strip()]
    events = [json.loads(line) for line in lines]

    # First event should be discovered with total=2
    assert events[0]["type"] == "progress"
    assert events[0]["state"] == "discovered"
    assert events[0]["total"] == 2
    assert events[0]["current"] == 0

    # Intermediate progress events
    item_ids_processed = {e["item_id"] for e in events if e.get("item_id")}
    assert "item_one" in item_ids_processed
    assert "item_two" in item_ids_processed

    # Last event should be complete
    last_event = events[-1]
    assert last_event["type"] == "complete"
    assert last_event["total"] == 2
    assert last_event["current"] == 2
    assert "result" in last_event
    assert last_event["result"]["aoi_id"] == aoi_id

    # Verify no unexpected session/workflow tables were created
    with connection() as db:
        tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        assert "imagery_acquisitions" in tables
        assert "aoi" in tables
        for unexpected in ("job", "jobs", "workflow_session", "session"):
            assert unexpected not in tables


def test_non_streaming_acquisition_returns_standard_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    aoi_id = save_aoi()
    items = [FakeItem("item_standard", 5.0)]
    patch_catalog_and_assets(monkeypatch, tmp_path, items)

    response = client.post(
        "/api/v1/imagery/acquisitions",
        json={"aoi_id": aoi_id, "start_datetime": "2024-01-01T00:00:00Z", "end_datetime": "2024-04-01T00:00:00Z"},
    )
    assert response.status_code == 200
    assert "application/json" in response.headers["content-type"]
    data = response.json()
    assert data["item_id"] == "item_standard"
    assert data["aoi_id"] == aoi_id


def test_streaming_acquisition_failure_emits_error_event(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    aoi_id = save_aoi()
    catalog = FakeCatalog([])
    monkeypatch.setattr(imagery.pystac_client.Client, "open", lambda _: catalog)

    response = client.post(
        "/api/v1/imagery/acquisitions?stream=true",
        json={"aoi_id": aoi_id, "start_datetime": "2024-01-01T00:00:00Z", "end_datetime": "2024-04-01T00:00:00Z"},
    )
    # Since catalog query failed inside the generator, it emitted an error event
    lines = [line for line in response.text.strip().split("\n") if line.strip()]
    events = [json.loads(line) for line in lines]
    assert len(events) >= 1
    assert events[-1]["type"] == "error"
    assert events[-1]["code"] in ("NoSuitableImageryError", "TerraWatchError")
