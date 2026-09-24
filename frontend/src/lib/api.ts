import { appConfig } from '../config'

export async function fetchHealth() {
  const response = await fetch(`${appConfig.apiBaseUrl}/api/v1/health`)

  if (!response.ok) {
    throw new Error('Health check failed')
  }

  return response.json()
}

export type AOIGeometry = {
  type: 'Polygon'
  coordinates: number[][][]
}

export type AOIResponse = {
  aoi_id: number
  geometry: AOIGeometry
  created_at: string
  updated_at: string
}

export type ImageryAcquisition = {
  acquisition_id: number
  aoi_id: number
  requested_start_datetime: string
  requested_end_datetime: string
  item_id: string
  collection: string
  acquisition_datetime: string
  assets: Array<{ asset_id: string; href: string }>
  prepared_path: string
  display_path?: string | null
  display_metadata?: { bounds_wgs84?: number[]; bands?: string[] } | null
  visualization_version?: string | null
  raster: {
    crs: string
    transform: number[]
    width: number
    height: number
    resolution: number[]
    bounds: number[]
    count: number
    dtype: string
    nodata: number | null
  }
  source_metadata: Record<string, unknown>
  created_at: string
  observation_state?: 'discovered' | 'acquired' | 'valid_unusable' | 'usable' | 'failed' | 'legacy_unassessed' | string
  quality_reason?: string
  quality_metrics?: Record<string, unknown>
}

export type RawChangeRegion = {
  region_id: number
  geometry: { type: string; coordinates: unknown }
  pixel_count: number
  area_m2: number
  mean_change_signal: number
  max_change_signal: number
}

export type DetectionResult = {
  run_id: number
  before_acquisition_id: number
  after_acquisition_id: number
  before_item_id: string
  after_item_id: string
  detector_version: string
  threshold: number
  min_region_pixels: number
  created_at: string
  region_count: number
  changed_pixel_count: number
  total_changed_area_m2: number
  regions: RawChangeRegion[]
}

export type TemporalObservationItem = {
  acquisition_id: number
  item_id: string
  acquisition_datetime: string
  sequence_index: number
  quality_state?: string
  usable_pixel_fraction?: number
  cloud_fraction?: number
  shadow_fraction?: number
  invalid_fraction?: number
  quality_processing_version?: string
}

export type TemporalRelationshipItem = {
  before_acquisition_id: number
  after_acquisition_id: number
  detection_run_id: number
  region_count: number
  changed_pixel_count?: number
  before_region_ids?: number[]
  after_region_ids?: number[]
  matched_region_pairs?: number[][]
  elapsed_days?: number
  quality_support?: number
  quality_state?: 'usable' | 'insufficient_quality_support' | string
  evaluated?: boolean
  detection_result?: DetectionResult
}

export type TemporalSignalItem = {
  signal_id: number
  geometry: { type: string; coordinates: unknown }
  support_count: number
  interval_count: number
  persistence_ratio: number
  recurrence_count: number
  transient_interval_count: number
  temporal_consistency: number
  matched_region_coverage: number
  first_change_datetime: string
  last_supporting_datetime: string
  state: 'persistent' | 'transient' | 'recurrent' | 'isolated'
  usable_interval_count?: number
  supporting_interval_count?: number
  quality_support?: number
  onset_before_acquisition_id?: number | null
  onset_after_acquisition_id?: number | null
  onset_start_datetime?: string | null
  onset_end_datetime?: string | null
  seasonal_interpretation?: 'seasonal_compatible' | 'less_seasonal_compatible' | 'insufficient_temporal_evidence'
  interval_change_means?: number[]
}

export type TemporalResult = {
  analysis_id: number
  iou_threshold: number
  detector_run_count: number
  observation_count: number
  temporal_span_days: number
  state: 'persistent' | 'transient' | 'recurrent' | 'isolated' | 'insufficient_history' | 'no_temporal_signal'
  usable_observation_count?: number
  usable_interval_count?: number
  quality_support?: number
  quality_aggregation_method?: string
  seasonal_interpretation?: 'seasonal_compatible' | 'less_seasonal_compatible' | 'insufficient_temporal_evidence'
  observations: TemporalObservationItem[]
  relationships: TemporalRelationshipItem[]
  signals: TemporalSignalItem[]
  created_at: string
}

export type ReviewDecision = 'accepted' | 'rejected' | 'investigate'
export type CandidateReviewState = 'unreviewed' | 'accepted' | 'rejected' | 'investigate'

export type CandidateReview = {
  candidate_id: string
  decision: CandidateReviewState
  note: string | null
  created_at: string | null
  updated_at: string | null
}

export type Candidate = {
  candidate_id: string
  analysis_id: number
  signal_id: number
  geometry: { type: string; coordinates: unknown }
  score: number
  rank: number
  severity: 'low' | 'medium' | 'high'
  priority: 'low' | 'normal' | 'high' | 'urgent'
  temporal_state?: 'persistent' | 'transient' | 'recurrent' | 'isolated'
  review_state: CandidateReviewState
  rationale?: {
    evidence_summary?: string
    factors?: string[]
    limiting_factors?: string[]
    severity_rationale?: string
    priority_rationale?: string
  }
  quality_support?: number
  mean_change_signal?: number
  max_change_signal?: number
  total_area_m2?: number
  source_signal_ids: number[]
  detection_run_ids: number[]
  acquisition_ids: number[]
  metrics: {
    support_count: number
    interval_count: number
    persistence_ratio: number
    recurrence_count: number
    transient_interval_count: number
    temporal_consistency: number
    matched_region_coverage: number
    first_change_datetime: string
    last_supporting_datetime: string
    temporal_state: 'persistent' | 'transient' | 'recurrent' | 'isolated'
    score_components?: Record<string, number>
    explanation?: {
      summary: string
      positive_factors: string[]
      limiting_factors: string[]
      severity_rationale: string
      priority_rationale: string
    }
    quality_support?: number
    mean_change_signal?: number
    max_change_signal?: number
    total_area_m2?: number
  }
  created_at: string
  updated_at: string
}

export type EvidenceAcquisitionItem = {
  acquisition_id: number
  aoi_id: number
  item_id: string
  collection: string
  acquisition_datetime: string
  cloud_cover: number | null
  mgrs_tile: string | null
  observation_state: string
  quality_reason: string | null
  usable_pixel_fraction: number | null
  cloud_fraction: number | null
  shadow_fraction: number | null
  invalid_fraction: number | null
  quality_processing_version: string | null
  masking_method: string | null
  quality_mask_available: boolean
  display_available: boolean
  display_url: string | null
  visualization_version: string | null
  raster: {
    crs: string
    transform: number[]
    width: number
    height: number
    resolution: [number, number]
    bounds: number[]
    count: number
    dtype: string
    nodata: number
  }
  bands: string[]
  preview_url: string
}

export type EvidenceIntervalItem = {
  detection_run_id: number
  before_acquisition_id: number
  after_acquisition_id: number
  detector_version: string
  threshold: number
  min_region_pixels: number
  changed_pixel_count: number
  total_changed_area_m2: number
  region_count: number
  quality_mask_used: boolean
  quality_processing_version: string
  quality_valid_pixel_count: number
  excluded_pixel_count: number
  excluded_cloud_pixel_count: number
  excluded_shadow_pixel_count: number
  raw_changed_pixel_count: number
  filtered_changed_pixel_count: number
  changed_pixel_percentage: number
  largest_region_area_m2: number
  mean_region_area_m2: number
  median_region_area_m2: number
  mean_change_signal: number
  max_change_signal: number
  regions: RawChangeRegion[]
}

export type CandidateEvidence = {
  candidate: Candidate
  temporal: {
    analysis_id: number
    signal_id: number
    state: string
    support_count: number
    interval_count: number
    persistence_ratio: number
    recurrence_count: number
    transient_interval_count: number
    temporal_consistency: number
    matched_region_coverage: number
    first_change_datetime: string
    last_supporting_datetime: string
    observation_count?: number
    onset_before_id?: number | null
    onset_after_id?: number | null
    quality_aware_temporal_support?: number | null
    quality_support?: number
  }
  intervals: EvidenceIntervalItem[]
  acquisitions: EvidenceAcquisitionItem[]
  evidence_bounds: number[]
  aoi_id: number
  is_spatially_aligned: boolean
  spatial_alignment_details: Record<string, any> | null
  is_quality_limited: boolean
  quality_limitation_reasons: string[]
  provenance_chain: Record<string, any>
}

export class ApiRequestError extends Error {
  readonly code: string

  constructor(code: string, message: string) {
    super(message)
    this.code = code
  }
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${appConfig.apiBaseUrl}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  const payload = await response.json().catch(() => null)
  if (!response.ok) throw new ApiRequestError(payload?.code ?? 'ApiError', payload?.message ?? 'API request failed')
  return payload as T
}

export function fetchAOI() {
  return request<AOIResponse>('/api/v1/aoi')
}

export function fetchAOIList() {
  return request<AOIResponse[]>('/api/v1/aois')
}

export function fetchAOIById(aoiId: number) {
  return request<AOIResponse>(`/api/v1/aoi/${aoiId}`)
}

export function saveAOI(geometry: AOIGeometry) {
  return request<AOIResponse>('/api/v1/aoi', { method: 'POST', body: JSON.stringify(geometry) })
}

export function clearAOI() {
  return request<{ cleared: boolean }>('/api/v1/aoi', { method: 'DELETE' })
}

export type AcquisitionProgress = {
  current: number
  total: number
  item_id?: string | null
  state: string
}

export async function requestImagery(
  aoiId: number,
  startDatetime: string,
  endDatetime: string,
  onProgress?: (progress: AcquisitionProgress) => void,
): Promise<ImageryAcquisition> {
  const url = `${appConfig.apiBaseUrl}/api/v1/imagery/acquisitions?stream=true`
  const response = await fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Accept': 'application/x-ndjson, application/json',
    },
    body: JSON.stringify({ aoi_id: aoiId, start_datetime: startDatetime, end_datetime: endDatetime }),
  })

  if (!response.ok) {
    const payload = await response.json().catch(() => null)
    throw new ApiRequestError(payload?.code ?? 'ApiError', payload?.message ?? 'API request failed')
  }

  const contentType = response.headers.get('content-type') ?? ''
  if (!contentType.includes('ndjson')) {
    const payload = await response.json().catch(() => null)
    if (payload && typeof payload === 'object') {
      if ('type' in payload && payload.type === 'complete' && payload.result) {
        return payload.result as ImageryAcquisition
      }
      if ('type' in payload && payload.type === 'error') {
        throw new ApiRequestError(payload.code ?? 'ApiError', payload.message ?? 'Observation acquisition failed')
      }
      return payload as ImageryAcquisition
    }
    throw new ApiRequestError('ApiError', 'Invalid acquisition response')
  }

  if (!response.body) {
    throw new ApiRequestError('ApiError', 'No response body received')
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''
  let finalResult: ImageryAcquisition | null = null

  while (true) {
    const { done, value } = await reader.read()
    if (value) {
      buffer += decoder.decode(value, { stream: !done })
    }
    const lines = buffer.split('\n')
    buffer = lines.pop() ?? ''

    for (const line of lines) {
      const trimmed = line.trim()
      if (!trimmed) continue
      try {
        const event = JSON.parse(trimmed)
        if (event.type === 'progress') {
          if (onProgress) {
            onProgress({
              current: event.current,
              total: event.total,
              item_id: event.item_id,
              state: event.state,
            })
          }
        } else if (event.type === 'complete') {
          finalResult = event.result as ImageryAcquisition
        } else if (event.type === 'error') {
          throw new ApiRequestError(event.code ?? 'ApiError', event.message ?? 'Observation acquisition failed')
        } else if (event.acquisition_id) {
          finalResult = event as ImageryAcquisition
        }
      } catch (err) {
        if (err instanceof ApiRequestError) throw err
      }
    }

    if (done) break
  }

  if (buffer.trim()) {
    try {
      const event = JSON.parse(buffer.trim())
      if (event.type === 'complete') {
        finalResult = event.result as ImageryAcquisition
      } else if (event.type === 'error') {
        throw new ApiRequestError(event.code ?? 'ApiError', event.message ?? 'Observation acquisition failed')
      } else if (event.acquisition_id) {
        finalResult = event as ImageryAcquisition
      }
    } catch (err) {
      if (err instanceof ApiRequestError) throw err
    }
  }

  if (!finalResult) {
    throw new ApiRequestError('ApiError', 'Observation acquisition stream closed without a completion event')
  }

  return finalResult
}

export function fetchAcquisitions(aoiId?: number) {
  const query = aoiId ? `?aoi_id=${aoiId}` : ''
  return request<{ acquisitions: ImageryAcquisition[] }>(`/api/v1/imagery/acquisitions${query}`)
}

export function displayImageryUrl(acquisition: ImageryAcquisition, aoiId: number) {
  return acquisition.display_path
    ? `${appConfig.apiBaseUrl}/api/v1/imagery/acquisitions/${acquisition.acquisition_id}/display?aoi_id=${aoiId}`
    : null
}

export function runDetection(beforeAcquisitionId: number, afterAcquisitionId: number, threshold = 0.2) {
  return request<DetectionResult>('/api/v1/detections', {
    method: 'POST',
    body: JSON.stringify({ before_acquisition_id: beforeAcquisitionId, after_acquisition_id: afterAcquisitionId, threshold }),
  })
}

export function runTemporalAnalysis(acquisitionIds: number[], iouThreshold = 0.25) {
  return request<TemporalResult>('/api/v1/temporal-analyses', {
    method: 'POST',
    body: JSON.stringify({ acquisition_ids: acquisitionIds, iou_threshold: iouThreshold }),
  })
}

export function triageCandidates(analysisId: number) {
  return request<{ analysis_id: number; candidates: Candidate[] }>('/api/v1/candidates/triage', {
    method: 'POST', body: JSON.stringify({ analysis_id: analysisId }),
  })
}

export function fetchCandidates(params?: {
  analysis_id?: number
  priority?: 'low' | 'normal' | 'high' | 'urgent'
  severity?: 'low' | 'medium' | 'high'
  review_state?: CandidateReviewState
  temporal_state?: 'persistent' | 'transient' | 'recurrent' | 'isolated'
  min_score?: number
  min_quality?: number
}) {
  const query = new URLSearchParams()
  if (params?.analysis_id) query.set('analysis_id', String(params.analysis_id))
  if (params?.priority) query.set('priority', params.priority)
  if (params?.severity) query.set('severity', params.severity)
  if (params?.review_state) query.set('review_state', params.review_state)
  if (params?.temporal_state) query.set('temporal_state', params.temporal_state)
  if (params?.min_score !== undefined) query.set('min_score', String(params.min_score))
  if (params?.min_quality !== undefined) query.set('min_quality', String(params.min_quality))
  const qs = query.toString() ? `?${query.toString()}` : ''
  return request<{ analysis_id: number | null; candidates: Candidate[] }>(`/api/v1/candidates${qs}`)
}

export function updateCandidateReviewState(candidateId: string, reviewState: CandidateReviewState) {
  return request<Candidate>(`/api/v1/candidates/${candidateId}/review-state`, {
    method: 'PATCH', body: JSON.stringify({ review_state: reviewState }),
  })
}

export function fetchCandidateReview(candidateId: string) {
  return request<CandidateReview>(`/api/v1/candidates/${candidateId}/review`)
}

export function saveCandidateReview(candidateId: string, decision: ReviewDecision, note?: string | null) {
  return request<CandidateReview>(`/api/v1/candidates/${candidateId}/review`, {
    method: 'PATCH',
    body: JSON.stringify({ decision, note: note ?? null }),
  })
}

export function fetchCandidateEvidence(candidateId: string) {
  return request<CandidateEvidence>(`/api/v1/candidates/${candidateId}/evidence`)
}

export async function exportInvestigation(
  candidateId: string,
  format: 'pdf' | 'json' = 'pdf',
): Promise<{ blob: Blob; filename: string }> {
  const url = `${appConfig.apiBaseUrl}/api/v1/candidates/${encodeURIComponent(candidateId)}/export?format=${format}`
  const response = await fetch(url)
  if (!response.ok) {
    let message = `Export failed with status ${response.status}`
    let code = 'ExportError'
    try {
      const err = await response.json()
      if (err?.message) message = err.message
      if (err?.code) code = err.code
    } catch {
      // fallback
    }
    throw new ApiRequestError(code, message)
  }
  const contentDisposition = response.headers.get('content-disposition') || ''
  let filename = `terrawatch_investigation_${candidateId}.${format}`
  const match = contentDisposition.match(/filename="?([^";]+)"?/)
  if (match && match[1]) {
    filename = match[1]
  }
  const blob = await response.blob()
  return { blob, filename }
}

export type AnalysisOrchestrationRequest = {
  aoi_id: number
  start_datetime?: string | null
  end_datetime?: string | null
  iou_threshold?: number
  threshold?: number
}

export type AnalysisOrchestrationResponse = {
  aoi_id: number
  analysis: TemporalResult
  candidates: {
    analysis_id: number | null
    candidates: Candidate[]
  }
  eligible_observation_ids: number[]
  reused_detection_count: number
  generated_detection_count: number
  reused_analysis: boolean
}

export type OrchestrationProgress = {
  phase: string
  message: string
  eligible_count?: number
  pair_count?: number
  pair_index?: number
  total_pairs?: number
  before_id?: number
  after_id?: number
  status?: string
}

export async function orchestrateAnalysis(
  req: AnalysisOrchestrationRequest,
  onProgress?: (progress: OrchestrationProgress) => void,
): Promise<AnalysisOrchestrationResponse> {
  const url = `${appConfig.apiBaseUrl}/api/v1/orchestrations/analyze?stream=true`
  const response = await fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Accept': 'application/x-ndjson, application/json',
    },
    body: JSON.stringify(req),
  })

  if (!response.ok) {
    const payload = await response.json().catch(() => null)
    throw new ApiRequestError(payload?.code ?? 'ApiError', payload?.message ?? 'Analysis orchestration failed')
  }

  const contentType = response.headers.get('content-type') ?? ''
  if (!contentType.includes('ndjson')) {
    const payload = await response.json().catch(() => null)
    if (payload && typeof payload === 'object') {
      if ('phase' in payload && payload.phase === 'complete' && payload.result) {
        return payload.result as AnalysisOrchestrationResponse
      }
      if ('type' in payload && payload.type === 'error') {
        throw new ApiRequestError(payload.code ?? 'ApiError', payload.message ?? 'Analysis orchestration failed')
      }
      return payload as AnalysisOrchestrationResponse
    }
    throw new ApiRequestError('ApiError', 'Invalid orchestration response')
  }

  if (!response.body) {
    throw new ApiRequestError('ApiError', 'No response body received')
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''
  let finalResult: AnalysisOrchestrationResponse | null = null

  while (true) {
    const { done, value } = await reader.read()
    if (value) {
      buffer += decoder.decode(value, { stream: !done })
    }
    const lines = buffer.split('\n')
    buffer = lines.pop() ?? ''

    for (const line of lines) {
      const trimmed = line.trim()
      if (!trimmed) continue
      try {
        const event = JSON.parse(trimmed)
        if (event.type === 'error') {
          throw new ApiRequestError(event.code ?? 'ApiError', event.message ?? 'Analysis orchestration failed')
        }
        if (event.phase === 'complete') {
          finalResult = event.result as AnalysisOrchestrationResponse
        } else if (event.phase && onProgress) {
          onProgress(event as OrchestrationProgress)
        }
      } catch (err) {
        if (err instanceof ApiRequestError) throw err
      }
    }

    if (done) break
  }

  if (buffer.trim()) {
    try {
      const event = JSON.parse(buffer.trim())
      if (event.type === 'error') {
        throw new ApiRequestError(event.code ?? 'ApiError', event.message ?? 'Analysis orchestration failed')
      }
      if (event.phase === 'complete') {
        finalResult = event.result as AnalysisOrchestrationResponse
      }
    } catch (err) {
      if (err instanceof ApiRequestError) throw err
    }
  }

  if (!finalResult) {
    throw new ApiRequestError('ApiError', 'Analysis orchestration stream closed without a completion event')
  }

  return finalResult
}

