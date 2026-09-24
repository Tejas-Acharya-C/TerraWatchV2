import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import type { Candidate, CandidateEvidence, CandidateReview, ImageryAcquisition } from './lib/api'

vi.mock('./MapCanvas', () => ({
  default: ({
    changeRegions,
    onSelectCandidate,
  }: {
    changeRegions?: Array<{ region_id: number; geometry: any; selected?: boolean; candidate_id?: string }>
    onSelectCandidate?: (id: string) => void
  }) => {
    const candidateRegions = changeRegions?.filter((r) => r.candidate_id) ?? []
    const selectedRegion = candidateRegions.find((r) => r.selected)
    return (
      <div data-testid="mock-map">
        <span data-testid="map-candidate-count">{candidateRegions.length}</span>
        <span data-testid="map-selected-id">{selectedRegion ? `map-selected:${selectedRegion.candidate_id}` : 'none'}</span>
        {candidateRegions.map((c) => (
          <button
            key={c.candidate_id}
            type="button"
            data-testid={`map-candidate-${c.candidate_id}`}
            onClick={() => c.candidate_id && onSelectCandidate?.(c.candidate_id)}
          >
            Focus {c.candidate_id}
          </button>
        ))}
      </div>
    )
  },
}))

describe('Stage 07 / REVIEW Workflow (Phase 9)', () => {
  const dummyRaster = {
    crs: 'EPSG:32643',
    transform: [10, 0, 0, 0, -10, 0],
    width: 100,
    height: 100,
    resolution: [10, 10] as [number, number],
    bounds: [0, 0, 1000, 1000],
    count: 2,
    dtype: 'uint16',
    nodata: 0,
  }

  const createMockAcquisition = (
    id: number,
    date: string,
    state: string = 'usable'
  ): ImageryAcquisition => ({
    acquisition_id: id,
    aoi_id: 1,
    requested_start_datetime: date,
    requested_end_datetime: date,
    item_id: `S2_${id}`,
    collection: 'sentinel-2-l2a',
    acquisition_datetime: date,
    assets: [],
    prepared_path: `path_${id}.tif`,
    raster: dummyRaster,
    source_metadata: {},
    created_at: date,
    observation_state: state,
    quality_reason: 'quality_policy_passed',
    quality_metrics: {
      usable_percentage: 85,
      cloud_percentage: 10,
      shadow_percentage: 5,
      invalid_percentage: 0,
    },
  })

  const candidateA: Candidate = {
    candidate_id: 'analysis-42-signal-101',
    analysis_id: 42,
    signal_id: 101,
    geometry: { type: 'Polygon', coordinates: [[[77.4, 12.8], [77.45, 12.8], [77.45, 12.85], [77.4, 12.85], [77.4, 12.8]]] },
    score: 0.8421,
    rank: 1,
    severity: 'high',
    priority: 'urgent',
    review_state: 'unreviewed',
    source_signal_ids: [101],
    detection_run_ids: [201],
    acquisition_ids: [10, 11],
    metrics: {
      support_count: 2,
      interval_count: 2,
      persistence_ratio: 1.0,
      recurrence_count: 0,
      transient_interval_count: 0,
      temporal_consistency: 0.92,
      matched_region_coverage: 0.88,
      first_change_datetime: '2024-01-15T10:00:00Z',
      last_supporting_datetime: '2024-03-15T10:00:00Z',
      temporal_state: 'persistent',
      quality_support: 0.91,
      mean_change_signal: 0.62,
      max_change_signal: 0.78,
      total_area_m2: 4500.0,
      score_components: { temporal_persistence: 0.45, quality_support: 0.25, magnitude: 0.14 },
    },
    created_at: '2024-01-01T00:00:00Z',
    updated_at: '2024-01-01T00:00:00Z',
  }

  const candidateB: Candidate = {
    candidate_id: 'analysis-42-signal-102',
    analysis_id: 42,
    signal_id: 102,
    geometry: { type: 'Polygon', coordinates: [[[77.5, 12.9], [77.55, 12.9], [77.55, 12.95], [77.5, 12.95], [77.5, 12.9]]] },
    score: 0.612,
    rank: 2,
    severity: 'medium',
    priority: 'normal',
    review_state: 'unreviewed',
    source_signal_ids: [102],
    detection_run_ids: [201],
    acquisition_ids: [10, 11],
    metrics: {
      support_count: 1,
      interval_count: 2,
      persistence_ratio: 0.5,
      recurrence_count: 0,
      transient_interval_count: 1,
      temporal_consistency: 0.75,
      matched_region_coverage: 0.65,
      first_change_datetime: '2024-02-01T10:00:00Z',
      last_supporting_datetime: '2024-03-15T10:00:00Z',
      temporal_state: 'transient',
      quality_support: 0.82,
      mean_change_signal: 0.38,
      max_change_signal: 0.45,
      total_area_m2: 2100.0,
      score_components: { temporal_persistence: 0.25, quality_support: 0.22, magnitude: 0.14 },
    },
    created_at: '2024-01-01T00:00:00Z',
    updated_at: '2024-01-01T00:00:00Z',
  }

  const mockEvidenceA: CandidateEvidence = {
    aoi_id: 1,
    candidate: candidateA,
    temporal: {
      analysis_id: 42,
      signal_id: 101,
      state: 'persistent',
      support_count: 2,
      recurrence_count: 0,
      transient_interval_count: 0,
      observation_count: 3,
      interval_count: 2,
      persistence_ratio: 1.0,
      first_change_datetime: '2024-01-15T10:00:00Z',
      last_supporting_datetime: '2024-03-15T10:00:00Z',
      quality_aware_temporal_support: 0.91,
      temporal_consistency: 0.92,
      matched_region_coverage: 0.88,
    },
    intervals: [{
      detection_run_id: 201,
      before_acquisition_id: 10,
      after_acquisition_id: 11,
      detector_version: 'diff-v2',
      threshold: 0.25,
      min_region_pixels: 4,
      changed_pixel_count: 45,
      total_changed_area_m2: 4500.0,
      region_count: 1,
      quality_mask_used: true,
      quality_processing_version: 'sentinel-2-l2a-v1',
      quality_valid_pixel_count: 40,
      excluded_pixel_count: 5,
      mean_change_signal: 0.62,
      max_change_signal: 0.78,
      raw_changed_pixel_count: 50,
      filtered_changed_pixel_count: 45,
      changed_pixel_percentage: 4.5,
      largest_region_area_m2: 4500.0,
      mean_region_area_m2: 4500.0,
      median_region_area_m2: 4500.0,
      excluded_cloud_pixel_count: 3,
      excluded_shadow_pixel_count: 2,
      regions: [],
    }],
    acquisitions: [
      {
        acquisition_id: 10,
        aoi_id: 1,
        item_id: 'S2_OBS_10',
        collection: 'sentinel-2-l2a',
        acquisition_datetime: '2024-01-15T10:00:00Z',
        cloud_cover: 0.05,
        mgrs_tile: '43PFS',
        observation_state: 'usable',
        quality_reason: null,
        usable_pixel_fraction: 0.95,
        cloud_fraction: 0.03,
        shadow_fraction: 0.02,
        invalid_fraction: 0.0,
        quality_processing_version: 'sentinel-2-l2a-v1',
        masking_method: 'scl-quality-mask-v1',
        quality_mask_available: true,
        display_available: true,
        display_url: '/api/v1/acquisitions/10/display',
        visualization_version: 'sentinel-2-rgb-percentile-v2',
        raster: dummyRaster,
        bands: ['B02', 'B03', 'B04', 'B08'],
        preview_url: '/api/v1/acquisitions/10/preview',
      },
      {
        acquisition_id: 11,
        aoi_id: 1,
        item_id: 'S2_OBS_11',
        collection: 'sentinel-2-l2a',
        acquisition_datetime: '2024-02-15T10:00:00Z',
        cloud_cover: 0.02,
        mgrs_tile: '43PFS',
        observation_state: 'usable',
        quality_reason: null,
        usable_pixel_fraction: 0.98,
        cloud_fraction: 0.01,
        shadow_fraction: 0.01,
        invalid_fraction: 0.0,
        quality_processing_version: 'sentinel-2-l2a-v1',
        masking_method: 'scl-quality-mask-v1',
        quality_mask_available: true,
        display_available: true,
        display_url: '/api/v1/acquisitions/11/display',
        visualization_version: 'sentinel-2-rgb-percentile-v2',
        raster: dummyRaster,
        bands: ['B02', 'B03', 'B04', 'B08'],
        preview_url: '/api/v1/acquisitions/11/preview',
      },
    ],
    evidence_bounds: [77.5, 12.8, 77.55, 12.85],
    is_spatially_aligned: true,
    spatial_alignment_details: {
      crs: 'EPSG:32643',
      width: 100,
      height: 100,
      pixel_size: 10,
      status: 'aligned',
    },
    is_quality_limited: false,
    quality_limitation_reasons: [],
    provenance_chain: {
      pipeline_version: '2.0.0',
      evidence_generated_at: '2024-03-16T12:00:00Z',
    },
  }

  let reviewsDb: Record<string, CandidateReview> = {}
  let patchCalls: Array<{ candidateId: string; body: any }> = []
  let customFetchHandler: ((input: RequestInfo | URL, init?: RequestInit) => Promise<Response | null>) | null = null

  beforeEach(() => {
    reviewsDb = {}
    patchCalls = []
    customFetchHandler = null

    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      if (customFetchHandler) {
        const customRes = await customFetchHandler(input, init)
        if (customRes) return customRes
      }

      const url = String(input)
      const method = init?.method ?? 'GET'

      if (url.includes('/api/v1/health')) {
        return new Response(JSON.stringify({ status: 'healthy' }), { status: 200 })
      }
      if (url.includes('/api/v1/aois')) {
        return new Response(JSON.stringify([
          { aoi_id: 1, geometry: { type: 'Polygon', coordinates: [[[77.0, 12.0], [77.5, 12.0], [77.5, 12.5], [77.0, 12.5], [77.0, 12.0]]] }, created_at: '2024-01-01T00:00:00Z', updated_at: '2024-01-01T00:00:00Z' },
        ]), { status: 200 })
      }
      if (url.includes('/api/v1/aoi/1') || (url.includes('/api/v1/aoi') && !url.includes('/acquisitions'))) {
        return new Response(JSON.stringify({
          aoi_id: 1,
          geometry: { type: 'Polygon', coordinates: [[[77.0, 12.0], [77.5, 12.0], [77.5, 12.5], [77.0, 12.5], [77.0, 12.0]]] },
          created_at: '2024-01-01T00:00:00Z',
          updated_at: '2024-01-01T00:00:00Z',
        }), { status: 200 })
      }
      if (url.includes('/api/v1/imagery/acquisitions') || url.includes('/api/v1/imagery/acquire') || url.includes('/acquisitions')) {
        return new Response(JSON.stringify({
          aoi_id: 1,
          total: 2,
          acquisitions: [createMockAcquisition(10, '2024-01-15T10:00:00Z'), createMockAcquisition(11, '2024-02-15T10:00:00Z')],
        }), { status: 200 })
      }
      if (url.includes('/api/v1/detections') || url.includes('/api/v1/detection/detect')) {
        return new Response(JSON.stringify({
          run_id: 201,
          before_acquisition_id: 10,
          after_acquisition_id: 11,
          region_count: 1,
          changed_pixel_count: 45,
          total_changed_area_m2: 4500.0,
          detector_version: 'diff-v2',
          threshold: 0.25,
          min_region_pixels: 4,
          regions: [{ region_id: 1, geometry: { type: 'Polygon', coordinates: [] }, area_m2: 4500.0, mean_change_signal: 0.62, max_change_signal: 0.78 }],
        }), { status: 200 })
      }
      if (url.includes('/api/v1/temporal-analyses') || url.includes('/api/v1/temporal/analyze')) {
        return new Response(JSON.stringify({
          analysis_id: 42,
          iou_threshold: 0.25,
          detector_run_count: 2,
          observation_count: 3,
          temporal_span_days: 60,
          state: 'persistent',
          usable_observation_count: 3,
          usable_interval_count: 2,
          quality_support: 0.91,
          quality_aggregation_method: 'mean',
          seasonal_interpretation: 'seasonal_compatible',
          observations: [],
          relationships: [],
          signals: [{
            signal_id: 101,
            geometry: { type: 'Polygon', coordinates: [] },
            support_count: 2,
            interval_count: 2,
            persistence_ratio: 1.0,
            recurrence_count: 0,
            transient_interval_count: 0,
            temporal_consistency: 0.92,
            matched_region_coverage: 0.88,
            first_change_datetime: '2024-01-15T10:00:00Z',
            last_supporting_datetime: '2024-03-15T10:00:00Z',
            state: 'persistent',
            usable_interval_count: 2,
            supporting_interval_count: 2,
            quality_support: 0.91,
            seasonal_interpretation: 'seasonal_compatible',
          }],
          created_at: '2024-01-01T00:00:00Z',
        }), { status: 200 })
      }
      if (url.includes('/api/v1/orchestrations/analyze')) {
        return new Response(
          JSON.stringify({
            aoi_id: 1,
            eligible_observation_ids: [10, 11, 12],
            reused_detection_count: 0,
            generated_detection_count: 2,
            reused_analysis: false,
            analysis: {
              analysis_id: 42,
              iou_threshold: 0.25,
              detector_run_count: 2,
              observation_count: 3,
              temporal_span_days: 60,
              state: 'persistent',
              usable_observation_count: 3,
              usable_interval_count: 2,
              quality_support: 0.91,
              quality_aggregation_method: 'mean',
              seasonal_interpretation: 'seasonal_compatible',
              observations: [],
              relationships: [],
              signals: [{
                signal_id: 101,
                geometry: { type: 'Polygon', coordinates: [] },
                support_count: 2,
                interval_count: 2,
                persistence_ratio: 1.0,
                recurrence_count: 0,
                transient_interval_count: 0,
                temporal_consistency: 0.92,
                matched_region_coverage: 0.88,
                first_change_datetime: '2024-01-15T10:00:00Z',
                last_supporting_datetime: '2024-03-15T10:00:00Z',
                state: 'persistent',
                usable_interval_count: 2,
                supporting_interval_count: 2,
                quality_support: 0.91,
                seasonal_interpretation: 'seasonal_compatible',
              }],
              created_at: '2024-01-01T00:00:00Z',
            },
            candidates: {
              analysis_id: 42,
              candidates: [],
            },
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        )
      }
      if (url.includes('/api/v1/candidates/triage')) {
        return new Response(JSON.stringify({
          analysis_id: 42,
          candidates: [candidateA, candidateB],
        }), { status: 200 })
      }
      if (url.includes('/api/v1/candidates/') && url.includes('/evidence')) {
        const match = url.match(/\/candidates\/([^/]+)\/evidence/)
        const cid = match ? decodeURIComponent(match[1]) : ''
        if (cid === candidateB.candidate_id) {
          return new Response(JSON.stringify({
            ...mockEvidenceA,
            candidate: candidateB,
          }), { status: 200 })
        }
        return new Response(JSON.stringify(mockEvidenceA), { status: 200 })
      }
      if (url.includes('/api/v1/candidates/') && url.includes('/review')) {
        const match = url.match(/\/candidates\/([^/]+)\/review/)
        const cid = match ? decodeURIComponent(match[1]) : ''

        if (method === 'PATCH') {
          const body = JSON.parse(init?.body as string)
          patchCalls.push({ candidateId: cid, body })

          const saved: CandidateReview = {
            candidate_id: cid,
            decision: body.decision,
            note: body.note ?? null,
            created_at: reviewsDb[cid]?.created_at ?? '2024-02-01T10:00:00Z',
            updated_at: '2024-02-01T10:05:00Z',
          }
          reviewsDb[cid] = saved
          return new Response(JSON.stringify(saved), { status: 200 })
        }

        if (reviewsDb[cid]) {
          return new Response(JSON.stringify(reviewsDb[cid]), { status: 200 })
        }
        return new Response(JSON.stringify({
          candidate_id: cid,
          decision: 'unreviewed',
          note: null,
          created_at: null,
          updated_at: null,
        }), { status: 200 })
      }

      return new Response(JSON.stringify({}), { status: 200 })
    })
  })

  async function navigateToCandidatesWorkflow() {
    render(<App />)
    await screen.findAllByText('No area selected')

    fireEvent.focus(screen.getByLabelText('Saved AOIs'))
    await waitFor(() => expect(screen.getByLabelText('Saved AOIs').querySelectorAll('option').length).toBeGreaterThan(1))
    fireEvent.change(screen.getByLabelText('Saved AOIs'), { target: { value: '1' } })

    await waitFor(() => expect(screen.getByTestId('proceed-to-observations-btn')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('proceed-to-observations-btn'))
    await waitFor(() => expect(screen.getByTestId('proceed-to-changes-btn')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('proceed-to-changes-btn'))
    await waitFor(() => expect(screen.getByText('03 / Changes')).toBeInTheDocument())

    const runBtn = screen.getByTestId('run-automated-analysis-btn')
    fireEvent.click(runBtn)
    await waitFor(() => expect(screen.getByTestId('orchestration-summary')).toBeInTheDocument())

    // Navigate to Stage 05 CANDIDATES
    const candidatesBtn = await screen.findByTestId('proceed-to-candidates-btn')
    fireEvent.click(candidatesBtn)
    await waitFor(() => expect(screen.getByText('05 / Candidates')).toBeInTheDocument())
  }

  async function navigateToReviewForCandidateA() {
    await navigateToCandidatesWorkflow()
    fireEvent.click(screen.getByRole('button', { name: 'Triage temporal signals' }))
    await waitFor(() => expect(screen.getByText('#1 / urgent priority')).toBeInTheDocument())
    fireEvent.click(screen.getByText('#1 / urgent priority'))
    fireEvent.click(screen.getByRole('button', { name: 'Inspect candidate evidence' }))
    await waitFor(() => expect(screen.getByText('06 / Evidence')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('record-review-decision-btn'))
    await waitFor(() => expect(screen.getByText('07 / Review')).toBeInTheDocument())
  }

  it('1. Stage 07 REVIEW appears correctly in the workflow rail', async () => {
    await navigateToCandidatesWorkflow()
    const railSteps = screen.getAllByLabelText(/\d{2}\s+[A-Z\s]+/)
    const railLabels = railSteps.map((s) => s.getAttribute('aria-label'))
    expect(railLabels).toContain('07 REVIEW')
  })

  it('2. REVIEW header/kicker/title/subtitle render correctly', async () => {
    await navigateToReviewForCandidateA()
    expect(screen.getByText('07 / Review')).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 2, name: 'REVIEW' })).toBeInTheDocument()
    expect(screen.getByText('Record analyst review decision for the selected candidate.')).toBeInTheDocument()
  })

  it('3. No candidate selected produces clear prerequisite state, no auto-selection, and navigation to CANDIDATES', async () => {
    await navigateToCandidatesWorkflow()
    fireEvent.click(screen.getByRole('button', { name: 'Triage temporal signals' }))
    await waitFor(() => expect(screen.getByText('#1 / urgent priority')).toBeInTheDocument())

    // Direct navigation to Stage 07 without selecting a candidate
    fireEvent.click(screen.getByTestId('inspect-review-unselected-btn'))

    await waitFor(() => {
      expect(screen.getByTestId('no-candidate-review-state')).toBeInTheDocument()
      expect(screen.getByText('No candidate is currently selected for review. Select a candidate in Stage 05 / CANDIDATES.')).toBeInTheDocument()
    })

    // No auto-selected candidate header
    expect(screen.queryByRole('heading', { level: 4, name: /Candidate #/i })).not.toBeInTheDocument()

    // Navigation button returns to CANDIDATES
    fireEvent.click(screen.getByRole('button', { name: 'Select candidate in Stage 05 / CANDIDATES' }))
    await waitFor(() => expect(screen.getByText('05 / Candidates')).toBeInTheDocument())
  })

  it('4. Candidate selected with no persisted review shows UNREVIEWED and three canonical decisions', async () => {
    await navigateToReviewForCandidateA()
    expect(screen.getByTestId('current-review-decision')).toHaveTextContent('UNREVIEWED')
    expect(screen.getByTestId('review-unreviewed-label')).toHaveTextContent('Candidate is unreviewed')

    expect(screen.getByTestId('decision-accepted-radio')).toBeInTheDocument()
    expect(screen.getByTestId('decision-rejected-radio')).toBeInTheDocument()
    expect(screen.getByTestId('decision-investigate-radio')).toBeInTheDocument()
  })

  it('5. Accept decision persists via PATCH API and updates UI', async () => {
    await navigateToReviewForCandidateA()

    fireEvent.click(screen.getByTestId('decision-accepted-radio'))
    fireEvent.change(screen.getByTestId('review-note-input'), { target: { value: 'Verified change' } })
    fireEvent.click(screen.getByTestId('save-review-btn'))

    await waitFor(() => expect(screen.getByTestId('review-save-success')).toBeInTheDocument())
    expect(screen.getByTestId('current-review-decision')).toHaveTextContent('ACCEPTED')
    expect(patchCalls).toHaveLength(1)
    expect(patchCalls[0]).toEqual({
      candidateId: candidateA.candidate_id,
      body: { decision: 'accepted', note: 'Verified change' },
    })
  })

  it('6. Reject decision persists via PATCH API with rejected', async () => {
    await navigateToReviewForCandidateA()

    fireEvent.click(screen.getByTestId('decision-rejected-radio'))
    fireEvent.click(screen.getByTestId('save-review-btn'))

    await waitFor(() => expect(screen.getByTestId('review-save-success')).toBeInTheDocument())
    expect(screen.getByTestId('current-review-decision')).toHaveTextContent('REJECTED')
    expect(patchCalls[0].body.decision).toBe('rejected')
  })

  it('7. Investigate decision persists via PATCH API with investigate', async () => {
    await navigateToReviewForCandidateA()

    fireEvent.click(screen.getByTestId('decision-investigate-radio'))
    fireEvent.click(screen.getByTestId('save-review-btn'))

    await waitFor(() => expect(screen.getByTestId('review-save-success')).toBeInTheDocument())
    expect(screen.getByTestId('current-review-decision')).toHaveTextContent('INVESTIGATE')
    expect(patchCalls[0].body.decision).toBe('investigate')
  })

  it('8. Existing review loads persisted decision and allows changing it via PATCH', async () => {
    reviewsDb[candidateA.candidate_id] = {
      candidate_id: candidateA.candidate_id,
      decision: 'investigate',
      note: 'Initial note',
      created_at: '2024-02-01T10:00:00Z',
      updated_at: '2024-02-01T10:00:00Z',
    }

    await navigateToReviewForCandidateA()

    await waitFor(() => expect(screen.getByTestId('current-review-decision')).toHaveTextContent('INVESTIGATE'))
    expect(screen.getByTestId('review-note-input')).toHaveValue('Initial note')

    // Change to accepted
    fireEvent.click(screen.getByTestId('decision-accepted-radio'))
    fireEvent.click(screen.getByTestId('save-review-btn'))

    await waitFor(() => expect(screen.getByTestId('review-save-success')).toBeInTheDocument())
    expect(screen.getByTestId('current-review-decision')).toHaveTextContent('ACCEPTED')
    expect(patchCalls[0].body.decision).toBe('accepted')
  })

  it('9. Candidate switching immediately clears previous review state and loads candidate B review', async () => {
    reviewsDb[candidateA.candidate_id] = {
      candidate_id: candidateA.candidate_id,
      decision: 'accepted',
      note: 'Note for A',
      created_at: '2024-02-01T10:00:00Z',
      updated_at: '2024-02-01T10:00:00Z',
    }

    await navigateToReviewForCandidateA()
    await waitFor(() => expect(screen.getByTestId('current-review-decision')).toHaveTextContent('ACCEPTED'))

    // Return to triage and select candidate B
    fireEvent.click(screen.getByRole('button', { name: '← Back to triage' }))
    await waitFor(() => expect(screen.getByText('05 / Candidates')).toBeInTheDocument())

    fireEvent.click(screen.getByText('#2 / normal priority'))
    fireEvent.click(screen.getByRole('button', { name: 'Inspect candidate evidence' }))
    await waitFor(() => expect(screen.getByText('06 / Evidence')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('record-review-decision-btn'))
    await waitFor(() => expect(screen.getByText('07 / Review')).toBeInTheDocument())

    // Candidate B should be UNREVIEWED and not show A's note
    await waitFor(() => expect(screen.getByTestId('current-review-decision')).toHaveTextContent('UNREVIEWED'))
    expect(screen.getByTestId('review-note-input')).toHaveValue('')
    expect(screen.getByTestId('review-candidate-id')).toHaveTextContent(candidateB.candidate_id)
  })

  it('10. Stale in-flight response for candidate A cannot overwrite candidate B review', async () => {
    let resolveReviewA!: (res: Response) => void
    const delayAPromise = new Promise<Response>((resolve) => {
      resolveReviewA = resolve
    })

    reviewsDb[candidateB.candidate_id] = {
      candidate_id: candidateB.candidate_id,
      decision: 'rejected',
      note: 'Decision for B',
      created_at: '2024-02-02T10:00:00Z',
      updated_at: '2024-02-02T10:00:00Z',
    }

    customFetchHandler = async (input, init) => {
      const url = String(input)
      if (url.includes(`${candidateA.candidate_id}/review`) && (!init || init.method === 'GET')) {
        return delayAPromise
      }
      return null
    }

    await navigateToCandidatesWorkflow()
    fireEvent.click(screen.getByRole('button', { name: 'Triage temporal signals' }))
    await waitFor(() => expect(screen.getByText('#1 / urgent priority')).toBeInTheDocument())

    // Select candidate A (triggers pending fetch for A)
    fireEvent.click(screen.getByText('#1 / urgent priority'))

    // Immediately switch to Candidate B before A resolves
    fireEvent.click(screen.getByText('#2 / normal priority'))
    fireEvent.click(screen.getByRole('button', { name: 'Inspect candidate evidence' }))
    await waitFor(() => expect(screen.getByText('06 / Evidence')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('record-review-decision-btn'))
    await waitFor(() => expect(screen.getByText('07 / Review')).toBeInTheDocument())

    // Resolve A's stale response now
    resolveReviewA(new Response(JSON.stringify({
      candidate_id: candidateA.candidate_id,
      decision: 'accepted',
      note: 'Late note from A',
      created_at: '2024-02-01T10:00:00Z',
      updated_at: '2024-02-01T10:00:00Z',
    }), { status: 200 }))

    // Verify candidate B's review is shown and NOT A's late response
    await waitFor(() => expect(screen.getByTestId('current-review-decision')).toHaveTextContent('REJECTED'))
    expect(screen.getByTestId('review-note-input')).toHaveValue('Decision for B')
    expect(screen.getByTestId('review-note-input')).not.toHaveValue('Late note from A')
  })

  it('11. Review save failure displays error and preserves unsaved draft', async () => {
    customFetchHandler = async (input, init) => {
      const url = String(input)
      if (url.includes('/review') && init?.method === 'PATCH') {
        return new Response(JSON.stringify({
          code: 'ReviewSaveError',
          message: 'Database lock timeout',
        }), { status: 500 })
      }
      return null
    }

    await navigateToReviewForCandidateA()

    fireEvent.click(screen.getByTestId('decision-accepted-radio'))
    fireEvent.change(screen.getByTestId('review-note-input'), { target: { value: 'My pending note' } })
    fireEvent.click(screen.getByTestId('save-review-btn'))

    await waitFor(() => expect(screen.getByTestId('review-save-error')).toBeInTheDocument())
    expect(screen.getByTestId('review-save-error')).toHaveTextContent('Database lock timeout')

    // Draft remains preserved
    expect(screen.getByTestId('review-note-input')).toHaveValue('My pending note')
    expect(screen.getByTestId('current-review-decision')).toHaveTextContent('UNREVIEWED')
  })

  it('12. Upstream invalidation clears active review UI but does not delete persisted review in DB', async () => {
    reviewsDb[candidateA.candidate_id] = {
      candidate_id: candidateA.candidate_id,
      decision: 'accepted',
      note: 'Persisted review',
      created_at: '2024-02-01T10:00:00Z',
      updated_at: '2024-02-01T10:00:00Z',
    }

    await navigateToReviewForCandidateA()
    await waitFor(() => expect(screen.getByTestId('current-review-decision')).toHaveTextContent('ACCEPTED'))

    // Invalidate upstream by starting drawing in AOI
    fireEvent.click(screen.getByTestId('back-to-area-btn'))
    fireEvent.click(screen.getByRole('button', { name: 'Draw area' }))

    // Active review UI is cleared and Stage 07 is locked
    expect(screen.queryByTestId('current-review-decision')).not.toBeInTheDocument()
    expect(screen.getByRole('group', { name: 'Analysis workflow status' })).toBeInTheDocument()

    // Persisted review record was NOT deleted from DB
    expect(reviewsDb[candidateA.candidate_id]).toBeDefined()
    expect(reviewsDb[candidateA.candidate_id].decision).toBe('accepted')
  })

  it('13. Fresh startup has no active candidate, no active review, and no auto-selection', async () => {
    render(<App />)
    await screen.findAllByText('No area selected')

    expect(screen.queryByTestId('analyst-review-section')).not.toBeInTheDocument()
    expect(screen.queryByTestId('current-review-decision')).not.toBeInTheDocument()
    expect(screen.queryByTestId('no-candidate-review-state')).not.toBeInTheDocument()
  })

  it('14. Scientific immutability: saving a review does not alter candidate scientific fields', async () => {
    await navigateToReviewForCandidateA()

    const scoreBefore = screen.getByTestId('review-score').textContent
    const priorityBefore = screen.getByTestId('review-priority').textContent
    const severityBefore = screen.getByTestId('review-severity').textContent

    fireEvent.click(screen.getByTestId('decision-accepted-radio'))
    fireEvent.click(screen.getByTestId('save-review-btn'))

    await waitFor(() => expect(screen.getByTestId('review-save-success')).toBeInTheDocument())

    expect(screen.getByTestId('review-score').textContent).toBe(scoreBefore)
    expect(screen.getByTestId('review-priority').textContent).toBe(priorityBefore)
    expect(screen.getByTestId('review-severity').textContent).toBe(severityBefore)
  })

  it('15. Terminology: no confidence/probability/certainty/likelihood wording is used for review', async () => {
    await navigateToReviewForCandidateA()

    const reviewSectionText = screen.getByTestId('analyst-review-section').textContent ?? ''
    expect(reviewSectionText.toLowerCase()).not.toMatch(/confidence|probability|certainty|likelihood/)
  })

  it('16. Stage 06 action button navigates to REVIEW for the selected candidate', async () => {
    await navigateToCandidatesWorkflow()
    fireEvent.click(screen.getByRole('button', { name: 'Triage temporal signals' }))
    await waitFor(() => expect(screen.getByText('#1 / urgent priority')).toBeInTheDocument())
    fireEvent.click(screen.getByText('#1 / urgent priority'))
    fireEvent.click(screen.getByRole('button', { name: 'Inspect candidate evidence' }))
    await waitFor(() => expect(screen.getByText('06 / Evidence')).toBeInTheDocument())

    const reviewBtn = screen.getByTestId('record-review-decision-btn')
    expect(reviewBtn).toHaveTextContent(/Record review decision/i)
    fireEvent.click(reviewBtn)

    await waitFor(() => {
      expect(screen.getByText('07 / Review')).toBeInTheDocument()
      expect(screen.getByTestId('review-candidate-id')).toHaveTextContent(candidateA.candidate_id)
    })
  })
})
