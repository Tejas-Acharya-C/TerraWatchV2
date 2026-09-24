import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import App from './App'
import { STAGE_TITLES } from './workflow'
import type { AOIResponse, AnalysisOrchestrationResponse, ImageryAcquisition } from './lib/api'

vi.mock('./MapCanvas', () => ({
  default: () => <div data-testid="mock-map">Mock Map Canvas</div>,
}))

const samplePolygon = {
  type: 'Polygon' as const,
  coordinates: [[[75.0, 13.0], [75.2, 13.0], [75.2, 13.2], [75.0, 13.2], [75.0, 13.0]]],
}

function makeAcquisition(overrides: Partial<ImageryAcquisition> = {}): ImageryAcquisition {
  return {
    acquisition_id: 1,
    aoi_id: 1,
    requested_start_datetime: '2024-01-01T00:00:00Z',
    requested_end_datetime: '2024-04-01T00:00:00Z',
    item_id: 'S2A_MSIL2A_20240115T050141_N0510_R019_T43PGQ',
    collection: 'sentinel-2-l2a',
    acquisition_datetime: '2024-01-15T10:00:00Z',
    assets: [{ asset_id: 'red', href: 'https://example.com/b04.tif' }],
    prepared_path: '/path/to/prepared.tif',
    raster: {
      crs: 'EPSG:32643',
      transform: [10, 0, 500000, 0, -10, 1500000],
      width: 100,
      height: 100,
      resolution: [10, 10],
      bounds: [500000, 1490000, 501000, 1500000],
      count: 2,
      dtype: 'uint16',
      nodata: 0,
    },
    source_metadata: { 'eo:cloud_cover': 3.5 },
    created_at: '2024-01-15T10:05:00Z',
    observation_state: 'usable',
    quality_reason: 'quality_policy_passed',
    quality_metrics: {
      usable_percentage: 95.0,
      cloud_percentage: 3.5,
      shadow_percentage: 1.5,
    },
    ...overrides,
  }
}

function makeMockOrchestrationResponse(overrides: Partial<AnalysisOrchestrationResponse> = {}): AnalysisOrchestrationResponse {
  return {
    aoi_id: 1,
    eligible_observation_ids: [1, 2, 4],
    reused_detection_count: 0,
    generated_detection_count: 2,
    reused_analysis: false,
    analysis: {
      analysis_id: 100,
      iou_threshold: 0.25,
      detector_run_count: 2,
      observation_count: 3,
      temporal_span_days: 90,
      state: 'persistent',
      usable_observation_count: 3,
      usable_interval_count: 2,
      quality_support: 0.95,
      quality_aggregation_method: 'mean_min_usable_fraction',
      seasonal_interpretation: 'less_seasonal_compatible',
      observations: [
        { acquisition_id: 1, item_id: 'S2A_1', acquisition_datetime: '2024-01-15T10:00:00Z', sequence_index: 0, quality_state: 'usable', usable_pixel_fraction: 0.95 },
        { acquisition_id: 2, item_id: 'S2A_2', acquisition_datetime: '2024-02-15T10:00:00Z', sequence_index: 1, quality_state: 'usable', usable_pixel_fraction: 0.95 },
        { acquisition_id: 4, item_id: 'S2A_4', acquisition_datetime: '2024-04-15T10:00:00Z', sequence_index: 2, quality_state: 'usable', usable_pixel_fraction: 0.95 },
      ],
      relationships: [
        {
          before_acquisition_id: 1,
          after_acquisition_id: 2,
          detection_run_id: 101,
          region_count: 3,
          changed_pixel_count: 85,
          elapsed_days: 31,
          quality_support: 0.95,
        },
        {
          before_acquisition_id: 2,
          after_acquisition_id: 4,
          detection_run_id: 102,
          region_count: 1,
          changed_pixel_count: 20,
          elapsed_days: 60,
          quality_support: 0.95,
        },
      ],
      signals: [
        {
          signal_id: 1,
          geometry: { type: 'Polygon', coordinates: [[[75.01, 13.01], [75.02, 13.01], [75.02, 13.02], [75.01, 13.02], [75.01, 13.01]]] },
          support_count: 2,
          interval_count: 2,
          persistence_ratio: 1.0,
          recurrence_count: 0,
          transient_interval_count: 0,
          temporal_consistency: 0.9,
          matched_region_coverage: 0.8,
          first_change_datetime: '2024-01-15T10:00:00Z',
          last_supporting_datetime: '2024-04-15T10:00:00Z',
          state: 'persistent',
        },
      ],
      created_at: '2024-04-15T10:05:00Z',
    },
    candidates: {
      analysis_id: 100,
      candidates: [
        {
          candidate_id: 'c1',
          analysis_id: 100,
          signal_id: 1,
          geometry: { type: 'Polygon', coordinates: [[[75.01, 13.01], [75.02, 13.01], [75.02, 13.02], [75.01, 13.02], [75.01, 13.01]]] },
          score: 85,
          rank: 1,
          severity: 'high',
          priority: 'high',
          temporal_state: 'persistent',
          review_state: 'unreviewed',
          rationale: {
            evidence_summary: 'Persistent change signal',
            factors: [],
            limiting_factors: [],
            severity_rationale: 'High severity',
            priority_rationale: 'High priority',
          },
          quality_support: 0.95,
          mean_change_signal: -0.35,
          max_change_signal: -0.6,
          metrics: {
            support_count: 2,
            interval_count: 2,
            persistence_ratio: 1.0,
            recurrence_count: 0,
            transient_interval_count: 0,
            temporal_consistency: 0.9,
            matched_region_coverage: 0.8,
            first_change_datetime: '2024-01-15T10:00:00Z',
            last_supporting_datetime: '2024-04-15T10:00:00Z',
            temporal_state: 'persistent',
            quality_support: 0.95,
          },
          source_signal_ids: [1],
          detection_run_ids: [101, 102],
          acquisition_ids: [1, 2, 4],
          created_at: '2024-04-15T10:05:00Z',
          updated_at: '2024-04-15T10:05:00Z',
        },
      ],
    },
    ...overrides,
  }
}

describe('TerraWatch V2 — Phase 19C: Stage 03 / CHANGES Automatic Analysis Orchestration', () => {
  let mockAois: AOIResponse[] = []
  let mockAcquisitions: ImageryAcquisition[] = []
  let mockOrchestration: AnalysisOrchestrationResponse = makeMockOrchestrationResponse()
  let orchestrationStatus = 200

  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    sessionStorage.clear()

    mockAois = [
      {
        aoi_id: 1,
        geometry: samplePolygon,
        created_at: '2024-01-01T00:00:00Z',
        updated_at: '2024-01-01T00:00:00Z',
      },
    ]

    mockAcquisitions = [
      makeAcquisition({
        acquisition_id: 1,
        acquisition_datetime: '2024-01-15T10:00:00Z',
        observation_state: 'usable',
        quality_reason: 'quality_policy_passed',
      }),
      makeAcquisition({
        acquisition_id: 2,
        acquisition_datetime: '2024-02-15T10:00:00Z',
        observation_state: 'usable',
        quality_reason: 'quality_policy_passed',
      }),
      makeAcquisition({
        acquisition_id: 3,
        acquisition_datetime: '2024-03-15T10:00:00Z',
        observation_state: 'valid_unusable',
        quality_reason: 'insufficient_usable_pixels',
        quality_metrics: { usable_percentage: 30.0, cloud_percentage: 65.0, shadow_percentage: 5.0 },
      }),
      makeAcquisition({
        acquisition_id: 4,
        acquisition_datetime: '2024-04-15T10:00:00Z',
        observation_state: 'usable',
        quality_reason: 'quality_policy_passed',
      }),
    ]

    mockOrchestration = makeMockOrchestrationResponse()
    orchestrationStatus = 200

    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input)

      if (url.includes('/api/v1/aois')) {
        return new Response(JSON.stringify(mockAois), { status: 200 })
      }
      if (url.includes('/api/v1/aoi/1') || (url.includes('/api/v1/aoi') && !url.includes('/aois'))) {
        return new Response(JSON.stringify(mockAois[0]), { status: 200 })
      }
      if (url.includes('/api/v1/imagery/acquisitions')) {
        return new Response(
          JSON.stringify({ aoi_id: 1, total: mockAcquisitions.length, acquisitions: mockAcquisitions }),
          { status: 200 },
        )
      }
      if (url.includes('/api/v1/orchestrations/analyze')) {
        if (orchestrationStatus !== 200) {
          return new Response(JSON.stringify({ code: 'OrchestrationError', message: 'Analysis orchestration failed' }), {
            status: orchestrationStatus,
            headers: { 'Content-Type': 'application/json' },
          })
        }
        return new Response(JSON.stringify(mockOrchestration), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }
      return new Response(JSON.stringify({}), { status: 200 })
    })
  })

  async function loadAreaAndNavigateToChanges() {
    render(<App />)
    fireEvent.focus(screen.getByLabelText('Saved AOIs'))
    await waitFor(() =>
      expect(screen.getByLabelText('Saved AOIs').querySelectorAll('option').length).toBeGreaterThan(1),
    )
    fireEvent.change(screen.getByLabelText('Saved AOIs'), { target: { value: '1' } })
    await waitFor(() => expect(screen.getByTestId('proceed-to-observations-btn')).toBeInTheDocument())

    // Advance via in-stage buttons (passive rail)
    await waitFor(() => expect(screen.getByTestId('proceed-to-observations-btn')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('proceed-to-observations-btn'))
    await waitFor(() => expect(screen.getByTestId('proceed-to-changes-btn')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('proceed-to-changes-btn'))

    // Wait until the panel switches to CHANGES
    await waitFor(() =>
      expect(screen.getByText('Analyze land-surface changes across observation intervals.')).toBeInTheDocument(),
    )
  }

  it('1. Stage 03 is labeled CHANGES in navigation, title, and headers', async () => {
    await loadAreaAndNavigateToChanges()
    expect(STAGE_TITLES.CHANGE).toBe('CHANGES')
    expect(screen.queryByRole('button', { name: /03 CHANGES/i })).not.toBeInTheDocument()
    expect(screen.getByLabelText('03 CHANGES')).toBeInTheDocument()
    expect(screen.getByText('03 / Changes')).toBeInTheDocument()
    expect(screen.getByText('CHANGES', { selector: '.panel-index' })).toBeInTheDocument()
  })

  it('2. Stage description clearly states its purpose: analyzing land-surface changes across observation intervals', async () => {
    await loadAreaAndNavigateToChanges()
    expect(screen.getByText('Analyze land-surface changes across observation intervals.')).toBeInTheDocument()
  })

  it('3. In Stage 03, the primary action is "Run automated analysis"', async () => {
    await loadAreaAndNavigateToChanges()
    const runBtn = screen.getByTestId('run-automated-analysis-btn')
    expect(runBtn).toBeInTheDocument()
    expect(runBtn).toHaveTextContent('Run automated analysis')
    expect(runBtn).toBeEnabled()
  })

  it('4. If fewer than 2 usable observations exist, "Run automated analysis" is disabled with dependency note', async () => {
    mockAcquisitions = [
      makeAcquisition({ acquisition_id: 1, observation_state: 'usable' }),
      makeAcquisition({ acquisition_id: 2, observation_state: 'valid_unusable' }),
    ]
    await loadAreaAndNavigateToChanges()
    const runBtn = screen.getByTestId('run-automated-analysis-btn')
    expect(runBtn).toBeDisabled()
    expect(screen.getAllByText(/at least two usable observations/i).length).toBeGreaterThan(0)
  })

  it('5. While orchestration is running, shows in-progress state and disables button', async () => {
    let resolveOrchestration: (value: Response) => void
    const orchestrationPromise = new Promise<Response>((resolve) => {
      resolveOrchestration = resolve
    })

    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/api/v1/orchestrations/analyze')) {
        return orchestrationPromise
      }
      if (url.includes('/api/v1/aois')) {
        return new Response(JSON.stringify(mockAois), { status: 200 })
      }
      if (url.includes('/api/v1/aoi/1') || (url.includes('/api/v1/aoi') && !url.includes('/aois'))) {
        return new Response(JSON.stringify(mockAois[0]), { status: 200 })
      }
      if (url.includes('/api/v1/imagery/acquisitions')) {
        return new Response(
          JSON.stringify({ aoi_id: 1, total: mockAcquisitions.length, acquisitions: mockAcquisitions }),
          { status: 200 },
        )
      }
      return new Response(JSON.stringify({}), { status: 200 })
    })

    await loadAreaAndNavigateToChanges()
    const runBtn = screen.getByTestId('run-automated-analysis-btn')
    fireEvent.click(runBtn)

    // Should now show in-progress state
    expect(screen.getByTestId('orchestration-progress')).toBeInTheDocument()
    expect(runBtn).toHaveTextContent('Analyzing observations…')
    expect(runBtn).toBeDisabled()

    // Resolve orchestration
    resolveOrchestration!(new Response(JSON.stringify(mockOrchestration), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))

    await waitFor(() => expect(screen.getByTestId('orchestration-summary')).toBeInTheDocument())
  })

  it('6. Successful orchestration displays high-level summary (evaluated intervals, observations, signals, span)', async () => {
    await loadAreaAndNavigateToChanges()
    const runBtn = screen.getByTestId('run-automated-analysis-btn')
    fireEvent.click(runBtn)

    await waitFor(() => expect(screen.getByTestId('orchestration-summary')).toBeInTheDocument())
    expect(screen.getByText('2 observation intervals evaluated')).toBeInTheDocument()
    expect(screen.getByText('3')).toBeInTheDocument() // observation count
    expect(screen.getByText('90 days')).toBeInTheDocument()
    expect(screen.getByText('Persistent')).toBeInTheDocument()
  })

  it('7. Orchestration summary provides direct navigation buttons to Candidates and Change History', async () => {
    await loadAreaAndNavigateToChanges()
    fireEvent.click(screen.getByTestId('run-automated-analysis-btn'))
    await waitFor(() => expect(screen.getByTestId('orchestration-summary')).toBeInTheDocument())

    const candidatesBtn = screen.getByTestId('proceed-to-candidates-btn')
    expect(candidatesBtn).toBeInTheDocument()
    expect(candidatesBtn).toHaveTextContent('Inspect Candidates (1) →')

    const historyBtn = screen.getByTestId('view-history-btn')
    expect(historyBtn).toBeInTheDocument()

    // Clicking "Inspect Candidates" moves to stage 05 CANDIDATES
    fireEvent.click(candidatesBtn)
    await waitFor(() => expect(screen.getByText('05 / Candidates')).toBeInTheDocument())
  })

  it('8. Interval inspector allows selecting intervals and shows interval-specific Before/After and detection results', async () => {
    await loadAreaAndNavigateToChanges()
    fireEvent.click(screen.getByTestId('run-automated-analysis-btn'))
    await waitFor(() => expect(screen.getByTestId('interval-select')).toBeInTheDocument())

    const intervalSelect = screen.getByTestId('interval-select') as HTMLSelectElement
    expect(intervalSelect.options.length).toBe(2)

    // Interval 1 default
    expect(screen.getByText('3 detected change regions')).toBeInTheDocument()
    expect(screen.getByText('Changed pixels:')).toBeInTheDocument()
    expect(screen.getByText('85')).toBeInTheDocument()

    // Change to Interval 2
    fireEvent.change(intervalSelect, { target: { value: '1' } })

    // Interval 2 shows 1 detected change region (proving dynamic interval context rather than global latest lock)
    await waitFor(() => {
      expect(screen.getByText('1 detected change region')).toBeInTheDocument()
    })
    expect(screen.getByText('20')).toBeInTheDocument()
  })

  it('9. Interval Before and After cards display human-readable acquisition dates as primary labels', async () => {
    await loadAreaAndNavigateToChanges()
    fireEvent.click(screen.getByTestId('run-automated-analysis-btn'))
    await waitFor(() => expect(screen.getByTestId('interval-select')).toBeInTheDocument())

    const cards = screen.getAllByRole('region', { name: 'Change interval inspection' })
    expect(cards.length).toBeGreaterThan(0)
    expect(screen.getByText(/15 Jan 2024/i)).toBeInTheDocument()
    expect(screen.getByText(/15 Feb 2024/i)).toBeInTheDocument()
  })

  it('10. Technical details (observation IDs, STAC item, detector parameters) are in collapsible sections', async () => {
    await loadAreaAndNavigateToChanges()
    fireEvent.click(screen.getByTestId('run-automated-analysis-btn'))
    await waitFor(() => expect(screen.getByTestId('interval-select')).toBeInTheDocument())

    const technicalDetails = screen.getAllByText('Technical details')
    expect(technicalDetails.length).toBeGreaterThan(0)
    expect(screen.getByText(/Observation ID: #1/)).toBeInTheDocument()
    expect(screen.getByText(/Detection Run: #101/)).toBeInTheDocument()
    expect(screen.getByText(/Threshold: 0\.20/)).toBeInTheDocument()
  })

  it('11. Zero change regions in an interval is clearly distinguished from a failed detection', async () => {
    mockOrchestration = makeMockOrchestrationResponse({
      analysis: {
        ...makeMockOrchestrationResponse().analysis!,
        relationships: [
          {
            before_acquisition_id: 1,
            after_acquisition_id: 2,
            detection_run_id: 101,
            region_count: 0,
            changed_pixel_count: 0,
            elapsed_days: 31,
            quality_support: 0.95,
          },
        ],
      },
    })

    await loadAreaAndNavigateToChanges()
    fireEvent.click(screen.getByTestId('run-automated-analysis-btn'))

    await waitFor(() =>
      expect(
        screen.getByText('No meaningful change detected (0 change regions)'),
      ).toBeInTheDocument(),
    )
    expect(screen.queryByText('Analysis failed')).not.toBeInTheDocument()
  })

  it('12. A failed analysis clearly explains what failed without technical jargon', async () => {
    orchestrationStatus = 500

    await loadAreaAndNavigateToChanges()
    fireEvent.click(screen.getByTestId('run-automated-analysis-btn'))

    await waitFor(() => expect(screen.getByText('Analysis failed')).toBeInTheDocument())
    expect(screen.getAllByText('Analysis orchestration failed').length).toBeGreaterThan(0)
  })

  it('13. Language remains strictly neutral: "detected change regions", never "confirmed change"', async () => {
    await loadAreaAndNavigateToChanges()
    fireEvent.click(screen.getByTestId('run-automated-analysis-btn'))

    await waitFor(() => expect(screen.getAllByText(/detected change region/i).length).toBeGreaterThan(0))
    expect(screen.queryByText(/confirmed change/i)).not.toBeInTheDocument()
  })

  it('14. Satellite layer switcher toggles between BEFORE and AFTER views for the inspected interval', async () => {
    await loadAreaAndNavigateToChanges()
    fireEvent.click(screen.getByTestId('run-automated-analysis-btn'))
    await waitFor(() => expect(screen.getByRole('button', { name: 'AFTER' })).toBeInTheDocument())

    const beforeBtn = screen.getByRole('button', { name: 'BEFORE' })
    const afterBtn = screen.getByRole('button', { name: 'AFTER' })

    expect(afterBtn).toHaveClass('layer-switcher__active')
    fireEvent.click(beforeBtn)
    expect(beforeBtn).toHaveClass('layer-switcher__active')
  })
})
