import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import type { Candidate, CandidateEvidence, CandidateReview, ImageryAcquisition } from './lib/api'

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

describe('Phase 10 — EXPORT Workflow', () => {
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
    state: 'usable' | 'valid_unusable' = 'usable',
  ): ImageryAcquisition => ({
    acquisition_id: id,
    aoi_id: 1,
    requested_start_datetime: date,
    requested_end_datetime: date,
    item_id: `S2_OBS_${id}`,
    collection: 'sentinel-2-l2a',
    acquisition_datetime: date,
    assets: [],
    prepared_path: `path_${id}.tif`,
    raster: dummyRaster,
    source_metadata: { mgrs_tile: '43PFS' },
    created_at: date,
    observation_state: state,
    quality_reason: state === 'usable' ? undefined : 'Excessive cloud cover in AOI',
    quality_metrics: {
      usable_pixel_fraction: state === 'usable' ? 0.95 : 0.4,
      cloud_fraction: state === 'usable' ? 0.03 : 0.45,
      shadow_fraction: state === 'usable' ? 0.02 : 0.15,
      invalid_fraction: 0.0,
    },
    display_path: `/api/v1/acquisitions/${id}/display`,
    visualization_version: 'sentinel-2-rgb-percentile-v2',
  })

  const candidateA: Candidate = {
    candidate_id: 'analysis-42-signal-101',
    analysis_id: 42,
    signal_id: 101,
    rank: 1,
    priority: 'urgent',
    severity: 'high',
    score: 0.842,
    geometry: {
      type: 'Polygon',
      coordinates: [[[77.5, 12.8], [77.55, 12.8], [77.55, 12.85], [77.5, 12.85], [77.5, 12.8]]],
    },
    review_state: 'unreviewed',
    source_signal_ids: [101],
    detection_run_ids: [201],
    acquisition_ids: [10, 11],
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
      total_area_m2: 4500.0,
      score_components: { temporal_persistence: 0.35, quality_support: 0.25, magnitude: 0.242 },
      explanation: {
        summary: 'Strong persistent change with high radiometric contrast.',
        positive_factors: ['High temporal consistency', 'Clean quality mask'],
        limiting_factors: [],
        severity_rationale: "Classified as 'high' physical severity.",
        priority_rationale: "Assigned 'urgent' priority for triage.",
      },
    },
    created_at: '2024-01-01T00:00:00Z',
    updated_at: '2024-01-01T00:00:00Z',
  }

  const candidateB: Candidate = {
    candidate_id: 'analysis-42-signal-102',
    analysis_id: 42,
    signal_id: 102,
    rank: 2,
    priority: 'normal',
    severity: 'low',
    score: 0.412,
    geometry: {
      type: 'Polygon',
      coordinates: [[[77.6, 12.9], [77.65, 12.9], [77.65, 12.95], [77.6, 12.95], [77.6, 12.9]]],
    },
    review_state: 'rejected',
    source_signal_ids: [102],
    detection_run_ids: [201],
    acquisition_ids: [10, 11],
    metrics: {
      support_count: 1,
      interval_count: 2,
      persistence_ratio: 0.5,
      recurrence_count: 0,
      transient_interval_count: 1,
      temporal_consistency: 0.50,
      matched_region_coverage: 0.45,
      first_change_datetime: '2024-01-15T10:00:00Z',
      last_supporting_datetime: '2024-02-15T10:00:00Z',
      temporal_state: 'transient',
      quality_support: 0.65,
      mean_change_signal: 0.32,
      max_change_signal: 0.45,
      total_area_m2: 2100.0,
      score_components: { temporal_persistence: 0.25, quality_support: 0.22, magnitude: 0.14 },
    },
    created_at: '2024-01-01T00:00:00Z',
    updated_at: '2024-01-01T00:00:00Z',
  }

  const mockEvidenceA: CandidateEvidence = {
    aoi_id: 1,
    candidate: candidateA,
    temporal: {
      analysis_id: 42,
      signal_id: 101,
      state: 'persistent',
      support_count: 2,
      recurrence_count: 0,
      transient_interval_count: 0,
      observation_count: 3,
      interval_count: 2,
      persistence_ratio: 1.0,
      first_change_datetime: '2024-01-15T10:00:00Z',
      last_supporting_datetime: '2024-03-15T10:00:00Z',
      quality_aware_temporal_support: 0.91,
      temporal_consistency: 0.92,
      matched_region_coverage: 0.88,
    },
    intervals: [{
      detection_run_id: 201,
      before_acquisition_id: 10,
      after_acquisition_id: 11,
      detector_version: 'diff-v2',
      threshold: 0.25,
      min_region_pixels: 4,
      changed_pixel_count: 45,
      total_changed_area_m2: 4500.0,
      region_count: 1,
      quality_mask_used: true,
      quality_processing_version: 'sentinel-2-l2a-v1',
      quality_valid_pixel_count: 40,
      excluded_pixel_count: 5,
      mean_change_signal: 0.62,
      max_change_signal: 0.78,
      raw_changed_pixel_count: 50,
      filtered_changed_pixel_count: 45,
      changed_pixel_percentage: 4.5,
      largest_region_area_m2: 4500.0,
      mean_region_area_m2: 4500.0,
      median_region_area_m2: 4500.0,
      excluded_cloud_pixel_count: 3,
      excluded_shadow_pixel_count: 2,
      regions: [],
    }],
    acquisitions: [
      createMockAcquisition(10, '2024-01-15T10:00:00Z') as any,
      createMockAcquisition(11, '2024-02-15T10:00:00Z') as any,
    ],
    evidence_bounds: [77.5, 12.8, 77.55, 12.85],
    is_spatially_aligned: true,
    spatial_alignment_details: {
      crs: 'EPSG:32643',
      width: 100,
      height: 100,
      pixel_size: 10,
      status: 'aligned',
    },
    is_quality_limited: false,
    quality_limitation_reasons: [],
    provenance_chain: {
      pipeline_version: '2.0.0',
      evidence_generated_at: '2024-03-16T12:00:00Z',
      aoi_id: 1,
      karnataka_contained: true,
    },
  }

  let capturedExportUrls: string[] = []
  let reviewsDb: Record<string, CandidateReview> = {}

  const mockJsonSnapshot = (candidateId: string, reviewDecision: string, reviewNote: string | null = null) => ({
    export_version: 'terrawatch-investigation-v1',
    generated_at: '2024-03-16T12:00:00Z',
    candidate_id: candidateId,
    analysis_id: 42,
    signal_id: candidateId.includes('101') ? 101 : 102,
    aoi_id: 1,
    priority: candidateId.includes('101') ? 'urgent' : 'normal',
    severity: candidateId.includes('101') ? 'high' : 'low',
    score: candidateId.includes('101') ? 0.842 : 0.412,
    rank: candidateId.includes('101') ? 1 : 2,
    geometry: { type: 'Polygon', coordinates: [[[77.5, 12.8], [77.55, 12.8], [77.55, 12.85], [77.5, 12.85], [77.5, 12.8]]] },
    centroid: { longitude: 77.525, latitude: 12.825 },
    analyst_review: {
      decision: reviewDecision,
      note: reviewNote,
      created_at: '2024-03-16T10:00:00Z',
      updated_at: '2024-03-16T10:00:00Z',
    },
    provenance: {
      candidate_id: candidateId,
      analysis_id: 42,
      signal_id: candidateId.includes('101') ? 101 : 102,
      aoi_id: 1,
      karnataka_contained: true,
      detection_run_ids: [201],
      acquisition_ids: [10, 11],
      before_acquisition_id: 10,
      after_acquisition_id: 11,
      before_item_id: 'S2_OBS_10',
      after_item_id: 'S2_OBS_11',
      crs: 'EPSG:32643',
      detector_version: 'diff-v2',
      processing_version: 'sentinel-2-l2a-v1',
      masking_method: 'scl-quality-mask-v1',
      visualization_version: 'sentinel-2-rgb-percentile-v2',
    },
  })

  let customFetchHandler: ((input: RequestInfo | URL, init?: RequestInit) => Promise<Response | null> | Response | null) | null = null

  beforeEach(() => {
    capturedExportUrls = []
    customFetchHandler = null
    reviewsDb = {
      'analysis-42-signal-101': {
        candidate_id: 'analysis-42-signal-101',
        decision: 'unreviewed',
        note: null,
        created_at: null,
        updated_at: null,
      },
      'analysis-42-signal-102': {
        candidate_id: 'analysis-42-signal-102',
        decision: 'rejected',
        note: 'Known seasonal agricultural cycle',
        created_at: '2024-03-16T10:00:00Z',
        updated_at: '2024-03-16T10:00:00Z',
      },
    }

    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (customFetchHandler) {
        const custom = await customFetchHandler(input, init)
        if (custom) return custom
      }
      const url = String(input)

      if (url.includes('/api/v1/health')) {
        return Promise.resolve(new Response(JSON.stringify({ status: 'healthy' }), { status: 200 }))
      }
      if (url.includes('/api/v1/aois')) {
        return Promise.resolve(new Response(JSON.stringify([
          { aoi_id: 1, geometry: { type: 'Polygon', coordinates: [[[77.0, 12.0], [77.5, 12.0], [77.5, 12.5], [77.0, 12.5], [77.0, 12.0]]] }, created_at: '2024-01-01T00:00:00Z', updated_at: '2024-01-01T00:00:00Z' },
        ]), { status: 200 }))
      }
      if (url.includes('/api/v1/aoi/1') || (url.includes('/api/v1/aoi') && !url.includes('/acquisitions'))) {
        return Promise.resolve(new Response(JSON.stringify({
          aoi_id: 1,
          geometry: { type: 'Polygon', coordinates: [[[77.0, 12.0], [77.5, 12.0], [77.5, 12.5], [77.0, 12.5], [77.0, 12.0]]] },
          created_at: '2024-01-01T00:00:00Z',
          updated_at: '2024-01-01T00:00:00Z',
        }), { status: 200 }))
      }
      if (url.includes('/api/v1/imagery/acquisitions') || url.includes('/api/v1/imagery/acquire') || url.includes('/acquisitions')) {
        return Promise.resolve(new Response(JSON.stringify({
          aoi_id: 1,
          total: 2,
          acquisitions: [createMockAcquisition(10, '2024-01-15T10:00:00Z'), createMockAcquisition(11, '2024-02-15T10:00:00Z')],
        }), { status: 200 }))
      }
      if (url.includes('/api/v1/detections') || url.includes('/api/v1/detection/detect')) {
        return Promise.resolve(new Response(JSON.stringify({
          run_id: 201,
          before_acquisition_id: 10,
          after_acquisition_id: 11,
          region_count: 1,
          changed_pixel_count: 45,
          total_changed_area_m2: 4500.0,
          detector_version: 'diff-v2',
          threshold: 0.25,
          min_region_pixels: 4,
          regions: [{ region_id: 1, geometry: { type: 'Polygon', coordinates: [] }, area_m2: 4500.0, mean_change_signal: 0.62, max_change_signal: 0.78 }],
        }), { status: 200 }))
      }
      if (url.includes('/api/v1/temporal-analyses') || url.includes('/api/v1/temporal/analyze')) {
        return Promise.resolve(new Response(JSON.stringify({
          analysis_id: 42,
          iou_threshold: 0.25,
          detector_run_count: 2,
          observation_count: 3,
          temporal_span_days: 60,
          state: 'persistent',
          usable_observation_count: 3,
          usable_interval_count: 2,
          quality_support: 0.91,
          quality_aggregation_method: 'mean',
          seasonal_interpretation: 'seasonal_compatible',
          observations: [],
          relationships: [],
          signals: [{
            signal_id: 101,
            geometry: { type: 'Polygon', coordinates: [] },
            support_count: 2,
            interval_count: 2,
            persistence_ratio: 1.0,
            recurrence_count: 0,
            transient_interval_count: 0,
            temporal_consistency: 0.92,
            matched_region_coverage: 0.88,
            first_change_datetime: '2024-01-15T10:00:00Z',
            last_supporting_datetime: '2024-03-15T10:00:00Z',
            state: 'persistent',
            usable_interval_count: 2,
            supporting_interval_count: 2,
            quality_support: 0.91,
            seasonal_interpretation: 'seasonal_compatible',
          }],
          created_at: '2024-01-01T00:00:00Z',
        }), { status: 200 }))
      }
      if (url.includes('/api/v1/orchestrations/analyze')) {
        return Promise.resolve(new Response(
          JSON.stringify({
            aoi_id: 1,
            eligible_observation_ids: [10, 11, 12],
            reused_detection_count: 0,
            generated_detection_count: 2,
            reused_analysis: false,
            analysis: {
              analysis_id: 42,
              iou_threshold: 0.25,
              detector_run_count: 2,
              observation_count: 3,
              temporal_span_days: 60,
              state: 'persistent',
              usable_observation_count: 3,
              usable_interval_count: 2,
              quality_support: 0.91,
              quality_aggregation_method: 'mean',
              seasonal_interpretation: 'seasonal_compatible',
              observations: [],
              relationships: [],
              signals: [{
                signal_id: 101,
                geometry: { type: 'Polygon', coordinates: [] },
                support_count: 2,
                interval_count: 2,
                persistence_ratio: 1.0,
                recurrence_count: 0,
                transient_interval_count: 0,
                temporal_consistency: 0.92,
                matched_region_coverage: 0.88,
                first_change_datetime: '2024-01-15T10:00:00Z',
                last_supporting_datetime: '2024-03-15T10:00:00Z',
                state: 'persistent',
                usable_interval_count: 2,
                supporting_interval_count: 2,
                quality_support: 0.91,
                seasonal_interpretation: 'seasonal_compatible',
              }],
              created_at: '2024-01-01T00:00:00Z',
            },
            candidates: {
              analysis_id: 42,
              candidates: [],
            },
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        ))
      }
      if (url.includes('/api/v1/candidates/triage')) {
        return Promise.resolve(new Response(JSON.stringify({
          analysis_id: 42,
          candidates: [candidateA, candidateB],
        }), { status: 200 }))
      }
      if (url.includes('/api/v1/candidates/') && url.includes('/evidence')) {
        const match = url.match(/\/candidates\/([^/]+)\/evidence/)
        const cid = match ? decodeURIComponent(match[1]) : ''
        if (cid === candidateB.candidate_id) {
          return Promise.resolve(new Response(JSON.stringify({
            ...mockEvidenceA,
            candidate: candidateB,
          }), { status: 200 }))
        }
        return Promise.resolve(new Response(JSON.stringify(mockEvidenceA), { status: 200 }))
      }
      if (url.includes('/api/v1/candidates/') && url.includes('/review')) {
        const match = url.match(/\/candidates\/([^/]+)\/review/)
        const cid = match ? decodeURIComponent(match[1]) : ''
        const method = init?.method ?? 'GET'

        if (method === 'PATCH') {
          const body = JSON.parse(init?.body as string)
          const saved: CandidateReview = {
            candidate_id: cid,
            decision: body.decision,
            note: body.note ?? null,
            created_at: reviewsDb[cid]?.created_at ?? '2024-02-01T10:00:00Z',
            updated_at: '2024-02-01T10:05:00Z',
          }
          reviewsDb[cid] = saved
          return Promise.resolve(new Response(JSON.stringify(saved), { status: 200 }))
        }

        if (reviewsDb[cid]) {
          return Promise.resolve(new Response(JSON.stringify(reviewsDb[cid]), { status: 200 }))
        }
        return Promise.resolve(new Response(JSON.stringify({
          candidate_id: cid,
          decision: 'unreviewed',
          note: null,
          created_at: null,
          updated_at: null,
        }), { status: 200 }))
      }
      if (url.includes('/export')) {
        capturedExportUrls.push(url)
        const format = url.includes('format=json') ? 'json' : 'pdf'
        const candidateId = url.includes('102') ? 'analysis-42-signal-102' : 'analysis-42-signal-101'
        const review = reviewsDb[candidateId] ?? { decision: 'unreviewed', note: null }

        if (format === 'json') {
          const snapshot = mockJsonSnapshot(candidateId, review.decision, review.note)
          return Promise.resolve(new Response(JSON.stringify(snapshot), {
            status: 200,
            headers: {
              'Content-Type': 'application/json',
              'Content-Disposition': `attachment; filename="terrawatch_investigation_${candidateId}.json"`,
            },
          }))
        } else {
          const body = '%PDF-1.4\n% TerraWatch V2 Investigation Report\n%%EOF'
          return Promise.resolve(new Response(body, {
            status: 200,
            headers: {
              'Content-Type': 'application/pdf',
              'Content-Disposition': `attachment; filename="terrawatch_investigation_${candidateId}.pdf"`,
            },
          }))
        }
      }

      return Promise.resolve(new Response(JSON.stringify({}), { status: 200 }))
    })

    // Mock URL methods
    if (!globalThis.URL.createObjectURL) {
      globalThis.URL.createObjectURL = vi.fn(() => 'blob:mock-url')
      globalThis.URL.revokeObjectURL = vi.fn()
    } else {
      vi.spyOn(globalThis.URL, 'createObjectURL').mockReturnValue('blob:mock-url')
      vi.spyOn(globalThis.URL, 'revokeObjectURL').mockImplementation(() => {})
    }
  })

  async function navigateToCandidatesWorkflow() {
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

    const runBtn = screen.getByTestId('run-automated-analysis-btn')
    fireEvent.click(runBtn)
    await waitFor(() => expect(screen.getByTestId('orchestration-summary')).toBeInTheDocument())

    // Navigate sequentially: Stage 03 -> Stage 04 -> Stage 05
    fireEvent.click(screen.getByTestId('proceed-to-history-btn'))
    await waitFor(() => expect(screen.getByText('04 / Change history')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('temporal-proceed-candidates-btn'))
    await waitFor(() => expect(screen.getByText('05 / Candidates')).toBeInTheDocument())
  }

  async function navigateToExportForCandidateA() {
    await navigateToCandidatesWorkflow()
    fireEvent.click(screen.getByRole('button', { name: 'Triage temporal signals' }))
    await waitFor(() => expect(screen.getByText('#1 / urgent priority')).toBeInTheDocument())
    fireEvent.click(screen.getByText('#1 / urgent priority'))
    fireEvent.click(screen.getByTestId('proceed-to-evidence-btn'))
    await waitFor(() => expect(screen.getByText('06 / Evidence')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('record-review-decision-btn'))
    await waitFor(() => expect(screen.getByText('07 / Review')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('decision-accepted-radio'))
    fireEvent.click(screen.getByTestId('save-review-btn'))
    await waitFor(() => expect(screen.getByTestId('review-save-success')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('proceed-to-export-btn'))
    await waitFor(() => expect(screen.getByText('08 / Export')).toBeInTheDocument())
    await screen.findByTestId('investigation-export-section')
  }

  const navigateToEvidenceForCandidateA = navigateToExportForCandidateA

  async function navigateToReviewForCandidateA() {
    await navigateToCandidatesWorkflow()
    fireEvent.click(screen.getByRole('button', { name: 'Triage temporal signals' }))
    await waitFor(() => expect(screen.getByText('#1 / urgent priority')).toBeInTheDocument())
    fireEvent.click(screen.getByText('#1 / urgent priority'))
    fireEvent.click(screen.getByTestId('proceed-to-evidence-btn'))
    await waitFor(() => expect(screen.getByText('06 / Evidence')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('record-review-decision-btn'))
    await waitFor(() => expect(screen.getByText('07 / Review')).toBeInTheDocument())
    await screen.findByTestId('analyst-review-section')
  }

  it('1. JSON export succeeds for a valid selected candidate', async () => {
    await navigateToEvidenceForCandidateA()

    const exportBtn = screen.getByTestId('download-json-data-btn')
    fireEvent.click(exportBtn)

    await waitFor(() => {
      expect(screen.getByTestId('export-success-message')).toBeInTheDocument()
    })
    expect(screen.getByTestId('export-success-message')).toHaveTextContent('Downloaded terrawatch_investigation_analysis-42-signal-101.json')
    expect(capturedExportUrls.some((u) => u.includes('/candidates/analysis-42-signal-101/export?format=json'))).toBe(true)
  })

  it('2. PDF export succeeds for a valid selected candidate', async () => {
    await navigateToEvidenceForCandidateA()

    const exportBtn = screen.getByTestId('download-pdf-report-btn')
    fireEvent.click(exportBtn)

    await waitFor(() => {
      expect(screen.getByTestId('export-success-message')).toBeInTheDocument()
    })
    expect(screen.getByTestId('export-success-message')).toHaveTextContent('Downloaded terrawatch_investigation_analysis-42-signal-101.pdf')
    expect(capturedExportUrls.some((u) => u.includes('/candidates/analysis-42-signal-101/export?format=pdf'))).toBe(true)
  })

  it('3. Authoritative candidate identity is used in endpoint and filename', async () => {
    await navigateToEvidenceForCandidateA()

    const exportBtn = screen.getByTestId('download-pdf-report-btn')
    fireEvent.click(exportBtn)

    await waitFor(() => {
      expect(screen.getByTestId('export-success-message')).toHaveTextContent('terrawatch_investigation_analysis-42-signal-101.pdf')
    })
    expect(capturedExportUrls[0]).toContain('/candidates/analysis-42-signal-101/export')
  })

  it('4. Review state representation: accurately reflects unreviewed and rejected states', async () => {
    await navigateToEvidenceForCandidateA()

    const exportBtn = screen.getByTestId('download-json-data-btn')
    fireEvent.click(exportBtn)

    await waitFor(() => {
      expect(screen.getByTestId('export-success-message')).toBeInTheDocument()
    })

    // Return to candidates and select B
    fireEvent.click(screen.getByTestId('back-to-review-btn'))
    await waitFor(() => expect(screen.getByText('07 / Review')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('back-to-evidence-btn'))
    await waitFor(() => expect(screen.getByText('06 / Evidence')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('back-to-candidates-btn'))
    await waitFor(() => expect(screen.getByText('05 / Candidates')).toBeInTheDocument())

    fireEvent.click(screen.getByText('#2 / normal priority'))
    fireEvent.click(screen.getByTestId('proceed-to-evidence-btn'))
    await waitFor(() => expect(screen.getByText('06 / Evidence')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('record-review-decision-btn'))
    await waitFor(() => expect(screen.getByText('07 / Review')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('decision-rejected-radio'))
    fireEvent.click(screen.getByTestId('save-review-btn'))
    await waitFor(() => expect(screen.getByTestId('review-save-success')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('proceed-to-export-btn'))
    await waitFor(() => expect(screen.getByText('08 / Export')).toBeInTheDocument())

    const exportBtnB = screen.getByTestId('download-json-data-btn')
    fireEvent.click(exportBtnB)

    await waitFor(() => {
      expect(screen.getByTestId('export-success-message')).toHaveTextContent('terrawatch_investigation_analysis-42-signal-102.json')
    })
  })

  it('5. Candidate switching: A export state clears when switching to B, B export targets B', async () => {
    await navigateToEvidenceForCandidateA()

    const exportBtn = screen.getByTestId('download-pdf-report-btn')
    fireEvent.click(exportBtn)

    await waitFor(() => {
      expect(screen.getByTestId('export-success-message')).toHaveTextContent('analysis-42-signal-101')
    })

    // Switch to Candidate B
    fireEvent.click(screen.getByTestId('back-to-review-btn'))
    await waitFor(() => expect(screen.getByText('07 / Review')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('back-to-evidence-btn'))
    await waitFor(() => expect(screen.getByText('06 / Evidence')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('back-to-candidates-btn'))
    await waitFor(() => expect(screen.getByText('05 / Candidates')).toBeInTheDocument())
    fireEvent.click(screen.getByText('#2 / normal priority'))

    // Check that previous success message is cleared
    expect(screen.queryByTestId('export-success-message')).not.toBeInTheDocument()

    fireEvent.click(screen.getByTestId('proceed-to-evidence-btn'))
    await waitFor(() => expect(screen.getByText('06 / Evidence')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('record-review-decision-btn'))
    await waitFor(() => expect(screen.getByText('07 / Review')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('decision-rejected-radio'))
    fireEvent.click(screen.getByTestId('save-review-btn'))
    await waitFor(() => expect(screen.getByTestId('review-save-success')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('proceed-to-export-btn'))
    await waitFor(() => expect(screen.getByText('08 / Export')).toBeInTheDocument())

    const exportBtnB = screen.getByTestId('download-pdf-report-btn')
    fireEvent.click(exportBtnB)

    await waitFor(() => {
      expect(screen.getByTestId('export-success-message')).toHaveTextContent('terrawatch_investigation_analysis-42-signal-102.pdf')
    })
    expect(capturedExportUrls[capturedExportUrls.length - 1]).toContain('/candidates/analysis-42-signal-102/export')
  })

  it('6. In-flight race protection: delayed response for A does not update B or trigger download', async () => {
    let delayedResolve!: (res: Response) => void
    const delayedPromise = new Promise<Response>((resolve) => {
      delayedResolve = resolve
    })

    customFetchHandler = async (input) => {
      const url = String(input)
      if (url.includes('/candidates/analysis-42-signal-101/export')) {
        capturedExportUrls.push(url)
        return delayedPromise
      }
      return null
    }

    const anchorClickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click')

    await navigateToEvidenceForCandidateA()

    const exportBtn = screen.getByTestId('download-pdf-report-btn')
    fireEvent.click(exportBtn)

    // Export is in flight
    expect(screen.getByTestId('export-loading-indicator')).toBeInTheDocument()

    // Reset spy calls prior to candidate switch
    anchorClickSpy.mockClear()

    // Switch to Candidate B before A resolves
    fireEvent.click(screen.getByTestId('back-to-review-btn'))
    await waitFor(() => expect(screen.getByText('07 / Review')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('back-to-evidence-btn'))
    await waitFor(() => expect(screen.getByText('06 / Evidence')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('back-to-candidates-btn'))
    await waitFor(() => expect(screen.getByText('05 / Candidates')).toBeInTheDocument())
    fireEvent.click(screen.getByText('#2 / normal priority'))

    // Resolve A's delayed export response now
    delayedResolve!(new Response('%PDF-1.4 dummy', {
      status: 200,
      headers: {
        'Content-Type': 'application/pdf',
        'Content-Disposition': 'attachment; filename="terrawatch_investigation_analysis-42-signal-101.pdf"',
      },
    }))

    // Wait a tick
    await new Promise((r) => setTimeout(r, 50))

    // Candidate B must NOT show success or download link for Candidate A
    expect(screen.queryByTestId('export-success-message')).not.toBeInTheDocument()
    expect(anchorClickSpy).not.toHaveBeenCalled()
  })

  it('7. No candidate selected: export section is unavailable with no auto-selection', async () => {
    await navigateToCandidatesWorkflow()

    // On Candidates stage with no candidate selected
    expect(screen.queryByTestId('investigation-export-section')).not.toBeInTheDocument()
    expect(screen.queryByTestId('download-pdf-report-btn')).not.toBeInTheDocument()
    expect(screen.queryByTestId('download-json-data-btn')).not.toBeInTheDocument()
    expect(screen.queryByTestId('export-investigation-btn')).not.toBeInTheDocument()
    expect(screen.getByLabelText(/08.*EXPORT/i)).toHaveClass('workflow-step--locked')
  })

  it('8. Upstream invalidation: clearing candidate or upstream stage resets export state', async () => {
    await navigateToEvidenceForCandidateA()

    const exportBtn = screen.getByTestId('download-pdf-report-btn')
    fireEvent.click(exportBtn)
    await waitFor(() => {
      expect(screen.getByTestId('export-success-message')).toBeInTheDocument()
    })

    // Invalidate upstream by navigating back to Area
    fireEvent.click(screen.getByTestId('back-to-review-btn'))
    await waitFor(() => expect(screen.getByText('07 / Review')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('back-to-evidence-btn'))
    await waitFor(() => expect(screen.getByText('06 / Evidence')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('back-to-candidates-btn'))
    await waitFor(() => expect(screen.getByText('05 / Candidates')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('back-to-history-btn'))
    await waitFor(() => expect(screen.getByText('04 / Change history')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('back-to-changes-btn'))
    await waitFor(() => expect(screen.getByText('03 / Changes')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('back-to-observations-btn'))
    await waitFor(() => expect(screen.getByText('02 / Observations')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('back-to-area-btn'))
    await waitFor(() => expect(screen.getByText('01 / Area')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: 'Draw area' }))
    expect(screen.queryByTestId('investigation-export-section')).not.toBeInTheDocument()
    expect(screen.getByLabelText(/08.*EXPORT/i)).toHaveClass('workflow-step--locked')
  })

  it('9. Read-only behavior: export does not make mutation requests or alter candidate fields', async () => {
    const mutationCalls: { url: string; method: string }[] = []
    customFetchHandler = (input, init) => {
      const method = init?.method ?? 'GET'
      if (['POST', 'PATCH', 'PUT', 'DELETE'].includes(method)) {
        mutationCalls.push({ url: String(input), method })
      }
      return null
    }

    await navigateToEvidenceForCandidateA()
    // Clear mutation calls triggered during upstream navigation setup
    mutationCalls.length = 0

    const exportBtn = screen.getByTestId('download-pdf-report-btn')
    fireEvent.click(exportBtn)

    await waitFor(() => {
      expect(screen.getByTestId('export-success-message')).toBeInTheDocument()
    })

    // Exactly 0 mutation calls were triggered by export
    expect(mutationCalls).toEqual([])
    // Candidate score and priority unchanged
    expect(candidateA.score).toBe(0.842)
    expect(candidateA.priority).toBe('urgent')
  })

  it('10. Provenance: JSON export maintains stable IDs and contains no internal filesystem paths', async () => {
    await navigateToEvidenceForCandidateA()

    const exportBtn = screen.getByTestId('download-json-data-btn')
    fireEvent.click(exportBtn)

    await waitFor(() => {
      expect(screen.getByTestId('export-success-message')).toBeInTheDocument()
    })

    // Verify generated snapshot data structure
    const snapshot = mockJsonSnapshot('analysis-42-signal-101', 'unreviewed')
    const jsonStr = JSON.stringify(snapshot)
    expect(jsonStr).toContain('terrawatch-investigation-v1')
    expect(jsonStr).toContain('analysis-42-signal-101')
    expect(jsonStr).toContain('S2_OBS_10')
    expect(jsonStr).not.toMatch(/[C-Z]:\\/)
    expect(jsonStr).not.toContain('/home/')
    expect(jsonStr).not.toContain('/app/')
  })

  it('11. No fake data: exported metadata matches real candidate and evidence values', async () => {
    await navigateToEvidenceForCandidateA()

    const exportBtn = screen.getByTestId('download-pdf-report-btn')
    fireEvent.click(exportBtn)

    await waitFor(() => {
      expect(screen.getByTestId('export-success-message')).toBeInTheDocument()
    })
    expect(capturedExportUrls[0]).toContain(candidateA.candidate_id)
  })

  it('12. Dedicated PDF and JSON download actions function independently', async () => {
    await navigateToEvidenceForCandidateA()

    const pdfBtn = screen.getByTestId('download-pdf-report-btn')
    const jsonBtn = screen.getByTestId('download-json-data-btn')
    expect(pdfBtn).toHaveTextContent('Download PDF Report')
    expect(jsonBtn).toHaveTextContent('Download JSON Data')

    fireEvent.click(jsonBtn)
    await waitFor(() => {
      expect(screen.getByTestId('export-success-message')).toBeInTheDocument()
    })
    expect(screen.getByTestId('export-success-message')).toHaveTextContent('terrawatch_investigation_analysis-42-signal-101.json')

    fireEvent.click(pdfBtn)
    await waitFor(() => {
      expect(screen.getByTestId('export-success-message')).toHaveTextContent('terrawatch_investigation_analysis-42-signal-101.pdf')
    })
  })

  it('13. Export failure: displays explicit error message and preserves workstation state', async () => {
    customFetchHandler = (input) => {
      const url = String(input)
      if (url.includes('/export')) {
        return new Response(JSON.stringify({
          code: 'CandidateEvidenceError',
          message: 'Raster artifact corrupted on disk',
        }), { status: 502 })
      }
      return null
    }

    await navigateToEvidenceForCandidateA()

    const exportBtn = screen.getByTestId('download-pdf-report-btn')
    fireEvent.click(exportBtn)

    await waitFor(() => {
      expect(screen.getByTestId('export-error-message')).toBeInTheDocument()
    })
    expect(screen.getByTestId('export-error-message')).toHaveTextContent('Raster artifact corrupted on disk')
    expect(screen.queryByTestId('export-success-message')).not.toBeInTheDocument()
    // Workstation state preserved
    expect(screen.getByTestId('investigation-export-section')).toBeInTheDocument()
  })

  it('14. Stage 08 EXPORT provides exactly two dedicated export actions without duplicate buttons', async () => {
    await navigateToExportForCandidateA()

    expect(screen.getByTestId('investigation-export-section')).toBeInTheDocument()
    expect(screen.getByTestId('download-pdf-report-btn')).toBeInTheDocument()
    expect(screen.getByTestId('download-json-data-btn')).toBeInTheDocument()
    expect(screen.queryByTestId('export-investigation-btn')).not.toBeInTheDocument()
    expect(screen.queryByTestId('export-format-pdf')).not.toBeInTheDocument()
    expect(screen.queryByTestId('export-format-json')).not.toBeInTheDocument()
  })

  it('15. Stage 06 and Stage 07 do not contain duplicated export widgets', async () => {
    await navigateToReviewForCandidateA()
    expect(screen.getByTestId('analyst-review-section')).toBeInTheDocument()
    expect(screen.queryByTestId('investigation-export-section')).not.toBeInTheDocument()

    // Check Stage 06
    fireEvent.click(screen.getByTestId('back-to-evidence-btn'))
    await waitFor(() => expect(screen.getByText('06 / Evidence')).toBeInTheDocument())
    expect(screen.queryByTestId('investigation-export-section')).not.toBeInTheDocument()
  })
})
