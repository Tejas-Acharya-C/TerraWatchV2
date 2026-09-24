from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from app.config import settings
from app.db import connection, initialize_database


@pytest.fixture
def isolated_database(tmp_path: Path):
    original_database = settings.database_path
    object.__setattr__(settings, "database_path", tmp_path / "phase_a.db")
    initialize_database()
    yield
    object.__setattr__(settings, "database_path", original_database)


def _create_aoi(db: sqlite3.Connection, aoi_id: int) -> None:
    db.execute(
        "INSERT INTO aoi (id, geometry_json, created_at, updated_at) VALUES (?, '{}', 'now', 'now')",
        (aoi_id,),
    )


def _create_acquisition(db: sqlite3.Connection, acquisition_id: int, aoi_id: int) -> None:
    db.execute(
        """INSERT INTO imagery_acquisitions (
            id, aoi_id, requested_start_datetime, requested_end_datetime,
            item_id, collection, acquisition_datetime, assets_json,
            prepared_path, raster_metadata_json, source_metadata_json, created_at
        ) VALUES (?, ?, '2024-01-01', '2024-01-02', ?, 'test', '2024-01-01',
                  '[]', 'unused.tif', '{}', '{}', 'now')""",
        (acquisition_id, aoi_id, f"item-{acquisition_id}"),
    )


def test_foreign_keys_are_enabled_and_parent_graph_is_declared(isolated_database):
    with connection() as db:
        assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        foreign_key_tables = {
            table: {row[2] for row in db.execute(f"PRAGMA foreign_key_list({table})")}
            for table in (
                "imagery_acquisitions",
                "detection_runs",
                "raw_change_regions",
                "temporal_observations",
                "temporal_relationships",
                "temporal_signals",
                "candidates",
            )
        }
    assert foreign_key_tables["imagery_acquisitions"] == {"aoi"}
    assert foreign_key_tables["detection_runs"] == {"imagery_acquisitions"}
    assert foreign_key_tables["raw_change_regions"] == {"detection_runs"}
    assert foreign_key_tables["temporal_observations"] == {"temporal_analyses", "imagery_acquisitions"}
    assert foreign_key_tables["temporal_relationships"] == {"temporal_analyses", "imagery_acquisitions", "detection_runs"}
    assert foreign_key_tables["temporal_signals"] == {"temporal_analyses"}
    assert foreign_key_tables["candidates"] == {"temporal_analyses", "temporal_signals"}


def test_invalid_parent_references_and_deletion_are_rejected(isolated_database):
    with connection() as db:
        with pytest.raises(sqlite3.IntegrityError):
            _create_acquisition(db, 1, 99)
        _create_aoi(db, 1)
        _create_acquisition(db, 1, 1)
        _create_acquisition(db, 2, 1)
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO detection_runs (before_acquisition_id, after_acquisition_id, detector_version, threshold, min_region_pixels, region_count, changed_pixel_count, total_changed_area_m2, created_at) VALUES (1, 99, 'test', 0.2, 1, 0, 0, 0, 'now')"
            )
        db.execute(
            "INSERT INTO temporal_analyses (id, iou_threshold, detector_run_count, observation_count, temporal_span_days, state, created_at) VALUES (1, 0.25, 0, 0, 0, 'insufficient_history', 'now')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO temporal_observations (analysis_id, acquisition_id, sequence_index) VALUES (1, 99, 0)"
            )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("DELETE FROM aoi WHERE id = 1")


def test_valid_provenance_graph_persists(isolated_database):
    with connection() as db:
        _create_aoi(db, 1)
        _create_acquisition(db, 1, 1)
        _create_acquisition(db, 2, 1)
        db.execute(
            "INSERT INTO detection_runs (id, before_acquisition_id, after_acquisition_id, detector_version, threshold, min_region_pixels, region_count, changed_pixel_count, total_changed_area_m2, created_at) VALUES (1, 1, 2, 'test', 0.2, 1, 0, 0, 0, 'now')"
        )
        db.execute(
            "INSERT INTO raw_change_regions (id, run_id, geometry_json, pixel_count, area_m2, mean_change_signal, max_change_signal) VALUES (1, 1, ?, 1, 1, 0.1, 0.2)",
            (json.dumps({"type": "Polygon", "coordinates": []}),),
        )
        db.execute(
            "INSERT INTO temporal_analyses (id, iou_threshold, detector_run_count, observation_count, temporal_span_days, state, created_at) VALUES (1, 0.25, 1, 2, 1, 'no_temporal_signal', 'now')"
        )
        db.execute("INSERT INTO temporal_observations VALUES (1, 1, 0)")
        db.execute("INSERT INTO temporal_observations VALUES (1, 2, 1)")
        db.execute("INSERT INTO temporal_relationships (analysis_id, before_acquisition_id, after_acquisition_id, detection_run_id, region_count, changed_pixel_count) VALUES (1, 1, 2, 1, 1, 1)")
        db.execute(
            "INSERT INTO temporal_signals (analysis_id, signal_id, geometry_json, support_count, interval_count, persistence_ratio, recurrence_count, transient_interval_count, temporal_consistency, matched_region_coverage, first_change_datetime, last_supporting_datetime, state, detection_run_ids_json, acquisition_ids_json) VALUES (1, 1, ?, 1, 1, 1, 0, 0, 1, 1, '2024-01-01', '2024-01-02', 'isolated', '[1]', '[1,2]')",
            (json.dumps({"type": "Polygon", "coordinates": []}),),
        )
        db.execute(
            "INSERT INTO candidates (candidate_id, analysis_id, signal_id, geometry_json, score, severity, priority, review_state, source_signal_ids_json, detection_run_ids_json, acquisition_ids_json, metrics_json, created_at, updated_at) VALUES ('candidate-1', 1, 1, ?, 1, 'low', 'low', 'unreviewed', '[1]', '[1]', '[1,2]', '{}', 'now', 'now')",
            (json.dumps({"type": "Polygon", "coordinates": []}),),
        )
        db.commit()
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
