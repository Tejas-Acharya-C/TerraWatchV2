from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
import numpy as np
import rasterio
from fastapi.testclient import TestClient
from rasterio.errors import RasterioIOError
from rasterio.transform import from_origin

from app import imagery
from app.config import settings
from app.db import connection, initialize_database
from app.exceptions import RasterValidationError
from app.main import app
from app.schemas import ImageryAcquisitionRequest

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


def test_search_uses_aoi_datetime_collection_and_selects_lowest_cloud(
    monkeypatch: pytest.MonkeyPatch,
):
    aoi_id = save_aoi()
    catalog = FakeCatalog([FakeItem("cloudy", 40), FakeItem("clear", 5)])
    monkeypatch.setattr(imagery.pystac_client.Client, "open", lambda _: catalog)
    monkeypatch.setattr(imagery, "_prepare_raster", lambda *_: (_ for _ in ()).throw(RasterValidationError("stop")))

    response = client.post(
        "/api/v1/imagery/acquisitions",
        json={"aoi_id": aoi_id, "start_datetime": "2024-01-01T00:00:00Z", "end_datetime": "2024-04-01T00:00:00Z"},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "RasterValidationError"
    assert catalog.search_kwargs["collections"] == ["sentinel-2-l2a"]
    assert catalog.search_kwargs["intersects"] == AOI
    assert catalog.search_kwargs["datetime"] == "2024-01-01T00:00:00+00:00/2024-04-01T00:00:00+00:00"


def test_success_prepares_realistic_raster_metadata_and_persists_provenance(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    aoi_id = save_aoi()
    patch_catalog_and_assets(monkeypatch, tmp_path, [FakeItem("S2_TEST", 5)])
    response = client.post(
        "/api/v1/imagery/acquisitions",
        json={"aoi_id": aoi_id, "start_datetime": "2024-01-01T00:00:00Z", "end_datetime": "2024-04-01T00:00:00Z"},
    )
    assert response.status_code == 200, response.json()
    payload = response.json()
    assert payload["item_id"] == "S2_TEST"
    assert payload["raster"]["crs"] == "EPSG:4326"
    assert payload["raster"]["count"] == 2
    assert payload["observation_state"] == "usable"
    assert payload["quality_reason"] == "quality_policy_passed"
    assert payload["quality_metrics"]["usable_percentage"] == 100.0
    assert payload["quality_asset_id"] == "scl"
    assert payload["masking_method"] == "sentinel-2-scl-nearest-v1"
    assert Path(payload["quality_mask_path"]).exists()
    assert Path(payload["prepared_path"]).exists()

    latest = client.get("/api/v1/imagery/acquisitions/latest")
    assert latest.status_code == 200
    with connection() as db_connection:
        row = db_connection.execute(
            "SELECT assets_json, prepared_path, observation_state, quality_metrics_json, quality_mask_path FROM imagery_acquisitions"
        ).fetchone()
    assert json.loads(row[0])[0]["asset_id"] == "red"
    assert row[1] == payload["prepared_path"]
    assert [asset["asset_id"] for asset in json.loads(row[0])] == ["red", "nir", "scl"]
    assert row[2] == "usable"
    assert json.loads(row[3])["usable_pixels"] == 1
    assert row[4] == payload["quality_mask_path"]


def test_invalid_date_range_is_rejected_before_catalog_call(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(imagery.pystac_client.Client, "open", lambda _: pytest.fail("catalog called"))
    response = client.post(
        "/api/v1/imagery/acquisitions",
        json={"start_datetime": "2024-04-01T00:00:00Z", "end_datetime": "2024-01-01T00:00:00Z"},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "ValidationError"


def test_missing_aoi_is_explicit():
    response = client.post(
        "/api/v1/imagery/acquisitions",
        json={"start_datetime": "2024-01-01T00:00:00Z", "end_datetime": "2024-04-01T00:00:00Z"},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "ValidationError"


def test_explicit_aoi_id_is_required_for_imagery_request(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    first_aoi = client.post("/api/v1/aoi", json=AOI)
    assert first_aoi.status_code == 200
    assert "aoi_id" in first_aoi.json()

    second_aoi = {
        "type": "Polygon",
        "coordinates": [[[77.6, 12.8], [77.7, 12.8], [77.7, 12.9], [77.6, 12.9], [77.6, 12.8]]],
    }
    second_response = client.post("/api/v1/aoi", json=second_aoi)
    assert second_response.status_code == 200
    second_id = second_response.json()["aoi_id"]
    assert second_id != first_aoi.json()["aoi_id"]

    catalog = FakeCatalog([FakeItem("S2_TEST", 5)])
    monkeypatch.setattr(imagery.pystac_client.Client, "open", lambda _: catalog)
    patch_catalog_and_assets(monkeypatch, tmp_path, [FakeItem("S2_TEST", 5)])

    response = client.post(
        "/api/v1/imagery/acquisitions",
        json={"aoi_id": second_id, "start_datetime": "2024-01-01T00:00:00Z", "end_datetime": "2024-04-01T00:00:00Z"},
    )
    assert response.status_code == 200, response.json()
    payload = response.json()
    assert payload["aoi_id"] == second_id


def test_stale_outside_karnataka_aoi_is_rejected_before_stac(
    monkeypatch: pytest.MonkeyPatch,
):
    stale_geometry = {
        "type": "Polygon",
        "coordinates": [[[20, 30], [21, 30], [21, 31], [20, 31], [20, 30]]],
    }
    with connection() as db_connection:
        db_connection.execute(
            "INSERT INTO aoi (id, geometry_json, created_at, updated_at) VALUES (1, ?, 'stale', 'stale')",
            (json.dumps(stale_geometry),),
        )
        db_connection.commit()
    monkeypatch.setattr(
        imagery.pystac_client.Client,
        "open",
        lambda _: pytest.fail("STAC must not be queried"),
    )

    response = client.post(
        "/api/v1/imagery/acquisitions",
        json={"aoi_id": 1, "start_datetime": "2024-01-01T00:00:00Z", "end_datetime": "2024-04-01T00:00:00Z"},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "AOIOutsideKarnatakaError"


def test_no_imagery_is_distinct_from_catalog_failure(
    monkeypatch: pytest.MonkeyPatch,
):
    aoi_id = save_aoi()
    catalog = FakeCatalog([])
    monkeypatch.setattr(imagery.pystac_client.Client, "open", lambda _: catalog)
    response = client.post(
        "/api/v1/imagery/acquisitions",
        json={"aoi_id": aoi_id, "start_datetime": "2024-01-01T00:00:00Z", "end_datetime": "2024-04-01T00:00:00Z"},
    )
    assert response.status_code == 404
    assert response.json()["code"] == "NoSuitableImageryError"

    monkeypatch.setattr(imagery.pystac_client.Client, "open", lambda _: (_ for _ in ()).throw(RuntimeError("offline")))
    response = client.post(
        "/api/v1/imagery/acquisitions",
        json={"aoi_id": aoi_id, "start_datetime": "2024-01-01T00:00:00Z", "end_datetime": "2024-04-01T00:00:00Z"},
    )
    assert response.status_code == 502
    assert response.json()["code"] == "CatalogFailureError"


def test_item_outside_karnataka_is_rejected(monkeypatch: pytest.MonkeyPatch):
    aoi_id = save_aoi()
    outside_geometry = {"type": "Polygon", "coordinates": [[[20, 30], [21, 30], [21, 31], [20, 31], [20, 30]]]}
    catalog = FakeCatalog([FakeItem("OUTSIDE", 0, geometry=outside_geometry)])
    monkeypatch.setattr(imagery.pystac_client.Client, "open", lambda _: catalog)

    response = client.post(
        "/api/v1/imagery/acquisitions",
        json={"aoi_id": aoi_id, "start_datetime": "2024-01-01T00:00:00Z", "end_datetime": "2024-04-01T00:00:00Z"},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "GeographicDomainError"


def test_asset_failure_is_distinct_from_raster_validation(
    monkeypatch: pytest.MonkeyPatch,
):
    aoi_id = save_aoi()
    catalog = FakeCatalog([FakeItem("S2_TEST", 5)])
    monkeypatch.setattr(imagery.pystac_client.Client, "open", lambda _: catalog)
    monkeypatch.setattr(imagery.rasterio, "open", lambda *args, **kwargs: (_ for _ in ()).throw(RasterioIOError("missing")))
    response = client.post(
        "/api/v1/imagery/acquisitions",
        json={"aoi_id": aoi_id, "start_datetime": "2024-01-01T00:00:00Z", "end_datetime": "2024-04-01T00:00:00Z"},
    )
    assert response.status_code == 502
    assert response.json()["code"] == "AssetAcquisitionError"


def test_latest_without_acquisition_is_explicit():
    response = client.get("/api/v1/imagery/acquisitions/latest")
    assert response.status_code == 404
    assert response.json()["code"] == "NoAcquisitionError"


def test_repeated_item_returns_canonical_acquisition_and_request_window(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    aoi_id = save_aoi()
    patch_catalog_and_assets(monkeypatch, tmp_path, [FakeItem("S2_REPEAT", 5)])
    first = client.post(
        "/api/v1/imagery/acquisitions",
        json={"aoi_id": aoi_id, "start_datetime": "2024-01-01T00:00:00Z", "end_datetime": "2024-04-01T00:00:00Z"},
    )
    second = client.post(
        "/api/v1/imagery/acquisitions",
        json={"aoi_id": aoi_id, "start_datetime": "2024-01-01T06:00:00Z", "end_datetime": "2024-12-01T00:00:00Z"},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["acquisition_id"] == first.json()["acquisition_id"]
    assert second.json()["requested_start_datetime"] == "2024-01-01T00:00:00Z"
    assert second.json()["requested_end_datetime"] == "2024-04-01T00:00:00Z"
    with connection() as db_connection:
        assert db_connection.execute("SELECT COUNT(*) FROM imagery_acquisitions").fetchone()[0] == 1


def test_same_item_for_different_aois_has_distinct_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    first_aoi = save_aoi()
    second_response = client.post(
        "/api/v1/aoi",
        json={"type": "Polygon", "coordinates": [[[77.6, 12.8], [77.7, 12.8], [77.7, 12.9], [77.6, 12.9], [77.6, 12.8]]]},
    )
    second_aoi = second_response.json()["aoi_id"]
    patch_catalog_and_assets(monkeypatch, tmp_path, [FakeItem("S2_CROSS_AOI", 5)])
    request = {"start_datetime": "2024-01-01T00:00:00Z", "end_datetime": "2024-04-01T00:00:00Z"}

    first = client.post("/api/v1/imagery/acquisitions", json={"aoi_id": first_aoi, **request})
    second = client.post("/api/v1/imagery/acquisitions", json={"aoi_id": second_aoi, **request})

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["acquisition_id"] != second.json()["acquisition_id"]
    assert first.json()["prepared_path"] != second.json()["prepared_path"]
    assert Path(first.json()["prepared_path"]).exists()
    assert Path(second.json()["prepared_path"]).exists()


def test_missing_existing_raster_is_repaired_without_new_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    aoi_id = save_aoi()
    patch_catalog_and_assets(monkeypatch, tmp_path, [FakeItem("S2_REPAIR", 5)])
    request = {"aoi_id": aoi_id, "start_datetime": "2024-01-01T00:00:00Z", "end_datetime": "2024-04-01T00:00:00Z"}
    first = client.post("/api/v1/imagery/acquisitions", json=request)
    first_payload = first.json()
    Path(first_payload["prepared_path"]).unlink()

    second = client.post("/api/v1/imagery/acquisitions", json=request)

    assert second.status_code == 200
    assert second.json()["acquisition_id"] == first_payload["acquisition_id"]
    assert Path(second.json()["prepared_path"]).exists()
    with connection() as db_connection:
        assert db_connection.execute("SELECT COUNT(*) FROM imagery_acquisitions").fetchone()[0] == 1


def test_database_rejects_duplicate_acquisition_identity():
    with connection() as db_connection:
        db_connection.execute(
            "INSERT INTO aoi (id, geometry_json, created_at, updated_at) VALUES (1, ?, 'now', 'now')",
            (json.dumps(AOI),),
        )
        values = (1, "S2_UNIQUE", "path", "{}", "{}")
        db_connection.execute(
            """
            INSERT INTO imagery_acquisitions (
                aoi_id, requested_start_datetime, requested_end_datetime, item_id,
                collection, acquisition_datetime, assets_json, prepared_path,
                raster_metadata_json, source_metadata_json, created_at
            ) VALUES (?, 'start', 'end', ?, 'collection', 'acquired', '[]', ?, ?, ?, 'created')
            """,
            values,
        )
        with pytest.raises(sqlite3.IntegrityError):
            db_connection.execute(
                """
                INSERT INTO imagery_acquisitions (
                    aoi_id, requested_start_datetime, requested_end_datetime, item_id,
                    collection, acquisition_datetime, assets_json, prepared_path,
                    raster_metadata_json, source_metadata_json, created_at
                ) VALUES (?, 'start2', 'end2', ?, 'collection', 'acquired', '[]', ?, ?, ?, 'created2')
                """,
                values,
            )


def test_uniqueness_race_returns_one_canonical_acquisition(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    aoi_id = save_aoi()
    item = FakeItem("S2_RACE", 5)
    patch_catalog_and_assets(monkeypatch, tmp_path, [item])
    request = ImageryAcquisitionRequest(
        aoi_id=aoi_id,
        start_datetime=datetime(2024, 1, 1, tzinfo=UTC),
        end_datetime=datetime(2024, 4, 1, tzinfo=UTC),
    )

    real_prepare = imagery._prepare_raster

    def synchronized_prepare(item, item_aoi_id, aoi):
        prepared_path, raster = real_prepare(item, item_aoi_id, aoi)
        with connection() as db_connection:
            db_connection.execute(
                """
                INSERT INTO imagery_acquisitions (
                    aoi_id, requested_start_datetime, requested_end_datetime,
                    item_id, collection, acquisition_datetime, assets_json,
                    prepared_path, raster_metadata_json, source_metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item_aoi_id,
                    request.start_datetime.isoformat(),
                    request.end_datetime.isoformat(),
                    item.id,
                    item.collection_id,
                    item.datetime.isoformat(),
                    json.dumps([{"asset_id": asset_id, "href": asset.href} for asset_id, asset in item.assets.items()]),
                    str(prepared_path),
                    raster.model_dump_json(),
                    json.dumps(item.properties),
                    datetime.now(UTC).isoformat(),
                ),
            )
            db_connection.commit()
        return prepared_path, raster

    monkeypatch.setattr(imagery, "_prepare_raster", synchronized_prepare)
    result = imagery.acquire_imagery(request)

    assert result.acquisition_id == 1
    with connection() as db_connection:
        assert db_connection.execute("SELECT COUNT(*) FROM imagery_acquisitions").fetchone()[0] == 1
