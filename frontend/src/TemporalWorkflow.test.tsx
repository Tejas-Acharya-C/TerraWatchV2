import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import { isTemporalEligible, getTemporalIneligibleReason, getUsablePixelFraction } from './lib/temporalEligibility'
import type { AnalysisOrchestrationResponse, ImageryAcquisition } from './lib/api'

vi.mock('./MapCanvas', () => ({
  default: () => <div data-testid="mock-map">Mock Map</div>,
}))

describe('Stage 4 Temporal Workflow Contract Cleanup (Phase 1 & Phase 19C)', () => {
  const dummyRaster = {
    crs: 'EPSG:32643',
    transform: [10, 0, 0, 0, -10, 0],
    width: 100,
    height: 100,
    resolution: [10, 10] as [number, number],
    bounds: [0, 0, 1000, 1000] as [number, number, number, number],
    count: 2,
    dtype: 'uint16',
    nodata: 0,
  }

  const createMockAcquisition = (
    id: number,
    date: string,
    state: string = 'usable',
    usablePercentage: number = 85,
    extraMetrics?: Record<string, unknown>
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
    quality_reason: state === 'usable' ? 'quality_policy_passed' : 'insufficient_usable_pixels',
    quality_metrics: {
      usable_percentage: usablePercentage,
      cloud_percentage: 10,
      shadow_percentage: 5,
      invalid_percentage: 0,
      ...extraMetrics,
    },
  })

  describe('Unit Eligibility Checks', () => {
    it('1. Usable observation with positive usable fraction is selectable', () => {
      const acq = createMockAcquisition(1, '2024-01-01T00:00:00Z', 'usable', 80)
      expect(isTemporalEligible(acq)).toBe(true)
      expect(getUsablePixelFraction(acq)).toBe(0.8)
    })

    it('2. valid_unusable observation is not selectable', () => {
      const acq = createMockAcquisition(2, '2024-02-01T00:00:00Z', 'valid_unusable', 30)
      expect(isTemporalEligible(acq)).toBe(false)
      expect(getTemporalIneligibleReason(acq)).toContain('insufficient usable pixels')
    })

    it('3. failed observation is not selectable', () => {
      const acq = createMockAcquisition(3, '2024-03-01T00:00:00Z', 'failed', 0)
      expect(isTemporalEligible(acq)).toBe(false)
      expect(getTemporalIneligibleReason(acq)).toBe('quality assessment failed')
    })

    it('4. legacy_unassessed observation is not selectable', () => {
      const acq = createMockAcquisition(4, '2024-04-01T00:00:00Z', 'legacy_unassessed', 50)
      expect(isTemporalEligible(acq)).toBe(false)
      expect(getTemporalIneligibleReason(acq)).toBe('legacy unassessed quality')
    })

    it('5. zero usable fraction observation is not selectable even if state is usable', () => {
      const acq = createMockAcquisition(5, '2024-05-01T00:00:00Z', 'usable', 0)
      expect(isTemporalEligible(acq)).toBe(false)
      expect(getTemporalIneligibleReason(acq)).toBe('zero usable pixels')
    })
  })

  describe('UI Component and Workflow Contract Checks', () => {
    let persistedAcquisitions: ImageryAcquisition[] = []
    let mockOrchestrationResponse: AnalysisOrchestrationResponse

    beforeEach(() => {
      persistedAcquisitions = [
        createMockAcquisition(10, '2024-01-01T00:00:00Z', 'usable', 80),
        createMockAcquisition(11, '2024-02-01T00:00:00Z', 'valid_unusable', 20),
        createMockAcquisition(12, '2024-03-01T00:00:00Z', 'failed', 0),
        createMockAcquisition(13, '2024-04-01T00:00:00Z', 'legacy_unassessed', 50),
        createMockAcquisition(14, '2024-05-01T00:00:00Z', 'usable', 0),
        createMockAcquisition(15, '2024-06-01T00:00:00Z', 'usable', 90),
      ]

      mockOrchestrationResponse = {
        aoi_id: 1,
        eligible_observation_ids: [10, 15],
        reused_detection_count: 0,
        generated_detection_count: 1,
        reused_analysis: false,
        analysis: {
          analysis_id: 100,
          iou_threshold: 0.25,
          detector_run_count: 1,
          observation_count: 2,
          temporal_span_days: 152,
          state: 'persistent',
          usable_observation_count: 2,
          usable_interval_count: 1,
          quality_support: 0.85,
          quality_aggregation_method: 'mean_min_usable_fraction',
          seasonal_interpretation: 'insufficient_temporal_evidence',
          observations: [
            { acquisition_id: 10, item_id: 'S2_10', acquisition_datetime: '2024-01-01T00:00:00Z', sequence_index: 0, quality_state: 'usable', usable_pixel_fraction: 0.8 },
            { acquisition_id: 15, item_id: 'S2_15', acquisition_datetime: '2024-06-01T00:00:00Z', sequence_index: 1, quality_state: 'usable', usable_pixel_fraction: 0.9 },
          ],
          relationships: [
            {
              before_acquisition_id: 10,
              after_acquisition_id: 15,
              detection_run_id: 1,
              region_count: 2,
              changed_pixel_count: 50,
              elapsed_days: 152,
              quality_support: 0.85,
            },
          ],
          signals: [
            {
              signal_id: 1,
              geometry: { type: 'Polygon', coordinates: [[[77.4, 12.8], [77.5, 12.8], [77.5, 12.9], [77.4, 12.9], [77.4, 12.8]]] },
              support_count: 1,
              interval_count: 1,
              persistence_ratio: 1.0,
              recurrence_count: 0,
              transient_interval_count: 0,
              temporal_consistency: 1.0,
              matched_region_coverage: 1.0,
              first_change_datetime: '2024-01-01T00:00:00Z',
              last_supporting_datetime: '2024-06-01T00:00:00Z',
              state: 'persistent',
            },
          ],
          created_at: '2024-06-01T00:00:00Z',
        },
        candidates: {
          analysis_id: 100,
          candidates: [
            {
              candidate_id: 'cand-1',
              analysis_id: 100,
              signal_id: 1,
              geometry: { type: 'Polygon', coordinates: [[[77.4, 12.8], [77.5, 12.8], [77.5, 12.9], [77.4, 12.9], [77.4, 12.8]]] },
              score: 75,
              rank: 1,
              severity: 'medium',
              priority: 'normal',
              temporal_state: 'persistent',
              review_state: 'unreviewed',
              metrics: {
                support_count: 1,
                interval_count: 1,
                persistence_ratio: 1.0,
                recurrence_count: 0,
                transient_interval_count: 0,
                temporal_consistency: 1.0,
                matched_region_coverage: 1.0,
                first_change_datetime: '2024-01-01T00:00:00Z',
                last_supporting_datetime: '2024-06-01T00:00:00Z',
                temporal_state: 'persistent',
                quality_support: 0.85,
              },
              source_signal_ids: [1],
              detection_run_ids: [1],
              acquisition_ids: [10, 15],
              rationale: {
                evidence_summary: 'Persistent signal',
                factors: [],
                limiting_factors: [],
                severity_rationale: 'Moderate',
                priority_rationale: 'Normal',
              },
              quality_support: 0.85,
              mean_change_signal: -0.3,
              max_change_signal: -0.5,
              total_area_m2: 5000,
              created_at: '2024-06-01T00:00:00Z',
              updated_at: '2024-06-01T00:00:00Z',
            },
          ],
        },
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
                geometry: { type: 'Polygon', coordinates: [[[77.4, 12.8], [77.5, 12.8], [77.5, 12.9], [77.4, 12.9], [77.4, 12.8]]] },
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
              geometry: { type: 'Polygon', coordinates: [[[77.4, 12.8], [77.5, 12.8], [77.5, 12.9], [77.4, 12.9], [77.4, 12.8]]] },
              created_at: '2024-01-01T00:00:00Z',
              updated_at: '2024-01-01T00:00:00Z',
            }),
            { status: 200 }
          )
        }
        if (url.includes('/api/v1/imagery/acquisitions')) {
          return new Response(JSON.stringify({ acquisitions: persistedAcquisitions }), { status: 200 })
        }
        if (url.includes('/api/v1/orchestrations/analyze')) {
          return new Response(JSON.stringify(mockOrchestrationResponse), {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
          })
        }
        return new Response(JSON.stringify({}), { status: 200 })
      })
    })

    async function loadAoiAndNavigateToTemporal() {
      render(<App />)
      await screen.findAllByText('No area selected')

      fireEvent.focus(screen.getByLabelText('Saved AOIs'))
      await waitFor(() => expect(screen.getByLabelText('Saved AOIs').querySelectorAll('option').length).toBeGreaterThan(1))
      fireEvent.change(screen.getByLabelText('Saved AOIs'), { target: { value: '1' } })

      // Navigate to Stage 04 CHANGE HISTORY via in-stage buttons (passive rail)
      await waitFor(() => expect(screen.getByTestId('proceed-to-observations-btn')).toBeInTheDocument())
      fireEvent.click(screen.getByTestId('proceed-to-observations-btn'))
      await waitFor(() => expect(screen.getByTestId('proceed-to-changes-btn')).toBeInTheDocument())
      fireEvent.click(screen.getByTestId('proceed-to-changes-btn'))
      await waitFor(() => expect(screen.getByTestId('proceed-to-history-btn')).toBeInTheDocument())
      fireEvent.click(screen.getByTestId('proceed-to-history-btn'))
      await waitFor(() => expect(screen.getByText('04 / Change history')).toBeInTheDocument())
    }

    it('6. Stage 04 displays automated orchestration button and purpose description', async () => {
      await loadAoiAndNavigateToTemporal()
      expect(screen.getByText('Examine how detected change behaves across observations.')).toBeInTheDocument()
      const runBtn = screen.getByTestId('run-automated-analysis-temporal-btn')
      expect(runBtn).toBeInTheDocument()
      expect(runBtn).toHaveTextContent('Run automated analysis')
      expect(runBtn).toBeEnabled()
    })

    it('7. Chronological timeline displays only eligible observations, excluding ineligible ones', async () => {
      await loadAoiAndNavigateToTemporal()
      // Out of 6 acquisitions, only 2 are eligible: 10 (usable 80%) and 15 (usable 90%)
      expect(screen.getByText('Available Eligible Observations (2)')).toBeInTheDocument()
      expect(screen.getByText(/ID #10/)).toBeInTheDocument()
      expect(screen.getByText(/ID #15/)).toBeInTheDocument()

      // Ineligible acquisitions are not in the timeline
      expect(screen.queryByText(/ID #11/)).not.toBeInTheDocument()
      expect(screen.queryByText(/ID #12/)).not.toBeInTheDocument()
      expect(screen.queryByText(/ID #13/)).not.toBeInTheDocument()
      expect(screen.queryByText(/ID #14/)).not.toBeInTheDocument()
    })

    it('8. While analysis is running from Stage 04, shows in-progress status', async () => {
      let resolveOrchestration: (value: Response) => void
      const orchestrationPromise = new Promise<Response>((resolve) => {
        resolveOrchestration = resolve
      })

      vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
        const url = String(input)
        if (url.includes('/api/v1/orchestrations/analyze')) {
          return orchestrationPromise
        }
        if (url.includes('/api/v1/aois')) {
          return new Response(JSON.stringify([{ aoi_id: 1, geometry: { type: 'Polygon', coordinates: [] } }]), { status: 200 })
        }
        if (url.includes('/api/v1/aoi/1') || url.endsWith('/api/v1/aoi')) {
          return new Response(JSON.stringify({ aoi_id: 1, geometry: { type: 'Polygon', coordinates: [] } }), { status: 200 })
        }
        if (url.includes('/api/v1/imagery/acquisitions')) {
          return new Response(JSON.stringify({ acquisitions: persistedAcquisitions }), { status: 200 })
        }
        return new Response(JSON.stringify({}), { status: 200 })
      })

      await loadAoiAndNavigateToTemporal()
      const runBtn = screen.getByTestId('run-automated-analysis-temporal-btn')
      fireEvent.click(runBtn)

      expect(screen.getByTestId('orchestration-progress')).toBeInTheDocument()
      expect(screen.getByText('Analyzing change history…')).toBeInTheDocument()

      resolveOrchestration!(new Response(JSON.stringify(mockOrchestrationResponse), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }))

      await waitFor(() => expect(screen.getByTestId('temporal-state-badge')).toHaveTextContent('Persistent'))
    })

    it('9. Successful automated analysis displays full change history results and candidate action', async () => {
      await loadAoiAndNavigateToTemporal()
      fireEvent.click(screen.getByTestId('run-automated-analysis-temporal-btn'))

      await waitFor(() => expect(screen.getByTestId('temporal-state-badge')).toHaveTextContent('Persistent'))
      expect(screen.getByText('Identified signals')).toBeInTheDocument()
      expect(screen.getByText('152 days')).toBeInTheDocument()
      expect(screen.getByText('85% quality support')).toBeInTheDocument()
      expect(screen.getByTestId('temporal-proceed-candidates-btn')).toBeInTheDocument()
      expect(screen.getByTestId('temporal-proceed-candidates-btn')).toHaveTextContent('Inspect Candidates (1) →')
    })
  })
})
