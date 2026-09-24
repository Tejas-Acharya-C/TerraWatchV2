from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import rasterio
from fastapi.testclient import TestClient
from rasterio.transform import from_origin

from app import detection, temporal
from app.config import settings
from app.db import connection, initialize_database
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated_database(tmp_path: Path):
    original_database = settings.database_path
    object.__setattr__(settings, "database_path", tmp_path / "test.db")
    initialize_database()

    raster_paths = []
    display_paths = []
    quality_mask_paths = []
    for acq_id in (1, 2):
        r_path = tmp_path / f"raster_{acq_id}.tif"
        with rasterio.open(
            r_path, "w", driver="GTiff", width=4, height=4, count=2, dtype="uint16",
            crs="EPSG:4326", transform=from_origin(75.0, 13.5, 0.001, 0.001), nodata=0
        ) as ds:
            ds.write(np.full((4, 4), acq_id * 100, dtype="uint16"), 1)
            ds.write(np.full((4, 4), acq_id * 200, dtype="uint16"), 2)
        raster_paths.append(r_path)

        d_path = tmp_path / f"display_{acq_id}.tif"
        with rasterio.open(
            d_path, "w", driver="GTiff", width=4, height=4, count=3, dtype="uint8",
            crs="EPSG:4326", transform=from_origin(75.0, 13.5, 0.001, 0.001), nodata=0
        ) as ds:
            for b in range(1, 4):
                ds.write(np.full((4, 4), acq_id * 50 + b * 10, dtype="uint8"), b)
        display_paths.append(d_path)

        q_path = tmp_path / f"quality_{acq_id}.tif"
        with rasterio.open(
            q_path, "w", driver="GTiff", width=4, height=4, count=1, dtype="uint8",
            crs="EPSG:4326", transform=from_origin(75.0, 13.5, 0.001, 0.001), nodata=0
        ) as ds:
            ds.write(np.full((4, 4), 4, dtype="uint8"), 1)  # SCL class 4 = vegetation (valid)
        quality_mask_paths.append(q_path)

    with connection() as db:
        # Karnataka-contained AOI (near Chikmagalur/Shimoga)
        aoi_coords = [[[75.0, 13.0], [75.1, 13.0], [75.1, 13.5], [75.0, 13.5], [75.0, 13.0]]]
        db.execute(
            "INSERT INTO aoi (id, geometry_json, created_at, updated_at) VALUES (1, ?, '2024-01-01T00:00:00+00:00', '2024-01-01T00:00:00+00:00')",
            (json.dumps({"type": "Polygon", "coordinates": aoi_coords}),),
        )

        for acq_id, r_path, d_path, q_path in zip((1, 2), raster_paths, display_paths, quality_mask_paths):
            raster_meta = {
                "crs": "EPSG:4326",
                "transform": [0.001, 0, 75.0, 0, -0.001, 13.5],
                "width": 4,
                "height": 4,
                "resolution": [0.001, 0.001],
                "bounds": [75.0, 13.496, 75.004, 13.5],
                "count": 2,
                "dtype": "uint16",
                "nodata": 0,
            }
            source_meta = {"eo:cloud_cover": 2.5, "s2:mgrs_tile": "43PGP"}
            quality_metrics = {
                "valid_pixel_fraction": 0.95,
                "cloud_pixel_fraction": 0.02,
                "shadow_pixel_fraction": 0.01,
                "invalid_pixel_fraction": 0.02,
            }
            db.execute(
                """INSERT INTO imagery_acquisitions (
                    id, aoi_id, requested_start_datetime, requested_end_datetime,
                    item_id, collection, acquisition_datetime, assets_json, prepared_path,
                    raster_metadata_json, source_metadata_json, created_at,
                    observation_state, quality_reason, quality_metrics_json, quality_mask_path,
                    processing_version, masking_method, display_path, display_metadata_json,
                    visualization_version
                ) VALUES (?, 1, '2024-01-01T00:00:00+00:00', '2024-01-02T00:00:00+00:00',
                    ?, 'sentinel-2-l2a', ?, '[]', ?, ?, ?, '2024-01-01T00:00:00+00:00',
                    'usable', 'Optimal conditions', ?, ?, 'aoi-observation-quality-v1',
                    'sentinel-2-scl-nearest-v1', ?, '{}', 'sentinel-2-rgb-percentile-v2')""",
                (
                    acq_id,
                    f"S2_ITEM_{acq_id}",
                    f"2024-0{acq_id}-01T00:00:00+00:00",
                    str(r_path),
                    json.dumps(raster_meta),
                    json.dumps(source_meta),
                    json.dumps(quality_metrics),
                    str(q_path),
                    str(d_path),
                ),
            )

        db.execute(
            """INSERT INTO temporal_analyses (
                id, iou_threshold, detector_run_count, observation_count, temporal_span_days,
                state, created_at
            ) VALUES (1, 0.25, 1, 2, 30, 'persistent', '2024-02-01T00:00:00+00:00')"""
        )

        db.execute(
            """INSERT INTO detection_runs (
                id, before_acquisition_id, after_acquisition_id, detector_version, threshold,
                min_region_pixels, region_count, changed_pixel_count, total_changed_area_m2,
                created_at, quality_mask_used, quality_processing_version, quality_valid_pixel_count,
                excluded_pixel_count, excluded_cloud_pixel_count, excluded_shadow_pixel_count,
                raw_changed_pixel_count, filtered_changed_pixel_count, changed_pixel_percentage,
                largest_region_area_m2, mean_region_area_m2, median_region_area_m2,
                mean_change_signal, max_change_signal
            ) VALUES (1, 1, 2, 'ndvi-diff-v1', 0.20, 4, 1, 8, 800.0,
                '2024-02-01T00:00:00+00:00', 1, 'aoi-observation-quality-v1', 16,
                1, 1, 0, 10, 8, 50.0, 800.0, 800.0, 800.0, 0.45, 0.65)"""
        )

        cand_coords = [[[75.001, 13.497], [75.003, 13.497], [75.003, 13.499], [75.001, 13.499], [75.001, 13.497]]]
        cand_geom_json = json.dumps({"type": "Polygon", "coordinates": cand_coords})

        db.execute(
            """INSERT INTO raw_change_regions (
                id, run_id, geometry_json, pixel_count, area_m2, mean_change_signal, max_change_signal
            ) VALUES (1, 1, ?, 8, 800.0, 0.45, 0.65)""",
            (cand_geom_json,),
        )

        db.execute(
            """INSERT INTO temporal_signals (
                analysis_id, signal_id, geometry_json, support_count, interval_count,
                persistence_ratio, recurrence_count, transient_interval_count,
                temporal_consistency, matched_region_coverage, first_change_datetime,
                last_supporting_datetime, state, source_region_ids_json, detection_run_ids_json,
                acquisition_ids_json, quality_support
            ) VALUES (1, 1, ?, 1, 1, 1.0, 0, 0, 0.95, 0.80, '2024-01-01T00:00:00+00:00',
                '2024-02-01T00:00:00+00:00', 'persistent', '[1]', '[1]', '[1,2]', 0.90)""",
            (cand_geom_json,),
        )

        cand_metrics = json.dumps({
            "support_count": 1,
            "interval_count": 1,
            "persistence_ratio": 1.0,
            "recurrence_count": 0,
            "transient_interval_count": 0,
            "temporal_consistency": 0.95,
            "matched_region_coverage": 0.80,
            "first_change_datetime": "2024-01-01T00:00:00+00:00",
            "last_supporting_datetime": "2024-02-01T00:00:00+00:00",
            "temporal_state": "persistent",
            "score_components": {
                "temporal_persistence": 1.0,
                "temporal_consistency": 0.95,
                "change_magnitude": 0.42,
                "observation_quality": 0.90,
            },
            "quality_support": 0.90,
            "mean_change_signal": 0.45,
            "max_change_signal": 0.65,
            "total_area_m2": 800.0,
        })

        db.execute(
            """INSERT INTO candidates (
                candidate_id, analysis_id, signal_id, geometry_json, score, rank,
                severity, priority, review_state, source_signal_ids_json, detection_run_ids_json,
                acquisition_ids_json, metrics_json, created_at, updated_at
            ) VALUES ('analysis-1-signal-1', 1, 1, ?, 0.725, 1, 'medium', 'high', 'unreviewed',
                '[1]', '[1]', '[1,2]', ?, '2024-02-01T00:00:00+00:00', '2024-02-01T00:00:00+00:00')""",
            (cand_geom_json, cand_metrics),
        )
        db.commit()

    yield
    object.__setattr__(settings, "database_path", original_database)


# =========================================================================
# Acceptance Requirement 1: Valid candidate evidence resolves
# =========================================================================
def test_01_valid_candidate_evidence_resolves():
    response = client.get("/api/v1/candidates/analysis-1-signal-1/evidence")
    assert response.status_code == 200
    payload = response.json()
    assert payload["candidate"]["candidate_id"] == "analysis-1-signal-1"
    assert "temporal" in payload
    assert "intervals" in payload
    assert "acquisitions" in payload
    assert "evidence_bounds" in payload
    assert payload["aoi_id"] == 1


# =========================================================================
# Acceptance Requirement 2: Complete provenance chain
# =========================================================================
def test_02_complete_provenance_chain():
    response = client.get("/api/v1/candidates/analysis-1-signal-1/evidence")
    assert response.status_code == 200
    chain = response.json()["provenance_chain"]
    assert chain["candidate_id"] == "analysis-1-signal-1"
    assert chain["analysis_id"] == 1
    assert chain["signal_id"] == 1
    assert chain["aoi_id"] == 1
    assert chain["detection_run_ids"] == [1]
    assert chain["acquisition_ids"] == [1, 2]
    assert chain["before_acquisition_id"] == 1
    assert chain["after_acquisition_id"] == 2
    assert chain["before_item_id"] == "S2_ITEM_1"
    assert chain["after_item_id"] == "S2_ITEM_2"
    assert "1" in chain["scientific_rasters"]
    assert "2" in chain["scientific_rasters"]
    assert "1" in chain["quality_masks"]
    assert "2" in chain["quality_masks"]
    assert "1" in chain["display_artifacts"]
    assert "2" in chain["display_artifacts"]
    assert chain["detector_version"] == "ndvi-diff-v1"


# =========================================================================
# Acceptance Requirement 3: Before/After distinctness
# =========================================================================
def test_03_before_after_distinctness():
    import sqlite3
    response = client.get("/api/v1/candidates/analysis-1-signal-1/evidence")
    assert response.status_code == 200
    intervals = response.json()["intervals"]
    assert len(intervals) >= 1
    for interval in intervals:
        assert interval["before_acquisition_id"] != interval["after_acquisition_id"]

    # DB CHECK constraint verification
    with connection() as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE detection_runs SET after_acquisition_id = 1 WHERE id = 1")


# =========================================================================
# Acceptance Requirement 4: Display artifact provenance
# =========================================================================
def test_04_display_artifact_provenance():
    response = client.get("/api/v1/candidates/analysis-1-signal-1/evidence")
    assert response.status_code == 200
    payload = response.json()
    acqs = payload["acquisitions"]
    for acq in acqs:
        assert acq["display_available"] is True
        assert acq["visualization_version"] == "sentinel-2-rgb-percentile-v2"
        assert f"/api/v1/imagery/acquisitions/{acq['acquisition_id']}/display?aoi_id=1" == acq["display_url"]


# =========================================================================
# Acceptance Requirement 5: Quality-mask provenance
# =========================================================================
def test_05_quality_mask_provenance():
    response = client.get("/api/v1/candidates/analysis-1-signal-1/evidence")
    assert response.status_code == 200
    for acq in response.json()["acquisitions"]:
        assert acq["quality_mask_available"] is True
        assert acq["quality_processing_version"] == "aoi-observation-quality-v1"
        assert acq["masking_method"] == "sentinel-2-scl-nearest-v1"


# =========================================================================
# Acceptance Requirement 6: Missing display artifact
# =========================================================================
def test_06_missing_display_artifact():
    with connection() as db:
        db.execute("UPDATE imagery_acquisitions SET display_path = 'non_existent_display.tif' WHERE id = 1")
        db.commit()

    response = client.get("/api/v1/candidates/analysis-1-signal-1/evidence")
    assert response.status_code == 200
    payload = response.json()
    before_acq = next(a for a in payload["acquisitions"] if a["acquisition_id"] == 1)
    assert before_acq["display_available"] is False
    assert before_acq["display_url"] is None
    # Still resolves preview via radiometric bands
    preview = client.get("/api/v1/candidates/analysis-1-signal-1/evidence/acquisitions/1/preview")
    assert preview.status_code == 200


# =========================================================================
# Acceptance Requirement 7: Missing quality mask
# =========================================================================
def test_07_missing_quality_mask():
    with connection() as db:
        db.execute("UPDATE imagery_acquisitions SET quality_mask_path = 'missing_mask.tif' WHERE id = 1")
        db.commit()

    response = client.get("/api/v1/candidates/analysis-1-signal-1/evidence")
    assert response.status_code == 200
    payload = response.json()
    before_acq = next(a for a in payload["acquisitions"] if a["acquisition_id"] == 1)
    assert before_acq["quality_mask_available"] is False
    assert payload["is_quality_limited"] is True
    assert any("quality mask is missing" in r for r in payload["quality_limitation_reasons"])
    # Missing quality mask does NOT break spatial grid alignment
    assert payload["is_spatially_aligned"] is True


# =========================================================================
# Acceptance Requirement 8: Stale display artifact
# =========================================================================
def test_08_stale_display_artifact():
    with connection() as db:
        db.execute("UPDATE imagery_acquisitions SET visualization_version = 'sentinel-2-rgb-percentile-v1' WHERE id = 1")
        db.commit()

    response = client.get("/api/v1/candidates/analysis-1-signal-1/evidence")
    assert response.status_code == 200
    before_acq = next(a for a in response.json()["acquisitions"] if a["acquisition_id"] == 1)
    assert before_acq["display_available"] is False
    assert before_acq["display_url"] is None


# =========================================================================
# Acceptance Requirement 9: Stale quality mask
# =========================================================================
def test_09_stale_quality_mask():
    with connection() as db:
        db.execute("UPDATE imagery_acquisitions SET processing_version = 'legacy-unassessed-v0' WHERE id = 1")
        db.commit()

    response = client.get("/api/v1/candidates/analysis-1-signal-1/evidence")
    assert response.status_code == 200
    payload = response.json()
    assert payload["is_quality_limited"] is True
    assert any("non-standard quality processing version" in r for r in payload["quality_limitation_reasons"])


# =========================================================================
# Acceptance Requirement 10: Detection Before/After mismatch
# =========================================================================
def test_10_detection_before_after_mismatch(tmp_path: Path):
    r_path = tmp_path / "raster_3.tif"
    with rasterio.open(
        r_path, "w", driver="GTiff", width=4, height=4, count=2, dtype="uint16",
        crs="EPSG:4326", transform=from_origin(75.0, 13.5, 0.001, 0.001), nodata=0
    ) as ds:
        ds.write(np.full((4, 4), 300, dtype="uint16"), 1)
        ds.write(np.full((4, 4), 600, dtype="uint16"), 2)

    with connection() as db:
        db.execute(
            """INSERT INTO imagery_acquisitions (
                id, aoi_id, requested_start_datetime, requested_end_datetime,
                item_id, collection, acquisition_datetime, assets_json, prepared_path,
                raster_metadata_json, source_metadata_json, created_at, observation_state
            ) VALUES (3, 1, '2024-03-01T00:00:00+00:00', '2024-03-02T00:00:00+00:00',
                'S2_ITEM_3', 'sentinel-2-l2a', '2024-03-01T00:00:00+00:00', '[]', ?,
                '{}', '{}', '2024-03-01T00:00:00+00:00', 'usable')""",
            (str(r_path),),
        )
        # Point detection run to acquisition 3 which is outside candidate signal acquisition_ids [1, 2]
        db.execute("UPDATE detection_runs SET after_acquisition_id = 3 WHERE id = 1")
        db.commit()

    response = client.get("/api/v1/candidates/analysis-1-signal-1/evidence")
    assert response.status_code == 502
    assert response.json()["code"] == "CandidateEvidenceError"
    assert "outside the candidate signal" in response.json()["message"]


# =========================================================================
# Acceptance Requirement 11: Invalid candidate
# =========================================================================
def test_11_invalid_candidate():
    response = client.get("/api/v1/candidates/nonexistent-candidate-id/evidence")
    assert response.status_code == 404
    assert response.json()["code"] == "CandidateNotFoundError"


# =========================================================================
# Acceptance Requirement 12: Outside-Karnataka geometry
# =========================================================================
def test_12_outside_karnataka_geometry():
    with connection() as db:
        # Arabian Sea coordinates outside Karnataka
        outside_geom = {"type": "Polygon", "coordinates": [[[70.0, 13.0], [70.1, 13.0], [70.1, 13.1], [70.0, 13.1], [70.0, 13.0]]]}
        db.execute("UPDATE candidates SET geometry_json = ? WHERE candidate_id = 'analysis-1-signal-1'", (json.dumps(outside_geom),))
        db.commit()

    response = client.get("/api/v1/candidates/analysis-1-signal-1/evidence")
    assert response.status_code == 502
    assert response.json()["code"] == "CandidateEvidenceError"
    assert "Karnataka" in response.json()["message"]


# =========================================================================
# Acceptance Requirement 13: AOI provenance mismatch
# =========================================================================
def test_13_aoi_provenance_mismatch():
    with connection() as db:
        # Valid Karnataka coordinates (near Bangalore) that do not intersect AOI 1 (Shimoga)
        disjoint_geom = {"type": "Polygon", "coordinates": [[[77.5, 12.9], [77.6, 12.9], [77.6, 13.0], [77.5, 13.0], [77.5, 12.9]]]}
        db.execute("UPDATE candidates SET geometry_json = ? WHERE candidate_id = 'analysis-1-signal-1'", (json.dumps(disjoint_geom),))
        db.commit()

    response = client.get("/api/v1/candidates/analysis-1-signal-1/evidence")
    assert response.status_code == 502
    assert response.json()["code"] == "CandidateEvidenceError"
    assert "does not intersect" in response.json()["message"]


# =========================================================================
# Acceptance Requirement 14: Incompatible raster/grid metadata
# =========================================================================
def test_14_incompatible_raster_grid_metadata():
    with connection() as db:
        diff_meta = {
            "crs": "EPSG:4326",
            "transform": [0.002, 0, 75.0, 0, -0.002, 13.5],
            "width": 2,
            "height": 2,
            "resolution": [0.002, 0.002],
            "bounds": [75.0, 13.496, 75.004, 13.5],
            "count": 2,
            "dtype": "uint16",
            "nodata": 0,
        }
        db.execute("UPDATE imagery_acquisitions SET raster_metadata_json = ? WHERE id = 2", (json.dumps(diff_meta),))
        db.commit()

    response = client.get("/api/v1/candidates/analysis-1-signal-1/evidence")
    assert response.status_code == 200
    payload = response.json()
    assert payload["is_spatially_aligned"] is False
    assert payload["spatial_alignment_details"]["status"] == "misaligned"
    assert "Spatial reference mismatch" in payload["spatial_alignment_details"]["details"]


# =========================================================================
# Acceptance Requirement 15: Temporal evidence is persisted/read-only
# =========================================================================
def test_15_temporal_evidence_is_persisted_read_only():
    with connection() as db:
        before_signals = db.execute("SELECT * FROM temporal_signals").fetchall()
        before_analyses = db.execute("SELECT * FROM temporal_analyses").fetchall()

    response = client.get("/api/v1/candidates/analysis-1-signal-1/evidence")
    assert response.status_code == 200

    with connection() as db:
        after_signals = db.execute("SELECT * FROM temporal_signals").fetchall()
        after_analyses = db.execute("SELECT * FROM temporal_analyses").fetchall()

    assert before_signals == after_signals
    assert before_analyses == after_analyses


# =========================================================================
# Acceptance Requirement 16: Detection metrics are persisted/read-only
# =========================================================================
def test_16_detection_metrics_are_persisted_read_only():
    with connection() as db:
        before_runs = db.execute("SELECT * FROM detection_runs").fetchall()
        before_regions = db.execute("SELECT * FROM raw_change_regions").fetchall()

    response = client.get("/api/v1/candidates/analysis-1-signal-1/evidence")
    assert response.status_code == 200
    interval = response.json()["intervals"][0]
    assert interval["raw_changed_pixel_count"] == 10
    assert interval["filtered_changed_pixel_count"] == 8
    assert interval["changed_pixel_percentage"] == 50.0
    assert interval["total_changed_area_m2"] == 800.0

    with connection() as db:
        after_runs = db.execute("SELECT * FROM detection_runs").fetchall()
        after_regions = db.execute("SELECT * FROM raw_change_regions").fetchall()

    assert before_runs == after_runs
    assert before_regions == after_regions


# =========================================================================
# Acceptance Requirement 17: No detection recomputation
# =========================================================================
def test_17_no_detection_recomputation(monkeypatch: pytest.MonkeyPatch):
    def fake_detect(*args, **kwargs):
        raise AssertionError("detect must NOT be called during evidence inspection")

    monkeypatch.setattr(detection, "detect", fake_detect)
    response = client.get("/api/v1/candidates/analysis-1-signal-1/evidence")
    assert response.status_code == 200


# =========================================================================
# Acceptance Requirement 18: No temporal recomputation
# =========================================================================
def test_18_no_temporal_recomputation(monkeypatch: pytest.MonkeyPatch):
    def fake_analyze(*args, **kwargs):
        raise AssertionError("analyze must NOT be called during evidence inspection")

    monkeypatch.setattr(temporal, "analyze", fake_analyze)
    response = client.get("/api/v1/candidates/analysis-1-signal-1/evidence")
    assert response.status_code == 200


# =========================================================================
# Acceptance Requirement 19: No fallback acquisition
# =========================================================================
def test_19_no_fallback_acquisition():
    with connection() as db:
        db.execute("UPDATE imagery_acquisitions SET prepared_path = 'missing_primary_raster.tif' WHERE id = 1")
        db.commit()

    response = client.get("/api/v1/candidates/analysis-1-signal-1/evidence")
    assert response.status_code == 404
    assert response.json()["code"] == "EvidenceUnavailableError"
    assert "missing from disk" in response.json()["message"]


# =========================================================================
# Acceptance Requirement 20: Legacy/unassessed evidence not silently reinterpreted
# =========================================================================
def test_20_legacy_unassessed_evidence_not_silently_reinterpreted():
    with connection() as db:
        db.execute(
            """UPDATE imagery_acquisitions SET observation_state = 'legacy_unassessed',
               quality_reason = 'quality_not_assessed', masking_method = 'not_assessed'
               WHERE id = 1"""
        )
        db.commit()

    response = client.get("/api/v1/candidates/analysis-1-signal-1/evidence")
    assert response.status_code == 200
    payload = response.json()
    before_acq = next(a for a in payload["acquisitions"] if a["acquisition_id"] == 1)
    assert before_acq["observation_state"] == "legacy_unassessed"
    assert before_acq["masking_method"] == "not_assessed"
    assert payload["is_quality_limited"] is True
    assert any("legacy_unassessed" in r for r in payload["quality_limitation_reasons"])


# =========================================================================
# Acceptance Requirement 21: Deterministic identical response
# =========================================================================
def test_21_deterministic_identical_response():
    first = client.get("/api/v1/candidates/analysis-1-signal-1/evidence").json()
    second = client.get("/api/v1/candidates/analysis-1-signal-1/evidence").json()
    assert first == second


# =========================================================================
# Acceptance Requirement 22: Unavailable evidence distinct from zero/no-change
# =========================================================================
def test_22_unavailable_evidence_distinct_from_zero_no_change(tmp_path: Path):
    # Proves missing raster is explicitly EvidenceUnavailableError (404)
    with connection() as db:
        db.execute("UPDATE imagery_acquisitions SET prepared_path = 'missing_file.tif' WHERE id = 2")
        db.commit()

    unavail_resp = client.get("/api/v1/candidates/analysis-1-signal-1/evidence")
    assert unavail_resp.status_code == 404
    assert unavail_resp.json()["code"] == "EvidenceUnavailableError"

    # Restore raster and verify zero-change run returns 200 with 0 changed pixels, NOT 404
    r_path = tmp_path / "raster_2.tif"
    with connection() as db:
        db.execute("UPDATE imagery_acquisitions SET prepared_path = ? WHERE id = 2", (str(r_path),))
        db.execute("UPDATE detection_runs SET raw_changed_pixel_count = 0, filtered_changed_pixel_count = 0, changed_pixel_count = 0 WHERE id = 1")
        db.commit()

    zero_resp = client.get("/api/v1/candidates/analysis-1-signal-1/evidence")
    assert zero_resp.status_code == 200
    assert zero_resp.json()["intervals"][0]["raw_changed_pixel_count"] == 0


# =========================================================================
# Acceptance Requirement 23: Correct quality-limited semantics
# =========================================================================
def test_23_correct_quality_limited_semantics():
    with connection() as db:
        q_metrics = {
            "valid_pixel_fraction": 0.40,
            "cloud_pixel_fraction": 0.35,
            "shadow_pixel_fraction": 0.10,
            "invalid_pixel_fraction": 0.15,
        }
        db.execute(
            """UPDATE imagery_acquisitions SET observation_state = 'valid_unusable',
               quality_reason = 'Usable pixels below 0.50 threshold',
               quality_metrics_json = ? WHERE id = 1""",
            (json.dumps(q_metrics),),
        )
        # Also set temporal quality support < 0.40
        db.execute("UPDATE temporal_signals SET quality_support = 0.30 WHERE analysis_id = 1")
        db.commit()

    response = client.get("/api/v1/candidates/analysis-1-signal-1/evidence")
    assert response.status_code == 200
    payload = response.json()
    assert payload["is_quality_limited"] is True
    reasons = payload["quality_limitation_reasons"]
    # 1. Authoritative observation state: valid_unusable
    assert any("valid_unusable" in r and "0.50" in r for r in reasons)
    # 2. Temporal quality limitation is explicitly labeled as temporal, not an observation state
    assert any("Temporal quality limitation" in r and "0.30" in r for r in reasons)


# =========================================================================
# Acceptance Requirement 24: Missing artifact states distinct from alignment failure
# =========================================================================
def test_24_missing_artifact_states_distinct_from_alignment_failure():
    # Make display artifact and quality mask missing
    with connection() as db:
        db.execute("UPDATE imagery_acquisitions SET display_path = 'missing_display.tif', quality_mask_path = 'missing_mask.tif' WHERE id = 1")
        db.commit()

    response = client.get("/api/v1/candidates/analysis-1-signal-1/evidence")
    assert response.status_code == 200
    payload = response.json()

    # Grid compatibility is preserved: is_spatially_aligned remains True!
    assert payload["is_spatially_aligned"] is True
    assert payload["spatial_alignment_details"]["status"] == "aligned"

    # Artifact states reflect individual unavailability
    before_acq = next(a for a in payload["acquisitions"] if a["acquisition_id"] == 1)
    assert before_acq["display_available"] is False
    assert before_acq["quality_mask_available"] is False
    assert payload["is_quality_limited"] is True
