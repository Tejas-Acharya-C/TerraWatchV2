import { render, act } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import MapCanvas from './MapCanvas'
import type { Polygon } from 'geojson'

// Mock maplibre-gl
const mockFitBounds = vi.fn()
const mockAddLayer = vi.fn()
const mockAddSource = vi.fn()
const mockSetLayoutProperty = vi.fn()
const mockUpdateImage = vi.fn()

const mockSatelliteSource = {
  updateImage: mockUpdateImage,
}

let loadCallback: (() => void) | null = null

vi.mock('maplibre-gl', () => {
  class MockMap {
    addControl = vi.fn()
    doubleClickZoom = { disable: vi.fn() }
    getCanvas = () => {
      const el = document.createElement('canvas')
      el.getBoundingClientRect = () => ({ left: 0, top: 0, right: 800, bottom: 600, width: 800, height: 600 } as DOMRect)
      return el
    }
    fitBounds = mockFitBounds
    addSource = mockAddSource
    addLayer = mockAddLayer
    setLayoutProperty = mockSetLayoutProperty
    getSource = vi.fn((id: string) => (id === 'satellite' ? mockSatelliteSource : undefined))
    isStyleLoaded = () => true
    on = vi.fn((event: string, callback: () => void) => {
      if (event === 'load') {
        loadCallback = callback
      }
    })
    getCenter = () => ({ lng: 76.3159, lat: 15.0149 })
    getZoom = () => 6.0
    project = () => ({ x: 0, y: 0 })
    unproject = () => ({ lng: 75.5, lat: 14.8 })
    remove = vi.fn()
    dragPan = { enable: vi.fn(), disable: vi.fn() }
  }

  return {
    Map: MockMap,
    NavigationControl: vi.fn(),
  }
})

describe('MapCanvas Satellite Rendering & Map Fitting (Part 11)', () => {
  const originalGeometry: Polygon = {
    type: 'Polygon',
    coordinates: [[[75.396, 14.793], [75.404, 14.793], [75.404, 14.800], [75.396, 14.800], [75.396, 14.793]]],
  }

  beforeEach(() => {
    vi.clearAllMocks()
    loadCallback = null
  })

  it('9 & 10: satellite raster layer uses raster-opacity = 1.0 and raster-resampling = nearest', async () => {
    render(
      <MapCanvas
        geometry={originalGeometry}
        draftPositions={[]}
        drawing={false}
        onMapClick={() => {}}
      />
    )

    // Trigger map load
    await act(async () => {
      if (loadCallback) await loadCallback()
    })

    // Find satellite layer add call
    const satelliteLayerCall = mockAddLayer.mock.calls.find((call) => call[0]?.id === 'satellite')
    expect(satelliteLayerCall).toBeDefined()
    expect(satelliteLayerCall![0].paint['raster-opacity']).toBe(1.0)
    expect(satelliteLayerCall![0].paint['raster-resampling']).toBe('nearest')
  })

  it('11, 12, 13, 14: selecting Before triggers map fitting using authoritative image bounds without modifying AOI geometry', async () => {
    const aoiCopy = JSON.parse(JSON.stringify(originalGeometry))

    const { rerender } = render(
      <MapCanvas
        geometry={aoiCopy}
        draftPositions={[]}
        drawing={false}
        onMapClick={() => {}}
        satellite={null}
      />
    )

    await act(async () => {
      if (loadCallback) await loadCallback()
    })
    mockFitBounds.mockClear()

    // Explicit Before observation selection
    const beforeBounds = [75.39624, 14.79390, 75.40406, 14.80052]
    await act(async () => {
      rerender(
        <MapCanvas
          geometry={aoiCopy}
          draftPositions={[]}
          drawing={false}
          onMapClick={() => {}}
          satellite={{
            role: 'Before',
            itemId: 'S2B_43PES_20250321_1_L2A',
            url: '/api/v1/imagery/acquisitions/128/display?aoi_id=16',
            bounds: beforeBounds,
            date: '2025-03-21T05:34:55Z',
          }}
        />
      )
    })

    // 11 & 12: fitBounds called with authoritative Before bounds [[west, south], [east, north]]
    expect(mockFitBounds).toHaveBeenCalledTimes(1)
    expect(mockFitBounds).toHaveBeenCalledWith(
      [[75.39624, 14.79390], [75.40406, 14.80052]],
      expect.objectContaining({ maxZoom: 16 })
    )

    // 13 & 14: AOI geometry is 100% unchanged
    expect(aoiCopy).toEqual(originalGeometry)
  })

  it('15 & 18: repeated renders and user pan/zoom do not continuously re-trigger fitBounds', async () => {
    const beforeBounds = [75.39624, 14.79390, 75.40406, 14.80052]
    const satelliteProp = {
      role: 'Before' as const,
      itemId: 'S2B_43PES_20250321_1_L2A',
      url: '/api/v1/imagery/acquisitions/128/display?aoi_id=16',
      bounds: beforeBounds,
      date: '2025-03-21T05:34:55Z',
    }

    const { rerender } = render(
      <MapCanvas
        geometry={originalGeometry}
        draftPositions={[]}
        drawing={false}
        onMapClick={() => {}}
        satellite={null}
      />
    )

    await act(async () => {
      if (loadCallback) await loadCallback()
    })
    mockFitBounds.mockClear()

    await act(async () => {
      rerender(
        <MapCanvas
          geometry={originalGeometry}
          draftPositions={[]}
          drawing={false}
          onMapClick={() => {}}
          satellite={satelliteProp}
        />
      )
    })
    expect(mockFitBounds).toHaveBeenCalledTimes(1)
    mockFitBounds.mockClear()

    // 15: Re-render with same satellite selection (e.g. parent state change, filter change)
    await act(async () => {
      rerender(
        <MapCanvas
          geometry={originalGeometry}
          draftPositions={[]}
          drawing={false}
          onMapClick={() => {}}
          satellite={satelliteProp}
        />
      )
    })
    expect(mockFitBounds).not.toHaveBeenCalled()

    // 18: Another re-render simulating user camera update
    await act(async () => {
      rerender(
        <MapCanvas
          geometry={originalGeometry}
          draftPositions={[]}
          drawing={false}
          onMapClick={() => {}}
          satellite={satelliteProp}
        />
      )
    })
    expect(mockFitBounds).not.toHaveBeenCalled()
  })

  it('16 & 17: switching Before <-> After updates the viewport to the respective image bounds', async () => {
    const beforeBounds = [75.39624, 14.79390, 75.40406, 14.80052]
    const afterBounds = [75.40000, 14.81000, 75.41000, 14.82000]

    const { rerender } = render(
      <MapCanvas
        geometry={originalGeometry}
        draftPositions={[]}
        drawing={false}
        onMapClick={() => {}}
        satellite={null}
      />
    )

    await act(async () => {
      if (loadCallback) await loadCallback()
    })
    mockFitBounds.mockClear()

    // Select Before
    await act(async () => {
      rerender(
        <MapCanvas
          geometry={originalGeometry}
          draftPositions={[]}
          drawing={false}
          onMapClick={() => {}}
          satellite={{
            role: 'Before',
            itemId: 'S2B_43PES_20250321_1_L2A',
            url: '/api/v1/imagery/acquisitions/128/display?aoi_id=16',
            bounds: beforeBounds,
            date: '2025-03-21T05:34:55Z',
          }}
        />
      )
    })
    expect(mockFitBounds).toHaveBeenCalledTimes(1)
    expect(mockFitBounds).toHaveBeenLastCalledWith(
      [[75.39624, 14.79390], [75.40406, 14.80052]],
      expect.anything()
    )
    mockFitBounds.mockClear()

    // Switch to After
    await act(async () => {
      rerender(
        <MapCanvas
          geometry={originalGeometry}
          draftPositions={[]}
          drawing={false}
          onMapClick={() => {}}
          satellite={{
            role: 'After',
            itemId: 'S2C_43PES_20250604_0_L2A',
            url: '/api/v1/imagery/acquisitions/136/display?aoi_id=16',
            bounds: afterBounds,
            date: '2025-06-04T05:35:12Z',
          }}
        />
      )
    })

    expect(mockFitBounds).toHaveBeenCalledTimes(1)
    expect(mockFitBounds).toHaveBeenLastCalledWith(
      [[75.40000, 14.81000], [75.41000, 14.82000]],
      expect.anything()
    )
    mockFitBounds.mockClear()

    // Switch back to Before
    await act(async () => {
      rerender(
        <MapCanvas
          geometry={originalGeometry}
          draftPositions={[]}
          drawing={false}
          onMapClick={() => {}}
          satellite={{
            role: 'Before',
            itemId: 'S2B_43PES_20250321_1_L2A',
            url: '/api/v1/imagery/acquisitions/128/display?aoi_id=16',
            bounds: beforeBounds,
            date: '2025-03-21T05:34:55Z',
          }}
        />
      )
    })

    expect(mockFitBounds).toHaveBeenCalledTimes(1)
    expect(mockFitBounds).toHaveBeenLastCalledWith(
      [[75.39624, 14.79390], [75.40406, 14.80052]],
      expect.anything()
    )
  })
})
