from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from shapely.geometry import shape
from shapely.strtree import STRtree
from shapely.ops import unary_union

from app.candidates import triage
from app.config import settings
from app.db import connection
from app.detection import DETECTOR_VERSION, detect
from app.domain import is_within_karnataka
from app.exceptions import (
    AOIOutsideKarnatakaError,
    InsufficientTemporalHistoryError,
    InvalidTemporalObservationsError,
    MissingDetectionError,
    NoAOIError,
    NoTemporalObservationsError,
    TemporalAnalysisFailureError,
)
from app.schemas import (
    AnalysisOrchestrationRequest,
    AnalysisOrchestrationResponse,
    CandidateListResponse,
    DetectionRequest,
    TemporalAnalysisRequest,
    TemporalAnalysisResponse,
    TemporalObservation,
    TemporalRelationship,
    TemporalSignal,
)

SEASONAL_DAY_TOLERANCE = 60


@dataclass(frozen=True)
class Region:
    region_id: int
    geometry: Any
    pixel_count: int


@dataclass(frozen=True)
class Interval:
    before_id: int
    after_id: int
    run_id: int
    regions: tuple[Region, ...]
    changed_pixel_count: int
    quality_support: float
    mean_change_signal: float
    max_change_signal: float
    evaluated: bool


def _load_observations(ids: list[int]) -> list[TemporalObservation]:
    placeholders = ",".join("?" for _ in ids)
    with connection() as db_connection:
        rows = db_connection.execute(
            f"SELECT id, item_id, acquisition_datetime, aoi_id, observation_state, quality_metrics_json, processing_version FROM imagery_acquisitions WHERE id IN ({placeholders})",
            ids,
        ).fetchall()
    if not rows:
        raise NoTemporalObservationsError("No imagery observations are available for the temporal request")
    by_id = {row[0]: row for row in rows}
    if len(by_id) != len(ids):
        raise NoTemporalObservationsError("One or more requested observations do not exist")
    ordered_rows = sorted((by_id[acquisition_id] for acquisition_id in ids), key=lambda row: (row[2], row[0]))
    datetimes = [datetime.fromisoformat(row[2]) for row in ordered_rows]
    if len(set(datetimes)) != len(datetimes):
        raise InvalidTemporalObservationsError("Observations must not share an acquisition timestamp")
    if any(left >= right for left, right in zip(datetimes, datetimes[1:])):
        raise InvalidTemporalObservationsError("Observations must have strictly increasing acquisition timestamps")
    aoi_ids = {row[3] for row in ordered_rows}
    if len(aoi_ids) != 1:
        raise InvalidTemporalObservationsError("Observations must belong to the same AOI")
    return [
        TemporalObservation(
            acquisition_id=row[0], item_id=row[1],
            acquisition_datetime=datetime.fromisoformat(row[2]), sequence_index=index,
            quality_state=row[4],
            usable_pixel_fraction=float(json.loads(row[5]).get("usable_percentage", 0.0)) / 100,
            cloud_fraction=float(json.loads(row[5]).get("cloud_percentage", 0.0)) / 100,
            shadow_fraction=float(json.loads(row[5]).get("shadow_percentage", 0.0)) / 100,
            invalid_fraction=float(json.loads(row[5]).get("invalid_percentage", 0.0)) / 100,
            quality_processing_version=row[6],
        )
        for index, row in enumerate(ordered_rows)
    ]


def _load_interval(before_id: int, after_id: int, quality_support: float) -> Interval:
    with connection() as db_connection:
        run = db_connection.execute(
                """SELECT id, changed_pixel_count, quality_mask_used,
                             quality_processing_version, mean_change_signal, max_change_signal
                    FROM detection_runs
               WHERE before_acquisition_id = ? AND after_acquisition_id = ?
               ORDER BY id DESC LIMIT 1""",
            (before_id, after_id),
        ).fetchone()
        if run is None:
            raise MissingDetectionError(f"No Phase 4 detection exists for acquisitions {before_id} and {after_id}")
        if run[2] != 1 or run[3] == "legacy-unassessed":
            raise MissingDetectionError(
                f"Detection for acquisitions {before_id} and {after_id} is not quality-aware"
            )
        rows = db_connection.execute(
            "SELECT id, geometry_json, pixel_count FROM raw_change_regions WHERE run_id = ? ORDER BY id ASC",
            (run[0],),
        ).fetchall()
    return Interval(
        before_id=before_id,
        after_id=after_id,
        run_id=run[0],
        changed_pixel_count=run[1],
        regions=tuple(Region(row[0], shape(json.loads(row[1])), row[2]) for row in rows),
        quality_support=quality_support,
        mean_change_signal=float(run[4]),
        max_change_signal=float(run[5]),
        evaluated=True,
    )


def _iou(left: Any, right: Any) -> float:
    intersection = left.intersection(right).area
    union = left.area + right.area - intersection
    return intersection / union if union else 0.0


def _seasonal_state(
    indices: list[int], observations: list[TemporalObservation], observation_count: int
) -> str:
    """Classify calendar comparability, not the physical cause of a signal.

    ``seasonal_compatible`` means that at least two supporting interval
    midpoints are within 60 calendar days of one another, using a circular
    year distance. It does not prove seasonal causation, construction,
    clearing, or any other land-use explanation, and is not a probability or
    calibrated confidence value.
    """
    if observation_count < 4:
        return "insufficient_temporal_evidence"
    if len(indices) < 2:
        return "less_seasonal_compatible"
    midpoints = [
        observations[index].acquisition_datetime
        + (observations[index + 1].acquisition_datetime - observations[index].acquisition_datetime) / 2
        for index in indices
    ]
    day_numbers = [midpoint.timetuple().tm_yday for midpoint in midpoints]
    anchor = day_numbers[0]
    seasonal_distance = [min(abs(day - anchor), 366 - abs(day - anchor)) for day in day_numbers[1:]]
    # Similar calendar-season support is compatible with vegetation response,
    # not proof of causality or a physical land-use explanation.
    return "seasonal_compatible" if seasonal_distance and max(seasonal_distance) <= SEASONAL_DAY_TOLERANCE else "less_seasonal_compatible"


def _signals(intervals: list[Interval], observations: list[TemporalObservation], threshold: float) -> tuple[list[TemporalSignal], list[list[list[int]]]]:
    tracks: list[list[tuple[int, Region, float]]] = []
    matched_pairs: list[list[list[int]]] = [[] for _ in intervals]
    total_interval_pixels = sum(region.pixel_count for interval in intervals for region in interval.regions)
    total_quality_support = sum(interval.quality_support for interval in intervals if interval.evaluated)
    for interval_index, interval in enumerate(intervals):
        assignments: dict[int, int] = {}
        track_geometries = [track[-1][1].geometry for track in tracks]
        spatial_index = STRtree(track_geometries) if track_geometries else None
        for region_index, region in enumerate(interval.regions):
            match_indexes = spatial_index.query(region.geometry) if spatial_index is not None else []
            matches = [
                (int(track_index), _iou(track_geometries[int(track_index)], region.geometry))
                for track_index in match_indexes
            ]
            matches = [match for match in matches if match[1] >= threshold]
            if matches:
                track_index, overlap = max(matches, key=lambda match: (match[1], -match[0]))
                if track_index not in assignments:
                    assignments[track_index] = region_index
                    matched_pairs[interval_index].append([tracks[track_index][-1][1].region_id, region.region_id])
                    tracks[track_index].append((interval_index, region, overlap))
                    continue
            tracks.append([(interval_index, region, 1.0)])
    output: list[TemporalSignal] = []
    for signal_id, track in enumerate(tracks, start=1):
        support_count = len(track)
        interval_count = len(intervals)
        indices = [entry[0] for entry in track]
        recurrence_count = sum(right > left + 1 for left, right in zip(indices, indices[1:]))
        transient_interval_count = 1 if support_count == 1 and interval_count > 1 else 0
        consistency = sum(entry[2] for entry in track) / support_count
        coverage = sum(entry[1].pixel_count for entry in track) / max(1, total_interval_pixels)
        if support_count == interval_count and interval_count > 1:
            state = "persistent"
        elif recurrence_count > 0:
            state = "recurrent"
        elif support_count > 0 and interval_count > 1:
            state = "transient"
        else:
            state = "isolated"
        support_quality = sum(intervals[index].quality_support for index in indices)
        persistence_ratio = support_quality / total_quality_support if total_quality_support else 0.0
        geometry = track[0][1].geometry if len(track) == 1 else unary_union([entry[1].geometry for entry in track])
        source_region_ids = [entry[1].region_id for entry in track]
        detection_run_ids = sorted({intervals[index].run_id for index in indices})
        acquisition_ids = sorted({value for index in indices for value in (intervals[index].before_id, intervals[index].after_id)})
        onset_interval = indices[0]
        interval_means = [intervals[index].mean_change_signal for index in indices]
        interval_maxima = [intervals[index].max_change_signal for index in indices]
        output.append(TemporalSignal(
            signal_id=signal_id,
            geometry=geometry.__geo_interface__,
            support_count=support_count,
            interval_count=interval_count,
            persistence_ratio=persistence_ratio,
            recurrence_count=recurrence_count,
            transient_interval_count=transient_interval_count,
            temporal_consistency=consistency,
            matched_region_coverage=coverage,
            first_change_datetime=observations[indices[0]].acquisition_datetime,
            last_supporting_datetime=observations[indices[-1] + 1].acquisition_datetime,
            state=state,
            usable_interval_count=sum(interval.evaluated for interval in intervals),
            supporting_interval_count=support_count,
            quality_support=(support_quality / support_count) if support_count else 0.0,
            onset_before_acquisition_id=intervals[onset_interval].before_id,
            onset_after_acquisition_id=intervals[onset_interval].after_id,
            onset_start_datetime=observations[onset_interval].acquisition_datetime,
            onset_end_datetime=observations[onset_interval + 1].acquisition_datetime,
            seasonal_interpretation=_seasonal_state(indices, observations, len(observations)),
            interval_change_means=interval_means,
            interval_change_maxima=interval_maxima,
            source_region_ids=source_region_ids,
            detection_run_ids=detection_run_ids,
            acquisition_ids=acquisition_ids,
        ))
    return sorted(output, key=lambda signal: (signal.first_change_datetime, signal.source_region_ids[0])), matched_pairs


def analyze(request: TemporalAnalysisRequest) -> TemporalAnalysisResponse:
    observations = _load_observations(request.acquisition_ids)
    quality_supported = all(
        observation.quality_state == "usable" and observation.usable_pixel_fraction > 0
        for observation in observations
    )
    intervals: list[Interval] = []
    if len(observations) >= 2 and quality_supported:
        intervals = [
            _load_interval(
                left.acquisition_id,
                right.acquisition_id,
                min(left.usable_pixel_fraction, right.usable_pixel_fraction),
            )
            for left, right in zip(observations, observations[1:])
        ]
    signals, matched_pairs = (
        _signals(intervals, observations, request.iou_threshold)
        if intervals else ([], [])
    )
    analysis_state = "insufficient_history" if len(observations) < 3 or not quality_supported else "no_temporal_signal"
    if analysis_state != "insufficient_history":
        if any(signal.state == "persistent" for signal in signals):
            analysis_state = "persistent"
        elif any(signal.state == "recurrent" for signal in signals):
            analysis_state = "recurrent"
        elif any(signal.state == "transient" for signal in signals):
            analysis_state = "transient"
        elif signals:
            analysis_state = "isolated"
    created_at = datetime.now(UTC)
    temporal_span_days = (observations[-1].acquisition_datetime - observations[0].acquisition_datetime).total_seconds() / 86400
    quality_support = sum(interval.quality_support for interval in intervals) / len(intervals) if intervals else 0.0
    seasonal_interpretation = "insufficient_temporal_evidence"
    if len(observations) >= 4 and signals:
        seasonal_interpretation = (
            "seasonal_compatible"
            if any(signal.seasonal_interpretation == "seasonal_compatible" for signal in signals)
            else "less_seasonal_compatible"
        )
    relationships = [
        TemporalRelationship(
            before_acquisition_id=interval.before_id, after_acquisition_id=interval.after_id,
            detection_run_id=interval.run_id, region_count=len(interval.regions),
            changed_pixel_count=interval.changed_pixel_count,
            before_region_ids=[region.region_id for region in interval.regions],
            after_region_ids=[pair[1] for pair in matched_pairs[index]],
            matched_region_pairs=matched_pairs[index],
            elapsed_days=(observations[index + 1].acquisition_datetime - observations[index].acquisition_datetime).total_seconds() / 86400,
            quality_support=interval.quality_support,
            quality_state="usable" if interval.evaluated else "insufficient_quality_support",
            evaluated=interval.evaluated,
        ) for index, interval in enumerate(intervals)]
    try:
        with connection() as db_connection:
            cursor = db_connection.execute(
                """INSERT INTO temporal_analyses (
                    iou_threshold, detector_run_count, observation_count,
                    temporal_span_days, state, created_at, usable_observation_count,
                    usable_interval_count, quality_support, quality_aggregation_method,
                    seasonal_interpretation
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (request.iou_threshold, len(intervals), len(observations), temporal_span_days,
                 analysis_state, created_at.isoformat(),
                 sum(observation.quality_state == "usable" for observation in observations),
                 sum(interval.evaluated for interval in intervals), quality_support,
                 "mean_min_usable_fraction", seasonal_interpretation),
            )
            analysis_id = cursor.lastrowid
            for observation in observations:
                db_connection.execute(
                    "INSERT INTO temporal_observations (analysis_id, acquisition_id, sequence_index) VALUES (?, ?, ?)",
                    (analysis_id, observation.acquisition_id, observation.sequence_index),
                )
            for relationship in relationships:
                db_connection.execute(
                    "INSERT INTO temporal_relationships (analysis_id, before_acquisition_id, after_acquisition_id, detection_run_id, region_count, changed_pixel_count, before_region_ids_json, after_region_ids_json, matched_region_pairs_json, elapsed_days, quality_support, quality_state, evaluated) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (analysis_id, relationship.before_acquisition_id, relationship.after_acquisition_id, relationship.detection_run_id, relationship.region_count, relationship.changed_pixel_count, json.dumps(relationship.before_region_ids), json.dumps(relationship.after_region_ids), json.dumps(relationship.matched_region_pairs), relationship.elapsed_days, relationship.quality_support, relationship.quality_state, relationship.evaluated),
                )
            for signal in signals:
                db_connection.execute(
                    """INSERT INTO temporal_signals (
                        analysis_id, signal_id, geometry_json, support_count, interval_count,
                        persistence_ratio, recurrence_count, transient_interval_count,
                        temporal_consistency, matched_region_coverage, first_change_datetime,
                        last_supporting_datetime, state, source_region_ids_json,
                        detection_run_ids_json, acquisition_ids_json, usable_interval_count,
                        supporting_interval_count, quality_support, onset_before_acquisition_id,
                        onset_after_acquisition_id, onset_start_datetime, onset_end_datetime,
                        seasonal_interpretation, interval_change_means_json, interval_change_maxima_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (analysis_id, signal.signal_id, json.dumps(signal.geometry), signal.support_count, signal.interval_count,
                     signal.persistence_ratio, signal.recurrence_count, signal.transient_interval_count,
                     signal.temporal_consistency, signal.matched_region_coverage,
                     signal.first_change_datetime.isoformat(), signal.last_supporting_datetime.isoformat(), signal.state,
                     json.dumps(signal.source_region_ids), json.dumps(signal.detection_run_ids),
                     json.dumps(signal.acquisition_ids), signal.usable_interval_count,
                     signal.supporting_interval_count, signal.quality_support,
                     signal.onset_before_acquisition_id, signal.onset_after_acquisition_id,
                     signal.onset_start_datetime.isoformat() if signal.onset_start_datetime else None,
                     signal.onset_end_datetime.isoformat() if signal.onset_end_datetime else None,
                     signal.seasonal_interpretation, json.dumps(signal.interval_change_means),
                     json.dumps(signal.interval_change_maxima)),
                )
            db_connection.commit()
    except Exception as exc:
        raise TemporalAnalysisFailureError("The temporal analysis could not be persisted") from exc
    return TemporalAnalysisResponse(
        analysis_id=analysis_id, iou_threshold=request.iou_threshold,
        detector_run_count=len(intervals), observation_count=len(observations),
        temporal_span_days=temporal_span_days, state=analysis_state,
        usable_observation_count=sum(observation.quality_state == "usable" for observation in observations),
        usable_interval_count=sum(interval.evaluated for interval in intervals),
        quality_support=quality_support,
        quality_aggregation_method="mean_min_usable_fraction",
        seasonal_interpretation=seasonal_interpretation,
        observations=observations, relationships=relationships, signals=signals,
        created_at=created_at,
    )


def get_analysis(analysis_id: int) -> TemporalAnalysisResponse:
    with connection() as db_connection:
        analysis = db_connection.execute(
            "SELECT id, iou_threshold, detector_run_count, observation_count, temporal_span_days, state, created_at, usable_observation_count, usable_interval_count, quality_support, quality_aggregation_method, seasonal_interpretation FROM temporal_analyses WHERE id = ?",
            (analysis_id,),
        ).fetchone()
        if analysis is None:
            raise NoTemporalObservationsError(f"Temporal analysis {analysis_id} does not exist")
        observations = db_connection.execute(
            """SELECT acquisition_id, sequence_index FROM temporal_observations
               WHERE analysis_id = ? ORDER BY sequence_index""",
            (analysis_id,),
        ).fetchall()
        acquisition_rows = {
            row[0]: row for row in db_connection.execute(
                "SELECT id, item_id, acquisition_datetime, observation_state, quality_metrics_json, processing_version FROM imagery_acquisitions WHERE id IN (%s)"
                % ",".join("?" for _ in observations),
                [row[0] for row in observations],
            ).fetchall()
        } if observations else {}
        relationship_rows = db_connection.execute(
            """SELECT before_acquisition_id, after_acquisition_id, detection_run_id,
                      region_count, changed_pixel_count, before_region_ids_json,
                      after_region_ids_json, matched_region_pairs_json,
                      elapsed_days, quality_support, quality_state, evaluated
               FROM temporal_relationships WHERE analysis_id = ?
               ORDER BY before_acquisition_id""",
            (analysis_id,),
        ).fetchall()
        signal_rows = db_connection.execute(
            """SELECT signal_id, geometry_json, support_count, interval_count,
                      persistence_ratio, recurrence_count, transient_interval_count,
                      temporal_consistency, matched_region_coverage,
                      first_change_datetime, last_supporting_datetime, state,
                      source_region_ids_json, detection_run_ids_json, acquisition_ids_json,
                      usable_interval_count, supporting_interval_count, quality_support,
                      onset_before_acquisition_id, onset_after_acquisition_id,
                      onset_start_datetime, onset_end_datetime, seasonal_interpretation,
                      interval_change_means_json, interval_change_maxima_json
               FROM temporal_signals WHERE analysis_id = ? ORDER BY signal_id""",
            (analysis_id,),
        ).fetchall()
    temporal_observations = [
        TemporalObservation(
            acquisition_id=row[0], item_id=acquisition_rows[row[0]][1],
            acquisition_datetime=datetime.fromisoformat(acquisition_rows[row[0]][2]),
            sequence_index=row[1],
            quality_state=acquisition_rows[row[0]][3],
            usable_pixel_fraction=float(json.loads(acquisition_rows[row[0]][4]).get("usable_percentage", 0.0)) / 100,
            cloud_fraction=float(json.loads(acquisition_rows[row[0]][4]).get("cloud_percentage", 0.0)) / 100,
            shadow_fraction=float(json.loads(acquisition_rows[row[0]][4]).get("shadow_percentage", 0.0)) / 100,
            invalid_fraction=float(json.loads(acquisition_rows[row[0]][4]).get("invalid_percentage", 0.0)) / 100,
            quality_processing_version=acquisition_rows[row[0]][5],
        ) for row in observations
    ]
    relationships = [
        TemporalRelationship(
            before_acquisition_id=row[0], after_acquisition_id=row[1], detection_run_id=row[2],
            region_count=row[3], changed_pixel_count=row[4],
            before_region_ids=json.loads(row[5]), after_region_ids=json.loads(row[6]),
            matched_region_pairs=json.loads(row[7]),
            elapsed_days=row[8], quality_support=row[9], quality_state=row[10], evaluated=bool(row[11]),
        ) for row in relationship_rows
    ]
    signals = [
        TemporalSignal(
            signal_id=row[0], geometry=json.loads(row[1]), support_count=row[2], interval_count=row[3],
            persistence_ratio=row[4], recurrence_count=row[5], transient_interval_count=row[6],
            temporal_consistency=row[7], matched_region_coverage=row[8],
            first_change_datetime=datetime.fromisoformat(row[9]),
            last_supporting_datetime=datetime.fromisoformat(row[10]), state=row[11],
            source_region_ids=json.loads(row[12]), detection_run_ids=json.loads(row[13]),
            acquisition_ids=json.loads(row[14]),
            usable_interval_count=row[15], supporting_interval_count=row[16],
            quality_support=row[17], onset_before_acquisition_id=row[18],
            onset_after_acquisition_id=row[19],
            onset_start_datetime=datetime.fromisoformat(row[20]) if row[20] else None,
            onset_end_datetime=datetime.fromisoformat(row[21]) if row[21] else None,
            seasonal_interpretation=row[22],
            interval_change_means=json.loads(row[23]), interval_change_maxima=json.loads(row[24]),
        ) for row in signal_rows
    ]
    return TemporalAnalysisResponse(
        analysis_id=analysis[0], iou_threshold=analysis[1], detector_run_count=analysis[2],
        observation_count=analysis[3], temporal_span_days=analysis[4], state=analysis[5],
        usable_observation_count=analysis[7], usable_interval_count=analysis[8],
        quality_support=analysis[9], quality_aggregation_method=analysis[10],
        seasonal_interpretation=analysis[11],
        observations=temporal_observations, relationships=relationships, signals=signals,
        created_at=datetime.fromisoformat(analysis[6]),
    )


def find_compatible_detection(
    db_connection: Any,
    before_id: int,
    after_id: int,
    aoi_id: int,
    threshold: float = 0.20,
    min_region_pixels: int = 4,
) -> int | None:
    """Find an existing compatible quality-aware detection run for this exact pair and AOI.

    Must strictly satisfy:
    - exact before_acquisition_id and after_acquisition_id
    - both acquisitions belong to the requested aoi_id
    - both acquisitions are quality-assessed and usable with positive usable percentage
    - quality_mask_used == 1
    - quality_processing_version != 'legacy-unassessed'
    - detector_version == DETECTOR_VERSION ('ndvi-absolute-difference-v1')
    - ABS(threshold - request_threshold) < 1e-6
    - min_region_pixels == request_min_region_pixels
    """
    row = db_connection.execute(
        """SELECT d.id, d.threshold, d.min_region_pixels, b.aoi_id, a.aoi_id,
                  b.observation_state, a.observation_state
           FROM detection_runs d
           JOIN imagery_acquisitions b ON b.id = d.before_acquisition_id
           JOIN imagery_acquisitions a ON a.id = d.after_acquisition_id
           WHERE d.before_acquisition_id = ? AND d.after_acquisition_id = ?
             AND d.quality_mask_used = 1
             AND d.quality_processing_version != 'legacy-unassessed'
             AND d.detector_version = ?
           ORDER BY d.id DESC LIMIT 1""",
        (before_id, after_id, DETECTOR_VERSION),
    ).fetchone()
    if row is None:
        return None
    run_id, run_thresh, run_min_pixels, b_aoi, a_aoi, b_state, a_state = row
    if b_aoi != aoi_id or a_aoi != aoi_id:
        return None
    if b_state != "usable" or a_state != "usable":
        return None
    if abs(run_thresh - threshold) > 1e-6:
        return None
    if run_min_pixels != min_region_pixels:
        return None
    return int(run_id)


def ensure_detection(
    before_id: int,
    after_id: int,
    aoi_id: int,
    threshold: float = 0.20,
    min_region_pixels: int = 4,
) -> tuple[int, bool]:
    """Ensure a valid detection run exists for the interval.

    Returns (run_id, was_generated).
    If an existing compatible run exists, reuses it (was_generated=False).
    Otherwise executes detection and persists it (was_generated=True).
    Explicitly surfaces any detection errors without masking.
    """
    with connection() as db_connection:
        existing_run_id = find_compatible_detection(
            db_connection,
            before_id=before_id,
            after_id=after_id,
            aoi_id=aoi_id,
            threshold=threshold,
            min_region_pixels=min_region_pixels,
        )
    if existing_run_id is not None:
        return existing_run_id, False

    response = detect(
        DetectionRequest(
            before_acquisition_id=before_id,
            after_acquisition_id=after_id,
            threshold=threshold,
            min_region_pixels=min_region_pixels,
        )
    )
    return response.run_id, True


def find_matching_temporal_analysis(
    acquisition_ids: list[int],
    aoi_id: int,
    iou_threshold: float,
    threshold: float = 0.20,
    min_region_pixels: int = 4,
) -> int | None:
    """Find an existing temporal analysis matching the exact chronological acquisition sequence.

    Also verifies that every adjacent relationship's detection_run_id matches the active compatible detection run.
    """
    with connection() as db_connection:
        candidates = db_connection.execute(
            """SELECT id FROM temporal_analyses
               WHERE observation_count = ? AND ABS(iou_threshold - ?) < 1e-6
               ORDER BY id DESC""",
            (len(acquisition_ids), iou_threshold),
        ).fetchall()
        for (analysis_id,) in candidates:
            obs = db_connection.execute(
                """SELECT acquisition_id FROM temporal_observations
                   WHERE analysis_id = ? ORDER BY sequence_index ASC""",
                (analysis_id,),
            ).fetchall()
            if [r[0] for r in obs] != acquisition_ids:
                continue
            rels = db_connection.execute(
                """SELECT before_acquisition_id, after_acquisition_id, detection_run_id
                   FROM temporal_relationships
                   WHERE analysis_id = ?
                   ORDER BY before_acquisition_id ASC""",
                (analysis_id,),
            ).fetchall()
            if len(rels) != len(acquisition_ids) - 1:
                continue
            all_valid = True
            for before_id, after_id, run_id in rels:
                compatible_run = find_compatible_detection(
                    db_connection,
                    before_id=before_id,
                    after_id=after_id,
                    aoi_id=aoi_id,
                    threshold=threshold,
                    min_region_pixels=min_region_pixels,
                )
                if compatible_run != run_id:
                    all_valid = False
                    break
            if all_valid:
                return int(analysis_id)
    return None


def orchestrate_analysis(request: AnalysisOrchestrationRequest) -> AnalysisOrchestrationResponse:
    """Execute the full automatic analysis pipeline for an AOI.

    1. Validates AOI existence and Karnataka containment.
    2. Authoritatively selects eligible observations within optional date bounds.
    3. Sorts observations chronologically and constructs adjacent intervals.
    4. Reuses or generates missing compatible detections for each interval.
    5. Reuses or generates temporal analysis.
    6. Generates/triages candidates idempotently.
    7. Returns complete authoritative result.
    """
    with connection() as db_connection:
        aoi_row = db_connection.execute(
            "SELECT id, geometry_json FROM aoi WHERE id = ?", (request.aoi_id,)
        ).fetchone()
    if aoi_row is None:
        raise NoAOIError(f"AOI {request.aoi_id} does not exist")
    aoi_geom = json.loads(aoi_row[1])
    if not is_within_karnataka(shape(aoi_geom)):
        raise AOIOutsideKarnatakaError("The requested AOI is not within Karnataka")

    query = """
        SELECT id, acquisition_datetime, observation_state, quality_metrics_json
        FROM imagery_acquisitions
        WHERE aoi_id = ?
    """
    params: list[Any] = [request.aoi_id]
    if request.start_datetime:
        query += " AND acquisition_datetime >= ?"
        params.append(request.start_datetime.isoformat())
    if request.end_datetime:
        query += " AND acquisition_datetime <= ?"
        params.append(request.end_datetime.isoformat())
    query += " ORDER BY acquisition_datetime ASC, id ASC"

    with connection() as db_connection:
        rows = db_connection.execute(query, params).fetchall()

    eligible_ids: list[int] = []
    for row in rows:
        acq_id, dt_str, state, metrics_json = row
        if state != "usable":
            continue
        metrics = json.loads(metrics_json) if metrics_json else {}
        usable_pct = float(metrics.get("usable_percentage", 0.0))
        if usable_pct <= 0:
            continue
        eligible_ids.append(acq_id)

    if len(eligible_ids) == 0:
        raise NoTemporalObservationsError("No eligible observations are available for the requested AOI and analysis period")
    if len(eligible_ids) == 1:
        raise InsufficientTemporalHistoryError("At least two eligible observations are required to construct an analysis interval")

    pairs = [(eligible_ids[i], eligible_ids[i + 1]) for i in range(len(eligible_ids) - 1)]

    reused_detections = 0
    generated_detections = 0
    min_region_pixels = settings.minimum_detection_region_pixels

    for before_id, after_id in pairs:
        _, was_generated = ensure_detection(
            before_id=before_id,
            after_id=after_id,
            aoi_id=request.aoi_id,
            threshold=request.threshold,
            min_region_pixels=min_region_pixels,
        )
        if was_generated:
            generated_detections += 1
        else:
            reused_detections += 1

    matching_analysis_id = find_matching_temporal_analysis(
        acquisition_ids=eligible_ids,
        aoi_id=request.aoi_id,
        iou_threshold=request.iou_threshold,
        threshold=request.threshold,
        min_region_pixels=min_region_pixels,
    )

    if matching_analysis_id is not None:
        analysis = get_analysis(matching_analysis_id)
        reused_analysis = True
    else:
        analysis = analyze(
            TemporalAnalysisRequest(
                acquisition_ids=eligible_ids,
                iou_threshold=request.iou_threshold,
            )
        )
        reused_analysis = False

    if analysis.state == "insufficient_history":
        candidates = CandidateListResponse(analysis_id=analysis.analysis_id, candidates=[])
    else:
        candidates = triage(analysis.analysis_id)

    return AnalysisOrchestrationResponse(
        aoi_id=request.aoi_id,
        analysis=analysis,
        candidates=candidates,
        eligible_observation_ids=eligible_ids,
        reused_detection_count=reused_detections,
        generated_detection_count=generated_detections,
        reused_analysis=reused_analysis,
    )


def orchestrate_analysis_events(request: AnalysisOrchestrationRequest) -> Iterator[dict[str, Any]]:
    """Yield progressive orchestration events during execution."""
    yield {
        "phase": "preparing_observations",
        "message": "Evaluating observation eligibility...",
    }

    with connection() as db_connection:
        aoi_row = db_connection.execute(
            "SELECT id, geometry_json FROM aoi WHERE id = ?", (request.aoi_id,)
        ).fetchone()
    if aoi_row is None:
        raise NoAOIError(f"AOI {request.aoi_id} does not exist")
    aoi_geom = json.loads(aoi_row[1])
    if not is_within_karnataka(shape(aoi_geom)):
        raise AOIOutsideKarnatakaError("The requested AOI is not within Karnataka")

    query = """
        SELECT id, acquisition_datetime, observation_state, quality_metrics_json
        FROM imagery_acquisitions
        WHERE aoi_id = ?
    """
    params: list[Any] = [request.aoi_id]
    if request.start_datetime:
        query += " AND acquisition_datetime >= ?"
        params.append(request.start_datetime.isoformat())
    if request.end_datetime:
        query += " AND acquisition_datetime <= ?"
        params.append(request.end_datetime.isoformat())
    query += " ORDER BY acquisition_datetime ASC, id ASC"

    with connection() as db_connection:
        rows = db_connection.execute(query, params).fetchall()

    eligible_ids: list[int] = []
    for row in rows:
        acq_id, dt_str, state, metrics_json = row
        if state != "usable":
            continue
        metrics = json.loads(metrics_json) if metrics_json else {}
        usable_pct = float(metrics.get("usable_percentage", 0.0))
        if usable_pct <= 0:
            continue
        eligible_ids.append(acq_id)

    if len(eligible_ids) == 0:
        raise NoTemporalObservationsError("No eligible observations are available for the requested AOI and analysis period")
    if len(eligible_ids) == 1:
        raise InsufficientTemporalHistoryError("At least two eligible observations are required to construct an analysis interval")

    pairs = [(eligible_ids[i], eligible_ids[i + 1]) for i in range(len(eligible_ids) - 1)]

    yield {
        "phase": "preparing_comparisons",
        "message": f"Forming {len(pairs)} chronological observation intervals...",
        "eligible_count": len(eligible_ids),
        "pair_count": len(pairs),
    }

    reused_detections = 0
    generated_detections = 0
    min_region_pixels = settings.minimum_detection_region_pixels

    for index, (before_id, after_id) in enumerate(pairs, start=1):
        yield {
            "phase": "detecting_change",
            "pair_index": index,
            "total_pairs": len(pairs),
            "before_id": before_id,
            "after_id": after_id,
            "message": f"Ensuring detection for interval {index} of {len(pairs)}...",
        }
        _, was_generated = ensure_detection(
            before_id=before_id,
            after_id=after_id,
            aoi_id=request.aoi_id,
            threshold=request.threshold,
            min_region_pixels=min_region_pixels,
        )
        if was_generated:
            generated_detections += 1
        else:
            reused_detections += 1

    yield {
        "phase": "analyzing_history",
        "message": "Building change history...",
    }

    matching_analysis_id = find_matching_temporal_analysis(
        acquisition_ids=eligible_ids,
        aoi_id=request.aoi_id,
        iou_threshold=request.iou_threshold,
        threshold=request.threshold,
        min_region_pixels=min_region_pixels,
    )

    if matching_analysis_id is not None:
        analysis = get_analysis(matching_analysis_id)
        reused_analysis = True
    else:
        analysis = analyze(
            TemporalAnalysisRequest(
                acquisition_ids=eligible_ids,
                iou_threshold=request.iou_threshold,
            )
        )
        reused_analysis = False

    yield {
        "phase": "preparing_candidates",
        "message": "Triaging and ranking candidates...",
    }

    if analysis.state == "insufficient_history":
        candidates = CandidateListResponse(analysis_id=analysis.analysis_id, candidates=[])
    else:
        candidates = triage(analysis.analysis_id)

    response = AnalysisOrchestrationResponse(
        aoi_id=request.aoi_id,
        analysis=analysis,
        candidates=candidates,
        eligible_observation_ids=eligible_ids,
        reused_detection_count=reused_detections,
        generated_detection_count=generated_detections,
        reused_analysis=reused_analysis,
    )

    yield {
        "phase": "complete",
        "message": "Analysis orchestration complete",
        "result": response.model_dump(mode="json"),
    }