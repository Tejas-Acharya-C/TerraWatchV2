import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import App from './App'
import { STAGE_ORDER, STAGE_TITLES } from './workflow'
import type { AnalysisOrchestrationResponse, ImageryAcquisition, AOIResponse } from './lib/api'

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

function makeMockOrchestrationResponse(): AnalysisOrchestrationResponse {
  return {
    aoi_id: 1,
    eligible_observation_ids: [1, 2],
    reused_detection_count: 0,
    generated_detection_count: 1,
    reused_analysis: false,
    analysis: {
      analysis_id: 100,
      iou_threshold: 0.25,
      detector_run_count: 1,
      observation_count: 2,
      temporal_span_days: 31,
      state: 'persistent',
      usable_observation_count: 2,
      usable_interval_count: 1,
      quality_support: 0.95,
      quality_aggregation_method: 'mean_min_usable_fraction',
      seasonal_interpretation: 'less_seasonal_compatible',
      observations: [
        { acquisition_id: 1, item_id: 'S2A_1', acquisition_datetime: '2024-01-15T10:00:00Z', sequence_index: 0, quality_state: 'usable', usable_pixel_fraction: 0.95 },
        { acquisition_id: 2, item_id: 'S2A_2', acquisition_datetime: '2024-02-15T10:00:00Z', sequence_index: 1, quality_state: 'usable', usable_pixel_fraction: 0.95 },
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
      ],
      signals: [
        {
          signal_id: 1,
          geometry: { type: 'Polygon', coordinates: [[[75.01, 13.01], [75.02, 13.01], [75.02, 13.02], [75.01, 13.02], [75.01, 13.01]]] },
          support_count: 1,
          interval_count: 1,
          persistence_ratio: 1.0,
          recurrence_count: 0,
          transient_interval_count: 0,
          temporal_consistency: 0.9,
          matched_region_coverage: 0.8,
          first_change_datetime: '2024-01-15T10:00:00Z',
          last_supporting_datetime: '2024-02-15T10:00:00Z',
          state: 'persistent',
        },
      ],
      created_at: '2024-02-15T10:05:00Z',
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
            support_count: 1,
            interval_count: 1,
            persistence_ratio: 1.0,
            recurrence_count: 0,
            transient_interval_count: 0,
            temporal_consistency: 0.9,
            matched_region_coverage: 0.8,
            first_change_datetime: '2024-01-15T10:00:00Z',
            last_supporting_datetime: '2024-02-15T10:00:00Z',
            temporal_state: 'persistent',
            quality_support: 0.95,
          },
          source_signal_ids: [1],
          detection_run_ids: [101],
          acquisition_ids: [1, 2],
          created_at: '2024-02-15T10:05:00Z',
          updated_at: '2024-02-15T10:05:00Z',
        },
      ],
    },
  }
}

describe('TerraWatch V2 — Phase 19E: Information Hierarchy', () => {
  let mockAois: AOIResponse[] = []
  let mockAcquisitions: ImageryAcquisition[] = []
  let mockOrchestration: AnalysisOrchestrationResponse = makeMockOrchestrationResponse()

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
      makeAcquisition({ acquisition_id: 1, acquisition_datetime: '2024-01-15T10:00:00Z' }),
      makeAcquisition({ acquisition_id: 2, acquisition_datetime: '2024-02-15T10:00:00Z' }),
    ]

    mockOrchestration = makeMockOrchestrationResponse()

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
        return new Response(JSON.stringify(mockOrchestration), { status: 200 })
      }
      return new Response(JSON.stringify({}), { status: 200 })
    })
  })

  async function loadAreaAndProceedToChanges() {
    render(<App />)
    fireEvent.focus(screen.getByLabelText('Saved AOIs'))
    await waitFor(() =>
      expect(screen.getByLabelText('Saved AOIs').querySelectorAll('option').length).toBeGreaterThan(1),
    )
    fireEvent.change(screen.getByLabelText('Saved AOIs'), { target: { value: '1' } })
    await waitFor(() => expect(screen.getByTestId('proceed-to-observations-btn')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('proceed-to-observations-btn'))
    await waitFor(() => expect(screen.getByTestId('proceed-to-changes-btn')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('proceed-to-changes-btn'))
    await waitFor(() =>
      expect(screen.getByText('Analyze land-surface changes across observation intervals.')).toBeInTheDocument(),
    )
  }

  it('1. Workflow rail is the authoritative location for overall workflow position and status', async () => {
    render(<App />)
    const rail = screen.getByRole('group', { name: /Analysis workflow status/i })
    expect(rail).toBeInTheDocument()

    STAGE_ORDER.forEach((stage, index) => {
      const stepNumber = String(index + 1).padStart(2, '0')
      const label = `${stepNumber} ${STAGE_TITLES[stage]}`
      expect(screen.getByLabelText(label)).toBeInTheDocument()
    })

    expect(screen.queryByRole('button', { name: /01 AREA/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /03 CHANGES/i })).not.toBeInTheDocument()
  })

  it('2. Header does not compete with workflow rail through duplicate workflow status', async () => {
    render(<App />)
    expect(screen.getByText('TerraWatch V2')).toBeInTheDocument()
    expect(screen.getByText('Change Detection for Karnataka')).toBeInTheDocument()
    expect(screen.getByText('Karnataka analysis')).toBeInTheDocument()

    const aoiState = screen.getByTestId('aoi-state')
    expect(aoiState).toHaveTextContent('No area selected')

    expect(screen.queryByTestId('aoi-status')).not.toBeInTheDocument()
    expect(document.querySelector('.header-context__status')).toBeNull()
  })

  it('3. Current stage does not display redundant copies of rail status above stage heading', async () => {
    render(<App />)
    expect(screen.getByText('01 / Area')).toBeInTheDocument()
    expect(screen.getByText('Define the area you want to investigate')).toBeInTheDocument()

    expect(document.querySelector('.stage-status')).toBeNull()
  })

  it('4. Active orchestration progress is visible in stage content while automation runs', async () => {
    let resolveOrchestration: (value: Response) => void
    const pendingPromise = new Promise<Response>((resolve) => {
      resolveOrchestration = resolve
    })

    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/api/v1/orchestrations/analyze')) {
        return pendingPromise
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

    await loadAreaAndProceedToChanges()

    const runBtn = screen.getByTestId('run-automated-analysis-btn')
    fireEvent.click(runBtn)

    await waitFor(() => {
      expect(screen.getByTestId('orchestration-progress')).toBeInTheDocument()
    })
    expect(screen.getByText('ANALYZING')).toBeInTheDocument()

    expect(screen.queryByTestId('aoi-status')).not.toBeInTheDocument()
    expect(document.querySelector('.header-context__status')).toBeNull()

    resolveOrchestration!(new Response(JSON.stringify(mockOrchestration), { status: 200 }))
    await waitFor(() => {
      expect(screen.getByTestId('orchestration-summary')).toBeInTheDocument()
    })

    expect(screen.queryByTestId('orchestration-progress')).not.toBeInTheDocument()
  })

  it('5. Post-orchestration summary is concise and does not duplicate completion badge', async () => {
    await loadAreaAndProceedToChanges()
    fireEvent.click(screen.getByTestId('run-automated-analysis-btn'))

    await waitFor(() => {
      expect(screen.getByTestId('orchestration-summary')).toBeInTheDocument()
    })

    expect(screen.getByText('Automated analysis summary')).toBeInTheDocument()
    expect(screen.getByText(/1 observation interval evaluated/i)).toBeInTheDocument()

    expect(screen.queryByText(/✓ Analysis complete/i)).not.toBeInTheDocument()
  })

  it('6. Scientific result metrics remain visible on their authoritative result surfaces', async () => {
    await loadAreaAndProceedToChanges()
    fireEvent.click(screen.getByTestId('run-automated-analysis-btn'))

    await waitFor(() => {
      expect(screen.getByTestId('orchestration-summary')).toBeInTheDocument()
    })

    expect(screen.getByText('3 detected change regions')).toBeInTheDocument()
    expect(screen.getByText('85')).toBeInTheDocument()
    expect(screen.getByText('95%')).toBeInTheDocument()
    expect(screen.getByText('31.0 d')).toBeInTheDocument()
    expect(screen.getByText('Persistent')).toBeInTheDocument()

    fireEvent.click(screen.getByTestId('view-history-btn'))
    await waitFor(() => {
      expect(screen.getByText('04 / Change history')).toBeInTheDocument()
    })

    expect(screen.getByTestId('temporal-state-badge')).toHaveTextContent('Persistent')
    expect(screen.getByText('Change matched across the required temporal intervals.')).toBeInTheDocument()
    expect(screen.getByText('31 days')).toBeInTheDocument()

    fireEvent.click(screen.getByTestId('temporal-proceed-candidates-btn'))
    await waitFor(() => {
      expect(screen.getByText('05 / Candidates')).toBeInTheDocument()
    })
    expect(screen.getByText(/#1 \/ high priority/i)).toBeInTheDocument()
    expect(screen.getByText(/evidence score: 85\.000/i)).toBeInTheDocument()
  })

  it('7. Explicit error states remain visible when operations fail', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/api/v1/orchestrations/analyze')) {
        return new Response(JSON.stringify({ code: 'OrchestrationError', message: 'Downstream STAC query failed' }), { status: 500 })
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

    await loadAreaAndProceedToChanges()
    fireEvent.click(screen.getByTestId('run-automated-analysis-btn'))

    await waitFor(() => {
      expect(screen.getByText('Analysis failed')).toBeInTheDocument()
    })
    expect(screen.getAllByText('Downstream STAC query failed').length).toBeGreaterThan(0)
  })

  it('8. Upstream inspection banner clearly explains read-only state without duplicate banners', async () => {
    await loadAreaAndProceedToChanges()
    fireEvent.click(screen.getByTestId('run-automated-analysis-btn'))
    await waitFor(() => expect(screen.getByTestId('proceed-to-candidates-btn')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('proceed-to-candidates-btn'))
    await waitFor(() => expect(screen.getByText('05 / Candidates')).toBeInTheDocument())

    fireEvent.click(screen.getByTestId('back-to-area-btn'))
    await waitFor(() => expect(screen.getByText('01 / Area')).toBeInTheDocument())

    const banner = screen.getByRole('status')
    expect(banner).toHaveTextContent(/Inspecting stage:.*AREA.*read-only/i)
    expect(screen.getByRole('button', { name: /^Return to CHANGES$/i })).toBeInTheDocument()

    expect(document.querySelector('.stage-status')).toBeNull()
    expect(screen.queryByTestId('aoi-status')).not.toBeInTheDocument()
  })
})
