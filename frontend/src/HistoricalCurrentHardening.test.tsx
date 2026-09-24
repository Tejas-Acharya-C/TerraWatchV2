import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import App from './App'
import type { AOIResponse, Candidate, CandidateEvidence, CandidateReview, DetectionResult, ImageryAcquisition, TemporalResult } from './lib/api'

vi.mock('./MapCanvas', () => ({
  default: () => <div data-testid="mock-map">Mock Map</div>,
}))

const karnatakaPolygon = {
  type: 'Polygon' as const,
  coordinates: [[[75.0, 13.0], [75.2, 13.0], [75.2, 13.2], [75.0, 13.2], [75.0, 13.0]]],
}

function makeObservation(overrides: Partial<ImageryAcquisition>): ImageryAcquisition {
  return {
    acquisition_id: 1,
    aoi_id: 1,
    requested_start_datetime: '2024-01-01T00:00:00Z',
    requested_end_datetime: '2024-02-01T00:00:00Z',
    item_id: 'S2_TEST_001',
    collection: 'sentinel-2-l2a',
    acquisition_datetime: '2024-01-15T00:00:00Z',
    assets: [],
    prepared_path: 'obs.tif',
    raster: { crs: 'EPSG:32643', transform: [], width: 100, height: 100, resolution: [10, 10], bounds: [], count: 2, dtype: 'uint16', nodata: null },
    source_metadata: {},
    created_at: '2024-01-15T00:00:00Z',
    observation_state: 'usable',
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

describe('Phase 13 — Historical / Current Workflow Hardening', () => {
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
      { aoi_id: 2, geometry: karnatakaPolygon, created_at: '2024-01-02T00:00:00Z', updated_at: '2024-01-02T00:00:00Z' },
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

    persistedReviews = {
      'cand-alpha': {
        candidate_id: 'cand-alpha',
        decision: 'accepted',
        note: 'Verified historical change',
        created_at: '2024-02-17T00:00:00Z',
        updated_at: '2024-02-17T00:00:00Z',
      },
    }

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
          created_at: persistedReviews[id]?.created_at ?? '2024-02-18T00:00:00Z',
          updated_at: '2024-02-18T00:00:00Z',
        }
        persistedReviews[id] = updated
        return new Response(JSON.stringify(updated), { status: 200 })
      }
      return new Response(JSON.stringify({}), { status: 200 })
    })
  })

  it('1. Persisted AOIs do not auto-select on startup (remains in NO_AOI blank state)', async () => {
    render(<App />)
    await screen.findAllByText('No area selected')
    expect(screen.getByTestId('aoi-state')).toHaveAttribute('data-state', 'NO_AOI')
    expect(screen.queryByTestId('active-aoi-badge')).not.toBeInTheDocument()
  })



  it('4. Explicit AOI selection activates the workflow and allows clearing back to blank', async () => {
    render(<App />)
    fireEvent.focus(screen.getByLabelText('Saved AOIs'))
    await waitFor(() => expect(screen.getByLabelText('Saved AOIs').querySelectorAll('option').length).toBeGreaterThan(1))

    // Explicitly choose AOI 1
    fireEvent.change(screen.getByLabelText('Saved AOIs'), { target: { value: '1' } })
    await waitFor(() => expect(screen.getByTestId('aoi-state')).toHaveTextContent('Area active'))
    expect(screen.getByTestId('aoi-state')).toHaveAttribute('data-state', 'AOI_SELECTED')
    expect(screen.queryByTestId('active-aoi-badge')).not.toBeInTheDocument()

    // Deselect AOI by choosing empty option
    fireEvent.change(screen.getByLabelText('Saved AOIs'), { target: { value: '' } })
    await waitFor(() => expect(screen.getByTestId('aoi-state')).toHaveAttribute('data-state', 'NO_AOI'))
  })






  it('10. Workflow invalidation does NOT delete or mutate persisted database records', async () => {
    const deleteCalls: string[] = []
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      if (method === 'DELETE') {
        deleteCalls.push(url)
      }
      if (url.includes('/api/v1/aois')) {
        return new Response(JSON.stringify(persistedAois), { status: 200 })
      }
      if (url.includes('/api/v1/imagery/acquisitions')) {
        return new Response(JSON.stringify({ acquisitions: persistedObservations }), { status: 200 })
      }
      return new Response(JSON.stringify({}), { status: 200 })
    })

    render(<App />)
    fireEvent.focus(screen.getByLabelText('Saved AOIs'))
    await waitFor(() => expect(screen.getByLabelText('Saved AOIs').querySelectorAll('option').length).toBeGreaterThan(1))
    fireEvent.change(screen.getByLabelText('Saved AOIs'), { target: { value: '1' } })
    await waitFor(() => expect(screen.getAllByText('Area active', { exact: false }).length).toBeGreaterThan(0))

    // Deselect AOI (invalidation of active workflow)
    fireEvent.change(screen.getByLabelText('Saved AOIs'), { target: { value: '' } })
    await waitFor(() => expect(screen.getByTestId('aoi-state')).toHaveAttribute('data-state', 'NO_AOI'))

    // No DELETE request was issued against candidate_reviews, detections, or acquisitions
    expect(deleteCalls).toEqual([])
    expect(persistedReviews['cand-alpha'].decision).toBe('accepted')
  })

  it('11. Stale in-flight AOI responses cannot overwrite a newer active AOI workflow', async () => {
    let resolveAoi1Acquisitions!: (value: unknown) => void
    const delayedAoi1Promise = new Promise((resolve) => {
      resolveAoi1Acquisitions = resolve
    })

    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/api/v1/aois')) {
        return new Response(JSON.stringify(persistedAois), { status: 200 })
      }
      if (url.includes('/api/v1/imagery/acquisitions?aoi_id=1')) {
        await delayedAoi1Promise
        return new Response(JSON.stringify({ acquisitions: [makeObservation({ acquisition_id: 999, aoi_id: 1, item_id: 'STALE_AOI_1_OBS' })] }), { status: 200 })
      }
      if (url.includes('/api/v1/imagery/acquisitions?aoi_id=2')) {
        return new Response(JSON.stringify({ acquisitions: [makeObservation({ acquisition_id: 201, aoi_id: 2, item_id: 'FRESH_AOI_2_OBS' })] }), { status: 200 })
      }
      return new Response(JSON.stringify({}), { status: 200 })
    })

    render(<App />)
    fireEvent.focus(screen.getByLabelText('Saved AOIs'))
    await waitFor(() => expect(screen.getByLabelText('Saved AOIs').querySelectorAll('option').length).toBeGreaterThan(1))

    // User selects AOI 1 (its acquisition fetch is delayed)
    fireEvent.change(screen.getByLabelText('Saved AOIs'), { target: { value: '1' } })

    // Return to Stage 1 if auto-advanced
    const areaRailBtn = screen.queryByRole('button', { name: /01.*AREA/i })
    if (areaRailBtn) fireEvent.click(areaRailBtn)

    // User immediately switches to AOI 2
    fireEvent.change(screen.getByLabelText('Saved AOIs'), { target: { value: '2' } })
    await waitFor(() => expect(screen.getAllByText('Area active', { exact: false }).length).toBeGreaterThan(0))

    // AOI 2 observations arrive
    await waitFor(() => expect(screen.getByTestId('proceed-to-observations-btn')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('proceed-to-observations-btn'))
    await waitFor(() => expect(screen.getByText('FRESH_AOI_2_OBS')).toBeInTheDocument())

    // Now delayed AOI 1 response arrives
    resolveAoi1Acquisitions(null)

    // Stale AOI 1 observations must NOT overwrite AOI 2
    await waitFor(() => {
      expect(screen.queryByText('STALE_AOI_1_OBS')).not.toBeInTheDocument()
      expect(screen.getByText('FRESH_AOI_2_OBS')).toBeInTheDocument()
    })
  })


  it('13. Reload / fresh startup contract: entire workflow starts completely blank', () => {
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
