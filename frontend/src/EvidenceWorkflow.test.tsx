import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import type { Candidate, CandidateEvidence, ImageryAcquisition } from './lib/api'

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

describe('Stage 06 / EVIDENCE Simplification (Phase 8)', () => {
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

  const candidate1: Candidate = {
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
      total_area_m2: 8400,
      score_components: {
        temporal_persistence: 1.0,
        temporal_consistency: 0.92,
        change_magnitude: 0.68,
        observation_quality: 0.91,
      },
      explanation: {
        summary: 'Deterministic evidence score 0.842 (urgent priority, high severity) for persistent land-surface change.',
        positive_factors: ['High persistence ratio (1.0)', 'Strong change magnitude (0.68)'],
        limiting_factors: [],
        severity_rationale: "Classified as 'high' physical severity based on observed extent (8400 m², mean ΔNDVI 0.62).",
        priority_rationale: "Assigned 'urgent' priority for triage based on operational urgency.",
      },
    },
    created_at: '2024-03-16T00:00:00Z',
    updated_at: '2024-03-16T00:00:00Z',
  }

  const candidate2: Candidate = {
    candidate_id: 'analysis-42-signal-102',
    analysis_id: 42,
    signal_id: 102,
    geometry: { type: 'Polygon', coordinates: [[[77.5, 12.8], [77.55, 12.8], [77.55, 12.85], [77.5, 12.85], [77.5, 12.8]]] },
    score: 0.3850,
    rank: 2,
    severity: 'low',
    priority: 'normal',
    review_state: 'investigate',
    source_signal_ids: [102],
    detection_run_ids: [201],
    acquisition_ids: [10, 11],
    metrics: {
      support_count: 1,
      interval_count: 2,
      persistence_ratio: 0.5,
      recurrence_count: 0,
      transient_interval_count: 1,
      temporal_consistency: 0.40,
      matched_region_coverage: 0.35,
      first_change_datetime: '2024-02-15T10:00:00Z',
      last_supporting_datetime: '2024-02-15T10:00:00Z',
      temporal_state: 'transient',
      quality_support: 0.45,
      mean_change_signal: 0.28,
      max_change_signal: 0.35,
      total_area_m2: 1200,
      score_components: {
        temporal_persistence: 0.50,
        temporal_consistency: 0.40,
        change_magnitude: 0.22,
        observation_quality: 0.45,
      },
      explanation: {
        summary: 'Deterministic evidence score 0.385 (normal priority, low severity) for transient land-surface change.',
        positive_factors: [],
        limiting_factors: ['Limited physical change magnitude (mean ΔNDVI: 0.28)'],
        severity_rationale: "Classified as 'low' physical severity based on observed extent (1200 m², mean ΔNDVI 0.28).",
        priority_rationale: "Assigned 'normal' priority for triage.",
      },
    },
    created_at: '2024-03-16T00:00:00Z',
    updated_at: '2024-03-16T00:00:00Z',
  }

  const createMockEvidence = (candidate: Candidate): CandidateEvidence => ({
    candidate,
    temporal: {
      analysis_id: candidate.analysis_id,
      signal_id: candidate.signal_id,
      state: candidate.metrics.temporal_state,
      support_count: candidate.metrics.support_count,
      interval_count: candidate.metrics.interval_count,
      persistence_ratio: candidate.metrics.persistence_ratio,
      recurrence_count: candidate.metrics.recurrence_count,
      transient_interval_count: candidate.metrics.transient_interval_count,
      temporal_consistency: candidate.metrics.temporal_consistency,
      matched_region_coverage: candidate.metrics.matched_region_coverage,
      first_change_datetime: candidate.metrics.first_change_datetime,
      last_supporting_datetime: candidate.metrics.last_supporting_datetime,
      quality_support: candidate.metrics.quality_support,
    },
    intervals: [
      {
        detection_run_id: 201,
        before_acquisition_id: 10,
        after_acquisition_id: 11,
        detector_version: 'ndvi-diff-v1',
        threshold: 0.20,
        min_region_pixels: 4,
        changed_pixel_count: 84,
        total_changed_area_m2: candidate.metrics.total_area_m2 ?? 8400,
        region_count: 2,
        quality_mask_used: true,
        quality_processing_version: 'aoi-observation-quality-v1',
        quality_valid_pixel_count: 900,
        excluded_pixel_count: 25,
        excluded_cloud_pixel_count: 15,
        excluded_shadow_pixel_count: 10,
        raw_changed_pixel_count: 95,
        filtered_changed_pixel_count: 84,
        changed_pixel_percentage: 9.33,
        largest_region_area_m2: 6000,
        mean_region_area_m2: 4200,
        median_region_area_m2: 4200,
        mean_change_signal: candidate.metrics.mean_change_signal ?? 0.62,
        max_change_signal: candidate.metrics.max_change_signal ?? 0.78,
        regions: [],
      },
    ],
    acquisitions: [
      {
        acquisition_id: 10,
        aoi_id: 1,
        item_id: 'S2_10',
        collection: 'sentinel-2-l2a',
        acquisition_datetime: '2024-01-15T10:00:00Z',
        cloud_cover: 0.05,
        mgrs_tile: '43PFS',
        observation_state: 'usable',
        quality_reason: 'quality_policy_passed',
        usable_pixel_fraction: 0.95,
        cloud_fraction: 0.03,
        shadow_fraction: 0.02,
        invalid_fraction: 0.0,
        quality_processing_version: 'aoi-observation-quality-v1',
        masking_method: 'scl-quality-mask-v1',
        quality_mask_available: true,
        display_available: true,
        display_url: '/api/v1/imagery/acquisitions/10/display?aoi_id=1',
        visualization_version: 'sentinel-2-rgb-percentile-v2',
        raster: dummyRaster,
        bands: ['B04 red', 'B08 nir'],
        preview_url: `/api/v1/candidates/${candidate.candidate_id}/evidence/acquisitions/10/preview`,
      },
      {
        acquisition_id: 11,
        aoi_id: 1,
        item_id: 'S2_11',
        collection: 'sentinel-2-l2a',
        acquisition_datetime: '2024-02-15T10:00:00Z',
        cloud_cover: 0.08,
        mgrs_tile: '43PFS',
        observation_state: 'usable',
        quality_reason: 'quality_policy_passed',
        usable_pixel_fraction: 0.92,
        cloud_fraction: 0.05,
        shadow_fraction: 0.03,
        invalid_fraction: 0.0,
        quality_processing_version: 'aoi-observation-quality-v1',
        masking_method: 'scl-quality-mask-v1',
        quality_mask_available: true,
        display_available: true,
        display_url: '/api/v1/imagery/acquisitions/11/display?aoi_id=1',
        visualization_version: 'sentinel-2-rgb-percentile-v2',
        raster: dummyRaster,
        bands: ['B04 red', 'B08 nir'],
        preview_url: `/api/v1/candidates/${candidate.candidate_id}/evidence/acquisitions/11/preview`,
      },
    ],
    evidence_bounds: [77.4, 12.8, 77.45, 12.85],
    aoi_id: 1,
    is_spatially_aligned: true,
    spatial_alignment_details: {
      status: 'aligned',
      crs: 'EPSG:32643',
      width: 100,
      height: 100,
      pixel_size: 10,
      details: 'Fully aligned grid',
    },
    is_quality_limited: false,
    quality_limitation_reasons: [],
    provenance_chain: {
      candidate_id: candidate.candidate_id,
      analysis_id: candidate.analysis_id,
      signal_id: candidate.signal_id,
      aoi_id: 1,
      detection_run_ids: [201],
      acquisition_ids: [10, 11],
      before_acquisition_id: 10,
      after_acquisition_id: 11,
      detector_version: 'ndvi-diff-v1',
    },
  })

  let mockCandidates: Candidate[] = [candidate1, candidate2]
  let mockEvidenceMap: Record<string, CandidateEvidence> = {
    [candidate1.candidate_id]: createMockEvidence(candidate1),
    [candidate2.candidate_id]: createMockEvidence(candidate2),
  }
  let mockEvidenceErrorMap: Record<string, { status: number; code: string; message: string }> = {}

  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    sessionStorage.clear()
    mockCandidates = [candidate1, candidate2]
    mockEvidenceMap = {
      [candidate1.candidate_id]: createMockEvidence(candidate1),
      [candidate2.candidate_id]: createMockEvidence(candidate2),
    }
    mockEvidenceErrorMap = {}

    const acq1 = createMockAcquisition(10, '2024-01-15T10:00:00Z')
    const acq2 = createMockAcquisition(11, '2024-02-15T10:00:00Z')
    const acq3 = createMockAcquisition(12, '2024-03-15T10:00:00Z')

    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input)

      if (url.includes('/api/v1/health')) {
        return new Response(JSON.stringify({ status: 'healthy' }), { status: 200 })
      }
      if (url.includes('/api/v1/aois')) {
        return new Response(JSON.stringify([
          { aoi_id: 1, geometry: { type: 'Polygon', coordinates: [[[77.0, 12.0], [77.5, 12.0], [77.5, 12.5], [77.0, 12.5], [77.0, 12.0]]] }, created_at: '2024-01-01T00:00:00Z', updated_at: '2024-01-01T00:00:00Z' },
        ]), { status: 200 })
      }
      if (url.includes('/api/v1/aoi/1') || (url.includes('/api/v1/aoi') && !url.includes('/aois'))) {
        return new Response(JSON.stringify({
          aoi_id: 1,
          geometry: { type: 'Polygon', coordinates: [[[77.0, 12.0], [77.5, 12.0], [77.5, 12.5], [77.0, 12.5], [77.0, 12.0]]] },
          created_at: '2024-01-01T00:00:00Z',
          updated_at: '2024-01-01T00:00:00Z',
        }), { status: 200 })
      }
      if (url.includes('/api/v1/imagery/acquisitions')) {
        return new Response(JSON.stringify({ aoi_id: 1, total: 3, acquisitions: [acq1, acq2, acq3] }), { status: 200 })
      }
      if (url.includes('/api/v1/detections')) {
        return new Response(JSON.stringify({
          run_id: 201,
          before_acquisition_id: 10,
          after_acquisition_id: 11,
          before_item_id: 'S2_10',
          after_item_id: 'S2_11',
          detector_version: 'ndvi-diff-v1',
          threshold: 0.2,
          min_region_pixels: 4,
          created_at: '2024-01-01T00:00:00Z',
          region_count: 1,
          changed_pixel_count: 50,
          total_changed_area_m2: 5000,
          regions: [{ region_id: 1, geometry: { type: 'Polygon', coordinates: [] }, pixel_count: 50, area_m2: 5000, mean_change_signal: 0.5, max_change_signal: 0.7 }],
        }), { status: 200 })
      }
      if (url.includes('/api/v1/temporal-analyses')) {
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
          candidates: mockCandidates,
        }), { status: 200 })
      }
      if (url.includes('/api/v1/candidates/') && url.includes('/evidence')) {
        const match = url.match(/\/candidates\/([^/]+)\/evidence/)
        const cid = match ? decodeURIComponent(match[1]) : ''
        if (mockEvidenceErrorMap[cid]) {
          const err = mockEvidenceErrorMap[cid]
          return new Response(JSON.stringify({ code: err.code, message: err.message }), { status: err.status })
        }
        if (mockEvidenceMap[cid]) {
          return new Response(JSON.stringify(mockEvidenceMap[cid]), { status: 200 })
        }
        return new Response(JSON.stringify({ code: 'EvidenceUnavailableError', message: 'Evidence not found' }), { status: 404 })
      }
      if (url.includes('/api/v1/candidates/') && url.includes('/review')) {
        return new Response(JSON.stringify({
          candidate_id: 'analysis-42-signal-101',
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

    // Advance via in-stage buttons (passive rail)
    await waitFor(() => expect(screen.getByTestId('proceed-to-observations-btn')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('proceed-to-observations-btn'))
    await waitFor(() => expect(screen.getByTestId('proceed-to-changes-btn')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('proceed-to-changes-btn'))
    await waitFor(() => expect(screen.getByText('03 / Changes')).toBeInTheDocument())

    const runBtn = screen.getByTestId('run-automated-analysis-btn')
    fireEvent.click(runBtn)
    await waitFor(() => expect(screen.getByTestId('orchestration-summary')).toBeInTheDocument())

    // Navigate sequentially: Stage 03 -> Stage 04 -> Stage 05
    fireEvent.click(screen.getByTestId('proceed-to-history-btn'))
    await waitFor(() => expect(screen.getByText('04 / Change history')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('temporal-proceed-candidates-btn'))
    await waitFor(() => expect(screen.getByText('05 / Candidates')).toBeInTheDocument())
  }

  it('1. Stage 06 renders as "EVIDENCE" in workflow rail, panel kicker, and title', async () => {
    await navigateToCandidatesWorkflow()
    fireEvent.click(screen.getByRole('button', { name: 'Triage temporal signals' }))
    await waitFor(() => expect(screen.getByText('#1 / urgent priority')).toBeInTheDocument())
    fireEvent.click(screen.getByText('#1 / urgent priority'))
    fireEvent.click(screen.getByRole('button', { name: 'Inspect candidate evidence' }))

    await waitFor(() => {
      expect(screen.getByText('06 / Evidence')).toBeInTheDocument()
      expect(screen.getByRole('heading', { level: 2, name: 'EVIDENCE' })).toBeInTheDocument()
      expect(screen.getByLabelText(/06 EVIDENCE/i)).toBeInTheDocument()
    })
  })

  it('2. Analyst-oriented presentation and subtitle are rendered', async () => {
    await navigateToCandidatesWorkflow()
    fireEvent.click(screen.getByRole('button', { name: 'Triage temporal signals' }))
    await waitFor(() => expect(screen.getByText('#1 / urgent priority')).toBeInTheDocument())
    fireEvent.click(screen.getByText('#1 / urgent priority'))
    fireEvent.click(screen.getByRole('button', { name: 'Inspect candidate evidence' }))

    await waitFor(() => {
      expect(screen.getByText("Inspect the selected candidate's supporting satellite evidence, observations, and detected change.")).toBeInTheDocument()
    })
  })

  it('3. Selected candidate context is shown correctly with human-facing title', async () => {
    await navigateToCandidatesWorkflow()
    fireEvent.click(screen.getByRole('button', { name: 'Triage temporal signals' }))
    await waitFor(() => expect(screen.getByText('#1 / urgent priority')).toBeInTheDocument())
    fireEvent.click(screen.getByText('#1 / urgent priority'))
    fireEvent.click(screen.getByRole('button', { name: 'Inspect candidate evidence' }))

    await waitFor(() => {
      // Primary heading is human-facing, not raw technical ID
      expect(screen.getByRole('heading', { level: 4, name: 'Candidate #1 — Urgent priority' })).toBeInTheDocument()
      expect(screen.getByTestId('evidence-priority')).toHaveTextContent('urgent')
      expect(screen.getByTestId('evidence-severity')).toHaveTextContent('high')
      expect(screen.getByTestId('evidence-score')).toHaveTextContent('0.842')
      expect(screen.getByTestId('evidence-temporal-state')).toHaveTextContent('persistent')
    })
  })

  it('4. No candidate selected produces a clear prerequisite state and disables Next', async () => {
    await navigateToCandidatesWorkflow()
    const nextBtn = screen.getByTestId('proceed-to-evidence-btn')
    expect(nextBtn).toBeDisabled()
    expect(screen.getByText('Select a candidate to continue.')).toBeInTheDocument()
  })

  it('5. No automatic candidate selection occurs on entering Stage 05', async () => {
    await navigateToCandidatesWorkflow()
    fireEvent.click(screen.getByRole('button', { name: 'Triage temporal signals' }))
    await waitFor(() => expect(screen.getByText('#1 / urgent priority')).toBeInTheDocument())

    // No candidate auto-selected, Next button remains disabled
    const nextBtn = screen.getByTestId('proceed-to-evidence-btn')
    expect(nextBtn).toBeDisabled()
    expect(screen.getByTestId('map-selected-id')).toHaveTextContent('none')
    expect(screen.queryByRole('heading', { level: 4, name: /Candidate #/i })).not.toBeInTheDocument()
  })

  it('6. No candidates[0] hardcoding is used; second candidate can be selected', async () => {
    await navigateToCandidatesWorkflow()
    fireEvent.click(screen.getByRole('button', { name: 'Triage temporal signals' }))
    await waitFor(() => expect(screen.getByText('#2 / normal priority')).toBeInTheDocument())
    fireEvent.click(screen.getByText('#2 / normal priority'))
    fireEvent.click(screen.getByTestId('proceed-to-evidence-btn'))

    await waitFor(() => {
      expect(screen.getByRole('heading', { level: 4, name: 'Candidate #2 — Normal priority' })).toBeInTheDocument()
      expect(screen.getByTestId('evidence-score')).toHaveTextContent('0.385')
    })
  })

  it('7. Candidate identity comes from the authoritative selected candidate', async () => {
    await navigateToCandidatesWorkflow()
    fireEvent.click(screen.getByRole('button', { name: 'Triage temporal signals' }))
    await waitFor(() => expect(screen.getByText('#2 / normal priority')).toBeInTheDocument())
    fireEvent.click(screen.getByText('#2 / normal priority'))
    fireEvent.click(screen.getByTestId('proceed-to-evidence-btn'))

    await waitFor(() => {
      expect(screen.getByTestId('evidence-candidate-id')).toHaveTextContent('analysis-42-signal-102')
    })
  })

  it('8. Evidence remains associated with the selected candidate', async () => {
    await navigateToCandidatesWorkflow()
    fireEvent.click(screen.getByRole('button', { name: 'Triage temporal signals' }))
    await waitFor(() => expect(screen.getByText('#1 / urgent priority')).toBeInTheDocument())
    fireEvent.click(screen.getByText('#1 / urgent priority'))
    fireEvent.click(screen.getByTestId('proceed-to-evidence-btn'))

    await waitFor(() => {
      expect(screen.getByTestId('evidence-candidate-id')).toHaveTextContent('analysis-42-signal-101')
      expect(screen.getByTestId('evidence-score')).toHaveTextContent('0.842')
    })
  })

  it('9. Changing the selected candidate changes the evidence target', async () => {
    await navigateToCandidatesWorkflow()
    fireEvent.click(screen.getByRole('button', { name: 'Triage temporal signals' }))
    await waitFor(() => expect(screen.getByText('#1 / urgent priority')).toBeInTheDocument())
    fireEvent.click(screen.getByText('#1 / urgent priority'))
    fireEvent.click(screen.getByTestId('proceed-to-evidence-btn'))
    await waitFor(() => expect(screen.getByRole('heading', { level: 4, name: 'Candidate #1 — Urgent priority' })).toBeInTheDocument())

    // Return to Stage 05 via Previous button and select candidate 2
    fireEvent.click(screen.getByTestId('back-to-candidates-btn'))
    await waitFor(() => expect(screen.getByText('#2 / normal priority')).toBeInTheDocument())
    fireEvent.click(screen.getByText('#2 / normal priority'))
    fireEvent.click(screen.getByTestId('proceed-to-evidence-btn'))

    await waitFor(() => {
      expect(screen.getByRole('heading', { level: 4, name: 'Candidate #2 — Normal priority' })).toBeInTheDocument()
      expect(screen.getByTestId('evidence-candidate-id')).toHaveTextContent('analysis-42-signal-102')
    })
  })

  it('10. Previous candidate evidence does not remain visible under a newly selected candidate', async () => {
    await navigateToCandidatesWorkflow()
    fireEvent.click(screen.getByRole('button', { name: 'Triage temporal signals' }))
    await waitFor(() => expect(screen.getByText('#1 / urgent priority')).toBeInTheDocument())
    fireEvent.click(screen.getByText('#1 / urgent priority'))
    fireEvent.click(screen.getByRole('button', { name: 'Inspect candidate evidence' }))
    await waitFor(() => expect(screen.getByText('0.842')).toBeInTheDocument())

    // Switch candidate via mock map focus (candidate 2)
    fireEvent.click(screen.getByTestId('map-candidate-analysis-42-signal-102'))

    // The score for candidate 1 (0.842) should be replaced by candidate 2 (0.385)
    await waitFor(() => {
      expect(screen.getByTestId('evidence-score')).toHaveTextContent('0.385')
      expect(screen.queryByText('0.842')).not.toBeInTheDocument()
    })
  })

  it('11. BEFORE and AFTER are clearly distinguished', async () => {
    await navigateToCandidatesWorkflow()
    fireEvent.click(screen.getByRole('button', { name: 'Triage temporal signals' }))
    await waitFor(() => expect(screen.getByText('#1 / urgent priority')).toBeInTheDocument())
    fireEvent.click(screen.getByText('#1 / urgent priority'))
    fireEvent.click(screen.getByRole('button', { name: 'Inspect candidate evidence' }))

    await waitFor(() => {
      expect(screen.getByText('BEFORE')).toBeInTheDocument()
      expect(screen.getByText('AFTER')).toBeInTheDocument()
    })
  })

  it('12. Before/after dates are rendered from authoritative observation data', async () => {
    await navigateToCandidatesWorkflow()
    fireEvent.click(screen.getByRole('button', { name: 'Triage temporal signals' }))
    await waitFor(() => expect(screen.getByText('#1 / urgent priority')).toBeInTheDocument())
    fireEvent.click(screen.getByText('#1 / urgent priority'))
    fireEvent.click(screen.getByRole('button', { name: 'Inspect candidate evidence' }))

    await waitFor(() => {
      expect(screen.getByTestId('before-date')).toBeInTheDocument()
      expect(screen.getByTestId('after-date')).toBeInTheDocument()
      expect(screen.getByTestId('before-acquisition-id')).toHaveTextContent('Acquisition #10')
      expect(screen.getByTestId('after-acquisition-id')).toHaveTextContent('Acquisition #11')
    })
  })

  it('13. Chronological before/after relationship is preserved', async () => {
    await navigateToCandidatesWorkflow()
    fireEvent.click(screen.getByRole('button', { name: 'Triage temporal signals' }))
    await waitFor(() => expect(screen.getByText('#1 / urgent priority')).toBeInTheDocument())
    fireEvent.click(screen.getByText('#1 / urgent priority'))
    fireEvent.click(screen.getByRole('button', { name: 'Inspect candidate evidence' }))

    await waitFor(() => {
      const beforeCard = screen.getByTestId('before-observation-card')
      const afterCard = screen.getByTestId('after-observation-card')
      expect(beforeCard).toHaveTextContent('BEFORE')
      expect(afterCard).toHaveTextContent('AFTER')
    })
  })

  it('14. Detected-change evidence is clearly distinguished from source imagery', async () => {
    await navigateToCandidatesWorkflow()
    fireEvent.click(screen.getByRole('button', { name: 'Triage temporal signals' }))
    await waitFor(() => expect(screen.getByText('#1 / urgent priority')).toBeInTheDocument())
    fireEvent.click(screen.getByText('#1 / urgent priority'))
    fireEvent.click(screen.getByRole('button', { name: 'Inspect candidate evidence' }))

    await waitFor(() => {
      expect(screen.getByText('Detected Change Evidence')).toBeInTheDocument()
      expect(screen.getByText(/Detected change represents the derived land-surface change signal \(ΔNDVI\), not a satellite observation\./i)).toBeInTheDocument()
    })
  })

  it('15. Existing evidence assets render when available', async () => {
    await navigateToCandidatesWorkflow()
    fireEvent.click(screen.getByRole('button', { name: 'Triage temporal signals' }))
    await waitFor(() => expect(screen.getByText('#1 / urgent priority')).toBeInTheDocument())
    fireEvent.click(screen.getByText('#1 / urgent priority'))
    fireEvent.click(screen.getByRole('button', { name: 'Inspect candidate evidence' }))

    await waitFor(() => {
      const images = screen.getAllByRole('img')
      expect(images.length).toBeGreaterThanOrEqual(2)
      expect(images[0]).toHaveAttribute('alt', 'Before Sentinel-2 RGB imagery')
      expect(images[1]).toHaveAttribute('alt', 'After Sentinel-2 RGB imagery')
    })
  })

  it('16. Missing/unavailable evidence is represented honestly', async () => {
    const evidenceWithoutDisplay = createMockEvidence(candidate1)
    evidenceWithoutDisplay.acquisitions[0].display_available = false
    evidenceWithoutDisplay.acquisitions[0].display_url = null
    mockEvidenceMap[candidate1.candidate_id] = evidenceWithoutDisplay

    await navigateToCandidatesWorkflow()
    fireEvent.click(screen.getByRole('button', { name: 'Triage temporal signals' }))
    await waitFor(() => expect(screen.getByText('#1 / urgent priority')).toBeInTheDocument())
    fireEvent.click(screen.getByText('#1 / urgent priority'))
    fireEvent.click(screen.getByRole('button', { name: 'Inspect candidate evidence' }))

    await waitFor(() => {
      expect(screen.getByText('Satellite RGB observation unavailable')).toBeInTheDocument()
    })
  })

  it('17. Evidence-generation/retrieval failure is distinct from normal unavailable state', async () => {
    mockEvidenceErrorMap[candidate1.candidate_id] = {
      status: 404,
      code: 'EvidenceUnavailableError',
      message: 'Evidence raster is missing from disk',
    }

    await navigateToCandidatesWorkflow()
    fireEvent.click(screen.getByRole('button', { name: 'Triage temporal signals' }))
    await waitFor(() => expect(screen.getByText('#1 / urgent priority')).toBeInTheDocument())
    fireEvent.click(screen.getByText('#1 / urgent priority'))
    fireEvent.click(screen.getByRole('button', { name: 'Inspect candidate evidence' }))

    await waitFor(() => {
      expect(screen.getByTestId('evidence-workstation-state')).toHaveTextContent('UNAVAILABLE')
      expect(screen.getByText('Evidence Unavailable')).toBeInTheDocument()
      expect(screen.getAllByText('Evidence raster is missing from disk')[0]).toBeInTheDocument()
    })
  })

  it('18. Actual backend error state uses appropriate alert semantics', async () => {
    mockEvidenceErrorMap[candidate1.candidate_id] = {
      status: 500,
      code: 'CandidateEvidenceError',
      message: 'Evidence calculation failed due to corrupted raster',
    }

    await navigateToCandidatesWorkflow()
    fireEvent.click(screen.getByRole('button', { name: 'Triage temporal signals' }))
    await waitFor(() => expect(screen.getByText('#1 / urgent priority')).toBeInTheDocument())
    fireEvent.click(screen.getByText('#1 / urgent priority'))
    fireEvent.click(screen.getByRole('button', { name: 'Inspect candidate evidence' }))

    await waitFor(() => {
      expect(screen.getByTestId('evidence-workstation-state')).toHaveTextContent('FAILED')
      const alerts = screen.getAllByRole('alert')
      const failureAlert = alerts.find((el) => el.textContent?.includes('Evidence Retrieval Failed'))
      expect(failureAlert).toBeDefined()
      expect(failureAlert).toHaveTextContent('Evidence calculation failed due to corrupted raster')
    })
  })

  it('19. Existing quality-support information renders correctly when supplied', async () => {
    await navigateToCandidatesWorkflow()
    fireEvent.click(screen.getByRole('button', { name: 'Triage temporal signals' }))
    await waitFor(() => expect(screen.getByText('#1 / urgent priority')).toBeInTheDocument())
    fireEvent.click(screen.getByText('#1 / urgent priority'))
    fireEvent.click(screen.getByRole('button', { name: 'Inspect candidate evidence' }))

    await waitFor(() => {
      const qualitySection = screen.getByTestId('observation-quality-section')
      expect(qualitySection).toHaveTextContent('95.0%') // Before usable
      expect(qualitySection).toHaveTextContent('92.0%') // After usable
      expect(qualitySection).toHaveTextContent('scl-quality-mask-v1')
      expect(qualitySection).toHaveTextContent('Available')
    })
  })

  it('20. Quality support is not labeled confidence/probability/certainty/likelihood', async () => {
    await navigateToCandidatesWorkflow()
    fireEvent.click(screen.getByRole('button', { name: 'Triage temporal signals' }))
    await waitFor(() => expect(screen.getByText('#1 / urgent priority')).toBeInTheDocument())
    fireEvent.click(screen.getByText('#1 / urgent priority'))
    fireEvent.click(screen.getByRole('button', { name: 'Inspect candidate evidence' }))

    await waitFor(() => {
      const content = screen.getByTestId('scientific-metrics-section')
      expect(content).not.toHaveTextContent(/confidence/i)
      expect(content).not.toHaveTextContent(/probability/i)
      expect(content).not.toHaveTextContent(/certainty/i)
      expect(content).not.toHaveTextContent(/likelihood/i)
    })
  })

  it('21. Existing geographic/candidate focus behavior remains correct', async () => {
    await navigateToCandidatesWorkflow()
    fireEvent.click(screen.getByRole('button', { name: 'Triage temporal signals' }))
    await waitFor(() => expect(screen.getByText('#1 / urgent priority')).toBeInTheDocument())
    fireEvent.click(screen.getByText('#1 / urgent priority'))
    fireEvent.click(screen.getByRole('button', { name: 'Inspect candidate evidence' }))

    await waitFor(() => {
      expect(screen.getByTestId('map-selected-id')).toHaveTextContent('map-selected:analysis-42-signal-101')
      expect(screen.getByTestId('evidence-spatial-alignment')).toHaveTextContent('Spatial Grid Aligned')
    })
  })

  it('22. Technical provenance is available without exposing internal paths as primary content', async () => {
    await navigateToCandidatesWorkflow()
    fireEvent.click(screen.getByRole('button', { name: 'Triage temporal signals' }))
    await waitFor(() => expect(screen.getByText('#1 / urgent priority')).toBeInTheDocument())
    fireEvent.click(screen.getByText('#1 / urgent priority'))
    fireEvent.click(screen.getByRole('button', { name: 'Inspect candidate evidence' }))

    await waitFor(() => {
      const provSection = screen.getByTestId('provenance-trace-section')
      expect(provSection).toHaveTextContent('analysis-42-signal-101')
      expect(provSection).toHaveTextContent('Run #201')
      expect(provSection).toHaveTextContent('#1 (Karnataka-contained)')
      expect(provSection).toHaveTextContent('Detector: ndvi-diff-v1')
      // No internal filesystem paths as primary content
      expect(provSection).not.toHaveTextContent('/path/to/raster.tif')
    })
  })

  it('23. Optional provenance fields do not crash the UI', async () => {
    const minimalEvidence = createMockEvidence(candidate1)
    minimalEvidence.spatial_alignment_details = null
    minimalEvidence.is_spatially_aligned = false
    mockEvidenceMap[candidate1.candidate_id] = minimalEvidence

    await navigateToCandidatesWorkflow()
    fireEvent.click(screen.getByRole('button', { name: 'Triage temporal signals' }))
    await waitFor(() => expect(screen.getByText('#1 / urgent priority')).toBeInTheDocument())
    fireEvent.click(screen.getByText('#1 / urgent priority'))
    fireEvent.click(screen.getByRole('button', { name: 'Inspect candidate evidence' }))

    await waitFor(() => {
      expect(screen.getByTestId('candidate-identity-section')).toBeInTheDocument()
      expect(screen.getByTestId('evidence-spatial-alignment')).toHaveTextContent('Spatial Grid Limitation')
    })
  })

  it('24. Upstream AOI changes clear stale evidence', async () => {
    await navigateToCandidatesWorkflow()
    fireEvent.click(screen.getByRole('button', { name: 'Triage temporal signals' }))
    await waitFor(() => expect(screen.getByText('#1 / urgent priority')).toBeInTheDocument())
    fireEvent.click(screen.getByText('#1 / urgent priority'))
    fireEvent.click(screen.getByRole('button', { name: 'Inspect candidate evidence' }))
    await waitFor(() => expect(screen.getByRole('heading', { level: 4, name: 'Candidate #1 — Urgent priority' })).toBeInTheDocument())

    // Go to Stage 01 via sequential Previous navigation
    fireEvent.click(screen.getByTestId('back-to-candidates-btn'))
    await waitFor(() => expect(screen.getByText('05 / Candidates')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('back-to-history-btn'))
    await waitFor(() => expect(screen.getByText('04 / Change history')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('back-to-changes-btn'))
    await waitFor(() => expect(screen.getByText('03 / Changes')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('back-to-observations-btn'))
    await waitFor(() => expect(screen.getByText('02 / Observations')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('back-to-area-btn'))
    await waitFor(() => expect(screen.getByText('01 / Area')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: 'Draw area' }))

    // Evidence should be cleared and Stage 06 locked
    expect(screen.getByLabelText(/06.*EVIDENCE/i)).toHaveClass('workflow-step--locked')
  })

  it('25. Upstream imagery changes clear stale evidence', async () => {
    await navigateToCandidatesWorkflow()
    fireEvent.click(screen.getByRole('button', { name: 'Triage temporal signals' }))
    await waitFor(() => expect(screen.getByText('#1 / urgent priority')).toBeInTheDocument())
    fireEvent.click(screen.getByText('#1 / urgent priority'))
    fireEvent.click(screen.getByRole('button', { name: 'Inspect candidate evidence' }))
    await waitFor(() => expect(screen.getByRole('heading', { level: 4, name: 'Candidate #1 — Urgent priority' })).toBeInTheDocument())

    // Go to Stage 02 via sequential Previous navigation
    fireEvent.click(screen.getByTestId('back-to-candidates-btn'))
    await waitFor(() => expect(screen.getByText('05 / Candidates')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('back-to-history-btn'))
    await waitFor(() => expect(screen.getByText('04 / Change history')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('back-to-changes-btn'))
    await waitFor(() => expect(screen.getByText('03 / Changes')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('back-to-observations-btn'))
    await waitFor(() => expect(screen.getByText('02 / Observations')).toBeInTheDocument())

    // Change date and acquire
    fireEvent.change(screen.getByLabelText('Observation start date'), { target: { value: '2024-01-01' } })
    fireEvent.change(screen.getByLabelText('Observation end date'), { target: { value: '2024-03-31' } })
    fireEvent.click(screen.getByRole('button', { name: 'Find observations' }))

    // Evidence should be cleared and Stage 06 locked
    await waitFor(() => {
      expect(screen.getByLabelText(/06.*EVIDENCE/i)).toHaveClass('workflow-step--locked')
    })
  })

  it('26. Upstream detection changes clear stale evidence', async () => {
    await navigateToCandidatesWorkflow()
    fireEvent.click(screen.getByRole('button', { name: 'Triage temporal signals' }))
    await waitFor(() => expect(screen.getByText('#1 / urgent priority')).toBeInTheDocument())
    fireEvent.click(screen.getByText('#1 / urgent priority'))
    fireEvent.click(screen.getByRole('button', { name: 'Inspect candidate evidence' }))
    await waitFor(() => expect(screen.getByRole('heading', { level: 4, name: 'Candidate #1 — Urgent priority' })).toBeInTheDocument())

    // Go to Stage 03 via sequential Previous navigation and re-run analysis
    fireEvent.click(screen.getByTestId('back-to-candidates-btn'))
    await waitFor(() => expect(screen.getByText('05 / Candidates')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('back-to-history-btn'))
    await waitFor(() => expect(screen.getByText('04 / Change history')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('back-to-changes-btn'))
    await waitFor(() => expect(screen.getByText('03 / Changes')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('run-automated-analysis-btn'))

    // Downstream selected candidate and evidence are cleared
    await waitFor(() => {
      expect(screen.queryByRole('heading', { level: 4, name: 'Candidate #1 — Urgent priority' })).not.toBeInTheDocument()
    })
  })

  it('27. Upstream temporal changes clear stale evidence', async () => {
    await navigateToCandidatesWorkflow()
    fireEvent.click(screen.getByRole('button', { name: 'Triage temporal signals' }))
    await waitFor(() => expect(screen.getByText('#1 / urgent priority')).toBeInTheDocument())
    fireEvent.click(screen.getByText('#1 / urgent priority'))
    fireEvent.click(screen.getByRole('button', { name: 'Inspect candidate evidence' }))
    await waitFor(() => expect(screen.getByRole('heading', { level: 4, name: 'Candidate #1 — Urgent priority' })).toBeInTheDocument())

    // Go to Stage 01 via sequential Previous navigation and start drawing new area
    fireEvent.click(screen.getByTestId('back-to-candidates-btn'))
    await waitFor(() => expect(screen.getByText('05 / Candidates')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('back-to-history-btn'))
    await waitFor(() => expect(screen.getByText('04 / Change history')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('back-to-changes-btn'))
    await waitFor(() => expect(screen.getByText('03 / Changes')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('back-to-observations-btn'))
    await waitFor(() => expect(screen.getByText('02 / Observations')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('back-to-area-btn'))
    await waitFor(() => expect(screen.getByText('01 / Area')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: 'Draw area' }))

    // Stage 06 locked
    expect(screen.getByLabelText(/06.*EVIDENCE/i)).toHaveClass('workflow-step--locked')
  })

  it('28. Upstream candidate-analysis changes clear stale evidence', async () => {
    await navigateToCandidatesWorkflow()
    fireEvent.click(screen.getByRole('button', { name: 'Triage temporal signals' }))
    await waitFor(() => expect(screen.getByText('#1 / urgent priority')).toBeInTheDocument())
    fireEvent.click(screen.getByText('#1 / urgent priority'))
    fireEvent.click(screen.getByRole('button', { name: 'Inspect candidate evidence' }))
    await waitFor(() => expect(screen.getByRole('heading', { level: 4, name: 'Candidate #1 — Urgent priority' })).toBeInTheDocument())

    // Go to Stage 05 via Previous button and re-triage
    fireEvent.click(screen.getByTestId('back-to-candidates-btn'))
    await waitFor(() => expect(screen.getByText('05 / Candidates')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: 'Triage temporal signals' }))

    // Stage 06 evidence is cleared
    await waitFor(() => {
      expect(screen.getByTestId('map-selected-id')).toHaveTextContent('none')
    })
  })

  it('29. Returning from Stage 05 with a different candidate causes Stage 06 to follow the new candidate', async () => {
    await navigateToCandidatesWorkflow()
    fireEvent.click(screen.getByRole('button', { name: 'Triage temporal signals' }))
    await waitFor(() => expect(screen.getByText('#1 / urgent priority')).toBeInTheDocument())
    fireEvent.click(screen.getByText('#1 / urgent priority'))
    fireEvent.click(screen.getByRole('button', { name: 'Inspect candidate evidence' }))
    await waitFor(() => expect(screen.getByRole('heading', { level: 4, name: 'Candidate #1 — Urgent priority' })).toBeInTheDocument())

    // Return to Stage 05 via Previous button
    fireEvent.click(screen.getByTestId('back-to-candidates-btn'))
    await waitFor(() => expect(screen.getByText('#2 / normal priority')).toBeInTheDocument())

    // Select candidate 2
    fireEvent.click(screen.getByText('#2 / normal priority'))

    // Go to Stage 06 via Next button
    fireEvent.click(screen.getByTestId('proceed-to-evidence-btn'))

    // Stage 06 displays Candidate #2
    await waitFor(() => {
      expect(screen.getByRole('heading', { level: 4, name: 'Candidate #2 — Normal priority' })).toBeInTheDocument()
      expect(screen.getByTestId('evidence-candidate-id')).toHaveTextContent('analysis-42-signal-102')
    })
  })
})
