from __future__ import annotations

import json
import time
import tracemalloc
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest
import rasterio
from fastapi.testclient import TestClient
from rasterio.features import shapes
from rasterio.transform import from_origin
from scipy import ndimage

from app.config import settings
from app.db import connection, initialize_database
from app.detection import _connected_components
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated_database(tmp_path: Path):
    original_database = settings.database_path
    object.__setattr__(settings, "database_path", tmp_path / "test.db")
    initialize_database()
    yield tmp_path
    object.__setattr__(settings, "database_path", original_database)


def write_raster(
    path: Path,
    red: np.ndarray,
    nir: np.ndarray,
    *,
    xsize: int = 10,
    crs: str = "EPSG:32632",
    transform=None,
    nodata: int = 0,
) -> None:
    with rasterio.open(
        path, "w", driver="GTiff", width=red.shape[1], height=red.shape[0],
        count=2,
        dtype="float32",
        crs=crs,
        transform=transform or from_origin(500000, 10, xsize, xsize),
        nodata=nodata,
    ) as dataset:
        dataset.write(red.astype("float32"), 1)
        dataset.write(nir.astype("float32"), 2)


def persist_acquisition(
    path: Path,
    acquisition_id: int,
    when: str,
    *,
    aoi_id: int = 1,
    quality_values: np.ndarray | None = None,
) -> None:
    quality_mask_path = path.with_name(f"{path.stem}_quality_mask.tif")
    if path.exists():
        with rasterio.open(path) as dataset:
            bounds = dataset.bounds
            width = dataset.width
            height = dataset.height
            crs = dataset.crs
            transform = dataset.transform
        metadata = {
            "crs": str(dataset.crs),
            "transform": [
                float(dataset.transform.a),
                float(dataset.transform.b),
                float(dataset.transform.c),
                float(dataset.transform.d),
                float(dataset.transform.e),
                float(dataset.transform.f),
            ],
            "width": dataset.width,
            "height": dataset.height,
            "resolution": [float(dataset.res[0]), float(dataset.res[1])],
            "bounds": [float(bounds.left), float(bounds.bottom), float(bounds.right), float(bounds.top)],
            "count": dataset.count,
            "dtype": dataset.dtypes[0],
            "nodata": dataset.nodata,
        }
        polygon = {
            "type": "Polygon",
            "coordinates": [[
                [bounds.left, bounds.top],
                [bounds.right, bounds.top],
                [bounds.right, bounds.bottom],
                [bounds.left, bounds.bottom],
                [bounds.left, bounds.top],
            ]],
        }
        with rasterio.open(
            quality_mask_path,
            "w",
            driver="GTiff",
            width=width,
            height=height,
            count=1,
            dtype="uint8",
            crs=crs,
            transform=transform,
            nodata=0,
        ) as quality_mask:
            quality_mask.write(
                quality_values.astype(np.uint8)
                if quality_values is not None
                else np.full((height, width), 4, dtype=np.uint8),
                1,
            )
    else:
        metadata = {}
        polygon = {
            "type": "Polygon",
            "coordinates": [[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0], [0.0, 0.0]]],
        }
    with connection() as db_connection:
        db_connection.execute(
            "INSERT INTO aoi (id, geometry_json, created_at, updated_at) VALUES (?, ?, ?, ?) ON CONFLICT(id) DO NOTHING",
            (
                aoi_id,
                json.dumps(polygon),
                when,
                when,
            ),
        )
        db_connection.execute(
            """INSERT INTO imagery_acquisitions (
                id, aoi_id, requested_start_datetime, requested_end_datetime, item_id,
                collection, acquisition_datetime, assets_json, prepared_path,
                raster_metadata_json, source_metadata_json, created_at,
                observation_state, quality_reason, quality_metrics_json,
                quality_mask_path, processing_version, masking_method, quality_asset_id
            ) VALUES (?, ?, ?, ?, ?, 'sentinel-2-l2a', ?, '[]', ?, ?, '{}', ?, 'usable', 'quality_policy_passed', ?, ?, 'aoi-observation-quality-v1', 'sentinel-2-scl-nearest-v1', 'scl')""",
            (acquisition_id, aoi_id, when, when, f"S2_{acquisition_id}", when, str(path), json.dumps(metadata), when,
             json.dumps({"total_pixels": int(width * height) if path.exists() else 0, "usable_pixels": int(width * height) if path.exists() else 0}),
             str(quality_mask_path) if path.exists() else str(quality_mask_path)),
        )
        db_connection.commit()


def test_detection_calculates_ndvi_change_and_persists_regions(tmp_path: Path):
    before_path = tmp_path / "before.tif"
    after_path = tmp_path / "after.tif"
    before_red = np.full((3, 3), 2)
    before_nir = np.full((3, 3), 6)
    after_red = before_red.copy()
    after_nir = before_nir.copy()
    after_nir[1, 1] = 2
    write_raster(before_path, before_red, before_nir)
    write_raster(after_path, after_red, after_nir)
    persist_acquisition(before_path, 1, "2024-01-01T00:00:00+00:00")
    persist_acquisition(after_path, 2, "2024-02-01T00:00:00+00:00")

    response = client.post("/api/v1/detections", json={"before_acquisition_id": 1, "after_acquisition_id": 2, "threshold": 0.2, "min_region_pixels": 1})

    assert response.status_code == 200, response.json()
    payload = response.json()
    assert payload["region_count"] == 1
    assert payload["changed_pixel_count"] == 1
    assert payload["regions"][0]["pixel_count"] == 1
    assert payload["regions"][0]["mean_change_signal"] == pytest.approx(0.5)
    with connection() as db_connection:
        assert db_connection.execute("SELECT COUNT(*) FROM detection_runs").fetchone()[0] == 1
        assert db_connection.execute("SELECT COUNT(*) FROM raw_change_regions").fetchone()[0] == 1
        diagnostics = db_connection.execute(
            "SELECT quality_mask_used, quality_valid_pixel_count, raw_changed_pixel_count, filtered_changed_pixel_count, changed_pixel_percentage FROM detection_runs"
        ).fetchone()
    assert diagnostics[0] == 1
    assert diagnostics[1] == 9
    assert diagnostics[2] == 1
    assert diagnostics[3] == 1
    assert diagnostics[4] == pytest.approx(100 / 9)


def test_invalid_and_zero_denominator_pixels_are_not_change(tmp_path: Path):
    before_path = tmp_path / "before.tif"
    after_path = tmp_path / "after.tif"
    before_red = np.array([[0, 2], [2, 0]])
    before_nir = np.array([[0, 6], [6, 0]])
    after_red = np.array([[9, 2], [0, 0]])
    after_nir = np.array([[9, 6], [0, 0]])
    write_raster(before_path, before_red, before_nir, xsize=10)
    write_raster(after_path, after_red, after_nir, xsize=10)
    persist_acquisition(before_path, 1, "2024-01-01T00:00:00+00:00")
    persist_acquisition(after_path, 2, "2024-02-01T00:00:00+00:00")

    response = client.post("/api/v1/detections", json={"before_acquisition_id": 1, "after_acquisition_id": 2, "threshold": 0.2, "min_region_pixels": 1})

    assert response.status_code == 200
    assert response.json()["changed_pixel_count"] == 0
    assert response.json()["region_count"] == 0


def test_legacy_unassessed_observations_are_rejected(tmp_path: Path):
    before_path = tmp_path / "before.tif"
    after_path = tmp_path / "after.tif"
    values = np.full((2, 2), 2)
    nir = np.full((2, 2), 6)
    write_raster(before_path, values, nir)
    write_raster(after_path, values, nir)
    persist_acquisition(before_path, 1, "2024-01-01T00:00:00+00:00")
    persist_acquisition(after_path, 2, "2024-02-01T00:00:00+00:00")
    with connection() as db_connection:
        db_connection.execute(
            "UPDATE imagery_acquisitions SET observation_state = 'legacy_unassessed' WHERE id IN (1, 2)"
        )
        db_connection.commit()

    response = client.post(
        "/api/v1/detections",
        json={"before_acquisition_id": 1, "after_acquisition_id": 2, "min_region_pixels": 1},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "DetectionRasterError"
    assert "not quality-assessed" in response.json()["message"]


def test_cloud_and_shadow_pixels_are_excluded_from_change_and_diagnostics(tmp_path: Path):
    before_path = tmp_path / "before.tif"
    after_path = tmp_path / "after.tif"
    before_red = np.full((2, 3), 2)
    before_nir = np.full((2, 3), 6)
    after_red = before_red.copy()
    after_nir = before_nir.copy()
    after_nir[0, 0] = 2
    after_nir[0, 1] = 2
    after_nir[1, 1] = 2
    after_nir[1, 2] = 2
    write_raster(before_path, before_red, before_nir)
    write_raster(after_path, after_red, after_nir)
    before_quality = np.array([[4, 8, 0], [4, 4, 3]], dtype=np.uint8)
    after_quality = np.array([[4, 4, 4], [4, 9, 3]], dtype=np.uint8)
    persist_acquisition(before_path, 1, "2024-01-01T00:00:00+00:00", quality_values=before_quality)
    persist_acquisition(after_path, 2, "2024-02-01T00:00:00+00:00", quality_values=after_quality)

    response = client.post(
        "/api/v1/detections",
        json={"before_acquisition_id": 1, "after_acquisition_id": 2, "threshold": 0.2, "min_region_pixels": 1},
    )

    assert response.status_code == 200, response.json()
    payload = response.json()
    assert payload["quality_mask_used"] is True
    assert payload["quality_valid_pixel_count"] == 2
    assert payload["excluded_cloud_pixel_count"] == 2
    assert payload["excluded_shadow_pixel_count"] == 1
    assert payload["excluded_pixel_count"] == 4
    assert payload["raw_changed_pixel_count"] == 1
    assert payload["filtered_changed_pixel_count"] == 1
    assert payload["changed_pixel_percentage"] == 50.0
    assert payload["region_count"] == 1


def test_region_filter_changes_final_regions_not_raw_changes(tmp_path: Path):
    before_path = tmp_path / "before.tif"
    after_path = tmp_path / "after.tif"
    before_red = np.full((4, 4), 2)
    before_nir = np.full((4, 4), 6)
    after_red = before_red.copy()
    after_nir = before_nir.copy()
    after_nir[0, 0] = 2
    after_nir[2, 2] = 2
    after_nir[2, 3] = 2
    after_nir[3, 2] = 2
    after_nir[3, 3] = 2
    write_raster(before_path, before_red, before_nir)
    write_raster(after_path, after_red, after_nir)
    persist_acquisition(before_path, 1, "2024-01-01T00:00:00+00:00")
    persist_acquisition(after_path, 2, "2024-02-01T00:00:00+00:00")

    one_pixel = client.post(
        "/api/v1/detections",
        json={"before_acquisition_id": 1, "after_acquisition_id": 2, "threshold": 0.2, "min_region_pixels": 1},
    )
    four_pixels = client.post(
        "/api/v1/detections",
        json={"before_acquisition_id": 1, "after_acquisition_id": 2, "threshold": 0.2, "min_region_pixels": 4},
    )

    assert one_pixel.status_code == 200
    assert four_pixels.status_code == 200
    first = one_pixel.json()
    second = four_pixels.json()
    assert first["raw_changed_pixel_count"] == second["raw_changed_pixel_count"] == 5
    assert first["filtered_changed_pixel_count"] == 5
    assert second["filtered_changed_pixel_count"] == 4
    assert first["region_count"] == 2
    assert second["region_count"] == 1


def test_no_comparable_pixels_is_explicit(tmp_path: Path):
    before_path = tmp_path / "before.tif"
    after_path = tmp_path / "after.tif"
    values = np.full((2, 2), 2)
    nir = np.full((2, 2), 6)
    write_raster(before_path, values, nir)
    write_raster(after_path, values, nir)
    all_cloud = np.full((2, 2), 9, dtype=np.uint8)
    persist_acquisition(before_path, 1, "2024-01-01T00:00:00+00:00", quality_values=all_cloud)
    persist_acquisition(after_path, 2, "2024-02-01T00:00:00+00:00", quality_values=all_cloud)

    response = client.post(
        "/api/v1/detections",
        json={"before_acquisition_id": 1, "after_acquisition_id": 2, "min_region_pixels": 1},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "DetectionRasterError"
    assert "No usable comparison pixels" in response.json()["message"]


def test_incompatible_grid_is_explicit(tmp_path: Path):
    before_path = tmp_path / "before.tif"
    after_path = tmp_path / "after.tif"
    values = np.ones((3, 3))
    write_raster(before_path, values, values)
    write_raster(after_path, values, values, xsize=20)
    persist_acquisition(before_path, 1, "2024-01-01T00:00:00+00:00")
    persist_acquisition(after_path, 2, "2024-02-01T00:00:00+00:00")

    response = client.post("/api/v1/detections", json={"before_acquisition_id": 1, "after_acquisition_id": 2, "min_region_pixels": 1})

    assert response.status_code == 422
    assert response.json()["code"] == "IncompatibleAcquisitionsError"


@pytest.mark.parametrize(
    ("variant", "expected_message"),
    [
        ("crs", "incompatible CRS"),
        ("dimensions", "incompatible dimensions"),
        ("transform", "incompatible raster grids"),
        ("nodata", "incompatible nodata values"),
    ],
)
def test_raster_compatibility_variants_are_explicit(
    tmp_path: Path, variant: str, expected_message: str
):
    before_path = tmp_path / "before.tif"
    after_path = tmp_path / "after.tif"
    before = np.ones((3, 3))
    nir = np.ones((3, 3))
    write_raster(before_path, before, nir)
    if variant == "crs":
        write_raster(after_path, before, nir, crs="EPSG:32633")
    elif variant == "dimensions":
        write_raster(after_path, np.ones((2, 3)), np.ones((2, 3)))
    elif variant == "transform":
        write_raster(after_path, before, nir, transform=from_origin(500001, 10, 10, 10))
    else:
        write_raster(after_path, before, nir, nodata=255)
    persist_acquisition(before_path, 1, "2024-01-01T00:00:00+00:00")
    persist_acquisition(after_path, 2, "2024-02-01T00:00:00+00:00")

    response = client.post("/api/v1/detections", json={"before_acquisition_id": 1, "after_acquisition_id": 2, "min_region_pixels": 1})

    assert response.status_code == 422
    assert response.json()["code"] == "IncompatibleAcquisitionsError"
    assert expected_message in response.json()["message"]


def test_identical_inputs_and_configuration_are_repeatable(tmp_path: Path):
    before_path = tmp_path / "before.tif"
    after_path = tmp_path / "after.tif"
    before = np.full((3, 3), 2)
    before_nir = np.full((3, 3), 6)
    after = before.copy()
    after_nir = before_nir.copy()
    after_nir[1, 1] = 2
    write_raster(before_path, before, before_nir)
    write_raster(after_path, after, after_nir)
    persist_acquisition(before_path, 1, "2024-01-01T00:00:00+00:00")
    persist_acquisition(after_path, 2, "2024-02-01T00:00:00+00:00")

    payload = {"before_acquisition_id": 1, "after_acquisition_id": 2, "threshold": 0.2, "min_region_pixels": 1}
    first = client.post("/api/v1/detections", json=payload).json()
    second = client.post("/api/v1/detections", json=payload).json()

    assert (first["region_count"], first["changed_pixel_count"]) == (second["region_count"], second["changed_pixel_count"])
    assert first["regions"] == second["regions"]


def _legacy_connected_components(changed: np.ndarray) -> list[list[tuple[int, int]]]:
    visited = np.zeros(changed.shape, dtype=bool)
    components: list[list[tuple[int, int]]] = []
    height, width = changed.shape
    for row in range(height):
        for column in range(width):
            if not changed[row, column] or visited[row, column]:
                continue
            component: list[tuple[int, int]] = []
            stack = [(row, column)]
            visited[row, column] = True
            while stack:
                current_row, current_column = stack.pop()
                component.append((current_row, current_column))
                for row_offset in (-1, 0, 1):
                    for column_offset in (-1, 0, 1):
                        if row_offset == 0 and column_offset == 0:
                            continue
                        neighbor_row = current_row + row_offset
                        neighbor_column = current_column + column_offset
                        if (
                            0 <= neighbor_row < height
                            and 0 <= neighbor_column < width
                            and changed[neighbor_row, neighbor_column]
                            and not visited[neighbor_row, neighbor_column]
                        ):
                            visited[neighbor_row, neighbor_column] = True
                            stack.append((neighbor_row, neighbor_column))
            components.append(component)
    return components


def test_missing_inputs_are_distinct():
    response = client.post("/api/v1/detections", json={"before_acquisition_id": 1, "after_acquisition_id": 2, "min_region_pixels": 1})
    assert response.status_code == 404
    assert response.json()["code"] == "MissingBeforeAcquisitionError"


def test_connected_components_legacy_compatibility_for_representative_small_rasters():
    changed = np.array(
        [
            [0, 1, 0, 0],
            [1, 1, 0, 1],
            [0, 0, 1, 1],
            [0, 1, 1, 0],
        ],
        dtype=bool,
    )

    legacy_components = _legacy_connected_components(changed)
    current_components = _connected_components(changed)

    assert len(current_components) == len(legacy_components)
    current_pixel_sets = [set(zip(*np.nonzero(component))) for component in current_components]
    legacy_pixel_sets = [set(component) for component in legacy_components]
    assert current_pixel_sets == legacy_pixel_sets


def test_multiple_disconnected_components_remain_separate(tmp_path: Path):
    before_path = tmp_path / "before.tif"
    after_path = tmp_path / "after.tif"
    before_red = np.full((4, 4), 2)
    before_nir = np.full((4, 4), 6)
    after_red = before_red.copy()
    after_nir = before_nir.copy()
    after_nir[0, 0] = 2
    after_nir[0, 1] = 2
    after_nir[1, 0] = 2
    after_nir[3, 3] = 2
    write_raster(before_path, before_red, before_nir)
    write_raster(after_path, after_red, after_nir)
    persist_acquisition(before_path, 1, "2024-01-01T00:00:00+00:00")
    persist_acquisition(after_path, 2, "2024-02-01T00:00:00+00:00")

    response = client.post(
        "/api/v1/detections",
        json={"before_acquisition_id": 1, "after_acquisition_id": 2, "threshold": 0.2, "min_region_pixels": 1},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["region_count"] == 2
    assert payload["changed_pixel_count"] == 4
    assert [region["pixel_count"] for region in payload["regions"]] == [3, 1]


def test_nodata_pixels_are_excluded_from_change_regions(tmp_path: Path):
    before_path = tmp_path / "before.tif"
    after_path = tmp_path / "after.tif"
    before_red = np.array([[2, 2], [2, 2]], dtype=float)
    before_nir = np.array([[6, 6], [6, 6]], dtype=float)
    after_red = before_red.copy()
    after_nir = before_nir.copy()
    after_red[1, 1] = 0.0
    after_nir[1, 1] = 0.0
    write_raster(before_path, before_red, before_nir, nodata=0)
    write_raster(after_path, after_red, after_nir, nodata=0)
    persist_acquisition(before_path, 1, "2024-01-01T00:00:00+00:00")
    persist_acquisition(after_path, 2, "2024-02-01T00:00:00+00:00")

    response = client.post(
        "/api/v1/detections",
        json={"before_acquisition_id": 1, "after_acquisition_id": 2, "threshold": 0.2},
    )

    assert response.status_code == 200
    assert response.json()["changed_pixel_count"] == 0
    assert response.json()["region_count"] == 0


def test_diagonal_pixels_use_eight_connectivity_and_minimum_region_size(tmp_path: Path):
    before_path = tmp_path / "before.tif"
    after_path = tmp_path / "after.tif"
    before_red = np.full((3, 3), 2)
    before_nir = np.full((3, 3), 6)
    after_red = before_red.copy()
    after_nir = before_nir.copy()
    after_nir[0, 0] = 2
    after_nir[1, 1] = 2
    write_raster(before_path, before_red, before_nir)
    write_raster(after_path, after_red, after_nir)
    persist_acquisition(before_path, 1, "2024-01-01T00:00:00+00:00")
    persist_acquisition(after_path, 2, "2024-02-01T00:00:00+00:00")

    response = client.post(
        "/api/v1/detections",
        json={"before_acquisition_id": 1, "after_acquisition_id": 2, "min_region_pixels": 2},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["region_count"] == 1
    assert payload["changed_pixel_count"] == 2
    assert payload["regions"][0]["area_m2"] == pytest.approx(200, rel=0.01)
    assert payload["regions"][0]["mean_change_signal"] == pytest.approx(0.5)
    assert payload["regions"][0]["max_change_signal"] == pytest.approx(0.5)
    assert payload["regions"][0]["geometry"]["type"] in {"Polygon", "MultiPolygon"}


def test_region_geometry_is_geographic_and_geometry_mask_is_not_used(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    before_path = tmp_path / "before.tif"
    after_path = tmp_path / "after.tif"
    before = np.full((3, 3), 2)
    before_nir = np.full((3, 3), 6)
    after = before.copy()
    after_nir = before_nir.copy()
    after_nir[0, 0] = 2
    after_nir[2, 2] = 2
    write_raster(before_path, before, before_nir)
    write_raster(after_path, after, after_nir)
    persist_acquisition(before_path, 1, "2024-01-01T00:00:00+00:00")
    persist_acquisition(after_path, 2, "2024-02-01T00:00:00+00:00")

    def fail_geometry_mask(*args, **kwargs):
        raise AssertionError("full-raster geometry_mask must not be used")

    monkeypatch.setattr(rasterio.features, "geometry_mask", fail_geometry_mask)
    response = client.post("/api/v1/detections", json={"before_acquisition_id": 1, "after_acquisition_id": 2, "min_region_pixels": 1})

    assert response.status_code == 200
    assert response.json()["region_count"] == 2
    assert all(
        abs(coordinate) <= 180
        for point in response.json()["regions"][0]["geometry"]["coordinates"][0]
        for coordinate in point
    )


def test_different_aoi_and_reverse_order_are_explicit(tmp_path: Path):
    before_path = tmp_path / "before.tif"
    after_path = tmp_path / "after.tif"
    values = np.full((3, 3), 2)
    nir = np.full((3, 3), 6)
    write_raster(before_path, values, nir)
    write_raster(after_path, values, nir)
    persist_acquisition(before_path, 1, "2024-02-01T00:00:00+00:00")
    persist_acquisition(after_path, 2, "2024-01-01T00:00:00+00:00", aoi_id=2)

    different_aoi = client.post("/api/v1/detections", json={"before_acquisition_id": 1, "after_acquisition_id": 2})
    assert different_aoi.status_code == 422
    assert different_aoi.json()["code"] == "IncompatibleAcquisitionsError"

    persist_acquisition(after_path, 3, "2024-01-01T00:00:00+00:00")
    reverse_order = client.post("/api/v1/detections", json={"before_acquisition_id": 1, "after_acquisition_id": 3})
    assert reverse_order.status_code == 422
    assert reverse_order.json()["code"] == "IncompatibleAcquisitionsError"


def test_raster_processing_failure_is_not_zero_regions(tmp_path: Path):
    missing_path = tmp_path / "missing.tif"
    persist_acquisition(missing_path, 1, "2024-01-01T00:00:00+00:00")
    persist_acquisition(missing_path, 2, "2024-02-01T00:00:00+00:00")

    response = client.post("/api/v1/detections", json={"before_acquisition_id": 1, "after_acquisition_id": 2})

    assert response.status_code == 422
    assert response.json()["code"] == "DetectionRasterError"
    with connection() as db_connection:
        assert db_connection.execute("SELECT COUNT(*) FROM detection_runs").fetchone()[0] == 0


def test_hole_preserves_geometry_and_region_metrics(tmp_path: Path):
    before_path = tmp_path / "before.tif"
    after_path = tmp_path / "after.tif"
    before_red = np.full((5, 5), 2)
    before_nir = np.full((5, 5), 6)
    after_red = before_red.copy()
    after_nir = before_nir.copy()

    after_nir[0, :] = 2
    after_nir[:, 0] = 2
    after_nir[-1, :] = 2
    after_nir[:, -1] = 2

    write_raster(before_path, before_red, before_nir)
    write_raster(after_path, after_red, after_nir)
    persist_acquisition(before_path, 1, "2024-01-01T00:00:00+00:00")
    persist_acquisition(after_path, 2, "2024-02-01T00:00:00+00:00")

    response = client.post(
        "/api/v1/detections",
        json={"before_acquisition_id": 1, "after_acquisition_id": 2, "threshold": 0.2},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["region_count"] == 1
    assert payload["changed_pixel_count"] == 16
    assert payload["regions"][0]["pixel_count"] == 16
    assert payload["regions"][0]["area_m2"] == pytest.approx(1600, rel=0.01)
    assert payload["regions"][0]["geometry"]["type"] == "Polygon"


def test_fragmented_mask_benchmark_handles_many_components():
    changed = np.zeros((512, 512), dtype=bool)
    for row in range(0, 512, 8):
        for column in range(0, 512, 8):
            changed[row : row + 2, column : column + 2] = True

    labels, num_labels = ndimage.label(changed, structure=np.ones((3, 3), dtype=np.uint8))
    tracemalloc.start()
    start = time.perf_counter()
    polygons = list(shapes(labels, mask=labels > 0, transform=from_origin(0, 0, 1, 1), connectivity=8))
    elapsed = time.perf_counter() - start
    _, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert num_labels == 4096
    assert len(polygons) == 4096
    assert elapsed < 5.0
    assert peak_bytes < 500_000_000


def test_large_mask_connected_component_benchmark_avoids_pathological_runtime_and_memory():
    changed = np.ones((4096, 4096), dtype=bool)

    tracemalloc.start()
    start = time.perf_counter()
    components = _connected_components(changed)
    elapsed = time.perf_counter() - start
    _, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert len(components) == 1
    assert sum(component.sum() for component in components) == changed.size
    assert elapsed < 15.0
    assert peak_bytes < 1_000_000_000