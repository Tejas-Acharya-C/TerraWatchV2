from __future__ import annotations

import json
import struct
import zlib
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from rasterio.mask import mask
from rasterio.warp import transform_geom
from shapely.geometry import box, mapping, shape

from app.candidates import get_candidate
from app.db import connection
from app.domain import is_within_karnataka
from app.exceptions import CandidateEvidenceError, EvidenceRasterError, EvidenceUnavailableError
from app.schemas import (
    CandidateEvidenceResponse,
    EvidenceAcquisition,
    EvidenceInterval,
    RasterMetadata,
    RawChangeRegion,
)

VISUALIZATION_VERSION = "sentinel-2-rgb-percentile-v2"
QUALITY_PROCESSING_VERSION = "aoi-observation-quality-v1"


def _metadata(raw: str) -> RasterMetadata:
    return RasterMetadata.model_validate(json.loads(raw))


def _build_acquisition(row: tuple[Any, ...], candidate_id: str) -> EvidenceAcquisition:
    prepared_path = Path(row[5])
    if not prepared_path.is_file():
        raise EvidenceUnavailableError(
            f"Evidence raster for acquisition {row[0]} ({row[2]}) is missing from disk storage: {row[5]}"
        )
    try:
        with rasterio.open(prepared_path) as ds:
            _ = ds.count
    except Exception as exc:
        raise CandidateEvidenceError(
            f"Evidence raster for acquisition {row[0]} ({row[2]}) is unreadable: {exc}"
        )

    source = json.loads(row[7]) if row[7] else {}
    quality_metrics = json.loads(row[10]) if row[10] else {}

    usable_fraction = quality_metrics.get("valid_pixel_fraction") or quality_metrics.get("usable_pixel_fraction")
    cloud_fraction = quality_metrics.get("cloud_pixel_fraction") or quality_metrics.get("cloud_fraction")
    shadow_fraction = quality_metrics.get("shadow_pixel_fraction") or quality_metrics.get("shadow_fraction")
    invalid_fraction = quality_metrics.get("invalid_pixel_fraction") or quality_metrics.get("invalid_fraction")

    quality_mask_path = row[11]
    quality_mask_available = False
    if quality_mask_path and Path(quality_mask_path).is_file():
        try:
            with rasterio.open(quality_mask_path) as ds:
                _ = ds.count
            quality_mask_available = True
        except Exception:
            quality_mask_available = False

    display_path = row[14]
    display_exists = False
    if display_path and Path(display_path).is_file():
        try:
            with rasterio.open(display_path) as ds:
                _ = ds.count
            display_exists = True
        except Exception:
            display_exists = False
    display_version_ok = bool(row[16] == VISUALIZATION_VERSION)
    display_available = display_exists and display_version_ok
    display_url = f"/api/v1/imagery/acquisitions/{row[0]}/display?aoi_id={row[1]}" if display_available else None

    return EvidenceAcquisition(
        acquisition_id=row[0],
        aoi_id=row[1],
        item_id=row[2],
        collection=row[3],
        acquisition_datetime=row[4],
        cloud_cover=source.get("eo:cloud_cover"),
        mgrs_tile=source.get("s2:mgrs_tile"),
        observation_state=row[8] or "usable",
        quality_reason=row[9],
        usable_pixel_fraction=usable_fraction,
        cloud_fraction=cloud_fraction,
        shadow_fraction=shadow_fraction,
        invalid_fraction=invalid_fraction,
        quality_processing_version=row[12],
        masking_method=row[13],
        quality_mask_available=quality_mask_available,
        display_available=display_available,
        display_url=display_url,
        visualization_version=row[16],
        raster=_metadata(row[6]),
        bands=["B04 red", "B08 nir"],
        preview_url=f"/api/v1/candidates/{candidate_id}/evidence/acquisitions/{row[0]}/preview",
    )


def get_candidate_evidence(candidate_id: str) -> CandidateEvidenceResponse:
    candidate = get_candidate(candidate_id)
    candidate_geometry = shape(candidate.geometry)
    if candidate_geometry.is_empty or not candidate_geometry.is_valid or not is_within_karnataka(candidate_geometry):
        raise CandidateEvidenceError("Candidate geometry is outside the Karnataka evidence domain")

    with connection() as db:
        # 1. Verify temporal analysis exists
        analysis_row = db.execute(
            "SELECT id, state, observation_count, temporal_span_days FROM temporal_analyses WHERE id = ?",
            (candidate.analysis_id,),
        ).fetchone()
        if analysis_row is None:
            raise CandidateEvidenceError("Candidate provenance references a missing temporal analysis")

        # 2. Verify temporal signal exists and matches candidate
        signal = db.execute(
            """SELECT support_count, interval_count, persistence_ratio, recurrence_count,
               transient_interval_count, temporal_consistency, matched_region_coverage,
               first_change_datetime, last_supporting_datetime, state, source_region_ids_json,
               detection_run_ids_json, acquisition_ids_json, quality_support
               FROM temporal_signals WHERE analysis_id = ? AND signal_id = ?""",
            (candidate.analysis_id, candidate.signal_id),
        ).fetchone()
        if signal is None:
            raise CandidateEvidenceError("Candidate provenance references a missing temporal signal")

        run_ids = json.loads(signal[11]) if signal[11] else []
        acquisition_ids = json.loads(signal[12]) if signal[12] else []
        if run_ids != candidate.detection_run_ids or acquisition_ids != candidate.acquisition_ids:
            raise CandidateEvidenceError("Candidate provenance does not match its temporal signal")
        if not run_ids or not acquisition_ids:
            raise EvidenceUnavailableError("The candidate has no persisted detection evidence")

        # 3. Load all acquisitions referenced by the candidate
        marks = ",".join("?" for _ in acquisition_ids)
        acquisitions = db.execute(
            f"""SELECT id, aoi_id, item_id, collection, acquisition_datetime, prepared_path,
                       raster_metadata_json, source_metadata_json, observation_state, quality_reason,
                       quality_metrics_json, quality_mask_path, processing_version, masking_method,
                       display_path, display_metadata_json, visualization_version
                FROM imagery_acquisitions WHERE id IN ({marks})""",
            acquisition_ids,
        ).fetchall()
        by_id = {row[0]: row for row in acquisitions}
        if set(by_id) != set(acquisition_ids):
            raise CandidateEvidenceError("Candidate provenance references a missing acquisition")

        # 4. Verify AOI provenance & Karnataka containment
        aoi_ids = {row[1] for row in acquisitions}
        if not aoi_ids:
            raise CandidateEvidenceError("Candidate evidence has no associated AOI")
        primary_aoi_id = next(iter(aoi_ids))
        aoi_marks = ",".join("?" for _ in aoi_ids)
        aoi_rows = db.execute(
            f"SELECT id, geometry_json FROM aoi WHERE id IN ({aoi_marks})",
            tuple(sorted(aoi_ids)),
        ).fetchall()
        if not aoi_rows:
            raise CandidateEvidenceError("Candidate evidence references a missing AOI")
        for aoi_row in aoi_rows:
            try:
                aoi_geom = shape(json.loads(aoi_row[1]))
                if aoi_geom.is_empty or not aoi_geom.is_valid:
                    raise CandidateEvidenceError("Persisted AOI has invalid geometry")
            except (json.JSONDecodeError, ValueError):
                raise CandidateEvidenceError("Persisted AOI has unreadable geometry")
        if not any(candidate_geometry.intersects(shape(json.loads(aoi[1]))) for aoi in aoi_rows):
            raise CandidateEvidenceError("Candidate geometry does not intersect the persisted Karnataka AOI")

        # 5. Load detection runs and verify Before/After distinctness
        run_marks = ",".join("?" for _ in run_ids)
        runs = db.execute(
            f"""SELECT id, before_acquisition_id, after_acquisition_id, detector_version, threshold,
                       min_region_pixels, region_count, changed_pixel_count, total_changed_area_m2,
                       quality_mask_used, quality_processing_version, quality_valid_pixel_count,
                       excluded_pixel_count, excluded_cloud_pixel_count, excluded_shadow_pixel_count,
                       raw_changed_pixel_count, filtered_changed_pixel_count, changed_pixel_percentage,
                       largest_region_area_m2, mean_region_area_m2, median_region_area_m2,
                       mean_change_signal, max_change_signal
                FROM detection_runs WHERE id IN ({run_marks}) ORDER BY id""",
            run_ids,
        ).fetchall()
        if len(runs) != len(run_ids):
            raise CandidateEvidenceError("Candidate provenance references a missing detection run")

        # Validate that Before and After are distinct and match candidate acquisitions
        for run in runs:
            if run[1] not in by_id or run[2] not in by_id:
                raise CandidateEvidenceError("Detection provenance references an acquisition outside the candidate signal")
            if run[1] == run[2]:
                raise CandidateEvidenceError("Detection provenance has identical Before and After acquisition IDs")

        # 6. Build intervals and change regions
        region_ids = json.loads(signal[10]) if signal[10] else []
        intervals: list[EvidenceInterval] = []
        for run in runs:
            regions = db.execute(
                "SELECT id, geometry_json, pixel_count, area_m2, mean_change_signal, max_change_signal FROM raw_change_regions WHERE run_id = ? ORDER BY id",
                (run[0],),
            ).fetchall()
            selected = [region for region in regions if not region_ids or region[0] in region_ids]
            intervals.append(EvidenceInterval(
                detection_run_id=run[0],
                before_acquisition_id=run[1],
                after_acquisition_id=run[2],
                detector_version=run[3],
                threshold=run[4],
                min_region_pixels=run[5],
                changed_pixel_count=run[7],
                total_changed_area_m2=run[8],
                region_count=run[6],
                quality_mask_used=bool(run[9]),
                quality_processing_version=run[10] or "legacy-unassessed",
                quality_valid_pixel_count=run[11] or 0,
                excluded_pixel_count=run[12] or 0,
                excluded_cloud_pixel_count=run[13] or 0,
                excluded_shadow_pixel_count=run[14] or 0,
                raw_changed_pixel_count=run[15] or 0,
                filtered_changed_pixel_count=run[16] or 0,
                changed_pixel_percentage=float(run[17] or 0.0),
                largest_region_area_m2=float(run[18] or 0.0),
                mean_region_area_m2=float(run[19] or 0.0),
                median_region_area_m2=float(run[20] or 0.0),
                mean_change_signal=float(run[21] or 0.0),
                max_change_signal=float(run[22] or 0.0),
                regions=[RawChangeRegion(
                    region_id=r[0],
                    geometry=json.loads(r[1]),
                    pixel_count=r[2],
                    area_m2=r[3],
                    mean_change_signal=r[4],
                    max_change_signal=r[5],
                ) for r in selected],
            ))

        evidence_acquisitions = [_build_acquisition(by_id[aid], candidate_id) for aid in acquisition_ids]
        bounds = _metadata(by_id[acquisition_ids[0]][6]).bounds

        # 7. Spatial alignment verification (strictly grid/reference compatibility, NOT file existence)
        is_spatially_aligned = True
        primary_run = runs[0]
        before_meta = _metadata(by_id[primary_run[1]][6])
        after_meta = _metadata(by_id[primary_run[2]][6])
        alignment_details_str = "Fully aligned grid (identical CRS, transform, resolution, bounds, and dimensions)"
        if before_meta.crs != after_meta.crs or before_meta.resolution != after_meta.resolution:
            is_spatially_aligned = False
            alignment_details_str = f"Spatial reference mismatch: Before ({before_meta.crs}, {before_meta.resolution}) vs After ({after_meta.crs}, {after_meta.resolution})"
        elif (
            before_meta.transform != after_meta.transform
            or before_meta.width != after_meta.width
            or before_meta.height != after_meta.height
        ):
            is_spatially_aligned = False
            alignment_details_str = f"Grid geometry mismatch: Before ({before_meta.width}x{before_meta.height}) vs After ({after_meta.width}x{after_meta.height})"
        elif before_meta.bounds != after_meta.bounds:
            is_spatially_aligned = False
            alignment_details_str = f"Grid bounds mismatch: Before ({before_meta.bounds}) vs After ({after_meta.bounds})"

        alignment_details = {
            "status": "aligned" if is_spatially_aligned else "misaligned",
            "crs": before_meta.crs,
            "width": before_meta.width,
            "height": before_meta.height,
            "pixel_size": before_meta.resolution[0] if before_meta.resolution else None,
            "details": alignment_details_str,
        }

        # 8. Observation quality limitations & authoritative state semantics
        quality_limitation_reasons: list[str] = []
        for acq in evidence_acquisitions:
            # Observation lifecycle state is authoritative
            if acq.observation_state == "valid_unusable":
                quality_limitation_reasons.append(
                    f"Observation {acq.acquisition_id} ({acq.item_id}) is valid_unusable: usable pixel fraction is below the 0.50 threshold"
                    + (f" ({acq.quality_reason})" if acq.quality_reason else "")
                )
            elif acq.observation_state == "legacy_unassessed":
                quality_limitation_reasons.append(
                    f"Observation {acq.acquisition_id} ({acq.item_id}) is legacy_unassessed: ingested before Phase B quality assessment"
                )
            elif acq.observation_state == "failed":
                quality_limitation_reasons.append(
                    f"Observation {acq.acquisition_id} ({acq.item_id}) preprocessing failed: {acq.quality_reason or 'unspecified error'}"
                )
            elif acq.observation_state in ("discovered", "acquired"):
                quality_limitation_reasons.append(
                    f"Observation {acq.acquisition_id} ({acq.item_id}) has not completed preprocessing (state: {acq.observation_state})"
                )
            elif acq.observation_state == "usable":
                # Usable evidence with specific quality limitations
                if not acq.quality_mask_available:
                    quality_limitation_reasons.append(
                        f"Observation {acq.acquisition_id} quality mask is missing or unreadable on disk storage"
                    )
                if acq.quality_processing_version and acq.quality_processing_version != QUALITY_PROCESSING_VERSION:
                    quality_limitation_reasons.append(
                        f"Observation {acq.acquisition_id} uses non-standard quality processing version '{acq.quality_processing_version}'"
                    )
                if not acq.display_available:
                    quality_limitation_reasons.append(
                        f"Observation {acq.acquisition_id} display artifact is unavailable or uses incompatible visualization version"
                    )

        # Temporal quality support < 0.40 is a temporal quality limitation, NOT an observation lifecycle state
        temporal_qual = float(signal[13]) if len(signal) > 13 and signal[13] is not None else candidate.metrics.quality_support
        if temporal_qual < 0.40 and temporal_qual > 0.0:
            quality_limitation_reasons.append(
                f"Temporal quality limitation: temporal quality support is degraded ({temporal_qual:.2f} < 0.40); signal confidence is dampened"
            )

        is_quality_limited = len(quality_limitation_reasons) > 0

        # 9. Complete Provenance Chain
        provenance_chain = {
            "candidate_id": candidate.candidate_id,
            "analysis_id": candidate.analysis_id,
            "signal_id": candidate.signal_id,
            "aoi_id": primary_aoi_id,
            "detection_run_ids": run_ids,
            "acquisition_ids": acquisition_ids,
            "before_acquisition_id": primary_run[1],
            "after_acquisition_id": primary_run[2],
            "before_item_id": by_id[primary_run[1]][2],
            "after_item_id": by_id[primary_run[2]][2],
            "scientific_rasters": {aid: by_id[aid][5] for aid in acquisition_ids},
            "quality_masks": {aid: by_id[aid][11] for aid in acquisition_ids if by_id[aid][11]},
            "display_artifacts": {aid: by_id[aid][14] for aid in acquisition_ids if by_id[aid][14]},
            "processing_versions": {aid: by_id[aid][12] for aid in acquisition_ids if by_id[aid][12]},
            "masking_methods": {aid: by_id[aid][13] for aid in acquisition_ids if by_id[aid][13]},
            "visualization_versions": {aid: by_id[aid][16] for aid in acquisition_ids if by_id[aid][16]},
            "detector_version": primary_run[3],
        }

    return CandidateEvidenceResponse(
        candidate=candidate,
        temporal={
            "analysis_id": candidate.analysis_id,
            "signal_id": candidate.signal_id,
            "state": signal[9],
            "support_count": signal[0],
            "interval_count": signal[1],
            "persistence_ratio": signal[2],
            "recurrence_count": signal[3],
            "transient_interval_count": signal[4],
            "temporal_consistency": signal[5],
            "matched_region_coverage": signal[6],
            "first_change_datetime": signal[7],
            "last_supporting_datetime": signal[8],
            "quality_support": temporal_qual,
        },
        intervals=intervals,
        acquisitions=evidence_acquisitions,
        evidence_bounds=bounds,
        aoi_id=primary_aoi_id,
        is_spatially_aligned=is_spatially_aligned,
        spatial_alignment_details=alignment_details,
        is_quality_limited=is_quality_limited,
        quality_limitation_reasons=quality_limitation_reasons,
        provenance_chain=provenance_chain,
    )


def _png_gray(values: np.ndarray, valid: np.ndarray) -> bytes:
    pixels = np.full(values.shape, 235, dtype=np.uint8)
    if valid.any():
        pixels[valid] = ((np.clip(values[valid], -1.0, 1.0) + 1.0) * 127.5).astype(np.uint8)
    raw = b"".join(b"\x00" + row.tobytes() for row in pixels)

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", pixels.shape[1], pixels.shape[0], 8, 0, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def render_preview(candidate_id: str, acquisition_id: int) -> bytes:
    evidence = get_candidate_evidence(candidate_id)
    acquisition = next((item for item in evidence.acquisitions if item.acquisition_id == acquisition_id), None)
    if acquisition is None:
        raise EvidenceUnavailableError("The requested acquisition is not part of this candidate's evidence")
    with connection() as db:
        row = db.execute(
            "SELECT prepared_path, raster_metadata_json FROM imagery_acquisitions WHERE id = ?",
            (acquisition_id,),
        ).fetchone()
    if row is None or not Path(row[0]).is_file():
        raise EvidenceUnavailableError(f"Evidence raster for acquisition {acquisition_id} is unavailable")
    try:
        _metadata(row[1])
        with rasterio.open(row[0]) as dataset:
            candidate_geometry = shape(evidence.candidate.geometry)
            projected = transform_geom("EPSG:4326", dataset.crs, mapping(candidate_geometry), precision=10)
            candidate_surface = shape(projected)
            raster_surface = box(*dataset.bounds)
            intersection = candidate_surface.intersection(raster_surface)
            if intersection.is_empty or not intersection.is_valid:
                raise EvidenceUnavailableError("Candidate geometry does not intersect the acquisition raster")
            data, _ = mask(dataset, [mapping(intersection)], crop=True, filled=True, nodata=0)
            if data.size == 0 or data.shape[0] < 2:
                raise EvidenceRasterError(f"Evidence raster for acquisition {acquisition_id} could not be rendered")
            red, nir = data[0].astype(np.float32), data[1].astype(np.float32)
            denominator = red + nir
            valid = (data[0] != 0) & (data[1] != 0) & (denominator != 0)
            with np.errstate(divide="ignore", invalid="ignore"):
                ndvi = (nir - red) / denominator
            return _png_gray(ndvi, valid & np.isfinite(ndvi))
    except (rasterio.errors.RasterioIOError, ValueError, IndexError) as exc:
        raise EvidenceRasterError(f"Evidence raster for acquisition {acquisition_id} could not be rendered") from exc