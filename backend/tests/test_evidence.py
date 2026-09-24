from __future__ import annotations

import json
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
    for acquisition_id in (1, 2):
        path = tmp_path / f"{acquisition_id}.tif"
        with rasterio.open(path, "w", driver="GTiff", width=3, height=3, count=2, dtype="uint16", crs="EPSG:4326", transform=from_origin(77, 15, 1, 1), nodata=0) as dataset:
            dataset.write(np.full((3, 3), acquisition_id * 100, dtype="uint16"), 1)
            dataset.write(np.full((3, 3), acquisition_id * 200, dtype="uint16"), 2)
        raster_paths.append(path)
    with connection() as db:
        db.execute("INSERT INTO aoi (id, geometry_json, created_at, updated_at) VALUES (1, ?, '2024-01-01T00:00:00+00:00', '2024-01-01T00:00:00+00:00')", (json.dumps({"type": "Polygon", "coordinates": [[[76, 11], [80, 11], [80, 16], [76, 16], [76, 11]]]}),))
        for acquisition_id, path in zip((1, 2), raster_paths):
            db.execute("INSERT INTO imagery_acquisitions (id, aoi_id, requested_start_datetime, requested_end_datetime, item_id, collection, acquisition_datetime, assets_json, prepared_path, raster_metadata_json, source_metadata_json, created_at) VALUES (?, 1, '2024-01-01T00:00:00+00:00', '2024-01-02T00:00:00+00:00', ?, 'sentinel-2-l2a', ?, '[]', ?, ?, ?, '2024-01-01T00:00:00+00:00')", (acquisition_id, f"S2_{acquisition_id}", f"2024-0{acquisition_id}-01T00:00:00+00:00", str(path), json.dumps({"crs": "EPSG:4326", "transform": [1, 0, 77, 0, -1, 15], "width": 3, "height": 3, "resolution": [1, 1], "bounds": [77, 12, 80, 15], "count": 2, "dtype": "uint16", "nodata": 0}), json.dumps({"eo:cloud_cover": acquisition_id})))
        db.execute("INSERT INTO temporal_analyses (id, iou_threshold, detector_run_count, observation_count, temporal_span_days, state, created_at) VALUES (1, 0.25, 1, 2, 1, 'transient', '2024-02-01T00:00:00+00:00')")
        db.execute("INSERT INTO detection_runs (id, before_acquisition_id, after_acquisition_id, detector_version, threshold, min_region_pixels, region_count, changed_pixel_count, total_changed_area_m2, created_at) VALUES (1, 1, 2, 'test', 0.2, 1, 1, 4, 4, '2024-02-01T00:00:00+00:00')")
        db.execute("INSERT INTO raw_change_regions (id, run_id, geometry_json, pixel_count, area_m2, mean_change_signal, max_change_signal) VALUES (1, 1, ?, 4, 4, 0.3, 0.4)", (json.dumps({"type": "Polygon", "coordinates": [[[77.4, 12.8], [77.5, 12.8], [77.5, 12.9], [77.4, 12.9], [77.4, 12.8]]]}),))
        db.execute("INSERT INTO temporal_signals (analysis_id, signal_id, geometry_json, support_count, interval_count, persistence_ratio, recurrence_count, transient_interval_count, temporal_consistency, matched_region_coverage, first_change_datetime, last_supporting_datetime, state, source_region_ids_json, detection_run_ids_json, acquisition_ids_json) VALUES (1, 1, ?, 1, 1, 1, 0, 0, 1, 1, '2024-01-01T00:00:00+00:00', '2024-02-01T00:00:00+00:00', 'transient', '[1]', '[1]', '[1,2]')", (json.dumps({"type": "Polygon", "coordinates": [[[77.4, 12.8], [77.5, 12.8], [77.5, 12.9], [77.4, 12.9], [77.4, 12.8]]]}),))
        candidate_geometry = json.dumps({"type": "Polygon", "coordinates": [[[77.4, 12.8], [77.5, 12.8], [77.5, 12.9], [77.4, 12.9], [77.4, 12.8]]]})
        candidate_metrics = json.dumps({"support_count": 1, "interval_count": 1, "persistence_ratio": 1, "recurrence_count": 0, "transient_interval_count": 0, "temporal_consistency": 1, "matched_region_coverage": 1, "first_change_datetime": "2024-01-01T00:00:00+00:00", "last_supporting_datetime": "2024-02-01T00:00:00+00:00", "temporal_state": "transient", "score_components": {"persistence_ratio": 1, "temporal_consistency": 1, "matched_region_coverage": 1}})
        db.execute(
            "INSERT INTO candidates VALUES ('analysis-1-signal-1', 1, 1, ?, 3, 1, 'low', 'normal', 'unreviewed', '[1]', '[1]', '[1,2]', ?, '2024-02-01T00:00:00+00:00', '2024-02-01T00:00:00+00:00')",
            (candidate_geometry, candidate_metrics),
        )
        db.commit()
    yield
    object.__setattr__(settings, "database_path", original_database)


def test_evidence_resolves_provenance_and_renders_both_rasters():
    response = client.get("/api/v1/candidates/analysis-1-signal-1/evidence")
    assert response.status_code == 200
    payload = response.json()
    assert payload["temporal"]["state"] == "transient"
    assert [(item["before_acquisition_id"], item["after_acquisition_id"]) for item in payload["intervals"]] == [(1, 2)]
    for acquisition_id in (1, 2):
        preview = client.get(f"/api/v1/candidates/analysis-1-signal-1/evidence/acquisitions/{acquisition_id}/preview")
        assert preview.status_code == 200
        assert preview.headers["content-type"] == "image/png"
        assert preview.content.startswith(b"\x89PNG")


def test_missing_candidate_is_explicit():
    response = client.get("/api/v1/candidates/missing/evidence")
    assert response.status_code == 404
    assert response.json()["code"] == "CandidateNotFoundError"


def test_missing_raster_is_evidence_unavailable():
    with connection() as db:
        db.execute("UPDATE imagery_acquisitions SET prepared_path = 'missing.tif' WHERE id = 1")
        db.commit()
    response = client.get("/api/v1/candidates/analysis-1-signal-1/evidence")
    assert response.status_code == 404
    assert response.json()["code"] == "EvidenceUnavailableError"


def test_preview_rejects_candidate_outside_the_raster_extent():
    with connection() as db:
        db.execute(
            "UPDATE candidates SET geometry_json = ? WHERE candidate_id = 'analysis-1-signal-1'",
            (json.dumps({
                "type": "Polygon",
                "coordinates": [[[50.0, 10.0], [51.0, 10.0], [51.0, 11.0], [50.0, 11.0], [50.0, 10.0]]],
            }),),
        )
        db.commit()
    response = client.get("/api/v1/candidates/analysis-1-signal-1/evidence/acquisitions/1/preview")
    assert response.status_code in {404, 422, 502}
    assert response.json()["code"] in {"EvidenceUnavailableError", "CandidateEvidenceError", "EvidenceRasterError"}


def test_database_uses_native_evidence_indexes_for_current_workload():
    with connection() as db:
        temporal_signal_plan = db.execute(
            "EXPLAIN QUERY PLAN SELECT signal_id FROM temporal_signals WHERE analysis_id = 1 AND signal_id = 1"
        ).fetchall()
        temporal_observation_plan = db.execute(
            "EXPLAIN QUERY PLAN SELECT acquisition_id FROM temporal_observations WHERE analysis_id = 1 ORDER BY sequence_index"
        ).fetchall()
    assert any("sqlite_autoindex_temporal_signals_1" in str(row) for row in temporal_signal_plan)
    assert any("sqlite_autoindex_temporal_observations_1" in str(row) for row in temporal_observation_plan)