from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import numpy as np
import pytest
import rasterio
from fastapi.testclient import TestClient
from rasterio.transform import from_origin

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
            "explanation": {
                "summary": "High-confidence persistent vegetation loss triage candidate.",
                "positive_factors": ["High temporal persistence (1.00)", "High temporal consistency (0.95)"],
                "limiting_factors": [],
                "severity_rationale": "Total changed area of 800.0 m2 exceeds medium threshold.",
                "priority_rationale": "High priority due to 1.00 persistence and 0.725 triage score.",
            },
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


def test_valid_json_export():
    """Verify machine-readable JSON export with authoritative persisted values and portable provenance."""
    res = client.get("/api/v1/candidates/analysis-1-signal-1/export?format=json")
    assert res.status_code == 200, res.text
    assert res.headers["content-type"] == "application/json"
    assert 'filename="terrawatch_investigation_analysis-1-signal-1.json"' in res.headers["content-disposition"]

    data = res.json()

    # Stable format version
    assert data["export_version"] == "terrawatch-investigation-v1"
    assert "generated_at" in data

    # Identity
    assert data["candidate_id"] == "analysis-1-signal-1"
    assert data["analysis_id"] == 1
    assert data["signal_id"] == 1
    assert data["aoi_id"] == 1
    assert data["priority"] == "high"
    assert data["severity"] == "medium"
    assert data["score"] == 0.725
    assert data["rank"] == 1

    # Centroid
    assert "longitude" in data["centroid"]
    assert "latitude" in data["centroid"]

    # Authoritative detection summary
    det = data["detection_summary"]
    assert det["detector_version"] == "ndvi-diff-v1"
    assert det["threshold"] == 0.20
    assert det["changed_pixel_count"] == 8
    assert det["total_changed_area_m2"] == 800.0
    assert det["mean_change_signal"] == 0.45
    assert det["max_change_signal"] == 0.65
    assert det["changed_pixel_percentage"] == 50.0

    # Authoritative temporal evidence
    temp = data["temporal_evidence"]
    assert temp["state"] == "persistent"
    assert temp["persistence_ratio"] == 1.0
    assert temp["temporal_consistency"] == 0.95
    assert temp["quality_support"] == 0.90

    # Before and After observations
    before = data["before_observation"]
    after = data["after_observation"]
    assert before["acquisition_id"] == 1
    assert after["acquisition_id"] == 2
    assert before["acquisition_id"] != after["acquisition_id"]
    assert before["item_id"] == "S2_ITEM_1"
    assert after["item_id"] == "S2_ITEM_2"
    assert before["observation_state"] == "usable"
    assert after["observation_state"] == "usable"

    # Quality & Provenance
    assert not data["observation_quality"]["is_quality_limited"]
    prov = data["provenance"]
    assert prov["candidate_id"] == "analysis-1-signal-1"
    assert prov["karnataka_contained"] is True
    assert prov["before_acquisition_id"] == 1
    assert prov["after_acquisition_id"] == 2

    # Authoritative review: no row in candidate_reviews -> unreviewed
    assert data["analyst_review"]["decision"] == "unreviewed"
    assert data["analyst_review"]["note"] is None


def test_valid_pdf_export():
    """Verify human-readable PDF export with valid PDF structure, text markers, reasonable size, and bounded display."""
    res = client.get("/api/v1/candidates/analysis-1-signal-1/export?format=pdf")
    assert res.status_code == 200, res.text
    assert res.headers["content-type"] == "application/pdf"
    assert 'filename="terrawatch_investigation_analysis-1-signal-1.pdf"' in res.headers["content-disposition"]

    pdf_bytes = res.content
    # Valid PDF structure
    assert pdf_bytes.startswith(b"%PDF-")
    assert len(pdf_bytes) > 5000  # Non-empty and substantial
    assert len(pdf_bytes) < 3_000_000  # Well within reasonable size (<3 MB)

    # Check textual markers in PDF binary stream
    pdf_text = pdf_bytes.decode("latin1", errors="ignore")
    assert "TerraWatch V2" in pdf_text
    assert "analysis-1-signal-1" in pdf_text
    assert "Karnataka" in pdf_text


def test_default_export_format_is_pdf():
    """Omitting format query parameter defaults to PDF."""
    res = client.get("/api/v1/candidates/analysis-1-signal-1/export")
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/pdf"
    assert res.content.startswith(b"%PDF-")


def test_nonexistent_candidate_returns_404():
    """Requesting export for a nonexistent candidate fails with 404."""
    res = client.get("/api/v1/candidates/analysis-999-signal-999/export?format=json")
    assert res.status_code == 404
    assert res.json()["code"] == "CandidateNotFoundError"

    res_pdf = client.get("/api/v1/candidates/analysis-999-signal-999/export?format=pdf")
    assert res_pdf.status_code == 404


def test_authoritative_review_decision_and_note_exported():
    """When a review row exists in candidate_reviews, export faithfully reflects decision and note."""
    with connection() as db:
        db.execute(
            """INSERT INTO candidate_reviews (candidate_id, decision, note, created_at, updated_at)
               VALUES ('analysis-1-signal-1', 'accepted', 'Field inspection confirmed tree loss',
                       '2024-03-01T10:00:00+00:00', '2024-03-01T12:00:00+00:00')"""
        )
        db.commit()

    # JSON export
    res_json = client.get("/api/v1/candidates/analysis-1-signal-1/export?format=json")
    assert res_json.status_code == 200
    review_data = res_json.json()["analyst_review"]
    assert review_data["decision"] == "accepted"
    assert review_data["note"] == "Field inspection confirmed tree loss"
    assert review_data["created_at"] == "2024-03-01T10:00:00+00:00"
    assert review_data["updated_at"] == "2024-03-01T12:00:00+00:00"

    # PDF export
    res_pdf = client.get("/api/v1/candidates/analysis-1-signal-1/export?format=pdf")
    assert res_pdf.status_code == 200
    pdf_text = res_pdf.content.decode("latin1", errors="ignore")
    assert "ACCEPTED" in pdf_text
    assert "Field inspection confirmed tree loss" in pdf_text


def test_no_fallback_to_candidates_review_state():
    """Strictly use candidate_reviews as source of truth. Never fall back to candidates.review_state."""
    with connection() as db:
        # Set candidates.review_state to investigate, but ensure candidate_reviews has NO row
        db.execute("UPDATE candidates SET review_state = 'investigate' WHERE candidate_id = 'analysis-1-signal-1'")
        db.execute("DELETE FROM candidate_reviews WHERE candidate_id = 'analysis-1-signal-1'")
        db.commit()

    res = client.get("/api/v1/candidates/analysis-1-signal-1/export?format=json")
    assert res.status_code == 200
    # Must be "unreviewed", NOT "investigate"
    assert res.json()["analyst_review"]["decision"] == "unreviewed"


def test_export_is_strictly_read_only():
    """Exporting JSON or PDF causes zero database mutations and leaves all tables untouched."""
    with connection() as db:
        c_count = db.execute("SELECT count(*) FROM candidates").fetchone()[0]
        r_count = db.execute("SELECT count(*) FROM candidate_reviews").fetchone()[0]
        d_count = db.execute("SELECT count(*) FROM detection_runs").fetchone()[0]
        t_count = db.execute("SELECT count(*) FROM temporal_analyses").fetchone()[0]
        orig_candidate = db.execute("SELECT score, rank, priority, severity, review_state, metrics_json FROM candidates WHERE candidate_id = 'analysis-1-signal-1'").fetchone()

    # Perform multiple exports
    res1 = client.get("/api/v1/candidates/analysis-1-signal-1/export?format=json")
    assert res1.status_code == 200
    res2 = client.get("/api/v1/candidates/analysis-1-signal-1/export?format=pdf")
    assert res2.status_code == 200

    # Verify database counts and candidate fields remain strictly identical
    with connection() as db:
        assert db.execute("SELECT count(*) FROM candidates").fetchone()[0] == c_count
        assert db.execute("SELECT count(*) FROM candidate_reviews").fetchone()[0] == r_count
        assert db.execute("SELECT count(*) FROM detection_runs").fetchone()[0] == d_count
        assert db.execute("SELECT count(*) FROM temporal_analyses").fetchone()[0] == t_count
        new_candidate = db.execute("SELECT score, rank, priority, severity, review_state, metrics_json FROM candidates WHERE candidate_id = 'analysis-1-signal-1'").fetchone()
        assert new_candidate == orig_candidate

        # PRAGMA integrity checks
        integrity = db.execute("PRAGMA integrity_check").fetchall()
        assert integrity == [("ok",)]
        fks = db.execute("PRAGMA foreign_key_check").fetchall()
        assert fks == []


def test_no_filesystem_path_leakage_in_json_or_pdf():
    """Verify that internal backend filesystem paths (e.g. .tif disk paths, drive letters, storage folders) are never leaked."""
    res_json = client.get("/api/v1/candidates/analysis-1-signal-1/export?format=json")
    assert res_json.status_code == 200
    json_text = res_json.text

    # No raster/mask/display filesystem paths in JSON
    assert ".tif" not in json_text
    assert "pytest" not in json_text
    assert "\\AppData\\" not in json_text
    assert "E:\\" not in json_text
    assert "C:\\" not in json_text
    assert "/tmp/" not in json_text

    res_pdf = client.get("/api/v1/candidates/analysis-1-signal-1/export?format=pdf")
    assert res_pdf.status_code == 200
    pdf_text = res_pdf.content.decode("latin1", errors="ignore")

    assert ".tif" not in pdf_text
    assert "pytest" not in pdf_text
    assert "\\AppData\\" not in pdf_text
    assert "/tmp/" not in pdf_text


def test_stale_or_missing_provenance_fails_explicitly():
    """If required provenance raster is missing on disk or corrupted, export must FAIL explicitly."""
    # Delete the physical raster file for acquisition 1
    with connection() as db:
        r_path = db.execute("SELECT prepared_path FROM imagery_acquisitions WHERE id = 1").fetchone()[0]
    Path(r_path).unlink(missing_ok=True)

    res = client.get("/api/v1/candidates/analysis-1-signal-1/export?format=json")
    # Must fail explicitly, not generate misleading report
    assert res.status_code in (400, 404, 500)
    assert res.json()["code"] in ("EvidenceUnavailableError", "CandidateEvidenceError")


def test_before_and_after_distinctness_validation(monkeypatch):
    """If provenance has identical Before and After acquisition IDs, export fails explicitly."""
    from app import export

    original_get_evidence = export.get_candidate_evidence

    def mock_get_evidence(cand_id):
        evidence = original_get_evidence(cand_id)
        evidence.intervals[0].after_acquisition_id = evidence.intervals[0].before_acquisition_id
        return evidence

    monkeypatch.setattr(export, "get_candidate_evidence", mock_get_evidence)
    res = client.get("/api/v1/candidates/analysis-1-signal-1/export?format=json")
    assert res.status_code == 502
    assert res.json()["code"] == "CandidateEvidenceError"
    assert "identical Before and After" in res.json()["message"]


def test_invalid_format_parameter_returns_422():
    """Unsupported format query parameter returns 422 validation error."""
    res = client.get("/api/v1/candidates/analysis-1-signal-1/export?format=docx")
    assert res.status_code == 422
    assert res.json()["code"] == "ValidationError"


def test_quality_limited_evidence_explicitly_represented():
    """Observations with quality limitations are explicitly marked, not concealed."""
    with connection() as db:
        db.execute("UPDATE imagery_acquisitions SET observation_state = 'valid_unusable', quality_reason = 'Cloud cover 65%' WHERE id = 2")
        db.commit()

    res = client.get("/api/v1/candidates/analysis-1-signal-1/export?format=json")
    assert res.status_code == 200
    data = res.json()
    assert data["observation_quality"]["is_quality_limited"] is True
    assert any("valid_unusable" in r for r in data["observation_quality"]["quality_limitation_reasons"])
    assert data["after_observation"]["observation_state"] == "valid_unusable"


def test_deterministic_content_apart_from_generation_timestamp():
    """Calling export twice returns deterministic content except for generated_at."""
    res1 = client.get("/api/v1/candidates/analysis-1-signal-1/export?format=json").json()
    res2 = client.get("/api/v1/candidates/analysis-1-signal-1/export?format=json").json()

    del res1["generated_at"]
    del res2["generated_at"]
    assert res1 == res2
