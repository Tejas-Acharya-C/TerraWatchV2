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


def persist_analysis() -> None:
    timestamp = "2024-01-01T00:00:00+00:00"
    with connection() as db:
        # Karnataka-contained AOI
        aoi_geom = {
            "type": "Polygon",
            "coordinates": [[[75.0, 13.0], [76.0, 13.0], [76.0, 14.0], [75.0, 14.0], [75.0, 13.0]]],
        }
        db.execute(
            "INSERT INTO aoi (id, geometry_json, created_at, updated_at) VALUES (1, ?, ?, ?)",
            (json.dumps(aoi_geom), timestamp, timestamp),
        )
        for acq_id in (1, 2, 3):
            when = f"2024-0{acq_id}-01T00:00:00+00:00"
            db.execute(
                """INSERT INTO imagery_acquisitions (
                    id, aoi_id, requested_start_datetime, requested_end_datetime,
                    item_id, collection, acquisition_datetime, assets_json,
                    prepared_path, raster_metadata_json, source_metadata_json, created_at,
                    observation_state, quality_mask_path, display_path
                ) VALUES (?, 1, ?, ?, ?, 'sentinel-2-l2a', ?, '[]', 'unused.tif', '{}', '{}', ?, 'usable', null, null)""",
                (acq_id, when, when, f"S2_{acq_id}", when, when),
            )
        for run_id, before_id, after_id in [(1, 1, 2), (2, 2, 3)]:
            db.execute(
                """INSERT INTO detection_runs (
                    id, before_acquisition_id, after_acquisition_id, detector_version,
                    threshold, min_region_pixels, region_count, changed_pixel_count,
                    total_changed_area_m2, created_at
                ) VALUES (?, ?, ?, 'test', 0.2, 1, 1, 10, 100, ?)""",
                (run_id, before_id, after_id, timestamp),
            )
        db.execute(
            """INSERT INTO temporal_analyses (
                id, iou_threshold, detector_run_count, observation_count,
                temporal_span_days, state, created_at
            ) VALUES (1, 0.25, 2, 3, 60, 'persistent', ?)""",
            (timestamp,),
        )
        db.executemany(
            "INSERT INTO temporal_relationships (analysis_id, before_acquisition_id, after_acquisition_id, detection_run_id, region_count, changed_pixel_count) VALUES (1, ?, ?, ?, 1, 10)",
            [(1, 2, 1), (2, 3, 2)],
        )
        # Signal inside the Karnataka AOI
        sig_geom = mapping(box(75.2, 13.2, 75.4, 13.4))
        db.execute(
            """INSERT INTO temporal_signals (
                analysis_id, signal_id, geometry_json, support_count, interval_count,
                persistence_ratio, recurrence_count, transient_interval_count,
                temporal_consistency, matched_region_coverage, first_change_datetime,
                last_supporting_datetime, state, detection_run_ids_json, acquisition_ids_json,
                source_region_ids_json
            ) VALUES (1, 1, ?, 2, 2, 1.0, 1, 0, 0.9, 0.25, ?, ?, 'persistent', '[1,2]', '[1,2,3]', '[1]')""",
            (json.dumps(sig_geom), timestamp, timestamp),
        )
        db.commit()


def setup_candidate() -> str:
    persist_analysis()
    res = client.post("/api/v1/candidates/triage", json={"analysis_id": 1})
    assert res.status_code == 200, res.text
    return res.json()["candidates"][0]["candidate_id"]


def test_new_candidate_is_unreviewed_without_persisted_row():
    candidate_id = setup_candidate()

    # GET review endpoint returns synthetic unreviewed state
    res = client.get(f"/api/v1/candidates/{candidate_id}/review")
    assert res.status_code == 200
    data = res.json()
    assert data["candidate_id"] == candidate_id
    assert data["decision"] == "unreviewed"
    assert data["note"] is None
    assert data["created_at"] is None
    assert data["updated_at"] is None

    # Verify no row in candidate_reviews was created
    with connection() as db:
        count = db.execute("SELECT count(*) FROM candidate_reviews").fetchone()[0]
        assert count == 0


def test_accept_decision_persists():
    candidate_id = setup_candidate()

    res = client.patch(
        f"/api/v1/candidates/{candidate_id}/review",
        json={"decision": "accepted", "note": "Verified change in area"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["candidate_id"] == candidate_id
    assert data["decision"] == "accepted"
    assert data["note"] == "Verified change in area"
    assert data["created_at"] is not None
    assert data["updated_at"] is not None

    # Deterministic GET returns the persisted state
    get_res = client.get(f"/api/v1/candidates/{candidate_id}/review")
    assert get_res.status_code == 200
    assert get_res.json() == data


def test_reject_decision_persists():
    candidate_id = setup_candidate()

    res = client.patch(
        f"/api/v1/candidates/{candidate_id}/review",
        json={"decision": "rejected", "note": "False positive change"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["decision"] == "rejected"
    assert data["note"] == "False positive change"


def test_investigate_decision_persists():
    candidate_id = setup_candidate()

    res = client.patch(
        f"/api/v1/candidates/{candidate_id}/review",
        json={"decision": "investigate", "note": "Needs ground confirmation"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["decision"] == "investigate"
    assert data["note"] == "Needs ground confirmation"


def test_invalid_decision_is_rejected():
    candidate_id = setup_candidate()

    for invalid_val in ("confirmed", "done", "reviewed", "approved", "processed", "foo"):
        res = client.patch(
            f"/api/v1/candidates/{candidate_id}/review",
            json={"decision": invalid_val},
        )
        assert res.status_code == 422


def test_nonexistent_candidate_returns_explicit_not_found():
    # GET nonexistent
    res_get = client.get("/api/v1/candidates/nonexistent-candidate/review")
    assert res_get.status_code == 404
    assert res_get.json()["code"] == "CandidateNotFoundError"

    # PATCH nonexistent
    res_patch = client.patch(
        "/api/v1/candidates/nonexistent-candidate/review",
        json={"decision": "accepted"},
    )
    assert res_patch.status_code == 404
    assert res_patch.json()["code"] == "CandidateNotFoundError"


def test_analyst_note_semantics_length_and_whitespace():
    candidate_id = setup_candidate()

    # Note up to 2000 chars is allowed
    long_note = "A" * 2000
    res = client.patch(
        f"/api/v1/candidates/{candidate_id}/review",
        json={"decision": "accepted", "note": long_note},
    )
    assert res.status_code == 200
    assert res.json()["note"] == long_note

    # Note exceeding 2000 chars is rejected
    too_long = "A" * 2001
    res_err = client.patch(
        f"/api/v1/candidates/{candidate_id}/review",
        json={"decision": "accepted", "note": too_long},
    )
    assert res_err.status_code == 422

    # Whitespace-only note normalizes to None
    res_ws = client.patch(
        f"/api/v1/candidates/{candidate_id}/review",
        json={"decision": "accepted", "note": "   \n\t  "},
    )
    assert res_ws.status_code == 200
    assert res_ws.json()["note"] is None


def test_decision_revision_and_timestamp_semantics():
    candidate_id = setup_candidate()

    # First save
    res1 = client.patch(
        f"/api/v1/candidates/{candidate_id}/review",
        json={"decision": "investigate", "note": "Initial check"},
    )
    assert res1.status_code == 200
    t1_created = res1.json()["created_at"]
    t1_updated = res1.json()["updated_at"]
    assert t1_created is not None
    assert t1_updated is not None

    # Revision: investigate -> accepted
    res2 = client.patch(
        f"/api/v1/candidates/{candidate_id}/review",
        json={"decision": "accepted", "note": "Resolved after review"},
    )
    assert res2.status_code == 200
    data2 = res2.json()
    assert data2["decision"] == "accepted"
    assert data2["note"] == "Resolved after review"
    # created_at must remain unchanged
    assert data2["created_at"] == t1_created
    # updated_at must be valid ISO timestamp
    assert data2["updated_at"] is not None

    # Repeated identical save does not create duplicate rows
    res3 = client.patch(
        f"/api/v1/candidates/{candidate_id}/review",
        json={"decision": "accepted", "note": "Resolved after review"},
    )
    assert res3.status_code == 200
    with connection() as db:
        rows = db.execute("SELECT count(*) FROM candidate_reviews WHERE candidate_id = ?", (candidate_id,)).fetchone()[0]
        assert rows == 1


def test_single_source_of_truth_and_denormalized_field_synchronization():
    candidate_id = setup_candidate()

    # Save accepted
    client.patch(
        f"/api/v1/candidates/{candidate_id}/review",
        json={"decision": "accepted", "note": "Sync test"},
    )

    with connection() as db:
        cr_decision = db.execute("SELECT decision FROM candidate_reviews WHERE candidate_id = ?", (candidate_id,)).fetchone()[0]
        c_review_state = db.execute("SELECT review_state FROM candidates WHERE candidate_id = ?", (candidate_id,)).fetchone()[0]
        assert cr_decision == "accepted"
        assert c_review_state == "accepted"

    # Revision: accepted -> rejected
    client.patch(
        f"/api/v1/candidates/{candidate_id}/review",
        json={"decision": "rejected", "note": "Revised to rejected"},
    )

    with connection() as db:
        cr_decision = db.execute("SELECT decision FROM candidate_reviews WHERE candidate_id = ?", (candidate_id,)).fetchone()[0]
        c_review_state = db.execute("SELECT review_state FROM candidates WHERE candidate_id = ?", (candidate_id,)).fetchone()[0]
        assert cr_decision == "rejected"
        assert c_review_state == "rejected"

    # Candidates GET detail and list also reflect the synchronized state
    detail = client.get(f"/api/v1/candidates/{candidate_id}").json()
    assert detail["review_state"] == "rejected"

    cand_list = client.get("/api/v1/candidates", params={"review_state": "rejected"}).json()["candidates"]
    assert len(cand_list) == 1
    assert cand_list[0]["candidate_id"] == candidate_id


def test_scientific_immutability_before_and_after_review():
    candidate_id = setup_candidate()

    before = client.get(f"/api/v1/candidates/{candidate_id}").json()

    # Perform decision creation
    client.patch(
        f"/api/v1/candidates/{candidate_id}/review",
        json={"decision": "accepted", "note": "Immutability check"},
    )
    after1 = client.get(f"/api/v1/candidates/{candidate_id}").json()

    # Perform decision revision
    client.patch(
        f"/api/v1/candidates/{candidate_id}/review",
        json={"decision": "rejected", "note": "Second check"},
    )
    after2 = client.get(f"/api/v1/candidates/{candidate_id}").json()

    scientific_keys = [
        "score", "rank", "severity", "priority", "geometry", "source_signal_ids",
        "detection_run_ids", "acquisition_ids", "metrics",
    ]
    for key in scientific_keys:
        assert before[key] == after1[key], f"Scientific field {key} changed on first review!"
        assert before[key] == after2[key], f"Scientific field {key} changed on revision!"


def test_foreign_key_integrity_and_protection_against_orphan_reviews():
    candidate_id = setup_candidate()

    # Save review
    client.patch(
        f"/api/v1/candidates/{candidate_id}/review",
        json={"decision": "accepted"},
    )

    with connection() as db:
        # Check SQLite foreign key enforcement
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []

        # Attempting to delete candidate with existing review should be RESTRICTED
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("DELETE FROM candidates WHERE candidate_id = ?", (candidate_id,))

        # Attempting to insert review for non-existent candidate directly into table should fail foreign key
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO candidate_reviews (candidate_id, decision, note, created_at, updated_at) VALUES ('fake-candidate', 'accepted', null, 'now', 'now')"
            )


def test_stale_and_invalid_candidate_provenance_protection():
    candidate_id = setup_candidate()

    # Corrupt the upstream detection run link in candidate
    with connection() as db:
        db.execute("UPDATE candidates SET detection_run_ids_json = '[999]' WHERE candidate_id = ?", (candidate_id,))
        db.commit()

    # Saving review must fail explicitly with provenance error and NOT attach review
    res = client.patch(
        f"/api/v1/candidates/{candidate_id}/review",
        json={"decision": "accepted"},
    )
    assert res.status_code == 502  # CandidateEvidenceError
    assert res.json()["code"] == "CandidateEvidenceError"

    # Verify no review record was saved
    with connection() as db:
        review_row = db.execute("SELECT 1 FROM candidate_reviews WHERE candidate_id = ?", (candidate_id,)).fetchone()
        assert review_row is None


def test_quality_limited_and_unavailable_evidence_can_be_reviewed_without_altering_quality():
    candidate_id = setup_candidate()

    # Mark observation as valid_unusable (quality limited)
    with connection() as db:
        db.execute(
            "UPDATE imagery_acquisitions SET observation_state = 'valid_unusable', quality_reason = 'cloud_cover_high' WHERE id = 1"
        )
        db.commit()

    before_cand = client.get(f"/api/v1/candidates/{candidate_id}").json()

    # Analyst can record a decision
    res = client.patch(
        f"/api/v1/candidates/{candidate_id}/review",
        json={"decision": "investigate", "note": "Reviewed despite quality limitation"},
    )
    assert res.status_code == 200
    assert res.json()["decision"] == "investigate"

    after_cand = client.get(f"/api/v1/candidates/{candidate_id}").json()
    assert before_cand["metrics"] == after_cand["metrics"]
    assert before_cand["score"] == after_cand["score"]
    assert before_cand["severity"] == after_cand["severity"]

    # Verify quality fields in database are untouched
    with connection() as db:
        acq_state = db.execute("SELECT observation_state FROM imagery_acquisitions WHERE id = 1").fetchone()[0]
        assert acq_state == "valid_unusable"


def test_candidate_reviews_is_sole_authoritative_source_over_stale_legacy_field():
    candidate_id = setup_candidate()

    # Intentionally corrupt/set candidates.review_state to a stale non-null value ('accepted')
    # while ensuring NO candidate_reviews row exists
    with connection() as db:
        db.execute("DELETE FROM candidate_reviews WHERE candidate_id = ?", (candidate_id,))
        db.execute("UPDATE candidates SET review_state = 'accepted' WHERE candidate_id = ?", (candidate_id,))
        db.commit()

        # Confirm DB state: candidate_reviews row absent, candidates.review_state is 'accepted'
        assert db.execute("SELECT 1 FROM candidate_reviews WHERE candidate_id = ?", (candidate_id,)).fetchone() is None
        raw_state = db.execute("SELECT review_state FROM candidates WHERE candidate_id = ?", (candidate_id,)).fetchone()[0]
        assert raw_state == "accepted"

    # 1. Authoritative review endpoint GET /review must report "unreviewed"
    review_res = client.get(f"/api/v1/candidates/{candidate_id}/review")
    assert review_res.status_code == 200
    assert review_res.json()["decision"] == "unreviewed"
    assert review_res.json()["created_at"] is None
    assert review_res.json()["updated_at"] is None
    assert review_res.json()["note"] is None

    # 2. Candidate detail endpoint GET /candidates/{id} must report "unreviewed"
    cand_res = client.get(f"/api/v1/candidates/{candidate_id}")
    assert cand_res.status_code == 200
    assert cand_res.json()["review_state"] == "unreviewed"

    # 3. Candidate listing endpoint GET /candidates must report "unreviewed"
    list_res = client.get("/api/v1/candidates")
    assert list_res.status_code == 200
    matched = [c for c in list_res.json()["candidates"] if c["candidate_id"] == candidate_id]
    assert len(matched) == 1
    assert matched[0]["review_state"] == "unreviewed"

    # 4. Filter by review_state=accepted must NOT match this candidate
    filter_acc_res = client.get("/api/v1/candidates?review_state=accepted")
    assert filter_acc_res.status_code == 200
    acc_ids = [c["candidate_id"] for c in filter_acc_res.json()["candidates"]]
    assert candidate_id not in acc_ids

    # 5. Filter by review_state=unreviewed MUST match this candidate
    filter_unrev_res = client.get("/api/v1/candidates?review_state=unreviewed")
    assert filter_unrev_res.status_code == 200
    unrev_ids = [c["candidate_id"] for c in filter_unrev_res.json()["candidates"]]
    assert candidate_id in unrev_ids

