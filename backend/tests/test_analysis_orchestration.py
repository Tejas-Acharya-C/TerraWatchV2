from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from shapely.geometry import box, mapping

from app.config import settings
from app.db import connection, initialize_database
from app.exceptions import DetectionRasterError
from app.main import app
from app.schemas import DetectionResponse, RawChangeRegion

client = TestClient(app)

KARNATAKA_POLYGON = {
    "type": "Polygon",
    "coordinates": [[[75.0, 13.0], [75.2, 13.0], [75.2, 13.2], [75.0, 13.2], [75.0, 13.0]]],
}


@pytest.fixture(autouse=True)
def isolated_database(tmp_path: Path):
    original_database = settings.database_path
    object.__setattr__(settings, "database_path", tmp_path / "test_orchestration.db")
    initialize_database()
    yield
    object.__setattr__(settings, "database_path", original_database)


def persist_aoi(aoi_id: int = 1, polygon: dict | None = None) -> None:
    poly = polygon or KARNATAKA_POLYGON
    with connection() as db:
        db.execute(
            "INSERT OR REPLACE INTO aoi (id, geometry_json, created_at, updated_at) VALUES (?, ?, '2024-01-01', '2024-01-01')",
            (aoi_id, json.dumps(poly)),
        )
        db.commit()


def persist_acquisition(
    acq_id: int,
    aoi_id: int,
    dt_str: str,
    *,
    state: str = "usable",
    usable_pct: float = 85.0,
) -> None:
    with connection() as db:
        db.execute(
            """INSERT INTO imagery_acquisitions (
                id, aoi_id, requested_start_datetime, requested_end_datetime,
                item_id, collection, acquisition_datetime, assets_json,
                prepared_path, raster_metadata_json, source_metadata_json, created_at,
                observation_state, quality_reason, quality_metrics_json,
                processing_version, masking_method, quality_asset_id
            ) VALUES (?, ?, ?, ?, ?, 'sentinel-2-l2a', ?, '[]', 'test.tif', '{}', '{}', ?, ?, 'quality_policy_passed', ?, 'aoi-observation-quality-v1', 'sentinel-2-scl-nearest-v1', 'scl')""",
            (
                acq_id,
                aoi_id,
                dt_str,
                dt_str,
                f"S2_{acq_id}",
                dt_str,
                dt_str,
                state,
                json.dumps(
                    {
                        "usable_percentage": usable_pct,
                        "cloud_percentage": 5,
                        "shadow_percentage": 5,
                        "invalid_percentage": 5,
                    }
                ),
            ),
        )
        db.commit()


def persist_run(
    before_id: int,
    after_id: int,
    geometries: list,
    pixels: int = 10,
    *,
    detector_version: str = "ndvi-absolute-difference-v1",
    threshold: float = 0.20,
    min_region_pixels: int = 4,
    quality_mask_used: int = 1,
    quality_processing_version: str = "aoi-observation-quality-v1",
) -> int:
    with connection() as db:
        cursor = db.execute(
            """INSERT INTO detection_runs (
                before_acquisition_id, after_acquisition_id, detector_version,
                threshold, min_region_pixels, region_count, changed_pixel_count,
                total_changed_area_m2, created_at, quality_mask_used,
                quality_processing_version, mean_change_signal, max_change_signal
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0.4, 0.5)""",
            (
                before_id,
                after_id,
                detector_version,
                threshold,
                min_region_pixels,
                len(geometries),
                pixels * len(geometries),
                100.0 * len(geometries),
                datetime.now().isoformat(),
                quality_mask_used,
                quality_processing_version,
            ),
        )
        run_id = cursor.lastrowid
        for geometry in geometries:
            db.execute(
                """INSERT INTO raw_change_regions (
                    run_id, geometry_json, pixel_count, area_m2,
                    mean_change_signal, max_change_signal
                ) VALUES (?, ?, ?, ?, 0.4, 0.5)""",
                (run_id, json.dumps(mapping(geometry)), pixels, float(geometry.area)),
            )
        db.commit()
    return run_id


def fake_detect(request):
    """Mock detection function that persists a dummy run when detection is invoked."""
    return DetectionResponse(
        run_id=persist_run(request.before_acquisition_id, request.after_acquisition_id, [box(0, 0, 10, 10)], pixels=10),
        before_acquisition_id=request.before_acquisition_id,
        after_acquisition_id=request.after_acquisition_id,
        before_item_id=f"S2_{request.before_acquisition_id}",
        after_item_id=f"S2_{request.after_acquisition_id}",
        detector_version="ndvi-absolute-difference-v1",
        threshold=request.threshold,
        min_region_pixels=request.min_region_pixels,
        created_at=datetime.now(),
        region_count=1,
        changed_pixel_count=10,
        total_changed_area_m2=100.0,
        regions=[
            RawChangeRegion(
                region_id=1,
                geometry=mapping(box(0, 0, 10, 10)),
                pixel_count=10,
                area_m2=100.0,
                mean_change_signal=0.4,
                max_change_signal=0.5,
            )
        ],
        quality_mask_used=True,
        quality_processing_version="aoi-observation-quality-v1",
        quality_valid_pixel_count=100,
        excluded_pixel_count=0,
        excluded_cloud_pixel_count=0,
        excluded_shadow_pixel_count=0,
        raw_changed_pixel_count=10,
        filtered_changed_pixel_count=10,
        changed_pixel_percentage=10.0,
        largest_region_area_m2=100.0,
        mean_region_area_m2=100.0,
        median_region_area_m2=100.0,
        mean_change_signal=0.4,
        max_change_signal=0.5,
    )


# 1. Multiple eligible observations automatically produce adjacent pairs.
def test_1_multiple_eligible_observations_produce_adjacent_pairs():
    persist_aoi(1)
    persist_acquisition(1, 1, "2024-01-01T00:00:00+00:00")
    persist_acquisition(2, 1, "2024-02-01T00:00:00+00:00")
    persist_acquisition(3, 1, "2024-03-01T00:00:00+00:00")

    with patch("app.temporal.detect", side_effect=fake_detect):
        response = client.post("/api/v1/orchestrations/analyze", json={"aoi_id": 1})

    assert response.status_code == 200, response.json()
    payload = response.json()
    relationships = payload["analysis"]["relationships"]
    assert len(relationships) == 2
    assert relationships[0]["before_acquisition_id"] == 1
    assert relationships[0]["after_acquisition_id"] == 2
    assert relationships[1]["before_acquisition_id"] == 2
    assert relationships[1]["after_acquisition_id"] == 3


# 2. No manual acquisition IDs are required.
def test_2_no_manual_acquisition_ids_required():
    persist_aoi(1)
    persist_acquisition(1, 1, "2024-01-01T00:00:00+00:00")
    persist_acquisition(2, 1, "2024-02-01T00:00:00+00:00")

    with patch("app.temporal.detect", side_effect=fake_detect):
        response = client.post("/api/v1/orchestrations/analyze", json={"aoi_id": 1})

    assert response.status_code == 200
    assert response.json()["eligible_observation_ids"] == [1, 2]


# 3. Existing compatible detection is reused.
def test_3_existing_compatible_detection_reused():
    persist_aoi(1)
    persist_acquisition(1, 1, "2024-01-01T00:00:00+00:00")
    persist_acquisition(2, 1, "2024-02-01T00:00:00+00:00")
    persist_acquisition(3, 1, "2024-03-01T00:00:00+00:00")

    run_1_2 = persist_run(1, 2, [box(0, 0, 10, 10)])

    with patch("app.temporal.detect", side_effect=fake_detect) as mock_detect:
        response = client.post("/api/v1/orchestrations/analyze", json={"aoi_id": 1})
        assert response.status_code == 200
        payload = response.json()
        assert payload["reused_detection_count"] == 1
        assert payload["generated_detection_count"] == 1
        # mock_detect was only called for (2, 3), not for (1, 2)
        assert mock_detect.call_count == 1
        assert mock_detect.call_args[0][0].before_acquisition_id == 2
        assert mock_detect.call_args[0][0].after_acquisition_id == 3


# 4. Missing detection is automatically generated.
def test_4_missing_detection_auto_generated():
    persist_aoi(1)
    persist_acquisition(1, 1, "2024-01-01T00:00:00+00:00")
    persist_acquisition(2, 1, "2024-02-01T00:00:00+00:00")

    with patch("app.temporal.detect", side_effect=fake_detect) as mock_detect:
        response = client.post("/api/v1/orchestrations/analyze", json={"aoi_id": 1})
        assert response.status_code == 200
        assert mock_detect.call_count == 1
        assert response.json()["generated_detection_count"] == 1


# 5. Running orchestration twice does not duplicate detection or results.
def test_5_repeated_orchestration_is_idempotent():
    persist_aoi(1)
    persist_acquisition(1, 1, "2024-01-01T00:00:00+00:00")
    persist_acquisition(2, 1, "2024-02-01T00:00:00+00:00")

    with patch("app.temporal.detect", side_effect=fake_detect):
        resp1 = client.post("/api/v1/orchestrations/analyze", json={"aoi_id": 1})
        assert resp1.status_code == 200

        with connection() as db:
            runs_count_1 = db.execute("SELECT COUNT(*) FROM detection_runs").fetchone()[0]
            analyses_count_1 = db.execute("SELECT COUNT(*) FROM temporal_analyses").fetchone()[0]
            candidates_count_1 = db.execute("SELECT COUNT(*) FROM candidates").fetchone()[0]

        resp2 = client.post("/api/v1/orchestrations/analyze", json={"aoi_id": 1})
        assert resp2.status_code == 200
        assert resp2.json()["reused_analysis"] is True
        assert resp2.json()["analysis"]["analysis_id"] == resp1.json()["analysis"]["analysis_id"]

        with connection() as db:
            runs_count_2 = db.execute("SELECT COUNT(*) FROM detection_runs").fetchone()[0]
            analyses_count_2 = db.execute("SELECT COUNT(*) FROM temporal_analyses").fetchone()[0]
            candidates_count_2 = db.execute("SELECT COUNT(*) FROM candidates").fetchone()[0]

        assert runs_count_2 == runs_count_1
        assert analyses_count_2 == analyses_count_1
        assert candidates_count_2 == candidates_count_1


# 6. Chronological ordering is deterministic.
def test_6_chronological_ordering_is_deterministic():
    persist_aoi(1)
    # Insert in reverse order
    persist_acquisition(3, 1, "2024-03-01T00:00:00+00:00")
    persist_acquisition(1, 1, "2024-01-01T00:00:00+00:00")
    persist_acquisition(2, 1, "2024-02-01T00:00:00+00:00")

    with patch("app.temporal.detect", side_effect=fake_detect):
        response = client.post("/api/v1/orchestrations/analyze", json={"aoi_id": 1})

    assert response.status_code == 200
    assert response.json()["eligible_observation_ids"] == [1, 2, 3]


# 7. Unusable observations are excluded.
def test_7_unusable_observations_are_excluded():
    persist_aoi(1)
    persist_acquisition(1, 1, "2024-01-01T00:00:00+00:00", state="usable", usable_pct=80.0)
    persist_acquisition(2, 1, "2024-02-01T00:00:00+00:00", state="valid_unusable", usable_pct=20.0)
    persist_acquisition(3, 1, "2024-03-01T00:00:00+00:00", state="failed", usable_pct=0.0)
    persist_acquisition(4, 1, "2024-04-01T00:00:00+00:00", state="usable", usable_pct=0.0)  # zero usable pct
    persist_acquisition(5, 1, "2024-05-01T00:00:00+00:00", state="usable", usable_pct=90.0)

    with patch("app.temporal.detect", side_effect=fake_detect):
        response = client.post("/api/v1/orchestrations/analyze", json={"aoi_id": 1})

    assert response.status_code == 200
    assert response.json()["eligible_observation_ids"] == [1, 5]


# 8. Insufficient eligible observations (<2) produces insufficient-history behavior.
def test_8_insufficient_eligible_observations_cleanly_fails():
    persist_aoi(1)
    # 0 observations
    response0 = client.post("/api/v1/orchestrations/analyze", json={"aoi_id": 1})
    assert response0.status_code == 404
    assert response0.json()["code"] == "NoTemporalObservationsError"

    # 1 observation
    persist_acquisition(1, 1, "2024-01-01T00:00:00+00:00")
    response1 = client.post("/api/v1/orchestrations/analyze", json={"aoi_id": 1})
    assert response1.status_code == 422
    assert response1.json()["code"] == "InsufficientTemporalHistoryError"


# 9. Detection failure is surfaced explicitly.
def test_9_detection_failure_surfaced_explicitly():
    persist_aoi(1)
    persist_acquisition(1, 1, "2024-01-01T00:00:00+00:00")
    persist_acquisition(2, 1, "2024-02-01T00:00:00+00:00")

    with patch("app.temporal.detect", side_effect=DetectionRasterError("Raster corrupted")):
        response = client.post("/api/v1/orchestrations/analyze", json={"aoi_id": 1})

    assert response.status_code == 422
    assert response.json()["code"] == "DetectionRasterError"
    assert "Raster corrupted" in response.json()["message"]


# 10. Cross-AOI detection cannot be reused.
def test_10_cross_aoi_detection_cannot_be_reused():
    persist_aoi(1)
    persist_aoi(2)
    # Acquisitions 1 and 2 belong to AOI 2
    persist_acquisition(1, 2, "2024-01-01T00:00:00+00:00")
    persist_acquisition(2, 2, "2024-02-01T00:00:00+00:00")
    persist_run(1, 2, [box(0, 0, 10, 10)])

    # Now for AOI 1, acquisitions 3 and 4
    persist_acquisition(3, 1, "2024-01-01T00:00:00+00:00")
    persist_acquisition(4, 1, "2024-02-01T00:00:00+00:00")

    with patch("app.temporal.detect", side_effect=fake_detect) as mock_detect:
        response = client.post("/api/v1/orchestrations/analyze", json={"aoi_id": 1})
        assert response.status_code == 200
        # AOI 2's detection was NOT reused; detection was called for 3 and 4
        assert mock_detect.call_count == 1
        assert mock_detect.call_args[0][0].before_acquisition_id == 3
        assert mock_detect.call_args[0][0].after_acquisition_id == 4


# 11. Incompatible detection (e.g. legacy-unassessed) is regenerated.
def test_11_incompatible_detection_is_regenerated():
    persist_aoi(1)
    persist_acquisition(1, 1, "2024-01-01T00:00:00+00:00")
    persist_acquisition(2, 1, "2024-02-01T00:00:00+00:00")

    # Persist an incompatible legacy run (quality_processing_version = 'legacy-unassessed')
    persist_run(1, 2, [box(0, 0, 10, 10)], quality_processing_version="legacy-unassessed")

    with patch("app.temporal.detect", side_effect=fake_detect) as mock_detect:
        response = client.post("/api/v1/orchestrations/analyze", json={"aoi_id": 1})
        assert response.status_code == 200
        # Incompatible run was NOT reused; new detection was generated
        assert response.json()["generated_detection_count"] == 1
        assert mock_detect.call_count == 1


# 12. Temporal analysis receives the automatically constructed chronological sequence.
def test_12_temporal_analysis_receives_chronological_sequence():
    persist_aoi(1)
    persist_acquisition(1, 1, "2024-01-15T00:00:00+00:00")
    persist_acquisition(2, 1, "2024-02-15T00:00:00+00:00")
    persist_acquisition(3, 1, "2024-03-15T00:00:00+00:00")

    with patch("app.temporal.detect", side_effect=fake_detect):
        response = client.post("/api/v1/orchestrations/analyze", json={"aoi_id": 1})

    assert response.status_code == 200
    obs = response.json()["analysis"]["observations"]
    assert len(obs) == 3
    assert obs[0]["acquisition_id"] == 1
    assert obs[1]["acquisition_id"] == 2
    assert obs[2]["acquisition_id"] == 3
    assert obs[0]["sequence_index"] == 0
    assert obs[1]["sequence_index"] == 1
    assert obs[2]["sequence_index"] == 2


# 13. Existing candidate generation still works automatically.
def test_13_existing_candidate_generation_succeeds_automatically():
    persist_aoi(1)
    persist_acquisition(1, 1, "2024-01-01T00:00:00+00:00")
    persist_acquisition(2, 1, "2024-02-01T00:00:00+00:00")
    persist_acquisition(3, 1, "2024-03-01T00:00:00+00:00")

    # Persist runs with overlapping persistent signal
    region = box(0, 0, 10, 10)
    persist_run(1, 2, [region])
    persist_run(2, 3, [region])

    response = client.post("/api/v1/orchestrations/analyze", json={"aoi_id": 1})
    assert response.status_code == 200
    candidates = response.json()["candidates"]["candidates"]
    assert len(candidates) > 0
    assert candidates[0]["priority"] in ("urgent", "high", "normal", "low")
    assert candidates[0]["rank"] == 1


# 14. Existing candidate IDs and scoring remain unchanged.
def test_14_candidate_scores_and_ids_remain_identical():
    persist_aoi(1)
    persist_acquisition(1, 1, "2024-01-01T00:00:00+00:00")
    persist_acquisition(2, 1, "2024-02-01T00:00:00+00:00")
    persist_acquisition(3, 1, "2024-03-01T00:00:00+00:00")

    region = box(0, 0, 10, 10)
    persist_run(1, 2, [region])
    persist_run(2, 3, [region])

    # Run orchestration
    orch_resp = client.post("/api/v1/orchestrations/analyze", json={"aoi_id": 1}).json()
    orch_candidates = orch_resp["candidates"]["candidates"]

    # Compare with direct manual triage on the resulting analysis
    analysis_id = orch_resp["analysis"]["analysis_id"]
    manual_triage = client.post("/api/v1/candidates/triage", json={"analysis_id": analysis_id}).json()

    assert len(orch_candidates) == len(manual_triage["candidates"])
    for o, m in zip(orch_candidates, manual_triage["candidates"]):
        assert o["candidate_id"] == m["candidate_id"]
        assert o["score"] == m["score"]
        assert o["rank"] == m["rank"]
        assert o["priority"] == m["priority"]


# 15. Fresh database / state handling.
def test_15_fresh_database_state_handling():
    # Calling with nonexistent AOI in fresh DB
    response = client.post("/api/v1/orchestrations/analyze", json={"aoi_id": 999})
    assert response.status_code == 404
    assert response.json()["code"] == "NoAOIError"


# 16. Existing manual scientific APIs remain backward-compatible.
def test_16_manual_scientific_endpoints_remain_backward_compatible():
    persist_aoi(1)
    persist_acquisition(1, 1, "2024-01-01T00:00:00+00:00")
    persist_acquisition(2, 1, "2024-02-01T00:00:00+00:00")

    # Direct manual temporal analysis without detections raises MissingDetectionError
    manual_resp = client.post("/api/v1/temporal-analyses", json={"acquisition_ids": [1, 2]})
    assert manual_resp.status_code == 404
    assert manual_resp.json()["code"] == "MissingDetectionError"


# Bonus: NDJSON Streaming support
def test_streaming_ndjson_events():
    persist_aoi(1)
    persist_acquisition(1, 1, "2024-01-01T00:00:00+00:00")
    persist_acquisition(2, 1, "2024-02-01T00:00:00+00:00")

    with patch("app.temporal.detect", side_effect=fake_detect):
        response = client.post(
            "/api/v1/orchestrations/analyze?stream=true",
            json={"aoi_id": 1},
            headers={"Accept": "application/x-ndjson"},
        )

    assert response.status_code == 200
    lines = [json.loads(line) for line in response.text.strip().split("\n") if line.strip()]
    phases = [evt.get("phase") for evt in lines]
    assert "preparing_observations" in phases
    assert "preparing_comparisons" in phases
    assert "detecting_change" in phases
    assert "analyzing_history" in phases
    assert "preparing_candidates" in phases
    assert "complete" in phases
