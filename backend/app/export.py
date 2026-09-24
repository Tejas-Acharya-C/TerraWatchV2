from __future__ import annotations

import io
import json
import re
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import HRFlowable, Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from shapely.geometry import shape

from app.candidates import get_candidate
from app.db import connection
from app.domain import is_within_karnataka
from app.evidence import get_candidate_evidence
from app.exceptions import CandidateEvidenceError, CandidateNotFoundError
from app.imagery import render_display
from app.schemas import CandidateEvidenceResponse

INVESTIGATION_EXPORT_VERSION = "terrawatch-investigation-v1"


class PortableObservationEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    acquisition_id: int
    aoi_id: int
    item_id: str
    collection: str
    acquisition_datetime: str
    cloud_cover: float | None = None
    mgrs_tile: str | None = None
    observation_state: str
    quality_reason: str | None = None
    usable_pixel_fraction: float | None = None
    cloud_fraction: float | None = None
    shadow_fraction: float | None = None
    invalid_fraction: float | None = None
    quality_processing_version: str | None = None
    masking_method: str | None = None
    quality_mask_available: bool = False
    display_available: bool = False
    visualization_version: str | None = None
    bands: list[str] = Field(default_factory=list)


class PortableDetectionSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    detector_version: str
    threshold: float
    min_region_pixels: int
    changed_pixel_count: int
    raw_changed_pixel_count: int
    filtered_changed_pixel_count: int
    changed_pixel_percentage: float
    total_changed_area_m2: float
    largest_region_area_m2: float
    mean_region_area_m2: float
    median_region_area_m2: float
    mean_change_signal: float
    max_change_signal: float
    quality_valid_pixel_count: int
    excluded_pixel_count: int
    excluded_cloud_pixel_count: int
    excluded_shadow_pixel_count: int


class PortableTemporalEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: str
    interval_count: int
    persistence_ratio: float
    support_count: int
    recurrence_count: int
    transient_interval_count: int
    temporal_consistency: float
    matched_region_coverage: float
    first_change_datetime: str | None = None
    last_supporting_datetime: str | None = None
    quality_support: float


class PortableAnalystReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: str
    note: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class PortableSystemExplanation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str
    positive_factors: list[str] = Field(default_factory=list)
    limiting_factors: list[str] = Field(default_factory=list)


class PortableObservationQuality(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_quality_limited: bool
    quality_limitation_reasons: list[str] = Field(default_factory=list)
    spatial_alignment: dict[str, Any] = Field(default_factory=dict)


class PortableInvestigationProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    analysis_id: int
    signal_id: int
    aoi_id: int
    karnataka_contained: bool
    detection_run_ids: list[int]
    acquisition_ids: list[int]
    before_acquisition_id: int
    after_acquisition_id: int
    before_item_id: str
    after_item_id: str
    crs: str
    detector_version: str
    processing_version: str
    masking_method: str
    visualization_version: str


class InvestigationSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    export_version: str = Field(default=INVESTIGATION_EXPORT_VERSION)
    generated_at: str
    candidate_id: str
    analysis_id: int
    signal_id: int
    aoi_id: int
    priority: str
    severity: str
    score: float
    rank: int
    geometry: dict[str, Any]
    centroid: dict[str, float]
    detection_summary: PortableDetectionSummary
    temporal_evidence: PortableTemporalEvidence
    before_observation: PortableObservationEvidence
    after_observation: PortableObservationEvidence
    all_observations: list[PortableObservationEvidence]
    observation_quality: PortableObservationQuality
    system_explanation: PortableSystemExplanation
    analyst_review: PortableAnalystReview
    provenance: PortableInvestigationProvenance


def _to_iso(dt_val: Any) -> str | None:
    if dt_val is None:
        return None
    if isinstance(dt_val, datetime):
        return dt_val.isoformat()
    return str(dt_val)


def sanitize_filename_candidate_id(candidate_id: str) -> str:
    """Produce a filesystem-safe identifier for Content-Disposition filename."""
    return re.sub(r"[^a-zA-Z0-9_\-]", "_", candidate_id)


def resolve_investigation_snapshot(candidate_id: str) -> InvestigationSnapshot:
    """
    Resolves the authoritative, internally consistent investigation state for export.
    Zero-mutation: only reads existing persisted state.
    Strictly uses candidate_reviews as authoritative review source of truth.
    Validates the entire provenance chain and geometry containment.
    Strictly excludes internal filesystem paths.
    """
    # 1. Authoritative evidence resolver (validates Karnataka domain, temporal signal, acquisitions, rasters, detection runs, intervals)
    evidence: CandidateEvidenceResponse = get_candidate_evidence(candidate_id)

    # 2. Authoritative review resolver: query candidate_reviews directly in a read connection
    with connection() as db:
        candidate_row = db.execute(
            "SELECT candidate_id, score, rank, priority, severity FROM candidates WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone()
        if candidate_row is None:
            raise CandidateNotFoundError(f"Candidate {candidate_id} not found")

        review_row = db.execute(
            "SELECT candidate_id, decision, note, created_at, updated_at FROM candidate_reviews WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone()

        aoi_row = db.execute(
            "SELECT id, geometry_json FROM aoi WHERE id = ?",
            (evidence.aoi_id,),
        ).fetchone()
        if aoi_row is None:
            raise CandidateEvidenceError("AOI provenance is missing from database")

    # Authoritative review semantics: if no row in candidate_reviews, decision is "unreviewed".
    # NEVER fall back to candidates.review_state.
    if review_row is None:
        analyst_review = PortableAnalystReview(
            decision="unreviewed",
            note=None,
            created_at=None,
            updated_at=None,
        )
    else:
        analyst_review = PortableAnalystReview(
            decision=review_row[1],
            note=review_row[2],
            created_at=_to_iso(review_row[3]),
            updated_at=_to_iso(review_row[4]),
        )

    # 3. Provenance & consistency validation
    if not evidence.intervals:
        raise CandidateEvidenceError("Investigation evidence has no detection intervals")

    primary_interval = evidence.intervals[0]
    if primary_interval.before_acquisition_id == primary_interval.after_acquisition_id:
        raise CandidateEvidenceError("Investigation provenance has identical Before and After acquisition IDs")

    cand_geom = shape(evidence.candidate.geometry)
    if cand_geom.is_empty or not cand_geom.is_valid or not is_within_karnataka(cand_geom):
        raise CandidateEvidenceError("Candidate geometry is outside the Karnataka evidence domain")

    aoi_geom = shape(json.loads(aoi_row[1]))
    if aoi_geom.is_empty or not aoi_geom.is_valid or not is_within_karnataka(aoi_geom):
        raise CandidateEvidenceError("AOI geometry is outside the Karnataka evidence domain")

    if not cand_geom.intersects(aoi_geom):
        raise CandidateEvidenceError("Candidate geometry does not intersect the persisted Karnataka AOI")

    # Calculate geographic centroid
    centroid_pt = cand_geom.centroid
    centroid = {"longitude": float(centroid_pt.x), "latitude": float(centroid_pt.y)}

    # Build portable observations (no filesystem paths)
    portable_acqs: dict[int, PortableObservationEvidence] = {}
    for acq in evidence.acquisitions:
        portable_acqs[acq.acquisition_id] = PortableObservationEvidence(
            acquisition_id=acq.acquisition_id,
            aoi_id=acq.aoi_id,
            item_id=acq.item_id,
            collection=acq.collection,
            acquisition_datetime=_to_iso(acq.acquisition_datetime) or "",
            cloud_cover=acq.cloud_cover,
            mgrs_tile=acq.mgrs_tile,
            observation_state=acq.observation_state,
            quality_reason=acq.quality_reason,
            usable_pixel_fraction=acq.usable_pixel_fraction,
            cloud_fraction=acq.cloud_fraction,
            shadow_fraction=acq.shadow_fraction,
            invalid_fraction=acq.invalid_fraction,
            quality_processing_version=acq.quality_processing_version,
            masking_method=acq.masking_method,
            quality_mask_available=acq.quality_mask_available,
            display_available=acq.display_available,
            visualization_version=acq.visualization_version,
            bands=acq.bands,
        )

    before_obs = portable_acqs.get(primary_interval.before_acquisition_id)
    after_obs = portable_acqs.get(primary_interval.after_acquisition_id)
    if before_obs is None or after_obs is None:
        raise CandidateEvidenceError("Detection interval references an observation missing from candidate evidence")

    # Build portable detection summary
    detection_summary = PortableDetectionSummary(
        detector_version=primary_interval.detector_version,
        threshold=primary_interval.threshold,
        min_region_pixels=primary_interval.min_region_pixels,
        changed_pixel_count=primary_interval.changed_pixel_count,
        raw_changed_pixel_count=primary_interval.raw_changed_pixel_count,
        filtered_changed_pixel_count=primary_interval.filtered_changed_pixel_count,
        changed_pixel_percentage=primary_interval.changed_pixel_percentage,
        total_changed_area_m2=primary_interval.total_changed_area_m2,
        largest_region_area_m2=primary_interval.largest_region_area_m2,
        mean_region_area_m2=primary_interval.mean_region_area_m2,
        median_region_area_m2=primary_interval.median_region_area_m2,
        mean_change_signal=primary_interval.mean_change_signal,
        max_change_signal=primary_interval.max_change_signal,
        quality_valid_pixel_count=primary_interval.quality_valid_pixel_count,
        excluded_pixel_count=primary_interval.excluded_pixel_count,
        excluded_cloud_pixel_count=primary_interval.excluded_cloud_pixel_count,
        excluded_shadow_pixel_count=primary_interval.excluded_shadow_pixel_count,
    )

    # Build portable temporal evidence
    temporal_dict = evidence.temporal
    temporal_evidence = PortableTemporalEvidence(
        state=temporal_dict.get("state", "unknown"),
        interval_count=temporal_dict.get("interval_count", 0),
        persistence_ratio=float(temporal_dict.get("persistence_ratio", 0.0)),
        support_count=temporal_dict.get("support_count", 0),
        recurrence_count=temporal_dict.get("recurrence_count", 0),
        transient_interval_count=temporal_dict.get("transient_interval_count", 0),
        temporal_consistency=float(temporal_dict.get("temporal_consistency", 0.0)),
        matched_region_coverage=float(temporal_dict.get("matched_region_coverage", 0.0)),
        first_change_datetime=_to_iso(temporal_dict.get("first_change_datetime")),
        last_supporting_datetime=_to_iso(temporal_dict.get("last_supporting_datetime")),
        quality_support=float(temporal_dict.get("quality_support", evidence.candidate.metrics.quality_support or 0.0)),
    )

    # Portable observation quality
    obs_quality = PortableObservationQuality(
        is_quality_limited=evidence.is_quality_limited,
        quality_limitation_reasons=evidence.quality_limitation_reasons,
        spatial_alignment=evidence.spatial_alignment_details or {},
    )

    # System explanation
    expl = evidence.candidate.metrics.explanation
    system_explanation = PortableSystemExplanation(
        summary=expl.summary if expl else "Deterministic change triage candidate.",
        positive_factors=expl.positive_factors if expl else [],
        limiting_factors=expl.limiting_factors if expl else [],
    )

    # Stable portable provenance: NO file paths
    provenance = PortableInvestigationProvenance(
        candidate_id=evidence.candidate.candidate_id,
        analysis_id=evidence.candidate.analysis_id,
        signal_id=evidence.candidate.signal_id,
        aoi_id=evidence.aoi_id,
        karnataka_contained=True,
        detection_run_ids=evidence.candidate.detection_run_ids,
        acquisition_ids=evidence.candidate.acquisition_ids,
        before_acquisition_id=primary_interval.before_acquisition_id,
        after_acquisition_id=primary_interval.after_acquisition_id,
        before_item_id=before_obs.item_id,
        after_item_id=after_obs.item_id,
        crs=evidence.spatial_alignment_details.get("crs", "EPSG:32643") if evidence.spatial_alignment_details else "EPSG:32643",
        detector_version=primary_interval.detector_version,
        processing_version=before_obs.quality_processing_version or "aoi-observation-quality-v1",
        masking_method=before_obs.masking_method or "sentinel-2-scl-nearest-v1",
        visualization_version=before_obs.visualization_version or "sentinel-2-rgb-percentile-v2",
    )

    return InvestigationSnapshot(
        export_version=INVESTIGATION_EXPORT_VERSION,
        generated_at=datetime.now(UTC).isoformat(),
        candidate_id=evidence.candidate.candidate_id,
        analysis_id=evidence.candidate.analysis_id,
        signal_id=evidence.candidate.signal_id,
        aoi_id=evidence.aoi_id,
        priority=evidence.candidate.priority,
        severity=evidence.candidate.severity,
        score=evidence.candidate.score,
        rank=evidence.candidate.rank,
        geometry=evidence.candidate.geometry,
        centroid=centroid,
        detection_summary=detection_summary,
        temporal_evidence=temporal_evidence,
        before_observation=before_obs,
        after_observation=after_obs,
        all_observations=list(portable_acqs.values()),
        observation_quality=obs_quality,
        system_explanation=system_explanation,
        analyst_review=analyst_review,
        provenance=provenance,
    )


def generate_investigation_json(snapshot: InvestigationSnapshot) -> bytes:
    """Serializes the investigation snapshot to canonical JSON bytes."""
    data = snapshot.model_dump()
    json_str = json.dumps(data, indent=2, sort_keys=True)
    return json_str.encode("utf-8")


def generate_investigation_pdf(snapshot: InvestigationSnapshot) -> bytes:
    """
    Renders a human-readable portable investigation report in PDF format.
    Embeds existing bounded Before/After display PNGs when available.
    Does NOT embed full scientific rasters or raw Sentinel scenes.
    Does NOT expose internal filesystem paths.
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=0.5 * inch,
        rightMargin=0.5 * inch,
        topMargin=0.5 * inch,
        bottomMargin=0.5 * inch,
        pageCompression=0,
    )

    styles = getSampleStyleSheet()

    # Define clean, professional color palette
    c_primary = colors.HexColor("#1e293b")  # Slate 800
    c_brand = colors.HexColor("#0f766e")    # Teal 700
    c_muted = colors.HexColor("#64748b")    # Slate 500
    c_card_bg = colors.HexColor("#f8fafc")  # Slate 50
    c_border = colors.HexColor("#e2e8f0")   # Slate 200
    c_highlight = colors.HexColor("#f1f5f9")

    # Typography styles
    style_title = ParagraphStyle(
        "TWTitle",
        parent=styles["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=18,
        leading=22,
        textColor=c_primary,
        spaceAfter=2,
    )
    style_subtitle = ParagraphStyle(
        "TWSubtitle",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=12,
        textColor=c_brand,
        spaceAfter=6,
    )
    style_section = ParagraphStyle(
        "TWSection",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=11,
        leading=14,
        textColor=c_primary,
        spaceBefore=8,
        spaceAfter=4,
    )
    style_body = ParagraphStyle(
        "TWBody",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8.5,
        leading=11,
        textColor=c_primary,
    )
    style_bold = ParagraphStyle(
        "TWBold",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=8.5,
        leading=11,
        textColor=c_primary,
    )
    style_muted = ParagraphStyle(
        "TWMuted",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=7.5,
        leading=10,
        textColor=c_muted,
    )
    style_badge_decision = ParagraphStyle(
        "TWBadgeDecision",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=10,
        leading=12,
        alignment=1,  # Centered
        textColor=colors.HexColor("#ffffff"),
    )

    story: list[Any] = []

    # --- Header Banner ---
    decision_upper = snapshot.analyst_review.decision.upper()
    decision_bg = {
        "ACCEPTED": colors.HexColor("#16a34a"),
        "REJECTED": colors.HexColor("#dc2626"),
        "INVESTIGATE": colors.HexColor("#d97706"),
        "UNREVIEWED": colors.HexColor("#475569"),
    }.get(decision_upper, colors.HexColor("#475569"))

    header_table_data = [
        [
            Paragraph("<b>TerraWatch V2</b> · Investigation Report", style_title),
            Paragraph(f"<b>DECISION: {decision_upper}</b>", style_badge_decision),
        ],
        [
            Paragraph(f"Candidate: <b>{snapshot.candidate_id}</b> | AOI #{snapshot.aoi_id} (Karnataka) | Generated: {snapshot.generated_at[:19].replace('T', ' ')} UTC", style_subtitle),
            Paragraph(f"Format: {snapshot.export_version}", style_muted),
        ],
    ]
    header_table = Table(header_table_data, colWidths=[5.4 * inch, 2.1 * inch])
    header_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (1, 0), (1, 0), "CENTER"),
        ("BACKGROUND", (1, 0), (1, 0), decision_bg),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
    ]))
    story.append(header_table)
    story.append(HRFlowable(width="100%", thickness=1, color=c_border, spaceBefore=4, spaceAfter=8))

    # --- Section 1: Candidate Identity & Geographic Location ---
    story.append(Paragraph("1. Investigation Identity & Geographic Location", style_section))
    ident_data = [
        [
            Paragraph("<b>Candidate ID:</b>", style_bold), Paragraph(snapshot.candidate_id, style_body),
            Paragraph("<b>Analysis / Signal ID:</b>", style_bold), Paragraph(f"{snapshot.analysis_id} / #{snapshot.signal_id}", style_body),
        ],
        [
            Paragraph("<b>Priority:</b>", style_bold), Paragraph(snapshot.priority.upper(), style_body),
            Paragraph("<b>Physical Severity:</b>", style_bold), Paragraph(snapshot.severity.upper(), style_body),
        ],
        [
            Paragraph("<b>Triage Score:</b>", style_bold), Paragraph(f"{snapshot.score:.4f} (Rank #{snapshot.rank})", style_body),
            Paragraph("<b>Centroid (Lon, Lat):</b>", style_bold), Paragraph(f"{snapshot.centroid['longitude']:.5f}°, {snapshot.centroid['latitude']:.5f}°", style_body),
        ],
        [
            Paragraph("<b>Geographic Domain:</b>", style_bold), Paragraph("Karnataka State, India (Containment verified)", style_body),
            Paragraph("<b>AOI Reference:</b>", style_bold), Paragraph(f"AOI #{snapshot.aoi_id} (CRS: {snapshot.provenance.crs})", style_body),
        ],
    ]
    ident_table = Table(ident_data, colWidths=[1.8 * inch, 1.95 * inch, 1.8 * inch, 1.95 * inch])
    ident_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), c_card_bg),
        ("BOX", (0, 0), (-1, -1), 0.5, c_border),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, c_border),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(ident_table)
    story.append(Spacer(1, 6))

    # --- Section 2: Analyst Review & Human Adjudication ---
    story.append(Paragraph("2. Authoritative Analyst Review", style_section))
    review_date = snapshot.analyst_review.updated_at or snapshot.analyst_review.created_at or "N/A"
    note_text = snapshot.analyst_review.note or "No analyst justification note recorded."
    review_data = [
        [
            Paragraph("<b>Decision:</b>", style_bold),
            Paragraph(f"<b>{snapshot.analyst_review.decision.upper()}</b>", style_bold),
            Paragraph("<b>Adjudication Timestamp:</b>", style_bold),
            Paragraph(review_date, style_body),
        ],
        [
            Paragraph("<b>Analyst Note:</b>", style_bold),
            Paragraph(note_text, style_body),
            Paragraph("", style_body),
            Paragraph("", style_body),
        ],
    ]
    review_table = Table(review_data, colWidths=[1.8 * inch, 1.95 * inch, 1.8 * inch, 1.95 * inch])
    review_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), c_highlight),
        ("BOX", (0, 0), (-1, -1), 0.5, c_border),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, c_border),
        ("SPAN", (1, 1), (3, 1)),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(review_table)
    story.append(Spacer(1, 6))

    # --- Section 3: Before / After Satellite Observations & Embedded Display ---
    story.append(Paragraph("3. Satellite Evidence & Observations (Distinct Before / After)", style_section))

    # Attempt to load bounded display PNGs for before and after
    before_img_flowable = None
    after_img_flowable = None

    if snapshot.before_observation.display_available:
        try:
            png_bytes = render_display(snapshot.before_observation.acquisition_id, snapshot.aoi_id)
            before_img_flowable = Image(io.BytesIO(png_bytes), width=2.4 * inch, height=2.4 * inch)
        except Exception:
            before_img_flowable = None

    if snapshot.after_observation.display_available:
        try:
            png_bytes = render_display(snapshot.after_observation.acquisition_id, snapshot.aoi_id)
            after_img_flowable = Image(io.BytesIO(png_bytes), width=2.4 * inch, height=2.4 * inch)
        except Exception:
            after_img_flowable = None

    b_flow = before_img_flowable or Paragraph("<i>Display artifact unavailable or quality-limited</i>", style_muted)
    a_flow = after_img_flowable or Paragraph("<i>Display artifact unavailable or quality-limited</i>", style_muted)

    b_cloud = f"{snapshot.before_observation.cloud_fraction * 100:.1f}%" if snapshot.before_observation.cloud_fraction is not None else "N/A"
    b_usable = f"{snapshot.before_observation.usable_pixel_fraction * 100:.1f}%" if snapshot.before_observation.usable_pixel_fraction is not None else "N/A"
    a_cloud = f"{snapshot.after_observation.cloud_fraction * 100:.1f}%" if snapshot.after_observation.cloud_fraction is not None else "N/A"
    a_usable = f"{snapshot.after_observation.usable_pixel_fraction * 100:.1f}%" if snapshot.after_observation.usable_pixel_fraction is not None else "N/A"

    obs_comparison_data = [
        [
            Paragraph("<b>BEFORE OBSERVATION</b>", style_bold),
            Paragraph("<b>AFTER OBSERVATION</b>", style_bold),
        ],
        [
            Paragraph(
                f"<b>Acquisition ID:</b> #{snapshot.before_observation.acquisition_id}<br/>"
                f"<b>Date:</b> {snapshot.before_observation.acquisition_datetime[:19].replace('T', ' ')} UTC<br/>"
                f"<b>STAC Item:</b> {snapshot.before_observation.item_id}<br/>"
                f"<b>State:</b> {snapshot.before_observation.observation_state}<br/>"
                f"<b>Usable Fraction:</b> {b_usable} | <b>Cloud:</b> {b_cloud}",
                style_body,
            ),
            Paragraph(
                f"<b>Acquisition ID:</b> #{snapshot.after_observation.acquisition_id}<br/>"
                f"<b>Date:</b> {snapshot.after_observation.acquisition_datetime[:19].replace('T', ' ')} UTC<br/>"
                f"<b>STAC Item:</b> {snapshot.after_observation.item_id}<br/>"
                f"<b>State:</b> {snapshot.after_observation.observation_state}<br/>"
                f"<b>Usable Fraction:</b> {a_usable} | <b>Cloud:</b> {a_cloud}",
                style_body,
            ),
        ],
        [b_flow, a_flow],
        [
            Paragraph("Sentinel-2 Natural RGB (percentile-v2 · B04/B03/B02)", style_muted),
            Paragraph("Sentinel-2 Natural RGB (percentile-v2 · B04/B03/B02)", style_muted),
        ],
    ]
    obs_table = Table(obs_comparison_data, colWidths=[3.75 * inch, 3.75 * inch])
    obs_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f8fafc")),
        ("BACKGROUND", (1, 0), (1, -1), colors.HexColor("#f8fafc")),
        ("BOX", (0, 0), (-1, -1), 0.5, c_border),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, c_border),
        ("ALIGN", (0, 2), (-1, 2), "CENTER"),
        ("ALIGN", (0, 3), (-1, 3), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(KeepTogether(obs_table))
    story.append(Spacer(1, 6))

    # --- Section 4: Detection & Physical Change Metrics ---
    story.append(Paragraph("4. Physical Change Evidence (Spectral & Spatial Metrics)", style_section))
    det = snapshot.detection_summary
    metrics_data = [
        [
            Paragraph("<b>Mean ΔNDVI:</b>", style_bold), Paragraph(f"{det.mean_change_signal:.4f}", style_body),
            Paragraph("<b>Max ΔNDVI:</b>", style_bold), Paragraph(f"{det.max_change_signal:.4f}", style_body),
        ],
        [
            Paragraph("<b>Total Changed Area:</b>", style_bold), Paragraph(f"{det.total_changed_area_m2:.1f} m²", style_body),
            Paragraph("<b>Changed Pixel %:</b>", style_bold), Paragraph(f"{det.changed_pixel_percentage:.2f}%", style_body),
        ],
        [
            Paragraph("<b>Filtered Changed Pixels:</b>", style_bold), Paragraph(f"{det.filtered_changed_pixel_count:,}", style_body),
            Paragraph("<b>Raw Changed Pixels:</b>", style_bold), Paragraph(f"{det.raw_changed_pixel_count:,}", style_body),
        ],
        [
            Paragraph("<b>Largest Region Area:</b>", style_bold), Paragraph(f"{det.largest_region_area_m2:.1f} m²", style_body),
            Paragraph("<b>Mean / Median Area:</b>", style_bold), Paragraph(f"{det.mean_region_area_m2:.1f} / {det.median_region_area_m2:.1f} m²", style_body),
        ],
        [
            Paragraph("<b>Quality-Valid Pixels:</b>", style_bold), Paragraph(f"{det.quality_valid_pixel_count:,}", style_body),
            Paragraph("<b>Cloud / Shadow Excluded:</b>", style_bold), Paragraph(f"{det.excluded_cloud_pixel_count:,} / {det.excluded_shadow_pixel_count:,} px", style_body),
        ],
    ]
    metrics_table = Table(metrics_data, colWidths=[1.8 * inch, 1.95 * inch, 1.8 * inch, 1.95 * inch])
    metrics_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), c_card_bg),
        ("BOX", (0, 0), (-1, -1), 0.5, c_border),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, c_border),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
    ]))
    story.append(metrics_table)
    story.append(Spacer(1, 6))

    # --- Section 5: Temporal Evidence & Persistence ---
    story.append(Paragraph("5. Temporal Evidence & Persistence Behavior", style_section))
    temp = snapshot.temporal_evidence
    temp_data = [
        [
            Paragraph("<b>Temporal State:</b>", style_bold), Paragraph(temp.state.upper(), style_body),
            Paragraph("<b>Persistence Ratio:</b>", style_bold), Paragraph(f"{temp.persistence_ratio:.2f}", style_body),
        ],
        [
            Paragraph("<b>Adjacent Intervals:</b>", style_bold), Paragraph(f"{temp.interval_count}", style_body),
            Paragraph("<b>Supporting Observations:</b>", style_bold), Paragraph(f"{temp.support_count}", style_body),
        ],
        [
            Paragraph("<b>Temporal Consistency:</b>", style_bold), Paragraph(f"{temp.temporal_consistency:.2f}", style_body),
            Paragraph("<b>Matched Region Coverage:</b>", style_bold), Paragraph(f"{temp.matched_region_coverage * 100:.1f}%", style_body),
        ],
        [
            Paragraph("<b>Quality-Aware Support:</b>", style_bold), Paragraph(f"{temp.quality_support * 100:.1f}%", style_body),
            Paragraph("<b>Change Onset Window:</b>", style_bold), Paragraph(f"{temp.first_change_datetime or 'N/A'}", style_body),
        ],
    ]
    temp_table = Table(temp_data, colWidths=[1.8 * inch, 1.95 * inch, 1.8 * inch, 1.95 * inch])
    temp_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), c_card_bg),
        ("BOX", (0, 0), (-1, -1), 0.5, c_border),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, c_border),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
    ]))
    story.append(temp_table)
    story.append(Spacer(1, 6))

    # --- Section 6: Observation Quality & Spatial Alignment ---
    story.append(Paragraph("6. Observation Quality Assessment & Material Limitations", style_section))
    alignment_str = snapshot.observation_quality.spatial_alignment.get("details", "Grid checked")
    quality_status_str = "QUALITY LIMITED" if snapshot.observation_quality.is_quality_limited else "UNCOMPROMISED"

    limitations_p = "None detected."
    if snapshot.observation_quality.quality_limitation_reasons:
        limitations_p = "<br/>".join(f"• {r}" for r in snapshot.observation_quality.quality_limitation_reasons)

    qual_data = [
        [
            Paragraph("<b>Quality Status:</b>", style_bold), Paragraph(f"<b>{quality_status_str}</b>", style_bold),
            Paragraph("<b>Spatial Alignment:</b>", style_bold), Paragraph(alignment_str, style_body),
        ],
        [
            Paragraph("<b>Material Limitations:</b>", style_bold),
            Paragraph(limitations_p, style_body),
            Paragraph("", style_body),
            Paragraph("", style_body),
        ],
    ]
    qual_table = Table(qual_data, colWidths=[1.8 * inch, 1.95 * inch, 1.8 * inch, 1.95 * inch])
    qual_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), c_card_bg),
        ("BOX", (0, 0), (-1, -1), 0.5, c_border),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, c_border),
        ("SPAN", (1, 1), (3, 1)),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
    ]))
    story.append(qual_table)
    story.append(Spacer(1, 6))

    # --- Section 7: Explainability & Assessment Factors ---
    story.append(Paragraph("7. Explainability & Triage Factors", style_section))
    pos_factors = "<br/>".join(f"+ {f}" for f in snapshot.system_explanation.positive_factors) or "None recorded."
    lim_factors = "<br/>".join(f"- {f}" for f in snapshot.system_explanation.limiting_factors) or "None recorded."
    expl_data = [
        [
            Paragraph("<b>Triage Summary:</b>", style_bold),
            Paragraph(snapshot.system_explanation.summary, style_body),
        ],
        [
            Paragraph("<b>Supporting Factors:</b>", style_bold),
            Paragraph(pos_factors, style_body),
        ],
        [
            Paragraph("<b>Limiting Factors:</b>", style_bold),
            Paragraph(lim_factors, style_body),
        ],
    ]
    expl_table = Table(expl_data, colWidths=[1.8 * inch, 5.7 * inch])
    expl_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), c_card_bg),
        ("BOX", (0, 0), (-1, -1), 0.5, c_border),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, c_border),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
    ]))
    story.append(expl_table)
    story.append(Spacer(1, 6))

    # --- Section 8: Portable Provenance Trace (Zero internal paths) ---
    story.append(Paragraph("8. Portable Pipeline Provenance (Authoritative References)", style_section))
    prov = snapshot.provenance
    prov_data = [
        [
            Paragraph("<b>Detection Runs:</b>", style_bold), Paragraph(f"Run IDs: {prov.detection_run_ids}", style_body),
            Paragraph("<b>Detector Version:</b>", style_bold), Paragraph(prov.detector_version, style_body),
        ],
        [
            Paragraph("<b>Acquisitions:</b>", style_bold), Paragraph(f"IDs: {prov.acquisition_ids}", style_body),
            Paragraph("<b>STAC Collection:</b>", style_bold), Paragraph(snapshot.before_observation.collection, style_body),
        ],
        [
            Paragraph("<b>Processing Version:</b>", style_bold), Paragraph(prov.processing_version, style_body),
            Paragraph("<b>Quality Masking:</b>", style_bold), Paragraph(prov.masking_method, style_body),
        ],
        [
            Paragraph("<b>Visualization Version:</b>", style_bold), Paragraph(prov.visualization_version, style_body),
            Paragraph("<b>Coordinate System:</b>", style_bold), Paragraph(prov.crs, style_body),
        ],
    ]
    prov_table = Table(prov_data, colWidths=[1.8 * inch, 1.95 * inch, 1.8 * inch, 1.95 * inch])
    prov_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), c_highlight),
        ("BOX", (0, 0), (-1, -1), 0.5, c_border),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, c_border),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
    ]))
    story.append(prov_table)
    story.append(Spacer(1, 8))

    # Notice / Disclaimer footer
    story.append(Paragraph(
        "<i>Notice: This report represents an authoritative portable investigation snapshot from TerraWatch V2. "
        "Confidence scores represent deterministic triage evidence and ranking, not calibrated mathematical probability. "
        "Physical change metrics are derived from persisted Sentinel-2 radiometric data. Zero internal filesystem paths are exposed.</i>",
        style_muted,
    ))

    # Build PDF document
    doc.build(story)
    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes
