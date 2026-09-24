from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from typing import Literal

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from shapely.geometry import shape
from shapely.validation import explain_validity

from app.config import settings
from app.candidates import (
    get_candidate,
    get_candidate_review,
    list_candidates,
    save_candidate_review,
    triage,
    update_review_state,
)
from app.db import connection, initialize_database
from app.detection import detect
from app.evidence import get_candidate_evidence, render_preview
from app.temporal import analyze, get_analysis, orchestrate_analysis, orchestrate_analysis_events
from app.domain import is_within_karnataka
from app.exceptions import AOIOutsideKarnatakaError, AOIPersistenceError, NoAOIError, TerraWatchError
from app.export import (
    generate_investigation_json,
    generate_investigation_pdf,
    resolve_investigation_snapshot,
    sanitize_filename_candidate_id,
)
from app.imagery import acquire_imagery, acquire_imagery_events, latest_acquisition, list_acquisitions, render_display
from app.logging_conf import log_exception, setup_logging
from app.schemas import (
    AOIClearResponse,
    AOIGeometry,
    AOIResponse,
    ApiError,
    HealthResponse,
    ImageryAcquisitionRequest,
    ImageryAcquisitionResponse,
    ImageryAcquisitionListResponse,
    DetectionRequest,
    DetectionResponse,
    TemporalAnalysisRequest,
    TemporalAnalysisResponse,
    AnalysisOrchestrationRequest,
    AnalysisOrchestrationResponse,
    CandidateListResponse,
    CandidateResponse,
    CandidateReviewRequest,
    CandidateReviewResponse,
    CandidateReviewState,
    CandidateReviewStateRequest,
    CandidateTriageRequest,
    CandidateEvidenceResponse,
)

logger = setup_logging()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    initialize_database()
    logger.info("TerraWatch V2 startup complete")
    yield


app = FastAPI(
    title="TerraWatch V2",
    version="0.1.0",
    description="Technical foundation for TerraWatch V2",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["*"],
)


@app.get(f"{settings.api_v1_prefix}/health", response_model=HealthResponse)
def health(probe: bool = Query(default=True)) -> HealthResponse:
    return HealthResponse(status="ok", app=settings.app_name, version="0.1.0")


@app.get(f"{settings.api_v1_prefix}/ready")
def readiness() -> dict[str, str]:
    return {"status": "ready", "app": settings.app_name}


def _validate_aoi(geometry: AOIGeometry) -> AOIGeometry:
    try:
        polygon = shape(geometry.model_dump())
    except (TypeError, ValueError) as exc:
        raise TerraWatchError("AOI geometry is malformed") from exc
    if polygon.geom_type != "Polygon":
        raise TerraWatchError("AOI geometry must be a Polygon")
    if polygon.is_empty:
        raise TerraWatchError("AOI geometry must not be empty")
    if not polygon.is_valid:
        raise TerraWatchError(f"AOI polygon is invalid: {explain_validity(polygon)}")
    if not is_within_karnataka(polygon):
        raise AOIOutsideKarnatakaError("AOI must be fully contained within Karnataka")
    return geometry


def _now() -> str:
    return datetime.now(UTC).isoformat()


@app.post(f"{settings.api_v1_prefix}/aoi", response_model=AOIResponse)
def save_aoi(geometry: AOIGeometry) -> AOIResponse:
    validated_geometry = _validate_aoi(geometry)
    timestamp = _now()
    payload = json.dumps(validated_geometry.model_dump(), separators=(",", ":"))
    try:
        with connection() as db_connection:
            cursor = db_connection.execute(
                """
                INSERT INTO aoi (geometry_json, created_at, updated_at)
                VALUES (?, ?, ?)
                """,
                (payload, timestamp, timestamp),
            )
            aoi_id = cursor.lastrowid
            db_connection.commit()
    except Exception as exc:
        raise AOIPersistenceError("AOI could not be saved") from exc
    return AOIResponse(aoi_id=aoi_id, geometry=validated_geometry, created_at=timestamp, updated_at=timestamp)


@app.get(f"{settings.api_v1_prefix}/aoi", response_model=AOIResponse)
def get_aoi() -> AOIResponse:
    try:
        with connection() as db_connection:
            row = db_connection.execute(
                "SELECT id, geometry_json, created_at, updated_at FROM aoi ORDER BY id DESC LIMIT 1"
            ).fetchone()
    except Exception as exc:
        raise AOIPersistenceError("AOI could not be retrieved") from exc
    if row is None:
        raise NoAOIError("No AOI has been saved")
    try:
        geometry = AOIGeometry.model_validate(json.loads(row[1]))
    except (json.JSONDecodeError, ValueError) as exc:
        raise AOIPersistenceError("Persisted AOI is unreadable") from exc
    _validate_aoi(geometry)
    return AOIResponse(aoi_id=row[0], geometry=geometry, created_at=row[2], updated_at=row[3])


@app.get(f"{settings.api_v1_prefix}/aois", response_model=list[AOIResponse])
def list_aois() -> list[AOIResponse]:
    try:
        with connection() as db_connection:
            rows = db_connection.execute(
                "SELECT id, geometry_json, created_at, updated_at FROM aoi ORDER BY id ASC"
            ).fetchall()
    except Exception as exc:
        raise AOIPersistenceError("AOIs could not be retrieved") from exc
    results: list[AOIResponse] = []
    for row in rows:
        try:
            geometry = AOIGeometry.model_validate(json.loads(row[1]))
            _validate_aoi(geometry)
            results.append(AOIResponse(aoi_id=row[0], geometry=geometry, created_at=row[2], updated_at=row[3]))
        except Exception:
            continue
    return results


@app.get(f"{settings.api_v1_prefix}/aoi/{{aoi_id}}", response_model=AOIResponse)
def get_aoi_by_id(aoi_id: int) -> AOIResponse:
    try:
        with connection() as db_connection:
            row = db_connection.execute(
                "SELECT id, geometry_json, created_at, updated_at FROM aoi WHERE id = ?",
                (aoi_id,),
            ).fetchone()
    except Exception as exc:
        raise AOIPersistenceError(f"AOI {aoi_id} could not be retrieved") from exc
    if row is None:
        raise NoAOIError(f"AOI with id {aoi_id} does not exist")
    try:
        geometry = AOIGeometry.model_validate(json.loads(row[1]))
    except (json.JSONDecodeError, ValueError) as exc:
        raise AOIPersistenceError("Persisted AOI is unreadable") from exc
    _validate_aoi(geometry)
    return AOIResponse(aoi_id=row[0], geometry=geometry, created_at=row[2], updated_at=row[3])


@app.delete(f"{settings.api_v1_prefix}/aoi", response_model=AOIClearResponse)
def clear_aoi() -> AOIClearResponse:
    try:
        with connection() as db_connection:
            latest_aoi = db_connection.execute(
                "SELECT id FROM aoi ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if latest_aoi is None:
                return AOIClearResponse(cleared=False)
            acquisition_exists = db_connection.execute(
                "SELECT 1 FROM imagery_acquisitions WHERE aoi_id = ? LIMIT 1",
                (latest_aoi[0],),
            ).fetchone()
            if acquisition_exists is not None:
                raise TerraWatchError(
                    "The AOI cannot be cleared while imagery provenance exists"
                )
            cursor = db_connection.execute("DELETE FROM aoi WHERE id = ?", (latest_aoi[0],))
            db_connection.commit()
    except TerraWatchError:
        raise
    except Exception as exc:
        raise AOIPersistenceError("AOI could not be cleared") from exc
    return AOIClearResponse(cleared=cursor.rowcount > 0)


@app.post(
    f"{settings.api_v1_prefix}/imagery/acquisitions",
    response_model=ImageryAcquisitionResponse,
)
def create_imagery_acquisition(
    request: ImageryAcquisitionRequest,
    req: Request,
    stream: bool = Query(default=False),
) -> Any:
    accept = req.headers.get("accept", "")
    wants_stream = stream or "application/x-ndjson" in accept
    if wants_stream:
        def stream_generator():
            try:
                for event in acquire_imagery_events(request):
                    yield json.dumps(event) + "\n"
            except TerraWatchError as exc:
                yield json.dumps({"type": "error", "code": exc.__class__.__name__, "message": str(exc)}) + "\n"
            except Exception as exc:
                log_exception(logger, exc, context="Stream acquisition error")
                yield json.dumps({"type": "error", "code": "InternalServerError", "message": "Observation acquisition failed"}) + "\n"

        return StreamingResponse(
            stream_generator(),
            media_type="application/x-ndjson",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    return acquire_imagery(request)


@app.get(
    f"{settings.api_v1_prefix}/imagery/acquisitions/latest",
    response_model=ImageryAcquisitionResponse,
)
def get_latest_imagery_acquisition() -> ImageryAcquisitionResponse:
    return latest_acquisition()


@app.get(
    f"{settings.api_v1_prefix}/imagery/acquisitions",
    response_model=ImageryAcquisitionListResponse,
)
def get_imagery_acquisitions(aoi_id: int | None = Query(default=None, gt=0)) -> ImageryAcquisitionListResponse:
    return list_acquisitions(aoi_id)


@app.get(f"{settings.api_v1_prefix}/imagery/acquisitions/{{acquisition_id}}/display")
def get_imagery_display(acquisition_id: int, aoi_id: int | None = Query(default=None, gt=0)) -> Response:
    return Response(content=render_display(acquisition_id, aoi_id), media_type="image/png")


@app.post(
    f"{settings.api_v1_prefix}/detections",
    response_model=DetectionResponse,
)
def create_detection(request: DetectionRequest) -> DetectionResponse:
    return detect(request)


@app.post(
    f"{settings.api_v1_prefix}/temporal-analyses",
    response_model=TemporalAnalysisResponse,
)
def create_temporal_analysis(request: TemporalAnalysisRequest) -> TemporalAnalysisResponse:
    return analyze(request)


@app.get(
    f"{settings.api_v1_prefix}/temporal-analyses/{{analysis_id}}",
    response_model=TemporalAnalysisResponse,
)
def get_temporal_analysis(analysis_id: int) -> TemporalAnalysisResponse:
    return get_analysis(analysis_id)


@app.post(
    f"{settings.api_v1_prefix}/orchestrations/analyze",
    response_model=AnalysisOrchestrationResponse,
)
def create_orchestration_analysis(
    request: AnalysisOrchestrationRequest,
    req: Request,
    stream: bool = Query(default=False),
) -> Any:
    accept = req.headers.get("accept", "")
    wants_stream = stream or "application/x-ndjson" in accept
    if wants_stream:
        def stream_generator():
            try:
                for event in orchestrate_analysis_events(request):
                    yield json.dumps(event) + "\n"
            except TerraWatchError as exc:
                yield json.dumps({"type": "error", "code": exc.__class__.__name__, "message": str(exc)}) + "\n"
            except Exception as exc:
                log_exception(logger, exc, context="Stream orchestration error")
                yield json.dumps({"type": "error", "code": "InternalServerError", "message": "Analysis orchestration failed"}) + "\n"

        return StreamingResponse(
            stream_generator(),
            media_type="application/x-ndjson",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    return orchestrate_analysis(request)


@app.post(f"{settings.api_v1_prefix}/candidates/triage", response_model=CandidateListResponse)
def create_candidates(request: CandidateTriageRequest) -> CandidateListResponse:
    return triage(request.analysis_id)


@app.get(f"{settings.api_v1_prefix}/candidates", response_model=CandidateListResponse)
def get_candidates(
    analysis_id: int | None = Query(default=None, gt=0),
    priority: Literal["low", "normal", "high", "urgent"] | None = Query(default=None),
    severity: Literal["low", "medium", "high"] | None = Query(default=None),
    review_state: CandidateReviewState | None = Query(default=None),
    temporal_state: Literal["persistent", "transient", "recurrent", "isolated"] | None = Query(default=None),
    min_score: float | None = Query(default=None, ge=0.0, le=1.0),
    min_quality: float | None = Query(default=None, ge=0.0, le=1.0),
) -> CandidateListResponse:
    return list_candidates(
        analysis_id=analysis_id,
        priority=priority,
        severity=severity,
        review_state=review_state,
        temporal_state=temporal_state,
        min_score=min_score,
        min_quality=min_quality,
    )


@app.get(f"{settings.api_v1_prefix}/candidates/{{candidate_id}}", response_model=CandidateResponse)
def get_candidate_detail(candidate_id: str) -> CandidateResponse:
    return get_candidate(candidate_id)


@app.get(f"{settings.api_v1_prefix}/candidates/{{candidate_id}}/review", response_model=CandidateReviewResponse)
def get_candidate_review_detail(candidate_id: str) -> CandidateReviewResponse:
    return get_candidate_review(candidate_id)


@app.patch(f"{settings.api_v1_prefix}/candidates/{{candidate_id}}/review", response_model=CandidateReviewResponse)
def patch_candidate_review(candidate_id: str, request: CandidateReviewRequest) -> CandidateReviewResponse:
    return save_candidate_review(candidate_id, request)


@app.get(f"{settings.api_v1_prefix}/candidates/{{candidate_id}}/evidence", response_model=CandidateEvidenceResponse)
def get_candidate_evidence_detail(candidate_id: str) -> CandidateEvidenceResponse:
    return get_candidate_evidence(candidate_id)


@app.get(f"{settings.api_v1_prefix}/candidates/{{candidate_id}}/evidence/acquisitions/{{acquisition_id}}/preview")
def get_candidate_evidence_preview(candidate_id: str, acquisition_id: int) -> Response:
    return Response(content=render_preview(candidate_id, acquisition_id), media_type="image/png")


@app.get(f"{settings.api_v1_prefix}/candidates/{{candidate_id}}/export")
def export_candidate_investigation(
    candidate_id: str,
    format: Literal["pdf", "json"] = Query(default="pdf"),
) -> Response:
    snapshot = resolve_investigation_snapshot(candidate_id)
    safe_id = sanitize_filename_candidate_id(candidate_id)

    if format == "json":
        content = generate_investigation_json(snapshot)
        media_type = "application/json"
        filename = f"terrawatch_investigation_{safe_id}.json"
    else:
        content = generate_investigation_pdf(snapshot)
        media_type = "application/pdf"
        filename = f"terrawatch_investigation_{safe_id}.pdf"

    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


@app.patch(f"{settings.api_v1_prefix}/candidates/{{candidate_id}}/review-state", response_model=CandidateResponse)
def change_candidate_review_state(candidate_id: str, request: CandidateReviewStateRequest) -> CandidateResponse:
    return update_review_state(candidate_id, request.review_state)


@app.exception_handler(TerraWatchError)
async def terrawatch_error_handler(request: Request, exc: TerraWatchError):
    code = exc.__class__.__name__
    payload = ApiError(code=code, message=str(exc))
    return JSONResponse(status_code=exc.status_code, content=payload.model_dump())


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    details = {"validation": "; ".join(err.get("msg", "invalid") for err in exc.errors())}
    payload = ApiError(code="ValidationError", message="Request validation failed", details=details)
    return JSONResponse(status_code=422, content=payload.model_dump())


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    log_exception(logger, exc, context="Unhandled API error")
    payload = ApiError(code="InternalServerError", message="An unexpected error occurred")
    return JSONResponse(status_code=500, content=payload.model_dump())


@app.get("/")
def root() -> dict[str, str]:
    return {"message": "TerraWatch V2 API"}
