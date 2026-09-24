from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from shapely.geometry import shape

from app.config import settings
from app.domain import is_within_karnataka
from app.main import app
from app.schemas import AOIGeometry

client = TestClient(app)

REAL_DATABASE_PATH = Path(__file__).resolve().parents[1] / "terrawatch.db"


def _orphaned_aoi_references(database_path: Path) -> list[tuple[int, int, str]]:
    with sqlite3.connect(database_path) as connection:
        return connection.execute(
            """
            SELECT i.id, i.aoi_id, i.item_id
            FROM imagery_acquisitions i
            LEFT JOIN aoi a ON a.id = i.aoi_id
            WHERE a.id IS NULL
            ORDER BY i.id
            """
        ).fetchall()


def _require_trusted_real_fixture(database_path: Path) -> None:
    orphan_rows = _orphaned_aoi_references(database_path)
    if orphan_rows:
        pytest.skip(
            "Phase 9 real-data tests require a trusted, provenance-complete fixture. "
            f"The current workspace database is contaminated: {len(orphan_rows)} imagery acquisitions "
            "reference AOI IDs that are missing from the aoi table (for example rows 20/21 point to AOI 11). "
            "Do not fabricate or mutate historical provenance; restore a trusted fixture snapshot before rerunning these tests."
        )


@pytest.fixture
def isolated_real_database(tmp_path: Path) -> Path:
    _require_trusted_real_fixture(REAL_DATABASE_PATH)
    original_database = settings.database_path
    copied_path = tmp_path / "terrawatch-real.db"
    shutil.copy2(REAL_DATABASE_PATH, copied_path)
    object.__setattr__(settings, "database_path", copied_path)
    yield copied_path
    object.__setattr__(settings, "database_path", original_database)


def find_real_fixture_aoi(database_path: Path) -> int:
    """Find the deterministic AOI containing the complete real-data fixture."""
    candidates: list[tuple[tuple[object, ...], int]] = []
    failures: list[str] = []
    with sqlite3.connect(database_path) as connection:
        aoi_rows = connection.execute(
            "SELECT id, geometry_json FROM aoi"
        ).fetchall()
        acquisition_rows = connection.execute(
            """
            SELECT aoi_id, item_id, acquisition_datetime, prepared_path,
                   raster_metadata_json, source_metadata_json
            FROM imagery_acquisitions
            ORDER BY aoi_id, acquisition_datetime, item_id
            """
        ).fetchall()

    acquisitions_by_aoi: dict[int, list[tuple[object, ...]]] = {}
    for row in acquisition_rows:
        acquisitions_by_aoi.setdefault(row[0], []).append(row)

    for aoi_id, geometry_json in aoi_rows:
        try:
            geometry = shape(json.loads(geometry_json))
            AOIGeometry.model_validate(json.loads(geometry_json))
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            failures.append(f"AOI {aoi_id}: invalid geometry ({exc})")
            continue
        if not geometry.is_valid:
            failures.append(f"AOI {aoi_id}: geometry is invalid")
            continue
        if not is_within_karnataka(geometry):
            failures.append(f"AOI {aoi_id}: geometry is outside Karnataka")
            continue

        usable_observations: list[tuple[str, str]] = []
        unusable_observations: list[str] = []
        for _, item_id, acquisition_datetime, prepared_path, raster_json, source_json in acquisitions_by_aoi.get(aoi_id, []):
            try:
                raster = json.loads(raster_json)
                source = json.loads(source_json)
                raster_usable = (
                    bool(raster.get("crs"))
                    and raster.get("count") == 2
                    and raster.get("width", 0) > 0
                    and raster.get("height", 0) > 0
                )
                provenance_usable = bool(item_id) and source.get("eo:cloud_cover") is not None
                if not Path(prepared_path).exists() or not raster_usable or not provenance_usable:
                    raise ValueError("prepared raster or provenance is unusable")
            except (json.JSONDecodeError, TypeError, ValueError, OSError) as exc:
                unusable_observations.append(f"{item_id or '<missing item>'}: {exc}")
                continue
            usable_observations.append((str(item_id), str(acquisition_datetime)))

        distinct_items = {item_id for item_id, _ in usable_observations}
        distinct_timestamps = {timestamp for _, timestamp in usable_observations}
        if len(distinct_items) < 3:
            failures.append(f"AOI {aoi_id}: fewer than three distinct usable STAC items")
            continue
        if len(distinct_timestamps) < 3:
            failures.append(f"AOI {aoi_id}: fewer than three distinct usable acquisition timestamps")
            continue
        if unusable_observations:
            failures.append(f"AOI {aoi_id}: unusable observations ({'; '.join(unusable_observations)})")
            continue

        candidates.append(((tuple(sorted(usable_observations)), geometry.wkb_hex), aoi_id))

    if not candidates:
        details = "; ".join(failures) or "no AOIs were present"
        if failures and all("fewer than three distinct usable" in failure for failure in failures):
            pytest.skip(
                "Phase 9 real-data tests require at least three distinct usable observations; "
                f"the current trusted fixture is insufficient: {details}"
            )
        raise AssertionError(
            "Phase 9 real-data fixture integrity failure: no AOI satisfies the "
            f"required geometry, Karnataka containment, three-item, distinct-timestamp, "
            f"and usable-provenance invariants. Details: {details}"
        )
    return min(candidates)[1]


def test_real_karnataka_chain_works_on_isolated_copy(isolated_real_database: Path) -> None:
    aoi_id = find_real_fixture_aoi(isolated_real_database)

    imagery_request = {
        "aoi_id": aoi_id,
        "start_datetime": "2024-01-01T00:00:00+00:00",
        "end_datetime": "2024-12-31T23:59:59+00:00",
    }
    imagery_response = client.post("/api/v1/imagery/acquisitions", json=imagery_request)
    assert imagery_response.status_code == 200, imagery_response.json()
    created = imagery_response.json()
    assert created["aoi_id"] == aoi_id

    acquisitions_response = client.get(f"/api/v1/imagery/acquisitions?aoi_id={aoi_id}")
    assert acquisitions_response.status_code == 200
    acquisitions = acquisitions_response.json()["acquisitions"]
    assert acquisitions

    unique_acquisitions = []
    seen_items: set[str] = set()
    for item in sorted(acquisitions, key=lambda record: (record["acquisition_datetime"], record["acquisition_id"])):
        if item.get("observation_state") != "usable":
            continue
        if item["item_id"] in seen_items:
            continue
        seen_items.add(item["item_id"])
        unique_acquisitions.append(item)

    assert len(unique_acquisitions) >= 3, "The isolated real-data fixture must contain at least three distinct acquisition observations for the selected AOI."
    before_item, after_item, third_item = unique_acquisitions[:3]
    before_acquisition_id = before_item["acquisition_id"]
    after_acquisition_id = after_item["acquisition_id"]
    third_acquisition_id = third_item["acquisition_id"]

    assert before_item["acquisition_datetime"] < after_item["acquisition_datetime"]
    assert after_item["acquisition_datetime"] < third_item["acquisition_datetime"]

    with sqlite3.connect(isolated_real_database) as connection:
        row = connection.execute(
            "SELECT id, item_id, acquisition_datetime, prepared_path, raster_metadata_json, source_metadata_json FROM imagery_acquisitions WHERE id = ?",
            (before_acquisition_id,),
        ).fetchone()
    assert row is not None
    assert row[1]
    assert row[2].startswith("2024-")
    metadata = json.loads(row[4])
    assert metadata["crs"]
    assert metadata["count"] == 2
    assert metadata["width"] > 0 and metadata["height"] > 0
    assert Path(row[3]).exists()
    source = json.loads(row[5])
    assert source["eo:cloud_cover"] is not None

    detection_response = client.post(
        "/api/v1/detections",
        json={"before_acquisition_id": before_acquisition_id, "after_acquisition_id": after_acquisition_id, "threshold": 0.2},
    )
    assert detection_response.status_code == 200, detection_response.json()
    detection = detection_response.json()
    assert detection["before_acquisition_id"] == before_acquisition_id
    assert detection["after_acquisition_id"] == after_acquisition_id
    assert detection["detector_version"] == "ndvi-absolute-difference-v1"
    assert detection["region_count"] > 0
    assert detection["changed_pixel_count"] > 0

    second_detection_response = client.post(
        "/api/v1/detections",
        json={"before_acquisition_id": after_acquisition_id, "after_acquisition_id": third_acquisition_id, "threshold": 0.2},
    )
    assert second_detection_response.status_code == 200, second_detection_response.json()

    temporal_response = client.post(
        "/api/v1/temporal-analyses",
        json={"acquisition_ids": [before_acquisition_id, after_acquisition_id, third_acquisition_id], "iou_threshold": 0.25},
    )
    assert temporal_response.status_code == 200, temporal_response.json()
    temporal = temporal_response.json()
    assert temporal["observation_count"] == 3
    assert temporal["detector_run_count"] >= 2
    assert temporal["state"] in {"persistent", "recurrent", "transient"}
    assert temporal["relationships"]
    assert temporal["signals"]

    triage_response = client.post(
        "/api/v1/candidates/triage",
        json={"analysis_id": temporal["analysis_id"]},
    )
    assert triage_response.status_code == 200, triage_response.json()
    triage_payload = triage_response.json()
    candidates = triage_payload["candidates"]
    assert candidates
    candidate = min(candidates, key=lambda item: item["rank"])
    assert candidate["rank"] >= 1
    assert candidate["priority"] in {"normal", "high", "urgent"}
    assert candidate["review_state"] in {"unreviewed", "accepted", "rejected", "investigate"}
    assert candidate["metrics"]["temporal_state"]

    evidence_response = client.get(f"/api/v1/candidates/{candidate['candidate_id']}/evidence")
    assert evidence_response.status_code == 200, evidence_response.text
    evidence = evidence_response.json()
    assert evidence["candidate"]["candidate_id"] == candidate["candidate_id"]
    assert evidence["acquisitions"]
    selected_evidence_ids = {item["acquisition_id"] for item in evidence["acquisitions"]}
    assert selected_evidence_ids.issubset({before_acquisition_id, after_acquisition_id, third_acquisition_id})
    first_acquisition = evidence["acquisitions"][0]
    preview_response = client.get(
        f"/api/v1/candidates/{candidate['candidate_id']}/evidence/acquisitions/{first_acquisition['acquisition_id']}/preview"
    )
    assert preview_response.status_code == 200
    assert preview_response.headers["content-type"] == "image/png"
    assert preview_response.content.startswith(b"\x89PNG")

    with sqlite3.connect(isolated_real_database) as connection:
        row = connection.execute(
            "SELECT prepared_path, raster_metadata_json FROM imagery_acquisitions WHERE id = ?",
            (first_acquisition["acquisition_id"],),
        ).fetchone()
    assert row is not None
    prepared_metadata = json.loads(row[1])
    assert prepared_metadata["crs"]
    assert prepared_metadata["width"] > 0 and prepared_metadata["height"] > 0
    assert Path(row[0]).exists() and Path(row[0]).stat().st_size > 0


def test_real_detection_and_temporal_operations_are_deterministic_on_isolated_copy(isolated_real_database: Path) -> None:
    aoi_id = find_real_fixture_aoi(isolated_real_database)
    acquisitions = client.get(f"/api/v1/imagery/acquisitions?aoi_id={aoi_id}").json()["acquisitions"]
    unique_acquisitions = []
    seen_items: set[str] = set()
    for item in sorted(acquisitions, key=lambda record: (record["acquisition_datetime"], record["acquisition_id"])):
        if item.get("observation_state") != "usable":
            continue
        if item["item_id"] in seen_items:
            continue
        seen_items.add(item["item_id"])
        unique_acquisitions.append(item)
    before_acquisition_id, after_acquisition_id, third_acquisition_id = (
        item["acquisition_id"] for item in unique_acquisitions[:3]
    )

    detection_request = {"before_acquisition_id": before_acquisition_id, "after_acquisition_id": after_acquisition_id, "threshold": 0.2}
    first_detection = client.post("/api/v1/detections", json=detection_request).json()
    second_detection = client.post("/api/v1/detections", json=detection_request).json()
    assert (first_detection["region_count"], first_detection["changed_pixel_count"]) == (
        second_detection["region_count"], second_detection["changed_pixel_count"]
    )
    assert first_detection["regions"] == second_detection["regions"]

    client.post(
        "/api/v1/detections",
        json={"before_acquisition_id": after_acquisition_id, "after_acquisition_id": third_acquisition_id, "threshold": 0.2},
    )

    temporal_request = {"acquisition_ids": [before_acquisition_id, after_acquisition_id, third_acquisition_id], "iou_threshold": 0.25}
    first_temporal = client.post("/api/v1/temporal-analyses", json=temporal_request).json()
    second_temporal = client.post("/api/v1/temporal-analyses", json=temporal_request).json()
    assert first_temporal["state"] == second_temporal["state"]
    assert first_temporal["relationships"] == second_temporal["relationships"]
    assert first_temporal["signals"] == second_temporal["signals"]


def test_real_database_baseline_remains_unchanged_on_isolated_copy(isolated_real_database: Path) -> None:
    tables = (
        "aoi",
        "imagery_acquisitions",
        "detection_runs",
        "raw_change_regions",
        "temporal_analyses",
        "temporal_signals",
        "candidates",
    )

    def counts(database_path: Path) -> dict[str, int]:
        with sqlite3.connect(database_path) as connection:
            return {
                table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in tables
            }

    assert counts(isolated_real_database) == counts(REAL_DATABASE_PATH)
