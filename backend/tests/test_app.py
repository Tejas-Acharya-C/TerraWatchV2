import json

import pytest
from fastapi.testclient import TestClient

from app.db import connection, initialize_database
from app.config import settings
from app.main import app

client = TestClient(app)

VALID_AOI = {
    "type": "Polygon",
    "coordinates": [[[77.4, 12.8], [77.5, 12.8], [77.5, 12.9], [77.4, 12.9], [77.4, 12.8]]],
}


@pytest.fixture(autouse=True)
def isolated_database(tmp_path):
    original_path = settings.database_path
    object.__setattr__(settings, "database_path", tmp_path / "test.db")
    initialize_database()
    yield
    object.__setattr__(settings, "database_path", original_path)


def test_root() -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["message"] == "TerraWatch V2 API"


def test_health() -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["app"] == "TerraWatch V2"


def test_readiness() -> None:
    response = client.get("/api/v1/ready")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"


def test_validation_error() -> None:
    response = client.get("/api/v1/health", params={"probe": "invalid"})
    assert response.status_code == 422
    assert response.json() == {
        "code": "ValidationError",
        "message": "Request validation failed",
        "details": {"validation": "Input should be a valid boolean, unable to interpret input"},
    }


def test_valid_aoi_is_saved_and_retrieved() -> None:
    save_response = client.post("/api/v1/aoi", json=VALID_AOI)
    assert save_response.status_code == 200
    assert save_response.json()["geometry"] == VALID_AOI

    retrieve_response = client.get("/api/v1/aoi")
    assert retrieve_response.status_code == 200
    assert retrieve_response.json()["geometry"] == VALID_AOI


def test_empty_aoi_returns_structured_not_found() -> None:
    response = client.get("/api/v1/aoi")
    assert response.status_code == 404
    assert response.json() == {
        "code": "NoAOIError",
        "message": "No AOI has been saved",
        "details": None,
    }


def test_malformed_aoi_is_rejected() -> None:
    response = client.post("/api/v1/aoi", json={"type": "Polygon", "coordinates": []})
    assert response.status_code == 400
    assert response.json()["code"] == "TerraWatchError"


def test_invalid_polygon_is_rejected() -> None:
    invalid_polygon = {
        "type": "Polygon",
        "coordinates": [[[0, 0], [2, 2], [2, 0], [0, 2], [0, 0]]],
    }
    response = client.post("/api/v1/aoi", json=invalid_polygon)
    assert response.status_code == 400
    assert "invalid" in response.json()["message"]


def test_invalid_persisted_aoi_is_rejected_on_retrieval() -> None:
    client.post("/api/v1/aoi", json=VALID_AOI)
    outside_aoi = {
        "type": "Polygon",
        "coordinates": [[[20, 30], [21, 30], [21, 31], [20, 31], [20, 30]]],
    }
    with connection() as db_connection:
        db_connection.execute(
            "UPDATE aoi SET geometry_json = ? WHERE id = 1",
            (json.dumps(outside_aoi),),
        )
        db_connection.commit()

    response = client.get("/api/v1/aoi")

    assert response.status_code == 422
    assert response.json()["code"] == "AOIOutsideKarnatakaError"


def test_unsupported_geometry_is_rejected() -> None:
    response = client.post(
        "/api/v1/aoi",
        json={"type": "LineString", "coordinates": [[0, 0], [1, 1]]},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "ValidationError"


def test_valid_aoi_outside_karnataka_is_rejected() -> None:
    response = client.post(
        "/api/v1/aoi",
        json={"type": "Polygon", "coordinates": [[[20, 30], [21, 30], [21, 31], [20, 31], [20, 30]]]},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "AOIOutsideKarnatakaError"


def test_aoi_crossing_karnataka_boundary_is_rejected() -> None:
    response = client.post(
        "/api/v1/aoi",
        json={"type": "Polygon", "coordinates": [[[74.0, 12.8], [77.5, 12.8], [77.5, 13.2], [74.0, 13.2], [74.0, 12.8]]]},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "AOIOutsideKarnatakaError"


def test_aoi_can_be_cleared() -> None:
    client.post("/api/v1/aoi", json=VALID_AOI)
    response = client.delete("/api/v1/aoi")
    assert response.status_code == 200
    assert response.json() == {"cleared": True}

    with connection() as db_connection:
        tables = db_connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
        rows = db_connection.execute("SELECT * FROM aoi").fetchall()
    table_names = {table[0] for table in tables}
    assert {"aoi", "imagery_acquisitions"}.issubset(table_names)
    assert rows == []


def test_aoi_cannot_be_cleared_after_imagery_is_persisted() -> None:
    client.post("/api/v1/aoi", json=VALID_AOI)
    with connection() as db_connection:
        db_connection.execute(
            """INSERT INTO imagery_acquisitions (
                aoi_id, requested_start_datetime, requested_end_datetime,
                item_id, collection, acquisition_datetime, assets_json,
                prepared_path, raster_metadata_json, source_metadata_json, created_at
            ) VALUES (1, '2024-01-01T00:00:00+00:00', '2024-02-01T00:00:00+00:00',
                'S2_TEST', 'sentinel-2-l2a', '2024-01-15T00:00:00+00:00',
                '[]', 'test.tif', '{}', '{}', '2024-01-15T00:00:00+00:00')"""
        )
        db_connection.commit()

    response = client.delete("/api/v1/aoi")

    assert response.status_code == 400
    assert response.json()["code"] == "TerraWatchError"
    assert client.get("/api/v1/aoi").status_code == 200


def test_list_aois_and_get_aoi_by_id() -> None:
    # 1. Initially empty
    assert client.get("/api/v1/aois").json() == []

    # 2. Save first AOI
    first_resp = client.post("/api/v1/aoi", json=VALID_AOI)
    assert first_resp.status_code == 200
    first_id = first_resp.json()["aoi_id"]

    # 3. Save second (edited/different) AOI
    second_aoi = {
        "type": "Polygon",
        "coordinates": [[[75.0, 15.0], [75.2, 15.0], [75.2, 15.2], [75.0, 15.2], [75.0, 15.0]]],
    }
    second_resp = client.post("/api/v1/aoi", json=second_aoi)
    assert second_resp.status_code == 200
    second_id = second_resp.json()["aoi_id"]
    assert second_id != first_id

    # 4. List AOIs returns both
    list_resp = client.get("/api/v1/aois")
    assert list_resp.status_code == 200
    aois = list_resp.json()
    assert len(aois) == 2
    assert aois[0]["aoi_id"] == first_id
    assert aois[1]["aoi_id"] == second_id

    # 5. Fetch by ID
    get_first = client.get(f"/api/v1/aoi/{first_id}")
    assert get_first.status_code == 200
    assert get_first.json()["aoi_id"] == first_id
    assert get_first.json()["geometry"] == VALID_AOI

    get_second = client.get(f"/api/v1/aoi/{second_id}")
    assert get_second.status_code == 200
    assert get_second.json()["aoi_id"] == second_id
    assert get_second.json()["geometry"] == second_aoi

    # 6. Non-existent ID returns 404
    not_found = client.get("/api/v1/aoi/9999")
    assert not_found.status_code == 404
    assert not_found.json()["code"] == "NoAOIError"


def test_edited_aoi_creates_new_identity_and_preserves_historical_provenance() -> None:
    # 1. Save original AOI
    first_resp = client.post("/api/v1/aoi", json=VALID_AOI)
    first_id = first_resp.json()["aoi_id"]

    # 2. Attach historical acquisition to original AOI
    with connection() as db:
        db.execute(
            """INSERT INTO imagery_acquisitions (
                aoi_id, requested_start_datetime, requested_end_datetime,
                item_id, collection, acquisition_datetime, assets_json,
                prepared_path, raster_metadata_json, source_metadata_json, created_at
            ) VALUES (?, '2024-01-01T00:00:00+00:00', '2024-02-01T00:00:00+00:00',
                'S2_HISTORICAL', 'sentinel-2-l2a', '2024-01-15T00:00:00+00:00',
                '[]', 'hist.tif', '{}', '{}', '2024-01-15T00:00:00+00:00')""",
            (first_id,),
        )
        db.commit()

    # 3. Simulate saving edited geometry
    edited_aoi = {
        "type": "Polygon",
        "coordinates": [[[75.0, 15.0], [75.3, 15.0], [75.3, 15.3], [75.0, 15.3], [75.0, 15.0]]],
    }
    second_resp = client.post("/api/v1/aoi", json=edited_aoi)
    assert second_resp.status_code == 200
    second_id = second_resp.json()["aoi_id"]
    assert second_id != first_id

    # 4. Verify historical AOI and its historical acquisition are completely intact
    with connection() as db:
        first_row = db.execute("SELECT geometry_json FROM aoi WHERE id = ?", (first_id,)).fetchone()
        assert json.loads(first_row[0]) == VALID_AOI

        hist_acq = db.execute("SELECT item_id FROM imagery_acquisitions WHERE aoi_id = ?", (first_id,)).fetchone()
        assert hist_acq[0] == "S2_HISTORICAL"

        # 5. Verify new AOI has NO inherited acquisitions
        new_acqs = db.execute("SELECT COUNT(*) FROM imagery_acquisitions WHERE aoi_id = ?", (second_id,)).fetchone()
        assert new_acqs[0] == 0


def test_invalid_edited_aoi_completely_outside_karnataka_is_rejected_without_database_change() -> None:
    resp = client.post("/api/v1/aoi", json=VALID_AOI)
    original_id = resp.json()["aoi_id"]
    with connection() as db:
        initial_aoi_count = db.execute("SELECT COUNT(*) FROM aoi").fetchone()[0]
        initial_acq_count = db.execute("SELECT COUNT(*) FROM imagery_acquisitions").fetchone()[0]

    completely_outside = {
        "type": "Polygon",
        "coordinates": [[[82.0, 22.0], [82.5, 22.0], [82.5, 22.5], [82.0, 22.5], [82.0, 22.0]]],
    }
    response = client.post("/api/v1/aoi", json=completely_outside)
    assert response.status_code == 422
    assert response.json()["code"] == "AOIOutsideKarnatakaError"

    with connection() as db:
        assert db.execute("SELECT COUNT(*) FROM aoi").fetchone()[0] == initial_aoi_count
        assert db.execute("SELECT COUNT(*) FROM imagery_acquisitions").fetchone()[0] == initial_acq_count
        orig_row = db.execute("SELECT geometry_json FROM aoi WHERE id = ?", (original_id,)).fetchone()
        assert json.loads(orig_row[0]) == VALID_AOI


def test_invalid_edited_aoi_crossing_karnataka_boundary_is_rejected_without_database_change() -> None:
    resp = client.post("/api/v1/aoi", json=VALID_AOI)
    original_id = resp.json()["aoi_id"]

    # Attach historical acquisitions and detection run to original AOI
    with connection() as db:
        db.execute(
            """INSERT INTO imagery_acquisitions (
                aoi_id, requested_start_datetime, requested_end_datetime,
                item_id, collection, acquisition_datetime, assets_json,
                prepared_path, raster_metadata_json, source_metadata_json, created_at
            ) VALUES (?, '2024-01-01T00:00:00+00:00', '2024-02-01T00:00:00+00:00',
                'S2_ORIG_ACQ_1', 'sentinel-2-l2a', '2024-01-15T00:00:00+00:00',
                '[]', 'orig1.tif', '{}', '{}', '2024-01-15T00:00:00+00:00')""",
            (original_id,),
        )
        acq1 = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        db.execute(
            """INSERT INTO imagery_acquisitions (
                aoi_id, requested_start_datetime, requested_end_datetime,
                item_id, collection, acquisition_datetime, assets_json,
                prepared_path, raster_metadata_json, source_metadata_json, created_at
            ) VALUES (?, '2024-02-01T00:00:00+00:00', '2024-03-01T00:00:00+00:00',
                'S2_ORIG_ACQ_2', 'sentinel-2-l2a', '2024-02-15T00:00:00+00:00',
                '[]', 'orig2.tif', '{}', '{}', '2024-02-15T00:00:00+00:00')""",
            (original_id,),
        )
        acq2 = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        db.execute(
            """INSERT INTO detection_runs (
                before_acquisition_id, after_acquisition_id, detector_version,
                threshold, min_region_pixels, region_count, changed_pixel_count,
                total_changed_area_m2, created_at
            ) VALUES (?, ?, 'v1', 0.2, 1, 0, 0, 0, '2024-02-15T00:00:00+00:00')""",
            (acq1, acq2),
        )
        det_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        db.commit()

        initial_aoi_count = db.execute("SELECT COUNT(*) FROM aoi").fetchone()[0]
        initial_acq_count = db.execute("SELECT COUNT(*) FROM imagery_acquisitions").fetchone()[0]
        initial_det_count = db.execute("SELECT COUNT(*) FROM detection_runs").fetchone()[0]

    # Partial crossing: begins inside Karnataka (lng 75.0, lat 14.0) and extends west into Arabian Sea (lng 73.5, lat 14.0)
    crossing_edit = {
        "type": "Polygon",
        "coordinates": [[[73.5, 14.0], [75.5, 14.0], [75.5, 14.5], [73.5, 14.5], [73.5, 14.0]]],
    }
    response = client.post("/api/v1/aoi", json=crossing_edit)
    assert response.status_code == 422
    assert response.json()["code"] == "AOIOutsideKarnatakaError"

    # Verify original AOI remains unchanged, no new AOI row created, acquisitions unchanged, no downstream provenance modified
    with connection() as db:
        assert db.execute("SELECT COUNT(*) FROM aoi").fetchone()[0] == initial_aoi_count
        assert db.execute("SELECT COUNT(*) FROM imagery_acquisitions").fetchone()[0] == initial_acq_count
        assert db.execute("SELECT COUNT(*) FROM detection_runs").fetchone()[0] == initial_det_count

        orig_row = db.execute("SELECT geometry_json FROM aoi WHERE id = ?", (original_id,)).fetchone()
        assert json.loads(orig_row[0]) == VALID_AOI

        acq1_row = db.execute("SELECT item_id, aoi_id FROM imagery_acquisitions WHERE id = ?", (acq1,)).fetchone()
        assert acq1_row[0] == "S2_ORIG_ACQ_1"
        assert acq1_row[1] == original_id

        acq2_row = db.execute("SELECT item_id, aoi_id FROM imagery_acquisitions WHERE id = ?", (acq2,)).fetchone()
        assert acq2_row[0] == "S2_ORIG_ACQ_2"
        assert acq2_row[1] == original_id

        det_row = db.execute("SELECT before_acquisition_id, after_acquisition_id FROM detection_runs WHERE id = ?", (det_id,)).fetchone()
        assert det_row[0] == acq1
        assert det_row[1] == acq2

