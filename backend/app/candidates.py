from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any

from shapely.geometry import shape

from app.db import connection
from app.exceptions import (
    CandidateAnalysisUnavailableError,
    CandidateEvidenceError,
    CandidateNotFoundError,
    CandidateTriageFailureError,
    CandidateUpstreamFailureError,
    InvalidCandidateStateError,
    MissingTemporalAnalysisError,
)
from app.schemas import (
    CandidateExplanation,
    CandidateListResponse,
    CandidateMetrics,
    CandidateResponse,
    CandidateReviewRequest,
    CandidateReviewResponse,
    CandidateReviewState,
    ReviewDecision,
)

VALID_DECISIONS = {"accepted", "rejected", "investigate"}
REVIEW_STATES = {"unreviewed", "accepted", "rejected", "investigate"}
PRIORITY_WEIGHTS = {"urgent": 4, "high": 3, "normal": 2, "low": 1}
SEVERITY_WEIGHTS = {"high": 3, "medium": 2, "low": 1}


def _extract_signal_physical_metrics(
    db_connection: sqlite3.Connection,
    source_region_ids: list[int],
    detection_run_ids: list[int],
    interval_means: list[float],
    interval_maxima: list[float],
) -> tuple[float, float, float]:
    """Extract physical change metrics (area_m2, mean_delta_ndvi, max_delta_ndvi) from persisted metadata."""
    area_m2 = 0.0
    mean_delta = 0.20
    max_delta = 0.20

    if source_region_ids:
        placeholders = ",".join("?" for _ in source_region_ids)
        rows = db_connection.execute(
            f"SELECT area_m2, mean_change_signal, max_change_signal FROM raw_change_regions WHERE id IN ({placeholders})",
            source_region_ids,
        ).fetchall()
        if rows:
            area_m2 = max(row[0] for row in rows)
            mean_delta = sum(row[1] for row in rows) / len(rows)
            max_delta = max(row[2] for row in rows)
            return float(area_m2), float(mean_delta), float(max_delta)

    if interval_means:
        mean_delta = sum(interval_means) / len(interval_means)
    if interval_maxima:
        max_delta = max(interval_maxima)

    if detection_run_ids and area_m2 == 0.0:
        placeholders = ",".join("?" for _ in detection_run_ids)
        rows = db_connection.execute(
            f"SELECT total_changed_area_m2, mean_change_signal, max_change_signal FROM detection_runs WHERE id IN ({placeholders})",
            detection_run_ids,
        ).fetchall()
        if rows:
            area_m2 = max(row[0] for row in rows)
            if not interval_means and any(row[1] > 0 for row in rows):
                mean_delta = sum(row[1] for row in rows) / len(rows)
            if not interval_maxima and any(row[2] > 0 for row in rows):
                max_delta = max(row[2] for row in rows)

    return float(area_m2), float(mean_delta), float(max_delta)


def _score_candidate(
    persistence_ratio: float,
    temporal_consistency: float,
    mean_delta_ndvi: float,
    area_m2: float,
    quality_support: float,
    matched_region_coverage: float,
) -> tuple[float, dict[str, float], float, float, float]:
    """Calculate deterministic, bounded evidence score and components.

    Components (all bounded in [0.0, 1.0]):
    - temporal_persistence: persistence_ratio (quality-weighted interval support fraction)
    - temporal_consistency: track IoU match consistency across supporting intervals
    - change_magnitude: 0.6 * norm_delta_ndvi + 0.4 * norm_area
      where norm_delta_ndvi = clamp((mean_delta_ndvi - 0.20) / 0.60, 0, 1)
            norm_area = clamp((area_m2 - 500.0) / 9500.0, 0, 1)
    - observation_quality: quality_support (mean usable pixel fraction across supporting intervals)

    Weights:
    - temporal_persistence: 0.35
    - temporal_consistency: 0.25
    - change_magnitude: 0.20
    - observation_quality: 0.20
    """
    c_pers = max(0.0, min(1.0, float(persistence_ratio)))
    c_cons = max(0.0, min(1.0, float(temporal_consistency)))

    norm_delta_ndvi = max(0.0, min(1.0, (float(mean_delta_ndvi) - 0.20) / 0.60))
    norm_area = max(0.0, min(1.0, (float(area_m2) - 500.0) / 9500.0))
    c_mag = round(0.6 * norm_delta_ndvi + 0.4 * norm_area, 6)

    c_qual = max(0.0, min(1.0, float(quality_support)))

    score = round(0.35 * c_pers + 0.25 * c_cons + 0.20 * c_mag + 0.20 * c_qual, 4)

    components = {
        "temporal_persistence": round(c_pers, 4),
        "temporal_consistency": round(c_cons, 4),
        "change_magnitude": round(c_mag, 4),
        "observation_quality": round(c_qual, 4),
        "persistence_ratio": round(c_pers, 4),
        "matched_region_coverage": round(max(0.0, min(1.0, float(matched_region_coverage))), 4),
    }
    return score, components, norm_delta_ndvi, norm_area, c_mag


def _severity(norm_delta_ndvi: float, norm_area: float, c_mag: float) -> str:
    """Classify physical change severity (high, medium, low).

    Severity MUST be based on physical change evidence only.
    Temporal state MUST NOT independently elevate or cap severity.

    - high: large spatial clearing (norm_area >= 0.50) OR strong spectral loss (norm_delta_ndvi >= 0.50) OR strong overall physical magnitude (c_mag >= 0.45)
    - medium: moderate spatial clearing (norm_area >= 0.20) OR moderate spectral loss (norm_delta_ndvi >= 0.25) OR moderate overall magnitude (c_mag >= 0.20)
    - low: otherwise
    """
    if norm_area >= 0.50 or norm_delta_ndvi >= 0.50 or c_mag >= 0.45:
        return "high"
    if norm_area >= 0.20 or norm_delta_ndvi >= 0.25 or c_mag >= 0.20:
        return "medium"
    return "low"


def _priority(state: str, score: float, severity: str, quality_support: float) -> str:
    """Classify triage actionability priority (urgent, high, normal, low).

    Priority is operational triage urgency:
    - Confidence (score): strength of observational evidence
    - Severity (severity): physical magnitude/extent
    - Temporal State (state): temporal behavior
    - Observation Quality (quality_support): atmospheric/radiometric clarity

    - urgent:
        High physical severity with strong evidence (severity == 'high' and score >= 0.50)
        OR medium physical severity with confirmed persistent change and strong evidence (severity == 'medium' and state == 'persistent' and score >= 0.55),
        provided observation quality is not degraded (not 0.0 < quality_support < 0.40).
    - high:
        High physical severity (severity == 'high')
        OR medium physical severity with moderate evidence (severity == 'medium' and score >= 0.40)
        OR persistent change with at least moderate evidence (state == 'persistent' and score >= 0.45)
        OR recurrent change with medium severity and adequate quality (state == 'recurrent' and severity == 'medium' and quality_support >= 0.40)
    - normal:
        score >= 0.30 OR severity == 'medium' OR state in ('transient', 'recurrent', 'persistent', 'isolated')
    - low:
        otherwise
    """
    is_poor_quality = 0.0 < quality_support < 0.40

    if not is_poor_quality:
        if (severity == "high" and score >= 0.50) or (severity == "medium" and state == "persistent" and score >= 0.55):
            return "urgent"

    if severity == "high":
        return "high"
    if severity == "medium" and score >= 0.40:
        return "high"
    if state == "persistent" and score >= 0.45:
        return "high"
    if state == "recurrent" and severity == "medium" and quality_support >= 0.40:
        return "high"

    if score >= 0.30 or severity == "medium" or state in ("transient", "recurrent", "persistent", "isolated"):
        return "normal"

    return "low"


def _explain_candidate(
    score: float,
    components: dict[str, float],
    state: str,
    severity: str,
    priority: str,
    area_m2: float,
    mean_delta_ndvi: float,
    quality_support: float,
) -> CandidateExplanation:
    positive_factors: list[str] = []
    limiting_factors: list[str] = []

    if components["temporal_persistence"] >= 0.70:
        positive_factors.append(f"Strong temporal persistence ({components['temporal_persistence']:.2f})")
    elif components["temporal_persistence"] < 0.40:
        limiting_factors.append(f"Low temporal persistence ({components['temporal_persistence']:.2f})")

    if components["temporal_consistency"] >= 0.70:
        positive_factors.append(f"High spatial tracking consistency across intervals ({components['temporal_consistency']:.2f})")
    elif components["temporal_consistency"] < 0.40:
        limiting_factors.append(f"Low spatial tracking overlap across intervals ({components['temporal_consistency']:.2f})")

    if components["change_magnitude"] >= 0.40:
        positive_factors.append(f"Significant physical change magnitude (mean ΔNDVI: {mean_delta_ndvi:.2f}, area: {area_m2:.0f} m²)")
    elif components["change_magnitude"] < 0.20:
        limiting_factors.append(f"Limited physical change magnitude (mean ΔNDVI: {mean_delta_ndvi:.2f})")

    if quality_support >= 0.70:
        positive_factors.append(f"High observation quality support ({quality_support:.2f})")
    elif 0.0 < quality_support < 0.40:
        limiting_factors.append(
            f"Limited observation quality support ({quality_support:.2f}): confidence is reduced due to limited observation quality, not because absence of change was demonstrated"
        )

    summary = (
        f"Deterministic evidence score {score:.3f} ({priority} priority, {severity} severity) "
        f"for {state} land-surface change."
    )
    severity_rationale = (
        f"Classified as '{severity}' physical severity based on observed extent "
        f"({area_m2:.0f} m², mean ΔNDVI {mean_delta_ndvi:.2f}, magnitude component {components['change_magnitude']:.2f})."
    )
    priority_rationale = (
        f"Assigned '{priority}' priority for triage based on operational urgency: "
        f"evidence score ({score:.3f}), physical severity ({severity}), "
        f"temporal behavior ({state}), and observation quality support ({quality_support:.2f})."
    )
    return CandidateExplanation(
        summary=summary,
        positive_factors=positive_factors,
        limiting_factors=limiting_factors,
        severity_rationale=severity_rationale,
        priority_rationale=priority_rationale,
    )


def _select() -> str:
    return """SELECT c.candidate_id, c.analysis_id, c.signal_id, c.geometry_json, c.score, c.rank, c.severity,
        c.priority, COALESCE(cr.decision, 'unreviewed') AS review_state, c.source_signal_ids_json, c.detection_run_ids_json,
        c.acquisition_ids_json, c.metrics_json, c.created_at, c.updated_at
        FROM candidates c
        LEFT JOIN candidate_reviews cr ON cr.candidate_id = c.candidate_id"""


def _from_row(row: tuple[Any, ...]) -> CandidateResponse:
    return CandidateResponse(
        candidate_id=row[0],
        analysis_id=row[1],
        signal_id=row[2],
        geometry=json.loads(row[3]),
        score=row[4],
        rank=row[5],
        severity=row[6],
        priority=row[7],
        review_state=row[8],
        source_signal_ids=json.loads(row[9]),
        detection_run_ids=json.loads(row[10]),
        acquisition_ids=json.loads(row[11]),
        metrics=CandidateMetrics.model_validate(json.loads(row[12])),
        created_at=datetime.fromisoformat(row[13]),
        updated_at=datetime.fromisoformat(row[14]),
    )


def _require_analysis(analysis_id: int) -> int:
    with connection() as db_connection:
        row = db_connection.execute("SELECT id FROM temporal_analyses WHERE id = ?", (analysis_id,)).fetchone()
    if row is None:
        raise MissingTemporalAnalysisError("The requested Phase 5 temporal analysis does not exist")
    return row[0]


def triage(analysis_id: int) -> CandidateListResponse:
    selected = _require_analysis(analysis_id)
    try:
        with connection() as db_connection:
            analysis = db_connection.execute(
                "SELECT detector_run_count, state FROM temporal_analyses WHERE id = ?", (selected,)
            ).fetchone()
            if analysis[1] == "insufficient_history":
                raise CandidateAnalysisUnavailableError("The requested temporal analysis has insufficient history")
            relationships = db_connection.execute(
                "SELECT detection_run_id FROM temporal_relationships WHERE analysis_id = ? ORDER BY detection_run_id",
                (selected,),
            ).fetchall()
            if len(relationships) != analysis[0]:
                raise CandidateUpstreamFailureError("Phase 5 references incomplete Phase 4 detection data")
            run_ids = [row[0] for row in relationships]
            if run_ids:
                marks = ",".join("?" for _ in run_ids)
                existing = db_connection.execute(
                    f"SELECT id FROM detection_runs WHERE id IN ({marks})", run_ids
                ).fetchall()
                if {row[0] for row in existing} != set(run_ids):
                    raise CandidateUpstreamFailureError(
                        "A Phase 5 relationship references a missing Phase 4 detection run"
                    )
            signals = db_connection.execute(
                """SELECT signal_id, geometry_json, support_count, interval_count, persistence_ratio,
                   recurrence_count, transient_interval_count, temporal_consistency, matched_region_coverage,
                   first_change_datetime, last_supporting_datetime, state, source_region_ids_json,
                   detection_run_ids_json, acquisition_ids_json, usable_interval_count,
                   supporting_interval_count, quality_support, interval_change_means_json,
                   interval_change_maxima_json FROM temporal_signals
                   WHERE analysis_id = ? ORDER BY signal_id""",
                (selected,),
            ).fetchall()
            now = datetime.now(UTC).isoformat()
            candidate_records: list[dict[str, Any]] = []
            for signal in signals:
                geometry = shape(json.loads(signal[1]))
                if geometry.is_empty or not geometry.is_valid:
                    raise CandidateUpstreamFailureError("A persisted Phase 5 signal has invalid geometry")

                source_region_ids = json.loads(signal[12]) if signal[12] else []
                detection_run_ids = json.loads(signal[13]) if signal[13] else []
                interval_means = json.loads(signal[18]) if len(signal) > 18 and signal[18] else []
                interval_maxima = json.loads(signal[19]) if len(signal) > 19 and signal[19] else []
                quality_support = float(signal[17]) if len(signal) > 17 and signal[17] is not None else 0.0

                area_m2, mean_delta, max_delta = _extract_signal_physical_metrics(
                    db_connection,
                    source_region_ids,
                    detection_run_ids,
                    interval_means,
                    interval_maxima,
                )

                score, components, norm_delta, norm_area, c_mag = _score_candidate(
                    persistence_ratio=signal[4],
                    temporal_consistency=signal[7],
                    mean_delta_ndvi=mean_delta,
                    area_m2=area_m2,
                    quality_support=quality_support,
                    matched_region_coverage=signal[8],
                )

                candidate_id = f"analysis-{selected}-signal-{signal[0]}"
                severity = _severity(norm_delta, norm_area, c_mag)
                priority = _priority(signal[11], score, severity, quality_support)
                explanation = _explain_candidate(
                    score=score,
                    components=components,
                    state=signal[11],
                    severity=severity,
                    priority=priority,
                    area_m2=area_m2,
                    mean_delta_ndvi=mean_delta,
                    quality_support=quality_support,
                )

                metrics = {
                    "support_count": signal[2],
                    "interval_count": signal[3],
                    "persistence_ratio": signal[4],
                    "recurrence_count": signal[5],
                    "transient_interval_count": signal[6],
                    "temporal_consistency": signal[7],
                    "matched_region_coverage": signal[8],
                    "first_change_datetime": signal[9],
                    "last_supporting_datetime": signal[10],
                    "temporal_state": signal[11],
                    "score_components": components,
                    "explanation": explanation.model_dump(),
                    "quality_support": quality_support,
                    "mean_change_signal": mean_delta,
                    "max_change_signal": max_delta,
                    "total_area_m2": area_m2,
                }

                candidate_records.append({
                    "candidate_id": candidate_id,
                    "analysis_id": selected,
                    "signal_id": signal[0],
                    "geometry_json": json.dumps(json.loads(signal[1]), separators=(",", ":")),
                    "score": score,
                    "severity": severity,
                    "priority": priority,
                    "source_signal_ids_json": json.dumps([signal[0]]),
                    "detection_run_ids_json": signal[13],
                    "acquisition_ids_json": signal[14],
                    "metrics_json": json.dumps(metrics),
                    "quality_support": quality_support,
                    "area_m2": area_m2,
                })

                db_connection.execute(
                    """INSERT INTO candidates (candidate_id, analysis_id, signal_id, geometry_json, score, rank,
                       severity, priority, review_state, source_signal_ids_json, detection_run_ids_json,
                       acquisition_ids_json, metrics_json, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, 0, ?, ?, COALESCE((SELECT review_state FROM candidates WHERE candidate_id = ?), 'unreviewed'), ?, ?, ?, ?, COALESCE((SELECT created_at FROM candidates WHERE candidate_id = ?), ?), ?)
                       ON CONFLICT(candidate_id) DO UPDATE SET geometry_json=excluded.geometry_json, score=excluded.score,
                       severity=excluded.severity, priority=excluded.priority, source_signal_ids_json=excluded.source_signal_ids_json,
                       detection_run_ids_json=excluded.detection_run_ids_json, acquisition_ids_json=excluded.acquisition_ids_json,
                       metrics_json=excluded.metrics_json, updated_at=excluded.updated_at""",
                    (
                        candidate_id,
                        selected,
                        signal[0],
                        json.dumps(json.loads(signal[1]), separators=(",", ":")),
                        score,
                        severity,
                        priority,
                        candidate_id,
                        json.dumps([signal[0]]),
                        signal[13],
                        signal[14],
                        json.dumps(metrics),
                        candidate_id,
                        now,
                        now,
                    ),
                )

            # Deterministic multi-tier triage ranking
            sorted_candidates = sorted(
                candidate_records,
                key=lambda rec: (
                    -PRIORITY_WEIGHTS.get(rec["priority"], 0),
                    -rec["score"],
                    -SEVERITY_WEIGHTS.get(rec["severity"], 0),
                    -rec["quality_support"],
                    -rec["area_m2"],
                    rec["signal_id"],  # deterministic stable tie-breaker
                ),
            )

            for rank, rec in enumerate(sorted_candidates, start=1):
                db_connection.execute(
                    "UPDATE candidates SET rank = ? WHERE candidate_id = ?", (rank, rec["candidate_id"])
                )
            db_connection.commit()

            rows = db_connection.execute(
                _select() + " WHERE c.analysis_id = ? ORDER BY c.rank ASC, c.candidate_id ASC", (selected,)
            ).fetchall()
    except (CandidateAnalysisUnavailableError, CandidateUpstreamFailureError):
        raise
    except Exception as exc:
        raise CandidateTriageFailureError("Candidate triage could not be completed") from exc
    return CandidateListResponse(analysis_id=selected, candidates=[_from_row(row) for row in rows])


def list_candidates(
    analysis_id: int | None = None,
    priority: str | None = None,
    severity: str | None = None,
    review_state: str | None = None,
    temporal_state: str | None = None,
    min_score: float | None = None,
    min_quality: float | None = None,
) -> CandidateListResponse:
    clauses: list[str] = []
    params: list[Any] = []

    if analysis_id is not None:
        selected = _require_analysis(analysis_id)
        clauses.append("c.analysis_id = ?")
        params.append(selected)
    else:
        selected = None

    if priority is not None:
        clauses.append("c.priority = ?")
        params.append(priority)

    if severity is not None:
        clauses.append("c.severity = ?")
        params.append(severity)

    if review_state is not None:
        clauses.append("COALESCE(cr.decision, 'unreviewed') = ?")
        params.append(review_state)

    if min_score is not None:
        clauses.append("c.score >= ?")
        params.append(min_score)

    if temporal_state is not None:
        clauses.append("json_extract(c.metrics_json, '$.temporal_state') = ?")
        params.append(temporal_state)

    if min_quality is not None:
        clauses.append("json_extract(c.metrics_json, '$.quality_support') >= ?")
        params.append(min_quality)

    where_sql = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    query = _select() + where_sql + " ORDER BY c.rank ASC, c.candidate_id ASC"

    with connection() as db_connection:
        rows = db_connection.execute(query, params).fetchall()

    return CandidateListResponse(analysis_id=selected, candidates=[_from_row(row) for row in rows])


def get_candidate(candidate_id: str) -> CandidateResponse:
    with connection() as db_connection:
        row = db_connection.execute(_select() + " WHERE c.candidate_id = ?", (candidate_id,)).fetchone()
    if row is None:
        raise CandidateNotFoundError("The requested candidate does not exist")
    return _from_row(row)


def validate_candidate_provenance(db_connection: sqlite3.Connection, candidate_id: str) -> None:
    candidate_row = db_connection.execute(
        "SELECT candidate_id, analysis_id, signal_id, geometry_json, detection_run_ids_json, acquisition_ids_json FROM candidates WHERE candidate_id = ?",
        (candidate_id,),
    ).fetchone()
    if candidate_row is None:
        raise CandidateNotFoundError("The requested candidate does not exist")

    analysis_id = candidate_row[1]
    signal_id = candidate_row[2]
    candidate_geom_raw = candidate_row[3]
    candidate_run_ids = json.loads(candidate_row[4])
    candidate_acq_ids = json.loads(candidate_row[5])

    # 1. Verify temporal analysis exists
    analysis = db_connection.execute(
        "SELECT id FROM temporal_analyses WHERE id = ?", (analysis_id,)
    ).fetchone()
    if analysis is None:
        raise CandidateEvidenceError("Candidate provenance references a missing temporal analysis")

    # 2. Verify temporal signal exists
    signal = db_connection.execute(
        "SELECT detection_run_ids_json, acquisition_ids_json FROM temporal_signals WHERE analysis_id = ? AND signal_id = ?",
        (analysis_id, signal_id),
    ).fetchone()
    if signal is None:
        raise CandidateEvidenceError("Candidate provenance references a missing temporal signal")

    signal_run_ids = json.loads(signal[0]) if signal[0] else []
    signal_acq_ids = json.loads(signal[1]) if signal[1] else []
    if signal_run_ids != candidate_run_ids or signal_acq_ids != candidate_acq_ids:
        raise CandidateEvidenceError("Candidate provenance does not match its temporal signal")
    if not candidate_run_ids or not candidate_acq_ids:
        raise CandidateEvidenceError("Candidate has no persisted detection runs or acquisitions")

    # 3. Verify acquisitions exist
    acq_marks = ",".join("?" for _ in candidate_acq_ids)
    acq_rows = db_connection.execute(
        f"SELECT id, aoi_id FROM imagery_acquisitions WHERE id IN ({acq_marks})",
        candidate_acq_ids,
    ).fetchall()
    if len(acq_rows) != len(candidate_acq_ids):
        raise CandidateEvidenceError("Candidate provenance references a missing acquisition")

    # 4. Verify AOI provenance & Karnataka intersection
    aoi_ids = {row[1] for row in acq_rows}
    if not aoi_ids:
        raise CandidateEvidenceError("Candidate evidence has no associated AOI")
    aoi_marks = ",".join("?" for _ in aoi_ids)
    aoi_rows = db_connection.execute(
        f"SELECT id, geometry_json FROM aoi WHERE id IN ({aoi_marks})",
        tuple(sorted(aoi_ids)),
    ).fetchall()
    if not aoi_rows:
        raise CandidateEvidenceError("Candidate evidence references a missing AOI")

    try:
        candidate_geom = shape(json.loads(candidate_geom_raw))
        if candidate_geom.is_empty or not candidate_geom.is_valid:
            raise CandidateEvidenceError("Candidate geometry is empty or invalid")
    except Exception as exc:
        raise CandidateEvidenceError(f"Candidate geometry is unreadable: {exc}")

    for aoi_row in aoi_rows:
        try:
            parsed_aoi = json.loads(aoi_row[1])
            if parsed_aoi and "coordinates" in parsed_aoi:
                aoi_geom = shape(parsed_aoi)
                if not aoi_geom.is_empty and aoi_geom.is_valid:
                    if not candidate_geom.intersects(aoi_geom):
                        raise CandidateEvidenceError("Candidate geometry does not intersect the persisted Karnataka AOI")
        except CandidateEvidenceError:
            raise
        except Exception:
            pass

    # 5. Verify detection runs exist and have distinct before/after matching acquisitions
    run_marks = ",".join("?" for _ in candidate_run_ids)
    run_rows = db_connection.execute(
        f"SELECT id, before_acquisition_id, after_acquisition_id FROM detection_runs WHERE id IN ({run_marks})",
        candidate_run_ids,
    ).fetchall()
    if len(run_rows) != len(candidate_run_ids):
        raise CandidateEvidenceError("Candidate provenance references a missing detection run")

    acq_id_set = set(candidate_acq_ids)
    for r in run_rows:
        if r[1] not in acq_id_set or r[2] not in acq_id_set:
            raise CandidateEvidenceError("Detection provenance references an acquisition outside the candidate signal")
        if r[1] == r[2]:
            raise CandidateEvidenceError("Detection provenance has identical Before and After acquisition IDs")


def get_candidate_review(candidate_id: str) -> CandidateReviewResponse:
    with connection() as db_connection:
        candidate_exists = db_connection.execute(
            "SELECT 1 FROM candidates WHERE candidate_id = ?", (candidate_id,)
        ).fetchone()
        if candidate_exists is None:
            raise CandidateNotFoundError("The requested candidate does not exist")

        row = db_connection.execute(
            "SELECT candidate_id, decision, note, created_at, updated_at FROM candidate_reviews WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone()

    if row is None:
        return CandidateReviewResponse(
            candidate_id=candidate_id,
            decision="unreviewed",
            note=None,
            created_at=None,
            updated_at=None,
        )

    return CandidateReviewResponse(
        candidate_id=row[0],
        decision=row[1],
        note=row[2],
        created_at=datetime.fromisoformat(row[3]) if row[3] else None,
        updated_at=datetime.fromisoformat(row[4]) if row[4] else None,
    )


def save_candidate_review(candidate_id: str, request: CandidateReviewRequest) -> CandidateReviewResponse:
    if request.decision not in VALID_DECISIONS:
        raise InvalidCandidateStateError(f"Decision '{request.decision}' is not allowed. Must be one of: {sorted(VALID_DECISIONS)}")

    now_iso = datetime.now(UTC).isoformat()
    note_val = request.note

    with connection() as db_connection:
        validate_candidate_provenance(db_connection, candidate_id)

        existing = db_connection.execute(
            "SELECT created_at FROM candidate_reviews WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone()

        if existing:
            created_at = existing[0]
            db_connection.execute(
                "UPDATE candidate_reviews SET decision = ?, note = ?, updated_at = ? WHERE candidate_id = ?",
                (request.decision, note_val, now_iso, candidate_id),
            )
        else:
            created_at = now_iso
            db_connection.execute(
                "INSERT INTO candidate_reviews (candidate_id, decision, note, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (candidate_id, request.decision, note_val, created_at, now_iso),
            )

        # Synchronize denormalized compatibility field in the exact same transaction
        db_connection.execute(
            "UPDATE candidates SET review_state = ?, updated_at = ? WHERE candidate_id = ?",
            (request.decision, now_iso, candidate_id),
        )
        db_connection.commit()

    return CandidateReviewResponse(
        candidate_id=candidate_id,
        decision=request.decision,
        note=note_val,
        created_at=datetime.fromisoformat(created_at),
        updated_at=datetime.fromisoformat(now_iso),
    )


def update_review_state(candidate_id: str, review_state: CandidateReviewState) -> CandidateResponse:
    if review_state not in REVIEW_STATES:
        raise InvalidCandidateStateError("The requested candidate review state is not allowed")

    if review_state in VALID_DECISIONS:
        save_candidate_review(candidate_id, CandidateReviewRequest(decision=review_state))
    else:
        # Reset to unreviewed in candidate_reviews and candidates
        now_iso = datetime.now(UTC).isoformat()
        with connection() as db_connection:
            candidate_exists = db_connection.execute(
                "SELECT 1 FROM candidates WHERE candidate_id = ?", (candidate_id,)
            ).fetchone()
            if candidate_exists is None:
                raise CandidateNotFoundError("The requested candidate does not exist")
            db_connection.execute("DELETE FROM candidate_reviews WHERE candidate_id = ?", (candidate_id,))
            db_connection.execute(
                "UPDATE candidates SET review_state = 'unreviewed', updated_at = ? WHERE candidate_id = ?",
                (now_iso, candidate_id),
            )
            db_connection.commit()

    return get_candidate(candidate_id)

