import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import App from './App'
import { STAGE_TITLES } from './workflow'
import type { AOIResponse, ImageryAcquisition } from './lib/api'

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
    item_id: 'S2A_MSIL2A_20240115T050141_N0510_R019_T43PGQ_20240115T072314',
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

describe('TerraWatch V2 — Simplification Phase 4: Stage 02 / OBSERVATIONS Simplification', () => {
  let mockAois: AOIResponse[] = []
  let mockAcquisitions: ImageryAcquisition[] = []

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
    mockAcquisitions = []

    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input)

      if (url.includes('/api/v1/aois')) {
        return new Response(JSON.stringify(mockAois), { status: 200 })
      }
      if (url.includes('/api/v1/aoi/1') || (url.includes('/api/v1/aoi') && !url.includes('/aois'))) {
        return new Response(JSON.stringify(mockAois[0]), { status: 200 })
      }
      if (url.includes('/api/v1/imagery/acquisitions')) {
        return new Response(JSON.stringify({ aoi_id: 1, total: mockAcquisitions.length, acquisitions: mockAcquisitions }), { status: 200 })
      }
      return new Response(JSON.stringify({}), { status: 200 })
    })
  })

  async function loadSavedAreaAndGoToObservations() {
    render(<App />)
    fireEvent.focus(screen.getByLabelText('Saved AOIs'))
    await waitFor(() => expect(screen.getByLabelText('Saved AOIs').querySelectorAll('option').length).toBeGreaterThan(1))
    fireEvent.change(screen.getByLabelText('Saved AOIs'), { target: { value: '1' } })
    const proceedBtn = await screen.findByTestId('proceed-to-observations-btn')
    fireEvent.click(proceedBtn)
  }

  it('1. Stage 02 is labeled OBSERVATIONS in navigation and panel header', async () => {
    await loadSavedAreaAndGoToObservations()
    expect(STAGE_TITLES.IMAGERY).toBe('OBSERVATIONS')
    expect(screen.getByText('02 / Observations')).toBeInTheDocument()
    expect(screen.getAllByText('OBSERVATIONS').length).toBeGreaterThan(1)
  })

  it('2. Analyst-facing description is correct', async () => {
    await loadSavedAreaAndGoToObservations()
    expect(screen.getByText('Choose the satellite observations available for this area and period.')).toBeInTheDocument()
  })

  it('3. No AOI creation, edit, clear, or duplicate AOI selection controls appear in Stage 02', async () => {
    await loadSavedAreaAndGoToObservations()
    expect(screen.queryByRole('button', { name: 'Draw area' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Edit area' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Save as new area' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Clear AOI' })).not.toBeInTheDocument()
    // Stage 02 should not contain duplicate Saved AOIs selector
    expect(screen.queryByLabelText('Saved AOIs')).not.toBeInTheDocument()
    // But shows active area summary
    expect(screen.getByText('Active area')).toBeInTheDocument()
    expect(screen.getByText('Area defined in Karnataka')).toBeInTheDocument()
  })

  it('4. No raw FSM tokens appear in primary Stage 02 UI', async () => {
    await loadSavedAreaAndGoToObservations()
    expect(screen.queryByText('NO_AOI')).not.toBeInTheDocument()
    expect(screen.queryByText('EDIT_DIRTY')).not.toBeInTheDocument()
    expect(screen.queryByText('AOI_SELECTED')).not.toBeInTheDocument()
  })

  it('5. From and To date controls are clean and not duplicated', async () => {
    await loadSavedAreaAndGoToObservations()
    expect(screen.getByLabelText('Observation start date')).toBeInTheDocument()
    expect(screen.getByLabelText('Observation end date')).toBeInTheDocument()
    expect(screen.getAllByLabelText('Observation start date').length).toBe(1)
    expect(screen.getAllByLabelText('Observation end date').length).toBe(1)
    expect(screen.getByText('Requested observation period')).toBeInTheDocument()
  })

  it('6. "Find observations" is the analyst-facing discovery action', async () => {
    await loadSavedAreaAndGoToObservations()
    const findBtn = screen.getByRole('button', { name: 'Find observations' })
    expect(findBtn).toBeInTheDocument()
    expect(findBtn).toHaveTextContent('Find observations')
    expect(screen.queryByText('Search STAC')).not.toBeInTheDocument()
    expect(screen.queryByText('Query catalog')).not.toBeInTheDocument()
  })

  it('7. Actual observation dates are distinguished from requested dates', async () => {
    mockAcquisitions = [
      makeAcquisition({
        acquisition_id: 10,
        acquisition_datetime: '2024-04-15T10:30:00Z',
        observation_state: 'usable',
      }),
    ]
    await loadSavedAreaAndGoToObservations()

    // Requested period form is clearly labeled
    expect(screen.getByText('Requested observation period')).toBeInTheDocument()
    // Observation card has eyebrow distinguishing actual observation date
    expect(screen.getByText('Actual observation date')).toBeInTheDocument()
    expect(screen.getByText('15 Apr 2024')).toBeInTheDocument()
  })

  it('8. Usable observations are presented as usable with Acquired badge', async () => {
    mockAcquisitions = [
      makeAcquisition({
        acquisition_id: 11,
        observation_state: 'usable',
      }),
    ]
    await loadSavedAreaAndGoToObservations()

    const itemCard = screen.getByTestId('observation-item-11')
    expect(itemCard).toHaveClass('observation-card--usable')
    expect(itemCard).toHaveTextContent('Usable')
    expect(itemCard).toHaveTextContent('Acquired')
  })

  it('9. Unusable observations remain visible and show their persisted quality limitation', async () => {
    mockAcquisitions = [
      makeAcquisition({
        acquisition_id: 12,
        acquisition_datetime: '2024-09-07T10:00:00Z',
        observation_state: 'valid_unusable',
        quality_reason: 'insufficient_usable_pixels',
      }),
    ]
    await loadSavedAreaAndGoToObservations()

    const itemCard = screen.getByTestId('observation-item-12')
    expect(itemCard).toHaveClass('observation-card--unusable')
    expect(itemCard).toHaveTextContent('Not usable')
    expect(itemCard).toHaveTextContent('Cloud/shadow coverage exceeds the usable limit')
    expect(itemCard).toHaveTextContent('Unavailable for change detection')
  })

  it('10. When only 1 usable observation exists, Next is disabled with requirement hint', async () => {
    const usableObs = makeAcquisition({ acquisition_id: 21, observation_state: 'usable', acquisition_datetime: '2024-01-01T00:00:00Z' })
    const unusableObs = makeAcquisition({ acquisition_id: 22, observation_state: 'valid_unusable', quality_reason: 'insufficient_usable_pixels', acquisition_datetime: '2024-02-01T00:00:00Z' })
    mockAcquisitions = [usableObs, unusableObs]

    await loadSavedAreaAndGoToObservations()

    const changeBtn = screen.getByTestId('proceed-to-changes-btn')
    expect(changeBtn).toBeDisabled()
    expect(screen.getByText('At least 2 usable observations required')).toBeInTheDocument()
  })

  it('11. Acquired state is clearly presented on observation cards', async () => {
    mockAcquisitions = [
      makeAcquisition({ acquisition_id: 30, observation_state: 'usable' }),
    ]
    await loadSavedAreaAndGoToObservations()

    expect(screen.getByText('Acquired observations')).toBeInTheDocument()
    expect(screen.getByText('Acquired')).toBeInTheDocument()
  })

  it('12. Finding observations does not automatically run detection', async () => {
    mockAcquisitions = [
      makeAcquisition({ acquisition_id: 41, observation_state: 'usable' }),
      makeAcquisition({ acquisition_id: 42, observation_state: 'usable', acquisition_datetime: '2024-02-01T00:00:00Z' }),
    ]
    const detectSpy = vi.fn()

    render(<App />)
    fireEvent.focus(screen.getByLabelText('Saved AOIs'))
    await waitFor(() => expect(screen.getByLabelText('Saved AOIs').querySelectorAll('option').length).toBeGreaterThan(1))
    fireEvent.change(screen.getByLabelText('Saved AOIs'), { target: { value: '1' } })
    await waitFor(() => expect(screen.getByTestId('proceed-to-observations-btn')).toBeInTheDocument())

    // Detection was never called
    expect(detectSpy).not.toHaveBeenCalled()
    expect(screen.queryByText('change regions detected')).not.toBeInTheDocument()
  })

  it('13. Acquiring observations does not automatically select Before/After pairs or run analysis', async () => {
    const obs1 = makeAcquisition({ acquisition_id: 51, observation_state: 'usable', acquisition_datetime: '2024-01-01T00:00:00Z' })
    const obs2 = makeAcquisition({ acquisition_id: 52, observation_state: 'usable', acquisition_datetime: '2024-02-01T00:00:00Z' })
    mockAcquisitions = [obs1, obs2]

    await loadSavedAreaAndGoToObservations()
    const changeBtn = screen.getByTestId('proceed-to-changes-btn')
    fireEvent.click(changeBtn)

    expect(screen.getByRole('button', { name: 'Run automated analysis' })).toBeInTheDocument()
    expect(screen.queryByTestId('orchestration-summary')).not.toBeInTheDocument()
  })

  it('14. Acquiring observations does not automatically run temporal analysis', async () => {
    mockAcquisitions = [
      makeAcquisition({ acquisition_id: 61, observation_state: 'usable', acquisition_datetime: '2024-01-01T00:00:00Z' }),
      makeAcquisition({ acquisition_id: 62, observation_state: 'usable', acquisition_datetime: '2024-02-01T00:00:00Z' }),
      makeAcquisition({ acquisition_id: 63, observation_state: 'usable', acquisition_datetime: '2024-03-01T00:00:00Z' }),
    ]
    await loadSavedAreaAndGoToObservations()

    expect(screen.queryByText('temporal signals ready')).not.toBeInTheDocument()
    expect(screen.queryByText('Analyzing temporal behavior')).not.toBeInTheDocument()
  })

  it('15. One usable observation is correctly represented as insufficient for pairwise change detection', async () => {
    mockAcquisitions = [
      makeAcquisition({ acquisition_id: 71, observation_state: 'usable' }),
      makeAcquisition({ acquisition_id: 72, observation_state: 'valid_unusable', quality_reason: 'insufficient_usable_pixels' }),
    ]
    await loadSavedAreaAndGoToObservations()

    expect(screen.getByText('1 usable observation available.')).toBeInTheDocument()
    expect(screen.getByText('Two observations are required for change detection.')).toBeInTheDocument()
  })

  it('16. Historical observations do not automatically become current downstream selections', async () => {
    mockAcquisitions = [
      makeAcquisition({ acquisition_id: 81, observation_state: 'usable', acquisition_datetime: '2024-01-01T00:00:00Z' }),
      makeAcquisition({ acquisition_id: 82, observation_state: 'usable', acquisition_datetime: '2024-02-01T00:00:00Z' }),
    ]
    render(<App />)
    fireEvent.focus(screen.getByLabelText('Saved AOIs'))
    await waitFor(() => expect(screen.getByLabelText('Saved AOIs').querySelectorAll('option').length).toBeGreaterThan(1))
    fireEvent.change(screen.getByLabelText('Saved AOIs'), { target: { value: '1' } })
    await waitFor(() => expect(screen.getByTestId('proceed-to-observations-btn')).toBeInTheDocument())

    // Navigating to downstream CHANGES shows ready state without pre-executed results
    fireEvent.click(screen.getByTestId('proceed-to-observations-btn'))
    await waitFor(() => expect(screen.getByText('02 / Observations')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('proceed-to-changes-btn'))
    expect(screen.getByRole('button', { name: 'Run automated analysis' })).toBeInTheDocument()
    expect(screen.queryByTestId('orchestration-summary')).not.toBeInTheDocument()
  })

  it('17. Fresh session remains blank without persisting state to storage', () => {
    render(<App />)
    expect(localStorage.length).toBe(0)
    expect(sessionStorage.length).toBe(0)
    expect(screen.getByTestId('aoi-state')).toHaveTextContent('No area selected')
  })

  it('18. Technical IDs are not primary analyst-facing labels and remain inside technical inspection', async () => {
    const rawItemId = 'S2A_MSIL2A_20240115T050141_N0510_R019_T43PGQ_20240115T072314'
    mockAcquisitions = [
      makeAcquisition({
        acquisition_id: 91,
        item_id: rawItemId,
        observation_state: 'usable',
      }),
    ]
    await loadSavedAreaAndGoToObservations()

    const itemCard = screen.getByTestId('observation-item-91')
    // Primary header has the formatted date, not raw item ID
    const dateHeading = itemCard.querySelector('.observation-card__date')
    expect(dateHeading).toHaveTextContent('15 Jan 2024')

    // Technical details holds the raw item ID
    const details = itemCard.querySelector('details')
    expect(details).toBeInTheDocument()
    expect(details).toHaveTextContent(rawItemId)
  })

  it('19. Phase 1 temporal eligibility behavior remains intact', async () => {
    render(<App />)
    // Select All and Deselect All remain removed
    expect(screen.queryByText('Select all')).not.toBeInTheDocument()
    expect(screen.queryByText('Deselect all')).not.toBeInTheDocument()
  })

  it('20. Workflow rail is passive and in-stage buttons navigate between stages', async () => {
    await loadSavedAreaAndGoToObservations()
    expect(screen.getByText('02 / Observations')).toBeInTheDocument()

    // Rail items are passive and non-interactive
    expect(screen.queryByRole('button', { name: /01 AREA/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /02 OBSERVATIONS/i })).not.toBeInTheDocument()
    fireEvent.click(screen.getByLabelText('01 AREA'))
    expect(screen.getByText('02 / Observations')).toBeInTheDocument()

    // Upstream navigation back to AREA via in-stage button
    fireEvent.click(screen.getByTestId('back-to-area-btn'))
    expect(screen.getByText('01 / Area')).toBeInTheDocument()

    // Forward back to OBSERVATIONS via in-stage button
    fireEvent.click(screen.getByTestId('proceed-to-observations-btn'))
    expect(screen.getByText('02 / Observations')).toBeInTheDocument()
  })

  it('21. Phase 3 AREA behavior remains intact (rectangle draw, save, etc.)', () => {
    render(<App />)
    expect(screen.getByRole('button', { name: 'Draw area' })).toBeInTheDocument()
    expect(screen.getByText('Define the area you want to investigate.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Clear AOI' })).not.toBeInTheDocument()
  })

  it('22. Observation progress and estimated remaining time are displayed during acquisition', async () => {
    let enqueueFn: ((chunk: string) => void) | null = null
    let closeFn: (() => void) | null = null

    const customStream = new ReadableStream({
      start(controller) {
        enqueueFn = (chunk: string) => controller.enqueue(new TextEncoder().encode(chunk))
        closeFn = () => controller.close()
      },
    })

    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.includes('/api/v1/imagery/acquisitions') && init?.method === 'POST') {
        return new Response(customStream, {
          status: 200,
          headers: { 'Content-Type': 'application/x-ndjson' },
        })
      }
      if (url.includes('/api/v1/imagery/acquisitions')) {
        return new Response(JSON.stringify({ aoi_id: 1, total: 1, acquisitions: [makeAcquisition({ acquisition_id: 1 })] }), { status: 200 })
      }
      if (url.includes('/api/v1/aois')) {
        return new Response(JSON.stringify(mockAois), { status: 200 })
      }
      if (url.includes('/api/v1/aoi/1') || (url.includes('/api/v1/aoi') && !url.includes('/aois'))) {
        return new Response(JSON.stringify(mockAois[0]), { status: 200 })
      }
      return new Response(JSON.stringify({}), { status: 200 })
    })

    let mockTime = 1000000
    vi.spyOn(Date, 'now').mockImplementation(() => mockTime)

    await loadSavedAreaAndGoToObservations()

    fireEvent.change(screen.getByLabelText('Observation start date'), { target: { value: '2024-01-01' } })
    fireEvent.change(screen.getByLabelText('Observation end date'), { target: { value: '2024-04-01' } })

    const findBtn = screen.getByRole('button', { name: 'Find observations' })
    fireEvent.click(findBtn)

    await waitFor(() => expect(screen.getAllByText('Finding observations…').length).toBeGreaterThan(0))

    // Step 1: 1 of 25 completed (elapsed 5s) -> initial "Estimating remaining time…"
    mockTime += 5000
    enqueueFn!(JSON.stringify({ type: 'progress', current: 1, total: 25, item_id: 'S2_1', state: 'completed' }) + '\n')
    await waitFor(() => {
      expect(screen.getAllByText(/Processing observations… 1 of 25 completed/).length).toBeGreaterThan(0)
      expect(screen.getAllByText(/Estimating remaining time…/).length).toBeGreaterThan(0)
    })

    // Step 2: 5 of 25 completed (elapsed 25s total) -> rate = 5/25 = 0.2 obs/s -> remaining 20 / 0.2 = 100s -> ~2 min
    mockTime += 20000
    enqueueFn!(JSON.stringify({ type: 'progress', current: 5, total: 25, item_id: 'S2_5', state: 'completed' }) + '\n')
    await waitFor(() => {
      expect(screen.getAllByText(/Processing observations… 5 of 25 completed/).length).toBeGreaterThan(0)
      expect(screen.getAllByText(/Estimated time remaining: ~2 min/).length).toBeGreaterThan(0)
    })

    // Step 3: Complete -> clears progress and ETA
    enqueueFn!(JSON.stringify({ type: 'complete', current: 25, total: 25, result: makeAcquisition({ acquisition_id: 1 }) }) + '\n')
    closeFn!()

    await waitFor(() => expect(screen.getByRole('heading', { name: 'Compare observations' })).toBeInTheDocument())
    expect(screen.queryByText(/Processing observations/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Estimated time remaining/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Estimating remaining time/)).not.toBeInTheDocument()
  })

  it('23. Acquisition failure clears progress and ETA, displaying failure state', async () => {
    let enqueueFn: ((chunk: string) => void) | null = null
    let closeFn: (() => void) | null = null

    const customStream = new ReadableStream({
      start(controller) {
        enqueueFn = (chunk: string) => controller.enqueue(new TextEncoder().encode(chunk))
        closeFn = () => controller.close()
      },
    })

    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.includes('/api/v1/imagery/acquisitions') && init?.method === 'POST') {
        return new Response(customStream, {
          status: 200,
          headers: { 'Content-Type': 'application/x-ndjson' },
        })
      }
      if (url.includes('/api/v1/aois')) {
        return new Response(JSON.stringify(mockAois), { status: 200 })
      }
      if (url.includes('/api/v1/aoi/1') || (url.includes('/api/v1/aoi') && !url.includes('/aois'))) {
        return new Response(JSON.stringify(mockAois[0]), { status: 200 })
      }
      return new Response(JSON.stringify({}), { status: 200 })
    })

    await loadSavedAreaAndGoToObservations()
    fireEvent.change(screen.getByLabelText('Observation start date'), { target: { value: '2024-01-01' } })
    fireEvent.change(screen.getByLabelText('Observation end date'), { target: { value: '2024-04-01' } })
    fireEvent.click(screen.getByRole('button', { name: 'Find observations' }))

    await waitFor(() => expect(screen.getAllByText('Finding observations…').length).toBeGreaterThan(0))

    enqueueFn!(JSON.stringify({ type: 'progress', current: 1, total: 10, item_id: 'S2_1', state: 'screened_unusable' }) + '\n')
    await waitFor(() => expect(screen.getAllByText(/Processing observations… 1 of 10 completed/).length).toBeGreaterThan(0))

    enqueueFn!(JSON.stringify({ type: 'error', code: 'NoSuitableImageryError', message: 'No usable Sentinel-2 observation was available' }) + '\n')
    closeFn!()

    await waitFor(() => expect(screen.getByText('No matching observations')).toBeInTheDocument())
    expect(screen.queryByText(/Processing observations/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Estimated time remaining/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Estimating remaining time/)).not.toBeInTheDocument()
  })

  it('24. Stale/obsolete acquisition progress events do not update current workflow after AOI switch', async () => {
    let enqueueFn: ((chunk: string) => void) | null = null

    const customStream = new ReadableStream({
      start(controller) {
        enqueueFn = (chunk: string) => controller.enqueue(new TextEncoder().encode(chunk))
      },
    })

    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.includes('/api/v1/imagery/acquisitions') && init?.method === 'POST') {
        return new Response(customStream, {
          status: 200,
          headers: { 'Content-Type': 'application/x-ndjson' },
        })
      }
      if (url.includes('/api/v1/aois')) {
        return new Response(JSON.stringify(mockAois), { status: 200 })
      }
      if (url.includes('/api/v1/aoi/')) {
        return new Response(JSON.stringify(mockAois[0]), { status: 200 })
      }
      return new Response(JSON.stringify({}), { status: 200 })
    })

    await loadSavedAreaAndGoToObservations()
    fireEvent.change(screen.getByLabelText('Observation start date'), { target: { value: '2024-01-01' } })
    fireEvent.change(screen.getByLabelText('Observation end date'), { target: { value: '2024-04-01' } })
    fireEvent.click(screen.getByRole('button', { name: 'Find observations' }))

    await waitFor(() => expect(screen.getAllByText('Finding observations…').length).toBeGreaterThan(0))

    // Switch to Stage 01 and deselect area
    fireEvent.click(screen.getByTestId('back-to-area-btn'))
    const aoiSelect = screen.getByLabelText('Saved AOIs')
    fireEvent.change(aoiSelect, { target: { value: '' } })

    // Stale progress arrives from previous in-flight request
    enqueueFn!(JSON.stringify({ type: 'progress', current: 15, total: 25, item_id: 'S2_OLD', state: 'completed' }) + '\n')

    // Must NOT adopt the stale progress
    expect(screen.queryByText(/Processing observations… 15 of 25 completed/)).not.toBeInTheDocument()
  })

  describe('Stage 02 Next Prerequisite Validation', () => {
    it('1. >=2 total observations but 0 usable -> Next disabled', async () => {
      const unusable1 = makeAcquisition({ acquisition_id: 101, observation_state: 'valid_unusable', quality_reason: 'cloud_cover' })
      const unusable2 = makeAcquisition({ acquisition_id: 102, observation_state: 'valid_unusable', quality_reason: 'shadow' })
      mockAcquisitions = [unusable1, unusable2]

      await loadSavedAreaAndGoToObservations()
      const nextBtn = screen.getByTestId('proceed-to-changes-btn')
      expect(nextBtn).toBeDisabled()
      expect(screen.getByText('At least 2 usable observations required')).toBeInTheDocument()
    })

    it('2. >=2 total observations but only 1 usable -> Next disabled', async () => {
      const usable = makeAcquisition({ acquisition_id: 201, observation_state: 'usable' })
      const unusable1 = makeAcquisition({ acquisition_id: 202, observation_state: 'valid_unusable', quality_reason: 'cloud_cover' })
      const unusable2 = makeAcquisition({ acquisition_id: 203, observation_state: 'valid_unusable', quality_reason: 'shadow' })
      mockAcquisitions = [usable, unusable1, unusable2]

      await loadSavedAreaAndGoToObservations()
      const nextBtn = screen.getByTestId('proceed-to-changes-btn')
      expect(nextBtn).toBeDisabled()
      expect(screen.getByText('At least 2 usable observations required')).toBeInTheDocument()
    })

    it('3. >=2 usable observations -> Next enabled', async () => {
      const usable1 = makeAcquisition({ acquisition_id: 301, observation_state: 'usable', acquisition_datetime: '2024-01-01T00:00:00Z' })
      const usable2 = makeAcquisition({ acquisition_id: 302, observation_state: 'usable', acquisition_datetime: '2024-02-01T00:00:00Z' })
      const unusable = makeAcquisition({ acquisition_id: 303, observation_state: 'valid_unusable', quality_reason: 'cloud_cover' })
      mockAcquisitions = [usable1, usable2, unusable]

      await loadSavedAreaAndGoToObservations()
      const nextBtn = screen.getByTestId('proceed-to-changes-btn')
      expect(nextBtn).not.toBeDisabled()
      expect(screen.queryByText('At least 2 usable observations required')).not.toBeInTheDocument()
    })

    it('4. No observations -> Next disabled', async () => {
      mockAcquisitions = []

      await loadSavedAreaAndGoToObservations()
      const nextBtn = screen.getByTestId('proceed-to-changes-btn')
      expect(nextBtn).toBeDisabled()
      expect(screen.getByText('At least 2 usable observations required')).toBeInTheDocument()
    })
  })
})
