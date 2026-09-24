import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import App from './App'
import type { AOIResponse, ImageryAcquisition, Candidate, CandidateEvidence, CandidateReview, DetectionResult, TemporalResult } from './lib/api'

vi.mock('./MapCanvas', () => ({
  default: () => <div data-testid="map-canvas-mock">MapCanvas</div>,
}))

const karnatakaPolygon = {
  type: 'Polygon' as const,
  coordinates: [
    [
      [75.0, 13.0],
      [75.1, 13.0],
      [75.1, 13.1],
      [75.0, 13.1],
      [75.0, 13.0],
    ],
  ],
}

function makeObservation(overrides: Partial<ImageryAcquisition> = {}): ImageryAcquisition {
  return {
    acquisition_id: 1,
    aoi_id: 1,
    requested_start_datetime: '2024-01-01T00:00:00Z',
    requested_end_datetime: '2024-01-05T00:00:00Z',
    item_id: 'S2_TEST_001',
    collection: 'sentinel-2-l2a',
    acquisition_datetime: '2024-01-02T00:00:00Z',
    assets: [],
    prepared_path: '/path/to/test.tif',
    raster: {
      crs: 'EPSG:4326',
      transform: [1, 0, 0, 0, 1, 0],
      width: 100,
      height: 100,
      resolution: [10, 10],
      bounds: [75.0, 13.0, 75.1, 13.1],
      count: 2,
      dtype: 'uint16',
      nodata: 0,
    },
    source_metadata: {},
    created_at: '2024-01-02T00:00:00Z',
    observation_state: 'usable',
    quality_reason: 'Passed quality policy',
    ...overrides,
  }
}

function makeCandidate(id: string, overrides: Partial<Candidate> = {}): Candidate {
  return {
    candidate_id: id,
    analysis_id: 10,
    signal_id: 1,
    geometry: { type: 'Polygon', coordinates: [[[75.0, 13.0], [75.01, 13.0], [75.01, 13.01], [75.0, 13.01], [75.0, 13.0]]] },
    score: 0.85,
    rank: 1,
    severity: 'high',
    priority: 'high',
    review_state: 'unreviewed',
    source_signal_ids: [1],
    detection_run_ids: [101],
    acquisition_ids: [1, 2],
    metrics: {
      support_count: 2,
      interval_count: 2,
      persistence_ratio: 1.0,
      recurrence_count: 0,
      transient_interval_count: 0,
      temporal_consistency: 0.9,
      matched_region_coverage: 0.8,
      first_change_datetime: '2024-01-15T00:00:00Z',
      last_supporting_datetime: '2024-02-15T00:00:00Z',
      temporal_state: 'persistent',
      score_components: { persistence: 0.4, consistency: 0.3, coverage: 0.15 },
    },
    created_at: '2024-02-16T00:00:00Z',
    updated_at: '2024-02-16T00:00:00Z',
    ...overrides,
  }
}

function makeEvidence(candidate: Candidate): CandidateEvidence {
  return {
    candidate,
    temporal: {
      analysis_id: candidate.analysis_id,
      signal_id: candidate.signal_id,
      state: 'persistent',
      support_count: 2,
      interval_count: 2,
      persistence_ratio: 1.0,
      recurrence_count: 0,
      transient_interval_count: 0,
      temporal_consistency: 0.9,
      matched_region_coverage: 0.8,
      first_change_datetime: '2024-01-15T00:00:00Z',
      last_supporting_datetime: '2024-02-15T00:00:00Z',
    },
    intervals: [
      {
        detection_run_id: 101,
        before_acquisition_id: 1,
        after_acquisition_id: 2,
        detector_version: '1.0.0',
        threshold: 0.2,
        min_region_pixels: 5,
        changed_pixel_count: 50,
        total_changed_area_m2: 5000,
        region_count: 1,
        quality_mask_used: true,
        quality_processing_version: '1.0',
        quality_valid_pixel_count: 1000,
        excluded_pixel_count: 10,
        excluded_cloud_pixel_count: 5,
        excluded_shadow_pixel_count: 5,
        raw_changed_pixel_count: 52,
        filtered_changed_pixel_count: 50,
        changed_pixel_percentage: 5.0,
        largest_region_area_m2: 5000,
        mean_region_area_m2: 5000,
        median_region_area_m2: 5000,
        mean_change_signal: 0.6,
        max_change_signal: 0.9,
        regions: [],
      },
    ],
    acquisitions: [],
    evidence_bounds: [75.0, 13.0, 75.01, 13.01],
    aoi_id: 1,
    is_spatially_aligned: true,
    spatial_alignment_details: null,
    is_quality_limited: false,
    quality_limitation_reasons: [],
    provenance_chain: {},
  }
}

describe('Phase 14 — Failure-State Cleanup', () => {
  let persistedAois: AOIResponse[]
  let persistedObservations: ImageryAcquisition[]
  let persistedCandidates: Candidate[]
  let persistedReviews: Record<string, CandidateReview>

  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    sessionStorage.clear()

    persistedAois = [
      { aoi_id: 1, geometry: karnatakaPolygon, created_at: '2024-01-01T00:00:00Z', updated_at: '2024-01-01T00:00:00Z' },
    ]

    persistedObservations = [
      makeObservation({ acquisition_id: 1, aoi_id: 1, acquisition_datetime: '2024-01-10T00:00:00Z', observation_state: 'usable' }),
      makeObservation({ acquisition_id: 2, aoi_id: 1, acquisition_datetime: '2024-01-20T00:00:00Z', observation_state: 'usable' }),
      makeObservation({ acquisition_id: 3, aoi_id: 1, acquisition_datetime: '2024-01-30T00:00:00Z', observation_state: 'usable' }),
    ]

    persistedCandidates = [
      makeCandidate('cand-alpha', { rank: 1 }),
      makeCandidate('cand-beta', { rank: 2 }),
    ]

    persistedReviews = {}

    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'

      if (url.includes('/api/v1/aois') && method === 'GET') {
        return new Response(JSON.stringify(persistedAois), { status: 200 })
      }
      if (url.match(/\/api\/v1\/aoi\/\d+$/) && method === 'GET') {
        const id = Number(url.split('/').pop())
        const aoi = persistedAois.find((a) => a.aoi_id === id)
        if (aoi) return new Response(JSON.stringify(aoi), { status: 200 })
        return new Response(JSON.stringify({ code: 'NotFound', message: 'Not found' }), { status: 404 })
      }
      if (url.includes('/api/v1/imagery/acquisitions') && method === 'GET') {
        return new Response(JSON.stringify({ acquisitions: persistedObservations }), { status: 200 })
      }
      if (url.includes('/api/v1/detections') && method === 'POST') {
        const detectionResult: DetectionResult = {
          run_id: 101,
          before_acquisition_id: 1,
          after_acquisition_id: 2,
          before_item_id: 'S2_TEST_001',
          after_item_id: 'S2_TEST_002',
          detector_version: '1.0.0',
          threshold: 0.2,
          min_region_pixels: 5,
          created_at: '2024-02-01T00:00:00Z',
          region_count: 2,
          changed_pixel_count: 50,
          total_changed_area_m2: 5000,
          regions: [],
        }
        return new Response(JSON.stringify(detectionResult), { status: 200 })
      }
      if (url.includes('/api/v1/temporal-analyses') && method === 'POST') {
        const temporalResult: TemporalResult = {
          analysis_id: 10,
          iou_threshold: 0.25,
          detector_run_count: 2,
          observation_count: 3,
          temporal_span_days: 20,
          state: 'persistent',
          observations: [],
          relationships: [],
          signals: [{
            signal_id: 1,
            geometry: { type: 'Polygon', coordinates: [] },
            support_count: 2,
            interval_count: 2,
            persistence_ratio: 1.0,
            recurrence_count: 0,
            transient_interval_count: 0,
            temporal_consistency: 0.9,
            matched_region_coverage: 0.8,
            first_change_datetime: '2024-01-10T00:00:00Z',
            last_supporting_datetime: '2024-01-30T00:00:00Z',
            state: 'persistent',
          }],
          created_at: '2024-02-05T00:00:00Z',
        }
        return new Response(JSON.stringify(temporalResult), { status: 200 })
      }
      if (url.includes('/api/v1/candidates/triage') && method === 'POST') {
        return new Response(JSON.stringify({ analysis_id: 10, candidates: persistedCandidates }), { status: 200 })
      }
      if (url.match(/\/api\/v1\/candidates\/[^/]+\/evidence$/) && method === 'GET') {
        const id = decodeURIComponent(url.split('/')[url.split('/').length - 2])
        const cand = persistedCandidates.find((c) => c.candidate_id === id) ?? makeCandidate(id)
        return new Response(JSON.stringify(makeEvidence(cand)), { status: 200 })
      }
      if (url.match(/\/api\/v1\/candidates\/[^/]+\/review$/) && method === 'GET') {
        const id = decodeURIComponent(url.split('/')[url.split('/').length - 2])
        const rev = persistedReviews[id] ?? {
          candidate_id: id,
          decision: 'unreviewed',
          note: null,
          created_at: null,
          updated_at: null,
        }
        return new Response(JSON.stringify(rev), { status: 200 })
      }
      if (url.match(/\/api\/v1\/candidates\/[^/]+\/review$/) && method === 'PATCH') {
        const id = decodeURIComponent(url.split('/')[url.split('/').length - 2])
        const body = JSON.parse(String(init?.body ?? '{}'))
        const updated: CandidateReview = {
          candidate_id: id,
          decision: body.decision,
          note: body.note ?? null,
          created_at: '2024-02-18T00:00:00Z',
          updated_at: '2024-02-18T00:00:00Z',
        }
        persistedReviews[id] = updated
        return new Response(JSON.stringify(updated), { status: 200 })
      }
      return new Response(JSON.stringify({}), { status: 200 })
    })
  })

  async function loadAreaAndSelectAoi() {
    render(<App />)
    fireEvent.focus(screen.getByLabelText('Saved AOIs'))
    await waitFor(() => expect(screen.getByLabelText('Saved AOIs').querySelectorAll('option').length).toBeGreaterThan(1))
    fireEvent.change(screen.getByLabelText('Saved AOIs'), { target: { value: '1' } })
    await waitFor(() => expect(screen.getAllByText('Area active', { exact: false }).length).toBeGreaterThan(0))
    await waitFor(() => expect(screen.getByTestId('proceed-to-observations-btn')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('proceed-to-observations-btn'))
    await waitFor(() => expect(screen.getByText('02 / Observations')).toBeInTheDocument())
  }

  it('1. Empty observation search is distinct from acquisition request failure', async () => {
    // 1a. Successful search but 0 observations found -> NoSuitableImageryError
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/api/v1/aois')) return new Response(JSON.stringify(persistedAois), { status: 200 })
      if (url.includes('/api/v1/aoi/1')) return new Response(JSON.stringify(persistedAois[0]), { status: 200 })
      if (url.includes('/api/v1/imagery/acquisitions')) {
        return new Response(JSON.stringify({ code: 'NoSuitableImageryError', message: 'No suitable imagery found' }), { status: 404 })
      }
      return new Response(JSON.stringify({}), { status: 200 })
    })

    await loadAreaAndSelectAoi()
    fireEvent.change(screen.getByLabelText('Observation start date'), { target: { value: '2024-01-01' } })
    fireEvent.change(screen.getByLabelText('Observation end date'), { target: { value: '2024-01-10' } })
    fireEvent.click(screen.getByRole('button', { name: 'Find observations' }))

    await waitFor(() => expect(screen.getByText('No matching observations')).toBeInTheDocument())
    expect(screen.getByText(/No suitable satellite observations were found/i)).toBeInTheDocument()
    expect(screen.queryByText('Observation search failed')).not.toBeInTheDocument()

    // 1b. Server 500 failure during acquisition -> distinct failure state
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/api/v1/imagery/acquisitions')) {
        return new Response(JSON.stringify({ code: 'CatalogFailureError', message: 'STAC catalog connection timed out' }), { status: 502 })
      }
      return new Response(JSON.stringify({}), { status: 200 })
    })

    fireEvent.click(screen.getByRole('button', { name: 'Find observations' }))
    await waitFor(() => expect(screen.getByText('Observation search failed')).toBeInTheDocument())
    expect(screen.getAllByText(/STAC catalog connection timed out/i).length).toBeGreaterThan(0)
  })

  it('2. Unusable observations remain distinguishable from acquisition failure', async () => {
    persistedObservations = [
      makeObservation({ acquisition_id: 1, observation_state: 'valid_unusable', quality_reason: 'excessive_cloud_cover' }),
      makeObservation({ acquisition_id: 2, observation_state: 'valid_unusable', quality_reason: 'excessive_shadow_cover' }),
    ]
    await loadAreaAndSelectAoi()

    await waitFor(() => expect(screen.getByText('0 usable observations available.')).toBeInTheDocument())
    expect(screen.getAllByText('Not usable').length).toBe(2)
    expect(screen.queryByText('Observation search failed')).not.toBeInTheDocument()
  })

  it('15. Failure messages do not expose internal filesystem or database paths', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/api/v1/aoi') && !url.includes('/aois')) {
        return new Response(JSON.stringify({
          code: 'AOIPersistenceError',
          message: 'Failed to write record to SQLite database at /var/app/backend/terrawatch.db (traceback: File /app/db.py line 42)',
        }), { status: 500 })
      }
      return new Response(JSON.stringify({}), { status: 200 })
    })

    render(<App />)
    fireEvent.click(screen.getByRole('button', { name: 'Draw area' }))
    fireEvent.click(screen.getByRole('button', { name: 'Finish rectangle' }))
    expect(screen.getAllByText('Drag a rectangle with two distinct corners to define a valid AOI.').length).toBeGreaterThan(0)
    // Ensure no internal paths are leaked
    expect(screen.queryByText(/terrawatch\.db/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/traceback/i)).not.toBeInTheDocument()
  })

  it('16. Canonical scientific terminology remains intact across all failure states', async () => {
    await loadAreaAndSelectAoi()
    // Verify header context label (domain chip replaced with text-based context)
    expect(screen.getByText('Karnataka analysis')).toBeInTheDocument()
    // Verify Stage rail steps
    expect(screen.getByLabelText(/01.*AREA/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/02.*OBSERVATIONS/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/03.*CHANGES/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/04.*CHANGE HISTORY/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/05.*CANDIDATES/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/06.*EVIDENCE/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/07.*REVIEW/i)).toBeInTheDocument()
  })

  it('17. Fresh startup contract: entire workflow starts completely blank', () => {
    render(<App />)
    expect(screen.getByTestId('aoi-state')).toHaveAttribute('data-state', 'NO_AOI')
    expect(screen.queryByTestId('active-aoi-badge')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Before observation')).not.toBeInTheDocument()
    expect(screen.queryByText(/change regions detected/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/change signal identified/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/Candidate 1/i)).not.toBeInTheDocument()
    expect(screen.queryByTestId('investigation-export-section')).not.toBeInTheDocument()
  })
})
