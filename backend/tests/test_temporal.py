from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from shapely.geometry import box, mapping

from app.config import settings
from app.db import connection, initialize_database
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated_database(tmp_path: Path):
    original_database = settings.database_path
    object.__setattr__(settings, "database_path", tmp_path / "test.db")
    initialize_database()
    yield
    object.__setattr__(settings, "database_path", original_database)


def persist_acquisitions(
    times: list[str],
    *,
    aoi_ids: list[int] | None = None,
    quality_states: list[str] | None = None,
    usable_percentages: list[float] | None = None,
) -> None:
    with connection() as db_connection:
        db_connection.execute(
            "INSERT OR IGNORE INTO aoi (id, geometry_json, created_at, updated_at) VALUES (1, '{}', ?, ?)",
            (times[0], times[0]),
        )
        if aoi_ids and 2 in aoi_ids:
            db_connection.execute(
                "INSERT OR IGNORE INTO aoi (id, geometry_json, created_at, updated_at) VALUES (2, '{}', ?, ?)",
                (times[0], times[0]),
            )
        for acquisition_id, when in enumerate(times, start=1):
            aoi_id = aoi_ids[acquisition_id - 1] if aoi_ids else 1
            quality_state = quality_states[acquisition_id - 1] if quality_states else "usable"
            usable_percentage = usable_percentages[acquisition_id - 1] if usable_percentages else 80
            db_connection.execute(
                """INSERT INTO imagery_acquisitions (
                    id, aoi_id, requested_start_datetime, requested_end_datetime,
                    item_id, collection, acquisition_datetime, assets_json,
                    prepared_path, raster_metadata_json, source_metadata_json, created_at,
                    observation_state, quality_reason, quality_metrics_json,
                    processing_version, masking_method, quality_asset_id
                ) VALUES (?, ?, ?, ?, ?, 'sentinel-2-l2a', ?, '[]', 'unused.tif', '{}', '{}', ?, ?, 'quality_policy_passed', ?, 'aoi-observation-quality-v1', 'sentinel-2-scl-nearest-v1', 'scl')""",
                (acquisition_id, aoi_id, when, when, f"S2_{acquisition_id}", when, when,
                 quality_state,
                 json.dumps({"usable_percentage": usable_percentage, "cloud_percentage": 10, "shadow_percentage": 5, "invalid_percentage": 5})),
            )
        db_connection.commit()


def persist_run(before_id: int, after_id: int, geometries: list, pixels: int = 10) -> None:
    with connection() as db_connection:
        cursor = db_connection.execute(
            """INSERT INTO detection_runs (
                before_acquisition_id, after_acquisition_id, detector_version,
                threshold, min_region_pixels, region_count, changed_pixel_count,
                total_changed_area_m2, created_at, quality_mask_used,
                quality_processing_version, mean_change_signal, max_change_signal
            ) VALUES (?, ?, 'test-detector', 0.2, 1, ?, ?, ?, ?, 1, 'aoi-observation-quality-v1', 0.4, 0.5)""",
            (before_id, after_id, len(geometries), pixels * len(geometries), 10.0 * len(geometries), datetime.now().isoformat()),
        )
        run_id = cursor.lastrowid
        for geometry in geometries:
            db_connection.execute(
                """INSERT INTO raw_change_regions (
                    run_id, geometry_json, pixel_count, area_m2,
                    mean_change_signal, max_change_signal
                ) VALUES (?, ?, ?, ?, 0.4, 0.5)""",
                (run_id, json.dumps(mapping(geometry)), pixels, float(geometry.area)),
            )
        db_connection.commit()


def test_persistent_signal_matches_over_adjacent_intervals_and_persists():
    persist_acquisitions([
        "2024-01-01T00:00:00+00:00",
        "2024-02-01T00:00:00+00:00",
        "2024-03-01T00:00:00+00:00",
    ])
    region = box(0, 0, 10, 10)
    persist_run(1, 2, [region])
    persist_run(2, 3, [region])

    response = client.post("/api/v1/temporal-analyses", json={"acquisition_ids": [1, 2, 3]})

    assert response.status_code == 200, response.json()
    payload = response.json()
    assert payload["state"] == "persistent"
    assert payload["relationships"][0]["detection_run_id"] == 1
    assert payload["signals"][0]["support_count"] == 2
    assert payload["signals"][0]["persistence_ratio"] == pytest.approx(1.0)
    with connection() as db_connection:
        assert db_connection.execute("SELECT COUNT(*) FROM temporal_analyses").fetchone()[0] == 1
        assert db_connection.execute("SELECT COUNT(*) FROM temporal_signals").fetchone()[0] == 1
        relationship = db_connection.execute(
            "SELECT before_region_ids_json, after_region_ids_json, matched_region_pairs_json FROM temporal_relationships ORDER BY before_acquisition_id LIMIT 1"
        ).fetchone()
        assert json.loads(relationship[0]) == [1]
        assert json.loads(relationship[1]) == []
        assert json.loads(relationship[2]) == []
        relationship = db_connection.execute(
            "SELECT before_region_ids_json, after_region_ids_json, matched_region_pairs_json FROM temporal_relationships ORDER BY before_acquisition_id DESC LIMIT 1"
        ).fetchone()
        assert json.loads(relationship[0]) == [2]
        assert json.loads(relationship[1]) == [2]
        assert json.loads(relationship[2]) == [[1, 2]]
        signal = db_connection.execute("SELECT source_region_ids_json, detection_run_ids_json, acquisition_ids_json FROM temporal_signals").fetchone()
        assert json.loads(signal[0]) == [1, 2]
        assert json.loads(signal[1]) == [1, 2]
        assert json.loads(signal[2]) == [1, 2, 3]


def test_after_region_provenance_is_empty_without_a_valid_match():
    persist_acquisitions([
        "2024-01-01T00:00:00+00:00",
        "2024-02-01T00:00:00+00:00",
        "2024-03-01T00:00:00+00:00",
    ])
    persist_run(1, 2, [box(0, 0, 10, 10)])
    persist_run(2, 3, [box(30, 0, 40, 10)])

    response = client.post("/api/v1/temporal-analyses", json={"acquisition_ids": [1, 2, 3], "iou_threshold": 0.5})

    assert response.status_code == 200
    with connection() as db_connection:
        rows = db_connection.execute(
            "SELECT before_region_ids_json, after_region_ids_json, matched_region_pairs_json FROM temporal_relationships ORDER BY before_acquisition_id"
        ).fetchall()
    assert json.loads(rows[0][1]) == []
    assert json.loads(rows[0][2]) == []
    assert json.loads(rows[1][1]) == []
    assert json.loads(rows[1][2]) == []


def test_transient_and_recurrent_signals_are_distinguished():
    persist_acquisitions([
        "2024-01-01T00:00:00+00:00",
        "2024-02-01T00:00:00+00:00",
        "2024-03-01T00:00:00+00:00",
        "2024-04-01T00:00:00+00:00",
    ])
    first = box(0, 0, 10, 10)
    second = box(30, 0, 40, 10)
    persist_run(1, 2, [first])
    persist_run(2, 3, [second])
    persist_run(3, 4, [first])

    response = client.post("/api/v1/temporal-analyses", json={"acquisition_ids": [1, 2, 3, 4], "iou_threshold": 0.5})

    assert response.status_code == 200
    states = {signal["state"] for signal in response.json()["signals"]}
    assert states == {"recurrent", "transient"}
    assert response.json()["state"] == "recurrent"


def test_contiguous_support_that_disappears_is_transient_not_recurrent():
    persist_acquisitions([
        "2024-01-01T00:00:00+00:00",
        "2024-02-01T00:00:00+00:00",
        "2024-03-01T00:00:00+00:00",
        "2024-04-01T00:00:00+00:00",
    ])
    region = box(0, 0, 10, 10)
    persist_run(1, 2, [region])
    persist_run(2, 3, [region])
    persist_run(3, 4, [])

    response = client.post("/api/v1/temporal-analyses", json={"acquisition_ids": [1, 2, 3, 4]})

    assert response.status_code == 200
    assert response.json()["signals"][0]["state"] == "transient"


def test_ordering_duplicates_and_missing_detection_are_explicit():
    persist_acquisitions(["2024-01-01T00:00:00+00:00", "2024-02-01T00:00:00+00:00", "2024-03-01T00:00:00+00:00"])
    duplicate = client.post("/api/v1/temporal-analyses", json={"acquisition_ids": [1, 1, 2]})
    assert duplicate.status_code == 422
    missing = client.post("/api/v1/temporal-analyses", json={"acquisition_ids": [1, 2, 3]})
    assert missing.status_code == 404
    assert missing.json()["code"] == "MissingDetectionError"


def test_single_observation_returns_insufficient_history():
    persist_acquisitions(["2024-01-01T00:00:00+00:00"])

    response = client.post("/api/v1/temporal-analyses", json={"acquisition_ids": [1]})

    assert response.status_code == 200
    assert response.json()["state"] == "insufficient_history"
    assert response.json()["detector_run_count"] == 0
    assert response.json()["signals"] == []


def test_duplicate_timestamps_and_mixed_aois_are_explicit():
    persist_acquisitions([
        "2024-01-01T00:00:00+00:00",
        "2024-01-01T00:00:00+00:00",
        "2024-03-01T00:00:00+00:00",
    ])
    duplicate = client.post("/api/v1/temporal-analyses", json={"acquisition_ids": [1, 2, 3]})
    assert duplicate.status_code == 422
    assert duplicate.json()["code"] == "InvalidTemporalObservationsError"

    with connection() as db_connection:
        db_connection.execute("DELETE FROM imagery_acquisitions")
        db_connection.commit()
    persist_acquisitions([
        "2024-01-01T00:00:00+00:00",
        "2024-02-01T00:00:00+00:00",
        "2024-03-01T00:00:00+00:00",
    ], aoi_ids=[1, 2, 1])
    mixed = client.post("/api/v1/temporal-analyses", json={"acquisition_ids": [1, 2, 3]})
    assert mixed.status_code == 422
    assert mixed.json()["code"] == "InvalidTemporalObservationsError"


def test_multiple_observations_without_changes_is_successful_no_signal():
    persist_acquisitions([
        "2024-01-01T00:00:00+00:00",
        "2024-02-01T00:00:00+00:00",
        "2024-03-01T00:00:00+00:00",
    ])
    persist_run(1, 2, [])
    persist_run(2, 3, [])

    response = client.post("/api/v1/temporal-analyses", json={"acquisition_ids": [1, 2, 3]})

    assert response.status_code == 200
    assert response.json()["state"] == "no_temporal_signal"
    assert response.json()["signals"] == []


def test_temporal_analysis_is_repeatable():
    persist_acquisitions(["2024-01-01T00:00:00+00:00", "2024-02-01T00:00:00+00:00", "2024-03-01T00:00:00+00:00"])
    persist_run(1, 2, [box(0, 0, 10, 10)])
    persist_run(2, 3, [box(0, 0, 10, 10)])
    request = {"acquisition_ids": [3, 1, 2], "iou_threshold": 0.25}

    first = client.post("/api/v1/temporal-analyses", json=request).json()
    second = client.post("/api/v1/temporal-analyses", json=request).json()

    assert first["state"] == second["state"]
    assert first["observations"] == second["observations"]
    assert first["relationships"] == second["relationships"]
    assert first["signals"] == second["signals"]


def test_persisted_temporal_analysis_can_be_retrieved():
    persist_acquisitions([
        "2024-01-01T00:00:00+00:00",
        "2024-02-01T00:00:00+00:00",
        "2024-03-01T00:00:00+00:00",
    ])
    region = box(0, 0, 10, 10)
    persist_run(1, 2, [region])
    persist_run(2, 3, [region])
    created = client.post("/api/v1/temporal-analyses", json={"acquisition_ids": [1, 2, 3]}).json()

    retrieved = client.get(f"/api/v1/temporal-analyses/{created['analysis_id']}")

    assert retrieved.status_code == 200
    assert retrieved.json()["signals"] == created["signals"]
    assert retrieved.json()["relationships"] == created["relationships"]


def test_unusable_observation_is_insufficient_history_not_no_change():
    persist_acquisitions(
        [
            "2024-01-01T00:00:00+00:00",
            "2024-02-01T00:00:00+00:00",
            "2024-03-01T00:00:00+00:00",
        ],
        quality_states=["usable", "valid_unusable", "usable"],
    )

    response = client.post("/api/v1/temporal-analyses", json={"acquisition_ids": [1, 2, 3]})

    assert response.status_code == 200
    payload = response.json()
    assert payload["state"] == "insufficient_history"
    assert payload["usable_observation_count"] == 2
    assert payload["usable_interval_count"] == 0
    assert payload["relationships"] == []
    assert payload["signals"] == []
    assert payload["observations"][1]["quality_state"] == "valid_unusable"


def test_two_usable_observations_evaluate_one_interval_without_claiming_persistence():
    persist_acquisitions([
        "2024-01-01T00:00:00+00:00",
        "2024-02-01T00:00:00+00:00",
    ])
    persist_run(1, 2, [box(0, 0, 10, 10)])

    response = client.post("/api/v1/temporal-analyses", json={"acquisition_ids": [1, 2]})

    assert response.status_code == 200
    payload = response.json()
    assert payload["state"] == "insufficient_history"
    assert payload["detector_run_count"] == 1
    assert payload["usable_interval_count"] == 1
    assert payload["relationships"]
    assert payload["signals"][0]["state"] == "isolated"
    assert payload["signals"][0]["persistence_ratio"] == pytest.approx(1.0)


def test_persistence_ratio_uses_quality_weighted_interval_support():
    persist_acquisitions(
        [
            "2024-01-01T00:00:00+00:00",
            "2024-02-01T00:00:00+00:00",
            "2024-03-01T00:00:00+00:00",
        ],
        usable_percentages=[60, 80, 70],
    )
    region = box(0, 0, 10, 10)
    persist_run(1, 2, [region])
    persist_run(2, 3, [])

    response = client.post("/api/v1/temporal-analyses", json={"acquisition_ids": [1, 2, 3]})

    assert response.status_code == 200
    signal = response.json()["signals"][0]
    assert signal["state"] == "transient"
    assert signal["usable_interval_count"] == 2
    assert signal["supporting_interval_count"] == 1
    assert signal["persistence_ratio"] == pytest.approx(0.6 / 1.3)
    assert signal["quality_support"] == pytest.approx(0.6)
    assert response.json()["quality_aggregation_method"] == "mean_min_usable_fraction"


def test_signal_onset_is_an_observation_bounded_interval():
    persist_acquisitions([
        "2024-01-01T00:00:00+00:00",
        "2024-02-01T00:00:00+00:00",
        "2024-03-01T00:00:00+00:00",
    ])
    region = box(0, 0, 10, 10)
    persist_run(1, 2, [])
    persist_run(2, 3, [region])

    response = client.post("/api/v1/temporal-analyses", json={"acquisition_ids": [1, 2, 3]})

    assert response.status_code == 200
    signal = response.json()["signals"][0]
    assert signal["onset_before_acquisition_id"] == 2
    assert signal["onset_after_acquisition_id"] == 3
    assert signal["onset_start_datetime"] == "2024-02-01T00:00:00Z"
    assert signal["onset_end_datetime"] == "2024-03-01T00:00:00Z"


def test_recurrent_signal_reports_limited_seasonal_compatibility():
    persist_acquisitions([
        "2024-01-01T00:00:00+00:00",
        "2024-02-01T00:00:00+00:00",
        "2024-03-01T00:00:00+00:00",
        "2024-04-01T00:00:00+00:00",
    ])
    first = box(0, 0, 10, 10)
    second = box(30, 0, 40, 10)
    persist_run(1, 2, [first])
    persist_run(2, 3, [second])
    persist_run(3, 4, [first])

    response = client.post("/api/v1/temporal-analyses", json={"acquisition_ids": [1, 2, 3, 4]})

    assert response.status_code == 200
    assert response.json()["seasonal_interpretation"] == "seasonal_compatible"
    assert {signal["seasonal_interpretation"] for signal in response.json()["signals"]} == {"seasonal_compatible", "less_seasonal_compatible"}


def test_recurrent_support_outside_calendar_window_is_less_seasonal_compatible():
    persist_acquisitions([
        "2024-01-01T00:00:00+00:00",
        "2024-02-01T00:00:00+00:00",
        "2024-07-01T00:00:00+00:00",
        "2024-08-01T00:00:00+00:00",
    ])
    region = box(0, 0, 10, 10)
    persist_run(1, 2, [region])
    persist_run(2, 3, [box(30, 0, 40, 10)])
    persist_run(3, 4, [region])

    response = client.post("/api/v1/temporal-analyses", json={"acquisition_ids": [1, 2, 3, 4]})

    assert response.status_code == 200
    assert response.json()["seasonal_interpretation"] == "less_seasonal_compatible"