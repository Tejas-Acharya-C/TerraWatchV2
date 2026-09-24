from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from app.config import settings


_SCHEMA_TABLES = (
    "aoi",
    "imagery_acquisitions",
    "detection_runs",
    "raw_change_regions",
    "temporal_analyses",
    "temporal_observations",
    "temporal_relationships",
    "temporal_signals",
    "candidates",
)


def database_path() -> Path:
    path = settings.database_path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    db_connection = sqlite3.connect(database_path())
    try:
        db_connection.execute("PRAGMA foreign_keys = ON")
        if db_connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
            raise RuntimeError("SQLite foreign-key enforcement could not be enabled")
        yield db_connection
    finally:
        db_connection.close()


def _has_foreign_keys(db_connection: sqlite3.Connection) -> bool:
    return any(
        db_connection.execute(f"PRAGMA foreign_key_list({table})").fetchone()
        for table in ("imagery_acquisitions", "detection_runs", "temporal_relationships")
    )


def _create_phase_a_tables(db_connection: sqlite3.Connection, suffix: str = "") -> None:
    db_connection.execute(f"""
        CREATE TABLE aoi{suffix} (
            id INTEGER PRIMARY KEY,
            geometry_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    db_connection.execute(f"""
        CREATE TABLE imagery_acquisitions{suffix} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            aoi_id INTEGER NOT NULL,
            requested_start_datetime TEXT NOT NULL,
            requested_end_datetime TEXT NOT NULL,
            item_id TEXT NOT NULL,
            collection TEXT NOT NULL,
            acquisition_datetime TEXT NOT NULL,
            assets_json TEXT NOT NULL,
            prepared_path TEXT NOT NULL,
            raster_metadata_json TEXT NOT NULL,
            source_metadata_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            display_path TEXT,
            display_metadata_json TEXT,
            visualization_version TEXT,
            FOREIGN KEY (aoi_id) REFERENCES aoi(id) ON DELETE RESTRICT
        )
    """)
    db_connection.execute(f"""
        CREATE TABLE detection_runs{suffix} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            before_acquisition_id INTEGER NOT NULL,
            after_acquisition_id INTEGER NOT NULL,
            detector_version TEXT NOT NULL,
            threshold REAL NOT NULL,
            min_region_pixels INTEGER NOT NULL,
            region_count INTEGER NOT NULL,
            changed_pixel_count INTEGER NOT NULL,
            total_changed_area_m2 REAL NOT NULL,
            created_at TEXT NOT NULL,
            quality_mask_used INTEGER NOT NULL DEFAULT 0,
            quality_processing_version TEXT NOT NULL DEFAULT 'legacy-unassessed',
            quality_valid_pixel_count INTEGER NOT NULL DEFAULT 0,
            excluded_pixel_count INTEGER NOT NULL DEFAULT 0,
            excluded_cloud_pixel_count INTEGER NOT NULL DEFAULT 0,
            excluded_shadow_pixel_count INTEGER NOT NULL DEFAULT 0,
            raw_changed_pixel_count INTEGER NOT NULL DEFAULT 0,
            filtered_changed_pixel_count INTEGER NOT NULL DEFAULT 0,
            changed_pixel_percentage REAL NOT NULL DEFAULT 0,
            largest_region_area_m2 REAL NOT NULL DEFAULT 0,
            mean_region_area_m2 REAL NOT NULL DEFAULT 0,
            median_region_area_m2 REAL NOT NULL DEFAULT 0,
            mean_change_signal REAL NOT NULL DEFAULT 0,
            max_change_signal REAL NOT NULL DEFAULT 0,
            FOREIGN KEY (before_acquisition_id) REFERENCES imagery_acquisitions(id) ON DELETE RESTRICT,
            FOREIGN KEY (after_acquisition_id) REFERENCES imagery_acquisitions(id) ON DELETE RESTRICT,
            CHECK (before_acquisition_id <> after_acquisition_id)
        )
    """)
    db_connection.execute(f"""
        CREATE TABLE raw_change_regions{suffix} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            geometry_json TEXT NOT NULL,
            pixel_count INTEGER NOT NULL,
            area_m2 REAL NOT NULL,
            mean_change_signal REAL NOT NULL,
            max_change_signal REAL NOT NULL,
            FOREIGN KEY (run_id) REFERENCES detection_runs(id) ON DELETE RESTRICT
        )
    """)
    db_connection.execute(f"""
        CREATE TABLE temporal_analyses{suffix} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            iou_threshold REAL NOT NULL,
            detector_run_count INTEGER NOT NULL,
            observation_count INTEGER NOT NULL,
            temporal_span_days REAL NOT NULL,
            state TEXT NOT NULL,
            created_at TEXT NOT NULL,
            usable_observation_count INTEGER NOT NULL DEFAULT 0,
            usable_interval_count INTEGER NOT NULL DEFAULT 0,
            quality_support REAL NOT NULL DEFAULT 0,
            quality_aggregation_method TEXT NOT NULL DEFAULT 'not_assessed',
            seasonal_interpretation TEXT NOT NULL DEFAULT 'insufficient_temporal_evidence'
        )
    """)
    db_connection.execute(f"""
        CREATE TABLE temporal_observations{suffix} (
            analysis_id INTEGER NOT NULL,
            acquisition_id INTEGER NOT NULL,
            sequence_index INTEGER NOT NULL,
            PRIMARY KEY (analysis_id, acquisition_id),
            FOREIGN KEY (analysis_id) REFERENCES temporal_analyses(id) ON DELETE RESTRICT,
            FOREIGN KEY (acquisition_id) REFERENCES imagery_acquisitions(id) ON DELETE RESTRICT
        )
    """)
    db_connection.execute(f"""
        CREATE TABLE temporal_relationships{suffix} (
            analysis_id INTEGER NOT NULL,
            before_acquisition_id INTEGER NOT NULL,
            after_acquisition_id INTEGER NOT NULL,
            detection_run_id INTEGER NOT NULL,
            region_count INTEGER NOT NULL,
            changed_pixel_count INTEGER NOT NULL,
            before_region_ids_json TEXT NOT NULL DEFAULT '[]',
            after_region_ids_json TEXT NOT NULL DEFAULT '[]',
            matched_region_pairs_json TEXT NOT NULL DEFAULT '[]',
            elapsed_days REAL NOT NULL DEFAULT 0,
            quality_support REAL NOT NULL DEFAULT 0,
            quality_state TEXT NOT NULL DEFAULT 'insufficient_quality_support',
            evaluated INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (analysis_id) REFERENCES temporal_analyses(id) ON DELETE RESTRICT,
            FOREIGN KEY (before_acquisition_id) REFERENCES imagery_acquisitions(id) ON DELETE RESTRICT,
            FOREIGN KEY (after_acquisition_id) REFERENCES imagery_acquisitions(id) ON DELETE RESTRICT,
            FOREIGN KEY (detection_run_id) REFERENCES detection_runs(id) ON DELETE RESTRICT
        )
    """)
    db_connection.execute(f"""
        CREATE TABLE temporal_signals{suffix} (
            analysis_id INTEGER NOT NULL,
            signal_id INTEGER NOT NULL,
            geometry_json TEXT NOT NULL,
            support_count INTEGER NOT NULL,
            interval_count INTEGER NOT NULL,
            persistence_ratio REAL NOT NULL,
            recurrence_count INTEGER NOT NULL,
            transient_interval_count INTEGER NOT NULL,
            temporal_consistency REAL NOT NULL,
            matched_region_coverage REAL NOT NULL,
            first_change_datetime TEXT NOT NULL,
            last_supporting_datetime TEXT NOT NULL,
            state TEXT NOT NULL,
            source_region_ids_json TEXT NOT NULL DEFAULT '[]',
            detection_run_ids_json TEXT NOT NULL DEFAULT '[]',
            acquisition_ids_json TEXT NOT NULL DEFAULT '[]',
            usable_interval_count INTEGER NOT NULL DEFAULT 0,
            supporting_interval_count INTEGER NOT NULL DEFAULT 0,
            quality_support REAL NOT NULL DEFAULT 0,
            onset_before_acquisition_id INTEGER,
            onset_after_acquisition_id INTEGER,
            onset_start_datetime TEXT,
            onset_end_datetime TEXT,
            seasonal_interpretation TEXT NOT NULL DEFAULT 'insufficient_temporal_evidence',
            interval_change_means_json TEXT NOT NULL DEFAULT '[]',
            interval_change_maxima_json TEXT NOT NULL DEFAULT '[]',
            PRIMARY KEY (analysis_id, signal_id),
            FOREIGN KEY (analysis_id) REFERENCES temporal_analyses(id) ON DELETE RESTRICT
        )
    """)
    db_connection.execute(f"""
        CREATE TABLE candidates{suffix} (
            candidate_id TEXT PRIMARY KEY,
            analysis_id INTEGER NOT NULL,
            signal_id INTEGER NOT NULL,
            geometry_json TEXT NOT NULL,
            score REAL NOT NULL,
            rank INTEGER NOT NULL DEFAULT 0,
            severity TEXT NOT NULL,
            priority TEXT NOT NULL,
            review_state TEXT NOT NULL,
            source_signal_ids_json TEXT NOT NULL,
            detection_run_ids_json TEXT NOT NULL,
            acquisition_ids_json TEXT NOT NULL,
            metrics_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (analysis_id, signal_id),
            FOREIGN KEY (analysis_id) REFERENCES temporal_analyses(id) ON DELETE RESTRICT,
            FOREIGN KEY (analysis_id, signal_id) REFERENCES temporal_signals(analysis_id, signal_id) ON DELETE RESTRICT
        )
    """)


def _migrate_to_phase_a_constraints(db_connection: sqlite3.Connection) -> None:
    if _has_foreign_keys(db_connection):
        return
    db_connection.commit()
    db_connection.execute("PRAGMA foreign_keys = OFF")
    try:
        db_connection.execute("BEGIN")
        _create_phase_a_tables(db_connection, "_phase_a")
        for table in _SCHEMA_TABLES:
            source_columns = {
                row[1]
                for row in db_connection.execute(f"PRAGMA table_info({table})")
            }
            target_columns = [
                row[1]
                for row in db_connection.execute(f"PRAGMA table_info({table}_phase_a)")
                if row[1] in source_columns
            ]
            columns = ", ".join(target_columns)
            db_connection.execute(
                f"INSERT INTO {table}_phase_a ({columns}) SELECT {columns} FROM {table}"
            )
        violations = []
        for table in _SCHEMA_TABLES:
            violations.extend(db_connection.execute(f"PRAGMA foreign_key_check({table}_phase_a)").fetchall())
        if violations:
            categories = ", ".join(sorted({f"{row[0]} -> {row[2]}" for row in violations}))
            raise RuntimeError(
                "Phase A migration refused; historical orphan records remain "
                f"({len(violations)} violations: {categories})"
            )
        for table in reversed(_SCHEMA_TABLES):
            db_connection.execute(f"DROP TABLE {table}")
        for table in _SCHEMA_TABLES:
            db_connection.execute(f"ALTER TABLE {table}_phase_a RENAME TO {table}")
        db_connection.execute(
            "CREATE UNIQUE INDEX idx_imagery_acquisitions_aoi_item "
            "ON imagery_acquisitions (aoi_id, item_id)"
        )
        db_connection.commit()
    except Exception:
        db_connection.rollback()
        raise
    finally:
        db_connection.execute("PRAGMA foreign_keys = ON")
    if not _has_foreign_keys(db_connection):
        raise RuntimeError("Phase A schema migration did not install foreign keys")


def _ensure_observation_quality_columns(db_connection: sqlite3.Connection) -> None:
    columns = {
        row[1]
        for row in db_connection.execute("PRAGMA table_info(imagery_acquisitions)")
    }
    definitions = {
        "observation_state": "TEXT NOT NULL DEFAULT 'legacy_unassessed'",
        "quality_reason": "TEXT NOT NULL DEFAULT 'quality_not_assessed'",
        "quality_metrics_json": "TEXT NOT NULL DEFAULT '{}'",
        "quality_mask_path": "TEXT",
        "processing_version": "TEXT NOT NULL DEFAULT 'legacy-unassessed-v0'",
        "masking_method": "TEXT NOT NULL DEFAULT 'not_assessed'",
        "quality_asset_id": "TEXT",
    }
    for column, definition in definitions.items():
        if column not in columns:
            db_connection.execute(
                f"ALTER TABLE imagery_acquisitions ADD COLUMN {column} {definition}"
            )


def _ensure_display_columns(db_connection: sqlite3.Connection) -> None:
    columns = {
        row[1]
        for row in db_connection.execute("PRAGMA table_info(imagery_acquisitions)")
    }
    definitions = {
        "display_path": "TEXT",
        "display_metadata_json": "TEXT",
        "visualization_version": "TEXT",
    }
    for column, definition in definitions.items():
        if column not in columns:
            db_connection.execute(
                f"ALTER TABLE imagery_acquisitions ADD COLUMN {column} {definition}"
            )


def _ensure_detection_diagnostic_columns(db_connection: sqlite3.Connection) -> None:
    columns = {
        row[1]
        for row in db_connection.execute("PRAGMA table_info(detection_runs)")
    }
    definitions = {
        "quality_mask_used": "INTEGER NOT NULL DEFAULT 0",
        "quality_processing_version": "TEXT NOT NULL DEFAULT 'legacy-unassessed'",
        "quality_valid_pixel_count": "INTEGER NOT NULL DEFAULT 0",
        "excluded_pixel_count": "INTEGER NOT NULL DEFAULT 0",
        "excluded_cloud_pixel_count": "INTEGER NOT NULL DEFAULT 0",
        "excluded_shadow_pixel_count": "INTEGER NOT NULL DEFAULT 0",
        "raw_changed_pixel_count": "INTEGER NOT NULL DEFAULT 0",
        "filtered_changed_pixel_count": "INTEGER NOT NULL DEFAULT 0",
        "changed_pixel_percentage": "REAL NOT NULL DEFAULT 0",
        "largest_region_area_m2": "REAL NOT NULL DEFAULT 0",
        "mean_region_area_m2": "REAL NOT NULL DEFAULT 0",
        "median_region_area_m2": "REAL NOT NULL DEFAULT 0",
        "mean_change_signal": "REAL NOT NULL DEFAULT 0",
        "max_change_signal": "REAL NOT NULL DEFAULT 0",
    }
    for column, definition in definitions.items():
        if column not in columns:
            db_connection.execute(
                f"ALTER TABLE detection_runs ADD COLUMN {column} {definition}"
            )


def _ensure_temporal_diagnostic_columns(db_connection: sqlite3.Connection) -> None:
    definitions_by_table = {
        "temporal_analyses": {
            "usable_observation_count": "INTEGER NOT NULL DEFAULT 0",
            "usable_interval_count": "INTEGER NOT NULL DEFAULT 0",
            "quality_support": "REAL NOT NULL DEFAULT 0",
            "quality_aggregation_method": "TEXT NOT NULL DEFAULT 'not_assessed'",
            "seasonal_interpretation": "TEXT NOT NULL DEFAULT 'insufficient_temporal_evidence'",
        },
        "temporal_relationships": {
            "elapsed_days": "REAL NOT NULL DEFAULT 0",
            "quality_support": "REAL NOT NULL DEFAULT 0",
            "quality_state": "TEXT NOT NULL DEFAULT 'insufficient_quality_support'",
            "evaluated": "INTEGER NOT NULL DEFAULT 0",
        },
        "temporal_signals": {
            "usable_interval_count": "INTEGER NOT NULL DEFAULT 0",
            "supporting_interval_count": "INTEGER NOT NULL DEFAULT 0",
            "quality_support": "REAL NOT NULL DEFAULT 0",
            "onset_before_acquisition_id": "INTEGER",
            "onset_after_acquisition_id": "INTEGER",
            "onset_start_datetime": "TEXT",
            "onset_end_datetime": "TEXT",
            "seasonal_interpretation": "TEXT NOT NULL DEFAULT 'insufficient_temporal_evidence'",
            "interval_change_means_json": "TEXT NOT NULL DEFAULT '[]'",
            "interval_change_maxima_json": "TEXT NOT NULL DEFAULT '[]'",
        },
    }
    for table, definitions in definitions_by_table.items():
        columns = {row[1] for row in db_connection.execute(f"PRAGMA table_info({table})")}
        for column, definition in definitions.items():
            if column not in columns:
                db_connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def initialize_database() -> None:
    with connection() as db_connection:
        aoi_exists = db_connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'aoi'"
        ).fetchone()
        if aoi_exists is not None:
            aoi_sql = db_connection.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'aoi'"
            ).fetchone()
            if aoi_sql is not None and "CHECK (id = 1)" in aoi_sql[0]:
                db_connection.execute("ALTER TABLE aoi RENAME TO aoi_singleton")
                db_connection.execute(
                    """
                    CREATE TABLE aoi (
                        id INTEGER PRIMARY KEY,
                        geometry_json TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                db_connection.execute(
                    """
                    INSERT INTO aoi (id, geometry_json, created_at, updated_at)
                    SELECT id, geometry_json, created_at, updated_at FROM aoi_singleton
                    """
                )
                db_connection.execute("DROP TABLE aoi_singleton")
        else:
            db_connection.execute(
                """
                CREATE TABLE IF NOT EXISTS aoi (
                    id INTEGER PRIMARY KEY,
                    geometry_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
        db_connection.execute(
            """
            CREATE TABLE IF NOT EXISTS imagery_acquisitions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                aoi_id INTEGER NOT NULL,
                requested_start_datetime TEXT NOT NULL,
                requested_end_datetime TEXT NOT NULL,
                item_id TEXT NOT NULL,
                collection TEXT NOT NULL,
                acquisition_datetime TEXT NOT NULL,
                assets_json TEXT NOT NULL,
                prepared_path TEXT NOT NULL,
                raster_metadata_json TEXT NOT NULL,
                source_metadata_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                display_path TEXT,
                display_metadata_json TEXT,
                visualization_version TEXT
            )
            """
        )
        db_connection.execute(
            """
            CREATE TABLE IF NOT EXISTS detection_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                before_acquisition_id INTEGER NOT NULL,
                after_acquisition_id INTEGER NOT NULL,
                detector_version TEXT NOT NULL,
                threshold REAL NOT NULL,
                min_region_pixels INTEGER NOT NULL,
                region_count INTEGER NOT NULL,
                changed_pixel_count INTEGER NOT NULL,
                total_changed_area_m2 REAL NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        db_connection.execute(
            """
            CREATE TABLE IF NOT EXISTS raw_change_regions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                geometry_json TEXT NOT NULL,
                pixel_count INTEGER NOT NULL,
                area_m2 REAL NOT NULL,
                mean_change_signal REAL NOT NULL,
                max_change_signal REAL NOT NULL
            )
            """
        )
        db_connection.execute(
            """
            CREATE TABLE IF NOT EXISTS temporal_analyses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                iou_threshold REAL NOT NULL,
                detector_run_count INTEGER NOT NULL,
                observation_count INTEGER NOT NULL,
                temporal_span_days REAL NOT NULL,
                state TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        db_connection.execute(
            """
            CREATE TABLE IF NOT EXISTS temporal_observations (
                analysis_id INTEGER NOT NULL,
                acquisition_id INTEGER NOT NULL,
                sequence_index INTEGER NOT NULL,
                PRIMARY KEY (analysis_id, acquisition_id)
            )
            """
        )
        db_connection.execute(
            """
            CREATE TABLE IF NOT EXISTS temporal_relationships (
                analysis_id INTEGER NOT NULL,
                before_acquisition_id INTEGER NOT NULL,
                after_acquisition_id INTEGER NOT NULL,
                detection_run_id INTEGER NOT NULL,
                region_count INTEGER NOT NULL,
                changed_pixel_count INTEGER NOT NULL
            )
            """
        )
        relationship_columns = {row[1] for row in db_connection.execute("PRAGMA table_info(temporal_relationships)")}
        if "before_region_ids_json" not in relationship_columns:
            db_connection.execute("ALTER TABLE temporal_relationships ADD COLUMN before_region_ids_json TEXT NOT NULL DEFAULT '[]'")
        if "after_region_ids_json" not in relationship_columns:
            db_connection.execute("ALTER TABLE temporal_relationships ADD COLUMN after_region_ids_json TEXT NOT NULL DEFAULT '[]'")
        if "matched_region_pairs_json" not in relationship_columns:
            db_connection.execute("ALTER TABLE temporal_relationships ADD COLUMN matched_region_pairs_json TEXT NOT NULL DEFAULT '[]'")
        db_connection.execute(
            """
            CREATE TABLE IF NOT EXISTS temporal_signals (
                analysis_id INTEGER NOT NULL,
                signal_id INTEGER NOT NULL,
                geometry_json TEXT NOT NULL,
                support_count INTEGER NOT NULL,
                interval_count INTEGER NOT NULL,
                persistence_ratio REAL NOT NULL,
                recurrence_count INTEGER NOT NULL,
                transient_interval_count INTEGER NOT NULL,
                temporal_consistency REAL NOT NULL,
                matched_region_coverage REAL NOT NULL,
                first_change_datetime TEXT NOT NULL,
                last_supporting_datetime TEXT NOT NULL,
                state TEXT NOT NULL,
                PRIMARY KEY (analysis_id, signal_id)
            )
            """
        )
        signal_columns = {row[1] for row in db_connection.execute("PRAGMA table_info(temporal_signals)")}
        for column in ("source_region_ids_json", "detection_run_ids_json", "acquisition_ids_json"):
            if column not in signal_columns:
                db_connection.execute(f"ALTER TABLE temporal_signals ADD COLUMN {column} TEXT NOT NULL DEFAULT '[]'")
        db_connection.execute(
            """
            CREATE TABLE IF NOT EXISTS candidates (
                candidate_id TEXT PRIMARY KEY,
                analysis_id INTEGER NOT NULL,
                signal_id INTEGER NOT NULL,
                geometry_json TEXT NOT NULL,
                score REAL NOT NULL,
                rank INTEGER NOT NULL DEFAULT 0,
                severity TEXT NOT NULL,
                priority TEXT NOT NULL,
                review_state TEXT NOT NULL,
                source_signal_ids_json TEXT NOT NULL,
                detection_run_ids_json TEXT NOT NULL,
                acquisition_ids_json TEXT NOT NULL,
                metrics_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE (analysis_id, signal_id)
            )
            """
        )
        candidate_columns = {row[1] for row in db_connection.execute("PRAGMA table_info(candidates)")}
        if "rank" not in candidate_columns:
            db_connection.execute("ALTER TABLE candidates ADD COLUMN rank INTEGER NOT NULL DEFAULT 0")
        db_connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_imagery_acquisitions_aoi_item
            ON imagery_acquisitions (aoi_id, item_id)
            """
        )
        _migrate_to_phase_a_constraints(db_connection)
        db_connection.execute(
            """
            CREATE TABLE IF NOT EXISTS candidate_reviews (
                candidate_id TEXT PRIMARY KEY,
                decision TEXT NOT NULL,
                note TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (candidate_id) REFERENCES candidates(candidate_id) ON DELETE RESTRICT
            )
            """
        )
        _ensure_observation_quality_columns(db_connection)
        _ensure_display_columns(db_connection)
        _ensure_detection_diagnostic_columns(db_connection)
        _ensure_temporal_diagnostic_columns(db_connection)
        db_connection.commit()
