from __future__ import annotations

import json
import os
import re
import sqlite3
import struct
import tempfile
import zlib
from collections.abc import Iterator
import concurrent.futures
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from affine import Affine
import numpy as np
import pystac_client
import rasterio
from rasterio.enums import Resampling
from rasterio.errors import RasterioIOError
from rasterio.features import geometry_mask, geometry_window
from rasterio.mask import mask
from rasterio.warp import reproject, transform_bounds, transform_geom
from rasterio.windows import Window, from_bounds, transform as window_transform
from shapely.geometry import mapping, shape

from app.config import settings
from app.domain import KARNATAKA_BOUNDARY, is_within_karnataka
from app.db import connection
from app.exceptions import (
    AssetAcquisitionError,
    AOIOutsideKarnatakaError,
    CatalogFailureError,
    NoAcquisitionError,
    NoAOIError,
    NoSuitableImageryError,
    RasterValidationError,
    GeographicDomainError,
    DisplayImageryError,
    DisplayImageryUnavailableError,
)
from app.schemas import (
    AOIGeometry,
    ImageryAcquisitionRequest,
    ImageryAcquisitionResponse,
    ImageryAcquisitionListResponse,
    ImageryAsset,
    RasterMetadata,
)

REQUIRED_ASSETS = ("red", "nir")
DISPLAY_ASSETS = ("blue", "green")
QUALITY_ASSET_ID = "scl"
PROCESSING_VERSION = "aoi-observation-quality-v1"
MASKING_METHOD = "sentinel-2-scl-nearest-v1"
VISUALIZATION_VERSION = "sentinel-2-rgb-percentile-v2"
CLOUD_SCL_CLASSES = frozenset({8, 9, 10})
SHADOW_SCL_CLASSES = frozenset({3})
VALID_SCL_CLASSES = frozenset({4, 5, 6})


@dataclass(frozen=True)
class QualityAssessment:
    state: str
    reason: str
    metrics: dict[str, int | float]
    mask_path: Path


@dataclass(frozen=True)
class TargetRasterGrid:
    crs: Any
    transform: Affine
    width: int
    height: int
    bounds: tuple[float, float, float, float]
    resolution: tuple[float, float]


@dataclass(frozen=True)
class SCLScreeningResult:
    passed: bool
    mask_path: Path
    metrics: dict[str, Any]
    reason: str | None = None


@dataclass(frozen=True)
class SceneProcessingResult:
    item_id: str
    status: str  # "screened_unusable" | "completed" | "failed"
    item: Any
    assets: list[ImageryAsset]
    acquisition_datetime: datetime
    collection: str
    source_metadata: dict[str, Any]
    created_at: datetime
    screening: SCLScreeningResult | None = None
    prepared_path: Path | None = None
    raster: RasterMetadata | None = None
    quality: QualityAssessment | None = None
    display_path: Path | None = None
    display_metadata: dict[str, Any] | None = None
    error_message: str | None = None
    error_exception: Exception | None = None


def _acquisition_columns() -> str:
    return """id, aoi_id, requested_start_datetime, requested_end_datetime,
        item_id, collection, acquisition_datetime, assets_json,
        prepared_path, raster_metadata_json, source_metadata_json, created_at,
        observation_state, quality_reason, quality_metrics_json, quality_mask_path,
        processing_version, masking_method, quality_asset_id, display_path,
        display_metadata_json, visualization_version"""


def _load_aoi(aoi_id: int) -> tuple[int, AOIGeometry]:
    try:
        with connection() as db_connection:
            row = db_connection.execute(
                "SELECT id, geometry_json FROM aoi WHERE id = ?",
                (aoi_id,),
            ).fetchone()
    except Exception as exc:
        raise NoAOIError("The requested AOI could not be retrieved") from exc
    if row is None:
        raise NoAOIError("The requested AOI does not exist")
    try:
        geometry = AOIGeometry.model_validate(json.loads(row[1]))
    except (json.JSONDecodeError, ValueError) as exc:
        raise NoAOIError("The requested AOI is unreadable") from exc
    if not is_within_karnataka(shape(geometry.model_dump())):
        raise AOIOutsideKarnatakaError("AOI must be fully contained within Karnataka")
    return row[0], geometry


def _item_datetime(item: Any) -> datetime:
    item_datetime = item.datetime
    if item_datetime is None:
        raw_datetime = item.properties.get("datetime")
        if not raw_datetime:
            raise NoSuitableImageryError("A STAC item has no acquisition timestamp")
        item_datetime = datetime.fromisoformat(raw_datetime.replace("Z", "+00:00"))
    if item_datetime.tzinfo is None:
        item_datetime = item_datetime.replace(tzinfo=UTC)
    return item_datetime


def _has_required_assets(item: Any) -> bool:
    return all(
        asset_id in item.assets and bool(getattr(item.assets[asset_id], "href", None))
        for asset_id in REQUIRED_ASSETS
    )


def _has_quality_asset(item: Any) -> bool:
    return QUALITY_ASSET_ID in item.assets and bool(
        getattr(item.assets[QUALITY_ASSET_ID], "href", None)
    )


def _select_item(items: list[Any]) -> Any:
    suitable_items = [
        item for item in items
        if _has_required_assets(item) and _has_quality_asset(item)
    ]
    if not suitable_items:
        raise NoSuitableImageryError(
            "No Sentinel-2 item with red, nir, and SCL quality assets was found"
        )

    def selection_key(item: Any) -> tuple[float, datetime, str]:
        cloud_cover = item.properties.get("eo:cloud_cover")
        try:
            cloud_sort_value = float(cloud_cover)
        except (TypeError, ValueError):
            cloud_sort_value = float("inf")
        return cloud_sort_value, _item_datetime(item), str(item.id)

    return min(suitable_items, key=selection_key)


def _discover_items(aoi: AOIGeometry, request: ImageryAcquisitionRequest) -> list[Any]:
    try:
        catalog = pystac_client.Client.open(settings.stac_api_url)
        search = catalog.search(
            collections=[settings.stac_collection],
            intersects=aoi.model_dump(),
            datetime=(
                f"{request.start_datetime.isoformat()}/"
                f"{request.end_datetime.isoformat()}"
            ),
            max_items=settings.stac_result_limit,
        )
        items = list(search.items())
    except Exception as exc:
        raise CatalogFailureError("The Sentinel-2 catalog could not be queried") from exc
    if not items:
        raise NoSuitableImageryError(
            "No suitable Sentinel-2 imagery was found for the AOI and date window"
        )

    requested_geometry = shape(aoi.model_dump())
    covered_items: list[Any] = []
    for item in items:
        if not _has_required_assets(item) or not _has_quality_asset(item):
            continue
        if item.geometry is None:
            continue
        footprint = shape(item.geometry)
        if not footprint.is_valid:
            continue
        if not footprint.intersects(KARNATAKA_BOUNDARY):
            raise GeographicDomainError("The selected Sentinel-2 item is outside Karnataka")
        if not footprint.intersects(requested_geometry):
            continue
        if footprint.covers(requested_geometry):
            covered_items.append(item)

    if not covered_items:
        raise NoSuitableImageryError(
            "No Sentinel-2 item with a quality asset fully covers the saved AOI "
            "for the requested date window"
        )

    def catalog_key(item: Any) -> tuple[float, datetime, str]:
        cloud_cover = item.properties.get("eo:cloud_cover")
        try:
            cloud_sort_value = float(cloud_cover)
        except (TypeError, ValueError):
            cloud_sort_value = float("inf")
        return cloud_sort_value, _item_datetime(item), str(item.id)

    return sorted(covered_items, key=catalog_key)


def _discover_item(aoi: AOIGeometry, request: ImageryAcquisitionRequest) -> Any:
    return _discover_items(aoi, request)[0]


def _validate_source_raster(dataset: rasterio.DatasetReader, asset_id: str) -> None:
    if dataset.crs is None:
        raise RasterValidationError(f"Sentinel-2 {asset_id} asset has no CRS")
    if dataset.width <= 0 or dataset.height <= 0:
        raise RasterValidationError(f"Sentinel-2 {asset_id} asset has invalid dimensions")
    if dataset.count != 1:
        raise RasterValidationError(f"Sentinel-2 {asset_id} asset must contain one band")
    if dataset.transform.is_identity:
        raise RasterValidationError(f"Sentinel-2 {asset_id} asset has no georeferencing")
    if dataset.res[0] <= 0 or dataset.res[1] <= 0:
        raise RasterValidationError(f"Sentinel-2 {asset_id} asset has invalid resolution")


def _raster_metadata(dataset: rasterio.DatasetReader) -> RasterMetadata:
    return RasterMetadata(
        crs=str(dataset.crs),
        transform=[
            float(dataset.transform.a),
            float(dataset.transform.b),
            float(dataset.transform.c),
            float(dataset.transform.d),
            float(dataset.transform.e),
            float(dataset.transform.f),
        ],
        width=dataset.width,
        height=dataset.height,
        resolution=[float(dataset.res[0]), float(dataset.res[1])],
        bounds=[
            float(dataset.bounds.left),
            float(dataset.bounds.bottom),
            float(dataset.bounds.right),
            float(dataset.bounds.top),
        ],
        count=dataset.count,
        dtype=dataset.dtypes[0],
        nodata=dataset.nodata,
    )


def _prepared_path(aoi_id: int, item_id: str) -> Path:
    safe_item_id = re.sub(r"[^A-Za-z0-9_.-]", "_", item_id)
    return settings.imagery_data_dir / str(aoi_id) / safe_item_id / f"{safe_item_id}_red_nir.tif"


def _display_path(aoi_id: int, item_id: str) -> Path:
    safe_item_id = re.sub(r"[^A-Za-z0-9_.-]", "_", item_id)
    return settings.imagery_data_dir / str(aoi_id) / safe_item_id / f"{safe_item_id}_rgb.tif"


def _prepared_raster_is_valid(path: Path) -> bool:
    try:
        with rasterio.open(path) as prepared:
            return (
                prepared.count == 2
                and prepared.width > 0
                and prepared.height > 0
                and prepared.crs is not None
            )
    except (RasterioIOError, OSError, ValueError):
        return False


def _geometry_in_raster_crs(geometry: Any, crs: Any) -> Any:
    bounds = geometry.bounds
    if not (-180 <= bounds[0] <= 180 and -90 <= bounds[1] <= 90 and -180 <= bounds[2] <= 180 and -90 <= bounds[3] <= 90):
        return geometry
    geometry_in_crs = transform_geom("EPSG:4326", crs, mapping(geometry), precision=10)
    return shape(geometry_in_crs)


def _prepared_raster_covers_aoi(path: Path, aoi: AOIGeometry) -> bool:
    try:
        with rasterio.open(path) as prepared:
            geometry = shape(aoi.model_dump())
            projected_geometry = _geometry_in_raster_crs(geometry, prepared.crs)
            if projected_geometry.is_empty or not projected_geometry.is_valid:
                return False
            requested_bounds = projected_geometry.bounds
            epsilon = 1e-6
            return (
                prepared.bounds.left <= requested_bounds[0] + epsilon
                and prepared.bounds.bottom <= requested_bounds[1] + epsilon
                and prepared.bounds.right >= requested_bounds[2] - epsilon
                and prepared.bounds.top >= requested_bounds[3] - epsilon
            )
    except (RasterioIOError, OSError, ValueError, TypeError):
        return False


def _quality_mask_path(prepared_path: Path) -> Path:
    return prepared_path.with_name(f"{prepared_path.stem}_quality_mask.tif")


def _write_quality_mask(item: Any, prepared_path: Path) -> Path:
    quality_asset = item.assets[QUALITY_ASSET_ID]
    quality_href = getattr(quality_asset, "href", None)
    if not quality_href or not quality_href.startswith(("http://", "https://")):
        raise AssetAcquisitionError("The Sentinel-2 SCL quality asset does not have an HTTP URL")

    mask_path = _quality_mask_path(prepared_path)
    temporary_fd, temporary_name = tempfile.mkstemp(
        prefix=f".{mask_path.stem}-", suffix=".tmp", dir=mask_path.parent
    )
    os.close(temporary_fd)
    temporary_path = Path(temporary_name)
    try:
        with rasterio.open(quality_href) as source, rasterio.open(prepared_path) as prepared:
            _validate_source_raster(source, QUALITY_ASSET_ID)
            if source.crs is None or prepared.crs is None:
                raise RasterValidationError("SCL and prepared imagery must have CRS metadata")

            # Determine AOI bounding box in SCL source CRS
            if source.crs != prepared.crs:
                s_left, s_bottom, s_right, s_top = transform_bounds(
                    prepared.crs, source.crs, *prepared.bounds
                )
            else:
                s_left, s_bottom, s_right, s_top = prepared.bounds

            # Buffer source window by 2 source pixels on each side to ensure
            # full coverage for nearest-neighbor resampling along target grid edges
            res_x = abs(source.res[0])
            res_y = abs(source.res[1])
            s_left -= 2 * res_x
            s_right += 2 * res_x
            s_bottom -= 2 * res_y
            s_top += 2 * res_y

            # Convert to window and clamp within source dimensions
            raw_win = from_bounds(s_left, s_bottom, s_right, s_top, transform=source.transform)
            col_off = max(0, min(int(np.floor(raw_win.col_off)), source.width - 1))
            row_off = max(0, min(int(np.floor(raw_win.row_off)), source.height - 1))
            col_end = max(col_off + 1, min(int(np.ceil(raw_win.col_off + raw_win.width)), source.width))
            row_end = max(row_off + 1, min(int(np.ceil(raw_win.row_off + raw_win.height)), source.height))
            clamped_win = Window(col_off, row_off, col_end - col_off, row_end - row_off)
            win_transform = window_transform(clamped_win, source.transform)

            source_data = source.read(1, window=clamped_win)
            destination = np.zeros((prepared.height, prepared.width), dtype=np.uint8)
            reproject(
                source_data,
                destination,
                src_transform=win_transform,
                src_crs=source.crs,
                src_nodata=source.nodata if source.nodata is not None else 0,
                dst_transform=prepared.transform,
                dst_crs=prepared.crs,
                dst_nodata=0,
                resampling=Resampling.nearest,
            )
            profile = prepared.profile.copy()
            profile.update(
                driver="GTiff",
                count=1,
                dtype="uint8",
                nodata=0,
                compress="deflate",
            )
            with rasterio.open(temporary_path, "w", **profile) as output:
                output.write(destination, 1)
        os.replace(temporary_path, mask_path)
        return mask_path
    except RasterValidationError:
        raise
    except RasterioIOError as exc:
        raise AssetAcquisitionError("The Sentinel-2 SCL quality asset could not be read") from exc
    except (ValueError, OSError) as exc:
        raise RasterValidationError("The Sentinel-2 SCL quality mask could not be prepared") from exc
    finally:
        temporary_path.unlink(missing_ok=True)


def _derive_target_grid_from_scl(
    scl_source: rasterio.DatasetReader, aoi: AOIGeometry
) -> TargetRasterGrid:
    if scl_source.crs is None:
        raise RasterValidationError("Sentinel-2 scl asset has no CRS")
    if scl_source.transform.is_identity:
        raise RasterValidationError("Sentinel-2 scl asset has no georeferencing")
    if scl_source.width <= 0 or scl_source.height <= 0:
        raise RasterValidationError("Sentinel-2 scl asset has invalid dimensions")
    if scl_source.res[0] <= 0 or scl_source.res[1] <= 0:
        raise RasterValidationError("Sentinel-2 scl asset has invalid resolution")

    geometry = shape(aoi.model_dump())
    geometry_in_crs = transform_geom("EPSG:4326", scl_source.crs, mapping(geometry), precision=10)

    # Sentinel-2 SCL is 20m resolution, while 10m bands (B02, B03, B04, B08) are 10m.
    # If SCL is approximately 20m, the authoritative prepared raster grid is 10m (half of 20m).
    # Otherwise preserve native resolution (supporting synthetic tests).
    res_x = abs(scl_source.res[0])
    res_y = abs(scl_source.res[1])
    if np.isclose(res_x, 20.0, atol=1e-3) and np.isclose(res_y, 20.0, atol=1e-3):
        target_res_x = 10.0
        target_res_y = 10.0
    else:
        target_res_x = res_x
        target_res_y = res_y

    grid_affine = Affine(target_res_x, 0.0, scl_source.transform.c, 0.0, -target_res_y, scl_source.transform.f)

    class _TargetDatasetHeader:
        def __init__(self, scl: rasterio.DatasetReader, transform: Affine, rx: float, ry: float):
            self.transform = transform
            self.width = int(round(scl.width * (abs(scl.res[0]) / rx)))
            self.height = int(round(scl.height * (abs(scl.res[1]) / ry)))
            self.crs = scl.crs

    header = _TargetDatasetHeader(scl_source, grid_affine, target_res_x, target_res_y)
    win = geometry_window(header, [geometry_in_crs])
    target_transform = window_transform(win, grid_affine)
    width = int(win.width)
    height = int(win.height)
    bounds = (
        target_transform.c,
        target_transform.f + height * target_transform.e,
        target_transform.c + width * target_transform.a,
        target_transform.f,
    )
    return TargetRasterGrid(
        crs=scl_source.crs,
        transform=target_transform,
        width=width,
        height=height,
        bounds=bounds,
        resolution=(target_res_x, target_res_y),
    )


def _screen_scl_candidate(
    item: Any,
    aoi_id: int,
    aoi: AOIGeometry,
) -> SCLScreeningResult:
    quality_asset = item.assets.get(QUALITY_ASSET_ID)
    quality_href = getattr(quality_asset, "href", None) if quality_asset else None
    if not quality_href or not quality_href.startswith(("http://", "https://")):
        raise AssetAcquisitionError("The Sentinel-2 SCL quality asset does not have an HTTP URL")

    expected_prepared = _prepared_path(aoi_id, str(item.id))
    mask_path = _quality_mask_path(expected_prepared)
    mask_path.parent.mkdir(parents=True, exist_ok=True)

    temporary_fd, temporary_name = tempfile.mkstemp(
        prefix=f".{mask_path.stem}-", suffix=".tmp", dir=mask_path.parent
    )
    os.close(temporary_fd)
    temporary_path = Path(temporary_name)

    try:
        with rasterio.open(quality_href) as source:
            _validate_source_raster(source, QUALITY_ASSET_ID)
            target_grid = _derive_target_grid_from_scl(source, aoi)

            if source.crs != target_grid.crs:
                s_left, s_bottom, s_right, s_top = transform_bounds(
                    target_grid.crs, source.crs, *target_grid.bounds
                )
            else:
                s_left, s_bottom, s_right, s_top = target_grid.bounds

            res_x = abs(source.res[0])
            res_y = abs(source.res[1])
            s_left -= 2 * res_x
            s_right += 2 * res_x
            s_bottom -= 2 * res_y
            s_top += 2 * res_y

            raw_win = from_bounds(s_left, s_bottom, s_right, s_top, transform=source.transform)
            col_off = max(0, min(int(np.floor(raw_win.col_off)), source.width - 1))
            row_off = max(0, min(int(np.floor(raw_win.row_off)), source.height - 1))
            col_end = max(col_off + 1, min(int(np.ceil(raw_win.col_off + raw_win.width)), source.width))
            row_end = max(row_off + 1, min(int(np.ceil(raw_win.row_off + raw_win.height)), source.height))
            clamped_win = Window(col_off, row_off, col_end - col_off, row_end - row_off)
            win_transform = window_transform(clamped_win, source.transform)

            source_data = source.read(1, window=clamped_win)
            destination = np.zeros((target_grid.height, target_grid.width), dtype=np.uint8)
            reproject(
                source_data,
                destination,
                src_transform=win_transform,
                src_crs=source.crs,
                src_nodata=source.nodata if source.nodata is not None else 0,
                dst_transform=target_grid.transform,
                dst_crs=target_grid.crs,
                dst_nodata=0,
                resampling=Resampling.nearest,
            )

            profile = {
                "driver": "GTiff",
                "count": 1,
                "height": target_grid.height,
                "width": target_grid.width,
                "transform": target_grid.transform,
                "crs": target_grid.crs,
                "dtype": "uint8",
                "nodata": 0,
                "compress": "deflate",
            }
            with rasterio.open(temporary_path, "w", **profile) as output:
                output.write(destination, 1)
        os.replace(temporary_path, mask_path)
    except RasterValidationError:
        raise
    except RasterioIOError as exc:
        raise AssetAcquisitionError("The Sentinel-2 SCL quality asset could not be read") from exc
    except (ValueError, OSError) as exc:
        raise RasterValidationError("The Sentinel-2 SCL quality mask could not be prepared") from exc
    finally:
        temporary_path.unlink(missing_ok=True)

    # Upper-bound screening logic
    scl_values = destination
    total_pixels = int(scl_values.size)
    source_invalid = ~np.isin(scl_values, tuple(VALID_SCL_CLASSES | CLOUD_SCL_CLASSES | SHADOW_SCL_CLASSES))
    cloud = ~source_invalid & np.isin(scl_values, tuple(CLOUD_SCL_CLASSES))
    shadow = ~source_invalid & np.isin(scl_values, tuple(SHADOW_SCL_CLASSES))
    potential_usable = ~source_invalid & ~cloud & ~shadow

    invalid_pixels = int(source_invalid.sum())
    cloud_pixels = int(cloud.sum())
    shadow_pixels = int(shadow.sum())
    usable_pixels = int(potential_usable.sum())

    def percentage(value: int) -> float:
        return round((value / total_pixels) * 100 if total_pixels else 0.0, 6)

    usable_fraction = usable_pixels / total_pixels if total_pixels else 0.0
    if usable_pixels == 0:
        passed = False
        reason = "no_usable_pixels"
    elif usable_fraction < settings.minimum_usable_pixel_fraction:
        passed = False
        reason = "insufficient_usable_pixels"
    else:
        passed = True
        reason = None

    metrics = {
        "evaluation_stage": "scl_screening",
        "total_pixels": total_pixels,
        "invalid_pixels": invalid_pixels,
        "cloud_pixels": cloud_pixels,
        "shadow_pixels": shadow_pixels,
        "usable_pixels": usable_pixels,
        "invalid_percentage": percentage(invalid_pixels),
        "cloud_percentage": percentage(cloud_pixels),
        "shadow_percentage": percentage(shadow_pixels),
        "usable_percentage": percentage(usable_pixels),
        "max_possible_usable_fraction": usable_fraction,
        "minimum_usable_pixel_fraction": settings.minimum_usable_pixel_fraction,
    }

    return SCLScreeningResult(
        passed=passed,
        mask_path=mask_path,
        metrics=metrics,
        reason=reason,
    )


def _assess_quality(prepared_path: Path, mask_path: Path) -> QualityAssessment:
    try:
        with rasterio.open(prepared_path) as prepared, rasterio.open(mask_path) as quality_mask:
            if (
                quality_mask.count != 1
                or quality_mask.width != prepared.width
                or quality_mask.height != prepared.height
                or quality_mask.crs != prepared.crs
                or quality_mask.transform != prepared.transform
            ):
                raise RasterValidationError(
                    "The SCL quality mask is not aligned with the prepared AOI raster"
                )
            red = prepared.read(1, masked=True).astype(np.float32)
            nir = prepared.read(2, masked=True).astype(np.float32)
            scl = quality_mask.read(1, masked=True)
    except RasterValidationError:
        raise
    except (RasterioIOError, OSError, ValueError) as exc:
        raise RasterValidationError("The prepared quality inputs could not be assessed") from exc

    red_values = red.filled(0.0)
    nir_values = nir.filled(0.0)
    denominator = red_values + nir_values
    source_invalid = (
        np.ma.getmaskarray(red)
        | np.ma.getmaskarray(nir)
        | np.ma.getmaskarray(scl)
        | ~np.isfinite(red_values)
        | ~np.isfinite(nir_values)
        | (denominator == 0)
    )
    scl_values = scl.filled(0).astype(np.uint8)
    source_invalid |= ~np.isin(scl_values, tuple(VALID_SCL_CLASSES | CLOUD_SCL_CLASSES | SHADOW_SCL_CLASSES))
    cloud = ~source_invalid & np.isin(scl_values, tuple(CLOUD_SCL_CLASSES))
    shadow = ~source_invalid & np.isin(scl_values, tuple(SHADOW_SCL_CLASSES))
    usable = ~source_invalid & ~cloud & ~shadow

    total_pixels = int(usable.size)
    invalid_pixels = int(source_invalid.sum())
    cloud_pixels = int(cloud.sum())
    shadow_pixels = int(shadow.sum())
    usable_pixels = int(usable.sum())

    def percentage(value: int) -> float:
        return round((value / total_pixels) * 100 if total_pixels else 0.0, 6)

    usable_percentage = percentage(usable_pixels)
    metrics: dict[str, int | float] = {
        "total_pixels": total_pixels,
        "invalid_pixels": invalid_pixels,
        "cloud_pixels": cloud_pixels,
        "shadow_pixels": shadow_pixels,
        "usable_pixels": usable_pixels,
        "invalid_percentage": percentage(invalid_pixels),
        "cloud_percentage": percentage(cloud_pixels),
        "shadow_percentage": percentage(shadow_pixels),
        "usable_percentage": usable_percentage,
        "minimum_usable_pixel_fraction": settings.minimum_usable_pixel_fraction,
    }
    usable_fraction = usable_pixels / total_pixels if total_pixels else 0.0
    if usable_fraction >= settings.minimum_usable_pixel_fraction and usable_pixels > 0:
        state = "usable"
        reason = "quality_policy_passed"
    elif usable_pixels == 0:
        state = "valid_unusable"
        reason = "no_usable_pixels"
    else:
        state = "valid_unusable"
        reason = "insufficient_usable_pixels"
    return QualityAssessment(state, reason, metrics, mask_path)


def _quality_sort_key(row: tuple[Any, ...]) -> tuple[float, float, float, str, str]:
    metrics = json.loads(row[14])
    return (
        -float(metrics.get("usable_percentage", 0.0)),
        float(metrics.get("cloud_percentage", 100.0)),
        float(metrics.get("shadow_percentage", 100.0)),
        row[6],
        row[4],
    )


def _prepare_raster(item: Any, aoi_id: int, aoi: AOIGeometry) -> tuple[Path, RasterMetadata]:
    assets = {asset_id: item.assets[asset_id] for asset_id in REQUIRED_ASSETS}
    hrefs = {asset_id: asset.href for asset_id, asset in assets.items()}
    if not all(href.startswith(("http://", "https://")) for href in hrefs.values()):
        raise AssetAcquisitionError("Required Sentinel-2 assets do not have HTTP URLs")

    output_path = _prepared_path(aoi_id, str(item.id))
    output_directory = output_path.parent
    output_directory.mkdir(parents=True, exist_ok=True)
    geometry = shape(aoi.model_dump())
    temporary_fd, temporary_name = tempfile.mkstemp(
        prefix=f".{output_path.stem}-", suffix=".tmp", dir=output_directory
    )
    os.close(temporary_fd)
    temporary_path = Path(temporary_name)

    try:
        with rasterio.open(hrefs["red"]) as red, rasterio.open(hrefs["nir"]) as nir:
            _validate_source_raster(red, "red")
            _validate_source_raster(nir, "nir")
            if red.crs != nir.crs or red.res != nir.res:
                raise RasterValidationError(
                    "Required Sentinel-2 assets do not share CRS and resolution"
                )
            geometry_in_crs = transform_geom(
                "EPSG:4326", red.crs, mapping(geometry), precision=10
            )
            red_data, output_transform = mask(
                red, [geometry_in_crs], crop=True, filled=True, nodata=red.nodata
            )
            nir_data, nir_transform = mask(
                nir, [geometry_in_crs], crop=True, filled=True, nodata=nir.nodata
            )
            if red_data.shape != nir_data.shape or output_transform != nir_transform:
                raise RasterValidationError(
                    "Required Sentinel-2 assets could not be aligned without resampling"
                )
            profile = red.profile.copy()
            profile.update(
                driver="GTiff",
                count=2,
                height=red_data.shape[1],
                width=red_data.shape[2],
                transform=output_transform,
                compress="deflate",
            )
            with rasterio.open(temporary_path, "w", **profile) as output:
                output.write(red_data[0], 1)
                output.write(nir_data[0], 2)
        with rasterio.open(temporary_path) as prepared:
            if prepared.count != 2 or prepared.crs is None:
                raise RasterValidationError("Prepared Sentinel-2 raster failed validation")
            projected_geometry = _geometry_in_raster_crs(geometry, prepared.crs)
            requested_bounds = projected_geometry.bounds
            epsilon = 1e-6
            if (
                prepared.bounds.left > requested_bounds[0] + epsilon
                or prepared.bounds.bottom > requested_bounds[1] + epsilon
                or prepared.bounds.right < requested_bounds[2] - epsilon
                or prepared.bounds.top < requested_bounds[3] - epsilon
            ):
                raise RasterValidationError(
                    "Prepared Sentinel-2 raster does not fully cover the saved AOI"
                )
            raster = _raster_metadata(prepared)
        os.replace(temporary_path, output_path)
        return output_path, raster
    except RasterValidationError:
        raise
    except RasterioIOError as exc:
        raise AssetAcquisitionError("A required Sentinel-2 asset could not be read") from exc
    except (ValueError, OSError) as exc:
        raise RasterValidationError(f"The Sentinel-2 raster could not be prepared: {exc}") from exc
    finally:
        temporary_path.unlink(missing_ok=True)


def _prepare_display_raster(
    item: Any,
    aoi_id: int,
    aoi: AOIGeometry,
    mask_path: Path | None = None,
    prepared_path: Path | None = None,
) -> tuple[Path, dict[str, Any]] | None:
    if not all(asset_id in item.assets and getattr(item.assets[asset_id], "href", None) for asset_id in DISPLAY_ASSETS):
        return None
    asset_ids = ("red", "green", "blue")
    hrefs = {asset_id: item.assets[asset_id].href for asset_id in asset_ids}
    if not all(href.startswith(("http://", "https://")) for href in hrefs.values()):
        raise AssetAcquisitionError("Sentinel-2 RGB assets do not have HTTP URLs")
    output_path = _display_path(aoi_id, str(item.id))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_fd, temporary_name = tempfile.mkstemp(prefix=f".{output_path.stem}-", suffix=".tmp", dir=output_path.parent)
    os.close(temporary_fd)
    temporary_path = Path(temporary_name)

    # Check for existing local prepared raster to reuse B04 if not explicitly passed
    if prepared_path is None:
        candidate_prepared = _prepared_path(aoi_id, str(item.id))
        if candidate_prepared.is_file():
            prepared_path = candidate_prepared

    local_red_ds = None
    if prepared_path is not None and Path(prepared_path).is_file():
        try:
            local_red_ds = rasterio.open(prepared_path)
        except (RasterioIOError, OSError):
            local_red_ds = None

    try:
        geometry = shape(aoi.model_dump())
        arrays: list[np.ndarray] = []
        output_transform = None
        profile = None
        scl: np.ndarray | None = None
        if mask_path is not None and mask_path.is_file():
            with rasterio.open(mask_path) as quality_ds:
                scl = quality_ds.read(1)
        p2_stats: dict[str, float] = {}
        p98_stats: dict[str, float] = {}

        if local_red_ds is not None:
            ref_crs = local_red_ds.crs
            ref_res = local_red_ds.res
        else:
            with rasterio.open(hrefs["red"]) as red:
                _validate_source_raster(red, "red")
                ref_crs = red.crs
                ref_res = red.res

        geometry_in_crs = transform_geom("EPSG:4326", ref_crs, mapping(geometry), precision=10)
        for asset_id in asset_ids:
            if asset_id == "red" and local_red_ds is not None:
                # Reuse already-prepared, AOI-cropped B04 data from the scientific acquisition path
                raw = local_red_ds.read(1).astype(np.float32)
                if output_transform is None:
                    output_transform = local_red_ds.transform
                    profile = local_red_ds.profile.copy()
            else:
                with rasterio.open(hrefs[asset_id]) as source:
                    _validate_source_raster(source, asset_id)
                    if source.crs != ref_crs or source.res != ref_res:
                        raise RasterValidationError("Sentinel-2 RGB assets do not share CRS and resolution")
                    data, transform = mask(source, [geometry_in_crs], crop=True, filled=True, nodata=source.nodata)
                    if arrays and data.shape[1:] != arrays[0].shape:
                        raise RasterValidationError("Sentinel-2 RGB assets could not be aligned")
                    if output_transform is None:
                        output_transform = transform
                        profile = source.profile.copy()
                    raw = np.asarray(data[0], dtype=np.float32)

            # Spatial validity from persisted AOI geometry polygon
            inside_aoi = geometry_mask(
                [geometry_in_crs],
                out_shape=raw.shape,
                transform=output_transform,
                invert=True,
            )

            # Authoritative Phase B quality mask:
            # Calculate p2 and p98 only from pixels that are:
            # - strictly inside the persisted AOI polygon (inside_aoi)
            # - source-valid (np.isfinite, > 0)
            # - accepted by the Phase B quality mask (scl in VALID_SCL_CLASSES = {4, 5, 6})
            # - exclude cloud / shadow / invalid pixels
            # - do not allow rectangular background / masked pixels to influence percentile statistics
            accepted_mask = inside_aoi & (raw > 0) & np.isfinite(raw)
            if scl is not None and scl.shape == raw.shape:
                accepted_mask &= np.isin(scl, tuple(VALID_SCL_CLASSES))

            accepted_values = raw[accepted_mask]
            if accepted_values.size > 0:
                p2 = float(np.percentile(accepted_values, 2))
                p98 = float(np.percentile(accepted_values, 98))
                if p98 <= p2:
                    p98 = p2 + 1.0
            else:
                p2, p98 = 0.0, 3000.0

            p2_stats[asset_id] = p2
            p98_stats[asset_id] = p98

            # Map p2 -> 0 and p98 -> 255; clamp below/above deterministically
            stretched = np.clip((raw - p2) / (p98 - p2) * 255.0, 0.0, 255.0).astype(np.uint8)
            stretched[~inside_aoi] = 0
            arrays.append(stretched)

        if profile is None or output_transform is None:
            raise RasterValidationError("Sentinel-2 RGB display assets were empty")
        profile.update(
            driver="GTiff",
            count=3,
            dtype="uint8",
            nodata=0,
            height=arrays[0].shape[0],
            width=arrays[0].shape[1],
            transform=output_transform,
            compress="deflate",
        )
        with rasterio.open(temporary_path, "w", **profile) as output:
            for index, array in enumerate(arrays, start=1):
                output.write(array, index)
        with rasterio.open(temporary_path) as display:
            metadata = {
                "crs": str(display.crs),
                "bounds": [float(value) for value in display.bounds],
                "bounds_wgs84": [float(value) for value in transform_bounds(display.crs, "EPSG:4326", *display.bounds)],
                "width": display.width,
                "height": display.height,
                "dtype": "uint8",
                "bands": ["B04 red", "B03 green", "B02 blue"],
                "visualization_version": VISUALIZATION_VERSION,
                "stretch": {
                    "method": "quality_aware_percentile_p2_p98",
                    "p2": p2_stats,
                    "p98": p98_stats,
                },
            }
        os.replace(temporary_path, output_path)
        return output_path, metadata
    except RasterValidationError:
        raise
    except RasterioIOError as exc:
        raise AssetAcquisitionError("A Sentinel-2 RGB asset could not be read") from exc
    except (ValueError, OSError) as exc:
        raise RasterValidationError(f"The Sentinel-2 RGB display raster could not be prepared: {exc}") from exc
    finally:
        if local_red_ds is not None:
            try:
                local_red_ds.close()
            except Exception:
                pass
        temporary_path.unlink(missing_ok=True)


def _response_from_row(row: tuple[Any, ...]) -> ImageryAcquisitionResponse:
    return ImageryAcquisitionResponse(
        acquisition_id=row[0],
        aoi_id=row[1],
        requested_start_datetime=datetime.fromisoformat(row[2]),
        requested_end_datetime=datetime.fromisoformat(row[3]),
        item_id=row[4],
        collection=row[5],
        acquisition_datetime=datetime.fromisoformat(row[6]),
        assets=[ImageryAsset.model_validate(asset) for asset in json.loads(row[7])],
        prepared_path=row[8],
        raster=RasterMetadata.model_validate(json.loads(row[9])) if row[9] and row[9] != "{}" else None,
        source_metadata=json.loads(row[10]),
        created_at=datetime.fromisoformat(row[11]),
        observation_state=row[12],
        quality_reason=row[13],
        quality_metrics=json.loads(row[14]),
        quality_mask_path=row[15],
        processing_version=row[16],
        masking_method=row[17],
        quality_asset_id=row[18],
        display_path=row[19],
        display_metadata=json.loads(row[20]) if row[20] else None,
        visualization_version=row[21],
    )


def _existing_acquisition(aoi_id: int, item_id: str) -> tuple[Any, ...] | None:
    with connection() as db_connection:
        return db_connection.execute(
            f"SELECT {_acquisition_columns()} FROM imagery_acquisitions WHERE aoi_id = ? AND item_id = ?",
            (aoi_id, item_id),
        ).fetchone()


def _repair_existing_acquisition(
    row: tuple[Any, ...], item: Any, aoi_id: int, aoi: AOIGeometry
) -> tuple[Any, ...]:
    expected_path = _prepared_path(aoi_id, str(item.id))
    if row[8] == str(expected_path) and _prepared_raster_is_valid(expected_path):
        if _prepared_raster_covers_aoi(expected_path, aoi):
            return row
    prepared_path, raster = _prepare_raster(item, aoi_id, aoi)
    with connection() as db_connection:
        db_connection.execute(
            "UPDATE imagery_acquisitions SET prepared_path = ?, raster_metadata_json = ? WHERE id = ?",
            (str(prepared_path), raster.model_dump_json(), row[0]),
        )
        db_connection.commit()
        repaired = db_connection.execute(
            f"SELECT {_acquisition_columns()} FROM imagery_acquisitions WHERE id = ?",
            (row[0],),
        ).fetchone()
    if repaired is None:
        raise CatalogFailureError("The existing imagery acquisition could not be repaired")
    return repaired


def _is_scl_screened_unusable(row: tuple[Any, ...]) -> bool:
    if row[12] != "valid_unusable":
        return False
    try:
        metrics = json.loads(row[14]) if row[14] else {}
        return metrics.get("evaluation_stage") == "scl_screening"
    except (json.JSONDecodeError, TypeError):
        return False


def _observation_artifacts_valid(row: tuple[Any, ...]) -> bool:
    state = row[12]
    prepared_path_str = row[8]
    mask_path_str = row[15]
    display_path_str = row[19]
    viz_version = row[21]

    mask_ok = mask_path_str is not None and Path(mask_path_str).is_file()

    if state == "usable":
        prepared_ok = bool(prepared_path_str and _prepared_raster_is_valid(Path(prepared_path_str)))
        display_ok = bool(
            display_path_str is not None
            and Path(display_path_str).is_file()
            and viz_version == VISUALIZATION_VERSION
        )
        return bool(prepared_ok and mask_ok and display_ok)

    if state == "valid_unusable":
        if _is_scl_screened_unusable(row):
            return bool(mask_ok)
        prepared_ok = bool(prepared_path_str and _prepared_raster_is_valid(Path(prepared_path_str)))
        return bool(prepared_ok and mask_ok)

    return False


def _process_single_scene_worker(
    item: Any,
    aoi_id: int,
    aoi: AOIGeometry,
) -> SceneProcessingResult:
    item_id = str(item.id)
    assets = [
        ImageryAsset(asset_id=asset_id, href=item.assets[asset_id].href)
        for asset_id in (*REQUIRED_ASSETS, QUALITY_ASSET_ID)
    ]
    if all(asset_id in item.assets and getattr(item.assets[asset_id], "href", None) for asset_id in DISPLAY_ASSETS):
        assets.extend(
            ImageryAsset(asset_id=asset_id, href=item.assets[asset_id].href)
            for asset_id in DISPLAY_ASSETS
        )
    acquisition_datetime = _item_datetime(item)
    collection = item.collection_id or settings.stac_collection
    source_metadata = dict(item.properties)
    created_at = datetime.now(UTC)

    screening = None
    try:
        screening = _screen_scl_candidate(item, aoi_id, aoi)
    except Exception:
        screening = None

    if screening is not None and not screening.passed:
        return SceneProcessingResult(
            item_id=item_id,
            status="screened_unusable",
            item=item,
            assets=assets,
            acquisition_datetime=acquisition_datetime,
            collection=collection,
            source_metadata=source_metadata,
            created_at=created_at,
            screening=screening,
        )

    try:
        prepared_path, raster = _prepare_raster(item, aoi_id, aoi)
        if screening is not None and screening.mask_path.is_file():
            mask_path = screening.mask_path
        else:
            mask_path = _write_quality_mask(item, prepared_path)
        quality = _assess_quality(prepared_path, mask_path)
        if quality.state == "usable":
            display_result = _prepare_display_raster(item, aoi_id, aoi, screening.mask_path if screening else None, prepared_path)
            display_path = display_result[0] if display_result else None
            display_metadata = display_result[1] if display_result else None
        else:
            display_result = None
            display_path = None
            display_metadata = None

        return SceneProcessingResult(
            item_id=item_id,
            status="completed",
            item=item,
            assets=assets,
            acquisition_datetime=acquisition_datetime,
            collection=collection,
            source_metadata=source_metadata,
            created_at=created_at,
            screening=screening,
            prepared_path=prepared_path,
            raster=raster,
            quality=quality,
            display_path=display_path,
            display_metadata=display_metadata,
        )
    except (AssetAcquisitionError, RasterValidationError) as exc:
        return SceneProcessingResult(
            item_id=item_id,
            status="failed",
            item=item,
            assets=assets,
            acquisition_datetime=acquisition_datetime,
            collection=collection,
            source_metadata=source_metadata,
            created_at=created_at,
            error_message=str(exc),
            error_exception=exc,
        )
    except Exception as exc:
        return SceneProcessingResult(
            item_id=item_id,
            status="failed",
            item=item,
            assets=assets,
            acquisition_datetime=acquisition_datetime,
            collection=collection,
            source_metadata=source_metadata,
            created_at=created_at,
            error_message=str(exc),
            error_exception=exc,
        )


def acquire_imagery_events(request: ImageryAcquisitionRequest) -> Iterator[dict[str, Any]]:
    aoi_id, aoi = _load_aoi(request.aoi_id)
    items = _discover_items(aoi, request)
    total = len(items)
    yield {
        "type": "progress",
        "current": 0,
        "total": total,
        "item_id": None,
        "state": "discovered",
    }
    usable_rows: list[tuple[Any, ...]] = []
    failure_reasons: list[str] = []
    quality_failures: list[str] = []
    processing_errors: list[Exception] = []
    completed_count = 0

    items_to_process: list[Any] = []

    # 1. Provenance-validated reuse check on existing acquisitions
    for item in items:
        item_id = str(item.id)
        existing = _existing_acquisition(aoi_id, item_id)
        if existing is not None:
            if existing[12] == "usable" and _observation_artifacts_valid(existing):
                usable_rows.append(existing)
                completed_count += 1
                yield {
                    "type": "progress",
                    "current": completed_count,
                    "total": total,
                    "item_id": item_id,
                    "state": "available",
                }
                continue
            elif existing[12] == "valid_unusable" and _observation_artifacts_valid(existing):
                failure_reasons.append(f"{item_id}: {existing[13]}")
                quality_failures.append(f"{item_id}: {existing[13]}")
                completed_count += 1
                yield {
                    "type": "progress",
                    "current": completed_count,
                    "total": total,
                    "item_id": item_id,
                    "state": "screened_unusable",
                }
                continue
        items_to_process.append(item)

    # 2. Process non-reused observations via bounded worker pool
    if items_to_process:
        concurrency = max(1, settings.acquisition_concurrency)
        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
            future_to_item = {
                executor.submit(_process_single_scene_worker, item, aoi_id, aoi): item
                for item in items_to_process
            }
            for future in concurrent.futures.as_completed(future_to_item):
                result = future.result()
                item_id = result.item_id
                existing = _existing_acquisition(aoi_id, item_id)

                if result.status == "screened_unusable" and result.screening is not None:
                    screening = result.screening
                    values = (
                        aoi_id,
                        request.start_datetime.isoformat(),
                        request.end_datetime.isoformat(),
                        item_id,
                        result.collection,
                        result.acquisition_datetime.isoformat(),
                        json.dumps([asset.model_dump() for asset in result.assets]),
                        "",
                        "{}",
                        json.dumps(result.source_metadata),
                        result.created_at.isoformat(),
                        "valid_unusable",
                        screening.reason,
                        json.dumps(screening.metrics, separators=(",", ":")),
                        str(screening.mask_path),
                        PROCESSING_VERSION,
                        MASKING_METHOD,
                        QUALITY_ASSET_ID,
                        None,
                        None,
                        None,
                    )
                    with connection() as db_connection:
                        target_id = existing[0] if existing is not None else None
                        if target_id is None:
                            try:
                                db_connection.execute(
                                    """
                                    INSERT INTO imagery_acquisitions (
                                        aoi_id, requested_start_datetime, requested_end_datetime,
                                        item_id, collection, acquisition_datetime, assets_json,
                                        prepared_path, raster_metadata_json, source_metadata_json, created_at,
                                        observation_state, quality_reason, quality_metrics_json,
                                        quality_mask_path, processing_version, masking_method, quality_asset_id,
                                        display_path, display_metadata_json, visualization_version
                                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                    """,
                                    values,
                                )
                            except sqlite3.IntegrityError:
                                raced = db_connection.execute(
                                    f"SELECT {_acquisition_columns()} FROM imagery_acquisitions WHERE aoi_id = ? AND item_id = ?",
                                    (aoi_id, item_id),
                                ).fetchone()
                                if raced is None:
                                    raise
                                target_id = raced[0]
                        if target_id is not None:
                            db_connection.execute(
                                """
                                UPDATE imagery_acquisitions SET
                                    assets_json = ?, prepared_path = ?, raster_metadata_json = ?,
                                    source_metadata_json = ?, observation_state = ?, quality_reason = ?,
                                    quality_metrics_json = ?, quality_mask_path = ?, processing_version = ?,
                                    masking_method = ?, quality_asset_id = ?,
                                    display_path = ?, display_metadata_json = ?, visualization_version = ?
                                WHERE id = ?
                                """,
                                (
                                    values[6], values[7], values[8], values[9], values[11], values[12],
                                    values[13], values[14], values[15], values[16], values[17], values[18], values[19], values[20], target_id,
                                ),
                            )
                        db_connection.commit()
                    failure_reasons.append(f"{item_id}: {screening.reason}")
                    quality_failures.append(f"{item_id}: {screening.reason}")
                    completed_count += 1
                    yield {
                        "type": "progress",
                        "current": completed_count,
                        "total": total,
                        "item_id": item_id,
                        "state": "screened_unusable",
                    }

                elif result.status == "completed" and result.quality is not None and result.prepared_path is not None and result.raster is not None:
                    quality = result.quality
                    values = (
                        aoi_id,
                        request.start_datetime.isoformat(),
                        request.end_datetime.isoformat(),
                        item_id,
                        result.collection,
                        result.acquisition_datetime.isoformat(),
                        json.dumps([asset.model_dump() for asset in result.assets]),
                        str(result.prepared_path),
                        result.raster.model_dump_json(),
                        json.dumps(result.source_metadata),
                        result.created_at.isoformat(),
                        quality.state,
                        quality.reason,
                        json.dumps(quality.metrics, separators=(",", ":")),
                        str(quality.mask_path),
                        PROCESSING_VERSION,
                        MASKING_METHOD,
                        QUALITY_ASSET_ID,
                        str(result.display_path) if result.display_path else None,
                        json.dumps(result.display_metadata, separators=(",", ":")) if result.display_metadata else None,
                        VISUALIZATION_VERSION if result.display_path else None,
                    )
                    with connection() as db_connection:
                        target_id = existing[0] if existing is not None else None
                        if target_id is None:
                            try:
                                db_connection.execute(
                                    """
                                    INSERT INTO imagery_acquisitions (
                                        aoi_id, requested_start_datetime, requested_end_datetime,
                                        item_id, collection, acquisition_datetime, assets_json,
                                        prepared_path, raster_metadata_json, source_metadata_json, created_at,
                                        observation_state, quality_reason, quality_metrics_json,
                                        quality_mask_path, processing_version, masking_method, quality_asset_id
                                        , display_path, display_metadata_json, visualization_version
                                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                    """,
                                    values,
                                )
                            except sqlite3.IntegrityError:
                                raced = db_connection.execute(
                                    f"SELECT {_acquisition_columns()} FROM imagery_acquisitions WHERE aoi_id = ? AND item_id = ?",
                                    (aoi_id, item_id),
                                ).fetchone()
                                if raced is None:
                                    raise
                                target_id = raced[0]
                        if target_id is not None:
                            db_connection.execute(
                                """
                                UPDATE imagery_acquisitions SET
                                    assets_json = ?, prepared_path = ?, raster_metadata_json = ?,
                                    source_metadata_json = ?, observation_state = ?, quality_reason = ?,
                                    quality_metrics_json = ?, quality_mask_path = ?, processing_version = ?,
                                    masking_method = ?, quality_asset_id = ?
                                    , display_path = ?, display_metadata_json = ?, visualization_version = ?
                                WHERE id = ?
                                """,
                                (
                                    values[6], values[7], values[8], values[9], values[11], values[12],
                                    values[13], values[14], values[15], values[16], values[17], values[18], values[19], values[20], target_id,
                                ),
                            )
                        db_connection.commit()
                    persisted = _existing_acquisition(aoi_id, item_id)
                    if persisted is None:
                        raise CatalogFailureError("The quality-assessed observation could not be retrieved")
                    if quality.state == "usable":
                        usable_rows.append(persisted)
                    else:
                        failure_reasons.append(f"{item_id}: {quality.reason}")
                        quality_failures.append(f"{item_id}: {quality.reason}")
                    completed_count += 1
                    yield {
                        "type": "progress",
                        "current": completed_count,
                        "total": total,
                        "item_id": item_id,
                        "state": "completed" if quality.state == "usable" else "screened_unusable",
                    }

                elif result.status == "failed":
                    error_msg = result.error_message or "Processing failure"
                    failure_reasons.append(f"{item_id}: {error_msg}")
                    if result.error_exception:
                        processing_errors.append(result.error_exception)
                    with connection() as db_connection:
                        if existing is not None:
                            db_connection.execute(
                                "UPDATE imagery_acquisitions SET observation_state = ?, quality_reason = ?, processing_version = ?, masking_method = ?, quality_asset_id = ? WHERE id = ?",
                                ("failed", error_msg, PROCESSING_VERSION, MASKING_METHOD, QUALITY_ASSET_ID, existing[0]),
                            )
                        else:
                            db_connection.execute(
                                """
                                INSERT INTO imagery_acquisitions (
                                    aoi_id, requested_start_datetime, requested_end_datetime,
                                    item_id, collection, acquisition_datetime, assets_json,
                                    prepared_path, raster_metadata_json, source_metadata_json, created_at,
                                    observation_state, quality_reason, quality_metrics_json,
                                    processing_version, masking_method, quality_asset_id
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, '', '{}', ?, ?, 'failed', ?, '{}', ?, ?, ?)
                                """,
                                (
                                    aoi_id, request.start_datetime.isoformat(), request.end_datetime.isoformat(),
                                    item_id, result.collection, result.acquisition_datetime.isoformat(),
                                    json.dumps([asset.model_dump() for asset in result.assets]),
                                    json.dumps(result.source_metadata), result.created_at.isoformat(), error_msg,
                                    PROCESSING_VERSION, MASKING_METHOD, QUALITY_ASSET_ID,
                                ),
                            )
                        db_connection.commit()
                    completed_count += 1
                    yield {
                        "type": "progress",
                        "current": completed_count,
                        "total": total,
                        "item_id": item_id,
                        "state": "failed",
                    }

    if not usable_rows:
        if processing_errors and not quality_failures:
            raise processing_errors[0]
        details = "; ".join(failure_reasons) if failure_reasons else "no quality-assessed observation passed"
        raise NoSuitableImageryError(f"No usable Sentinel-2 observation was available: {details}")

    best_row = min(usable_rows, key=_quality_sort_key)
    response = _response_from_row(best_row)
    yield {
        "type": "complete",
        "current": total,
        "total": total,
        "result": response.model_dump(mode="json"),
    }


def acquire_imagery(request: ImageryAcquisitionRequest) -> ImageryAcquisitionResponse:
    complete_event = None
    for event in acquire_imagery_events(request):
        if event.get("type") == "complete":
            complete_event = event
    if complete_event is None:
        raise NoSuitableImageryError("No usable Sentinel-2 observation was available")
    return ImageryAcquisitionResponse.model_validate(complete_event["result"])


def latest_acquisition() -> ImageryAcquisitionResponse:
    try:
        with connection() as db_connection:
            row = db_connection.execute(
                f"SELECT {_acquisition_columns()} FROM imagery_acquisitions ORDER BY id DESC LIMIT 1"
            ).fetchone()
    except Exception as exc:
        raise CatalogFailureError("Imagery provenance could not be retrieved") from exc
    if row is None:
        raise NoAcquisitionError("No imagery acquisition has been completed")
    return _response_from_row(row)


def list_acquisitions(aoi_id: int | None = None) -> ImageryAcquisitionListResponse:
    try:
        with connection() as db_connection:
            if aoi_id is None:
                rows = db_connection.execute(
                    f"SELECT {_acquisition_columns()} FROM imagery_acquisitions ORDER BY acquisition_datetime ASC, id ASC"
                ).fetchall()
            else:
                rows = db_connection.execute(
                    f"SELECT {_acquisition_columns()} FROM imagery_acquisitions WHERE aoi_id = ? ORDER BY acquisition_datetime ASC, id ASC",
                    (aoi_id,),
                ).fetchall()
    except Exception as exc:
        raise CatalogFailureError("Imagery provenance could not be retrieved") from exc
    return ImageryAcquisitionListResponse(
        acquisitions=[_response_from_row(row) for row in rows]
    )


def render_display(acquisition_id: int, requested_aoi_id: int | None = None) -> bytes:
    with connection() as db_connection:
        row = db_connection.execute(
            """
            SELECT i.id, i.aoi_id, i.display_path, i.display_metadata_json, i.quality_mask_path,
                   i.observation_state, i.visualization_version, a.geometry_json
            FROM imagery_acquisitions i
            JOIN aoi a ON a.id = i.aoi_id
            WHERE i.id = ?
            """,
            (acquisition_id,),
        ).fetchone()
    if row is None:
        raise DisplayImageryUnavailableError("The requested observation does not exist")
    if requested_aoi_id is not None and row[1] != requested_aoi_id:
        raise DisplayImageryError("The observation does not belong to the requested AOI")
    if row[5] != "usable" or not row[2] or not Path(row[2]).is_file():
        raise DisplayImageryUnavailableError("No display imagery is available for this observation")
    if not row[4] or not Path(row[4]).is_file():
        raise DisplayImageryUnavailableError("The observation quality mask is unavailable")
    if row[6] != VISUALIZATION_VERSION:
        raise DisplayImageryUnavailableError(
            f"Display imagery version '{row[6]}' is incompatible with current standard '{VISUALIZATION_VERSION}'"
        )
    try:
        with rasterio.open(row[2]) as display, rasterio.open(row[4]) as quality:
            if display.count != 3 or display.width != quality.width or display.height != quality.height or display.transform != quality.transform:
                raise DisplayImageryError("The display artifact and quality mask are not aligned")
            bands = display.read(out_dtype="uint8")
            scl = quality.read(1)

            aoi_geometry = shape(json.loads(row[7]))
            geometry_in_crs = transform_geom("EPSG:4326", display.crs, mapping(aoi_geometry), precision=10)
            inside_aoi = geometry_mask(
                [geometry_in_crs],
                out_shape=(display.height, display.width),
                transform=display.transform,
                invert=True,
            )

            # Visual display alpha semantics:
            # Inside the AOI:
            # - Finite/displayable RGB pixels are opaque (alpha 255), including clouds,
            #   shadows, and all observed spectral surfaces.
            # Outside the AOI:
            # - Masked to transparent (alpha 0) so only the analyst's defined AOI is displayed.
            # Genuinely missing/non-displayable RGB pixels remain transparent.
            # Note: The authoritative SCL quality mask remains strictly enforced for scientific
            # detection and temporal analysis, while the visual display presents the authentic observation.
            data_valid = np.isfinite(bands).all(axis=0)
            valid = inside_aoi & data_valid
            alpha = np.where(valid, 255, 0).astype(np.uint8)
            rgba = np.stack([bands[0], bands[1], bands[2], alpha], axis=-1)
            raw = b"".join(b"\x00" + row_data.tobytes() for row_data in rgba)
            def chunk(kind: bytes, data: bytes) -> bytes:
                return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
            return (
                b"\x89PNG\r\n\x1a\n"
                + chunk(b"IHDR", struct.pack(">IIBBBBB", display.width, display.height, 8, 6, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw))
                + chunk(b"IEND", b"")
            )
    except (RasterioIOError, OSError, ValueError, IndexError) as exc:
        raise DisplayImageryError(f"Satellite imagery could not be rendered for observation {acquisition_id}") from exc
