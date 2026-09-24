import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import App from './App'
import type { AOIResponse, ImageryAcquisition } from './lib/api'

vi.mock('./MapCanvas', () => ({
  default: (props: {
    geometry?: { type: string; coordinates: unknown } | null
    draftGeometry?: { type: string; coordinates: unknown } | null
    draftPositions?: number[][]
    drawing?: boolean
    editing?: boolean
    onMapClick?: (pos: number[]) => void
    onDraftChange?: (geom: { type: string; coordinates: number[][][] }) => void
  }) => (
    <div data-testid="mock-map">
      <span data-testid="mock-persisted-geom">{props.geometry ? JSON.stringify(props.geometry) : 'null'}</span>
      <span data-testid="mock-draft-geom">{props.draftGeometry ? JSON.stringify(props.draftGeometry) : 'null'}</span>
      <span data-testid="mock-draft-count">{props.draftPositions?.length ?? 0}</span>
      <button type="button" onClick={() => props.onMapClick?.([75.5, 13.5])}>Simulate map point 1</button>
      <button type="button" onClick={() => props.onMapClick?.([75.8, 13.8])}>Simulate map point 2</button>
      <button
        type="button"
        data-testid="simulate-edit-drag"
        onClick={() => props.onDraftChange?.({ type: 'Polygon', coordinates: [[[75.0, 13.0], [75.9, 13.0], [75.9, 13.9], [75.0, 13.9], [75.0, 13.0]]] })}
      >
        Simulate edit drag
      </button>
    </div>
  ),
}))

const karnatakaPolygon = {
  type: 'Polygon' as const,
  coordinates: [[[75.0, 13.0], [75.2, 13.0], [75.2, 13.2], [75.0, 13.2], [75.0, 13.0]]],
}

let persistedAois: AOIResponse[] = []

function makeAcquisition(overrides: Partial<ImageryAcquisition>): ImageryAcquisition {
  return {
    acquisition_id: 1,
    aoi_id: 1,
    requested_start_datetime: '2024-01-01T00:00:00Z',
    requested_end_datetime: '2024-02-01T00:00:00Z',
    item_id: 'S2_OBS_TEST',
    collection: 'sentinel-2-l2a',
    acquisition_datetime: '2024-01-15T00:00:00Z',
    assets: [],
    prepared_path: 'obs.tif',
    raster: { crs: 'EPSG:32643', transform: [], width: 100, height: 100, resolution: [10, 10], bounds: [], count: 2, dtype: 'uint16', nodata: null },
    source_metadata: {},
    created_at: '2024-01-15T00:00:00Z',
    observation_state: 'usable',
    quality_metrics: {
      usable_pixel_fraction: 0.95,
      cloud_fraction: 0.02,
    },
    ...overrides,
  }
}

beforeEach(() => {
  persistedAois = []

  vi.restoreAllMocks()

  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = init?.method ?? 'GET'

    if (url.includes('/api/v1/aois')) {
      return new Response(JSON.stringify(persistedAois), { status: 200 })
    }

    if (method === 'POST' && url.includes('/api/v1/aoi')) {
      const body = JSON.parse(String(init?.body))
      const nextId = (persistedAois.length ? Math.max(...persistedAois.map((a) => a.aoi_id)) : 0) + 1
      const newAoi: AOIResponse = {
        aoi_id: nextId,
        geometry: body,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      }
      persistedAois.push(newAoi)
      return new Response(JSON.stringify(newAoi), { status: 200 })
    }

    if (url.includes('/api/v1/aoi/')) {
      const match = url.match(/\/aoi\/(\d+)/)
      const id = match ? Number(match[1]) : 1
      const found = persistedAois.find((a) => a.aoi_id === id)
      if (found) return new Response(JSON.stringify(found), { status: 200 })
      return new Response(JSON.stringify({ code: 'NotFoundError', message: 'AOI not found' }), { status: 404 })
    }

    if (url.includes('/api/v1/imagery/acquisitions')) {
      return new Response(JSON.stringify({
        aoi_id: 1,
        total: 2,
        acquisitions: [
          makeAcquisition({ acquisition_id: 101, acquisition_datetime: '2024-01-01T00:00:00Z' }),
          makeAcquisition({ acquisition_id: 102, acquisition_datetime: '2024-02-01T00:00:00Z' }),
        ],
      }), { status: 200 })
    }

    return new Response(JSON.stringify({}), { status: 200 })
  })
})

describe('TerraWatch V2 Phase 3: Stage 01 / AREA Simplification', () => {
  it('1. AREA uses the analyst-facing action terminology', async () => {
    render(<App />)
    await screen.findAllByText('No area selected')

    // Initial state offers "Draw area"
    expect(screen.getByRole('button', { name: 'Draw area' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Draw AOI' })).not.toBeInTheDocument()

    // Begin drawing
    fireEvent.click(screen.getByRole('button', { name: 'Draw area' }))
    expect(screen.getByRole('button', { name: 'Finish rectangle' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Discard draft' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Save as new area' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Save AOI' })).not.toBeInTheDocument()
  })

  it('2. No raw FSM tokens appear in primary user-facing text', async () => {
    render(<App />)
    await screen.findAllByText('No area selected')

    const bodyText = document.body.textContent ?? ''
    expect(bodyText).not.toContain('NO_AOI')
    expect(bodyText).not.toContain('AOI_SELECTED')
    expect(bodyText).not.toContain('EDIT_DIRTY')

    const stateChip = screen.getByTestId('aoi-state')
    expect(stateChip).toHaveAttribute('data-state', 'NO_AOI')
    expect(stateChip).toHaveTextContent('No area selected')
    expect(stateChip).not.toHaveTextContent('NO_AOI')
  })

  it('3. No destructive "Clear AOI" action exists in the document', async () => {
    persistedAois = [{ aoi_id: 1, geometry: karnatakaPolygon, created_at: '2024-01-01T00:00:00Z', updated_at: '2024-01-01T00:00:00Z' }]
    render(<App />)
    await screen.findAllByText('No area selected')

    // Select saved area
    fireEvent.focus(screen.getByLabelText('Saved AOIs'))
    await waitFor(() => expect(screen.getByLabelText('Saved AOIs').querySelectorAll('option').length).toBeGreaterThan(1))
    fireEvent.change(screen.getByLabelText('Saved AOIs'), { target: { value: '1' } })
    await waitFor(() => expect(screen.getAllByText('Area active', { exact: false }).length).toBeGreaterThan(0))

    // Confirm Clear AOI does not exist
    expect(screen.queryByRole('button', { name: 'Clear AOI' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /clear/i })).not.toBeInTheDocument()
  })

  it('4. New AOI creation remains rectangle-only via two map points', async () => {
    render(<App />)
    await screen.findAllByText('No area selected')

    fireEvent.click(screen.getByRole('button', { name: 'Draw area' }))
    const saveBtn = screen.getByRole('button', { name: 'Save as new area' })
    expect(saveBtn).toBeDisabled()

    // Place 1st corner
    fireEvent.click(screen.getByRole('button', { name: 'Simulate map point 1' }))
    expect(screen.getByTestId('mock-draft-count')).toHaveTextContent('1')
    expect(saveBtn).toBeDisabled()

    // Place 2nd corner
    fireEvent.click(screen.getByRole('button', { name: 'Simulate map point 2' }))
    expect(screen.getByTestId('mock-draft-count')).toHaveTextContent('2')

    // Complete rectangle
    fireEvent.click(screen.getByRole('button', { name: 'Finish rectangle' }))
    expect(screen.getByTestId('mock-draft-geom').textContent).toContain('Polygon')
    expect(screen.getByRole('button', { name: 'Save as new area' })).toBeEnabled()
  })

  it('5. Draft geometry does not overwrite persisted AOI before save', async () => {
    persistedAois = [{ aoi_id: 1, geometry: karnatakaPolygon, created_at: '2024-01-01T00:00:00Z', updated_at: '2024-01-01T00:00:00Z' }]
    render(<App />)
    await screen.findAllByText('No area selected')

    fireEvent.focus(screen.getByLabelText('Saved AOIs'))
    await waitFor(() => expect(screen.getByLabelText('Saved AOIs').querySelectorAll('option').length).toBeGreaterThan(1))
    fireEvent.change(screen.getByLabelText('Saved AOIs'), { target: { value: '1' } })
    await waitFor(() => expect(screen.getAllByText('Area active', { exact: false }).length).toBeGreaterThan(0))

    const persistedBeforeEdit = screen.getByTestId('mock-persisted-geom').textContent
    expect(persistedBeforeEdit).toContain('75.2')

    // Enter edit mode and simulate dragging handles
    fireEvent.click(screen.getByRole('button', { name: 'Edit area' }))
    fireEvent.click(screen.getByRole('button', { name: 'Simulate edit drag' }))

    // Draft is updated with 75.9, but persisted remains unchanged
    expect(screen.getByTestId('mock-draft-geom').textContent).toContain('75.9')
    expect(screen.getByTestId('mock-persisted-geom').textContent).toBe(persistedBeforeEdit)
  })

  it('6. Cancel/discard restores the persisted AOI and removes the draft', async () => {
    persistedAois = [{ aoi_id: 1, geometry: karnatakaPolygon, created_at: '2024-01-01T00:00:00Z', updated_at: '2024-01-01T00:00:00Z' }]
    render(<App />)
    await screen.findAllByText('No area selected')

    fireEvent.focus(screen.getByLabelText('Saved AOIs'))
    await waitFor(() => expect(screen.getByLabelText('Saved AOIs').querySelectorAll('option').length).toBeGreaterThan(1))
    fireEvent.change(screen.getByLabelText('Saved AOIs'), { target: { value: '1' } })
    await waitFor(() => expect(screen.getAllByText('Area active', { exact: false }).length).toBeGreaterThan(0))

    const originalPersisted = screen.getByTestId('mock-persisted-geom').textContent

    // Enter edit mode, dirty draft, then discard draft
    fireEvent.click(screen.getByRole('button', { name: 'Edit area' }))
    fireEvent.click(screen.getByRole('button', { name: 'Simulate edit drag' }))
    expect(screen.getByTestId('mock-draft-geom').textContent).not.toBe('null')

    fireEvent.click(screen.getByRole('button', { name: 'Discard draft' }))

    // Draft removed, persisted restored
    expect(screen.getByTestId('mock-draft-geom').textContent).toBe('null')
    expect(screen.getByTestId('mock-persisted-geom').textContent).toBe(originalPersisted)
    expect(screen.getByTestId('aoi-state')).toHaveAttribute('data-state', 'AOI_SELECTED')
    expect(screen.getByTestId('aoi-state')).toHaveTextContent('Area active')
    expect(screen.queryByTestId('active-aoi-badge')).not.toBeInTheDocument()
  })

  it('7. "Save as new area" wording is used and creates an append-only area', async () => {
    render(<App />)
    await screen.findAllByText('No area selected')

    fireEvent.click(screen.getByRole('button', { name: 'Draw area' }))
    fireEvent.click(screen.getByRole('button', { name: 'Simulate map point 1' }))
    fireEvent.click(screen.getByRole('button', { name: 'Simulate map point 2' }))
    fireEvent.click(screen.getByRole('button', { name: 'Finish rectangle' }))

    const saveBtn = screen.getByRole('button', { name: 'Save as new area' })
    expect(saveBtn).toBeInTheDocument()
    fireEvent.click(saveBtn)

    await waitFor(() => expect(screen.getAllByText('Area active', { exact: false }).length).toBeGreaterThan(0))
    expect(screen.getByTestId('mock-persisted-geom').textContent).not.toBe('null')
  })

  it('8. Failed save preserves the draft and leaves the persisted AOI unchanged', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      if (method === 'POST' && url.includes('/api/v1/aoi')) {
        return new Response(JSON.stringify({
          code: 'AOIOutsideKarnatakaError',
          message: 'Geometry must be entirely within Karnataka state boundary',
        }), { status: 422 })
      }
      return new Response(JSON.stringify([]), { status: 200 })
    })

    render(<App />)
    await screen.findAllByText('No area selected')

    fireEvent.click(screen.getByRole('button', { name: 'Draw area' }))
    fireEvent.click(screen.getByRole('button', { name: 'Simulate map point 1' }))
    fireEvent.click(screen.getByRole('button', { name: 'Simulate map point 2' }))
    fireEvent.click(screen.getByRole('button', { name: 'Finish rectangle' }))

    const draftBeforeSave = screen.getByTestId('mock-draft-geom').textContent
    fireEvent.click(screen.getByRole('button', { name: 'Save as new area' }))

    await waitFor(() => expect(screen.getByTestId('aoi-state')).toHaveAttribute('data-state', 'SAVE_FAILED'))

    // Draft remains visible for correction
    expect(screen.getByTestId('mock-draft-geom').textContent).toBe(draftBeforeSave)
    // Persisted remains null
    expect(screen.getByTestId('mock-persisted-geom').textContent).toBe('null')
  })

  it('9. Karnataka validation failure is shown clearly without raw exception names', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      if (method === 'POST' && url.includes('/api/v1/aoi')) {
        return new Response(JSON.stringify({
          code: 'AOIOutsideKarnatakaError',
          message: 'Geometry must be entirely within Karnataka state boundary',
        }), { status: 422 })
      }
      return new Response(JSON.stringify([]), { status: 200 })
    })

    render(<App />)
    await screen.findAllByText('No area selected')

    fireEvent.click(screen.getByRole('button', { name: 'Draw area' }))
    fireEvent.click(screen.getByRole('button', { name: 'Simulate map point 1' }))
    fireEvent.click(screen.getByRole('button', { name: 'Simulate map point 2' }))
    fireEvent.click(screen.getByRole('button', { name: 'Finish rectangle' }))
    fireEvent.click(screen.getByRole('button', { name: 'Save as new area' }))

    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument())
    const alert = screen.getByRole('alert')
    expect(alert).toHaveTextContent('Area must be entirely within Karnataka.')
    expect(alert.textContent).not.toContain('AOIOutsideKarnatakaError')
  })

  it('10. Downstream current-workflow state is invalidated appropriately on new AOI save', async () => {
    persistedAois = [{ aoi_id: 1, geometry: karnatakaPolygon, created_at: '2024-01-01T00:00:00Z', updated_at: '2024-01-01T00:00:00Z' }]
    render(<App />)
    await screen.findAllByText('No area selected')

    // Select AOI 1 and navigate to observations
    fireEvent.focus(screen.getByLabelText('Saved AOIs'))
    await waitFor(() => expect(screen.getByLabelText('Saved AOIs').querySelectorAll('option').length).toBeGreaterThan(1))
    fireEvent.change(screen.getByLabelText('Saved AOIs'), { target: { value: '1' } })
    await waitFor(() => expect(screen.getAllByText('Area active', { exact: false }).length).toBeGreaterThan(0))

    fireEvent.click(screen.getByTestId('proceed-to-observations-btn'))
    await waitFor(() => expect(screen.getByText('02 / Observations')).toBeInTheDocument())
    fireEvent.change(screen.getByLabelText('Observation start date'), { target: { value: '2024-01-01' } })
    fireEvent.change(screen.getByLabelText('Observation end date'), { target: { value: '2024-02-01' } })
    expect(screen.getByLabelText('Observation start date')).toHaveValue('2024-01-01')

    // Return to Area and save as new area
    fireEvent.click(screen.getByTestId('back-to-area-btn'))
    fireEvent.click(screen.getByRole('button', { name: 'Edit area' }))
    fireEvent.click(screen.getByRole('button', { name: 'Simulate edit drag' }))
    fireEvent.click(screen.getByRole('button', { name: 'Save as new area' }))

    await waitFor(() => expect(screen.getAllByText('Area active', { exact: false }).length).toBeGreaterThan(0))

    // Downstream state for previous area reset in current workflow
    fireEvent.click(screen.getByTestId('proceed-to-observations-btn'))
    expect(screen.getByLabelText('Observation start date')).toHaveValue('')
    expect(screen.getByLabelText('Observation end date')).toHaveValue('')
  })

  it('11. Saved AOI selection does not automatically restore downstream workflow state', async () => {
    persistedAois = [
      { aoi_id: 1, geometry: karnatakaPolygon, created_at: '2024-01-01T00:00:00Z', updated_at: '2024-01-01T00:00:00Z' },
    ]
    render(<App />)
    await screen.findAllByText('No area selected')

    fireEvent.focus(screen.getByLabelText('Saved AOIs'))
    await waitFor(() => expect(screen.getByLabelText('Saved AOIs').querySelectorAll('option').length).toBeGreaterThan(1))
    fireEvent.change(screen.getByLabelText('Saved AOIs'), { target: { value: '1' } })
    await waitFor(() => expect(screen.getAllByText('Area active', { exact: false }).length).toBeGreaterThan(0))

    // Remains on Area stage, not auto-advanced to Stage 03 or Stage 05
    expect(screen.getByText('01 / Area')).toBeInTheDocument()
    expect(screen.queryByText(/03 \/ Changes/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/05 \/ Candidates/i)).not.toBeInTheDocument()
  })

  it('12. Fresh session invariant: starts with no active area', async () => {
    render(<App />)
    await screen.findAllByText('No area selected')

    expect(screen.getByTestId('aoi-state')).toHaveAttribute('data-state', 'NO_AOI')
    expect(screen.getByTestId('aoi-state')).toHaveTextContent('No area selected')
    expect(screen.queryByTestId('active-aoi-badge')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Draw area' })).toBeInTheDocument()
  })

  it('13. Workflow rail is passive and in-stage navigation buttons manage transitions', async () => {
    persistedAois = [{ aoi_id: 1, geometry: karnatakaPolygon, created_at: '2024-01-01T00:00:00Z', updated_at: '2024-01-01T00:00:00Z' }]
    render(<App />)
    await screen.findAllByText('No area selected')

    fireEvent.focus(screen.getByLabelText('Saved AOIs'))
    await waitFor(() => expect(screen.getByLabelText('Saved AOIs').querySelectorAll('option').length).toBeGreaterThan(1))
    fireEvent.change(screen.getByLabelText('Saved AOIs'), { target: { value: '1' } })
    await waitFor(() => expect(screen.getAllByText('Area active', { exact: false }).length).toBeGreaterThan(0))

    // Workflow rail displays steps passively without button role
    const obsStep = screen.getByLabelText(/02.*OBSERVATIONS/i)
    expect(obsStep).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /02.*OBSERVATIONS/i })).not.toBeInTheDocument()

    // Intentional transition via in-stage button
    fireEvent.click(screen.getByTestId('proceed-to-observations-btn'))
    await waitFor(() => expect(screen.getByText('02 / Observations')).toBeInTheDocument())

    // Safe return to Stage 01 via in-stage button
    fireEvent.click(screen.getByTestId('back-to-area-btn'))
    await waitFor(() => expect(screen.getByText('01 / Area')).toBeInTheDocument())
  })
})
