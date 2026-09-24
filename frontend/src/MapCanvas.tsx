import { useEffect, useRef, useState } from 'react'
import * as maplibregl from 'maplibre-gl'
import type { Map as MapLibreMap } from 'maplibre-gl'
import type { Feature, FeatureCollection, GeoJSON, Polygon, Position } from 'geojson'
import 'maplibre-gl/dist/maplibre-gl.css'

type MapCanvasProps = {
  geometry: Polygon | null
  draftPositions: Position[]
  drawing: boolean
  onMapClick: (position: Position) => void
  changeRegions?: Array<{ geometry: { type: string; coordinates: unknown }; region_id: number; candidate_id?: string; selected?: boolean }>
  satellite?: { url: string; bounds: number[]; role: 'Before' | 'After'; itemId: string; date: string } | null
  editing?: boolean
  draftGeometry?: Polygon | null
  onDraftChange?: (geometry: Polygon) => void
  onSelectCandidate?: (candidateId: string) => void
}

type EditHandleType = 'nw' | 'ne' | 'se' | 'sw' | 'center'

const emptyCollection: FeatureCollection = { type: 'FeatureCollection', features: [] }
const emptySatelliteImage = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII='
const karnatakaBounds: maplibregl.LngLatBoundsLike = [[74.0543, 11.5745], [78.5775, 18.4551]]

function getMapPadding(container: HTMLElement | null): maplibregl.PaddingOptions {
  if (!container) return { top: 48, bottom: 48, left: 48, right: 48 }
  const panel = container.parentElement?.querySelector('.workstation__panel') as HTMLElement | null
  if (panel && window.getComputedStyle(panel).position === 'absolute') {
    const panelRect = panel.getBoundingClientRect()
    const containerRect = container.getBoundingClientRect()
    const panelOverlap = Math.max(0, panelRect.right - containerRect.left)
    if (panelOverlap > 0 && panelOverlap < containerRect.width * 0.75) {
      return {
        top: 48,
        bottom: 48,
        left: Math.round(panelOverlap + 24),
        right: 48,
      }
    }
  }
  return { top: 48, bottom: 48, left: 48, right: 48 }
}

function featureForGeometry(geometry: Polygon | null, isDraft = false): Feature<Polygon> | null {
  return geometry ? { type: 'Feature', properties: { isDraft }, geometry } : null
}

function getHandlesCollection(geometry: Polygon | null): FeatureCollection {
  if (!geometry || !geometry.coordinates?.[0]?.length) return emptyCollection
  const ring = geometry.coordinates[0]
  if (ring.length < 5) return emptyCollection
  const xs = ring.map((p) => p[0])
  const ys = ring.map((p) => p[1])
  const minX = Math.min(...xs)
  const maxX = Math.max(...xs)
  const minY = Math.min(...ys)
  const maxY = Math.max(...ys)

  const center: Position = [(minX + maxX) / 2, (minY + maxY) / 2]

  return {
    type: 'FeatureCollection',
    features: [
      { type: 'Feature', properties: { handle: 'nw', kind: 'corner' }, geometry: { type: 'Point', coordinates: [minX, maxY] } },
      { type: 'Feature', properties: { handle: 'ne', kind: 'corner' }, geometry: { type: 'Point', coordinates: [maxX, maxY] } },
      { type: 'Feature', properties: { handle: 'se', kind: 'corner' }, geometry: { type: 'Point', coordinates: [maxX, minY] } },
      { type: 'Feature', properties: { handle: 'sw', kind: 'corner' }, geometry: { type: 'Point', coordinates: [minX, minY] } },
      { type: 'Feature', properties: { handle: 'center', kind: 'move' }, geometry: { type: 'Point', coordinates: center } },
    ],
  }
}

function findHandleAtScreenPoint(
  map: MapLibreMap,
  point: [number, number],
  geometry: Polygon | null,
  hitRadius = 18
): EditHandleType | null {
  if (!geometry || !geometry.coordinates?.[0]?.length) return null
  const ring = geometry.coordinates[0]
  if (ring.length < 5) return null
  const xs = ring.map((p) => p[0])
  const ys = ring.map((p) => p[1])
  const minX = Math.min(...xs)
  const maxX = Math.max(...xs)
  const minY = Math.min(...ys)
  const maxY = Math.max(...ys)

  const handles: Record<EditHandleType, [number, number]> = {
    nw: [minX, maxY],
    ne: [maxX, maxY],
    se: [maxX, minY],
    sw: [minX, minY],
    center: [(minX + maxX) / 2, (minY + maxY) / 2],
  }

  for (const [key, coords] of Object.entries(handles) as [EditHandleType, [number, number]][]) {
    const screenPos = map.project(coords)
    const dist = Math.hypot(screenPos.x - point[0], screenPos.y - point[1])
    if (dist <= hitRadius) {
      return key
    }
  }
  return null
}

function draftFeature(positions: Position[]): Feature<Polygon | { type: 'LineString'; coordinates: Position[] }> | null {
  if (positions.length < 2) return null
  if (positions.length === 2) {
    const [start, end] = positions
    const minLongitude = Math.min(start[0], end[0])
    const maxLongitude = Math.max(start[0], end[0])
    const minLatitude = Math.min(start[1], end[1])
    const maxLatitude = Math.max(start[1], end[1])
    return {
      type: 'Feature',
      properties: {},
      geometry: {
        type: 'Polygon',
        coordinates: [[
          [minLongitude, minLatitude],
          [maxLongitude, minLatitude],
          [maxLongitude, maxLatitude],
          [minLongitude, maxLatitude],
          [minLongitude, minLatitude],
        ]],
      },
    }
  }
  return {
    type: 'Feature',
    properties: {},
    geometry: { type: 'LineString', coordinates: positions },
  }
}

function boundsForPolygon(geometry: Polygon): maplibregl.LngLatBoundsLike {
  const positions = geometry.coordinates.flat()
  const longitudes = positions.map(([longitude]) => longitude)
  const latitudes = positions.map(([, latitude]) => latitude)
  return [[Math.min(...longitudes), Math.min(...latitudes)], [Math.max(...longitudes), Math.max(...latitudes)]]
}

function boundsForGeometry(geometry: { coordinates: unknown }): maplibregl.LngLatBoundsLike | null {
  const positions: number[][] = []
  function collect(value: unknown) {
    if (Array.isArray(value) && value.length >= 2 && typeof value[0] === 'number' && typeof value[1] === 'number') {
      positions.push([value[0], value[1]])
      return
    }
    if (Array.isArray(value)) value.forEach(collect)
  }
  collect(geometry.coordinates)
  if (!positions.length) return null
  const longitudes = positions.map(([longitude]) => longitude)
  const latitudes = positions.map(([, latitude]) => latitude)
  return [[Math.min(...longitudes), Math.min(...latitudes)], [Math.max(...longitudes), Math.max(...latitudes)]]
}

export default function MapCanvas({
  geometry,
  draftPositions,
  drawing,
  onMapClick,
  changeRegions = [],
  satellite = null,
  editing = false,
  draftGeometry = null,
  onDraftChange,
  onSelectCandidate,
}: MapCanvasProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const mapRef = useRef<MapLibreMap | null>(null)
  const onMapClickRef = useRef(onMapClick)
  const drawingRef = useRef(drawing)
  const editingRef = useRef(editing)
  const draftGeometryRef = useRef(draftGeometry)
  const onDraftChangeRef = useRef(onDraftChange)
  const onSelectCandidateRef = useRef(onSelectCandidate)
  const dragStartRef = useRef<Position | null>(null)
  const activeEditHandleRef = useRef<EditHandleType | null>(null)
  const initialGeomForDragRef = useRef<Polygon | null>(null)
  const dragStartPointRef = useRef<Position | null>(null)
  const [mapReady, setMapReady] = useState(false)
  const hasFitGeometryRef = useRef(false)
  const lastFitSatelliteKeyRef = useRef<string | null>(null)

  useEffect(() => {
    onMapClickRef.current = onMapClick
    drawingRef.current = drawing
    editingRef.current = editing
    draftGeometryRef.current = draftGeometry
    onDraftChangeRef.current = onDraftChange
    onSelectCandidateRef.current = onSelectCandidate
  }, [drawing, onMapClick, editing, draftGeometry, onDraftChange, onSelectCandidate])

  useEffect(() => {
    if (!containerRef.current) return
    const map = new maplibregl.Map({
      container: containerRef.current,
      center: [76.3159, 15.0149],
      zoom: 6,
      style: {
        version: 8,
        sources: {
          openstreetmap: {
            type: 'raster',
            tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
            tileSize: 256,
            attribution: '&copy; OpenStreetMap contributors',
          },
        },
        layers: [{ id: 'osm', type: 'raster', source: 'openstreetmap' }],
      },
    })
    mapRef.current = map
    map.addControl(new maplibregl.NavigationControl(), 'top-right')
    map.doubleClickZoom.disable()
    const canvas = map.getCanvas()
    let activePointerId: number | null = null

    const releaseDrag = () => {
      const pointerId = activePointerId
      dragStartRef.current = null
      activeEditHandleRef.current = null
      initialGeomForDragRef.current = null
      dragStartPointRef.current = null
      activePointerId = null
      map.dragPan.enable()
      if (pointerId !== null && canvas.hasPointerCapture?.(pointerId)) canvas.releasePointerCapture(pointerId)
    }

    const positionFromPointer = (event: PointerEvent): Position => {
      const bounds = canvas.getBoundingClientRect()
      const point: [number, number] = [event.clientX - bounds.left, event.clientY - bounds.top]
      const location = map.unproject(point)
      return [location.lng, location.lat]
    }

    const handlePointerDown = (event: PointerEvent) => {
      if (event.button !== 0) return

      // Handle editing interaction first
      if (editingRef.current && draftGeometryRef.current) {
        const bounds = canvas.getBoundingClientRect()
        const point: [number, number] = [event.clientX - bounds.left, event.clientY - bounds.top]
        const handle = findHandleAtScreenPoint(map, point, draftGeometryRef.current)
        if (handle) {
          event.preventDefault()
          activePointerId = event.pointerId
          canvas.setPointerCapture(event.pointerId)
          map.dragPan.disable()
          activeEditHandleRef.current = handle
          initialGeomForDragRef.current = JSON.parse(JSON.stringify(draftGeometryRef.current)) as Polygon
          dragStartPointRef.current = positionFromPointer(event)
          return
        }
      }

      if (!drawingRef.current) return
      event.preventDefault()
      activePointerId = event.pointerId
      canvas.setPointerCapture(event.pointerId)
      map.dragPan.disable()
      const position = positionFromPointer(event)
      dragStartRef.current = position
      onMapClickRef.current(position)
    }

    const handlePointerMove = (event: PointerEvent) => {
      // 1. Edit handle drag active
      if (activeEditHandleRef.current && initialGeomForDragRef.current && dragStartPointRef.current) {
        event.preventDefault()
        const currentPos = positionFromPointer(event)
        const handle = activeEditHandleRef.current
        const ring = initialGeomForDragRef.current.coordinates[0]
        const xs = ring.map((p) => p[0])
        const ys = ring.map((p) => p[1])
        const minX = Math.min(...xs)
        const maxX = Math.max(...xs)
        const minY = Math.min(...ys)
        const maxY = Math.max(...ys)

        let newMinX = minX
        let newMaxX = maxX
        let newMinY = minY
        let newMaxY = maxY

        if (handle === 'center') {
          const dLng = currentPos[0] - dragStartPointRef.current[0]
          const dLat = currentPos[1] - dragStartPointRef.current[1]
          newMinX = minX + dLng
          newMaxX = maxX + dLng
          newMinY = minY + dLat
          newMaxY = maxY + dLat
        } else {
          let fixedX: number
          let fixedY: number
          if (handle === 'nw') {
            fixedX = maxX
            fixedY = minY
          } else if (handle === 'ne') {
            fixedX = minX
            fixedY = minY
          } else if (handle === 'se') {
            fixedX = minX
            fixedY = maxY
          } else {
            fixedX = maxX
            fixedY = maxY
          }
          newMinX = Math.min(fixedX, currentPos[0])
          newMaxX = Math.max(fixedX, currentPos[0])
          newMinY = Math.min(fixedY, currentPos[1])
          newMaxY = Math.max(fixedY, currentPos[1])
        }

        if (newMaxX - newMinX > 1e-6 && newMaxY - newMinY > 1e-6) {
          const updated: Polygon = {
            type: 'Polygon',
            coordinates: [[
              [newMinX, newMinY],
              [newMaxX, newMinY],
              [newMaxX, newMaxY],
              [newMinX, newMaxY],
              [newMinX, newMinY],
            ]],
          }
          onDraftChangeRef.current?.(updated)
        }
        return
      }

      // 2. Drawing interaction
      if (drawingRef.current && event.pointerId === activePointerId && dragStartRef.current) {
        event.preventDefault()
        onMapClickRef.current(positionFromPointer(event))
        return
      }

      // 3. Hover cursor feedback in edit mode
      if (editingRef.current && draftGeometryRef.current && !drawingRef.current) {
        const bounds = canvas.getBoundingClientRect()
        const point: [number, number] = [event.clientX - bounds.left, event.clientY - bounds.top]
        const handle = findHandleAtScreenPoint(map, point, draftGeometryRef.current)
        if (handle === 'center') {
          canvas.style.cursor = 'move'
        } else if (handle === 'nw' || handle === 'se') {
          canvas.style.cursor = 'nwse-resize'
        } else if (handle === 'ne' || handle === 'sw') {
          canvas.style.cursor = 'nesw-resize'
        } else {
          canvas.style.cursor = ''
        }
      }
    }

    const handlePointerUp = (event: PointerEvent) => {
      if (activeEditHandleRef.current) {
        event.preventDefault()
        releaseDrag()
        return
      }
      if (event.pointerId !== activePointerId || !dragStartRef.current) return
      event.preventDefault()
      onMapClickRef.current(positionFromPointer(event))
      releaseDrag()
    }

    const handlePointerCancel = (event: PointerEvent) => {
      if (event.pointerId === activePointerId) releaseDrag()
    }

    canvas.addEventListener('pointerdown', handlePointerDown)
    canvas.addEventListener('pointermove', handlePointerMove)
    canvas.addEventListener('pointerup', handlePointerUp)
    canvas.addEventListener('pointercancel', handlePointerCancel)
    canvas.addEventListener('lostpointercapture', releaseDrag)

    map.on('load', async () => {
      let karnatakaBoundary: GeoJSON | null = null
      try {
        const response = await fetch('/karnataka.geojson')
        if (response.ok) karnatakaBoundary = await response.json() as GeoJSON
      } catch {
        karnatakaBoundary = null
      }
      map.fitBounds(karnatakaBounds, { padding: 72, duration: 0, maxZoom: 7 })
      updateViewportMetadata(map, containerRef.current)
      if (karnatakaBoundary) {
        map.addSource('karnataka-boundary', { type: 'geojson', data: karnatakaBoundary })
        map.addLayer({
          id: 'karnataka-fill',
          type: 'fill',
          source: 'karnataka-boundary',
          paint: { 'fill-color': '#16a34a', 'fill-opacity': 0.12 },
        })
        map.addLayer({
          id: 'karnataka-line',
          type: 'line',
          source: 'karnataka-boundary',
          paint: { 'line-color': '#527064', 'line-width': 1.5, 'line-opacity': 0.7 },
        })
      }
      map.addSource('aoi', { type: 'geojson', data: emptyCollection })
      map.addSource('satellite', { type: 'image', url: emptySatelliteImage, coordinates: [[74.0543, 18.4551], [78.5775, 18.4551], [78.5775, 11.5745], [74.0543, 11.5745]] })
      map.addLayer({
        id: 'satellite',
        type: 'raster',
        source: 'satellite',
        layout: { visibility: 'none' },
        paint: {
          'raster-opacity': 1.0,
          'raster-resampling': 'nearest',
        },
      })
      map.addLayer({
        id: 'aoi-fill',
        type: 'fill',
        source: 'aoi',
        paint: {
          'fill-color': ['case', ['==', ['get', 'isDraft'], true], '#2563eb', '#d97706'],
          'fill-opacity': ['case', ['==', ['get', 'isDraft'], true], 0.12, 0.08],
        },
      })
      map.addLayer({
        id: 'aoi-line',
        type: 'line',
        source: 'aoi',
        paint: {
          'line-color': ['case', ['==', ['get', 'isDraft'], true], '#2563eb', '#f59e0b'],
          'line-width': ['case', ['==', ['get', 'isDraft'], true], 3.0, 2.5],
        },
      })
      map.addSource('aoi-points', { type: 'geojson', data: emptyCollection })
      map.addLayer({
        id: 'aoi-points',
        type: 'circle',
        source: 'aoi-points',
        paint: { 'circle-color': '#fff7ed', 'circle-radius': 5, 'circle-stroke-color': '#b45309', 'circle-stroke-width': 2 },
      })
      map.addSource('raw-change', { type: 'geojson', data: emptyCollection })
      map.addLayer({
        id: 'raw-change-fill',
        type: 'fill',
        source: 'raw-change',
        paint: {
          'fill-color': ['case', ['==', ['get', 'selected'], true], '#0f766e', '#d97706'],
          'fill-opacity': ['case', ['==', ['get', 'selected'], true], 0.55, 0.22],
        },
      })
      map.addLayer({
        id: 'raw-change-line',
        type: 'line',
        source: 'raw-change',
        paint: {
          'line-color': ['case', ['==', ['get', 'selected'], true], '#115e59', '#b45309'],
          'line-width': ['case', ['==', ['get', 'selected'], true], 3.5, 1.5],
        },
      })

      map.on('click', 'raw-change-fill', (e) => {
        if (drawingRef.current || editingRef.current) return
        const feature = e.features?.[0]
        const candidateId = feature?.properties?.candidate_id
        if (candidateId && onSelectCandidateRef.current) {
          onSelectCandidateRef.current(candidateId)
        }
      })
      map.on('mouseenter', 'raw-change-fill', () => {
        if (!drawingRef.current && !editingRef.current) {
          canvas.style.cursor = 'pointer'
        }
      })
      map.on('mouseleave', 'raw-change-fill', () => {
        if (!drawingRef.current && !editingRef.current) {
          canvas.style.cursor = ''
        }
      })

      map.addSource('aoi-handles', { type: 'geojson', data: emptyCollection })
      map.addLayer({
        id: 'aoi-handles',
        type: 'circle',
        source: 'aoi-handles',
        paint: {
          'circle-radius': ['case', ['==', ['get', 'kind'], 'move'], 8, 6],
          'circle-color': ['case', ['==', ['get', 'kind'], 'move'], '#2563eb', '#ffffff'],
          'circle-stroke-color': ['case', ['==', ['get', 'kind'], 'move'], '#ffffff', '#1e40af'],
          'circle-stroke-width': 2.5,
        },
      })
      setMapReady(true)
    })
    map.on('moveend', () => updateViewportMetadata(map, containerRef.current))

    return () => {
      releaseDrag()
      canvas.removeEventListener('pointerdown', handlePointerDown)
      canvas.removeEventListener('pointermove', handlePointerMove)
      canvas.removeEventListener('pointerup', handlePointerUp)
      canvas.removeEventListener('pointercancel', handlePointerCancel)
      canvas.removeEventListener('lostpointercapture', releaseDrag)
      map.remove()
      mapRef.current = null
    }
  }, [])

  useEffect(() => {
    const map = mapRef.current
    if (mapReady && map?.isStyleLoaded()) {
      updateSources(map, geometry, draftPositions, changeRegions, editing, draftGeometry)
      const dynamicPadding = getMapPadding(containerRef.current)
      if (geometry && !hasFitGeometryRef.current) {
        map.fitBounds(boundsForPolygon(geometry), { padding: dynamicPadding, duration: 0, maxZoom: 12 })
        hasFitGeometryRef.current = true
      }
      if (!geometry) hasFitGeometryRef.current = false
      const selectedRegion = changeRegions.find((region) => region.selected)
      const selectedBounds = selectedRegion ? boundsForGeometry(selectedRegion.geometry) : null
      if (selectedBounds) map.fitBounds(selectedBounds, { padding: dynamicPadding, duration: 250, maxZoom: 14 })
    }
  }, [geometry, draftPositions, changeRegions, mapReady, editing, draftGeometry])

  useEffect(() => {
    const map = mapRef.current
    if (!mapReady || !map?.isStyleLoaded()) return
    const source = map.getSource('satellite') as maplibregl.ImageSource | undefined
    if (!source || !satellite) {
      map.setLayoutProperty('satellite', 'visibility', 'none')
      lastFitSatelliteKeyRef.current = null
      return
    }
    const [west, south, east, north] = satellite.bounds
    source.updateImage({ url: satellite.url, coordinates: [[west, north], [east, north], [east, south], [west, south]] })
    map.setLayoutProperty('satellite', 'visibility', 'visible')

    // Automatic map viewport fit to authoritative satellite image footprint on explicit selection
    const fitKey = `${satellite.role}:${satellite.itemId}:${satellite.url}`
    if (fitKey !== lastFitSatelliteKeyRef.current) {
      const dynamicPadding = getMapPadding(containerRef.current)
      const imageBounds: maplibregl.LngLatBoundsLike = [[west, south], [east, north]]
      map.fitBounds(imageBounds, { padding: dynamicPadding, duration: 250, maxZoom: 16 })
      lastFitSatelliteKeyRef.current = fitKey
    }
  }, [mapReady, satellite])

  return <div ref={containerRef} className="map-canvas" aria-label="AOI map" data-map-domain="karnataka" data-map-boundary="karnataka.geojson" data-map-center="76.3159,15.0149" data-map-zoom="6.00" />
}

function updateSources(
  map: MapLibreMap,
  geometry: Polygon | null,
  draftPositions: Position[],
  changeRegions: MapCanvasProps['changeRegions'],
  editing?: boolean,
  draftGeometry?: Polygon | null
) {
  const aoiSource = map.getSource('aoi') as maplibregl.GeoJSONSource | undefined
  const pointsSource = map.getSource('aoi-points') as maplibregl.GeoJSONSource | undefined
  const handlesSource = map.getSource('aoi-handles') as maplibregl.GeoJSONSource | undefined
  const changeSource = map.getSource('raw-change') as maplibregl.GeoJSONSource | undefined

  const displayedGeometry = editing ? (draftGeometry ?? geometry) : geometry
  const activeFeature = featureForGeometry(displayedGeometry, Boolean(editing))
  const draft = draftFeature(draftPositions)

  aoiSource?.setData(draft ? (draft as Feature<Polygon>) : (activeFeature ?? emptyCollection))
  pointsSource?.setData({
    type: 'FeatureCollection',
    features: draftPositions.map((position) => ({ type: 'Feature', properties: {}, geometry: { type: 'Point', coordinates: position } })),
  })
  changeSource?.setData({
    type: 'FeatureCollection',
    features: (changeRegions ?? []).map((region) => ({
      type: 'Feature',
      properties: {
        region_id: region.region_id,
        candidate_id: region.candidate_id,
        selected: region.selected ?? false,
      },
      geometry: region.geometry,
    })),
  } as never)

  if (editing && displayedGeometry) {
    handlesSource?.setData(getHandlesCollection(displayedGeometry))
  } else {
    handlesSource?.setData(emptyCollection)
  }
}

function updateViewportMetadata(map: MapLibreMap, container: HTMLDivElement | null) {
  if (!container) return
  const center = map.getCenter()
  container.dataset.mapCenter = `${center.lng.toFixed(4)},${center.lat.toFixed(4)}`
  container.dataset.mapZoom = map.getZoom().toFixed(2)
}
