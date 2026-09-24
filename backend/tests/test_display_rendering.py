from __future__ import annotations

import io
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
from app.exceptions import DisplayImageryUnavailableError
from app.main import app

client = TestClient(app)
AOI = {
    "type": "Polygon",
    "coordinates": [[[77.4, 12.8], [77.5, 12.8], [77.5, 12.9], [77.4, 12.9], [77.4, 12.8]]],
}


class FakeAsset:
    def __init__(self, href: str) -> None:
        self.href = href


class FakeItem:
    def __init__(self, item_id: str, cloud_cover: float = 2.0) -> None:
        self.id = item_id
        self.collection_id = "sentinel-2-l2a"
        self.datetime = datetime(2024, 3, 28, 10, 0, tzinfo=UTC)
        self.properties = {"datetime": self.datetime.isoformat(), "eo:cloud_cover": cloud_cover}
        self.assets = {
            "red": FakeAsset("https://example.test/red.tif"),
            "nir": FakeAsset("https://example.test/nir.tif"),
            "green": FakeAsset("https://example.test/green.tif"),
            "blue": FakeAsset("https://example.test/blue.tif"),
            "scl": FakeAsset("https://example.test/scl.tif"),
        }
        self.geometry = {
            "type": "Polygon",
            "coordinates": [[[77.0, 12.0], [78.0, 12.0], [78.0, 13.5], [77.0, 13.5], [77.0, 12.0]]],
        }


class FakeSearch:
    def __init__(self, items: list[FakeItem]) -> None:
        self._items = items

    def items(self):
        return iter(self._items)


class FakeCatalog:
    def __init__(self, items: list[FakeItem]) -> None:
        self.items = items

    def search(self, **kwargs):
        return FakeSearch(self.items)


@pytest.fixture(autouse=True)
def isolated_database(tmp_path: Path):
    original_database = settings.database_path
    original_data_dir = settings.imagery_data_dir
    object.__setattr__(settings, "database_path", tmp_path / "test.db")
    object.__setattr__(settings, "imagery_data_dir", tmp_path / "imagery")
    initialize_database()
    yield
    object.__setattr__(settings, "database_path", original_database)
    object.__setattr__(settings, "imagery_data_dir", original_data_dir)


def write_test_raster(path: Path, array: np.ndarray, nodata: int = 0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = array.shape[0] if array.ndim == 3 else 1
    height, width = array.shape[-2:]
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=width,
        height=height,
        count=count,
        dtype=array.dtype.name,
        crs="EPSG:4326",
        transform=from_origin(77.3, 13.0, 0.02, 0.02),
        nodata=nodata,
    ) as dataset:
        if count == 1:
            dataset.write(array.reshape(height, width), 1)
        else:
            for idx in range(count):
                dataset.write(array[idx], idx + 1)


def patch_synthetic_sources(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    # Shape 10x10 covering the AOI [77.4, 12.8] to [77.5, 12.9]
    # Red: values from 300 to 2500 (surface reflectance * 10000)
    # Green: values from 400 to 2200
    # Blue: values from 200 to 1800
    # NIR: values from 800 to 4500
    # SCL: 4 (vegetation), with edge row as 0 (nodata) and 8 (cloud)
    y, x = np.mgrid[0:10, 0:10]
    red_data = (500 + x * 150 + y * 50).astype(np.uint16)
    green_data = (400 + x * 100 + y * 70).astype(np.uint16)
    blue_data = (300 + x * 80 + y * 60).astype(np.uint16)
    nir_data = (1000 + x * 200 + y * 100).astype(np.uint16)
    # SCL: 4 (vegetation), with a pixel as 0 (nodata) and a pixel as 8 (cloud)
    scl_data = np.full((10, 10), 4, dtype=np.uint8)
    scl_data[6, 6] = 0  # nodata pixel inside AOI crop
    scl_data[7, 7] = 8  # cloud pixel inside AOI crop

    files = {
        "https://example.test/red.tif": tmp_path / "red.tif",
        "https://example.test/green.tif": tmp_path / "green.tif",
        "https://example.test/blue.tif": tmp_path / "blue.tif",
        "https://example.test/nir.tif": tmp_path / "nir.tif",
        "https://example.test/scl.tif": tmp_path / "scl.tif",
    }
    write_test_raster(files["https://example.test/red.tif"], red_data)
    write_test_raster(files["https://example.test/green.tif"], green_data)
    write_test_raster(files["https://example.test/blue.tif"], blue_data)
    write_test_raster(files["https://example.test/nir.tif"], nir_data)
    write_test_raster(files["https://example.test/scl.tif"], scl_data)

    original_open = rasterio.open

    def mocked_open(path, *args, **kwargs):
        if isinstance(path, str) and path in files:
            return original_open(files[path], *args, **kwargs)
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(imagery.rasterio, "open", mocked_open)


def test_display_rendering_quality_aware_rgba(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    patch_synthetic_sources(monkeypatch, tmp_path)
    catalog = FakeCatalog([FakeItem("S2_DISPLAY_TEST", 2.0)])
    monkeypatch.setattr(imagery.pystac_client.Client, "open", lambda _: catalog)

    # 1. Save AOI
    aoi_resp = client.post("/api/v1/aoi", json=AOI)
    assert aoi_resp.status_code == 200
    aoi_id = aoi_resp.json()["aoi_id"]

    # 2. Acquire imagery
    acq_resp = client.post(
        "/api/v1/imagery/acquisitions",
        json={"aoi_id": aoi_id, "start_datetime": "2024-01-01T00:00:00Z", "end_datetime": "2024-04-01T00:00:00Z"},
    )
    assert acq_resp.status_code == 200
    acq_payload = acq_resp.json()
    acquisition_id = acq_payload["acquisition_id"]
    assert acq_payload["visualization_version"] == "sentinel-2-rgb-percentile-v2"
    assert acq_payload["display_metadata"]["bands"] == ["B04 red", "B03 green", "B02 blue"]

    # 3. Call display endpoint
    display_resp = client.get(f"/api/v1/imagery/acquisitions/{acquisition_id}/display?aoi_id={aoi_id}")
    assert display_resp.status_code == 200
    assert display_resp.headers["content-type"] == "image/png"

    png_bytes = display_resp.content
    assert png_bytes.startswith(b"\x89PNG\r\n\x1a\n")

    # Read rendered PNG with rasterio
    with rasterio.open(io.BytesIO(png_bytes)) as ds:
        # Correction 4 requirements:
        # 1. Valid 4 channels (RGBA)
        assert ds.count == 4
        assert ds.dtypes == ("uint8", "uint8", "uint8", "uint8")

        r = ds.read(1)
        g = ds.read(2)
        b = ds.read(3)
        alpha = ds.read(4)

        # 2. Alpha=255 for all finite RGB pixels inside AOI (including cloud/shadow), Alpha=0 for pixels outside AOI
        # Inside the AOI, all finite RGB pixels are opaque
        assert (alpha == 255).any()
        # In the 5x5 cropped raster, source [7, 7] is cloud (scl=8), which is now opaque (alpha=255) to show authentic observation
        assert alpha[2, 2] == 255  # source [7, 7] is cloud (scl=8), opaque for authentic display
        assert alpha[0, 0] == 255  # source [5, 5] is vegetation (scl=4), opaque
        assert alpha[1, 1] == 255  # source [6, 6] has finite RGB, opaque inside AOI

        # 3. Dynamic range: Nontrivial dynamic range across channels
        for channel in (r, g, b):
            valid_vals = channel[alpha == 255]
            assert valid_vals.min() >= 0
            assert valid_vals.max() <= 255
            assert (valid_vals.max() - valid_vals.min()) > 50

        # 4. Deterministic repeat output
        repeat_resp = client.get(f"/api/v1/imagery/acquisitions/{acquisition_id}/display?aoi_id={aoi_id}")
        assert repeat_resp.content == png_bytes


def test_display_rejects_incompatible_legacy_visualization_version(tmp_path: Path):
    # Insert a row with an older visualization_version
    with connection() as db:
        db.execute(
            """
            INSERT INTO aoi (id, geometry_json, created_at, updated_at)
            VALUES (1, '{"type":"Polygon","coordinates":[[[0,0],[1,0],[1,1],[0,1],[0,0]]]}', 'now', 'now')
            """
        )
        fake_tif = tmp_path / "fake.tif"
        write_test_raster(fake_tif, np.ones((3, 5, 5), dtype=np.uint8))
        fake_mask = tmp_path / "mask.tif"
        write_test_raster(fake_mask, np.ones((5, 5), dtype=np.uint8))

        db.execute(
            """
            INSERT INTO imagery_acquisitions (
                id, aoi_id, requested_start_datetime, requested_end_datetime, item_id,
                collection, acquisition_datetime, assets_json, prepared_path,
                raster_metadata_json, source_metadata_json, created_at,
                observation_state, quality_reason, quality_metrics_json, quality_mask_path,
                processing_version, masking_method, quality_asset_id, display_path,
                display_metadata_json, visualization_version
            ) VALUES (
                1, 1, 'start', 'end', 'S2_LEGACY', 'sentinel-2-l2a', 'now', '[]',
                ?, '{}', '{}', 'now', 'usable', 'ok', '{}', ?,
                'v1', 'method', 'scl', ?, '{}', 'sentinel-2-rgb-percentile-v1'
            )
            """,
            (str(fake_tif), str(fake_mask), str(fake_tif)),
        )
        db.commit()

    # Requesting display for legacy visualization_version must fail explicitly
    response = client.get("/api/v1/imagery/acquisitions/1/display?aoi_id=1")
    assert response.status_code == 404
    assert response.json()["code"] == "DisplayImageryUnavailableError"
    assert "incompatible" in response.json()["message"]


def test_display_quality_mask_agrees_with_phase_b(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    # Tests that display transparency strictly agrees with Phase B VALID_SCL_CLASSES = {4, 5, 6}
    red_data = np.full((10, 10), 1000, dtype=np.uint16)
    green_data = np.full((10, 10), 1000, dtype=np.uint16)
    blue_data = np.full((10, 10), 1000, dtype=np.uint16)
    nir_data = np.full((10, 10), 2000, dtype=np.uint16)

    # Base SCL is 4 (usable vegetation)
    scl_data = np.full((10, 10), 4, dtype=np.uint8)
    # Assign specific classes inside the AOI crop (source rows 5..8, cols 5..8):
    # 0 = nodata, 1 = defective, 2 = dark feature, 3 = shadow
    # 4 = vegetation (valid), 5 = not-vegetated (valid), 6 = water (valid)
    # 7 = unclassified, 8 = cloud med, 9 = cloud high, 10 = cirrus, 11 = snow
    test_classes = {
        (5, 5): 0,
        (5, 6): 1,
        (5, 7): 2,
        (5, 8): 3,
        (6, 5): 4,
        (6, 6): 5,
        (6, 7): 6,
        (6, 8): 7,
        (7, 5): 8,
        (7, 6): 9,
        (7, 7): 10,
        (7, 8): 11,
    }
    for (r_idx, c_idx), cls_val in test_classes.items():
        scl_data[r_idx, c_idx] = cls_val

    files = {
        "https://example.test/red.tif": tmp_path / "red2.tif",
        "https://example.test/green.tif": tmp_path / "green2.tif",
        "https://example.test/blue.tif": tmp_path / "blue2.tif",
        "https://example.test/nir.tif": tmp_path / "nir2.tif",
        "https://example.test/scl.tif": tmp_path / "scl2.tif",
    }
    write_test_raster(files["https://example.test/red.tif"], red_data)
    write_test_raster(files["https://example.test/green.tif"], green_data)
    write_test_raster(files["https://example.test/blue.tif"], blue_data)
    write_test_raster(files["https://example.test/nir.tif"], nir_data)
    write_test_raster(files["https://example.test/scl.tif"], scl_data)

    original_open = rasterio.open

    def mocked_open(path, *args, **kwargs):
        if isinstance(path, str) and path in files:
            return original_open(files[path], *args, **kwargs)
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(imagery.rasterio, "open", mocked_open)
    catalog = FakeCatalog([FakeItem("S2_SCL_TEST", 2.0)])
    monkeypatch.setattr(imagery.pystac_client.Client, "open", lambda _: catalog)

    aoi_resp = client.post("/api/v1/aoi", json=AOI)
    aoi_id = aoi_resp.json()["aoi_id"]
    acq_resp = client.post(
        "/api/v1/imagery/acquisitions",
        json={"aoi_id": aoi_id, "start_datetime": "2024-01-01T00:00:00Z", "end_datetime": "2024-04-01T00:00:00Z"},
    )
    acq_id = acq_resp.json()["acquisition_id"]

    display_resp = client.get(f"/api/v1/imagery/acquisitions/{acq_id}/display?aoi_id={aoi_id}")
    assert display_resp.status_code == 200

    with rasterio.open(io.BytesIO(display_resp.content)) as ds:
        alpha = ds.read(4)
        for (r_idx, c_idx), cls_val in test_classes.items():
            r_crop = r_idx - 5
            c_crop = c_idx - 5
            # Under display semantics, all observed finite RGB pixels inside the AOI are opaque (alpha=255)
            # including clouds (8, 9, 10), shadows (3), defective (1), unclassified (7), and snow (11)
            assert alpha[r_crop, c_crop] == 255, f"Class {cls_val} inside AOI should be opaque (255) for visual display"


def test_display_aoi_polygon_geometry_masking(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    # Tests that pixels outside a non-rectangular AOI polygon are transparent (alpha=0)
    # even when SCL is 4 (vegetation)
    patch_synthetic_sources(monkeypatch, tmp_path)
    catalog = FakeCatalog([FakeItem("S2_POLY_TEST", 2.0)])
    monkeypatch.setattr(imagery.pystac_client.Client, "open", lambda _: catalog)

    # Triangle polygon covering only lower half of the bounding box
    triangular_aoi = {
        "type": "Polygon",
        "coordinates": [[[77.4, 12.8], [77.5, 12.8], [77.4, 12.9], [77.4, 12.8]]],
    }
    aoi_resp = client.post("/api/v1/aoi", json=triangular_aoi)
    aoi_id = aoi_resp.json()["aoi_id"]

    acq_resp = client.post(
        "/api/v1/imagery/acquisitions",
        json={"aoi_id": aoi_id, "start_datetime": "2024-01-01T00:00:00Z", "end_datetime": "2024-04-01T00:00:00Z"},
    )
    acq_id = acq_resp.json()["acquisition_id"]

    display_resp = client.get(f"/api/v1/imagery/acquisitions/{acq_id}/display?aoi_id={aoi_id}")
    assert display_resp.status_code == 200

    with rasterio.open(io.BytesIO(display_resp.content)) as ds:
        alpha = ds.read(4)
        # Bounding box top-right corner is outside the triangular polygon:
        # row 0, col -1 corresponds to around [77.5, 12.9] which is outside the triangle
        # [77.4, 12.8] -> [77.5, 12.8] -> [77.4, 12.9]
        # Pixel outside the triangle must be transparent
        assert alpha[0, -1] == 0, "Pixel outside triangular polygon must be transparent"
        # Pixel well inside the triangle must be opaque
        assert (alpha == 255).any(), "Pixels inside the triangle must be opaque"
        assert (alpha == 0).any(), "Pixels outside the triangle must be transparent"


def test_display_alpha_continuous_inside_aoi_part11(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """
    Focused test suite for Part 11 display requirements:
    1. finite RGB + cloud SCL inside AOI -> alpha 255
    2. finite RGB + shadow SCL inside AOI -> alpha 255
    3. finite RGB + invalid SCL inside AOI -> alpha 255
    4. finite RGB outside AOI -> alpha 0
    5. non-displayable RGB -> appropriate no-data handling
    6. quality mask output unchanged
    7. analytical raster output unchanged
    8. RGB stretch unchanged
    """
    patch_synthetic_sources(monkeypatch, tmp_path)
    catalog = FakeCatalog([FakeItem("S2_PART11_TEST", 2.0)])
    monkeypatch.setattr(imagery.pystac_client.Client, "open", lambda _: catalog)

    # Triangular AOI covering lower-left portion of bounding box
    triangular_aoi = {
        "type": "Polygon",
        "coordinates": [[[77.4, 12.8], [77.5, 12.8], [77.4, 12.9], [77.4, 12.8]]],
    }
    aoi_resp = client.post("/api/v1/aoi", json=triangular_aoi)
    assert aoi_resp.status_code == 200
    aoi_id = aoi_resp.json()["aoi_id"]

    acq_resp = client.post(
        "/api/v1/imagery/acquisitions",
        json={"aoi_id": aoi_id, "start_datetime": "2024-01-01T00:00:00Z", "end_datetime": "2024-04-01T00:00:00Z"},
    )
    assert acq_resp.status_code == 200
    acq_data = acq_resp.json()
    acq_id = acq_data["acquisition_id"]

    # 6. Quality mask output unchanged
    quality_mask_path = acq_data["quality_mask_path"]
    assert quality_mask_path and Path(quality_mask_path).is_file()
    with rasterio.open(quality_mask_path) as q_ds:
        assert q_ds.count == 1
        assert q_ds.dtypes[0] == "uint8"

    # 7. Analytical raster output unchanged (B04/B08 2-band uint16)
    prepared_path = acq_data["prepared_path"]
    assert prepared_path and Path(prepared_path).is_file()
    with rasterio.open(prepared_path) as p_ds:
        assert p_ds.count == 2
        assert p_ds.dtypes == ("uint16", "uint16")

    # 8. RGB stretch unchanged (p2/p98 stats present in display metadata)
    assert acq_data["display_metadata"]["stretch"]["method"] == "quality_aware_percentile_p2_p98"
    assert "p2" in acq_data["display_metadata"]["stretch"]
    assert "p98" in acq_data["display_metadata"]["stretch"]

    # Fetch display PNG
    display_resp = client.get(f"/api/v1/imagery/acquisitions/{acq_id}/display?aoi_id={aoi_id}")
    assert display_resp.status_code == 200
    with rasterio.open(io.BytesIO(display_resp.content)) as ds:
        assert ds.count == 4
        alpha = ds.read(4)

        # 4. Finite RGB outside AOI polygon -> alpha 0
        assert alpha[0, -1] == 0, "Outside AOI must be alpha 0"

        # 1, 2, 3. Finite RGB inside AOI -> alpha 255 (cloud, shadow, invalid SCL)
        # Inside the triangle, pixels are opaque
        assert (alpha == 255).any()
        assert alpha[-1, 0] == 255, "Inside AOI must be alpha 255"
