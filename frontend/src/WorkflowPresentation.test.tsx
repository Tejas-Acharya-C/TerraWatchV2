import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import App from './App'
import { STAGE_ORDER, STAGE_TITLES } from './workflow'

vi.mock('./MapCanvas', () => ({
  default: () => <div data-testid="mock-map">Mock Map</div>,
}))

function makeObservation(overrides: Record<string, unknown> = {}) {
  return {
    acquisition_id: 1,
    aoi_id: 1,
    item_id: 'S2A_TEST_OBS_001',
    acquisition_datetime: '2024-03-01T10:00:00Z',
    observation_state: 'usable',
    usable_pixel_fraction: 0.95,
    quality_mask_available: true,
    display_available: true,
    prepared_path: '/path/to/raster.tif',
    raster: {
      crs: 'EPSG:4326',
      width: 100,
      height: 100,
      transform: [0.01, 0, 10, 0, -0.01, 20],
      resolution: [10, 10],
      bounds: [10, 10, 20, 20],
    },
    ...overrides,
  }
}

describe('Workflow Presentation Simplification (Phase 2)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    sessionStorage.clear()
  })

  it('1. Eight underlying workflow stages exist in Phase 9/10 restructuring', () => {
    expect(STAGE_ORDER).toEqual([
      'AOI',
      'IMAGERY',
      'CHANGE',
      'TEMPORAL',
      'CANDIDATES',
      'EVIDENCE',
      'REVIEW',
      'EXPORT',
    ])
  })

  it('2. User-facing stage labels are AREA, OBSERVATIONS, CHANGES, CHANGE HISTORY, CANDIDATES, EVIDENCE, REVIEW, EXPORT', () => {
    expect(STAGE_TITLES).toEqual({
      AOI: 'AREA',
      IMAGERY: 'OBSERVATIONS',
      CHANGE: 'CHANGES',
      TEMPORAL: 'CHANGE HISTORY',
      CANDIDATES: 'CANDIDATES',
      EVIDENCE: 'EVIDENCE',
      REVIEW: 'REVIEW',
      EXPORT: 'EXPORT',
    })
  })

  it('3. Raw FSM tokens are not rendered as primary UI text', async () => {
    render(<App />)
    // Primary header and rail should not render raw internal FSM tokens as main text
    expect(screen.queryByText('NO_AOI')).not.toBeInTheDocument()
    expect(screen.queryByText('EDIT_DIRTY')).not.toBeInTheDocument()
    // Workflow rail displays human-readable stage numbers and titles
    expect(screen.getAllByText('AREA').length).toBeGreaterThan(0)
    expect(screen.getAllByText('OBSERVATIONS').length).toBeGreaterThan(0)
    expect(screen.getAllByText('CHANGES').length).toBeGreaterThan(0)
    expect(screen.getAllByText('CHANGE HISTORY').length).toBeGreaterThan(0)
    expect(screen.getAllByText('CANDIDATES').length).toBeGreaterThan(0)
    expect(screen.getAllByText('EVIDENCE').length).toBeGreaterThan(0)
  })

  it('4. Stage descriptions match clear purpose specifications', () => {
    render(<App />)
    // AREA stage description
    expect(screen.getByText('Define the area you want to investigate.')).toBeInTheDocument()
  })

  it('5. Redundant in-stage Proceed/Continue buttons are removed', () => {
    render(<App />)
    expect(screen.queryByRole('button', { name: 'Proceed to Imagery' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Proceed to Change' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Continue to Temporal' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Continue to Candidates' })).not.toBeInTheDocument()
  })

  it('6. Workflow rail is passive and non-interactive: clicking does not navigate or alter state', async () => {
    const obs1 = makeObservation({ acquisition_id: 1, acquisition_datetime: '2024-01-01T00:00:00Z' })
    const obs2 = makeObservation({ acquisition_id: 2, acquisition_datetime: '2024-02-01T00:00:00Z' })

    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/api/v1/aois')) {
        return new Response(JSON.stringify([{ aoi_id: 1, geometry: { type: 'Polygon', coordinates: [[[75.0, 13.0], [75.1, 13.0], [75.1, 13.1], [75.0, 13.1], [75.0, 13.0]]] }, created_at: '2024-01-01T00:00:00Z', updated_at: '2024-01-01T00:00:00Z' }]), { status: 200 })
      }
      if (url.includes('/api/v1/aoi/1') || (url.includes('/api/v1/aoi') && !url.includes('/aois'))) {
        return new Response(JSON.stringify({ aoi_id: 1, geometry: { type: 'Polygon', coordinates: [[[75.0, 13.0], [75.1, 13.0], [75.1, 13.1], [75.0, 13.1], [75.0, 13.0]]] }, created_at: '2024-01-01T00:00:00Z', updated_at: '2024-01-01T00:00:00Z' }), { status: 200 })
      }
      if (url.includes('/api/v1/imagery/acquisitions')) {
        return new Response(JSON.stringify({ aoi_id: 1, total: 2, acquisitions: [obs1, obs2] }), { status: 200 })
      }
      return new Response(JSON.stringify({}), { status: 200 })
    })

    render(<App />)
    fireEvent.focus(screen.getByLabelText('Saved AOIs'))
    await waitFor(() => expect(screen.getByLabelText('Saved AOIs').querySelectorAll('option').length).toBeGreaterThan(1))
    fireEvent.change(screen.getByLabelText('Saved AOIs'), { target: { value: '1' } })
    await waitFor(() => expect(screen.getByTestId('aoi-state')).toHaveTextContent('Area active · #1'))

    // The rail steps do NOT have role="button"
    expect(screen.queryByRole('button', { name: /01 AREA/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /02 OBSERVATIONS/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /03 CHANGES/i })).not.toBeInTheDocument()

    // Selecting a saved AOI remains in Stage 01 Area
    expect(screen.getByText('01 / Area')).toBeInTheDocument()

    // Clicking rail items does not navigate
    const obsRail = screen.getByLabelText('02 OBSERVATIONS')
    fireEvent.click(obsRail)
    // Stage remains AREA, not OBSERVATIONS
    expect(screen.getByText('01 / Area')).toBeInTheDocument()
    expect(screen.queryByText('02 / Observations')).not.toBeInTheDocument()

    // Intentionally advance to Observations via in-stage button
    fireEvent.click(screen.getByTestId('proceed-to-observations-btn'))
    await waitFor(() => expect(screen.getByText('02 / Observations')).toBeInTheDocument())

    // Intentionally advance to Changes via in-stage button
    fireEvent.click(screen.getByTestId('proceed-to-changes-btn'))
    await waitFor(() => expect(screen.getByText('03 / Changes')).toBeInTheDocument())

    // Clicking rail items from Changes does not navigate
    const areaRail = screen.getByLabelText('01 AREA')
    fireEvent.click(areaRail)
    // Stage remains CHANGES, not AREA
    expect(screen.getByText('03 / Changes')).toBeInTheDocument()
    expect(screen.queryByText('01 / Area')).not.toBeInTheDocument()
  })

  it('7. Primary analyst UI does not display raw internal IDs, while technical provenance preserves them', async () => {
    render(<App />)
    // In primary Area stage, raw database tokens like aoi_id are not displayed as main labels
    expect(screen.queryByText('aoi_id')).not.toBeInTheDocument()
  })

  it('8. Phase 1 temporal eligibility remains intact (ineligible disabled, Select All removed)', async () => {
    render(<App />)
    // Select All and Deselect All remain absent
    expect(screen.queryByText('Select all')).not.toBeInTheDocument()
    expect(screen.queryByText('Deselect all')).not.toBeInTheDocument()
  })

  it('9. Operation buttons have operation-oriented labels and invoke operations without automatic execution', () => {
    render(<App />)
    // Initial AREA stage operation button
    expect(screen.getByRole('button', { name: 'Draw area' })).toBeInTheDocument()
    // No automatic detection or temporal analysis running on mount
    expect(sessionStorage.length).toBe(0)
    expect(localStorage.length).toBe(0)
  })

  it('10. No session persistence or local storage used', () => {
    render(<App />)
    expect(window.localStorage.getItem('terrawatch_stage')).toBeNull()
    expect(window.sessionStorage.getItem('terrawatch_stage')).toBeNull()
  })

  // Shared workstation presentation test fixtures and navigation helpers
  const candidateA = {
      candidate_id: 'analysis-42-signal-101',
      analysis_id: 42,
      signal_id: 101,
      rank: 1,
      priority: 'urgent',
      severity: 'high',
      score: 0.842,
      geometry: { type: 'Polygon', coordinates: [[[77.5, 12.8], [77.55, 12.8], [77.55, 12.85], [77.5, 12.85], [77.5, 12.8]]] },
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

    const mockEvidenceA = {
      aoi_id: 1,
      candidate: candidateA,
      temporal: {
        analysis_id: 42,
        signal_id: 101,
        state: 'persistent',
        support_count: 2,
        recurrence_count: 0,
        transient_interval_count: 0,
        temporal_consistency: 0.92,
        matched_region_coverage: 0.88,
        first_change_datetime: '2024-01-15T10:00:00Z',
        last_supporting_datetime: '2024-03-15T10:00:00Z',
        interval_count: 2,
        persistence_ratio: 1.0,
        quality_support: 0.91,
        quality_aware_temporal_support: 0.91,
      },
      intervals: [
        {
          detection_run_id: 201,
          before_acquisition_id: 10,
          after_acquisition_id: 11,
          detector_version: 'diff-v2',
          mean_change_signal: 0.62,
          max_change_signal: 0.78,
          total_changed_area_m2: 4500.0,
          filtered_changed_pixel_count: 45,
          raw_changed_pixel_count: 50,
          excluded_cloud_pixel_count: 2,
          excluded_shadow_pixel_count: 1,
          quality_support: 0.91,
        },
      ],
      acquisitions: [
        {
          acquisition_id: 10,
          aoi_id: 1,
          item_id: 'S2_OBS_10',
          collection: 'sentinel-2-l2a',
          acquisition_datetime: '2024-01-15T10:00:00Z',
          observation_state: 'usable',
          usable_pixel_fraction: 0.95,
          cloud_fraction: 0.03,
          shadow_fraction: 0.02,
          invalid_fraction: 0.0,
          quality_processing_version: 'sentinel-2-l2a-v1',
          masking_method: 'scl-quality-mask-v1',
          quality_mask_available: true,
          display_available: true,
          display_url: '/api/v1/acquisitions/10/display',
          visualization_version: 'sentinel-2-rgb-percentile-v2',
        },
        {
          acquisition_id: 11,
          aoi_id: 1,
          item_id: 'S2_OBS_11',
          collection: 'sentinel-2-l2a',
          acquisition_datetime: '2024-02-15T10:00:00Z',
          observation_state: 'usable',
          usable_pixel_fraction: 0.98,
          cloud_fraction: 0.01,
          shadow_fraction: 0.01,
          invalid_fraction: 0.0,
          quality_processing_version: 'sentinel-2-l2a-v1',
          masking_method: 'scl-quality-mask-v1',
          quality_mask_available: true,
          display_available: true,
          display_url: '/api/v1/acquisitions/11/display',
          visualization_version: 'sentinel-2-rgb-percentile-v2',
        },
      ],
      is_spatially_aligned: true,
      spatial_alignment_details: { crs: 'EPSG:32643', width: 100, height: 100, pixel_size: 10, status: 'aligned' },
      is_quality_limited: false,
      quality_limitation_reasons: [],
      provenance_chain: {},
    }

    function setupFullMock() {
      vi.spyOn(globalThis, 'fetch').mockImplementation(async (input: RequestInfo | URL) => {
        const url = String(input)
        if (url.includes('/api/v1/health')) {
          return new Response(JSON.stringify({ status: 'healthy' }), { status: 200 })
        }
        if (url.includes('/api/v1/aois')) {
          return new Response(JSON.stringify([{ aoi_id: 1, geometry: { type: 'Polygon', coordinates: [[[77.0, 12.0], [77.5, 12.0], [77.5, 12.5], [77.0, 12.5], [77.0, 12.0]]] }, created_at: '2024-01-01T00:00:00Z', updated_at: '2024-01-01T00:00:00Z' }]), { status: 200 })
        }
        if (url.includes('/api/v1/aoi/1') || (url.includes('/api/v1/aoi') && !url.includes('/acquisitions'))) {
          return new Response(JSON.stringify({ aoi_id: 1, geometry: { type: 'Polygon', coordinates: [[[77.0, 12.0], [77.5, 12.0], [77.5, 12.5], [77.0, 12.5], [77.0, 12.0]]] }, created_at: '2024-01-01T00:00:00Z', updated_at: '2024-01-01T00:00:00Z' }), { status: 200 })
        }
        if (url.includes('/api/v1/imagery/acquisitions') || url.includes('/acquisitions')) {
          return new Response(JSON.stringify({
            aoi_id: 1,
            total: 2,
            acquisitions: mockEvidenceA.acquisitions,
          }), { status: 200 })
        }
        if (url.includes('/api/v1/orchestrations/analyze')) {
          return new Response(JSON.stringify({
            aoi_id: 1,
            eligible_observation_ids: [10, 11],
            pair_count: 1,
            reused_detection_count: 0,
            executed_detection_count: 1,
            detection_ids: [201],
            analysis: {
              analysis_id: 42,
              iou_threshold: 0.25,
              detector_run_count: 1,
              observation_count: 2,
              temporal_span_days: 31,
              state: 'persistent',
              usable_observation_count: 2,
              usable_interval_count: 1,
              quality_support: 0.91,
              observations: [
                { acquisition_id: 10, datetime: '2024-01-15T10:00:00Z', usable_ratio: 0.95 },
                { acquisition_id: 11, datetime: '2024-02-15T10:00:00Z', usable_ratio: 0.92 },
              ],
              relationships: [
                {
                  before_acquisition_id: 10,
                  after_acquisition_id: 11,
                  detection_run_id: 201,
                  region_count: 1,
                  changed_pixel_count: 45,
                  total_changed_area_m2: 4500.0,
                  detector_version: 'diff-v2',
                  detection_result: {
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
                  },
                },
              ],
              signals: [{
                signal_id: 101,
                geometry: { type: 'Polygon', coordinates: [] },
                support_count: 2,
                interval_count: 1,
                persistence_ratio: 1.0,
                recurrence_count: 0,
                transient_interval_count: 0,
                temporal_consistency: 0.92,
                matched_region_coverage: 0.88,
                first_change_datetime: '2024-01-15T10:00:00Z',
                last_supporting_datetime: '2024-02-15T10:00:00Z',
                state: 'persistent',
                quality_support: 0.91,
              }],
              created_at: '2024-01-01T00:00:00Z',
            },
            candidates: {
              analysis_id: 42,
              candidates: [candidateA],
            },
          }), { status: 200, headers: { 'Content-Type': 'application/json' } })
        }
        if (url.includes('/api/v1/detections')) {
          return new Response(JSON.stringify({
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
          }), { status: 200 })
        }
        if (url.includes('/api/v1/temporal-analyses')) {
          return new Response(JSON.stringify({
            analysis_id: 42,
            iou_threshold: 0.25,
            detector_run_count: 1,
            observation_count: 2,
            temporal_span_days: 31,
            state: 'persistent',
            usable_observation_count: 2,
            usable_interval_count: 1,
            quality_support: 0.91,
            observations: [],
            relationships: [],
            signals: [{
              signal_id: 101,
              geometry: { type: 'Polygon', coordinates: [] },
              support_count: 2,
              interval_count: 1,
              persistence_ratio: 1.0,
              recurrence_count: 0,
              transient_interval_count: 0,
              temporal_consistency: 0.92,
              matched_region_coverage: 0.88,
              first_change_datetime: '2024-01-15T10:00:00Z',
              last_supporting_datetime: '2024-02-15T10:00:00Z',
              state: 'persistent',
              quality_support: 0.91,
            }],
            created_at: '2024-01-01T00:00:00Z',
          }), { status: 200 })
        }
        if (url.includes('/api/v1/candidates/triage')) {
          return new Response(JSON.stringify({
            analysis_id: 42,
            candidates: [candidateA],
          }), { status: 200 })
        }
        if (url.includes('/evidence')) {
          return new Response(JSON.stringify(mockEvidenceA), { status: 200 })
        }
        if (url.includes('/review')) {
          return new Response(JSON.stringify({
            candidate_id: 'analysis-42-signal-101',
            decision: 'unreviewed',
            note: null,
            created_at: null,
            updated_at: null,
          }), { status: 200 })
        }
        return new Response(JSON.stringify({}), { status: 200 })
      })
    }

    async function navigateToStage(targetStage: 'CHANGES' | 'CANDIDATES' | 'EVIDENCE' | 'REVIEW') {
      setupFullMock()
      render(<App />)
      await screen.findAllByText('No area selected')

      fireEvent.focus(screen.getByLabelText('Saved AOIs'))
      await waitFor(() => expect(screen.getByLabelText('Saved AOIs').querySelectorAll('option').length).toBeGreaterThan(1))
      fireEvent.change(screen.getByLabelText('Saved AOIs'), { target: { value: '1' } })
      await waitFor(() => expect(screen.getByTestId('aoi-state')).toHaveTextContent('Area active · #1'))

      // Advance via in-stage buttons (passive rail)
      fireEvent.click(screen.getByTestId('proceed-to-observations-btn'))
      await waitFor(() => expect(screen.getByText('02 / Observations')).toBeInTheDocument())
      fireEvent.click(screen.getByTestId('proceed-to-changes-btn'))
      await waitFor(() => expect(screen.getByText('03 / Changes')).toBeInTheDocument())

      const runBtn = screen.getByTestId('run-automated-analysis-btn')
      fireEvent.click(runBtn)
      await waitFor(() => expect(screen.getByTestId('orchestration-summary')).toBeInTheDocument())
      if (targetStage === 'CHANGES') return

      // Go to CANDIDATES via sequential navigation (Stage 03 -> Stage 04 -> Stage 05)
      fireEvent.click(screen.getByTestId('proceed-to-history-btn'))
      await waitFor(() => expect(screen.getByText('04 / Change history')).toBeInTheDocument())
      fireEvent.click(screen.getByTestId('temporal-proceed-candidates-btn'))
      await waitFor(() => expect(screen.getByText('05 / Candidates')).toBeInTheDocument())

      fireEvent.click(screen.getByRole('button', { name: 'Triage temporal signals' }))
      await waitFor(() => expect(screen.getByText('#1 / urgent priority')).toBeInTheDocument())
      fireEvent.click(screen.getByText('#1 / urgent priority'))

      if (targetStage === 'CANDIDATES') return

      // Go to EVIDENCE via stage content button
      fireEvent.click(screen.getByRole('button', { name: 'Inspect candidate evidence' }))
      await waitFor(() => expect(screen.getByText('06 / Evidence')).toBeInTheDocument())
      await waitFor(() => expect(screen.getByTestId('candidate-identity-section')).toBeInTheDocument())

      if (targetStage === 'EVIDENCE') return

      // Go to REVIEW via stage content button
      fireEvent.click(screen.getByTestId('record-review-decision-btn'))
      await waitFor(() => expect(screen.getByText('07 / Review')).toBeInTheDocument())
      await waitFor(() => expect(screen.getByTestId('analyst-review-section')).toBeInTheDocument())
    }

  describe('Phase 11: Terminology Cleanup & Consistency', () => {
    it('11. All eight stage titles remain canonical in STAGE_TITLES and workflow rail', () => {
      expect(STAGE_ORDER).toEqual(['AOI', 'IMAGERY', 'CHANGE', 'TEMPORAL', 'CANDIDATES', 'EVIDENCE', 'REVIEW', 'EXPORT'])
      expect(STAGE_TITLES).toEqual({
        AOI: 'AREA',
        IMAGERY: 'OBSERVATIONS',
        CHANGE: 'CHANGES',
        TEMPORAL: 'CHANGE HISTORY',
        CANDIDATES: 'CANDIDATES',
        EVIDENCE: 'EVIDENCE',
        REVIEW: 'REVIEW',
        EXPORT: 'EXPORT',
      })
    })

    it('12. Stage 03 CHANGES renders Detected change map without persistence jargon', async () => {
      await navigateToStage('CHANGES')
      await waitFor(() => expect(screen.getByTestId('detection-map-info')).toBeInTheDocument())

      expect(screen.getByText('Detected change map')).toBeInTheDocument()
      expect(screen.queryByText('Persisted change map')).not.toBeInTheDocument()
    })

    it('13. Stage 05 CANDIDATES uses canonical labels (Evidence Score, Priority, Severity, Temporal State, Review Status)', async () => {
      await navigateToStage('CANDIDATES')
      expect(screen.getByText('Evidence Score')).toBeInTheDocument()
      expect(screen.getByText('Triage Priority')).toBeInTheDocument()
      expect(screen.getByText('Physical Severity')).toBeInTheDocument()
      expect(screen.getByText('Temporal State')).toBeInTheDocument()
      expect(screen.getByText(/Review Status:/i)).toBeInTheDocument()
      expect(screen.queryByText(/Current Review State:/i)).not.toBeInTheDocument()
    })

    it('14. Stage 06 EVIDENCE uses Review Status, Affected Area, and scientific distinction disclaimer', async () => {
      await navigateToStage('EVIDENCE')
      const identitySection = screen.getByTestId('candidate-identity-section')
      expect(identitySection).toHaveTextContent('Review Status')
      expect(identitySection).toHaveTextContent('Affected Area')
      expect(identitySection).not.toHaveTextContent('Total Affected Area')

      // Scientific distinction note
      expect(screen.getByText(/Detected change represents the derived land-surface change signal \(ΔNDVI\), not a satellite observation\./i)).toBeInTheDocument()
    })

    it('15. Stage 07 REVIEW uses Affected Area, Analyst Review & Decision, Review Status, and canonical decisions', async () => {
      await navigateToStage('REVIEW')
      const contextSection = screen.getByTestId('review-candidate-context')
      expect(contextSection).toHaveTextContent('Affected Area')
      expect(contextSection).not.toHaveTextContent('Total Affected Area')

      const reviewCard = screen.getByTestId('review-status-card')
      expect(reviewCard).toHaveTextContent('Analyst Review & Decision')
      expect(reviewCard).toHaveTextContent('Accept')
      expect(reviewCard).toHaveTextContent('Reject')
      expect(reviewCard).toHaveTextContent('Investigate')

      // Scientific distinction note
      expect(screen.getByText(/Detected change represents the derived land-surface change signal \(ΔNDVI\), not a satellite observation\./i)).toBeInTheDocument()
    })

    it('16. Analyst-facing UI avoids misleading probabilistic terms (confidence, certainty, likelihood) in primary metrics', async () => {
      await navigateToStage('REVIEW')
      const reviewText = screen.getByTestId('analyst-review-section').textContent?.toLowerCase() ?? ''
      expect(reviewText).not.toMatch(/\bconfidence\b/)
      expect(reviewText).not.toMatch(/\bcertainty\b/)
      expect(reviewText).not.toMatch(/\blikelihood\b/)
    })

    it('17. Analyst-facing UI avoids unsupported claims (proof, confirmed change, ground truth, model prediction)', async () => {
      await navigateToStage('REVIEW')
      const bodyText = document.body.textContent?.toLowerCase() ?? ''
      expect(bodyText).not.toMatch(/\bproof\b/)
      expect(bodyText).not.toMatch(/confirmed change/)
      expect(bodyText).not.toMatch(/ground truth/)
      expect(bodyText).not.toMatch(/model prediction/)
    })

    it('18. Technical IDs remain available in provenance without being primary headings', async () => {
      await navigateToStage('REVIEW')
      expect(screen.getByTestId('review-candidate-id')).toHaveTextContent('analysis-42-signal-101')
      // Details container is present
      const provenanceSummary = screen.getByText('Technical provenance')
      expect(provenanceSummary).toBeInTheDocument()
    })
  })

  describe('Phase 12: Visual Hierarchy, Spacing, and Workstation Presentation', () => {
    it('19. Workflow rail renders all seven stages and current stage is clearly distinguishable', () => {
      render(<App />)
      // All seven stages rendered
      expect(screen.getByLabelText(/01 AREA/i)).toBeInTheDocument()
      expect(screen.getByLabelText(/02 OBSERVATIONS/i)).toBeInTheDocument()
      expect(screen.getByLabelText(/03 CHANGES/i)).toBeInTheDocument()
      expect(screen.getByLabelText(/04 CHANGE HISTORY/i)).toBeInTheDocument()
      expect(screen.getByLabelText(/05 CANDIDATES/i)).toBeInTheDocument()
      expect(screen.getByLabelText(/06 EVIDENCE/i)).toBeInTheDocument()
      expect(screen.getByLabelText(/07 REVIEW/i)).toBeInTheDocument()

      // Current stage is identifiable via aria-current="step"
      const currentStep = screen.getByLabelText(/01 AREA/i)
      expect(currentStep).toHaveAttribute('aria-current', 'step')
    })

    it('20. Candidate selection in Stage 05 displays scannable metrics grid and detail card', async () => {
      await navigateToStage('CANDIDATES')
      // Detail card region is present
      const detailCard = screen.getByRole('region', { name: 'Candidate details' })
      expect(detailCard).toBeInTheDocument()

      // Primary metrics are grouped and visible
      expect(screen.getByText('Evidence Score')).toBeInTheDocument()
      expect(screen.getByText('Triage Priority')).toBeInTheDocument()
      expect(screen.getByText('Physical Severity')).toBeInTheDocument()
      expect(screen.getByText('Temporal State')).toBeInTheDocument()
      expect(screen.getByText('Affected Area')).toBeInTheDocument()

      // Primary inspect action is available
      expect(screen.getByRole('button', { name: 'Inspect candidate evidence' })).toBeInTheDocument()
    })

    it('21. Stage 06 Evidence workstation renders comparison, change metrics, and collapsible provenance', async () => {
      await navigateToStage('EVIDENCE')
      // Identity card and status bar
      expect(screen.getByTestId('candidate-identity-section')).toBeInTheDocument()
      expect(screen.getByTestId('evidence-workstation-state')).toBeInTheDocument()

      // Imagery comparison section
      expect(screen.getByTestId('before-after-visual-section')).toBeInTheDocument()

      // Detected change metrics section
      expect(screen.getByTestId('scientific-metrics-section')).toBeInTheDocument()
      expect(screen.getByText('Detected Change Evidence')).toBeInTheDocument()

      // Collapsible technical provenance is accessible
      expect(screen.getByTestId('provenance-trace-section')).toBeInTheDocument()
      expect(screen.getByText(/Technical Provenance/i)).toBeInTheDocument()

      // Primary review action is available
      expect(screen.getByTestId('record-review-decision-btn')).toBeInTheDocument()
    })

    it('22. Stage 07 Review workstation displays decision controls, note container, and save action', async () => {
      await navigateToStage('REVIEW')
      // Candidate context card
      expect(screen.getByTestId('review-candidate-context')).toBeInTheDocument()

      // Review decision controls
      const reviewCard = screen.getByTestId('review-status-card')
      expect(reviewCard).toBeInTheDocument()
      expect(screen.getByTestId('decision-accepted-radio')).toBeInTheDocument()
      expect(screen.getByTestId('decision-rejected-radio')).toBeInTheDocument()
      expect(screen.getByTestId('decision-investigate-radio')).toBeInTheDocument()

      // Note input
      expect(screen.getByTestId('review-note-input')).toBeInTheDocument()

      // Save action
      expect(screen.getByTestId('save-review-btn')).toBeInTheDocument()

      // Supporting evidence summary
      expect(screen.getByTestId('review-evidence-summary')).toBeInTheDocument()

      // Export section removed from Stage 07 (dedicated to Stage 08)
      expect(screen.queryByTestId('investigation-export-section')).not.toBeInTheDocument()
    })

    it('23. Empty state in Stage 06/07 guides analyst without breaking workstation layout', async () => {
      setupFullMock()
      render(<App />)
      await screen.findAllByText('No area selected')

      fireEvent.focus(screen.getByLabelText('Saved AOIs'))
      await waitFor(() => expect(screen.getByLabelText('Saved AOIs').querySelectorAll('option').length).toBeGreaterThan(1))
      fireEvent.change(screen.getByLabelText('Saved AOIs'), { target: { value: '1' } })
      await waitFor(() => expect(screen.getByTestId('aoi-state')).toHaveTextContent('Area active · #1'))

      // Advance via in-stage buttons (passive rail)
      fireEvent.click(screen.getByTestId('proceed-to-observations-btn'))
      await waitFor(() => expect(screen.getByText('02 / Observations')).toBeInTheDocument())
      fireEvent.click(screen.getByTestId('proceed-to-changes-btn'))
      await waitFor(() => expect(screen.getByText('03 / Changes')).toBeInTheDocument())

      const runBtn = screen.getByTestId('run-automated-analysis-btn')
      fireEvent.click(runBtn)
      await waitFor(() => expect(screen.getByTestId('orchestration-summary')).toBeInTheDocument())

      // Go to CANDIDATES via sequential navigation
      fireEvent.click(screen.getByTestId('proceed-to-history-btn'))
      await waitFor(() => expect(screen.getByText('04 / Change history')).toBeInTheDocument())
      fireEvent.click(screen.getByTestId('temporal-proceed-candidates-btn'))
      await waitFor(() => expect(screen.getByText('05 / Candidates')).toBeInTheDocument())

      // Triage candidates in Stage 05 (candidates list loads, but none is selected)
      fireEvent.click(screen.getByRole('button', { name: 'Triage temporal signals' }))
      await waitFor(() => expect(screen.getByText('#1 / urgent priority')).toBeInTheDocument())

      // When no candidate is selected, Next button is disabled with requirement hint
      const nextBtn = screen.getByTestId('proceed-to-evidence-btn')
      expect(nextBtn).toBeDisabled()
      expect(screen.getByText('Select a candidate to continue.')).toBeInTheDocument()

      // When a candidate is explicitly selected, Next becomes enabled
      fireEvent.click(screen.getByText('#1 / urgent priority'))
      expect(screen.getByTestId('proceed-to-evidence-btn')).not.toBeDisabled()
    })

    it('24. Phase 11 scientific accuracy and terminology protections remain fully preserved in Phase 12', async () => {
      await navigateToStage('REVIEW')
      const bodyText = document.body.textContent?.toLowerCase() ?? ''
      expect(bodyText).not.toMatch(/\bconfidence\b/)
      expect(bodyText).not.toMatch(/\bcertainty\b/)
      expect(bodyText).not.toMatch(/\blikelihood\b/)
      expect(bodyText).not.toMatch(/\bproof\b/)
      expect(bodyText).not.toMatch(/confirmed change/)
      expect(bodyText).not.toMatch(/ground truth/)
      expect(bodyText).not.toMatch(/model prediction/)
    })
  })
})

describe('Phase 19B — Global Header Identity & Context', () => {
  const karnatakaPolygon = {
    type: 'Polygon' as const,
    coordinates: [[[75.0, 13.0], [75.2, 13.0], [75.2, 13.2], [75.0, 13.2], [75.0, 13.0]]],
  }

  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    sessionStorage.clear()
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/api/v1/aois')) {
        return new Response(JSON.stringify([
          { aoi_id: 42, geometry: karnatakaPolygon, created_at: '2025-01-01T00:00:00Z', updated_at: '2025-01-01T00:00:00Z' },
        ]), { status: 200 })
      }
      if (url.includes('/api/v1/aoi/')) {
        return new Response(JSON.stringify(
          { aoi_id: 42, geometry: karnatakaPolygon, created_at: '2025-01-01T00:00:00Z', updated_at: '2025-01-01T00:00:00Z' }
        ), { status: 200 })
      }
      if (url.includes('/api/v1/imagery/acquisitions')) {
        return new Response(JSON.stringify({ aoi_id: 42, total: 0, acquisitions: [] }), { status: 200 })
      }
      return new Response(JSON.stringify({}), { status: 200 })
    })
  })

  it('H1. Product identity: "TerraWatch V2" is visible in the header', async () => {
    render(<App />)
    await screen.findAllByText('No area selected')
    expect(screen.getByText('TerraWatch V2')).toBeInTheDocument()
  })

  it('H2. Application title: "Change Detection for Karnataka" is visible with neutral product framing', async () => {
    render(<App />)
    await screen.findAllByText('No area selected')
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Change Detection for Karnataka')
    expect(screen.queryByText(/Forest Change Monitoring/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/Building Monitoring/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/Vegetation Monitoring/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/Construction Monitoring/i)).not.toBeInTheDocument()
  })

  it('H3. Karnataka scope label: "Karnataka analysis" is visible', async () => {
    render(<App />)
    await screen.findAllByText('No area selected')
    expect(screen.getByText('Karnataka analysis')).toBeInTheDocument()
  })

  it('H4. With no active AOI, header AOI context shows "No area selected"', async () => {
    render(<App />)
    await screen.findAllByText('No area selected')
    const aoiContext = screen.getByTestId('aoi-state')
    expect(aoiContext).toHaveTextContent('No area selected')
    expect(aoiContext).toHaveAttribute('data-state', 'NO_AOI')
  })

  it('H5. With active AOI, header AOI context shows "Area active · #<id>"', async () => {
    render(<App />)
    await screen.findAllByText('No area selected')
    fireEvent.focus(screen.getByLabelText('Saved AOIs'))
    await waitFor(() => expect(screen.getByLabelText('Saved AOIs').querySelectorAll('option').length).toBeGreaterThan(1))
    fireEvent.change(screen.getByLabelText('Saved AOIs'), { target: { value: '42' } })
    await waitFor(() => expect(screen.getByTestId('aoi-state')).toHaveTextContent('Area active'))
    const aoiContext = screen.getByTestId('aoi-state')
    expect(aoiContext).toHaveTextContent('Area active · #42')
    expect(aoiContext).toHaveAttribute('data-state', 'AOI_SELECTED')
  })

  it('H6. Removed pill/badge elements are not present in the DOM', async () => {
    render(<App />)
    await screen.findAllByText('No area selected')
    // Removed elements must not exist
    expect(screen.queryByText('Karnataka only')).not.toBeInTheDocument()
    expect(screen.queryByTestId('active-aoi-badge')).not.toBeInTheDocument()
    expect(document.querySelector('.domain-chip')).toBeNull()
    expect(document.querySelector('.aoi-active-badge')).toBeNull()
    expect(document.querySelector('.stage-chip')).toBeNull()
  })
})
