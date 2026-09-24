from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import numpy as np
import rasterio
from pyproj import Transformer
from rasterio.features import shapes
from scipy import ndimage
from rasterio.windows import Window, transform as window_transform
from shapely.geometry import shape
from shapely.ops import transform as transform_geometry, unary_union

from app.db import connection
from app.config import settings
from app.exceptions import (
    DetectionFailureError,
    DetectionRasterError,
    IncompatibleAcquisitionsError,
    MissingAfterAcquisitionError,
    MissingBeforeAcquisitionError,
)
from app.schemas import DetectionRequest, DetectionResponse, RawChangeRegion

DETECTOR_VERSION = "ndvi-absolute-difference-v1"
VALID_SCL_CLASSES = frozenset({4, 5, 6})
CLOUD_SCL_CLASSES = frozenset({8, 9, 10})
SHADOW_SCL_CLASSES = frozenset({3})


@dataclass(frozen=True)
class RasterComparison:
    change_signal: np.ndarray
    valid: np.ndarray
    transform: Any
    crs: Any
    quality_processing_version: str
    quality_valid_pixel_count: int
    excluded_pixel_count: int
    excluded_cloud_pixel_count: int
    excluded_shadow_pixel_count: int


def _acquisition_row(acquisition_id: int, before: bool) -> tuple[Any, ...]:
    with connection() as db_connection:
        row = db_connection.execute(
            """
                 SELECT id, aoi_id, item_id, acquisition_datetime, prepared_path,
                     raster_metadata_json, observation_state, quality_mask_path,
                     processing_version, masking_method
            FROM imagery_acquisitions WHERE id = ?
            """,
            (acquisition_id,),
        ).fetchone()
    if row is None:
        error = MissingBeforeAcquisitionError if before else MissingAfterAcquisitionError
        raise error(f"The requested {'before' if before else 'after'} acquisition does not exist")
    return row


def _quality_mask(dataset: rasterio.DatasetReader, row: tuple[Any, ...]) -> tuple[np.ndarray, str]:
    if row[6] != "usable":
        raise DetectionRasterError(
            f"Acquisition {row[0]} is not quality-assessed and usable for detection"
        )
    mask_path = row[7]
    if not mask_path:
        raise DetectionRasterError(f"Acquisition {row[0]} has no persisted quality mask")
    try:
        with rasterio.open(mask_path) as quality:
            if quality.count != 1:
                raise DetectionRasterError("Quality mask must contain exactly one band")
            if quality.width != dataset.width or quality.height != dataset.height:
                raise DetectionRasterError("Quality mask has incompatible dimensions")
            if quality.crs != dataset.crs:
                raise DetectionRasterError("Quality mask has incompatible CRS")
            if quality.transform != dataset.transform:
                raise DetectionRasterError("Quality mask has incompatible raster grid")
            values = quality.read(1, masked=True)
            if values.dtype.kind not in "ui":
                raise DetectionRasterError("Quality mask has invalid class values")
            classes = values.filled(0).astype(np.uint8)
            valid_classes = np.isin(classes, tuple(VALID_SCL_CLASSES))
            cloud_classes = np.isin(classes, tuple(CLOUD_SCL_CLASSES))
            shadow_classes = np.isin(classes, tuple(SHADOW_SCL_CLASSES))
            source_valid = ~np.ma.getmaskarray(values)
            valid = source_valid & (valid_classes | cloud_classes | shadow_classes)
            return np.stack((valid, source_valid & cloud_classes, source_valid & shadow_classes)), row[8]
    except DetectionRasterError:
        raise
    except (rasterio.errors.RasterioIOError, OSError, ValueError, TypeError) as exc:
        raise DetectionRasterError(f"The quality mask for acquisition {row[0]} could not be read") from exc


def _validate_compatibility(before: rasterio.DatasetReader, after: rasterio.DatasetReader) -> None:
    if before.count != 2 or after.count != 2:
        raise IncompatibleAcquisitionsError("Both acquisitions must contain red and nir bands")
    if before.dtypes[:2] != after.dtypes[:2]:
        raise IncompatibleAcquisitionsError("Acquisitions have incompatible band dtypes")
    if before.nodata != after.nodata:
        raise IncompatibleAcquisitionsError("Acquisitions have incompatible nodata values")
    if before.crs != after.crs:
        raise IncompatibleAcquisitionsError("Acquisitions have incompatible CRS")
    if before.width != after.width or before.height != after.height:
        raise IncompatibleAcquisitionsError("Acquisitions have incompatible dimensions")
    if before.res != after.res:
        raise IncompatibleAcquisitionsError("Acquisitions have incompatible resolution")
    if before.transform != after.transform:
        raise IncompatibleAcquisitionsError("Acquisitions have incompatible raster grids")
    if not np.allclose(
        [before.bounds.left, before.bounds.bottom, before.bounds.right, before.bounds.top],
        [after.bounds.left, after.bounds.bottom, after.bounds.right, after.bounds.top],
    ):
        raise IncompatibleAcquisitionsError("Acquisitions have incompatible bounds")


def _geometry_in_raster_crs(geometry: Any, crs: Any) -> Any:
    bounds = geometry.bounds
    if not (-180 <= bounds[0] <= 180 and -90 <= bounds[1] <= 90 and -180 <= bounds[2] <= 180 and -90 <= bounds[3] <= 90):
        return geometry
    return transform_geometry(
        Transformer.from_crs("EPSG:4326", crs, always_xy=True).transform,
        geometry,
    )


def _ensure_raster_covers_aoi(aoi_id: int, prepared_path: str) -> None:
    try:
        with connection() as db_connection:
            row = db_connection.execute(
                "SELECT geometry_json FROM aoi WHERE id = ?",
                (aoi_id,),
            ).fetchone()
        if row is None:
            raise DetectionRasterError("The saved AOI could not be resolved for coverage validation")
        aoi_geometry = shape(json.loads(row[0]))
        if aoi_geometry.is_empty or not aoi_geometry.is_valid:
            raise DetectionRasterError("The saved AOI geometry is invalid for coverage validation")
        with rasterio.open(prepared_path) as dataset:
            if dataset.crs is None:
                raise DetectionRasterError("Prepared imagery has no CRS for coverage validation")
            projected_geometry = _geometry_in_raster_crs(aoi_geometry, dataset.crs)
            bounds = projected_geometry.bounds
            epsilon = 1e-6
            if (
                dataset.bounds.left > bounds[0] + epsilon
                or dataset.bounds.bottom > bounds[1] + epsilon
                or dataset.bounds.right < bounds[2] - epsilon
                or dataset.bounds.top < bounds[3] - epsilon
            ):
                raise DetectionRasterError(
                    "The selected acquisition does not fully cover the saved AOI"
                )
    except (DetectionRasterError, IncompatibleAcquisitionsError):
        raise
    except (rasterio.errors.RasterioIOError, OSError, ValueError, TypeError) as exc:
        raise DetectionRasterError(
            "The selected acquisition raster could not be validated against the saved AOI"
        ) from exc


def compare_rasters(
    before_path: str,
    after_path: str,
    before_row: tuple[Any, ...] | None = None,
    after_row: tuple[Any, ...] | None = None,
) -> RasterComparison:
    try:
        with rasterio.open(before_path) as before, rasterio.open(after_path) as after:
            _validate_compatibility(before, after)
            if before_row is None or after_row is None:
                raise DetectionRasterError("Quality-assessed acquisition metadata is required")
            before_quality, before_version = _quality_mask(before, before_row)
            after_quality, after_version = _quality_mask(after, after_row)
            if before_version != after_version:
                raise DetectionRasterError("Acquisitions use incompatible quality processing versions")
            before_ndvi, before_valid = _read_ndvi(before)
            after_ndvi, after_valid = _read_ndvi(after)
            source_valid = before_valid & after_valid
            quality_valid = before_quality[0] & after_quality[0]
            cloud = before_quality[1] | after_quality[1]
            shadow = (before_quality[2] | after_quality[2]) & ~cloud
            quality_invalid = ~quality_valid | cloud | shadow
            invalid = ~source_valid | quality_invalid
            with np.errstate(divide="ignore", invalid="ignore"):
                change_signal = np.abs(after_ndvi - before_ndvi)
            valid = ~invalid & np.isfinite(change_signal)
            change_signal = np.where(valid, change_signal, 0.0)
            excluded_cloud = int((source_valid & cloud).sum())
            excluded_shadow = int((source_valid & shadow).sum())
            excluded = int(valid.size - valid.sum())
            return RasterComparison(
                change_signal, valid, before.transform, before.crs, before_version,
                int(valid.sum()), excluded, excluded_cloud, excluded_shadow,
            )
    except (IncompatibleAcquisitionsError, DetectionRasterError):
        raise
    except (rasterio.errors.RasterioIOError, OSError, ValueError) as exc:
        raise DetectionRasterError("An acquisition raster could not be read or validated") from exc


def _read_ndvi(dataset: rasterio.DatasetReader) -> tuple[np.ndarray, np.ndarray]:
    red = dataset.read(1, masked=True).astype(np.float32)
    nir = dataset.read(2, masked=True).astype(np.float32)
    invalid = np.ma.getmaskarray(red) | np.ma.getmaskarray(nir)
    red_values = red.filled(0.0)
    nir_values = nir.filled(0.0)
    denominator = nir_values + red_values
    with np.errstate(divide="ignore", invalid="ignore"):
        ndvi = (nir_values - red_values) / denominator
    valid = ~invalid & (denominator != 0) & np.isfinite(ndvi)
    return ndvi, valid


def _metric_geometry(geometry: Any, crs: Any) -> tuple[dict[str, Any], float]:
    area_crs = Transformer.from_crs(crs, "EPSG:6933", always_xy=True).transform
    geographic = Transformer.from_crs(crs, "EPSG:4326", always_xy=True).transform
    area = transform_geometry(area_crs, geometry).area
    return transform_geometry(geographic, geometry).__geo_interface__, float(area)


def _connected_components(changed: np.ndarray) -> list[np.ndarray]:
    if changed.size == 0:
        return []
    labels, num_labels = ndimage.label(changed, structure=np.ones((3, 3), dtype=np.uint8))
    if num_labels == 0:
        return []
    return [labels == label for label in range(1, num_labels + 1)]


def _component_geometries(labels: np.ndarray, transform: Any) -> dict[int, Any]:
    if labels.size == 0:
        return {}

    geometries: dict[int, Any] = {}
    for geometry_json, label_id in shapes(labels, mask=labels > 0, transform=transform, connectivity=8):
        geometries[int(label_id)] = shape(geometry_json)
    return geometries


def _component_geometry(
    component: np.ndarray,
    transform: Any,
) -> Any:
    rows, columns = np.nonzero(component)
    top = int(rows.min())
    left = int(columns.min())
    bottom = int(rows.max())
    right = int(columns.max())
    local_mask = component[top : bottom + 1, left : right + 1].astype(np.uint8)
    local_transform = window_transform(
        Window(left, top, local_mask.shape[1], local_mask.shape[0]), transform
    )
    polygons = [
        shape(geometry_json)
        for geometry_json, _ in shapes(local_mask, mask=local_mask.astype(bool), transform=local_transform)
    ]
    return unary_union(polygons)


def detect(request: DetectionRequest) -> DetectionResponse:
    before = _acquisition_row(request.before_acquisition_id, True)
    after = _acquisition_row(request.after_acquisition_id, False)
    if before[1] != after[1]:
        raise IncompatibleAcquisitionsError("Acquisitions belong to different AOIs")
    before_datetime = datetime.fromisoformat(before[3])
    after_datetime = datetime.fromisoformat(after[3])
    if before_datetime >= after_datetime:
        raise IncompatibleAcquisitionsError("Before acquisition must be earlier than after acquisition")
    comparison = compare_rasters(before[4], after[4], before, after)
    _ensure_raster_covers_aoi(before[1], before[4])
    _ensure_raster_covers_aoi(after[1], after[4])
    if comparison.quality_valid_pixel_count == 0:
        raise DetectionRasterError("No usable comparison pixels remain after quality filtering")
    changed = comparison.valid & (comparison.change_signal >= request.threshold)
    raw_changed_pixel_count = int(changed.sum())
    labels, num_labels = ndimage.label(changed, structure=np.ones((3, 3), dtype=np.uint8))
    regions: list[RawChangeRegion] = []
    filtered_changed_pixel_count = 0

    if num_labels > 0:
        changed_labels = labels[changed]
        label_counts = np.bincount(changed_labels, minlength=num_labels + 1)
        label_signal_sums = np.bincount(
            changed_labels,
            weights=comparison.change_signal[changed],
            minlength=num_labels + 1,
        )
        label_max_signal = np.full(num_labels + 1, -np.inf, dtype=np.float64)
        np.maximum.at(label_max_signal, changed_labels, comparison.change_signal[changed])

        valid_labels = np.flatnonzero(label_counts[1:] >= request.min_region_pixels) + 1
        filtered_changed_pixel_count = int(label_counts[valid_labels].sum()) if valid_labels.size else 0
        component_geometries = _component_geometries(labels, comparison.transform)

        for region_id, label_id in enumerate(valid_labels, start=1):
            pixel_count = int(label_counts[label_id])
            geometry = component_geometries.get(label_id)
            if geometry is None:
                geometry = _component_geometry(labels == label_id, comparison.transform)
            if geometry.is_empty or not geometry.is_valid:
                geometry = geometry.buffer(0)
            output_geometry, area = _metric_geometry(geometry, comparison.crs)
            mean_change_signal = float(label_signal_sums[label_id] / pixel_count)
            max_change_signal = float(label_max_signal[label_id])
            regions.append(RawChangeRegion(
                region_id=region_id,
                geometry=output_geometry,
                pixel_count=pixel_count,
                area_m2=area,
                mean_change_signal=mean_change_signal,
                max_change_signal=max_change_signal,
            ))
    region_areas = [region.area_m2 for region in regions]
    changed_values = comparison.change_signal[changed]
    mean_change_signal = float(changed_values.mean()) if changed_values.size else 0.0
    max_change_signal = float(changed_values.max()) if changed_values.size else 0.0
    changed_pixel_percentage = round(
        raw_changed_pixel_count / comparison.quality_valid_pixel_count * 100,
        6,
    )
    largest_region_area = max(region_areas, default=0.0)
    mean_region_area = float(np.mean(region_areas)) if region_areas else 0.0
    median_region_area = float(np.median(region_areas)) if region_areas else 0.0
    created_at = datetime.now(UTC)
    try:
        with connection() as db_connection:
            cursor = db_connection.execute(
                """INSERT INTO detection_runs (
                    before_acquisition_id, after_acquisition_id, detector_version,
                    threshold, min_region_pixels, region_count, changed_pixel_count,
                    total_changed_area_m2, created_at, quality_mask_used,
                    quality_processing_version, quality_valid_pixel_count,
                    excluded_pixel_count, excluded_cloud_pixel_count,
                    excluded_shadow_pixel_count, raw_changed_pixel_count,
                    filtered_changed_pixel_count, changed_pixel_percentage,
                    largest_region_area_m2, mean_region_area_m2, median_region_area_m2,
                    mean_change_signal, max_change_signal
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (before[0], after[0], DETECTOR_VERSION, request.threshold,
                 request.min_region_pixels, len(regions), filtered_changed_pixel_count,
                 sum(r.area_m2 for r in regions), created_at.isoformat(), 1,
                 comparison.quality_processing_version, comparison.quality_valid_pixel_count,
                 comparison.excluded_pixel_count, comparison.excluded_cloud_pixel_count,
                 comparison.excluded_shadow_pixel_count, raw_changed_pixel_count,
                 filtered_changed_pixel_count, changed_pixel_percentage,
                 largest_region_area, mean_region_area, median_region_area,
                 mean_change_signal, max_change_signal),
            )
            run_id = cursor.lastrowid
            for region in regions:
                db_connection.execute(
                    """INSERT INTO raw_change_regions (
                        run_id, geometry_json, pixel_count, area_m2,
                        mean_change_signal, max_change_signal
                    ) VALUES (?, ?, ?, ?, ?, ?)""",
                    (run_id, json.dumps(region.geometry), region.pixel_count, region.area_m2,
                     region.mean_change_signal, region.max_change_signal),
                )
            db_connection.commit()
    except Exception as exc:
        raise DetectionFailureError("The detection result could not be persisted") from exc
    return DetectionResponse(
        run_id=run_id, before_acquisition_id=before[0], after_acquisition_id=after[0],
        before_item_id=before[2], after_item_id=after[2], detector_version=DETECTOR_VERSION,
        threshold=request.threshold, min_region_pixels=request.min_region_pixels,
        created_at=created_at, region_count=len(regions),
        changed_pixel_count=filtered_changed_pixel_count,
        total_changed_area_m2=sum(r.area_m2 for r in regions), regions=regions,
        quality_mask_used=True,
        quality_processing_version=comparison.quality_processing_version,
        quality_valid_pixel_count=comparison.quality_valid_pixel_count,
        excluded_pixel_count=comparison.excluded_pixel_count,
        excluded_cloud_pixel_count=comparison.excluded_cloud_pixel_count,
        excluded_shadow_pixel_count=comparison.excluded_shadow_pixel_count,
        raw_changed_pixel_count=raw_changed_pixel_count,
        filtered_changed_pixel_count=filtered_changed_pixel_count,
        changed_pixel_percentage=changed_pixel_percentage,
        largest_region_area_m2=largest_region_area,
        mean_region_area_m2=mean_region_area,
        median_region_area_m2=median_region_area,
        mean_change_signal=mean_change_signal,
        max_change_signal=max_change_signal,
    )