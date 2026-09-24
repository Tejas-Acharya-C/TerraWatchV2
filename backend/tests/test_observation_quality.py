from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from app import imagery
from app.config import settings
from app.exceptions import NoSuitableImageryError


class Asset:
    def __init__(self, href: str) -> None:
        self.href = href


def write_raster(path: Path, values: np.ndarray, dtype: str, transform, nodata=0) -> None:
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=values.shape[1],
        height=values.shape[0],
        count=1,
        dtype=dtype,
        crs="EPSG:4326",
        transform=transform,
        nodata=nodata,
    ) as dataset:
        dataset.write(values.astype(dtype), 1)


def test_scl_quality_classes_and_combined_validity(tmp_path: Path):
    prepared_path = tmp_path / "prepared.tif"
    mask_path = tmp_path / "quality_mask.tif"
    red = np.full((4, 4), 10, dtype=np.uint16)
    nir = np.full((4, 4), 20, dtype=np.uint16)
    red[3, 3] = 0
    with rasterio.open(
        prepared_path,
        "w",
        driver="GTiff",
        width=4,
        height=4,
        count=2,
        dtype="uint16",
        crs="EPSG:4326",
        transform=from_origin(0, 4, 1, 1),
        nodata=0,
    ) as dataset:
        dataset.write(red, 1)
        dataset.write(nir, 2)
    scl = np.array([[4, 4, 8, 3], [4, 0, 4, 4], [4, 4, 9, 10], [7, 2, 4, 12]], dtype=np.uint8)
    write_raster(mask_path, scl, "uint8", from_origin(0, 4, 1, 1))

    assessment = imagery._assess_quality(prepared_path, mask_path)

    assert assessment.state == "usable"
    assert assessment.reason == "quality_policy_passed"
    assert assessment.metrics == {
        "total_pixels": 16,
        "invalid_pixels": 4,
        "cloud_pixels": 3,
        "shadow_pixels": 1,
        "usable_pixels": 8,
        "invalid_percentage": 25.0,
        "cloud_percentage": 18.75,
        "shadow_percentage": 6.25,
        "usable_percentage": 50.0,
        "minimum_usable_pixel_fraction": settings.minimum_usable_pixel_fraction,
    }


def test_quality_policy_marks_cloud_contaminated_observation_unusable(tmp_path: Path):
    prepared_path = tmp_path / "prepared.tif"
    mask_path = tmp_path / "quality_mask.tif"
    values = np.full((2, 2), 10, dtype=np.uint16)
    with rasterio.open(
        prepared_path,
        "w",
        driver="GTiff",
        width=2,
        height=2,
        count=2,
        dtype="uint16",
        crs="EPSG:4326",
        transform=from_origin(0, 2, 1, 1),
        nodata=0,
    ) as dataset:
        dataset.write(values, 1)
        dataset.write(values, 2)
    write_raster(mask_path, np.full((2, 2), 9, dtype=np.uint8), "uint8", from_origin(0, 2, 1, 1))

    assessment = imagery._assess_quality(prepared_path, mask_path)

    assert assessment.state == "valid_unusable"
    assert assessment.reason == "no_usable_pixels"
    assert assessment.metrics["cloud_pixels"] == 4
    assert assessment.metrics["usable_pixels"] == 0


def test_quality_threshold_is_a_provisional_observation_policy():
    assert settings.minimum_usable_pixel_fraction == 0.5
    assert 0 < settings.minimum_usable_pixel_fraction < 1
    assert "provisional observation-level" in (settings.__class__.__doc__ or "")
    assert "not a claim" in (settings.__class__.__doc__ or "")


def test_scl_mask_is_explicitly_aligned_to_prepared_grid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    prepared_path = tmp_path / "prepared.tif"
    scl_path = tmp_path / "scl.tif"
    write_raster(prepared_path, np.ones((4, 4), dtype=np.uint16), "uint16", from_origin(0, 4, 1, 1))
    write_raster(scl_path, np.array([[4, 8], [3, 0]], dtype=np.uint8), "uint8", from_origin(0, 4, 2, 2))
    real_open = imagery.rasterio.open

    def open_asset(path, *args, **kwargs):
        return real_open(scl_path if path == "https://example.test/scl.tif" else path, *args, **kwargs)

    monkeypatch.setattr(imagery.rasterio, "open", open_asset)
    item = SimpleNamespace(assets={"scl": Asset("https://example.test/scl.tif")})

    output_path = imagery._write_quality_mask(item, prepared_path)

    with rasterio.open(output_path) as output:
        assert output.width == 4
        assert output.height == 4
        assert output.transform == from_origin(0, 4, 1, 1)
        assert output.crs.to_string() == "EPSG:4326"
        assert output.read(1).tolist() == [[4, 4, 8, 8], [4, 4, 8, 8], [3, 3, 0, 0], [3, 3, 0, 0]]


def test_quality_asset_is_required_for_selection():
    item = SimpleNamespace(
        id="without-scl",
        datetime=datetime(2024, 1, 1, tzinfo=UTC),
        properties={"eo:cloud_cover": 1},
        assets={"red": Asset("red"), "nir": Asset("nir")},
    )

    with pytest.raises(NoSuitableImageryError, match="SCL"):
        imagery._select_item([item])


def test_quality_ranking_is_deterministic_and_aoi_aware():
    def row(item_id: str, when: str, usable: float, cloud: float, shadow: float):
        return (0, 1, "start", "end", item_id, "sentinel-2-l2a", when, "[]", "path", "{}", "{}", "created", "usable", "ok", json.dumps({"usable_percentage": usable, "cloud_percentage": cloud, "shadow_percentage": shadow}), "mask", "v1", "scl")

    better_quality = row("later", "2024-02-01T00:00:00+00:00", 90, 5, 5)
    lower_catalog_cloud = row("earlier", "2024-01-01T00:00:00+00:00", 70, 1, 1)

    assert min([lower_catalog_cloud, better_quality], key=imagery._quality_sort_key) == better_quality
    assert min([row("b", "2024-01-01", 90, 5, 5), row("a", "2024-01-01", 90, 5, 5)], key=imagery._quality_sort_key)[4] == "a"
