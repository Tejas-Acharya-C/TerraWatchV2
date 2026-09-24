import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import type { ImageryAcquisition, TemporalResult } from './lib/api'

vi.mock('./MapCanvas', () => ({
  default: () => <div data-testid="mock-map">Mock Map</div>,
}))

describe('Stage 04 / CHANGE HISTORY Simplification (Phase 6)', () => {
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

  let persistedAcquisitions: ImageryAcquisition[] = []
  let mockTemporalResponse: Partial<TemporalResult> | null = null
  let mockTemporalStatusCode: number = 200
  let mockTemporalErrorPayload: unknown = null

  beforeEach(() => {
    mockTemporalStatusCode = 200
    mockTemporalErrorPayload = null
    mockTemporalResponse = null

    persistedAcquisitions = [
      createMockAcquisition(10, '2024-01-15T10:00:00Z', 'usable', 80),
      createMockAcquisition(11, '2024-02-15T10:00:00Z', 'valid_unusable', 20),
      createMockAcquisition(12, '2024-03-15T10:00:00Z', 'failed', 0),
      createMockAcquisition(13, '2024-04-15T10:00:00Z', 'legacy_unassessed', 50),
      createMockAcquisition(14, '2024-05-15T10:00:00Z', 'usable', 0),
      createMockAcquisition(15, '2024-06-15T10:00:00Z', 'usable', 90),
      createMockAcquisition(16, '2024-07-15T10:00:00Z', 'usable', 85),
    ]

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
      if (url.includes('/api/v1/detections')) {
        return new Response(
          JSON.stringify({
            run_id: 1,
            before_acquisition_id: 10,
            after_acquisition_id: 15,
            detector_version: 'ndvi-absolute-difference-v1',
            threshold: 0.2,
            min_region_pixels: 4,
            region_count: 1,
            changed_pixel_count: 50,
            total_changed_area_m2: 5000,
            regions: [],
            created_at: '2024-06-15T10:00:00Z',
          }),
          { status: 200 }
        )
      }
      if (url.includes('/api/v1/temporal-analyses')) {
        if (mockTemporalStatusCode !== 200) {
          return new Response(JSON.stringify(mockTemporalErrorPayload || { detail: 'Missing detection' }), {
            status: mockTemporalStatusCode,
          })
        }
        const defaultResponse: TemporalResult = {
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
          seasonal_interpretation: 'seasonal_compatible',
          observations: [
            { acquisition_id: 10, item_id: 'S2_10', acquisition_datetime: '2024-01-15T10:00:00Z', sequence_index: 0 },
            { acquisition_id: 15, item_id: 'S2_15', acquisition_datetime: '2024-06-15T10:00:00Z', sequence_index: 1 },
          ],
          relationships: [
            {
              before_acquisition_id: 10,
              after_acquisition_id: 15,
              detection_run_id: 1,
              region_count: 2,
              changed_pixel_count: 120,
              elapsed_days: 152.0,
              quality_support: 0.85,
              quality_state: 'usable',
            },
          ],
          signals: [
            {
              signal_id: 501,
              geometry: { type: 'Polygon', coordinates: [[[77.42, 12.82], [77.43, 12.82], [77.43, 12.83], [77.42, 12.83], [77.42, 12.82]]] },
              support_count: 1,
              interval_count: 1,
              persistence_ratio: 1.0,
              recurrence_count: 0,
              transient_interval_count: 0,
              temporal_consistency: 1.0,
              matched_region_coverage: 0.9,
              first_change_datetime: '2024-01-15T10:00:00Z',
              last_supporting_datetime: '2024-06-15T10:00:00Z',
              state: 'persistent',
              quality_support: 0.85,
              seasonal_interpretation: 'seasonal_compatible',
            },
          ],
          created_at: '2024-06-15T10:00:00Z',
        }
        return new Response(JSON.stringify({ ...defaultResponse, ...mockTemporalResponse }), { status: 200 })
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

    await waitFor(() => expect(screen.getByTestId('proceed-to-observations-btn')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('proceed-to-observations-btn'))

    await waitFor(() => expect(screen.getByTestId('proceed-to-changes-btn')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('proceed-to-changes-btn'))
    await waitFor(() => expect(screen.getByText('03 / Changes')).toBeInTheDocument())

    await waitFor(() => expect(screen.getByTestId('proceed-to-history-btn')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('proceed-to-history-btn'))
    await waitFor(() => expect(screen.getByText('04 / Change history')).toBeInTheDocument())
  }

  it('1. Stage 04 renders as CHANGE HISTORY in navigation and panel header', async () => {
    await loadAoiAndNavigateToTemporal()
    expect(screen.queryByRole('button', { name: /04 CHANGE HISTORY/i })).not.toBeInTheDocument()
    expect(screen.getByLabelText('04 CHANGE HISTORY')).toBeInTheDocument()
    expect(screen.getByText('04 / Change history')).toBeInTheDocument()
    expect(screen.getByText('CHANGE HISTORY', { selector: '.panel-index' })).toBeInTheDocument()
  })

  it('2. The analyst-oriented description is shown', async () => {
    await loadAoiAndNavigateToTemporal()
    expect(screen.getByText('Examine how detected change behaves across observations.')).toBeInTheDocument()
  })

  it('25. Workflow rail is passive and in-stage buttons navigate between stages', async () => {
    await loadAoiAndNavigateToTemporal()
    // Rail is passive
    expect(screen.queryByRole('button', { name: /01 AREA/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /04 CHANGE HISTORY/i })).not.toBeInTheDocument()
    fireEvent.click(screen.getByLabelText('01 AREA'))
    expect(screen.getByText('04 / Change history')).toBeInTheDocument()

    // Navigate back to CHANGES stage via in-stage button
    fireEvent.click(screen.getByTestId('back-to-changes-btn'))
    await waitFor(() => expect(screen.getByText('03 / Changes')).toBeInTheDocument())

    // Navigate back to CHANGE HISTORY stage via in-stage button
    fireEvent.click(screen.getByTestId('proceed-to-history-btn'))
    await waitFor(() => expect(screen.getByText('04 / Change history')).toBeInTheDocument())
  })
})
