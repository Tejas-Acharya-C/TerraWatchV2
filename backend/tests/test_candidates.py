from __future__ import annotations

import json
import sqlite3
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


def persist_analysis(*, with_signal: bool = True) -> None:
    timestamp = "2024-01-01T00:00:00+00:00"
    with connection() as db_connection:
        db_connection.execute(
            "INSERT INTO aoi (id, geometry_json, created_at, updated_at) VALUES (1, '{}', ?, ?)",
            (timestamp, timestamp),
        )
        for acquisition_id in range(1, 4):
            when = f"2024-0{acquisition_id}-01T00:00:00+00:00"
            db_connection.execute(
                """INSERT INTO imagery_acquisitions (
                    id, aoi_id, requested_start_datetime, requested_end_datetime,
                    item_id, collection, acquisition_datetime, assets_json,
                    prepared_path, raster_metadata_json, source_metadata_json, created_at
                ) VALUES (?, 1, ?, ?, ?, 'sentinel-2-l2a', ?, '[]', 'unused.tif', '{}', '{}', ?)""",
                (acquisition_id, when, when, f"S2_{acquisition_id}", when, when),
            )
        for run_id, before_id, after_id in [(1, 1, 2), (2, 2, 3)]:
            db_connection.execute(
                """INSERT INTO detection_runs (
                    id, before_acquisition_id, after_acquisition_id, detector_version,
                    threshold, min_region_pixels, region_count, changed_pixel_count,
                    total_changed_area_m2, created_at
                ) VALUES (?, ?, ?, 'test', 0.2, 1, 1, 10, 100, ?)""",
                (run_id, before_id, after_id, timestamp),
            )
        db_connection.execute(
            """INSERT INTO temporal_analyses (
                id, iou_threshold, detector_run_count, observation_count,
                temporal_span_days, state, created_at
            ) VALUES (1, 0.25, 2, 3, 60, 'persistent', ?)""",
            (timestamp,),
        )
        db_connection.executemany(
            "INSERT INTO temporal_relationships (analysis_id, before_acquisition_id, after_acquisition_id, detection_run_id, region_count, changed_pixel_count) VALUES (1, ?, ?, ?, 1, 10)",
            [(1, 2, 1), (2, 3, 2)],
        )
        if with_signal:
            db_connection.execute(
                """INSERT INTO temporal_signals (
                    analysis_id, signal_id, geometry_json, support_count, interval_count,
                    persistence_ratio, recurrence_count, transient_interval_count,
                    temporal_consistency, matched_region_coverage, first_change_datetime,
                    last_supporting_datetime, state
                ) VALUES (1, 1, ?, 2, 2, 1.0, 1, 0, 0.9, 0.25, ?, ?, 'persistent')""",
                (json.dumps(mapping(box(0, 0, 1, 1))), "2024-01-01T00:00:00+00:00", "2024-03-01T00:00:00+00:00"),
            )
            db_connection.execute("UPDATE temporal_signals SET detection_run_ids_json = '[1,2]', acquisition_ids_json = '[1,2,3]', source_region_ids_json = '[1,2]' WHERE analysis_id = 1 AND signal_id = 1")
        db_connection.commit()


def test_candidate_generation_is_deterministic_and_persists_provenance():
    persist_analysis()

    first = client.post("/api/v1/candidates/triage", json={"analysis_id": 1})
    second = client.post("/api/v1/candidates/triage", json={"analysis_id": 1})

    assert first.status_code == 200
    first_candidate = first.json()["candidates"][0]
    second_candidate = second.json()["candidates"][0]
    for timestamp in ("created_at", "updated_at"):
        first_candidate.pop(timestamp)
        second_candidate.pop(timestamp)
    assert first_candidate == second_candidate
    candidate = first_candidate
    assert candidate["candidate_id"] == "analysis-1-signal-1"
    assert candidate["score"] == pytest.approx(0.575)
    assert candidate["severity"] == "low"
    assert candidate["priority"] == "high"
    assert candidate["rank"] == 1
    assert candidate["detection_run_ids"] == [1, 2]
    assert candidate["acquisition_ids"] == [1, 2, 3]
    # Check score components
    components = candidate["metrics"]["score_components"]
    assert components["temporal_persistence"] == 1.0
    assert components["temporal_consistency"] == 0.9
    assert components["change_magnitude"] == 0.0
    assert components["observation_quality"] == 0.0
    assert components["persistence_ratio"] == 1.0
    assert components["matched_region_coverage"] == 0.25
    # Check explainability
    explanation = candidate["metrics"]["explanation"]
    assert explanation is not None
    assert "0.575" in explanation["summary"]
    assert len(explanation["positive_factors"]) >= 2
    assert explanation["severity_rationale"]
    assert explanation["priority_rationale"]


def test_valid_zero_candidates_is_not_an_upstream_error():
    persist_analysis(with_signal=False)

    response = client.post("/api/v1/candidates/triage", json={"analysis_id": 1})

    assert response.status_code == 200
    assert response.json()["candidates"] == []


def test_missing_analysis_and_broken_phase_four_reference_are_distinct():
    missing = client.post("/api/v1/candidates/triage", json={"analysis_id": 99})
    assert missing.status_code == 404
    assert missing.json()["code"] == "MissingTemporalAnalysisError"

    persist_analysis()
    with connection() as db_connection:
        with pytest.raises(sqlite3.IntegrityError):
            db_connection.execute("DELETE FROM detection_runs WHERE id = 2")
    intact = client.post("/api/v1/candidates/triage", json={"analysis_id": 1})
    assert intact.status_code == 200


def test_review_state_is_persistent():
    persist_analysis()
    client.post("/api/v1/candidates/triage", json={"analysis_id": 1})

    response = client.patch(
        "/api/v1/candidates/analysis-1-signal-1/review-state",
        json={"review_state": "accepted"},
    )

    assert response.status_code == 200
    assert response.json()["review_state"] == "accepted"
    assert client.get("/api/v1/candidates/analysis-1-signal-1").json()["review_state"] == "accepted"

    # Re-triage must NOT overwrite the analyst's review state
    re_triage = client.post("/api/v1/candidates/triage", json={"analysis_id": 1})
    assert re_triage.status_code == 200
    assert re_triage.json()["candidates"][0]["review_state"] == "accepted"


def test_invalid_review_state_and_missing_candidate_are_explicit():
    persist_analysis()
    client.post("/api/v1/candidates/triage", json={"analysis_id": 1})
    invalid = client.patch("/api/v1/candidates/analysis-1-signal-1/review-state", json={"review_state": "in_review"})
    missing = client.get("/api/v1/candidates/analysis-1-signal-99")
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "ValidationError"
    assert missing.status_code == 404
    assert missing.json()["code"] == "CandidateNotFoundError"


def test_unusable_analysis_is_not_zero_candidates():
    persist_analysis()
    with connection() as db_connection:
        db_connection.execute("UPDATE temporal_analyses SET state = 'insufficient_history' WHERE id = 1")
        db_connection.commit()
    response = client.post("/api/v1/candidates/triage", json={"analysis_id": 1})
    assert response.status_code == 422
    assert response.json()["code"] == "CandidateAnalysisUnavailableError"


def test_candidate_list_can_filter_analysis_and_is_ranked():
    persist_analysis()
    client.post("/api/v1/candidates/triage", json={"analysis_id": 1})
    response = client.get("/api/v1/candidates", params={"analysis_id": 1})
    assert response.status_code == 200
    assert response.json()["analysis_id"] == 1
    assert [candidate["rank"] for candidate in response.json()["candidates"]] == [1]


def test_persistence_affects_confidence_and_ranking_monotonically():
    persist_analysis()
    with connection() as db:
        # Add a second signal with lower persistence ratio (0.5 vs 1.0) and transient state
        db.execute(
            """INSERT INTO temporal_signals (
                analysis_id, signal_id, geometry_json, support_count, interval_count,
                persistence_ratio, recurrence_count, transient_interval_count,
                temporal_consistency, matched_region_coverage, first_change_datetime,
                last_supporting_datetime, state, detection_run_ids_json, acquisition_ids_json
            ) VALUES (1, 2, ?, 1, 2, 0.5, 0, 1, 0.9, 0.25, '2024-01-01', '2024-02-01', 'transient', '[1]', '[1,2]')""",
            (json.dumps(mapping(box(2, 2, 3, 3))),),
        )
        db.commit()

    response = client.post("/api/v1/candidates/triage", json={"analysis_id": 1})
    candidates = response.json()["candidates"]
    assert len(candidates) == 2

    c1 = next(c for c in candidates if c["signal_id"] == 1)
    c2 = next(c for c in candidates if c["signal_id"] == 2)

    # c1 has persistence 1.0, c2 has persistence 0.5; identical otherwise
    assert c1["score"] > c2["score"]
    assert c1["rank"] < c2["rank"]
    assert c1["priority"] == "high"
    assert c2["priority"] in {"normal", "low"}


def test_change_magnitude_affects_confidence_and_severity():
    persist_analysis()
    with connection() as db:
        # Signal 1 with region that has significant delta NDVI and area
        db.execute(
            "INSERT INTO raw_change_regions (id, run_id, geometry_json, pixel_count, area_m2, mean_change_signal, max_change_signal) VALUES (1, 1, '{}', 50, 6000.0, 0.60, 0.75)"
        )
        db.execute("UPDATE temporal_signals SET source_region_ids_json = '[1]' WHERE analysis_id = 1 AND signal_id = 1")
        db.commit()

    response = client.post("/api/v1/candidates/triage", json={"analysis_id": 1})
    candidate = response.json()["candidates"][0]

    # c_mag is now positive due to norm_delta_ndvi and norm_area
    assert candidate["metrics"]["score_components"]["change_magnitude"] > 0.0
    assert candidate["metrics"]["total_area_m2"] == 6000.0
    assert candidate["metrics"]["mean_change_signal"] == 0.60
    assert candidate["severity"] == "high"


def test_observation_quality_affects_confidence_and_does_not_become_false_negative():
    persist_analysis()
    with connection() as db:
        # Add physical change region so both signals have high physical severity
        db.execute(
            "INSERT INTO raw_change_regions (id, run_id, geometry_json, pixel_count, area_m2, mean_change_signal, max_change_signal) VALUES (1, 1, '{}', 50, 6000.0, 0.60, 0.75)"
        )
        # High quality signal
        db.execute("UPDATE temporal_signals SET source_region_ids_json = '[1]', quality_support = 0.90 WHERE analysis_id = 1 AND signal_id = 1")
        # Add low quality signal with identical physical change
        db.execute(
            """INSERT INTO temporal_signals (
                analysis_id, signal_id, geometry_json, support_count, interval_count,
                persistence_ratio, recurrence_count, transient_interval_count,
                temporal_consistency, matched_region_coverage, first_change_datetime,
                last_supporting_datetime, state, quality_support, source_region_ids_json, detection_run_ids_json, acquisition_ids_json
            ) VALUES (1, 2, ?, 2, 2, 1.0, 1, 0, 0.9, 0.25, '2024-01-01', '2024-03-01', 'persistent', 0.20, '[1]', '[1,2]', '[1,2,3]')""",
            (json.dumps(mapping(box(2, 2, 3, 3))),),
        )
        db.commit()

    response = client.post("/api/v1/candidates/triage", json={"analysis_id": 1})
    candidates = response.json()["candidates"]

    c_high = next(c for c in candidates if c["signal_id"] == 1)
    c_low = next(c for c in candidates if c["signal_id"] == 2)

    # High quality yields higher evidence score
    assert c_high["score"] > c_low["score"]
    # Poor quality (0.20) reduces confidence, but does NOT become negative or zero
    assert c_low["score"] > 0.50
    # Poor quality (< 0.40) downgrades urgent priority to high priority
    assert c_low["priority"] == "high"
    # Explainability notes that quality reduced confidence without claiming no change
    limiting = c_low["metrics"]["explanation"]["limiting_factors"]
    assert any("not because absence of change was demonstrated" in factor for factor in limiting)


def test_deterministic_tie_breaking_is_stable():
    persist_analysis()
    with connection() as db:
        # Insert 3 identical signals (same persistence, consistency, quality, state)
        for sig_id in (2, 3):
            db.execute(
                """INSERT INTO temporal_signals (
                    analysis_id, signal_id, geometry_json, support_count, interval_count,
                    persistence_ratio, recurrence_count, transient_interval_count,
                    temporal_consistency, matched_region_coverage, first_change_datetime,
                    last_supporting_datetime, state, detection_run_ids_json, acquisition_ids_json
                ) VALUES (1, ?, ?, 2, 2, 1.0, 1, 0, 0.9, 0.25, '2024-01-01', '2024-03-01', 'persistent', '[1,2]', '[1,2,3]')""",
                (sig_id, json.dumps(mapping(box(sig_id, 0, sig_id + 1, 1)))),
            )
        db.commit()

    first_run = client.post("/api/v1/candidates/triage", json={"analysis_id": 1}).json()["candidates"]
    second_run = client.post("/api/v1/candidates/triage", json={"analysis_id": 1}).json()["candidates"]

    # All 3 have identical scores
    scores = [c["score"] for c in first_run]
    assert scores[0] == scores[1] == scores[2]

    # Deterministic tie-breaker assigns ranks by signal_id: 1, 2, 3
    ranks_first = [(c["signal_id"], c["rank"]) for c in first_run]
    ranks_second = [(c["signal_id"], c["rank"]) for c in second_run]
    assert ranks_first == [(1, 1), (2, 2), (3, 3)]
    assert ranks_first == ranks_second


def test_frontend_cannot_override_candidate_scoring_or_metadata():
    persist_analysis()
    client.post("/api/v1/candidates/triage", json={"analysis_id": 1})

    # Frontend attempts to send fake score, priority, or severity in triage request
    spoofed = client.post("/api/v1/candidates/triage", json={"analysis_id": 1, "score": 9.99, "priority": "urgent"})
    assert spoofed.status_code == 422

    # Frontend attempts to send fake score in review-state patch
    spoofed_patch = client.patch(
        "/api/v1/candidates/analysis-1-signal-1/review-state",
        json={"review_state": "accepted", "score": 0.0, "severity": "low"},
    )
    assert spoofed_patch.status_code == 422


def test_triage_filtering_by_priority_severity_review_state():
    persist_analysis()
    with connection() as db:
        # Give Signal 1 high physical severity and urgent priority
        db.execute(
            "INSERT INTO raw_change_regions (id, run_id, geometry_json, pixel_count, area_m2, mean_change_signal, max_change_signal) VALUES (1, 1, '{}', 50, 6000.0, 0.60, 0.75)"
        )
        db.execute("UPDATE temporal_signals SET source_region_ids_json = '[1]' WHERE analysis_id = 1 AND signal_id = 1")
        # Add a transient low-priority signal
        db.execute(
            """INSERT INTO temporal_signals (
                analysis_id, signal_id, geometry_json, support_count, interval_count,
                persistence_ratio, recurrence_count, transient_interval_count,
                temporal_consistency, matched_region_coverage, first_change_datetime,
                last_supporting_datetime, state, detection_run_ids_json, acquisition_ids_json
            ) VALUES (1, 2, ?, 1, 2, 0.2, 0, 1, 0.3, 0.1, '2024-01-01', '2024-02-01', 'transient', '[1]', '[1,2]')""",
            (json.dumps(mapping(box(2, 2, 3, 3))),),
        )
        db.commit()

    client.post("/api/v1/candidates/triage", json={"analysis_id": 1})

    # Filter by priority
    urgent_only = client.get("/api/v1/candidates", params={"analysis_id": 1, "priority": "urgent"}).json()["candidates"]
    assert len(urgent_only) == 1
    assert urgent_only[0]["signal_id"] == 1

    # Filter by severity
    high_only = client.get("/api/v1/candidates", params={"analysis_id": 1, "severity": "high"}).json()["candidates"]
    assert len(high_only) == 1
    assert high_only[0]["signal_id"] == 1

    # Filter by review_state
    client.patch("/api/v1/candidates/analysis-1-signal-1/review-state", json={"review_state": "accepted"})
    accepted_only = client.get("/api/v1/candidates", params={"analysis_id": 1, "review_state": "accepted"}).json()["candidates"]
    assert len(accepted_only) == 1
    assert accepted_only[0]["candidate_id"] == "analysis-1-signal-1"

    unreviewed_only = client.get("/api/v1/candidates", params={"analysis_id": 1, "review_state": "unreviewed"}).json()["candidates"]
    assert len(unreviewed_only) == 1
    assert unreviewed_only[0]["candidate_id"] == "analysis-1-signal-2"


def test_triage_filtering_combinations_temporal_score_quality():
    persist_analysis()
    with connection() as db:
        # Signal 1: persistent, score ~0.575, quality_support = 0.0
        # Signal 2: transient, score lower, quality_support = 0.8
        db.execute(
            """INSERT INTO temporal_signals (
                analysis_id, signal_id, geometry_json, support_count, interval_count,
                persistence_ratio, recurrence_count, transient_interval_count,
                temporal_consistency, matched_region_coverage, first_change_datetime,
                last_supporting_datetime, state, quality_support, detection_run_ids_json, acquisition_ids_json
            ) VALUES (1, 2, ?, 1, 2, 0.4, 0, 1, 0.5, 0.1, '2024-01-01', '2024-02-01', 'transient', 0.8, '[1]', '[1,2]')""",
            (json.dumps(mapping(box(2, 2, 3, 3))),),
        )
        db.commit()

    client.post("/api/v1/candidates/triage", json={"analysis_id": 1})

    # 1. Unfiltered returns all candidates unaltered
    all_candidates = client.get("/api/v1/candidates", params={"analysis_id": 1}).json()["candidates"]
    assert len(all_candidates) == 2

    # 2. Filter by temporal_state
    persistent_only = client.get("/api/v1/candidates", params={"analysis_id": 1, "temporal_state": "persistent"}).json()["candidates"]
    assert len(persistent_only) == 1
    assert persistent_only[0]["signal_id"] == 1

    transient_only = client.get("/api/v1/candidates", params={"analysis_id": 1, "temporal_state": "transient"}).json()["candidates"]
    assert len(transient_only) == 1
    assert transient_only[0]["signal_id"] == 2

    # 3. Filter by min_score
    high_score = client.get("/api/v1/candidates", params={"analysis_id": 1, "min_score": 0.50}).json()["candidates"]
    assert len(high_score) == 1
    assert high_score[0]["signal_id"] == 1

    # 4. Filter by min_quality
    high_qual = client.get("/api/v1/candidates", params={"analysis_id": 1, "min_quality": 0.50}).json()["candidates"]
    assert len(high_qual) == 1
    assert high_qual[0]["signal_id"] == 2

    # 5. Combined filter: temporal_state + min_score
    combined = client.get("/api/v1/candidates", params={"analysis_id": 1, "temporal_state": "persistent", "min_score": 0.50}).json()["candidates"]
    assert len(combined) == 1
    assert combined[0]["signal_id"] == 1

    # 6. Combined filter with conflicting criteria returns empty list
    conflicting = client.get("/api/v1/candidates", params={"analysis_id": 1, "temporal_state": "transient", "min_score": 0.90}).json()["candidates"]
    assert len(conflicting) == 0


def test_invalid_upstream_signal_geometry_is_rejected():
    persist_analysis()
    with connection() as db:
        # Corrupt geometry to empty polygon
        db.execute("UPDATE temporal_signals SET geometry_json = '{\"type\":\"Polygon\",\"coordinates\":[]}' WHERE analysis_id = 1 AND signal_id = 1")
        db.commit()

    response = client.post("/api/v1/candidates/triage", json={"analysis_id": 1})
    assert response.status_code == 502
    assert response.json()["code"] == "CandidateUpstreamFailureError"


def test_normalization_bounds_and_monotonicity():
    from app.candidates import _score_candidate

    # Test delta NDVI bounds: 0.20 -> 0, 0.80 -> 1, clamping below and above
    _, _, norm_010, _, _ = _score_candidate(0, 0, 0.10, 500, 0, 0)
    assert norm_010 == 0.0
    _, _, norm_020, _, _ = _score_candidate(0, 0, 0.20, 500, 0, 0)
    assert norm_020 == 0.0
    _, _, norm_050, _, _ = _score_candidate(0, 0, 0.50, 500, 0, 0)
    assert norm_050 == pytest.approx(0.50)
    _, _, norm_080, _, _ = _score_candidate(0, 0, 0.80, 500, 0, 0)
    assert norm_080 == 1.0
    _, _, norm_100, _, _ = _score_candidate(0, 0, 1.00, 500, 0, 0)
    assert norm_100 == 1.0

    # Test Area bounds: 500 m² -> 0, 10,000 m² -> 1, clamping below and above
    _, _, _, norm_a100, _ = _score_candidate(0, 0, 0.20, 100, 0, 0)
    assert norm_a100 == 0.0
    _, _, _, norm_a500, _ = _score_candidate(0, 0, 0.20, 500, 0, 0)
    assert norm_a500 == 0.0
    _, _, _, norm_a5250, _ = _score_candidate(0, 0, 0.20, 5250, 0, 0)
    assert norm_a5250 == pytest.approx(0.50)
    _, _, _, norm_a10000, _ = _score_candidate(0, 0, 0.20, 10000, 0, 0)
    assert norm_a10000 == 1.0
    _, _, _, norm_a20000, _ = _score_candidate(0, 0, 0.20, 20000, 0, 0)
    assert norm_a20000 == 1.0

    # Test score bounding [0, 1]
    score_min, comps_min, _, _, _ = _score_candidate(0, 0, 0.0, 0, 0, 0)
    assert score_min == 0.0
    assert all(v == 0.0 for v in comps_min.values())

    score_max, comps_max, _, _, _ = _score_candidate(1.0, 1.0, 1.0, 20000, 1.0, 1.0)
    assert score_max == 1.0
    assert all(v == 1.0 for v in comps_max.values())


def test_severity_is_purely_physical_and_independent_of_temporal_state():
    from app.candidates import _score_candidate, _severity

    temporal_states = ["persistent", "recurrent", "transient", "isolated", "insufficient_history", "no_temporal_signal"]

    # 1. Same physical magnitude gives the exact same severity regardless of temporal state
    # Small physical change:
    _, _, ndvi_sm, area_sm, cmag_sm = _score_candidate(1.0, 1.0, 0.10, 200.0, 1.0, 1.0)
    assert _severity(ndvi_sm, area_sm, cmag_sm) == "low"

    # Moderate physical change:
    _, _, ndvi_md, area_md, cmag_md = _score_candidate(0.5, 0.5, 0.40, 3000.0, 0.5, 0.5)
    assert _severity(ndvi_md, area_md, cmag_md) == "medium"

    # Large physical change:
    _, _, ndvi_lg, area_lg, cmag_lg = _score_candidate(0.2, 0.2, 0.65, 7500.0, 0.2, 0.2)
    assert _severity(ndvi_lg, area_lg, cmag_lg) == "high"

    # 2. Increasing physical magnitude monotonically increases severity
    assert _severity(ndvi_sm, area_sm, cmag_sm) == "low"
    assert _severity(ndvi_md, area_md, cmag_md) == "medium"
    assert _severity(ndvi_lg, area_lg, cmag_lg) == "high"

    # 3. A small persistent signal is NOT automatically high severity solely because it is persistent
    # Small area (200 m²) and low delta ndvi (0.10) with persistence 1.0
    _, _, ndvi_persist, area_persist, cmag_persist = _score_candidate(1.0, 1.0, 0.10, 200.0, 1.0, 1.0)
    assert _severity(ndvi_persist, area_persist, cmag_persist) == "low"

    # 4. A large transient physical signal has high severity because severity describes physical magnitude/extent
    # Large area (8000 m²) and high delta ndvi (0.70) with transient persistence 0.2
    _, _, ndvi_transient, area_transient, cmag_transient = _score_candidate(0.2, 0.3, 0.70, 8000.0, 0.5, 0.2)
    assert _severity(ndvi_transient, area_transient, cmag_transient) == "high"


def test_priority_and_ranking_prevent_small_persistent_signal_from_outranking_strong_large_change():
    persist_analysis()
    with connection() as db:
        # Signal 1: Small persistent signal (area 100 m², mean delta NDVI 0.0, persistence 1.0)
        # Signal 2: Strong, large, high-quality change (area 7500 m², mean delta NDVI 0.65, quality 0.85, persistence 0.6)
        db.execute("UPDATE temporal_signals SET source_region_ids_json = '[]' WHERE analysis_id = 1 AND signal_id = 1")
        db.execute(
            "INSERT INTO raw_change_regions (id, run_id, geometry_json, pixel_count, area_m2, mean_change_signal, max_change_signal) VALUES (3, 2, '{}', 60, 7500.0, 0.65, 0.80)"
        )
        db.execute(
            """INSERT INTO temporal_signals (
                analysis_id, signal_id, geometry_json, support_count, interval_count,
                persistence_ratio, recurrence_count, transient_interval_count,
                temporal_consistency, matched_region_coverage, first_change_datetime,
                last_supporting_datetime, state, quality_support, source_region_ids_json, detection_run_ids_json, acquisition_ids_json
            ) VALUES (1, 2, ?, 2, 2, 0.6, 0, 1, 0.8, 0.7, '2024-01-01', '2024-03-01', 'transient', 0.85, '[3]', '[1,2]', '[1,2,3]')""",
            (json.dumps(mapping(box(2, 2, 3, 3))),),
        )
        db.commit()

    response = client.post("/api/v1/candidates/triage", json={"analysis_id": 1})
    candidates = response.json()["candidates"]
    assert len(candidates) == 2

    c_small_persistent = next(c for c in candidates if c["signal_id"] == 1)
    c_strong_large = next(c for c in candidates if c["signal_id"] == 2)

    # Small persistent candidate has low physical severity and high (not urgent) priority
    assert c_small_persistent["severity"] == "low"
    assert c_small_persistent["priority"] == "high"

    # Strong large candidate has high physical severity and urgent priority
    assert c_strong_large["severity"] == "high"
    assert c_strong_large["priority"] == "urgent"

    # The strong large candidate decisively outranks the small persistent candidate
    assert c_strong_large["rank"] == 1
    assert c_small_persistent["rank"] == 2

