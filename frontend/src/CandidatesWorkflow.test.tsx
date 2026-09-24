import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import type { Candidate, ImageryAcquisition, TemporalResult } from './lib/api'

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

describe('Stage 05 / CANDIDATES Simplification (Phase 7)', () => {
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

  let mockCandidates: Candidate[] = []
  let mockTriageStatus: number = 200
  let mockTriageError: { code: string; message: string } | null = null
  let mockTemporalResult: TemporalResult | null = null

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
    detection_run_ids: [201, 202],
    acquisition_ids: [10, 11, 12],
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
        positive_factors: [
          'Strong temporal persistence (1.00)',
          'High spatial tracking consistency across intervals (0.92)',
          'Significant physical change magnitude (mean ΔNDVI: 0.62, area: 8400 m²)',
          'High observation quality support (0.91)',
        ],
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
        limiting_factors: [
          'Low spatial tracking overlap across intervals (0.40)',
          'Limited physical change magnitude (mean ΔNDVI: 0.28)',
        ],
        severity_rationale: "Classified as 'low' physical severity based on observed extent (1200 m², mean ΔNDVI 0.28).",
        priority_rationale: "Assigned 'normal' priority for triage.",
      },
    },
    created_at: '2024-03-16T00:00:00Z',
    updated_at: '2024-03-16T00:00:00Z',
  }

  beforeEach(() => {
    mockCandidates = [candidate1, candidate2]
    mockTriageStatus = 200
    mockTriageError = null

    mockTemporalResult = {
      analysis_id: 42,
      state: 'persistent',
      observation_count: 2,
      detector_run_count: 1,
      iou_threshold: 0.25,
      temporal_span_days: 31,
      quality_support: 0.88,
      created_at: '2024-02-16T12:00:00Z',
      observations: [
        { acquisition_id: 10, item_id: 'S2_10', acquisition_datetime: '2024-01-15T10:00:00Z', sequence_index: 0 },
        { acquisition_id: 11, item_id: 'S2_11', acquisition_datetime: '2024-02-15T10:00:00Z', sequence_index: 1 },
      ],
      relationships: [
        { detection_run_id: 201, before_acquisition_id: 10, after_acquisition_id: 11, region_count: 2, changed_pixel_count: 50 },
      ],
      signals: [
        {
          signal_id: 101,
          geometry: { type: 'Polygon', coordinates: [[[77.4, 12.8], [77.45, 12.8], [77.45, 12.85], [77.4, 12.85], [77.4, 12.8]]] },
          state: 'persistent',
          support_count: 2,
          interval_count: 2,
          persistence_ratio: 1.0,
          recurrence_count: 0,
          transient_interval_count: 0,
          temporal_consistency: 0.92,
          matched_region_coverage: 0.88,
          first_change_datetime: '2024-01-15T10:00:00Z',
          last_supporting_datetime: '2024-03-15T10:00:00Z',
        },
      ],
    }

    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input)
      if (url.includes('/api/v1/health')) {
        return new Response(JSON.stringify({ status: 'ok', app: 'TerraWatch V2', version: '0.1.0' }), { status: 200 })
      }
      if (url.endsWith('/api/v1/aois')) {
        return new Response(
          JSON.stringify([
            {
              aoi_id: 1,
              geometry: { type: 'Polygon', coordinates: [[[77.4, 12.8], [77.6, 12.8], [77.6, 13.0], [77.4, 13.0], [77.4, 12.8]]] },
              created_at: '2024-01-01T00:00:00Z',
              updated_at: '2024-01-01T00:00:00Z',
            },
          ]),
          { status: 200 }
        )
      }
      if (url.includes('/api/v1/aoi/1') || url.endsWith('/api/v1/aoi')) {
        return new Response(
          JSON.stringify({
            aoi_id: 1,
            geometry: { type: 'Polygon', coordinates: [[[77.4, 12.8], [77.6, 12.8], [77.6, 13.0], [77.4, 13.0], [77.4, 12.8]]] },
            created_at: '2024-01-01T00:00:00Z',
            updated_at: '2024-01-01T00:00:00Z',
          }),
          { status: 200 }
        )
      }
      if (url.includes('/api/v1/imagery/acquisitions')) {
        return new Response(
          JSON.stringify({
            acquisitions: [
              createMockAcquisition(10, '2024-01-15T10:00:00Z'),
              createMockAcquisition(11, '2024-02-15T10:00:00Z'),
            ],
          }),
          { status: 200 }
        )
      }
      if (url.includes('/api/v1/detections')) {
        return new Response(
          JSON.stringify({
            run_id: 201,
            aoi_id: 1,
            before_acquisition_id: 10,
            after_acquisition_id: 11,
            detector_version: 'diff-v1',
            threshold: 0.18,
            min_region_pixels: 5,
            region_count: 2,
            total_changed_area_m2: 8400,
            changed_pixel_count: 84,
            created_at: '2024-02-16T00:00:00Z',
            regions: [],
          }),
          { status: 200 }
        )
      }
      if (url.includes('/api/v1/orchestrations/analyze')) {
        return new Response(
          JSON.stringify({
            aoi_id: 1,
            eligible_observation_ids: [10, 11, 12],
            reused_detection_count: 0,
            generated_detection_count: 2,
            reused_analysis: false,
            analysis: mockTemporalResult,
            candidates: {
              analysis_id: 42,
              candidates: [],
            },
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        )
      }
      if (url.includes('/api/v1/temporal/run')) {
        return new Response(JSON.stringify(mockTemporalResult), { status: 200 })
      }
      if (url.includes('/api/v1/candidates/triage')) {
        if (mockTriageStatus !== 200) {
          return new Response(JSON.stringify(mockTriageError ?? { message: 'Triage failed' }), { status: mockTriageStatus })
        }
        return new Response(
          JSON.stringify({
            analysis_id: 42,
            candidates: mockCandidates,
          }),
          { status: 200 }
        )
      }
      if (url.includes('/evidence')) {
        return new Response(
          JSON.stringify({
            aoi_id: 1,
            candidate: candidate1,
            intervals: [],
            acquisitions: [],
            is_quality_limited: false,
          }),
          { status: 200 }
        )
      }
      if (url.includes('/review')) {
        return new Response(
          JSON.stringify({
            candidate_id: 'analysis-42-signal-101',
            decision: 'unreviewed',
            note: null,
            created_at: null,
            updated_at: null,
          }),
          { status: 200 }
        )
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

    // Navigate to Stage 05 CANDIDATES via in-stage button
    fireEvent.click(screen.getByTestId('proceed-to-candidates-btn'))
    await waitFor(() => expect(screen.getByText('05 / Candidates')).toBeInTheDocument())
  }

  it('1. Stage 05 renders as "CANDIDATES"', async () => {
    await navigateToCandidatesWorkflow()
    expect(screen.getByRole('heading', { level: 2, name: 'Review candidates' })).toBeInTheDocument()
    expect(screen.getByText('CANDIDATES', { selector: '.panel-index' })).toBeInTheDocument()
    expect(screen.getByText('05 / Candidates')).toBeInTheDocument()
  })

  it('2. Analyst-oriented subtitle/presentation is rendered', async () => {
    await navigateToCandidatesWorkflow()
    expect(
      screen.getByText('Review detected changes and select a candidate for investigation.')
    ).toBeInTheDocument()
  })

  it('3. Candidate results render in the authoritative backend order', async () => {
    await navigateToCandidatesWorkflow()
    fireEvent.click(screen.getByRole('button', { name: 'Triage temporal signals' }))

    await waitFor(() => expect(screen.getByText('#1 / urgent priority')).toBeInTheDocument())
    const buttons = screen.getAllByRole('button', { name: /priority/i })
    expect(buttons[0]).toHaveTextContent('#1 / urgent priority')
    expect(buttons[1]).toHaveTextContent('#2 / normal priority')
  })

  it('4. Candidate identity uses the existing authoritative identifier', async () => {
    await navigateToCandidatesWorkflow()
    fireEvent.click(screen.getByRole('button', { name: 'Triage temporal signals' }))
    await waitFor(() => expect(screen.getByText('#1 / urgent priority')).toBeInTheDocument())

    // Select candidate 1
    fireEvent.click(screen.getByText('#1 / urgent priority'))
    expect(screen.getByText('analysis-42-signal-101')).toBeInTheDocument()
  })

  it('5. Candidate identity is not derived from array position', async () => {
    // If backend returns candidate2 first (e.g. rank 1), its authoritative candidate_id is displayed
    const reversedCandidate = { ...candidate2, rank: 1, priority: 'urgent' as const }
    mockCandidates = [reversedCandidate]
    await navigateToCandidatesWorkflow()
    fireEvent.click(screen.getByRole('button', { name: 'Triage temporal signals' }))

    await waitFor(() => expect(screen.getByText('#1 / urgent priority')).toBeInTheDocument())
    fireEvent.click(screen.getByText('#1 / urgent priority'))
    expect(screen.getByText('analysis-42-signal-102')).toBeInTheDocument()
  })

  it('6. No candidates[0] hardcoding is used for active selection/display', async () => {
    await navigateToCandidatesWorkflow()
    fireEvent.click(screen.getByRole('button', { name: 'Triage temporal signals' }))

    await waitFor(() => expect(screen.getByText('#1 / urgent priority')).toBeInTheDocument())
    // Neither candidate should be auto-selected
    expect(screen.queryByText('Candidate Assessment')).not.toBeInTheDocument()
    expect(screen.getByText(/Select a candidate from the list above/i)).toBeInTheDocument()
  })

  it('7. Selecting a candidate establishes the selected candidate correctly', async () => {
    await navigateToCandidatesWorkflow()
    fireEvent.click(screen.getByRole('button', { name: 'Triage temporal signals' }))
    await waitFor(() => expect(screen.getByText('#1 / urgent priority')).toBeInTheDocument())

    fireEvent.click(screen.getByText('#1 / urgent priority'))
    expect(screen.getByText('Candidate Assessment')).toBeInTheDocument()
    expect(screen.getByText('analysis-42-signal-101')).toBeInTheDocument()
    expect(screen.getByTestId('map-selected-id')).toHaveTextContent('analysis-42-signal-101')
  })

  it('8. Switching candidates updates candidate-specific information', async () => {
    await navigateToCandidatesWorkflow()
    fireEvent.click(screen.getByRole('button', { name: 'Triage temporal signals' }))
    await waitFor(() => expect(screen.getByText('#1 / urgent priority')).toBeInTheDocument())

    // Select candidate 1
    fireEvent.click(screen.getByText('#1 / urgent priority'))
    expect(screen.getByText('analysis-42-signal-101')).toBeInTheDocument()
    expect(screen.getByText('0.842')).toBeInTheDocument()

    // Switch to candidate 2
    fireEvent.click(screen.getByText('#2 / normal priority'))
    expect(screen.getByText('analysis-42-signal-102')).toBeInTheDocument()
    expect(screen.getByText('0.385')).toBeInTheDocument()
    expect(screen.getByTestId('map-selected-id')).toHaveTextContent('analysis-42-signal-102')
  })

  it('9. Analysis-level information is not incorrectly mutated when candidate selection changes', async () => {
    await navigateToCandidatesWorkflow()
    fireEvent.click(screen.getByRole('button', { name: 'Triage temporal signals' }))
    await waitFor(() => expect(screen.getByText('#1 / urgent priority')).toBeInTheDocument())

    fireEvent.click(screen.getByText('#1 / urgent priority'))
    expect(screen.getByText('Requires: ✓ Change history completed')).toBeInTheDocument()

    fireEvent.click(screen.getByText('#2 / normal priority'))
    // Upstream requirement status remains intact
    expect(screen.getByText('Requires: ✓ Change history completed')).toBeInTheDocument()
  })

  it('10. Existing score values render without frontend recalculation', async () => {
    await navigateToCandidatesWorkflow()
    fireEvent.click(screen.getByRole('button', { name: 'Triage temporal signals' }))
    await waitFor(() => expect(screen.getByText(/evidence score: 0\.842/i)).toBeInTheDocument())
    expect(screen.getByText(/evidence score: 0\.385/i)).toBeInTheDocument()
  })

  it('11. Score is not presented as probability/confidence or redefined as evidence strength', async () => {
    await navigateToCandidatesWorkflow()
    fireEvent.click(screen.getByRole('button', { name: 'Triage temporal signals' }))
    await waitFor(() => expect(screen.getByText('#1 / urgent priority')).toBeInTheDocument())
    fireEvent.click(screen.getByText('#1 / urgent priority'))

    // Should render "Evidence Score", never probability, confidence, certainty, or likelihood
    expect(screen.getAllByText(/Evidence Score/i).length).toBeGreaterThan(0)
    expect(screen.queryByText(/calibrated confidence/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/probability/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/certainty/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/likelihood/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/evidence strength/i)).not.toBeInTheDocument()
  })

  it('12. Severity and priority remain distinct', async () => {
    await navigateToCandidatesWorkflow()
    fireEvent.click(screen.getByRole('button', { name: 'Triage temporal signals' }))
    await waitFor(() => expect(screen.getByText('#1 / urgent priority')).toBeInTheDocument())
    fireEvent.click(screen.getByText('#1 / urgent priority'))

    // Priority = urgent, Severity = high
    expect(screen.getByText('Triage Priority')).toBeInTheDocument()
    expect(screen.getByText('Physical Severity')).toBeInTheDocument()
  })

  it('13. Existing severity values render correctly', async () => {
    await navigateToCandidatesWorkflow()
    fireEvent.click(screen.getByRole('button', { name: 'Triage temporal signals' }))
    await waitFor(() => expect(screen.getByText(/high severity/i)).toBeInTheDocument())
    expect(screen.getByText(/low severity/i)).toBeInTheDocument()
  })

  it('14. Existing priority values render correctly', async () => {
    await navigateToCandidatesWorkflow()
    fireEvent.click(screen.getByRole('button', { name: 'Triage temporal signals' }))
    await waitFor(() => expect(screen.getByText(/urgent priority/i)).toBeInTheDocument())
    expect(screen.getByText(/normal priority/i)).toBeInTheDocument()
  })

  it('15. Existing explainability information renders correctly', async () => {
    await navigateToCandidatesWorkflow()
    fireEvent.click(screen.getByRole('button', { name: 'Triage temporal signals' }))
    await waitFor(() => expect(screen.getByText('#1 / urgent priority')).toBeInTheDocument())
    fireEvent.click(screen.getByText('#1 / urgent priority'))

    expect(screen.getByText(/Deterministic evidence score 0\.842/)).toBeInTheDocument()
    expect(screen.getByText('Strong temporal persistence (1.00)')).toBeInTheDocument()
    expect(screen.getByText('High observation quality support (0.91)')).toBeInTheDocument()
    expect(screen.getByText(/Classified as 'high' physical severity/)).toBeInTheDocument()
    expect(screen.getByText(/Assigned 'urgent' priority for triage/)).toBeInTheDocument()
  })

  it('16. Temporal state/onset/quality context renders when supplied', async () => {
    await navigateToCandidatesWorkflow()
    fireEvent.click(screen.getByRole('button', { name: 'Triage temporal signals' }))
    await waitFor(() => expect(screen.getByText('#1 / urgent priority')).toBeInTheDocument())
    fireEvent.click(screen.getByText('#1 / urgent priority'))

    expect(screen.getByText('persistent')).toBeInTheDocument()
    expect(screen.getByText('91%')).toBeInTheDocument()
    expect(screen.getByText('8400 m²')).toBeInTheDocument()
    expect(screen.getByText('0.62')).toBeInTheDocument() // mean ΔNDVI
  })

  it('17. Missing optional candidate fields do not crash the UI', async () => {
    const minimalCandidate: Candidate = {
      candidate_id: 'minimal-candidate',
      analysis_id: 1,
      signal_id: 1,
      geometry: { type: 'Polygon', coordinates: [[[0, 0], [1, 0], [1, 1], [0, 0]]] },
      score: 0.5000,
      rank: 1,
      severity: 'medium',
      priority: 'normal',
      review_state: 'unreviewed',
      source_signal_ids: [],
      detection_run_ids: [],
      acquisition_ids: [],
      metrics: {
        support_count: 1,
        interval_count: 1,
        persistence_ratio: 0.5,
        recurrence_count: 0,
        transient_interval_count: 0,
        temporal_consistency: 0.5,
        matched_region_coverage: 0.5,
        first_change_datetime: '',
        last_supporting_datetime: '',
        temporal_state: 'isolated',
        score_components: {},
      },
      created_at: '2024-01-01T00:00:00Z',
      updated_at: '2024-01-01T00:00:00Z',
    }
    mockCandidates = [minimalCandidate]
    await navigateToCandidatesWorkflow()
    fireEvent.click(screen.getByRole('button', { name: 'Triage temporal signals' }))

    await waitFor(() => expect(screen.getByText('#1 / normal priority')).toBeInTheDocument())
    fireEvent.click(screen.getByText('#1 / normal priority'))
    expect(screen.getByText('Candidate Assessment')).toBeInTheDocument()
    expect(screen.getByText('isolated')).toBeInTheDocument()
  })

  it('18. Zero-candidate successful results show a neutral completed-analysis state', async () => {
    mockCandidates = []
    await navigateToCandidatesWorkflow()
    fireEvent.click(screen.getByRole('button', { name: 'Triage temporal signals' }))

    await waitFor(() =>
      expect(
        screen.getByText('Analysis completed. No qualifying candidate changes were detected across the evaluated history.')
      ).toBeInTheDocument()
    )
  })

  it('19. Insufficient temporal history is not incorrectly presented as an ordinary zero-candidate result', async () => {
    mockTriageStatus = 400
    mockTriageError = { code: 'CandidateAnalysisUnavailableError', message: 'The requested temporal analysis has insufficient history' }
    await navigateToCandidatesWorkflow()
    fireEvent.click(screen.getByRole('button', { name: 'Triage temporal signals' }))

    await waitFor(() =>
      expect(
        screen.getByText(/Candidate generation is unavailable: the evaluated change history has insufficient temporal history/i)
      ).toBeInTheDocument()
    )
    expect(screen.queryByText(/Analysis completed\. No qualifying candidate/i)).not.toBeInTheDocument()
  })

  it('20. Actual candidate-analysis failures remain distinguishable from a successful zero-candidate result', async () => {
    mockTriageStatus = 500
    mockTriageError = { code: 'InternalError', message: 'Unexpected database failure' }
    await navigateToCandidatesWorkflow()
    fireEvent.click(screen.getByRole('button', { name: 'Triage temporal signals' }))

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Unexpected database failure'))
    expect(screen.queryByText(/Analysis completed\. No qualifying candidate/i)).not.toBeInTheDocument()
  })

  it('21. Changing upstream area inputs clears stale candidates', async () => {
    await navigateToCandidatesWorkflow()
    fireEvent.click(screen.getByRole('button', { name: 'Triage temporal signals' }))
    await waitFor(() => expect(screen.getByText('#1 / urgent priority')).toBeInTheDocument())

    // Go back to Stage 01 and start drawing new area
    fireEvent.click(screen.getByTestId('back-to-area-btn'))
    await waitFor(() => expect(screen.getByText('01 / Area')).toBeInTheDocument())

    fireEvent.click(screen.getByRole('button', { name: 'Draw area' }))

    // Stage 05 should now be locked and candidates cleared
    expect(screen.getByLabelText(/05.*CANDIDATES/i)).toHaveClass('workflow-step--locked')
  })

  it('22. Downstream evidence state is cleared when prerequisites become stale', async () => {
    await navigateToCandidatesWorkflow()
    fireEvent.click(screen.getByRole('button', { name: 'Triage temporal signals' }))
    await waitFor(() => expect(screen.getByText('#1 / urgent priority')).toBeInTheDocument())
    fireEvent.click(screen.getByText('#1 / urgent priority'))

    // Go to evidence
    fireEvent.click(screen.getByRole('button', { name: 'Inspect candidate evidence' }))
    await waitFor(() => expect(screen.getByText('06 / Evidence')).toBeInTheDocument())

    // Go to Stage 01 and start drawing new area
    fireEvent.click(screen.getByTestId('back-to-area-btn'))
    await waitFor(() => expect(screen.getByText('01 / Area')).toBeInTheDocument())

    fireEvent.click(screen.getByRole('button', { name: 'Draw area' }))

    // Stage 06 should now be locked
    expect(screen.getByLabelText(/06.*EVIDENCE/i)).toHaveClass('workflow-step--locked')
  })

  it('23. No automatic candidate selection occurs after result loading', async () => {
    await navigateToCandidatesWorkflow()
    fireEvent.click(screen.getByRole('button', { name: 'Triage temporal signals' }))
    await waitFor(() => expect(screen.getByText('#1 / urgent priority')).toBeInTheDocument())

    expect(screen.getByTestId('map-selected-id')).toHaveTextContent('none')
    expect(screen.queryByText('Candidate Assessment')).not.toBeInTheDocument()
  })

  it('24. Technical IDs are not used as the primary analyst-facing labels', async () => {
    await navigateToCandidatesWorkflow()
    fireEvent.click(screen.getByRole('button', { name: 'Triage temporal signals' }))
    await waitFor(() => expect(screen.getByText('#1 / urgent priority')).toBeInTheDocument())

    const listButtons = screen.getAllByRole('button', { name: /priority/i })
    expect(listButtons[0].querySelector('strong')).toHaveTextContent('#1 / urgent priority')
    expect(listButtons[0].querySelector('strong')).not.toHaveTextContent('analysis-42-signal-101')
  })

  it('25. Candidate selection remains compatible with the existing non-linear workflow', async () => {
    await navigateToCandidatesWorkflow()
    fireEvent.click(screen.getByRole('button', { name: 'Triage temporal signals' }))
    await waitFor(() => expect(screen.getByText('#1 / urgent priority')).toBeInTheDocument())
    fireEvent.click(screen.getByText('#1 / urgent priority'))

    // Navigate to Stage 01 AREA via in-stage button
    fireEvent.click(screen.getByTestId('back-to-area-btn'))
    await waitFor(() => expect(screen.getByText('01 / Area')).toBeInTheDocument())

    // Return to Stage 05 CANDIDATES via in-stage button: selected candidate remains selected
    fireEvent.click(screen.getByTestId('return-to-candidates-btn'))
    await waitFor(() => expect(screen.getByText('Candidate Assessment')).toBeInTheDocument())
    expect(screen.getByText('analysis-42-signal-101')).toBeInTheDocument()
  })

  it('26. Existing map/candidate focus behavior remains correct where already implemented', async () => {
    await navigateToCandidatesWorkflow()
    fireEvent.click(screen.getByRole('button', { name: 'Triage temporal signals' }))
    await waitFor(() => expect(screen.getByText('#1 / urgent priority')).toBeInTheDocument())

    // Focus candidate 2 via map
    fireEvent.click(screen.getByTestId('map-candidate-analysis-42-signal-102'))
    expect(screen.getByText('Candidate Assessment')).toBeInTheDocument()
    expect(screen.getByText('analysis-42-signal-102')).toBeInTheDocument()
  })

  it('27. Candidate ordering remains deterministic', async () => {
    await navigateToCandidatesWorkflow()
    fireEvent.click(screen.getByRole('button', { name: 'Triage temporal signals' }))
    await waitFor(() => expect(screen.getByText('#1 / urgent priority')).toBeInTheDocument())

    const listItems = screen.getAllByRole('button', { name: /priority/i })
    expect(listItems[0]).toHaveTextContent('#1 / urgent priority')
    expect(listItems[1]).toHaveTextContent('#2 / normal priority')
  })

  it('28. No frontend score/ranking formula has been introduced', async () => {
    await navigateToCandidatesWorkflow()
    fireEvent.click(screen.getByRole('button', { name: 'Triage temporal signals' }))
    await waitFor(() => expect(screen.getByText('#1 / urgent priority')).toBeInTheDocument())

    // Exactly matches backend-supplied scores
    expect(screen.getByText(/0\.842/)).toBeInTheDocument()
    expect(screen.getByText(/0\.385/)).toBeInTheDocument()
  })
})
