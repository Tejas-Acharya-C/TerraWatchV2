import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import App from './App'
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
          candidate_id: 'cand-001',
          analysis_id: 100,
          signal_id: 1,
          geometry: { type: 'Polygon', coordinates: [[[75.01, 13.01], [75.02, 13.01], [75.02, 13.02], [75.01, 13.02], [75.01, 13.01]]] },
          score: 85,
          rank: 1,
          severity: 'high',
          priority: 'high',
          temporal_state: 'persistent',
          review_state: 'unreviewed',
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
          rationale: {
            evidence_summary: 'Persistent change signal across evaluated intervals',
            factors: [],
            limiting_factors: [],
            severity_rationale: 'High severity change',
            priority_rationale: 'Urgent field attention needed',
          },
          quality_support: 0.95,
          mean_change_signal: -0.35,
          max_change_signal: -0.6,
          total_area_m2: 4000.0,
          created_at: '2024-04-15T10:05:00Z',
          updated_at: '2024-04-15T10:05:00Z',
        },
      ],
    },
    ...overrides,
  }
}

describe('TerraWatch V2 — Phase 19C End-to-End Orchestration Workflow', () => {
  let mockAois: AOIResponse[] = []
  let mockAcquisitions: ImageryAcquisition[] = []
  let capturedOrchestrationPayload: unknown = null

  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    sessionStorage.clear()
    capturedOrchestrationPayload = null

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

    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
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
        if (init?.body) {
          capturedOrchestrationPayload = JSON.parse(String(init.body))
        }
        return new Response(JSON.stringify(makeMockOrchestrationResponse()), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }
      return new Response(JSON.stringify({}), { status: 200 })
    })
  })

  it('orchestrates analysis automatically from Stage 03 CHANGES and enables downstream stages', async () => {
    render(<App />)

    // Select AOI
    fireEvent.focus(screen.getByLabelText('Saved AOIs'))
    await waitFor(() =>
      expect(screen.getByLabelText('Saved AOIs').querySelectorAll('option').length).toBeGreaterThan(1),
    )
    fireEvent.change(screen.getByLabelText('Saved AOIs'), { target: { value: '1' } })
    await waitFor(() => expect(screen.getByTestId('aoi-state')).toHaveTextContent('Area active · #1'))

    // Navigate to Stage 03 CHANGES via in-stage buttons (passive rail)
    await waitFor(() => expect(screen.getByTestId('proceed-to-observations-btn')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('proceed-to-observations-btn'))
    await waitFor(() => expect(screen.getByTestId('proceed-to-changes-btn')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('proceed-to-changes-btn'))
    await waitFor(() => expect(screen.getByText('03 / Changes')).toBeInTheDocument())

    // Run automated analysis
    const runBtn = screen.getByTestId('run-automated-analysis-btn')
    expect(runBtn).toBeEnabled()
    fireEvent.click(runBtn)

    // Verify orchestration request payload was sent to backend authoritative endpoint
    await waitFor(() => expect(capturedOrchestrationPayload).not.toBeNull())
    expect(capturedOrchestrationPayload).toMatchObject({
      aoi_id: 1,
    })

    // Verify summary is rendered
    await waitFor(() => expect(screen.getByTestId('orchestration-summary')).toBeInTheDocument())
    expect(screen.getByText('2 observation intervals evaluated')).toBeInTheDocument()

    // Downstream stages CANDIDATES and CHANGE HISTORY are now ready in the passive rail
    const candidatesRailStep = screen.getByLabelText('05 CANDIDATES')
    expect(candidatesRailStep).toHaveClass('workflow-step--ready')
    expect(screen.queryByRole('button', { name: /05 CANDIDATES/i })).not.toBeInTheDocument()

    // Click Inspect Candidates
    fireEvent.click(screen.getByTestId('proceed-to-candidates-btn'))
    await waitFor(() => expect(screen.getByText('05 / Candidates')).toBeInTheDocument())
    expect(screen.getByText('#1 / high priority')).toBeInTheDocument()
  })

  it('orchestrates analysis automatically from Stage 04 CHANGE HISTORY and renders signals', async () => {
    render(<App />)

    // Select AOI
    fireEvent.focus(screen.getByLabelText('Saved AOIs'))
    await waitFor(() =>
      expect(screen.getByLabelText('Saved AOIs').querySelectorAll('option').length).toBeGreaterThan(1),
    )
    fireEvent.change(screen.getByLabelText('Saved AOIs'), { target: { value: '1' } })
    await waitFor(() => expect(screen.getByTestId('aoi-state')).toHaveTextContent('Area active · #1'))

    // Navigate to Stage 04 CHANGE HISTORY via in-stage buttons (passive rail)
    await waitFor(() => expect(screen.getByTestId('proceed-to-observations-btn')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('proceed-to-observations-btn'))
    await waitFor(() => expect(screen.getByTestId('proceed-to-changes-btn')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('proceed-to-changes-btn'))
    await waitFor(() => expect(screen.getByTestId('proceed-to-history-btn')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('proceed-to-history-btn'))
    await waitFor(() => expect(screen.getByText('04 / Change history')).toBeInTheDocument())

    // Verify eligible observations timeline is shown
    expect(screen.getByText('Available Eligible Observations (3)')).toBeInTheDocument()

    // Run automated analysis
    const runBtn = screen.getByTestId('run-automated-analysis-temporal-btn')
    expect(runBtn).toBeEnabled()
    fireEvent.click(runBtn)

    // Verify results
    await waitFor(() => expect(screen.getByTestId('temporal-state-badge')).toHaveTextContent('Persistent'))
    expect(screen.getByText('Identified signals')).toBeInTheDocument()
    expect(screen.getByTestId('temporal-proceed-candidates-btn')).toBeInTheDocument()
  })
})
