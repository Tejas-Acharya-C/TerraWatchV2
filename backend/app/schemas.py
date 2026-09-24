from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.config import settings


class ApiError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    details: dict[str, str] | None = None


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = Field(default="ok")
    app: str = Field(default="TerraWatch V2")
    version: str = Field(default="0.1.0")


class AOIGeometry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["Polygon"]
    coordinates: Any


class AOIResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    aoi_id: int
    geometry: AOIGeometry
    created_at: str
    updated_at: str


class AOIClearResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cleared: bool


class ImageryAcquisitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    aoi_id: int = Field(gt=0)
    start_datetime: datetime
    end_datetime: datetime

    @model_validator(mode="after")
    def validate_window(self) -> "ImageryAcquisitionRequest":
        if self.start_datetime.tzinfo is None or self.end_datetime.tzinfo is None:
            raise ValueError("start_datetime and end_datetime must include a timezone")
        if self.start_datetime > self.end_datetime:
            raise ValueError("start_datetime must be before or equal to end_datetime")
        return self


class ImageryAsset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: str
    href: str


class RasterMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    crs: str
    transform: list[float]
    width: int
    height: int
    resolution: list[float]
    bounds: list[float]
    count: int
    dtype: str
    nodata: float | int | None


class ImageryAcquisitionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    acquisition_id: int
    aoi_id: int
    requested_start_datetime: datetime
    requested_end_datetime: datetime
    item_id: str
    collection: str
    acquisition_datetime: datetime
    assets: list[ImageryAsset]
    prepared_path: str
    raster: RasterMetadata | None = None
    source_metadata: dict[str, Any]
    created_at: datetime
    observation_state: Literal[
        "discovered", "acquired", "valid_unusable", "usable", "failed", "legacy_unassessed"
    ]
    quality_reason: str
    quality_metrics: dict[str, Any]
    quality_mask_path: str | None = None
    processing_version: str
    masking_method: str
    quality_asset_id: str | None = None
    display_path: str | None = None
    display_metadata: dict[str, Any] | None = None
    visualization_version: str | None = None


class ImageryAcquisitionListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    acquisitions: list[ImageryAcquisitionResponse]


class DetectionRequest(BaseModel):
    """Deterministic spectral-change request.

    ``threshold`` is an absolute NDVI difference, not a percentage, area
    change, probability, or confidence value. ``min_region_pixels`` is a
    configurable spatial noise floor; the default is four pixels on the
    current 10 m Sentinel-2 grid (approximately 400 square metres).
    """

    model_config = ConfigDict(extra="forbid")

    before_acquisition_id: int = Field(gt=0)
    after_acquisition_id: int = Field(gt=0)
    threshold: float = Field(default=0.2, gt=0, le=2)
    min_region_pixels: int = Field(default=settings.minimum_detection_region_pixels, ge=1)

    @model_validator(mode="after")
    def validate_distinct_inputs(self) -> "DetectionRequest":
        if self.before_acquisition_id == self.after_acquisition_id:
            raise ValueError("before and after acquisitions must be different")
        return self


class RawChangeRegion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    region_id: int
    geometry: dict[str, Any]
    pixel_count: int
    area_m2: float
    mean_change_signal: float
    max_change_signal: float


class DetectionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: int
    before_acquisition_id: int
    after_acquisition_id: int
    before_item_id: str
    after_item_id: str
    detector_version: str
    threshold: float
    min_region_pixels: int
    created_at: datetime
    region_count: int
    changed_pixel_count: int
    total_changed_area_m2: float
    quality_mask_used: bool
    quality_processing_version: str
    quality_valid_pixel_count: int
    excluded_pixel_count: int
    excluded_cloud_pixel_count: int
    excluded_shadow_pixel_count: int
    raw_changed_pixel_count: int
    filtered_changed_pixel_count: int
    changed_pixel_percentage: float
    largest_region_area_m2: float
    mean_region_area_m2: float
    median_region_area_m2: float
    mean_change_signal: float
    max_change_signal: float
    regions: list[RawChangeRegion]


class TemporalAnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    acquisition_ids: list[int] = Field(min_length=1)
    iou_threshold: float = Field(default=0.25, gt=0, le=1)

    @model_validator(mode="after")
    def validate_ids(self) -> "TemporalAnalysisRequest":
        if any(acquisition_id <= 0 for acquisition_id in self.acquisition_ids):
            raise ValueError("acquisition_ids must be positive")
        if len(set(self.acquisition_ids)) != len(self.acquisition_ids):
            raise ValueError("acquisition_ids must not contain duplicates")
        return self


class TemporalObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    acquisition_id: int
    item_id: str
    acquisition_datetime: datetime
    sequence_index: int
    quality_state: Literal[
        "discovered", "acquired", "valid_unusable", "usable", "failed", "legacy_unassessed"
    ]
    usable_pixel_fraction: float
    cloud_fraction: float
    shadow_fraction: float
    invalid_fraction: float
    quality_processing_version: str


class TemporalRelationship(BaseModel):
    model_config = ConfigDict(extra="forbid")

    before_acquisition_id: int
    after_acquisition_id: int
    detection_run_id: int
    region_count: int
    changed_pixel_count: int
    before_region_ids: list[int] = Field(default_factory=list)
    after_region_ids: list[int] = Field(default_factory=list)
    matched_region_pairs: list[list[int]] = Field(default_factory=list)
    elapsed_days: float
    quality_support: float
    quality_state: Literal["usable", "insufficient_quality_support"]
    evaluated: bool


class TemporalSignal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    signal_id: int
    geometry: dict[str, Any]
    support_count: int
    interval_count: int
    persistence_ratio: float
    recurrence_count: int
    transient_interval_count: int
    temporal_consistency: float
    matched_region_coverage: float
    first_change_datetime: datetime
    last_supporting_datetime: datetime
    state: Literal["persistent", "transient", "recurrent", "isolated"]
    usable_interval_count: int
    supporting_interval_count: int
    quality_support: float
    onset_before_acquisition_id: int | None = None
    onset_after_acquisition_id: int | None = None
    onset_start_datetime: datetime | None = None
    onset_end_datetime: datetime | None = None
    seasonal_interpretation: Literal[
        "seasonal_compatible", "less_seasonal_compatible", "insufficient_temporal_evidence"
    ]
    interval_change_means: list[float] = Field(default_factory=list)
    interval_change_maxima: list[float] = Field(default_factory=list)
    source_region_ids: list[int] = Field(default_factory=list)
    detection_run_ids: list[int] = Field(default_factory=list)
    acquisition_ids: list[int] = Field(default_factory=list)


class TemporalAnalysisResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    analysis_id: int
    iou_threshold: float
    detector_run_count: int
    observation_count: int
    temporal_span_days: float
    state: Literal["persistent", "transient", "recurrent", "isolated", "insufficient_history", "no_temporal_signal"]
    usable_observation_count: int
    usable_interval_count: int
    quality_support: float
    quality_aggregation_method: str
    seasonal_interpretation: Literal[
        "seasonal_compatible", "less_seasonal_compatible", "insufficient_temporal_evidence"
    ]
    observations: list[TemporalObservation]
    relationships: list[TemporalRelationship]
    signals: list[TemporalSignal]
    created_at: datetime


ReviewDecision = Literal["accepted", "rejected", "investigate"]
CandidateReviewState = Literal["unreviewed", "accepted", "rejected", "investigate"]


class CandidateReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: ReviewDecision
    note: str | None = Field(default=None, max_length=2000)

    @field_validator("note", mode="before")
    @classmethod
    def normalize_note(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("Note must be a string")
        stripped = value.strip()
        return stripped if stripped else None


class CandidateReviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    decision: CandidateReviewState
    note: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class CandidateTriageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    analysis_id: int = Field(gt=0)


class CandidateReviewStateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_state: CandidateReviewState


class CandidateExplanation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str
    positive_factors: list[str] = Field(default_factory=list)
    limiting_factors: list[str] = Field(default_factory=list)
    severity_rationale: str
    priority_rationale: str


class CandidateMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    support_count: int
    interval_count: int
    persistence_ratio: float
    recurrence_count: int
    transient_interval_count: int
    temporal_consistency: float
    matched_region_coverage: float
    first_change_datetime: datetime
    last_supporting_datetime: datetime
    temporal_state: Literal["persistent", "transient", "recurrent", "isolated"]
    score_components: dict[str, float]
    explanation: CandidateExplanation | None = None
    quality_support: float = 0.0
    mean_change_signal: float = 0.0
    max_change_signal: float = 0.0
    total_area_m2: float = 0.0


class CandidateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    analysis_id: int
    signal_id: int
    geometry: dict[str, Any]
    score: float
    rank: int
    severity: Literal["low", "medium", "high"]
    priority: Literal["low", "normal", "high", "urgent"]
    review_state: CandidateReviewState
    source_signal_ids: list[int]
    detection_run_ids: list[int]
    acquisition_ids: list[int]
    metrics: CandidateMetrics
    created_at: datetime
    updated_at: datetime


class EvidenceAcquisition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    acquisition_id: int
    aoi_id: int
    item_id: str
    collection: str
    acquisition_datetime: datetime
    cloud_cover: float | None = None
    mgrs_tile: str | None = None
    observation_state: str = "usable"
    quality_reason: str | None = None
    usable_pixel_fraction: float | None = None
    cloud_fraction: float | None = None
    shadow_fraction: float | None = None
    invalid_fraction: float | None = None
    quality_processing_version: str | None = None
    masking_method: str | None = None
    quality_mask_available: bool = False
    display_available: bool = False
    display_url: str | None = None
    visualization_version: str | None = None
    raster: RasterMetadata
    bands: list[str]
    preview_url: str


class EvidenceInterval(BaseModel):
    model_config = ConfigDict(extra="forbid")

    detection_run_id: int
    before_acquisition_id: int
    after_acquisition_id: int
    detector_version: str
    threshold: float
    min_region_pixels: int
    changed_pixel_count: int
    total_changed_area_m2: float
    region_count: int
    quality_mask_used: bool = False
    quality_processing_version: str = "legacy-unassessed"
    quality_valid_pixel_count: int = 0
    excluded_pixel_count: int = 0
    excluded_cloud_pixel_count: int = 0
    excluded_shadow_pixel_count: int = 0
    raw_changed_pixel_count: int = 0
    filtered_changed_pixel_count: int = 0
    changed_pixel_percentage: float = 0.0
    largest_region_area_m2: float = 0.0
    mean_region_area_m2: float = 0.0
    median_region_area_m2: float = 0.0
    mean_change_signal: float = 0.0
    max_change_signal: float = 0.0
    regions: list[RawChangeRegion]


class CandidateEvidenceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate: CandidateResponse
    temporal: dict[str, Any]
    intervals: list[EvidenceInterval]
    acquisitions: list[EvidenceAcquisition]
    evidence_bounds: list[float]
    aoi_id: int
    is_spatially_aligned: bool = True
    spatial_alignment_details: dict[str, Any] | None = None
    is_quality_limited: bool = False
    quality_limitation_reasons: list[str] = Field(default_factory=list)
    provenance_chain: dict[str, Any] = Field(default_factory=dict)


class CandidateListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    analysis_id: int | None
    candidates: list[CandidateResponse]


class AnalysisOrchestrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    aoi_id: int = Field(gt=0)
    start_datetime: datetime | None = None
    end_datetime: datetime | None = None
    iou_threshold: float = Field(default=0.25, gt=0, le=1)
    threshold: float = Field(default=0.20, ge=0, le=1)

    @model_validator(mode="after")
    def validate_dates(self) -> "AnalysisOrchestrationRequest":
        if self.start_datetime and self.end_datetime and self.start_datetime >= self.end_datetime:
            raise ValueError("start_datetime must be earlier than end_datetime")
        return self


class AnalysisOrchestrationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    aoi_id: int
    analysis: TemporalAnalysisResponse
    candidates: CandidateListResponse
    eligible_observation_ids: list[int]
    reused_detection_count: int
    generated_detection_count: int
    reused_analysis: bool

