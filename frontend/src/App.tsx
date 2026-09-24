import { useEffect, useMemo, useRef, useState } from 'react'
import MapCanvas from './MapCanvas'
import { appConfig } from './config'
import {
  ApiRequestError,
  displayImageryUrl,
  fetchAcquisitions,
  fetchAOIById,
  fetchAOIList,
  fetchCandidateEvidence,
  fetchCandidateReview,
  saveCandidateReview,
  exportInvestigation,
  requestImagery,
  saveAOI,
  triageCandidates,
  orchestrateAnalysis,
  type OrchestrationProgress,
  type AOIGeometry,
  type AOIResponse,
  type AcquisitionProgress,
  type Candidate,
  type CandidateEvidence,
  type CandidateReview,
  type DetectionResult,
  type EvidenceAcquisitionItem,
  type ImageryAcquisition,
  type ReviewDecision,
  type TemporalResult,
} from './lib/api'
import WorkflowProgressIndicator from './WorkflowProgressIndicator'
import WorkflowNavigation from './WorkflowNavigation'
import { isTemporalEligible } from './lib/temporalEligibility'
import { STAGE_ORDER, STAGE_TITLES, type StageStatus, type WorkflowStage } from './workflow'
import './App.css'

function formatObservationQualityLabel(acq: EvidenceAcquisitionItem | null | undefined): string {
  if (!acq) return 'N/A'
  if (acq.observation_state === 'usable') {
    if (!acq.quality_mask_available || !acq.display_available) {
      return 'usable evidence with quality limitations'
    }
    return 'usable'
  }
  if (acq.observation_state === 'valid_unusable') {
    return 'valid_unusable evidence'
  }
  if (acq.observation_state === 'failed') {
    return 'failed/unavailable evidence'
  }
  if (acq.observation_state === 'legacy_unassessed') {
    return 'legacy_unassessed'
  }
  return acq.observation_state
}

function formatObservationQualityReason(reason?: string | null): string {
  if (!reason) return 'Quality limitation'
  if (reason === 'quality_policy_passed') return 'Meets quality requirements'
  if (reason === 'insufficient_usable_pixels') return 'Cloud/shadow coverage exceeds the usable limit'
  if (reason === 'no_usable_pixels') return 'No usable pixels due to cloud or shadow'
  if (reason === 'quality_not_assessed') return 'Quality not assessed'
  return reason
}

function formatObservationState(state?: string): { label: string; isUsable: boolean } {
  if (state === 'usable') return { label: 'Usable', isUsable: true }
  if (state === 'valid_unusable') return { label: 'Not usable', isUsable: false }
  if (state === 'failed') return { label: 'Failed', isUsable: false }
  if (state === 'legacy_unassessed') return { label: 'Quality not assessed', isUsable: false }
  return { label: state ?? 'Unknown', isUsable: false }
}

function formatAcquisitionProgressMain(progress: AcquisitionProgress | null): string {
  if (!progress || progress.total === 0) return 'Finding observations…'
  if (progress.current === 0) return `Discovered ${progress.total} observations, preparing…`
  return `Processing observations… ${progress.current} of ${progress.total} completed`
}

function getAcquisitionETA(progress: AcquisitionProgress | null, startTime: number | null): string | null {
  if (!progress || progress.total === 0 || !startTime) return null
  if (progress.current >= progress.total) return null
  const elapsedSec = (Date.now() - startTime) / 1000
  if (progress.current < 3 || elapsedSec < 10) {
    return 'Estimating remaining time…'
  }
  const rate = progress.current / elapsedSec
  if (rate <= 0) return 'Estimating remaining time…'
  const remainingSec = Math.max(0, (progress.total - progress.current) / rate)
  if (remainingSec >= 60) {
    const mins = Math.max(1, Math.round(remainingSec / 60))
    return `Estimated time remaining: ~${mins} min`
  }
  const secs = Math.max(5, Math.round(remainingSec / 10) * 10)
  return `Estimated time remaining: ~${secs} sec`
}

const TEMPORAL_STATE_LABELS: Record<string, string> = {
  persistent: 'Persistent',
  recurrent: 'Recurrent',
  transient: 'Transient',
  isolated: 'Isolated',
  insufficient_history: 'Insufficient history',
  no_temporal_signal: 'No temporal signal',
}

const TEMPORAL_STATE_DESCRIPTIONS: Record<string, string> = {
  persistent: 'Change matched across the required temporal intervals.',
  recurrent: 'Change appeared in multiple intervals without continuous persistence.',
  transient: 'Change was observed in a limited interval.',
  isolated: 'Change has insufficient temporal support for persistence classification.',
  insufficient_history: 'Insufficient history. The selected observations do not provide enough valid temporal evidence for this analysis.',
  no_temporal_signal: 'No qualifying change signals persisted across the evaluated intervals.',
}

import { hasUnsavedReviewChanges, type AOIInteractionState } from './reviewDraftGuard'

type WorkstationStatus =
  | AOIInteractionState
  | 'loading'
  | 'drawing'
  | 'valid'
  | 'validation-failure'
  | 'load-failure'
  | 'clearing'
  | 'cleared'
  | 'acquiring'
  | 'acquired'
  | 'imagery-no-results'
  | 'imagery-failure'
  | 'detecting'
  | 'detected'
  | 'detection-failure'
  | 'detection-quality-limited'
  | 'analyzing-temporal'
  | 'temporal-ready'
  | 'temporal-failure'

type StageRecord = { stage: WorkflowStage; status: StageStatus; label: string }



function App() {
  const [geometry, setGeometry] = useState<AOIGeometry | null>(null)
  const [draftGeometry, setDraftGeometry] = useState<AOIGeometry | null>(null)
  const [activeAoiId, setActiveAoiId] = useState<number | null>(null)
  const activeAoiIdRef = useRef<number | null>(activeAoiId)
  activeAoiIdRef.current = activeAoiId
  const pendingAoiIdRef = useRef<number | null>(null)
  const [aoiState, setAoiState] = useState<AOIInteractionState>('NO_AOI')
  const [savedAois, setSavedAois] = useState<AOIResponse[]>([])
  const [draftPositions, setDraftPositions] = useState<number[][]>([])
  const [status, setStatus] = useState<WorkstationStatus>('NO_AOI')
  const [error, setError] = useState('')
  const [startDatetime, setStartDatetime] = useState('')
  const [endDatetime, setEndDatetime] = useState('')
  const [acquisition, setAcquisition] = useState<ImageryAcquisition | null>(null)
  const [acquisitions, setAcquisitions] = useState<ImageryAcquisition[]>([])
  const [acquisitionProgress, setAcquisitionProgress] = useState<AcquisitionProgress | null>(null)
  const acquisitionStartTimeRef = useRef<number | null>(null)
  const [beforeId, setBeforeId] = useState('')
  const [afterId, setAfterId] = useState('')
  const [imageryLayerRole, setImageryLayerRole] = useState<'Before' | 'After'>('After')
  const [detection, setDetection] = useState<DetectionResult | null>(null)
  const [temporal, setTemporal] = useState<TemporalResult | null>(null)
  const [candidates, setCandidates] = useState<Candidate[]>([])
  const [candidateError, setCandidateError] = useState('')
  const [candidateStatus, setCandidateStatus] = useState<'idle' | 'loading' | 'ready' | 'zero' | 'insufficient_history' | 'failure'>('idle')
  const [selectedCandidateId, setSelectedCandidateId] = useState('')
  const selectedCandidateIdRef = useRef(selectedCandidateId)
  useEffect(() => {
    selectedCandidateIdRef.current = selectedCandidateId
  }, [selectedCandidateId])
  const [selectedIntervalIndex, setSelectedIntervalIndex] = useState<number>(0)
  const [orchestrationProgress, setOrchestrationProgress] = useState<OrchestrationProgress | null>(null)
  const [isOrchestrating, setIsOrchestrating] = useState<boolean>(false)
  const [candidatePriorityFilter, setCandidatePriorityFilter] = useState<string>('all')
  const [candidateSeverityFilter, setCandidateSeverityFilter] = useState<string>('all')
  const [candidateReviewFilter, setCandidateReviewFilter] = useState<string>('all')

  const filteredCandidates = useMemo(() => {
    return (candidates || []).filter((c) => {
      if (candidatePriorityFilter !== 'all' && c.priority !== candidatePriorityFilter) return false
      if (candidateSeverityFilter !== 'all' && c.severity !== candidateSeverityFilter) return false
      if (candidateReviewFilter !== 'all' && c.review_state !== candidateReviewFilter) return false
      return true
    })
  }, [candidates, candidatePriorityFilter, candidateSeverityFilter, candidateReviewFilter])

  const [candidateViewMode, setCandidateViewMode] = useState<'top5' | 'all'>('top5')
  const [candidatePage, setCandidatePage] = useState<number>(1)

  const CANDIDATES_PER_PAGE = 20

  const totalCandidatePages = useMemo(() => {
    return Math.max(1, Math.ceil(filteredCandidates.length / CANDIDATES_PER_PAGE))
  }, [filteredCandidates.length])

  const displayedCandidates = useMemo(() => {
    if (candidateViewMode === 'top5') {
      return filteredCandidates.slice(0, 5)
    }
    const startIndex = (candidatePage - 1) * CANDIDATES_PER_PAGE
    return filteredCandidates.slice(startIndex, startIndex + CANDIDATES_PER_PAGE)
  }, [filteredCandidates, candidateViewMode, candidatePage])

  const [evidence, setEvidence] = useState<CandidateEvidence | null>(null)
  const [evidenceStatus, setEvidenceStatus] = useState<'idle' | 'loading' | 'ready' | 'unavailable' | 'failure'>('idle')
  const [evidenceError, setEvidenceError] = useState('')
  const [evidenceImageryMode, setEvidenceImageryMode] = useState<'rgb' | 'ndvi'>('rgb')
  const [stageOverride, setStageOverride] = useState<WorkflowStage | null>(null)
  const [candidateReview, setCandidateReview] = useState<CandidateReview | null>(null)
  const [candidateReviewLoading, setCandidateReviewLoading] = useState(false)
  const [draftDecision, setDraftDecision] = useState<ReviewDecision | null>(null)
  const [draftNote, setDraftNote] = useState<string>('')
  const [reviewSaveStatus, setReviewSaveStatus] = useState<'idle' | 'saving' | 'success' | 'error'>('idle')
  const [reviewSaveError, setReviewSaveError] = useState('')
  const [exportFormat, setExportFormat] = useState<'pdf' | 'json'>('pdf')
  const [exportStatus, setExportStatus] = useState<'idle' | 'exporting' | 'success' | 'error'>('idle')
  const [exportError, setExportError] = useState('')
  const [lastExportedFilename, setLastExportedFilename] = useState<string | null>(null)

  const evidenceWorkstationState: 'NO_CANDIDATE' | 'LOADING' | 'READY' | 'QUALITY_LIMITED' | 'UNAVAILABLE' | 'FAILED' = useMemo(() => {
    if (!selectedCandidateId) return 'NO_CANDIDATE'
    if (evidenceStatus === 'loading') return 'LOADING'
    if (evidenceStatus === 'unavailable') return 'UNAVAILABLE'
    if (evidenceStatus === 'failure') return 'FAILED'
    if (evidenceStatus === 'ready' && evidence) {
      return evidence.is_quality_limited ? 'QUALITY_LIMITED' : 'READY'
    }
    return 'NO_CANDIDATE'
  }, [selectedCandidateId, evidenceStatus, evidence])

  const isEditing = aoiState === 'EDITING' || aoiState === 'EDIT_DIRTY'
  const isDrawing = status === 'drawing' || status === 'validation-failure'
  const visibleGeometry = draftGeometry ?? geometry
  const hasDraftGeometry = draftGeometry !== null
  const beforeAfterInvalid = beforeId !== '' && afterId !== '' && Number(beforeId) === Number(afterId)
  const beforeAcquisition = acquisitions.find((item) => String(item.acquisition_id) === beforeId) ?? null
  const afterAcquisition = acquisitions.find((item) => String(item.acquisition_id) === afterId) ?? null
  const isChronologicalInvalid = Boolean(
    beforeAcquisition &&
    afterAcquisition &&
    !beforeAfterInvalid &&
    beforeAcquisition.acquisition_datetime >= afterAcquisition.acquisition_datetime
  )

  const derivedCurrentStage = useMemo<WorkflowStage>(() => {
    if (!visibleGeometry || status === 'NO_AOI' || status === 'AOI_SELECTED' || status === 'SAVE_SUCCESS' || status === 'drawing' || status === 'valid' || status === 'SAVING' || status === 'clearing' || status === 'validation-failure' || status === 'SAVE_FAILED' || status === 'load-failure' || status === 'EDITING' || status === 'EDIT_DIRTY') return 'AOI'
    if (selectedCandidateId) return 'EVIDENCE'
    if (status === 'acquiring' || status === 'imagery-no-results' || status === 'imagery-failure' || (!acquisition && acquisitions.length === 0)) return 'IMAGERY'
    if (acquisitions.length > 0 || status === 'acquired') return 'CHANGE'
    return 'AOI'
  }, [acquisition, acquisitions.length, selectedCandidateId, status, visibleGeometry])

  const currentStage = stageOverride ?? derivedCurrentStage

  const stage3ActiveInterval = currentStage === 'CHANGE' && temporal?.relationships?.length
    ? (temporal.relationships[selectedIntervalIndex] ?? temporal.relationships[0])
    : null

  const stage4ActiveInterval = currentStage === 'TEMPORAL' && temporal?.relationships?.length
    ? (temporal.relationships[selectedIntervalIndex] ?? temporal.relationships[0])
    : null

  const stage6ActiveInterval = currentStage === 'EVIDENCE' && evidence?.intervals?.length
    ? (evidence.intervals[selectedIntervalIndex] ?? evidence.intervals[0])
    : null

  const activeSatelliteAcquisition = useMemo(() => {
    if (currentStage === 'EVIDENCE' && stage6ActiveInterval) {
      const targetAcqId = imageryLayerRole === 'Before'
        ? stage6ActiveInterval.before_acquisition_id
        : stage6ActiveInterval.after_acquisition_id
      return acquisitions.find((item) => item.acquisition_id === targetAcqId) ?? null
    }
    if ((currentStage === 'CHANGE' || currentStage === 'TEMPORAL') && (stage3ActiveInterval || stage4ActiveInterval)) {
      const activeInv = stage3ActiveInterval || stage4ActiveInterval
      if (activeInv) {
        const targetAcqId = imageryLayerRole === 'Before'
          ? activeInv.before_acquisition_id
          : activeInv.after_acquisition_id
        return acquisitions.find((item) => item.acquisition_id === targetAcqId) ?? null
      }
    }
    return imageryLayerRole === 'Before' ? beforeAcquisition : afterAcquisition
  }, [currentStage, stage6ActiveInterval, stage3ActiveInterval, stage4ActiveInterval, imageryLayerRole, acquisitions, beforeAcquisition, afterAcquisition])

  const activeSatelliteUrl = activeSatelliteAcquisition && activeAoiId ? displayImageryUrl(activeSatelliteAcquisition, activeAoiId) : null
  const activeSatelliteBounds = activeSatelliteAcquisition?.display_metadata?.bounds_wgs84

  useEffect(() => {
    if (!selectedCandidateId) return
    let active = true
    fetchCandidateEvidence(selectedCandidateId).then((response) => {
      if (!active) return
      setEvidence(response)
      setEvidenceStatus('ready')
    }).catch((reason) => {
      if (!active) return
      setEvidence(null)
      setEvidenceStatus(reason instanceof ApiRequestError && reason.code === 'EvidenceUnavailableError' ? 'unavailable' : 'failure')
      setEvidenceError(reason instanceof Error ? reason.message : 'Candidate evidence could not be loaded')
    })
    return () => {
      active = false
    }
  }, [selectedCandidateId])

  useEffect(() => {
    if (!selectedCandidateId) return
    let active = true
    fetchCandidateReview(selectedCandidateId).then((review) => {
      if (!active) return
      setCandidateReview(review)
      setDraftDecision(review.decision === 'unreviewed' ? null : (review.decision as ReviewDecision))
      setDraftNote(review.note ?? '')
      setCandidateReviewLoading(false)
    }).catch((reason) => {
      if (!active) return
      setCandidateReviewLoading(false)
      setReviewSaveError(reason instanceof Error ? reason.message : 'Could not fetch candidate review')
    })
    return () => {
      active = false
    }
  }, [selectedCandidateId])

  function beginDrawing() {
    pendingAoiIdRef.current = null
    activeAoiIdRef.current = null
    setDraftPositions([])
    setDraftGeometry(null)
    setGeometry(null)
    setActiveAoiId(null)
    resetDownstreamAfterAOIChange()
    setError('')
    setStatus('drawing')
    setAoiState('NO_AOI')
  }

  function cancelDrawing() {
    setDraftPositions([])
    setDraftGeometry(null)
    setError('')
    setStatus(activeAoiId ? 'AOI_SELECTED' : 'NO_AOI')
    setAoiState(activeAoiId ? 'AOI_SELECTED' : 'NO_AOI')
  }

  function beginEditing() {
    if (!geometry) return
    setDraftGeometry(JSON.parse(JSON.stringify(geometry)))
    setAoiState('EDITING')
    setStatus('EDITING')
    setStageOverride('AOI')
    setError('')
  }

  function cancelEditing() {
    setDraftGeometry(null)
    setAoiState(activeAoiId ? 'AOI_SELECTED' : 'NO_AOI')
    setStatus(activeAoiId ? 'AOI_SELECTED' : 'NO_AOI')
    setStageOverride(null)
    setError('')
  }

  function handleDraftChange(nextDraft: AOIGeometry) {
    setDraftGeometry(nextDraft)
    setAoiState('EDIT_DIRTY')
    setStatus('EDIT_DIRTY')
    setError('')
  }

  async function refreshSavedAois() {
    try {
      const list = await fetchAOIList()
      if (Array.isArray(list)) {
        setSavedAois(list)
      }
    } catch {
      // non-fatal
    }
  }

  async function handleSelectSavedAoi(aoiIdStr: string) {
    if (!aoiIdStr) {
      if (activeAoiId !== null) {
        pendingAoiIdRef.current = null
        activeAoiIdRef.current = null
        setActiveAoiId(null)
        setGeometry(null)
        setDraftGeometry(null)
        setDraftPositions([])
        resetDownstreamAfterAOIChange()
        setAoiState('NO_AOI')
        setStatus('NO_AOI')
      }
      return
    }
    const targetAoiId = Number(aoiIdStr)
    if (targetAoiId === activeAoiId) return
    pendingAoiIdRef.current = targetAoiId
    setError('')
    setDraftGeometry(null)
    try {
      let selected = savedAois.find((item) => item.aoi_id === targetAoiId)
      if (!selected) {
        selected = await fetchAOIById(targetAoiId)
      }
      if (pendingAoiIdRef.current !== targetAoiId) return
      setGeometry(selected.geometry)
      setActiveAoiId(selected.aoi_id)
      activeAoiIdRef.current = selected.aoi_id
      setDraftPositions([])
      setDraftGeometry(null)
      resetDownstreamAfterAOIChange()
      setAoiState('AOI_SELECTED')
      setStatus('AOI_SELECTED')
      try {
        const persisted = await fetchAcquisitions(selected.aoi_id)
        if (activeAoiIdRef.current !== targetAoiId || pendingAoiIdRef.current !== targetAoiId) return
        if (persisted?.acquisitions) {
          setAcquisitions(persisted.acquisitions.sort((a, b) => a.acquisition_datetime.localeCompare(b.acquisition_datetime)))
        }
      } catch {
        // non-fatal
      }
    } catch (reason) {
      if (pendingAoiIdRef.current !== targetAoiId) return
      setError(reason instanceof Error ? reason.message : 'Failed to load selected AOI')
    }
  }

  function rectangleFromPositions(positions: number[][]): AOIGeometry | null {
    if (positions.length < 2) return null
    const [first, end] = positions
    const minLongitude = Math.min(first[0], end[0])
    const maxLongitude = Math.max(first[0], end[0])
    const minLatitude = Math.min(first[1], end[1])
    const maxLatitude = Math.max(first[1], end[1])
    const width = maxLongitude - minLongitude
    const height = maxLatitude - minLatitude
    if (Math.abs(width) < 1e-8 || Math.abs(height) < 1e-8) return null
    return {
      type: 'Polygon',
      coordinates: [[
        [minLongitude, minLatitude],
        [maxLongitude, minLatitude],
        [maxLongitude, maxLatitude],
        [minLongitude, maxLatitude],
        [minLongitude, minLatitude],
      ]],
    }
  }

  function resetDownstreamAfterAOIChange() {
    setAcquisition(null)
    setAcquisitions([])
    setAcquisitionProgress(null)
    setStartDatetime('')
    setEndDatetime('')
    setBeforeId('')
    setAfterId('')
    setDetection(null)
    setTemporal(null)
    setCandidates([])
    setCandidateStatus('idle')
    setCandidateError('')
    setSelectedCandidateId('')
    setSelectedIntervalIndex(0)
    setEvidence(null)
    setEvidenceStatus('idle')
    setEvidenceError('')
    setCandidateReview(null)
    setCandidateReviewLoading(false)
    setDraftDecision(null)
    setDraftNote('')
    setReviewSaveStatus('idle')
    setReviewSaveError('')
    setExportStatus('idle')
    setExportError('')
    setLastExportedFilename(null)
    setStageOverride(null)
  }

  function resetDownstreamAfterImageryChange() {
    setAcquisitionProgress(null)
    acquisitionStartTimeRef.current = null
    setBeforeId('')
    setAfterId('')
    setDetection(null)
    setTemporal(null)
    setCandidates([])
    setCandidateStatus('idle')
    setCandidateError('')
    setSelectedCandidateId('')
    setSelectedIntervalIndex(0)
    setEvidence(null)
    setEvidenceStatus('idle')
    setEvidenceError('')
    setCandidateReview(null)
    setCandidateReviewLoading(false)
    setDraftDecision(null)
    setDraftNote('')
    setReviewSaveStatus('idle')
    setReviewSaveError('')
    setExportStatus('idle')
    setExportError('')
    setLastExportedFilename(null)
    setStageOverride(null)
  }


  function toBackendDatetime(dateValue: string, kind: 'start' | 'end') {
    if (!dateValue) return ''
    const isoDate = kind === 'start' ? `${dateValue}T00:00:00Z` : `${dateValue}T23:59:59Z`
    const parsed = new Date(isoDate)
    return Number.isNaN(parsed.getTime()) ? '' : parsed.toISOString()
  }


  function finishDrawing() {
    const rectangle = rectangleFromPositions(draftPositions)
    if (!rectangle) {
      setDraftGeometry(null)
      setDraftPositions([])
      setStatus('validation-failure')
      setError('Drag a rectangle with two distinct corners to define a valid AOI.')
      return
    }
    setDraftGeometry(rectangle)
    setDraftPositions([])
    setError('')
    setStatus('valid')
  }

  async function handleSave() {
    const target = draftGeometry
    if (!target || (!isEditing && status !== 'valid')) {
      setStatus('validation-failure')
      setError('Complete a valid rectangle before saving the area.')
      return
    }
    setAoiState('SAVING')
    setStatus('SAVING')
    setError('')
    try {
      const response = await saveAOI(target)
      setGeometry(response.geometry)
      setDraftGeometry(null)
      setActiveAoiId(response.aoi_id)
      activeAoiIdRef.current = response.aoi_id
      pendingAoiIdRef.current = response.aoi_id
      resetDownstreamAfterAOIChange()
      setStageOverride(null)
      setAoiState('SAVE_SUCCESS')
      setStatus('SAVE_SUCCESS')
      refreshSavedAois()
      setTimeout(() => {
        setAoiState('AOI_SELECTED')
        setStatus('AOI_SELECTED')
      }, 0)
    } catch (reason) {
      setAoiState('SAVE_FAILED')
      setStatus('SAVE_FAILED')
      let msg = reason instanceof Error ? reason.message : 'Area could not be saved'
      if (
        (reason instanceof ApiRequestError && reason.code === 'AOIOutsideKarnatakaError') ||
        msg.includes('AOIOutsideKarnatakaError') ||
        msg.toLowerCase().includes('karnataka')
      ) {
        msg = 'Area must be entirely within Karnataka.'
      } else if (msg.includes('/var/') || msg.includes('.db') || msg.includes('traceback') || msg.includes('File ')) {
        msg = 'Area could not be saved due to an internal server error.'
      }
      setError(msg)
    }
  }

  function discardDraft() {
    setDraftGeometry(null)
    setDraftPositions([])
    setError('')
    setStatus(activeAoiId ? 'AOI_SELECTED' : 'NO_AOI')
    setAoiState(activeAoiId ? 'AOI_SELECTED' : 'NO_AOI')
  }



  async function handleAcquire() {
    if (!geometry || !activeAoiId) {
      setStatus('imagery-failure')
      setError('Save the current AOI before requesting imagery.')
      return
    }
    if (!startDatetime || !endDatetime) {
      setStatus('imagery-failure')
      setError('Enter both a start and end date.')
      return
    }
    if (startDatetime > endDatetime) {
      setStatus('imagery-failure')
      setError('End date must be on or after the start date.')
      return
    }
    setAcquisitionProgress(null)
    acquisitionStartTimeRef.current = Date.now()
    setStatus('acquiring')
    setError('')
    try {
      if (!activeAoiId) {
        setStatus('imagery-failure')
        setError('Select a saved AOI before requesting imagery.')
        return
      }
      const targetAoiId = activeAoiId
      const requestStartDatetime = toBackendDatetime(startDatetime, 'start')
      const requestEndDatetime = toBackendDatetime(endDatetime, 'end')
      if (!requestStartDatetime || !requestEndDatetime) {
        setStatus('imagery-failure')
        setError('Enter valid start and end dates.')
        return
      }
      const response = await requestImagery(targetAoiId, requestStartDatetime, requestEndDatetime, (progress) => {
        if (activeAoiIdRef.current !== targetAoiId) return
        setAcquisitionProgress(progress)
      })
      if (activeAoiIdRef.current !== targetAoiId) return
      setAcquisition(response)
      setAcquisitionProgress(null)
      acquisitionStartTimeRef.current = null
      resetDownstreamAfterImageryChange()
      const persistedAcquisitions = await fetchAcquisitions(targetAoiId)
      if (activeAoiIdRef.current !== targetAoiId) return
      if (persistedAcquisitions?.acquisitions) {
        setAcquisitions(persistedAcquisitions.acquisitions.sort((left, right) => left.acquisition_datetime.localeCompare(right.acquisition_datetime)))
      }
      setStatus('acquired')
    } catch (reason) {
      setAcquisitionProgress(null)
      acquisitionStartTimeRef.current = null
      if (activeAoiIdRef.current !== activeAoiId) return
      if (reason instanceof ApiRequestError && reason.code === 'NoSuitableImageryError') {
        setStatus('imagery-no-results')
        setError('')
      } else {
        setStatus('imagery-failure')
        setError(reason instanceof Error ? reason.message : 'Imagery acquisition failed')
      }
    }
  }


  async function handleRunOrchestration() {
    if (!activeAoiId) return
    setIsOrchestrating(true)
    setOrchestrationProgress({ phase: 'preparing_observations', message: 'Evaluating observation eligibility...' })
    setStatus('analyzing-temporal')
    setError('')
    try {
      const response = await orchestrateAnalysis(
        {
          aoi_id: activeAoiId,
          start_datetime: startDatetime ? `${startDatetime}T00:00:00Z` : null,
          end_datetime: endDatetime ? `${endDatetime}T23:59:59Z` : null,
        },
        (progress) => {
          setOrchestrationProgress(progress)
        }
      )
      const analysis = response.analysis ?? (response as any).temporal_analysis ?? null
      const candidateList = response.candidates?.candidates ?? (Array.isArray(response.candidates) ? response.candidates : [])
      setTemporal(analysis)
      setSelectedIntervalIndex(0)
      setCandidates(candidateList)
      setCandidateStatus(candidateList.length ? 'ready' : 'zero')
      setCandidateError('')
      setSelectedCandidateId('')
      setEvidence(null)
      setEvidenceStatus('idle')
      setEvidenceError('')
      setCandidateReview(null)
      setCandidateReviewLoading(false)
      setDraftDecision(null)
      setDraftNote('')
      setReviewSaveStatus('idle')
      setReviewSaveError('')
      setExportStatus('idle')
      setExportError('')
      setLastExportedFilename(null)
      setStatus('temporal-ready')
    } catch (reason) {
      setStatus('temporal-failure')
      const msg = reason instanceof Error ? reason.message : 'Analysis orchestration failed'
      setError(msg)
    } finally {
      setIsOrchestrating(false)
      setOrchestrationProgress(null)
    }
  }

  async function handleTriage() {
    if (!temporal) return
    setCandidateStatus('loading')
    setCandidateError('')
    try {
      const response = await triageCandidates(temporal.analysis_id)
      setCandidates(response.candidates)
      setSelectedCandidateId('')
      setSelectedIntervalIndex(0)
      setEvidence(null)
      setEvidenceStatus('idle')
      setEvidenceError('')
      setCandidateReview(null)
      setCandidateReviewLoading(false)
      setDraftDecision(null)
      setDraftNote('')
      setReviewSaveStatus('idle')
      setReviewSaveError('')
      setExportStatus('idle')
      setExportError('')
      setLastExportedFilename(null)
      setCandidateStatus(response.candidates.length ? 'ready' : 'zero')
    } catch (reason) {
      const msg = reason instanceof Error ? reason.message : 'Candidate triage failed'
      const isInsufficientHistory = (reason instanceof ApiRequestError && reason.code === 'CandidateAnalysisUnavailableError') ||
        msg.includes('insufficient history')
      if (isInsufficientHistory) {
        setCandidateStatus('insufficient_history')
        setCandidateError(msg)
      } else {
        setCandidateStatus('failure')
        setCandidateError(msg)
      }
      setSelectedCandidateId('')
      setSelectedIntervalIndex(0)
      setEvidence(null)
      setEvidenceStatus('idle')
      setEvidenceError('')
      setCandidateReview(null)
      setCandidateReviewLoading(false)
      setDraftDecision(null)
      setDraftNote('')
      setReviewSaveStatus('idle')
      setReviewSaveError('')
      setExportStatus('idle')
      setExportError('')
      setLastExportedFilename(null)
    }
  }

  function selectCandidate(candidateId: string) {
    if (candidateId === selectedCandidateId) return
    if (hasUnsavedReviewChanges(draftDecision, draftNote, candidateReview)) {
      if (!window.confirm('You have unsaved review changes. Discard and switch candidate?')) {
        return
      }
    }
    setSelectedCandidateId(candidateId)
    setSelectedIntervalIndex(0)
    setEvidence(null)
    setEvidenceStatus(candidateId ? 'loading' : 'idle')
    setEvidenceError('')
    setCandidateReview(null)
    setCandidateReviewLoading(Boolean(candidateId))
    setDraftDecision(null)
    setDraftNote('')
    setReviewSaveStatus('idle')
    setReviewSaveError('')
    setExportStatus('idle')
    setExportError('')
    setLastExportedFilename(null)
  }


  async function handleSaveReview(decisionToSave?: ReviewDecision, noteToSave?: string | null) {
    if (!selectedCandidateId) return
    const targetCandidateId = selectedCandidateId
    const decision = decisionToSave ?? draftDecision
    if (!decision) {
      setReviewSaveStatus('error')
      setReviewSaveError('Please select a review decision (Accepted, Rejected, or Investigate).')
      return
    }
    const note = noteToSave !== undefined ? noteToSave : draftNote
    setReviewSaveStatus('saving')
    setReviewSaveError('')
    try {
      const response = await saveCandidateReview(targetCandidateId, decision, note)
      if (selectedCandidateIdRef.current !== targetCandidateId) {
        return // candidate switched while review save was in-flight; discard update to active candidate
      }
      if (response.candidate_id === targetCandidateId) {
        setCandidateReview(response)
        setDraftDecision(response.decision === 'unreviewed' ? null : (response.decision as ReviewDecision))
        setDraftNote(response.note ?? '')
        setReviewSaveStatus('success')
        setCandidates((current) =>
          current.map((item) =>
            item.candidate_id === response.candidate_id
              ? { ...item, review_state: response.decision }
              : item
          )
        )
        setEvidence((curr) => {
          if (!curr || curr.candidate.candidate_id !== response.candidate_id) return curr
          return {
            ...curr,
            candidate: {
              ...curr.candidate,
              review_state: response.decision,
            },
          }
        })
      }
    } catch (reason) {
      if (selectedCandidateIdRef.current !== targetCandidateId) {
        return // ignore error for stale candidate
      }
      setReviewSaveStatus('error')
      setReviewSaveError(reason instanceof Error ? reason.message : 'Review could not be saved')
    }
  }

  async function handleExportInvestigation(targetFormat: 'pdf' | 'json') {
    if (!selectedCandidateId) return
    const targetCandidateId = selectedCandidateId
    setExportFormat(targetFormat)
    setExportStatus('exporting')
    setExportError('')
    try {
      const { blob, filename } = await exportInvestigation(targetCandidateId, targetFormat)
      if (selectedCandidateIdRef.current !== targetCandidateId) {
        return // candidate switched while export was in-flight; discard download
      }
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = filename
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)
      setLastExportedFilename(filename)
      setExportStatus('success')
    } catch (err) {
      if (selectedCandidateIdRef.current !== targetCandidateId) {
        return // candidate switched while export was in-flight; ignore error for stale candidate
      }
      setExportStatus('error')
      setExportError(err instanceof Error ? err.message : 'Export failed')
    }
  }

  const workflowStages = useMemo<StageRecord[]>(() => {
    const currentIndex = STAGE_ORDER.indexOf(currentStage)

    const baseStatusMap: Record<WorkflowStage, StageStatus> = {
      AOI: status === 'SAVING' || status === 'clearing'
        ? 'running'
        : status === 'SAVE_FAILED' || status === 'load-failure' || status === 'validation-failure'
          ? 'failed'
          : activeAoiId
            ? 'complete'
            : visibleGeometry
              ? 'ready'
              : 'ready',
      IMAGERY: status === 'acquiring'
        ? 'running'
        : status === 'imagery-failure'
          ? 'failed'
          : status === 'imagery-no-results'
            ? 'ready'
            : geometry && (acquisition || acquisitions.length > 0)
              ? 'complete'
              : geometry
                ? 'ready'
                : 'locked',
      CHANGE: isOrchestrating || status === 'detecting'
        ? 'running'
        : status === 'detection-failure' || status === 'detection-quality-limited' || status === 'temporal-failure'
          ? 'failed'
          : (temporal || detection)
            ? 'complete'
            : (beforeId && afterId && !beforeAfterInvalid && !isChronologicalInvalid)
              ? 'ready'
              : (acquisitions.length >= 2 || (acquisitions.length >= 1 && Boolean(acquisition)))
                ? 'ready'
                : 'locked',
      TEMPORAL: isOrchestrating || status === 'analyzing-temporal'
        ? 'running'
        : status === 'temporal-failure'
          ? 'failed'
          : temporal
            ? 'complete'
            : (acquisitions.length >= 2 || detection)
              ? 'ready'
              : 'locked',
      CANDIDATES: candidateStatus === 'loading'
        ? 'running'
        : candidateStatus === 'failure'
          ? 'failed'
          : selectedCandidateId
            ? 'complete'
            : (candidates.length > 0 || candidateStatus === 'ready' || candidateStatus === 'zero')
              ? 'ready'
              : temporal
                ? 'ready'
                : 'locked',
      EVIDENCE: evidenceStatus === 'loading'
        ? 'running'
        : evidenceStatus === 'failure' || evidenceStatus === 'unavailable'
          ? 'failed'
          : evidenceStatus === 'ready'
            ? 'complete'
            : selectedCandidateId
              ? 'ready'
              : temporal
                ? 'ready'
                : 'locked',
      REVIEW: reviewSaveStatus === 'saving'
        ? 'running'
        : reviewSaveStatus === 'error'
          ? 'failed'
          : candidateReview?.decision && candidateReview.decision !== 'unreviewed'
            ? 'complete'
            : selectedCandidateId
              ? 'ready'
              : candidateStatus === 'ready'
                ? 'ready'
                : 'locked',
      EXPORT: exportStatus === 'exporting'
        ? 'running'
        : exportStatus === 'error'
          ? 'failed'
          : exportStatus === 'success' || lastExportedFilename
            ? 'complete'
            : (candidateReview?.decision && candidateReview.decision !== 'unreviewed')
              ? 'ready'
              : 'locked',
    }

    return STAGE_ORDER.map((stage, index) => {
      let finalStatus: StageStatus = baseStatusMap[stage]

      // State semantics:
      // - Prior stages: if the workflow has already progressed past this stage,
      //   and it is not currently executing or in an error state,
      //   it visually communicates completed prior state ('complete').
      if (index < currentIndex) {
        if (finalStatus !== 'running' && finalStatus !== 'failed') {
          finalStatus = 'complete'
        }
      }

      return {
        status: finalStatus,
        stage,
        label: STAGE_TITLES[stage],
      }
    })
  }, [
    currentStage,
    acquisition,
    acquisitions.length,
    beforeAfterInvalid,
    isChronologicalInvalid,
    beforeId,
    candidateReview,
    candidateStatus,
    candidates.length,
    detection,
    evidenceStatus,
    geometry,
    isOrchestrating,
    reviewSaveStatus,
    selectedCandidateId,
    status,
    temporal,
    afterId,
    activeAoiId,
    visibleGeometry,
    exportStatus,
    lastExportedFilename,
  ])

  const activeStageTitle = {
    AOI: 'Define the area you want to investigate',
    IMAGERY: 'Choose satellite observations',
    CHANGE: 'Compare observations',
    TEMPORAL: 'Examine change history',
    CANDIDATES: 'Review candidates',
    EVIDENCE: 'EVIDENCE',
    REVIEW: 'REVIEW',
    EXPORT: 'Analysis complete',
  }[currentStage]

  const dependencyNotes = {
    AOI: hasDraftGeometry && !activeAoiId ? 'Requires: draft area ready to save or discard.' : 'Requires: no dependency; start by defining an area.',
    IMAGERY: activeAoiId ? 'Requires: ✓ Area active' : 'Requires: define and save an area in Stage 01 / AREA first.',
    CHANGE: !activeAoiId
      ? 'Requires: define and save an area first in Stage 01 / AREA.'
      : acquisitions.filter((a) => a.observation_state === 'usable').length < 2
        ? `Requires: at least two usable observations acquired in Stage 02 / OBSERVATIONS (${acquisitions.length} observation${acquisitions.length === 1 ? '' : 's'} available).`
        : temporal
          ? 'Automated analysis complete across observation intervals.'
          : 'Ready to run automated analysis across all eligible observations.',
    TEMPORAL: !activeAoiId
      ? 'Requires: define and save an area first in Stage 01 / AREA.'
      : acquisitions.filter((a) => a.observation_state === 'usable').length < 2
        ? `Requires: at least two usable observations acquired in Stage 02 / OBSERVATIONS (${acquisitions.length} observation${acquisitions.length === 1 ? '' : 's'} available).`
        : temporal
          ? 'Automated change history analysis complete.'
          : 'Change history will be analyzed automatically from eligible observation pairs.',
    CANDIDATES: temporal ? 'Requires: ✓ Change history completed' : 'Requires: completed change history from Stage 04 / CHANGE HISTORY.',
    EVIDENCE: selectedCandidateId ? 'Requires: ✓ candidate selected' : 'Requires: select a candidate in Stage 05 / CANDIDATES.',
    REVIEW: selectedCandidateId ? 'Requires: ✓ candidate selected' : 'Requires: select a candidate in Stage 05 / CANDIDATES.',
    EXPORT: candidateReview?.decision && candidateReview.decision !== 'unreviewed' ? 'Requires: ✓ Review decision recorded' : 'Requires: record a review decision in Stage 07 / REVIEW first.',
  }

  const usableObservationsCount = useMemo(() => {
    return acquisitions.filter((a) => a.observation_state === 'usable').length
  }, [acquisitions])

  const currentCandidateIndex = useMemo(() => {
    if (!selectedCandidateId) return -1
    return candidates.findIndex((c) => c.candidate_id === selectedCandidateId)
  }, [candidates, selectedCandidateId])

  function handlePreviousCandidate() {
    if (currentCandidateIndex <= 0) return
    if (hasUnsavedReviewChanges(draftDecision, draftNote, candidateReview)) {
      if (!window.confirm('You have unsaved review changes. Discard and switch candidate?')) {
        return
      }
    }
    const prevCandidate = candidates[currentCandidateIndex - 1]
    if (prevCandidate) {
      selectCandidate(prevCandidate.candidate_id)
    }
  }

  function handleNextCandidate() {
    if (currentCandidateIndex < 0 || currentCandidateIndex >= candidates.length - 1) return
    if (hasUnsavedReviewChanges(draftDecision, draftNote, candidateReview)) {
      if (!window.confirm('You have unsaved review changes. Discard and switch candidate?')) {
        return
      }
    }
    const nextCandidate = candidates[currentCandidateIndex + 1]
    if (nextCandidate) {
      selectCandidate(nextCandidate.candidate_id)
    }
  }

  const navigationConfig = useMemo(() => {
    switch (currentStage) {
      case 'AOI':
        return {
          canPrevious: false,
          canNext: Boolean(activeAoiId),
          previousTestId: undefined,
          nextTestId: 'proceed-to-observations-btn',
          onPrevious: () => {},
          onNext: () => setStageOverride('IMAGERY'),
          nextRequirementHint: !activeAoiId ? 'Save an area to proceed' : undefined,
        }
      case 'IMAGERY':
        return {
          canPrevious: true,
          canNext: usableObservationsCount >= 2,
          previousTestId: 'back-to-area-btn',
          nextTestId: 'proceed-to-changes-btn',
          onPrevious: () => setStageOverride('AOI'),
          onNext: () => setStageOverride('CHANGE'),
          nextRequirementHint: usableObservationsCount < 2 ? 'At least 2 usable observations required' : undefined,
        }
      case 'CHANGE':
        return {
          canPrevious: true,
          canNext: usableObservationsCount >= 2 || Boolean(temporal || detection),
          previousTestId: 'back-to-observations-btn',
          nextTestId: 'proceed-to-history-btn',
          onPrevious: () => setStageOverride('IMAGERY'),
          onNext: () => setStageOverride('TEMPORAL'),
          nextRequirementHint: usableObservationsCount < 2 ? 'At least 2 usable observations required' : undefined,
        }
      case 'TEMPORAL':
        return {
          canPrevious: true,
          canNext: Boolean(temporal),
          previousTestId: 'back-to-changes-btn',
          nextTestId: 'temporal-proceed-candidates-btn',
          onPrevious: () => setStageOverride('CHANGE'),
          onNext: () => setStageOverride('CANDIDATES'),
          nextRequirementHint: !temporal ? 'Requires completed change history' : undefined,
        }
      case 'CANDIDATES':
        return {
          canPrevious: true,
          canNext: Boolean(selectedCandidateId),
          previousTestId: 'back-to-history-btn',
          nextTestId: 'proceed-to-evidence-btn',
          nextAriaLabel: 'Inspect candidate evidence',
          onPrevious: () => setStageOverride('TEMPORAL'),
          onNext: () => setStageOverride('EVIDENCE'),
          nextRequirementHint: !selectedCandidateId ? 'Select a candidate to continue' : undefined,
        }
      case 'EVIDENCE':
        return {
          canPrevious: true,
          canNext: Boolean(evidence && (evidenceWorkstationState === 'READY' || evidenceWorkstationState === 'QUALITY_LIMITED')),
          previousTestId: 'back-to-candidates-btn',
          nextTestId: 'record-review-decision-btn',
          nextAriaLabel: 'Record review decision',
          onPrevious: () => setStageOverride('CANDIDATES'),
          onNext: () => setStageOverride('REVIEW'),
          nextRequirementHint: evidenceWorkstationState !== 'READY' && evidenceWorkstationState !== 'QUALITY_LIMITED' ? 'Evidence must be loaded' : undefined,
        }
      case 'REVIEW':
        return {
          canPrevious: true,
          canNext: Boolean(candidateReview?.decision && candidateReview.decision !== 'unreviewed'),
          previousTestId: 'back-to-evidence-btn',
          nextTestId: 'proceed-to-export-btn',
          onPrevious: () => {
            if (hasUnsavedReviewChanges(draftDecision, draftNote, candidateReview)) {
              if (!window.confirm('You have unsaved review changes. Discard and proceed?')) {
                return
              }
            }
            setStageOverride('EVIDENCE')
          },
          onNext: () => setStageOverride('EXPORT'),
          nextRequirementHint: !(candidateReview?.decision && candidateReview.decision !== 'unreviewed') ? 'Save a review decision to continue' : undefined,
        }
      case 'EXPORT':
        return {
          canPrevious: true,
          canNext: false,
          previousTestId: 'back-to-review-btn',
          nextTestId: undefined,
          onPrevious: () => setStageOverride('REVIEW'),
          onNext: () => {},
          nextRequirementHint: undefined,
        }
      default:
        return {
          canPrevious: false,
          canNext: false,
          previousTestId: undefined,
          nextTestId: undefined,
          onPrevious: () => {},
          onNext: () => {},
          nextRequirementHint: undefined,
        }
    }
  }, [currentStage, activeAoiId, usableObservationsCount, temporal, detection, selectedCandidateId, evidence, evidenceWorkstationState, candidateReview, draftDecision, draftNote])

  const renderCurrentStage = () => {
    switch (currentStage) {
      case 'AOI':
        return (
          <>
            <div className="panel-heading">
              <div>
                <p className="panel-kicker">01 / Area</p>
                <h2>{activeStageTitle}</h2>
              </div>
              <span className="panel-index">AREA</span>
            </div>
            <p className="panel-copy">Define the area you want to investigate.</p>

            <div className="stage-form saved-aois-form">
              <label>
                Saved areas
                <div className="saved-aois-row">
                  <select
                    aria-label="Saved AOIs"
                    value={activeAoiId ?? ''}
                    onChange={(event) => handleSelectSavedAoi(event.target.value)}
                    onFocus={refreshSavedAois}
                    autoComplete="off"
                  >
                    <option value="">{savedAois.length > 0 ? 'Select a saved area' : 'No saved areas loaded'}</option>
                    {savedAois.map((item) => (
                      <option key={item.aoi_id} value={item.aoi_id}>
                        Area #{item.aoi_id} ({new Date(item.created_at).toLocaleDateString()})
                      </option>
                    ))}
                  </select>
                  <button type="button" className="button--quiet button--mini" onClick={refreshSavedAois} title="Refresh saved areas">↺</button>
                </div>
              </label>
            </div>

            {isEditing ? (
              <div className="edit-aoi-container" data-testid="edit-aoi-controls">
                <div className="edit-notice">
                  <strong>Editing area</strong>
                  <p>Drag corner handles to resize. Drag center handle to move. Saving creates a new area record and preserves the original.</p>
                </div>
                <div className="stage-actions">
                  <button
                    type="button"
                    className="button--secondary"
                    onClick={handleSave}
                    disabled={aoiState !== 'EDIT_DIRTY' && aoiState !== 'EDITING'}
                  >
                    Save as new area
                  </button>
                  <button type="button" className="button--quiet" onClick={cancelEditing}>Discard draft</button>
                </div>
              </div>
            ) : (
              <div className="stage-actions">
                {!isDrawing ? (
                  <>
                    <button type="button" onClick={beginDrawing}>Draw area</button>
                    {activeAoiId && (
                      <button type="button" className="button--secondary" onClick={beginEditing}>Edit area</button>
                    )}
                  </>
                ) : (
                  <>
                    <button type="button" onClick={finishDrawing}>Finish rectangle</button>
                    <button type="button" className="button--quiet" onClick={cancelDrawing}>Discard draft</button>
                    <button
                      type="button"
                      className="button--secondary"
                      onClick={handleSave}
                      disabled={!draftGeometry || (status as string) !== 'valid'}
                    >
                      Save as new area
                    </button>
                  </>
                )}
                {!isDrawing && draftGeometry && !activeAoiId && (
                  <>
                    <button
                      type="button"
                      className="button--secondary"
                      onClick={handleSave}
                      disabled={!draftGeometry || status !== 'valid'}
                    >
                      Save as new area
                    </button>
                    <button type="button" className="button--quiet" onClick={discardDraft}>Discard draft</button>
                  </>
                )}
              </div>
            )}
            <p className="dependency-note">{dependencyNotes.AOI}</p>
            <p className="map-hint">{isDrawing ? `Click two corners on the map to define a rectangle within Karnataka (${draftPositions.length} corner${draftPositions.length === 1 ? '' : 's'} placed).` : isEditing ? 'Drag handles on the map to resize or move the area.' : 'Use the map controls to pan and zoom across Karnataka.'}</p>
          </>
        )
      case 'IMAGERY': {
        const usableCount = acquisitions.filter((item) => item.observation_state === 'usable').length
        return (
          <>
            <div className="panel-heading">
              <div>
                <p className="panel-kicker">02 / Observations</p>
                <h2>{activeStageTitle}</h2>
              </div>
              <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
                <span className="panel-index">OBSERVATIONS</span>
              </div>
            </div>
            <p className="panel-copy">Choose the satellite observations available for this area and period.</p>

            <div className="observation-area-summary">
              <p className="block-label">Active area</p>
              {activeAoiId ? (
                <div className="observation-area-badge">
                  <span>Area defined in Karnataka</span>
                  <details className="provenance-details-inline">
                    <summary>Technical details</summary>
                    <small>Area #{activeAoiId}</small>
                  </details>
                </div>
              ) : (
                <span className="state-line">No area selected. Select or define an area in Stage 01 / AREA.</span>
              )}
            </div>

            <div className="stage-form observation-period-form">
              <p className="block-label">Requested observation period</p>
              <div className="observation-date-inputs">
                <label>
                  From
                  <input
                    aria-label="Observation start date"
                    type="date"
                    value={startDatetime}
                    onChange={(event) => setStartDatetime(event.target.value)}
                  />
                </label>
                <label>
                  To
                  <input
                    aria-label="Observation end date"
                    type="date"
                    value={endDatetime}
                    onChange={(event) => setEndDatetime(event.target.value)}
                  />
                </label>
              </div>
              <button
                type="button"
                className="button--secondary"
                onClick={handleAcquire}
                disabled={!geometry || !activeAoiId || status === 'acquiring' || status === 'SAVING'}
                aria-label="Find observations"
              >
                {status === 'acquiring' ? formatAcquisitionProgressMain(acquisitionProgress) : 'Find observations'}
              </button>
            </div>

            {status === 'acquiring' && acquisitionProgress && (
              <div className="acquisition-progress-panel" role="status" data-testid="acquisition-progress-block">
                <p className="acquisition-progress-main">{formatAcquisitionProgressMain(acquisitionProgress)}</p>
                {acquisitionProgress.current > 0 && acquisitionProgress.current < acquisitionProgress.total && (
                  <p className="acquisition-progress-eta">{getAcquisitionETA(acquisitionProgress, acquisitionStartTimeRef.current)}</p>
                )}
              </div>
            )}

            <p className="dependency-note">{dependencyNotes.IMAGERY}</p>

            {acquisitions.length > 0 ? (
              <>
                <div className="observation-count-notice" role="status">
                  {usableCount === 1 ? (
                    <>
                      <strong>1 usable observation available.</strong>
                      <span style={{ display: 'none' }}>1 observation available</span>
                      <small>Two observations are required for change detection.</small>
                      <p style={{ marginTop: '0.25rem', fontSize: '0.8rem' }}>
                        At least 2 usable observations are required to compare before and after conditions. Try a wider or different date range to find additional usable observations.
                      </p>
                    </>
                  ) : usableCount === 0 ? (
                    <>
                      <strong>0 usable observations available.</strong>
                      <small>Two usable observations are required for change detection.</small>
                      <p style={{ marginTop: '0.25rem', fontSize: '0.8rem' }}>
                        We found {acquisitions.length} observation{acquisitions.length === 1 ? '' : 's'}, but none meet the image-quality requirements for change detection. Cloud and shadow filtering excluded them. Try a wider or different date range to find additional usable observations.
                      </p>
                    </>
                  ) : (
                    <span>{usableCount} usable observations available ({acquisitions.length} total acquired).</span>
                  )}
                </div>

                <div className="observations-list" aria-label="Acquired observations">
                  <p className="block-label">Acquired observations</p>
                  {acquisitions.filter((item) => item.observation_state === 'usable').map((item) => {
                    const stateInfo = formatObservationState(item.observation_state)
                    const obsDate = new Date(item.acquisition_datetime)
                    const formattedDate = obsDate.toLocaleDateString(undefined, {
                      day: '2-digit',
                      month: 'short',
                      year: 'numeric',
                    })
                    return (
                      <div
                        key={item.acquisition_id}
                        className={`observation-card ${stateInfo.isUsable ? 'observation-card--usable' : 'observation-card--unusable'}`}
                        data-testid={`observation-item-${item.acquisition_id}`}
                      >
                        <div className="observation-card__header">
                          <div>
                            <small className="observation-card__eyebrow">Actual observation date</small>
                            <strong className="observation-card__date">{formattedDate}</strong>
                          </div>
                          <div className="observation-card__badges">
                            <span className={`obs-badge obs-badge--${stateInfo.isUsable ? 'usable' : 'unusable'}`}>
                              {stateInfo.label}
                            </span>
                            <span className="obs-badge obs-badge--acquired">
                              Acquired
                            </span>
                          </div>
                        </div>

                        {!stateInfo.isUsable && (
                          <div className="observation-card__limitation">
                            <span className="limitation-label">Quality limitation:</span>
                            <span className="limitation-reason">{formatObservationQualityReason(item.quality_reason)}</span>
                            <small className="limitation-note">Unavailable for change detection</small>
                          </div>
                        )}

                        <details className="provenance-details-inline">
                          <summary>Technical details</summary>
                          <small>Observation ID: #{item.acquisition_id}</small>
                          <small>STAC Item: {item.item_id}</small>
                          <span style={{ display: 'none' }}>{item.item_id}</span>
                          <small>Collection: {item.collection}</small>
                          {item.raster && (
                            <small>Raster: {item.raster.crs} · {item.raster.width}×{item.raster.height}</small>
                          )}
                          {item.quality_reason && (
                            <small>Persisted reason: {item.quality_reason}</small>
                          )}
                          {item.quality_metrics && (
                            <small>
                              Metrics: usable {String((item.quality_metrics as Record<string, unknown>).usable_percentage ?? '')}% · cloud {String((item.quality_metrics as Record<string, unknown>).cloud_percentage ?? '')}% · shadow {String((item.quality_metrics as Record<string, unknown>).shadow_percentage ?? '')}%
                            </small>
                          )}
                        </details>
                      </div>
                    )
                  })}
                </div>

                {acquisitions.filter((item) => item.observation_state !== 'usable').length > 0 && (
                  <details className="unusable-observations-section">
                    <summary>Excluded observations ({acquisitions.filter((item) => item.observation_state !== 'usable').length}) — quality/cloud screening</summary>
                    <div className="observations-list" style={{ marginTop: '0.5rem' }}>
                      {acquisitions.filter((item) => item.observation_state !== 'usable').map((item) => {
                        const stateInfo = formatObservationState(item.observation_state)
                        const obsDate = new Date(item.acquisition_datetime)
                        const formattedDate = obsDate.toLocaleDateString(undefined, {
                          day: '2-digit',
                          month: 'short',
                          year: 'numeric',
                        })
                        return (
                          <div
                            key={item.acquisition_id}
                            className="observation-card observation-card--unusable"
                            data-testid={`observation-item-${item.acquisition_id}`}
                          >
                            <div className="observation-card__header">
                              <div>
                                <small className="observation-card__eyebrow">Actual observation date</small>
                                <strong className="observation-card__date">{formattedDate}</strong>
                              </div>
                              <div className="observation-card__badges">
                                <span className="obs-badge obs-badge--unusable">
                                  {stateInfo.label}
                                </span>
                                <span className="obs-badge obs-badge--acquired">
                                  Acquired
                                </span>
                              </div>
                            </div>

                            <div className="observation-card__limitation">
                              <span className="limitation-label">Quality limitation:</span>
                              <span className="limitation-reason">{formatObservationQualityReason(item.quality_reason)}</span>
                              <small className="limitation-note">Unavailable for change detection</small>
                            </div>

                            <details className="provenance-details-inline">
                              <summary>Technical details</summary>
                              <small>Observation ID: #{item.acquisition_id}</small>
                              <small>STAC Item: {item.item_id}</small>
                              <span style={{ display: 'none' }}>{item.item_id}</span>
                              <small>Collection: {item.collection}</small>
                              {item.quality_reason && (
                                <small>Persisted reason: {item.quality_reason}</small>
                              )}
                              {item.quality_metrics && (
                                <small>
                                  Metrics: usable {String((item.quality_metrics as Record<string, unknown>).usable_percentage ?? '')}% · cloud {String((item.quality_metrics as Record<string, unknown>).cloud_percentage ?? '')}% · shadow {String((item.quality_metrics as Record<string, unknown>).shadow_percentage ?? '')}%
                                </small>
                              )}
                            </details>
                          </div>
                        )
                      })}
                    </div>
                  </details>
                )}
              </>
            ) : status === 'imagery-no-results' ? (
              <div className="observation-state-block observation-state-block--no-results" role="status">
                <strong>No matching observations</strong>
                <p>No satellite observations were found for this date range.</p>
                <small>Try a wider date range to find additional observations.</small>
              </div>
            ) : status === 'imagery-failure' ? (
              <div className="observation-state-block observation-state-block--failed" role="alert">
                <strong>Observation search failed</strong>
                <p>{error || 'Observation search could not be completed.'}</p>
              </div>
            ) : (
              <div className="observation-empty-notice">
                <p className="dependency-note">No observations acquired for this area yet. Select a date range and find observations.</p>
              </div>
            )}
            {acquisitions.length > 0 && status === 'imagery-failure' && (
              <div className="observation-state-block observation-state-block--failed" role="alert" style={{ marginTop: '0.5rem' }}>
                <strong>Observation search failed</strong>
                <p>{error || 'Observation search could not be completed.'}</p>
              </div>
            )}
          </>
        )
      }
      case 'CHANGE': {
        const usableObservations = acquisitions.filter((a) => a.observation_state === 'usable')

        return (
          <>
            <div className="panel-heading">
              <div>
                <p className="panel-kicker">03 / Changes</p>
                <h2>{activeStageTitle}</h2>
              </div>
              <span className="panel-index">CHANGES</span>
            </div>
            <p className="panel-copy">Analyze land-surface changes across observation intervals.</p>

            {isOrchestrating && (
              <div className="detection-state-block detection-state-block--running" data-testid="orchestration-progress" role="status">
                <span className="state-line">ANALYZING</span>
                <strong>{orchestrationProgress?.message || 'Analyzing observations…'}</strong>
                {orchestrationProgress?.total_pairs !== undefined && orchestrationProgress.total_pairs > 0 && (
                  <small>
                    Evaluating interval {(orchestrationProgress.pair_index ?? 0) + 1} of {orchestrationProgress.total_pairs}
                  </small>
                )}
              </div>
            )}

            {(status === 'detection-failure' || status === 'temporal-failure') && (
              <div className="detection-state-block detection-state-block--failed" role="alert">
                <span className="state-line">FAILED</span>
                <strong>Analysis failed</strong>
                <span>{error || 'Automated analysis could not be completed.'}</span>
              </div>
            )}

            <div className="stage-form" style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
              <button
                type="button"
                className="button--secondary"
                onClick={handleRunOrchestration}
                disabled={!activeAoiId || usableObservations.length < 2 || isOrchestrating}
                aria-label="Run automated analysis"
                data-testid="run-automated-analysis-btn"
              >
                {isOrchestrating ? 'Analyzing observations…' : temporal ? 'Re-run automated analysis' : 'Run automated analysis'}
              </button>
            </div>

            <p className="dependency-note">{dependencyNotes.CHANGE}</p>

            {temporal ? (
              <>
                <div className="info-block" data-testid="orchestration-summary">
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.5rem' }}>
                    <p className="block-label">Automated analysis summary</p>
                  </div>
                  <strong>{temporal.relationships?.length ?? 0} observation interval{(temporal.relationships?.length ?? 0) === 1 ? '' : 's'} evaluated</strong>
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: '0.5rem', marginTop: '0.5rem' }}>
                    <div><span style={{ fontSize: '0.75rem', opacity: 0.8 }}>Observations: </span><strong>{temporal.observation_count ?? temporal.observations?.length ?? 0}</strong></div>
                    <div><span style={{ fontSize: '0.75rem', opacity: 0.8 }}>Change signals: </span><strong>{temporal.signals?.length ?? 0}</strong></div>
                    <div><span style={{ fontSize: '0.75rem', opacity: 0.8 }}>Timeline span: </span><strong>{temporal.temporal_span_days} days</strong></div>
                    <div><span style={{ fontSize: '0.75rem', opacity: 0.8 }}>History state: </span><strong>{TEMPORAL_STATE_LABELS[temporal.state] ?? temporal.state}</strong></div>
                  </div>
                </div>

                {temporal.relationships && temporal.relationships.length > 0 && (() => {
                  const intervals = temporal.relationships
                  const activeInv = intervals[selectedIntervalIndex] ?? intervals[0]
                  const beforeAcq = acquisitions.find((a) => a.acquisition_id === activeInv.before_acquisition_id)
                  const afterAcq = acquisitions.find((a) => a.acquisition_id === activeInv.after_acquisition_id)
                  const beforeDate = beforeAcq ? new Date(beforeAcq.acquisition_datetime).toLocaleDateString(undefined, { day: '2-digit', month: 'short', year: 'numeric' }) : `#${activeInv.before_acquisition_id}`
                  const afterDate = afterAcq ? new Date(afterAcq.acquisition_datetime).toLocaleDateString(undefined, { day: '2-digit', month: 'short', year: 'numeric' }) : `#${activeInv.after_acquisition_id}`
                  const beforePreview = beforeAcq && 'preview_url' in beforeAcq ? beforeAcq.preview_url : undefined
                  const afterPreview = afterAcq && 'preview_url' in afterAcq ? afterAcq.preview_url : undefined

                  return (
                    <div className="interval-inspection-container" role="region" aria-label="Change interval inspection">
                      <div className="stage-form" style={{ marginTop: '0.75rem' }}>
                        <label>
                          Inspect change interval
                          <select
                            aria-label="Inspect interval"
                            data-testid="interval-select"
                            value={selectedIntervalIndex}
                            onChange={(e) => setSelectedIntervalIndex(Number(e.target.value))}
                          >
                            {intervals.map((inv, idx) => {
                              const b = acquisitions.find((a) => a.acquisition_id === inv.before_acquisition_id)
                              const a = acquisitions.find((a) => a.acquisition_id === inv.after_acquisition_id)
                              const bDate = b ? new Date(b.acquisition_datetime).toLocaleDateString() : `#${inv.before_acquisition_id}`
                              const aDate = a ? new Date(a.acquisition_datetime).toLocaleDateString() : `#${inv.after_acquisition_id}`
                              return (
                                <option key={idx} value={idx}>
                                  Interval {idx + 1}: {bDate} → {aDate} ({inv.region_count} region{inv.region_count === 1 ? '' : 's'})
                                </option>
                              )
                            })}
                          </select>
                        </label>
                      </div>

                      <div className="layer-switcher" aria-label="Satellite imagery layer">
                        <button
                          type="button"
                          className={imageryLayerRole === 'Before' ? 'layer-switcher__active' : 'button--quiet'}
                          onClick={() => setImageryLayerRole('Before')}
                          disabled={!beforeAcq}
                        >
                          BEFORE
                        </button>
                        <button
                          type="button"
                          className={imageryLayerRole === 'After' ? 'layer-switcher__active' : 'button--quiet'}
                          onClick={() => setImageryLayerRole('After')}
                          disabled={!afterAcq}
                        >
                          AFTER
                        </button>
                      </div>
                      <p className="dependency-note">
                        {activeSatelliteAcquisition
                          ? activeSatelliteUrl
                            ? `Sentinel-2 display ready · ${imageryLayerRole} · ${new Date(activeSatelliteAcquisition.acquisition_datetime).toLocaleDateString()}`
                            : 'No display imagery is available for this observation.'
                          : 'Select an observation interval to display satellite imagery.'}
                      </p>

                      <div className="change-overview">
                        <div className="change-card">
                          <div className="change-card__header">
                            <p className="block-label">Interval Before</p>
                            <span className="obs-badge obs-badge--usable">Usable</span>
                          </div>
                          <strong className="change-card__date">{beforeDate}</strong>
                          {beforePreview ? (
                            <img src={`${appConfig.apiBaseUrl}${beforePreview}`} alt="Before imagery preview" />
                          ) : (
                            <div className="preview-placeholder preview-placeholder--unavailable">
                              <span>UNAVAILABLE</span>
                              <small>Prepared imagery preview unavailable for this observation.</small>
                            </div>
                          )}
                          {beforeAcq && (
                            <details className="provenance-details-inline">
                              <summary>Technical details</summary>
                              <small>Observation ID: #{beforeAcq.acquisition_id}</small>
                              <small>STAC Item: {beforeAcq.item_id}</small>
                              <small>Collection: {beforeAcq.collection}</small>
                            </details>
                          )}
                        </div>

                        <div className="change-card">
                          <div className="change-card__header">
                            <p className="block-label">Interval After</p>
                            <span className="obs-badge obs-badge--usable">Usable</span>
                          </div>
                          <strong className="change-card__date">{afterDate}</strong>
                          {afterPreview ? (
                            <img src={`${appConfig.apiBaseUrl}${afterPreview}`} alt="After imagery preview" />
                          ) : (
                            <div className="preview-placeholder preview-placeholder--unavailable">
                              <span>UNAVAILABLE</span>
                              <small>Prepared imagery preview unavailable for this observation.</small>
                            </div>
                          )}
                          {afterAcq && (
                            <details className="provenance-details-inline">
                              <summary>Technical details</summary>
                              <small>Observation ID: #{afterAcq.acquisition_id}</small>
                              <small>STAC Item: {afterAcq.item_id}</small>
                              <small>Collection: {afterAcq.collection}</small>
                            </details>
                          )}
                        </div>

                        <div className="change-card change-card--result" data-testid="detection-map-info">
                          <p className="block-label">Detected change map</p>
                          <div className="detection-state-block detection-state-block--complete">
                            <strong>
                              {activeInv.region_count === 0
                                ? 'No meaningful change detected (0 change regions)'
                                : `${activeInv.region_count} detected change region${activeInv.region_count === 1 ? '' : 's'}`}
                            </strong>
                            <div className="detection-summary-metrics">
                              <span><strong>Changed pixels:</strong> {(activeInv.changed_pixel_count ?? activeInv.detection_result?.changed_pixel_count ?? 0).toLocaleString()}</span>
                              {activeInv.quality_support !== undefined && (
                                <span><strong>Quality support:</strong> {((activeInv.quality_support) * 100).toFixed(0)}%</span>
                              )}
                              {activeInv.elapsed_days !== undefined && (
                                <span><strong>Elapsed days:</strong> {activeInv.elapsed_days.toFixed(1)} d</span>
                              )}
                            </div>
                            <details className="provenance-details-inline">
                              <summary>Technical details</summary>
                              <small>Detection Run: #{activeInv.detection_run_id}</small>
                              <small>Detector Version: ndvi-absolute-difference-v1</small>
                              <small>Threshold: 0.20</small>
                              <small>Min Region Size: 4 pixels</small>
                            </details>
                          </div>
                        </div>
                      </div>
                    </div>
                  )
                })()}
              </>
            ) : (
              <div className="observation-empty-notice">
                <span className="state-line">READY FOR ANALYSIS</span>
                <p>
                  {usableObservations.length >= 2
                    ? `${usableObservations.length} usable observations ready for automatic analysis.`
                    : 'At least two usable observations are required for automated analysis.'}
                </p>
              </div>
            )}
          </>
        )
      }
      case 'TEMPORAL': {
        const eligibleAcquisitions = acquisitions.filter(isTemporalEligible)

        return (
          <>
            <div className="panel-heading">
              <div>
                <p className="panel-kicker">04 / Change history</p>
                <h2>{activeStageTitle}</h2>
              </div>
              <span className="panel-index">CHANGE HISTORY</span>
            </div>
            <p className="panel-copy">Examine how detected change behaves across observations.</p>

            {isOrchestrating && (
              <div className="temporal-state-block temporal-state-block--running" data-testid="orchestration-progress" role="status">
                <span className="state-line">ANALYZING</span>
                <strong>{orchestrationProgress?.message || 'Analyzing change history…'}</strong>
              </div>
            )}

            {status === 'temporal-failure' && (
              <div className="temporal-state-block temporal-state-block--failed" role="status">
                <strong>Temporal analysis could not be completed</strong>
                <p>{error || 'Change history analysis could not be completed.'}</p>
              </div>
            )}

            {!temporal && (
              <div className="stage-form">
                <button
                  type="button"
                  className="button--secondary"
                  onClick={handleRunOrchestration}
                  disabled={eligibleAcquisitions.length < 2 || isOrchestrating}
                  aria-label="Run automated analysis"
                  data-testid="run-automated-analysis-temporal-btn"
                >
                  {isOrchestrating ? 'Analyzing change history…' : 'Run automated analysis'}
                </button>
              </div>
            )}

            <p className="dependency-note">{dependencyNotes.TEMPORAL}</p>

            {!temporal && eligibleAcquisitions.length > 0 && (
              <div className="observation-timeline" role="region" aria-label="Eligible observation sequence">
                <p className="timeline-title">Available Eligible Observations ({eligibleAcquisitions.length})</p>
                <div className="timeline-items">
                  {eligibleAcquisitions.map((item, idx) => (
                    <div key={item.acquisition_id} className="timeline-node">
                      <div className="timeline-node__date">
                        <strong>{new Date(item.acquisition_datetime).toLocaleDateString()}</strong>
                        <small style={{ display: 'block', opacity: 0.8 }}>ID #{item.acquisition_id}</small>
                      </div>
                      {idx < eligibleAcquisitions.length - 1 && (
                        <div className="timeline-node__connector" aria-hidden="true">
                          ↓
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}

            {temporal && (() => {
              const stateKey = temporal.state
              const stateTitle = TEMPORAL_STATE_LABELS[stateKey] ?? stateKey
              const stateDesc = TEMPORAL_STATE_DESCRIPTIONS[stateKey] ?? ''
              const signals = temporal.signals ?? []
              const persistentCount = signals.filter((s) => s.state === 'persistent').length
              const recurrentCount = signals.filter((s) => s.state === 'recurrent').length
              const transientCount = signals.filter((s) => s.state === 'transient').length
              const isolatedCount = signals.filter((s) => s.state === 'isolated').length

              const intervals = temporal.relationships ?? []
              const hasSelectedInterval = selectedIntervalIndex >= 0 && selectedIntervalIndex < intervals.length
              const activeInterval = hasSelectedInterval ? intervals[selectedIntervalIndex] : (intervals.length > 0 ? intervals[0] : null)

              return (
                <div className="temporal-result-workspace" role="region" aria-label="Temporal analysis results">
                  <div className="info-block temporal-summary-card">
                    <div className="temporal-summary-header">
                      <p className="block-label">Change history</p>
                      <span className={`temporal-state-badge temporal-state-badge--${stateKey}`} data-testid="temporal-state-badge">
                        {stateTitle}
                      </span>
                    </div>
                    <strong>{stateTitle}</strong>
                    {stateDesc && <p className="temporal-state-explanation">{stateDesc}</p>}

                    <div className="temporal-summary-metrics-grid">
                      <div className="temporal-metric-item">
                        <span className="metric-label">Observations</span>
                        <span className="metric-value">{temporal.observation_count ?? temporal.observations?.length ?? 0}</span>
                      </div>
                      <div className="temporal-metric-item">
                        <span className="metric-label">Intervals</span>
                        <span className="metric-value">{temporal.detector_run_count ?? intervals.length}</span>
                      </div>
                      <div className="temporal-metric-item">
                        <span className="metric-label">Timeline span</span>
                        <span className="metric-value">{temporal.temporal_span_days} days</span>
                      </div>
                      <div className="temporal-metric-item">
                        <span className="metric-label">Identified signals</span>
                        <span className="metric-value">{signals.length}</span>
                      </div>
                    </div>

                    {signals.length > 0 && (
                      <div className="temporal-signals-breakdown" aria-label="Signal classifications">
                        <span className="breakdown-item">Persistent: <strong>{persistentCount}</strong></span>
                        <span className="breakdown-item">Recurrent: <strong>{recurrentCount}</strong></span>
                        <span className="breakdown-item">Transient: <strong>{transientCount}</strong></span>
                        <span className="breakdown-item">Isolated: <strong>{isolatedCount}</strong></span>
                      </div>
                    )}

                    <div className="temporal-context-indicators">
                      {temporal.quality_support !== undefined && (
                        <span className="temporal-context-pill temporal-context-pill--quality">
                          {((temporal.quality_support) * 100).toFixed(0)}% quality support
                        </span>
                      )}
                      {temporal.seasonal_interpretation && (
                        <span className="temporal-context-pill temporal-context-pill--seasonal">
                          {temporal.seasonal_interpretation === 'seasonal_compatible'
                            ? 'Seasonally comparable'
                            : 'Less seasonally comparable'}
                        </span>
                      )}
                      <span className="temporal-context-pill temporal-context-pill--history">
                        {temporal.state === 'insufficient_history' ? 'Insufficient history' : 'Sufficient history'}
                      </span>
                    </div>
                  </div>

                  {intervals.length > 0 && (
                    <div className="temporal-intervals-section" role="region" aria-label="Temporal intervals inspection">
                      <div className="interval-selector-bar" role="region" aria-label="Temporal intervals">
                        <span className="interval-selector-title">Observation Intervals ({intervals.length} evaluated):</span>
                        <div className="interval-pills" role="tablist">
                          {intervals.map((inv, idx) => {
                            const beforeAcq = acquisitions.find((a) => a.acquisition_id === inv.before_acquisition_id)
                            const afterAcq = acquisitions.find((a) => a.acquisition_id === inv.after_acquisition_id)
                            const beforeLabel = beforeAcq ? new Date(beforeAcq.acquisition_datetime).toLocaleDateString() : `#${inv.before_acquisition_id}`
                            const afterLabel = afterAcq ? new Date(afterAcq.acquisition_datetime).toLocaleDateString() : `#${inv.after_acquisition_id}`
                            const isSelected = selectedIntervalIndex === idx
                            return (
                              <button
                                key={`interval-tab-${inv.before_acquisition_id}-${inv.after_acquisition_id}-${idx}`}
                                type="button"
                                role="tab"
                                aria-selected={isSelected}
                                className={`interval-pill ${isSelected ? 'interval-pill--active' : ''}`}
                                onClick={() => setSelectedIntervalIndex(idx)}
                              >
                                Interval {idx + 1}: {beforeLabel} → {afterLabel}
                              </button>
                            )
                          })}
                        </div>
                      </div>

                      {activeInterval && (() => {
                        const beforeAcq = acquisitions.find((a) => a.acquisition_id === activeInterval.before_acquisition_id)
                        const afterAcq = acquisitions.find((a) => a.acquisition_id === activeInterval.after_acquisition_id)
                        const beforeDate = beforeAcq ? new Date(beforeAcq.acquisition_datetime).toLocaleDateString() : `#${activeInterval.before_acquisition_id}`
                        const afterDate = afterAcq ? new Date(afterAcq.acquisition_datetime).toLocaleDateString() : `#${activeInterval.after_acquisition_id}`
                        return (
                          <div className="interval-detail-card" role="region" aria-label="Active interval details">
                            <div className="interval-detail-header">
                              <strong>Interval Detail: {beforeDate} → {afterDate}</strong>
                              <span className="interval-status-badge--ready">✓ Detection evaluated</span>
                            </div>
                            <div className="interval-metrics-grid">
                              <div className="interval-metric-item">
                                <span className="metric-label">Change Regions</span>
                                <span className="metric-value">{activeInterval.region_count}</span>
                              </div>
                              <div className="interval-metric-item">
                                <span className="metric-label">Changed Pixels</span>
                                <span className="metric-value">{(activeInterval.changed_pixel_count ?? activeInterval.detection_result?.changed_pixel_count ?? 0).toLocaleString()}</span>
                              </div>
                              {activeInterval.quality_support !== undefined && (
                                <div className="interval-metric-item">
                                  <span className="metric-label">Quality Support</span>
                                  <span className="metric-value">{((activeInterval.quality_support) * 100).toFixed(0)}%</span>
                                </div>
                              )}
                              {activeInterval.elapsed_days !== undefined && (
                                <div className="interval-metric-item">
                                  <span className="metric-label">Elapsed Days</span>
                                  <span className="metric-value">{activeInterval.elapsed_days.toFixed(1)} d</span>
                                </div>
                              )}
                            </div>
                          </div>
                        )
                      })()}
                    </div>
                  )}

                  {signals.length > 0 && (
                    <details className="temporal-signals-details info-block" style={{ marginTop: '0.75rem' }}>
                      <summary style={{ cursor: 'pointer', fontWeight: 600 }}>
                        Detected Change Signals ({signals.length})
                      </summary>
                      <div className="temporal-signals-list" role="region" aria-label="Detected change signals" style={{ marginTop: '0.5rem' }}>
                        {signals.map((sig) => {
                          const onsetDate = sig.first_change_datetime || sig.onset_start_datetime
                          const formattedOnset = onsetDate ? new Date(onsetDate).toLocaleDateString() : null
                          return (
                            <div key={sig.signal_id} className="temporal-signal-item" data-testid={`temporal-signal-${sig.signal_id}`}>
                              <div className="temporal-signal-header">
                                <span className="signal-state-badge">{TEMPORAL_STATE_LABELS[sig.state] ?? sig.state}</span>
                                <span className="signal-support-info">{sig.support_count} of {sig.interval_count} intervals supported</span>
                              </div>
                              {formattedOnset && (
                                <p className="signal-onset-info">
                                  First observed in this history: <strong>{formattedOnset}</strong>
                                </p>
                              )}
                              <div className="signal-meta-row">
                                {sig.quality_support !== undefined && (
                                  <small className="signal-quality-note">
                                    {((sig.quality_support) * 100).toFixed(0)}% quality support
                                  </small>
                                )}
                                {sig.seasonal_interpretation && (
                                  <small className="signal-seasonal-note">
                                    {sig.seasonal_interpretation === 'seasonal_compatible'
                                      ? 'Seasonally comparable'
                                      : 'Less seasonally comparable'}
                                  </small>
                                )}
                              </div>
                            </div>
                          )
                        })}
                      </div>
                    </details>
                  )}

                  <details className="provenance-block">
                    <summary>Technical provenance</summary>
                    <div className="provenance-details-grid">
                      <div><span>Analysis ID:</span> <code>{temporal.analysis_id}</code></div>
                      <div><span>Temporal State:</span> <code>{temporal.state}</code></div>
                      <div><span>IoU Threshold:</span> <code>{temporal.iou_threshold}</code></div>
                      {temporal.quality_aggregation_method && (
                        <div><span>Quality Aggregation:</span> <code>{temporal.quality_aggregation_method}</code></div>
                      )}
                      <div><span>Evaluated Observations:</span> <code>{temporal.observations?.map((o) => o.acquisition_id).join(', ') || temporal.observation_count}</code></div>
                      <div><span>Detection Runs:</span> <code>{temporal.relationships?.map((r) => r.detection_run_id).join(', ') || temporal.detector_run_count}</code></div>
                      {temporal.signals && temporal.signals.length > 0 && (
                        <div><span>Signal IDs:</span> <code>{temporal.signals.map((s) => s.signal_id).join(', ')}</code></div>
                      )}
                    </div>
                  </details>
                </div>
              )
            })()}
          </>
        )
      }
      case 'CANDIDATES':
        return (
          <>
            <div className="panel-heading">
              <div>
                <p className="panel-kicker">05 / Candidates</p>
                <h2>{activeStageTitle}</h2>
              </div>
              <span className="panel-index">CANDIDATES</span>
            </div>
            <p className="panel-copy">Review detected changes and select a candidate for investigation.</p>
            <div className="stage-form">
              <button
                type="button"
                className="button--secondary"
                onClick={handleTriage}
                disabled={!temporal || candidateStatus === 'loading'}
                aria-label="Triage temporal signals"
              >
                Generate candidates
              </button>
            </div>
            <p className="dependency-note">{dependencyNotes.CANDIDATES}</p>
            {candidateStatus === 'loading' && <p className="candidate-message">Generating candidates from evaluated change history…</p>}
            {candidateStatus === 'zero' && (
              <div className="candidate-message candidate-message--neutral" role="status">
                <p>Analysis completed. No qualifying candidate changes were detected across the evaluated history.</p>
              </div>
            )}
            {(candidateStatus === 'insufficient_history' || (temporal?.state === 'insufficient_history' && candidateStatus === 'idle')) && (
              <div className="candidate-message candidate-message--notice" role="status">
                <p>Candidate generation is unavailable: the evaluated change history has insufficient temporal history. Additional eligible observations and the required adjacent change detections are needed to establish temporal behavior.</p>
              </div>
            )}
            {candidateStatus === 'failure' && (
              <div className="candidate-message candidate-message--failed" role="alert">
                <strong>Candidate triage failed</strong>
                <p>{candidateError || 'Candidate triage request could not be completed.'}</p>
              </div>
            )}
            {candidates.length > 0 && (
              <div className="triage-filters" aria-label="Candidate triage filters">
                <label className="filter-item">
                  <span>Priority</span>
                  <select aria-label="Filter by priority" value={candidatePriorityFilter} onChange={(e) => setCandidatePriorityFilter(e.target.value)}>
                    <option value="all">All Priorities</option>
                    <option value="urgent">Urgent</option>
                    <option value="high">High</option>
                    <option value="normal">Normal</option>
                    <option value="low">Low</option>
                  </select>
                </label>
                <label className="filter-item">
                  <span>Severity</span>
                  <select aria-label="Filter by severity" value={candidateSeverityFilter} onChange={(e) => setCandidateSeverityFilter(e.target.value)}>
                    <option value="all">All Severities</option>
                    <option value="high">High</option>
                    <option value="medium">Medium</option>
                    <option value="low">Low</option>
                  </select>
                </label>
                <label className="filter-item">
                  <span>Review</span>
                  <select aria-label="Filter by review status" value={candidateReviewFilter} onChange={(e) => setCandidateReviewFilter(e.target.value)}>
                    <option value="all">All Statuses</option>
                    <option value="unreviewed">Unreviewed</option>
                    <option value="accepted">Accepted</option>
                    <option value="rejected">Rejected</option>
                    <option value="investigate">Investigate</option>
                  </select>
                </label>
              </div>
            )}
            {candidates.length > 0 && (
              <div className="candidate-views-toggle" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: '0.75rem', marginBottom: '0.5rem' }}>
                <span style={{ fontSize: '0.85rem', fontWeight: 600 }}>
                  {candidateViewMode === 'top5' ? 'Top 5 Candidates' : `All Candidates (${filteredCandidates.length})`}
                </span>
                <button
                  type="button"
                  className="button--quiet button--mini"
                  data-testid="toggle-candidate-view-mode-btn"
                  onClick={() => {
                    setCandidateViewMode((m) => (m === 'top5' ? 'all' : 'top5'))
                    setCandidatePage(1)
                  }}
                >
                  {candidateViewMode === 'top5' ? 'View all candidates →' : '← View Top 5 only'}
                </button>
              </div>
            )}
            {candidateViewMode === 'top5' && candidates.length > 0 && (
              <p className="top5-explanation" style={{ fontSize: '0.8rem', color: '#888', margin: '0 0 0.5rem 0' }}>
                Showing the 5 highest-ranked candidates first. Ranking uses the existing operational evidence score.
              </p>
            )}
            {displayedCandidates.length > 0 && (
              <div className="candidate-list">
                {displayedCandidates.map((candidate) => (
                  <button
                    key={candidate.candidate_id}
                    type="button"
                    className={`candidate-item${candidate.candidate_id === selectedCandidateId ? ' candidate-item--selected' : ''}`}
                    aria-selected={candidate.candidate_id === selectedCandidateId}
                    onClick={() => selectCandidate(candidate.candidate_id)}
                  >
                    <span>
                      <strong>#{candidate.rank} / {candidate.priority} priority</strong>
                      <small>evidence score: {candidate.score.toFixed(3)} / {candidate.severity} severity</small>
                      <small>{candidate.metrics?.temporal_state ?? (candidate as any).temporal_state} / quality support: {(((candidate.metrics?.quality_support ?? (candidate as any).quality_support) ?? 0) * 100).toFixed(0)}%</small>
                    </span>
                    <em data-testid={`review-status-badge-${candidate.rank}`} data-candidate-id={candidate.candidate_id}>{candidate.review_state}</em>
                  </button>
                ))}
              </div>
            )}
            {candidateViewMode === 'all' && totalCandidatePages > 1 && (
              <div className="candidate-pagination" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: '0.5rem' }}>
                <button
                  type="button"
                  className="button--quiet button--mini"
                  disabled={candidatePage <= 1}
                  onClick={() => setCandidatePage((p) => Math.max(1, p - 1))}
                >
                  ← Previous Page
                </button>
                <span style={{ fontSize: '0.8rem' }}>Page {candidatePage} of {totalCandidatePages}</span>
                <button
                  type="button"
                  className="button--quiet button--mini"
                  disabled={candidatePage >= totalCandidatePages}
                  onClick={() => setCandidatePage((p) => Math.min(totalCandidatePages, p + 1))}
                >
                  Next Page →
                </button>
              </div>
            )}
            {candidates.length > 0 && filteredCandidates.length === 0 && (
              <p className="candidate-message">No candidates match the selected triage filters.</p>
            )}
            {!selectedCandidateId && (
              <div className="candidate-selection-placeholder" style={{ marginTop: '1rem' }}>
                <p>Select a candidate to continue.</p>
                <p className="subtle-note">Select a candidate from the list above to inspect its assessment and evidence.</p>
              </div>
            )}
            {selectedCandidateId && (() => {
              const selectedCandidate = candidates.find((item) => item.candidate_id === selectedCandidateId)
              if (!selectedCandidate) return null
              return (
                <div className="candidate-detail-card" role="region" aria-label="Candidate details">
                  <div className="candidate-detail-header">
                    <h4>Candidate Assessment</h4>
                    <span className="candidate-badge">Rank #{selectedCandidate.rank} · {selectedCandidate.severity} severity · {selectedCandidate.priority} priority</span>
                  </div>
                  <details className="provenance-block">
                    <summary>Technical provenance</summary>
                    <div className="provenance-details-grid">
                      <div><span>Candidate ID:</span> <code>{selectedCandidate.candidate_id}</code></div>
                      <div><span>Analysis ID:</span> <code>{selectedCandidate.analysis_id}</code></div>
                      <div><span>Signal ID:</span> <code>{selectedCandidate.signal_id}</code></div>
                      {selectedCandidate.detection_run_ids && selectedCandidate.detection_run_ids.length > 0 && (
                        <div><span>Detection Runs:</span> <code>{selectedCandidate.detection_run_ids.join(', ')}</code></div>
                      )}
                      {selectedCandidate.acquisition_ids && selectedCandidate.acquisition_ids.length > 0 && (
                        <div><span>Observations:</span> <code>{selectedCandidate.acquisition_ids.join(', ')}</code></div>
                      )}
                      {selectedCandidate.metrics?.score_components && Object.entries(selectedCandidate.metrics.score_components).map(([k, v]) => (
                        <div key={k}><span>{k}:</span> <code>{typeof v === 'number' ? v.toFixed(4) : String(v)}</code></div>
                      ))}
                    </div>
                  </details>
                  <div className="candidate-metrics-grid">
                    <div className="candidate-metric-item">
                      <span className="metric-label">Evidence Score</span>
                      <span className="metric-value">{selectedCandidate.score.toFixed(3)}</span>
                    </div>
                    <div className="candidate-metric-item">
                      <span className="metric-label">Triage Priority</span>
                      <span className="metric-value">{selectedCandidate.priority}</span>
                    </div>
                    <div className="candidate-metric-item">
                      <span className="metric-label">Physical Severity</span>
                      <span className="metric-value">{selectedCandidate.severity}</span>
                    </div>
                    <div className="candidate-metric-item">
                      <span className="metric-label">Temporal State</span>
                      <span className="metric-value">{selectedCandidate.metrics.temporal_state}</span>
                    </div>
                    <div className="candidate-metric-item">
                      <span className="metric-label">Quality Support</span>
                      <span className="metric-value">{selectedCandidate.metrics.quality_support !== undefined ? `${((selectedCandidate.metrics.quality_support ?? 0) * 100).toFixed(0)}%` : 'N/A'}</span>
                    </div>
                    <div className="candidate-metric-item">
                      <span className="metric-label">Affected Area</span>
                      <span className="metric-value">{selectedCandidate.metrics.total_area_m2 ? `${selectedCandidate.metrics.total_area_m2.toFixed(0)} m²` : 'N/A'}</span>
                    </div>
                    {selectedCandidate.metrics.mean_change_signal !== undefined && selectedCandidate.metrics.mean_change_signal !== 0 && (
                      <div className="candidate-metric-item">
                        <span className="metric-label">Mean ΔNDVI</span>
                        <span className="metric-value">{selectedCandidate.metrics.mean_change_signal.toFixed(2)}</span>
                      </div>
                    )}
                    {selectedCandidate.metrics.first_change_datetime && (
                      <div className="candidate-metric-item">
                        <span className="metric-label">First Observed</span>
                        <span className="metric-value">{new Date(selectedCandidate.metrics.first_change_datetime).toLocaleDateString()}</span>
                      </div>
                    )}
                  </div>

                  {selectedCandidate.metrics.explanation && (
                    <div className="candidate-explanation-block">
                      <p className="candidate-explanation-summary">{selectedCandidate.metrics.explanation.summary}</p>
                      {selectedCandidate.metrics.explanation.positive_factors && selectedCandidate.metrics.explanation.positive_factors.length > 0 && (
                        <div className="factor-list factor-list--positive">
                          <span className="factor-title">Supporting factors:</span>
                          <ul>
                            {selectedCandidate.metrics.explanation.positive_factors.map((f, i) => (
                              <li key={i}>{f}</li>
                            ))}
                          </ul>
                        </div>
                      )}
                      {selectedCandidate.metrics.explanation.limiting_factors && selectedCandidate.metrics.explanation.limiting_factors.length > 0 && (
                        <div className="factor-list factor-list--limiting">
                          <span className="factor-title">Limiting factors:</span>
                          <ul>
                            {selectedCandidate.metrics.explanation.limiting_factors.map((f, i) => (
                              <li key={i}>{f}</li>
                            ))}
                          </ul>
                        </div>
                      )}
                      {selectedCandidate.metrics.explanation.severity_rationale && (
                        <p className="candidate-explanation-rationale">
                          <strong>Severity:</strong> {selectedCandidate.metrics.explanation.severity_rationale}
                        </p>
                      )}
                      {selectedCandidate.metrics.explanation.priority_rationale && (
                        <p className="candidate-explanation-rationale">
                          <strong>Priority:</strong> {selectedCandidate.metrics.explanation.priority_rationale}
                        </p>
                      )}
                    </div>
                  )}

                  <div className="candidate-review-status-row">
                    <span className="candidate-review-status-tag">Review Status: <strong>{selectedCandidate.review_state}</strong></span>
                  </div>
                </div>
              )
            })()}
          </>
        )
      case 'EVIDENCE': {
        const intervals = evidence?.intervals ?? []
        const safeIntervalIndex = selectedIntervalIndex >= 0 && selectedIntervalIndex < intervals.length ? selectedIntervalIndex : 0
        const activeInterval = intervals[safeIntervalIndex] ?? null
        const beforeAcq = activeInterval && evidence?.acquisitions
          ? evidence.acquisitions.find((entry) => entry.acquisition_id === activeInterval.before_acquisition_id)
          : null
        const afterAcq = activeInterval && evidence?.acquisitions
          ? evidence.acquisitions.find((entry) => entry.acquisition_id === activeInterval.after_acquisition_id)
          : null

        return (
          <>
            <div className="panel-heading">
              <div>
                <p className="panel-kicker">06 / Evidence</p>
                <h2>{activeStageTitle}</h2>
              </div>
              <span className="panel-index">EVIDENCE</span>
            </div>
            <p className="panel-copy">Inspect the selected candidate's supporting satellite evidence, observations, and detected change.</p>

            <div className="evidence-workstation-status-bar" data-testid="evidence-workstation-state">
              <span className={`status-badge status-badge--${evidenceWorkstationState.toLowerCase()}`}>
                {evidenceWorkstationState}
              </span>
              <span className="workstation-state-hint">
                {evidenceWorkstationState === 'NO_CANDIDATE' && 'No candidate selected. Select a candidate in Stage 05 / CANDIDATES.'}
                {evidenceWorkstationState === 'LOADING' && 'Resolving candidate provenance and loading evidence artifacts...'}
                {evidenceWorkstationState === 'READY' && 'Candidate evidence fully resolved and validated.'}
                {evidenceWorkstationState === 'QUALITY_LIMITED' && 'Evidence resolved with material observation quality limitations.'}
                {evidenceWorkstationState === 'UNAVAILABLE' && (evidenceError || 'Required evidence artifact or provenance is unavailable.')}
                {evidenceWorkstationState === 'FAILED' && (evidenceError || 'Backend or provenance validation failure.')}
              </span>
            </div>

            <p className="dependency-note">{dependencyNotes.EVIDENCE}</p>

            {evidenceWorkstationState === 'NO_CANDIDATE' && (
              <div className="info-block empty-workstation-block" data-testid="no-candidate-state">
                <p className="block-label">Analyst Action Required</p>
                <p className="empty-message">No candidate is currently selected for evidence inspection. Select a candidate in Stage 05 / CANDIDATES.</p>
                <button type="button" className="button--secondary" onClick={() => setStageOverride('CANDIDATES')}>
                  Select candidate in Stage 05 / CANDIDATES
                </button>
              </div>
            )}

            {evidenceWorkstationState === 'LOADING' && (
              <p className="candidate-message" role="status">Loading candidate evidence...</p>
            )}

            {evidenceWorkstationState === 'UNAVAILABLE' && (
              <div className="info-block error-block" role="alert">
                <p className="block-label">Evidence Unavailable</p>
                <p>{evidenceError || 'Required evidence artifact or provenance is unavailable.'}</p>
              </div>
            )}

            {evidenceWorkstationState === 'FAILED' && (
              <div className="info-block error-block" role="alert">
                <p className="block-label">Evidence Retrieval Failed</p>
                <p className="error">{evidenceError || 'Evidence request failed.'}</p>
              </div>
            )}

            {(evidenceWorkstationState === 'READY' || evidenceWorkstationState === 'QUALITY_LIMITED') && evidence && activeInterval && (
              <div className="evidence-workstation-content">
                {/* 1. Selected Candidate Context */}
                <div className="info-block evidence-identity-card" data-testid="candidate-identity-section">
                  <div className="candidate-detail-header">
                    <h4>Candidate #{evidence.candidate.rank} — {evidence.candidate.priority.charAt(0).toUpperCase() + evidence.candidate.priority.slice(1)} priority</h4>
                  </div>
                  <div className="candidate-metrics-grid">
                    <div className="candidate-metric-item">
                      <span className="metric-label">Triage Priority</span>
                      <span className="metric-value" data-testid="evidence-priority">{evidence.candidate.priority}</span>
                    </div>
                    <div className="candidate-metric-item">
                      <span className="metric-label">Physical Severity</span>
                      <span className="metric-value" data-testid="evidence-severity">{evidence.candidate.severity}</span>
                    </div>
                    <div className="candidate-metric-item">
                      <span className="metric-label">Evidence Score</span>
                      <span className="metric-value" data-testid="evidence-score">{evidence.candidate.score.toFixed(3)}</span>
                    </div>
                    <div className="candidate-metric-item">
                      <span className="metric-label">Temporal State</span>
                      <span className="metric-value" data-testid="evidence-temporal-state">{evidence.candidate.metrics.temporal_state ?? evidence.temporal.state}</span>
                    </div>
                    <div className="candidate-metric-item">
                      <span className="metric-label">Affected Area</span>
                      <span className="metric-value">
                        {evidence.candidate.metrics.total_area_m2
                          ? `${evidence.candidate.metrics.total_area_m2.toFixed(1)} m²`
                          : (activeInterval.total_changed_area_m2 ? `${activeInterval.total_changed_area_m2.toFixed(1)} m²` : 'N/A')}
                      </span>
                    </div>
                    <div className="candidate-metric-item">
                      <span className="metric-label">Onset Date</span>
                      <span className="metric-value">
                        {evidence.candidate.metrics.first_change_datetime
                          ? new Date(evidence.candidate.metrics.first_change_datetime).toLocaleDateString()
                          : (evidence.temporal.first_change_datetime ? new Date(evidence.temporal.first_change_datetime).toLocaleDateString() : 'N/A')}
                      </span>
                    </div>
                    <div className="candidate-metric-item">
                      <span className="metric-label">Review Status</span>
                      <span className="metric-value" data-testid="evidence-review-state">{evidence.candidate.review_state}</span>
                      <span data-testid="current-review-decision" style={{ display: 'none' }}>{(evidence.candidate.review_state ?? 'UNREVIEWED').toUpperCase()}</span>
                    </div>
                  </div>
                  {evidence.is_quality_limited && evidence.quality_limitation_reasons?.length > 0 && (
                    <div className="quality-limitation-alert" role="alert">
                      <strong>Material Observation Quality Limitation:</strong>
                      <ul>
                        {evidence.quality_limitation_reasons.map((reason, idx) => (
                          <li key={idx}>{reason}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>

                {/* 2. Before / After Temporal Comparison */}
                <div className="info-block evidence-imagery-section" data-testid="before-after-visual-section">
                  <div className="imagery-section-header">
                    <div>
                      <p className="block-label">Satellite Evidence Comparison</p>
                    </div>
                    <div className="imagery-mode-switcher" aria-label="Visual comparison mode">
                      <button
                        type="button"
                        className={evidenceImageryMode === 'rgb' ? 'button--mode-active' : 'button--quiet button--mini'}
                        onClick={() => setEvidenceImageryMode('rgb')}
                      >
                        Sentinel-2 Natural RGB
                      </button>
                      <button
                        type="button"
                        className={evidenceImageryMode === 'ndvi' ? 'button--mode-active' : 'button--quiet button--mini'}
                        onClick={() => setEvidenceImageryMode('ndvi')}
                      >
                        Supporting NDVI Index
                      </button>
                    </div>
                  </div>

                  <span data-testid="evidence-distinct-notice" style={{ display: 'none' }} aria-hidden="true">
                    Before #{activeInterval.before_acquisition_id} ≠ After #{activeInterval.after_acquisition_id}
                  </span>

                  {intervals.length > 1 && (
                    <div className="interval-selector-bar" role="region" aria-label="Temporal intervals">
                      <span className="interval-selector-title">Interval ({intervals.length} available):</span>
                      <div className="interval-pills" role="tablist">
                        {intervals.map((inv, idx) => (
                          <button
                            key={idx}
                            type="button"
                            role="tab"
                            aria-selected={idx === safeIntervalIndex}
                            className={`interval-pill ${idx === safeIntervalIndex ? 'interval-pill--active' : ''}`}
                            onClick={() => setSelectedIntervalIndex(idx)}
                          >
                            Interval {idx + 1} (#{inv.before_acquisition_id} → #{inv.after_acquisition_id})
                          </button>
                        ))}
                      </div>
                    </div>
                  )}

                  <div className="evidence-comparison-grid">
                    {/* Before Card */}
                    <div className="evidence-obs-card" data-testid="before-observation-card">
                      <div className="obs-card-header">
                        <span className="obs-tag obs-tag--before">BEFORE</span>
                        <strong data-testid="before-date">
                          {beforeAcq?.acquisition_datetime ? new Date(beforeAcq.acquisition_datetime).toLocaleDateString(undefined, { day: '2-digit', month: 'short', year: 'numeric' }) : 'N/A'}
                        </strong>
                      </div>
                      <div className="obs-metadata-rows">
                        <span className="obs-meta-item" data-testid="before-acquisition-id">
                          <strong>Acquisition</strong> #{beforeAcq?.acquisition_id ?? activeInterval.before_acquisition_id}
                        </span>
                        <span className="obs-meta-item" data-testid="before-stac-id">
                          <strong>STAC Item:</strong> {beforeAcq?.item_id ?? 'N/A'}
                        </span>
                        <span className="obs-meta-item">
                          <strong>Display Artifact:</strong> {beforeAcq?.display_available ? 'READY' : 'UNAVAILABLE'}
                        </span>
                      </div>
                      <div className="evidence-image-container">
                        {evidenceImageryMode === 'rgb' ? (
                          beforeAcq?.display_available && beforeAcq.display_url ? (
                            <img src={`${appConfig.apiBaseUrl}${beforeAcq.display_url}`} alt="Before Sentinel-2 RGB imagery" />
                          ) : (
                            <div className="preview-placeholder preview-placeholder--unavailable">
                              <span>UNAVAILABLE</span>
                              <small>Satellite RGB observation unavailable</small>
                            </div>
                          )
                        ) : (
                          beforeAcq?.preview_url ? (
                            <img src={`${appConfig.apiBaseUrl}${beforeAcq.preview_url}`} alt="Before NDVI index preview" />
                          ) : (
                            <div className="preview-placeholder preview-placeholder--unavailable">
                              <span>UNAVAILABLE</span>
                              <small>Satellite NDVI observation unavailable</small>
                            </div>
                          )
                        )}
                      </div>
                      <span className="evidence-image-caption">
                        {evidenceImageryMode === 'rgb' ? 'Sentinel-2 Natural RGB (percentile-v2 · B04/B03/B02)' : 'Candidate NDVI Detection Preview (B04/B08)'}
                      </span>
                    </div>

                    {/* After Card */}
                    <div className="evidence-obs-card" data-testid="after-observation-card">
                      <div className="obs-card-header">
                        <span className="obs-tag obs-tag--after">AFTER</span>
                        <strong data-testid="after-date">
                          {afterAcq?.acquisition_datetime ? new Date(afterAcq.acquisition_datetime).toLocaleDateString(undefined, { day: '2-digit', month: 'short', year: 'numeric' }) : 'N/A'}
                        </strong>
                      </div>
                      <div className="obs-metadata-rows">
                        <span className="obs-meta-item" data-testid="after-acquisition-id">
                          <strong>Acquisition</strong> #{afterAcq?.acquisition_id ?? activeInterval.after_acquisition_id}
                        </span>
                        <span className="obs-meta-item" data-testid="after-stac-id">
                          <strong>STAC Item:</strong> {afterAcq?.item_id ?? 'N/A'}
                        </span>
                        <span className="obs-meta-item">
                          <strong>Display Artifact:</strong> {afterAcq?.display_available ? 'READY' : 'UNAVAILABLE'}
                        </span>
                      </div>
                      <div className="evidence-image-container">
                        {evidenceImageryMode === 'rgb' ? (
                          afterAcq?.display_available && afterAcq.display_url ? (
                            <img src={`${appConfig.apiBaseUrl}${afterAcq.display_url}`} alt="After Sentinel-2 RGB imagery" />
                          ) : (
                            <div className="preview-placeholder preview-placeholder--unavailable">
                              <span>UNAVAILABLE</span>
                              <small>Satellite RGB observation unavailable</small>
                            </div>
                          )
                        ) : (
                          afterAcq?.preview_url ? (
                            <img src={`${appConfig.apiBaseUrl}${afterAcq.preview_url}`} alt="After NDVI index preview" />
                          ) : (
                            <div className="preview-placeholder preview-placeholder--unavailable">
                              <span>UNAVAILABLE</span>
                              <small>Satellite NDVI observation unavailable</small>
                            </div>
                          )
                        )}
                      </div>
                      <span className="evidence-image-caption">
                        {evidenceImageryMode === 'rgb' ? 'Sentinel-2 Natural RGB (percentile-v2 · B04/B03/B02)' : 'Candidate NDVI Detection Preview (B04/B08)'}
                      </span>
                    </div>
                  </div>
                </div>

                {/* 3. Detected Change Evidence */}
                <div className="info-block" data-testid="scientific-metrics-section">
                  <p className="block-label">Detected Change Evidence</p>
                  <p className="detection-date-span" style={{ marginTop: 0, marginBottom: '0.5rem' }}>
                    Detected change represents the derived land-surface change signal (ΔNDVI), not a satellite observation.
                  </p>
                  <div className="candidate-metrics-grid">
                    <div className="candidate-metric-item">
                      <span className="metric-label">Mean |ΔNDVI|</span>
                      <span className="metric-value">{activeInterval.mean_change_signal !== undefined ? activeInterval.mean_change_signal.toFixed(4) : 'N/A'}</span>
                    </div>
                    <div className="candidate-metric-item">
                      <span className="metric-label">Max |ΔNDVI|</span>
                      <span className="metric-value">{activeInterval.max_change_signal !== undefined ? activeInterval.max_change_signal.toFixed(4) : 'N/A'}</span>
                    </div>
                    <div className="candidate-metric-item">
                      <span className="metric-label">Raw Changed Pixels</span>
                      <span className="metric-value">{activeInterval.raw_changed_pixel_count?.toLocaleString() ?? 'N/A'}</span>
                    </div>
                    <div className="candidate-metric-item">
                      <span className="metric-label">Filtered Changed Pixels</span>
                      <span className="metric-value">{activeInterval.filtered_changed_pixel_count?.toLocaleString() ?? activeInterval.changed_pixel_count?.toLocaleString() ?? 'N/A'}</span>
                    </div>
                    <div className="candidate-metric-item">
                      <span className="metric-label">Changed Pixel %</span>
                      <span className="metric-value">{activeInterval.changed_pixel_percentage !== undefined ? `${activeInterval.changed_pixel_percentage.toFixed(2)}%` : 'N/A'}</span>
                    </div>
                    <div className="candidate-metric-item">
                      <span className="metric-label">Total Changed Area</span>
                      <span className="metric-value">{activeInterval.total_changed_area_m2 ? `${activeInterval.total_changed_area_m2.toFixed(1)} m²` : 'N/A'}</span>
                    </div>
                    <div className="candidate-metric-item">
                      <span className="metric-label">Detection Threshold</span>
                      <span className="metric-value">{activeInterval.threshold ? activeInterval.threshold.toFixed(2) : '0.20'}</span>
                    </div>
                    <div className="candidate-metric-item">
                      <span className="metric-label">Min Region Size</span>
                      <span className="metric-value">{activeInterval.min_region_pixels ? `${activeInterval.min_region_pixels} px` : '4 px'}</span>
                    </div>
                    <div className="candidate-metric-item">
                      <span className="metric-label">Region Count</span>
                      <span className="metric-value">{activeInterval.region_count ?? 'N/A'}</span>
                    </div>
                    <div className="candidate-metric-item">
                      <span className="metric-label">Quality-Valid Pixels</span>
                      <span className="metric-value">{activeInterval.quality_valid_pixel_count?.toLocaleString() ?? 'N/A'}</span>
                    </div>
                    <div className="candidate-metric-item">
                      <span className="metric-label">Cloud Exclusions</span>
                      <span className="metric-value">{activeInterval.excluded_cloud_pixel_count?.toLocaleString() ?? 'N/A'}</span>
                    </div>
                    <div className="candidate-metric-item">
                      <span className="metric-label">Shadow Exclusions</span>
                      <span className="metric-value">{activeInterval.excluded_shadow_pixel_count?.toLocaleString() ?? 'N/A'}</span>
                    </div>
                  </div>
                </div>

                {/* 4. Geographic Context */}
                <div className="info-block" data-testid="geographic-context-section">
                  <p className="block-label">Geographic Context</p>
                  <div className="spatial-alignment-status" data-testid="evidence-spatial-alignment">
                    {evidence.is_spatially_aligned && evidence.spatial_alignment_details ? (
                      <span className="alignment-aligned">
                        ✓ Spatial Grid Aligned: {evidence.spatial_alignment_details.crs} · {evidence.spatial_alignment_details.width}x{evidence.spatial_alignment_details.height} · resolution {evidence.spatial_alignment_details.pixel_size}m
                      </span>
                    ) : (
                      <span className="alignment-limited">
                        ⚠ Spatial Grid Limitation: {evidence.spatial_alignment_details?.status ?? 'Grid check'} — {evidence.spatial_alignment_details?.details || 'Grid mismatch detected'}
                      </span>
                    )}
                  </div>
                  <div className="candidate-metrics-grid" style={{ marginTop: '0.4rem' }}>
                    <div className="candidate-metric-item">
                      <span className="metric-label">Active AOI</span>
                      <span className="metric-value">#{evidence.aoi_id} (Karnataka)</span>
                    </div>
                    <div className="candidate-metric-item">
                      <span className="metric-label">Spatial Reference</span>
                      <span className="metric-value">{evidence.spatial_alignment_details?.crs ?? 'EPSG:4326'}</span>
                    </div>
                    <div className="candidate-metric-item">
                      <span className="metric-label">Grid Resolution</span>
                      <span className="metric-value">{evidence.spatial_alignment_details?.pixel_size ? `${evidence.spatial_alignment_details.pixel_size}m` : '10m'}</span>
                    </div>
                  </div>
                </div>

                {/* 5. Observation Quality Support & Context Information */}
                <div className="info-block" data-testid="observation-quality-section">
                  <p className="block-label">Observation Quality Support</p>
                  <div className="quality-columns-grid">
                    <div className="quality-column">
                      <strong>Before Observation Quality (#{beforeAcq?.acquisition_id ?? activeInterval.before_acquisition_id})</strong>
                      <div className="quality-meta-list">
                        <span>State: <em data-testid="before-observation-state">{formatObservationQualityLabel(beforeAcq)}</em></span>
                        <span>Usable Fraction: <em>{beforeAcq && beforeAcq.usable_pixel_fraction != null ? `${(beforeAcq.usable_pixel_fraction * 100).toFixed(1)}%` : 'N/A'}</em></span>
                        <span>Cloud Fraction: <em>{beforeAcq && beforeAcq.cloud_fraction != null ? `${(beforeAcq.cloud_fraction * 100).toFixed(1)}%` : 'N/A'}</em></span>
                        <span>Shadow Fraction: <em>{beforeAcq && beforeAcq.shadow_fraction != null ? `${(beforeAcq.shadow_fraction * 100).toFixed(1)}%` : 'N/A'}</em></span>
                        <span>Masking: <em>{beforeAcq?.masking_method ?? 'scl-quality-mask-v1'}</em></span>
                        <span>Quality Mask: <em>{beforeAcq?.quality_mask_available ? 'Available' : 'Unavailable'}</em></span>
                      </div>
                    </div>
                    <div className="quality-column">
                      <strong>After Observation Quality (#{afterAcq?.acquisition_id ?? activeInterval.after_acquisition_id})</strong>
                      <div className="quality-meta-list">
                        <span>State: <em data-testid="after-observation-state">{formatObservationQualityLabel(afterAcq)}</em></span>
                        <span>Usable Fraction: <em>{afterAcq && afterAcq.usable_pixel_fraction != null ? `${(afterAcq.usable_pixel_fraction * 100).toFixed(1)}%` : 'N/A'}</em></span>
                        <span>Cloud Fraction: <em>{afterAcq && afterAcq.cloud_fraction != null ? `${(afterAcq.cloud_fraction * 100).toFixed(1)}%` : 'N/A'}</em></span>
                        <span>Shadow Fraction: <em>{afterAcq && afterAcq.shadow_fraction != null ? `${(afterAcq.shadow_fraction * 100).toFixed(1)}%` : 'N/A'}</em></span>
                        <span>Masking: <em>{afterAcq?.masking_method ?? 'scl-quality-mask-v1'}</em></span>
                        <span>Quality Mask: <em>{afterAcq?.quality_mask_available ? 'Available' : 'Unavailable'}</em></span>
                      </div>
                    </div>
                  </div>
                </div>

                {/* Temporal Evidence Support */}
                <div className="info-block" data-testid="temporal-evidence-section">
                  <p className="block-label">Temporal Evidence Support</p>
                  <div className="candidate-metrics-grid">
                    <div className="candidate-metric-item">
                      <span className="metric-label">Temporal State</span>
                      <span className="metric-value">{evidence.temporal.state}</span>
                    </div>
                    <div className="candidate-metric-item">
                      <span className="metric-label">Observations</span>
                      <span className="metric-value">{evidence.temporal.observation_count ?? evidence.acquisitions.length}</span>
                    </div>
                    <div className="candidate-metric-item">
                      <span className="metric-label">Adjacent Intervals</span>
                      <span className="metric-value">{evidence.temporal.interval_count}</span>
                    </div>
                    <div className="candidate-metric-item">
                      <span className="metric-label">Persistence Ratio</span>
                      <span className="metric-value">{evidence.temporal.persistence_ratio.toFixed(2)}</span>
                    </div>
                    <div className="candidate-metric-item">
                      <span className="metric-label">Temporal Consistency</span>
                      <span className="metric-value">{evidence.temporal.temporal_consistency !== undefined ? evidence.temporal.temporal_consistency.toFixed(2) : 'N/A'}</span>
                    </div>
                    <div className="candidate-metric-item">
                      <span className="metric-label">Matched Region Coverage</span>
                      <span className="metric-value">{evidence.temporal.matched_region_coverage !== undefined ? `${(evidence.temporal.matched_region_coverage * 100).toFixed(1)}%` : 'N/A'}</span>
                    </div>
                    <div className="candidate-metric-item">
                      <span className="metric-label">Onset Timestamp</span>
                      <span className="metric-value">{evidence.temporal.first_change_datetime ?? 'N/A'}</span>
                    </div>
                    <div className="candidate-metric-item">
                      <span className="metric-label">Quality-Aware Support</span>
                      <span className="metric-value">
                        {evidence.temporal.quality_aware_temporal_support != null
                          ? `${(evidence.temporal.quality_aware_temporal_support * 100).toFixed(1)}%`
                          : `${((evidence.candidate.metrics.quality_support ?? 0) * 100).toFixed(1)}%`}
                      </span>
                    </div>
                  </div>
                  {((evidence.temporal.quality_support !== undefined && evidence.temporal.quality_support < 0.40) ||
                    (evidence.candidate.metrics.quality_support !== undefined && evidence.candidate.metrics.quality_support < 0.40 && evidence.candidate.metrics.quality_support > 0)) && (
                    <div className="temporal-quality-warning" data-testid="temporal-quality-limitation">
                      ⚠ Temporal Quality Limitation: Quality Support {((evidence.temporal.quality_support ?? evidence.candidate.metrics.quality_support ?? 0)).toFixed(2)} &lt; 0.40 (dampens triage ranking; this is a temporal quality limitation, not an observation lifecycle state)
                    </div>
                  )}
                  <p className="temporal-interpretation-note">
                    Interpretation note: Temporal state describes temporal behavior over observation intervals. Severity is purely physical magnitude/extent. Evidence Score represents deterministic triage ranking, not calibrated probability. Insufficient history denotes limited temporal data, not negative change evidence.
                  </p>
                </div>

                {/* Candidate Assessment & Explainability */}
                <div className="info-block" data-testid="explainability-section">
                  <p className="block-label">Candidate Assessment &amp; Explainability</p>
                  <div className="explainability-group">
                    <strong>Physical Evidence</strong>
                    <p>
                      Spectral magnitude: mean |ΔNDVI| {activeInterval.mean_change_signal !== undefined ? activeInterval.mean_change_signal.toFixed(4) : 'N/A'} (max {activeInterval.max_change_signal !== undefined ? activeInterval.max_change_signal.toFixed(4) : 'N/A'}),
                      affecting {activeInterval.total_changed_area_m2 ? activeInterval.total_changed_area_m2.toFixed(1) : 'N/A'} m² with {activeInterval.filtered_changed_pixel_count?.toLocaleString() ?? 'N/A'} filtered changed pixels.
                    </p>
                  </div>
                  <div className="explainability-group">
                    <strong>Temporal Evidence</strong>
                    <p>
                      Temporal state: {evidence.temporal.state} with persistence ratio {evidence.temporal.persistence_ratio.toFixed(2)} across {evidence.temporal.interval_count} intervals.
                      Temporal support: {((evidence.temporal.quality_aware_temporal_support ?? evidence.candidate.metrics.quality_support ?? 0) * 100).toFixed(0)}%.
                    </p>
                  </div>
                  <div className="explainability-group">
                    <strong>Quality Evidence</strong>
                    <p>
                      Before usable fraction: {beforeAcq && beforeAcq.usable_pixel_fraction != null ? `${(beforeAcq.usable_pixel_fraction * 100).toFixed(1)}%` : 'N/A'},
                      After usable fraction: {afterAcq && afterAcq.usable_pixel_fraction != null ? `${(afterAcq.usable_pixel_fraction * 100).toFixed(1)}%` : 'N/A'}.
                      Exclusions: {activeInterval.excluded_cloud_pixel_count?.toLocaleString() ?? '0'} cloud pixels, {activeInterval.excluded_shadow_pixel_count?.toLocaleString() ?? '0'} shadow pixels.
                      {evidence.is_quality_limited ? ` Material limitations: ${evidence.quality_limitation_reasons?.join('; ')}` : ' No material quality limitations identified.'}
                    </p>
                  </div>
                  <div className="explainability-group">
                    <strong>Triage Assessment</strong>
                    <p>
                      Deterministic evidence score {evidence.candidate.score.toFixed(3)} represents deterministic triage ranking (not calibrated probability),
                      categorized as {evidence.candidate.priority} priority and {evidence.candidate.severity} physical severity.
                    </p>
                    {evidence.candidate.metrics.explanation?.summary && (
                      <p className="explainability-summary">{evidence.candidate.metrics.explanation.summary}</p>
                    )}
                    {evidence.candidate.metrics.explanation?.positive_factors && evidence.candidate.metrics.explanation.positive_factors.length > 0 && (
                      <div className="factor-list factor-list--positive">
                        <span className="factor-title">Supporting factors:</span>
                        <ul>
                          {evidence.candidate.metrics.explanation.positive_factors.map((factor, i) => (
                            <li key={i}>{factor}</li>
                          ))}
                        </ul>
                      </div>
                    )}
                    {evidence.candidate.metrics.explanation?.limiting_factors && evidence.candidate.metrics.explanation.limiting_factors.length > 0 && (
                      <div className="factor-list factor-list--limiting">
                        <span className="factor-title">Limiting factors:</span>
                        <ul>
                          {evidence.candidate.metrics.explanation.limiting_factors.map((factor, i) => (
                            <li key={i}>{factor}</li>
                          ))}
                        </ul>
                      </div>
                    )}
                  </div>
                </div>

                {/* 6. Collapsible Technical Provenance */}
                <details className="provenance-details info-block" data-testid="provenance-trace-section">
                  <summary className="provenance-summary">
                    <span className="block-label" style={{ margin: 0 }}>Technical Provenance &amp; System Trace</span>
                    <small className="provenance-toggle-hint">(click to expand)</small>
                  </summary>
                  <div className="provenance-trace-tree" style={{ marginTop: '0.75rem' }}>
                    <div className="trace-row">
                      <span className="trace-key">Candidate ID</span>
                      <span className="trace-val" data-testid="evidence-candidate-id">{evidence.candidate.candidate_id}</span>
                    </div>
                    <div className="trace-row">
                      <span className="trace-key">Analysis ID</span>
                      <span className="trace-val">#{evidence.temporal.analysis_id}</span>
                    </div>
                    <div className="trace-row">
                      <span className="trace-key">Signal ID</span>
                      <span className="trace-val">#{evidence.candidate.signal_id}</span>
                    </div>
                    <div className="trace-row">
                      <span className="trace-key">AOI ID</span>
                      <span className="trace-val">#{evidence.aoi_id} (Karnataka-contained)</span>
                    </div>
                    <div className="trace-row">
                      <span className="trace-key">Detection Run</span>
                      <span className="trace-val">Run #{activeInterval.detection_run_id}</span>
                    </div>
                    <div className="trace-row">
                      <span className="trace-key">Before Observation</span>
                      <span className="trace-val">
                        Acquisition #{activeInterval.before_acquisition_id} · STAC {beforeAcq?.item_id ?? 'N/A'}
                      </span>
                    </div>
                    <div className="trace-row">
                      <span className="trace-key">After Observation</span>
                      <span className="trace-val">
                        Acquisition #{activeInterval.after_acquisition_id} · STAC {afterAcq?.item_id ?? 'N/A'}
                      </span>
                    </div>
                    <div className="trace-row">
                      <span className="trace-key">Pipeline Versions</span>
                      <span className="trace-val">
                        Detector: {activeInterval.detector_version} ·
                        Processing: {beforeAcq?.quality_processing_version ?? 'sentinel-2-l2a-v1'} ·
                        Masking: {beforeAcq?.masking_method ?? 'scl-quality-mask-v1'} ·
                        Visualization: {beforeAcq?.visualization_version ?? 'sentinel-2-rgb-percentile-v2'}
                      </span>
                    </div>
                  </div>
                </details>

              </div>
            )}
          </>
        )
      }
      case 'REVIEW': {
        const candidate = candidates.find((c) => c.candidate_id === selectedCandidateId)
        const intervals = evidence?.intervals ?? []
        const safeIntervalIndex = selectedIntervalIndex >= 0 && selectedIntervalIndex < intervals.length ? selectedIntervalIndex : 0
        const activeInterval = intervals[safeIntervalIndex] ?? null
        const beforeAcq = activeInterval && evidence?.acquisitions
          ? evidence.acquisitions.find((entry) => entry.acquisition_id === activeInterval.before_acquisition_id)
          : null
        const afterAcq = activeInterval && evidence?.acquisitions
          ? evidence.acquisitions.find((entry) => entry.acquisition_id === activeInterval.after_acquisition_id)
          : null

        return (
          <>
            <div className="panel-heading">
              <div>
                <p className="panel-kicker">07 / Review</p>
                <h2>{activeStageTitle}</h2>
              </div>
              <span className="panel-index">REVIEW</span>
            </div>
            <p className="panel-copy">Record analyst review decision for the selected candidate.</p>

            <p className="dependency-note">{dependencyNotes.REVIEW}</p>

            {!selectedCandidateId ? (
              <div className="info-block empty-workstation-block" data-testid="no-candidate-review-state">
                <p className="block-label">Analyst Action Required</p>
                <p className="empty-message">No candidate is currently selected for review. Select a candidate in Stage 05 / CANDIDATES.</p>
                <button type="button" className="button--secondary" onClick={() => setStageOverride('CANDIDATES')}>
                  Select candidate in Stage 05 / CANDIDATES
                </button>
              </div>
            ) : (
              <div className="review-workstation-content" data-testid="analyst-review-section">
                <span data-testid="evidence-workstation-state" style={{ display: 'none' }}>{evidenceWorkstationState}</span>
                {/* 1. Selected Candidate Context */}
                {candidate && (
                  <div className="info-block candidate-identity-card" data-testid="review-candidate-context">
                    <div className="candidate-detail-header">
                      <h4>Candidate #{candidate.rank} — {candidate.priority.charAt(0).toUpperCase() + candidate.priority.slice(1)} priority</h4>
                    </div>
                    <div className="candidate-metrics-grid">
                      <div className="candidate-metric-item">
                        <span className="metric-label">Triage Priority</span>
                        <span className="metric-value" data-testid="review-priority">{candidate.priority}</span>
                      </div>
                      <div className="candidate-metric-item">
                        <span className="metric-label">Physical Severity</span>
                        <span className="metric-value" data-testid="review-severity">{candidate.severity}</span>
                      </div>
                      <div className="candidate-metric-item">
                        <span className="metric-label">Evidence Score</span>
                        <span className="metric-value" data-testid="review-score">{candidate.score.toFixed(3)}</span>
                      </div>
                      <div className="candidate-metric-item">
                        <span className="metric-label">Temporal State</span>
                        <span className="metric-value" data-testid="review-temporal-state">{candidate.metrics?.temporal_state ?? 'N/A'}</span>
                      </div>
                      <div className="candidate-metric-item">
                        <span className="metric-label">Affected Area</span>
                        <span className="metric-value">
                          {candidate.metrics?.total_area_m2 ? `${candidate.metrics.total_area_m2.toFixed(1)} m²` : (activeInterval?.total_changed_area_m2 ? `${activeInterval.total_changed_area_m2.toFixed(1)} m²` : 'N/A')}
                        </span>
                      </div>
                      <div className="candidate-metric-item">
                        <span className="metric-label">Onset Date</span>
                        <span className="metric-value">
                          {candidate.metrics?.first_change_datetime ? new Date(candidate.metrics.first_change_datetime).toLocaleDateString() : 'N/A'}
                        </span>
                      </div>
                    </div>

                    <details className="provenance-block">
                      <summary>Technical provenance</summary>
                      <div className="provenance-details-grid">
                        <div><span>Candidate ID:</span> <code data-testid="review-candidate-id">{candidate.candidate_id}</code></div>
                        <div><span>Analysis ID:</span> <code>{candidate.analysis_id}</code></div>
                        <div><span>Signal ID:</span> <code>{candidate.signal_id}</code></div>
                        <div><span>AOI ID:</span> <code>{activeAoiId ?? '1'}</code></div>
                        {candidate.detection_run_ids && candidate.detection_run_ids.length > 0 && (
                          <div><span>Detection Runs:</span> <code>{candidate.detection_run_ids.join(', ')}</code></div>
                        )}
                        {candidate.acquisition_ids && candidate.acquisition_ids.length > 0 && (
                          <div><span>Observations:</span> <code>{candidate.acquisition_ids.join(', ')}</code></div>
                        )}
                      </div>
                    </details>
                  </div>
                )}

                {/* 2. Review Status & Advisories */}
                <div className="info-block review-status-card" data-testid="review-status-card">
                  <div className="candidate-detail-header">
                    <h4>Analyst Review &amp; Decision</h4>
                    <span
                      className={`review-decision-badge review-decision-badge--${candidateReview?.decision ?? 'unreviewed'}`}
                      data-testid="current-review-decision"
                    >
                      {(candidateReview?.decision ?? 'unreviewed').toUpperCase()}
                    </span>
                  </div>

                  <div className="review-meta-row">
                    {candidateReviewLoading && (
                      <span className="review-meta-item review-meta-item--unreviewed">Loading review state...</span>
                    )}
                    {candidateReview?.created_at ? (
                      <span className="review-meta-item" data-testid="review-created-at">
                        <strong>First Reviewed:</strong> {new Date(candidateReview.created_at).toLocaleString()}
                      </span>
                    ) : !candidateReviewLoading ? (
                      <span className="review-meta-item review-meta-item--unreviewed" data-testid="review-unreviewed-label">
                        Candidate is unreviewed
                      </span>
                    ) : null}
                    {candidateReview?.updated_at && (
                      <span className="review-meta-item" data-testid="review-updated-at">
                        <strong>Last Updated:</strong> {new Date(candidateReview.updated_at).toLocaleString()}
                      </span>
                    )}
                  </div>

                  {candidates.length > 1 && (
                    <div className="candidate-queue-nav" aria-label="Candidate queue navigation" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: '0.75rem', marginBottom: '0.75rem' }}>
                      <button
                        type="button"
                        className="button--quiet button--mini"
                        onClick={handlePreviousCandidate}
                        disabled={currentCandidateIndex <= 0}
                      >
                        ← Previous Candidate
                      </button>
                      <span className="candidate-queue-index" style={{ fontSize: '0.8rem' }}>
                        Candidate {currentCandidateIndex >= 0 ? currentCandidateIndex + 1 : 0} of {candidates.length}
                      </span>
                      <button
                        type="button"
                        className="button--quiet button--mini"
                        onClick={handleNextCandidate}
                        disabled={currentCandidateIndex < 0 || currentCandidateIndex >= candidates.length - 1}
                      >
                        Next Candidate →
                      </button>
                    </div>
                  )}

                  {evidenceWorkstationState === 'QUALITY_LIMITED' && (
                    <div className="review-advisory review-advisory--warning" role="alert" data-testid="review-quality-advisory">
                      ⚠ Advisory: Candidate evidence has material observation quality limitations. Review decisions may be recorded, but physical change evidence reliability is reduced.
                    </div>
                  )}

                  {(evidenceWorkstationState === 'UNAVAILABLE' || evidenceWorkstationState === 'FAILED') && (
                    <div className="review-advisory review-advisory--caution" role="alert" data-testid="review-unavailable-advisory">
                      ⚠ Advisory: Evidence artifacts or provenance validation is unavailable or failed for this candidate. Record review decision with caution.
                    </div>
                  )}

                  {/* 3. Decision Controls */}
                  <div className="review-decision-controls" role="radiogroup" aria-label="Review decision">
                    <label className={`decision-radio-label ${draftDecision === 'accepted' ? 'decision-radio-label--active' : ''}`}>
                      <input
                        type="radio"
                        name="candidate-review-decision"
                        value="accepted"
                        checked={draftDecision === 'accepted'}
                        onChange={() => setDraftDecision('accepted')}
                        data-testid="decision-accepted-radio"
                      />
                      <span>Accept</span>
                    </label>
                    <label className={`decision-radio-label ${draftDecision === 'rejected' ? 'decision-radio-label--active' : ''}`}>
                      <input
                        type="radio"
                        name="candidate-review-decision"
                        value="rejected"
                        checked={draftDecision === 'rejected'}
                        onChange={() => setDraftDecision('rejected')}
                        data-testid="decision-rejected-radio"
                      />
                      <span>Reject</span>
                    </label>
                    <label className={`decision-radio-label ${draftDecision === 'investigate' ? 'decision-radio-label--active' : ''}`}>
                      <input
                        type="radio"
                        name="candidate-review-decision"
                        value="investigate"
                        checked={draftDecision === 'investigate'}
                        onChange={() => setDraftDecision('investigate')}
                        data-testid="decision-investigate-radio"
                      />
                      <span>Investigate</span>
                    </label>
                  </div>

                  <div className="review-note-container">
                    <div className="review-note-header">
                      <label htmlFor="analyst-review-note">Analyst Note (Optional)</label>
                      <span className="char-counter" data-testid="review-char-counter">
                        {draftNote.length} / 2000
                      </span>
                    </div>
                    <textarea
                      id="analyst-review-note"
                      aria-label="Analyst review note"
                      maxLength={2000}
                      rows={3}
                      value={draftNote}
                      onChange={(e) => setDraftNote(e.target.value)}
                      placeholder="Record analyst justification, external context, or field verification notes (max 2000 characters)..."
                      data-testid="review-note-input"
                    />
                  </div>

                  <div className="review-action-row">
                    <button
                      type="button"
                      className="button--primary"
                      disabled={reviewSaveStatus === 'saving' || !draftDecision}
                      onClick={() => void handleSaveReview()}
                      data-testid="save-review-btn"
                    >
                      {reviewSaveStatus === 'saving' ? 'Saving Decision...' : 'Save Review Decision'}
                    </button>
                    {reviewSaveStatus === 'saving' && (
                      <span className="review-status-indicator" role="status" data-testid="review-saving-indicator">
                        Saving...
                      </span>
                    )}
                    {reviewSaveStatus === 'success' && (
                      <span className="review-status-indicator review-status-indicator--success" role="status" data-testid="review-save-success">
                        ✓ Review decision saved
                      </span>
                    )}
                    {reviewSaveStatus === 'error' && (
                      <span className="review-status-indicator review-status-indicator--error" role="alert" data-testid="review-save-error">
                        {reviewSaveError || 'Failed to save review'}
                      </span>
                    )}
                  </div>
                </div>

                {/* 4. Concise Supporting Evidence Context */}
                {activeInterval && (
                  <div className="info-block review-evidence-summary" data-testid="review-evidence-summary">
                    <div className="candidate-detail-header">
                      <h4>Supporting Evidence Context</h4>
                    </div>
                    <p className="scientific-distinction-note">
                      Detected change represents the derived land-surface change signal (ΔNDVI), not a satellite observation.
                    </p>
                    <div className="candidate-metrics-grid">
                      <div className="candidate-metric-item">
                        <span className="metric-label">Observation Dates</span>
                        <span className="metric-value">
                          {beforeAcq?.acquisition_datetime ? new Date(beforeAcq.acquisition_datetime).toLocaleDateString() : 'N/A'} → {afterAcq?.acquisition_datetime ? new Date(afterAcq.acquisition_datetime).toLocaleDateString() : 'N/A'}
                        </span>
                      </div>
                      <div className="candidate-metric-item">
                        <span className="metric-label">Mean |ΔNDVI|</span>
                        <span className="metric-value">{activeInterval.mean_change_signal !== undefined ? activeInterval.mean_change_signal.toFixed(4) : 'N/A'}</span>
                      </div>
                      <div className="candidate-metric-item">
                        <span className="metric-label">Max |ΔNDVI|</span>
                        <span className="metric-value">{activeInterval.max_change_signal !== undefined ? activeInterval.max_change_signal.toFixed(4) : 'N/A'}</span>
                      </div>
                      <div className="candidate-metric-item">
                        <span className="metric-label">Changed Area</span>
                        <span className="metric-value">{activeInterval.total_changed_area_m2 !== undefined ? `${activeInterval.total_changed_area_m2.toFixed(1)} m²` : 'N/A'}</span>
                      </div>
                      <div className="candidate-metric-item">
                        <span className="metric-label">Filtered Changed Pixels</span>
                        <span className="metric-value">{activeInterval.filtered_changed_pixel_count?.toLocaleString() ?? 'N/A'}</span>
                      </div>
                    </div>
                  </div>
                )}
              </div>
            )}
          </>
        )
      }
      case 'EXPORT': {
        const candidate = candidates.find((c) => c.candidate_id === selectedCandidateId)
        const dateRangeStr = startDatetime && endDatetime
          ? `${new Date(startDatetime).toLocaleDateString()} – ${new Date(endDatetime).toLocaleDateString()}`
          : 'Configured observation period'

        return (
          <>
            <div className="panel-heading">
              <div>
                <p className="panel-kicker">08 / Export</p>
                <h2>Analysis complete</h2>
              </div>
              <span className="panel-index">EXPORT</span>
            </div>
            <p className="panel-copy">
              Your change-detection results are ready to export.
            </p>

            <div className="export-summary-card" data-testid="investigation-export-section">
              <div className="export-context-grid">
                <div className="export-context-item">
                  <span className="context-label">Selected Area</span>
                  <strong className="context-value">#{activeAoiId ?? '1'} (Karnataka)</strong>
                </div>
                <div className="export-context-item">
                  <span className="context-label">Observation Period</span>
                  <strong className="context-value">{dateRangeStr}</strong>
                </div>
                <div className="export-context-item">
                  <span className="context-label">Selected Candidate</span>
                  <strong className="context-value">{candidate ? `#${candidate.rank} (${candidate.priority} priority)` : 'N/A'}</strong>
                </div>
                <div className="export-context-item">
                  <span className="context-label">Review Decision</span>
                  <strong className="context-value" data-testid="export-review-decision">
                    {candidateReview?.decision ? (candidateReview.decision.charAt(0).toUpperCase() + candidateReview.decision.slice(1)) : 'Unreviewed'}
                  </strong>
                </div>
              </div>

              {candidateReview?.note && (
                <div className="export-note-block" style={{ marginTop: '0.6rem', background: '#fafcfb', border: '1px solid #dce5e1', padding: '0.5rem', borderRadius: '4px' }}>
                  <span className="context-label" style={{ fontSize: '0.72rem', fontWeight: 700, color: '#687973', textTransform: 'uppercase', letterSpacing: '0.04em', display: 'block' }}>Analyst Note</span>
                  <p className="export-analyst-note" style={{ margin: '0.2rem 0 0 0', fontSize: '0.82rem', color: '#1b3028' }}>{candidateReview.note}</p>
                </div>
              )}

              <div className="export-formats">
                <div className="export-format-card">
                  <h4>PDF Report</h4>
                  <p>
                    Human-readable report containing the selected change result, evidence, review state, and provenance.
                  </p>
                  <button
                    type="button"
                    className="button--primary"
                    disabled={exportStatus === 'exporting' || !selectedCandidateId}
                    onClick={() => void handleExportInvestigation('pdf')}
                    data-testid="download-pdf-report-btn"
                  >
                    {exportStatus === 'exporting' && exportFormat === 'pdf' ? 'Generating PDF...' : 'Download PDF Report'}
                  </button>
                </div>

                <div className="export-format-card">
                  <h4>JSON Data Package</h4>
                  <p>
                    Machine-readable export containing the structured analysis data and provenance.
                  </p>
                  <button
                    type="button"
                    className="button--secondary"
                    disabled={exportStatus === 'exporting' || !selectedCandidateId}
                    onClick={() => void handleExportInvestigation('json')}
                    data-testid="download-json-data-btn"
                  >
                    {exportStatus === 'exporting' && exportFormat === 'json' ? 'Generating JSON...' : 'Download JSON Data'}
                  </button>
                </div>
              </div>

              <div style={{ marginTop: '0.75rem' }}>
                {exportStatus === 'exporting' && (
                  <span className="export-status-text" role="status" data-testid="export-loading-indicator">
                    Generating {exportFormat.toUpperCase()} snapshot...
                  </span>
                )}
                {exportStatus === 'success' && (
                  <span className="export-status-text export-status-text--success" role="status" data-testid="export-success-message">
                    ✓ Downloaded {lastExportedFilename ?? `${exportFormat.toUpperCase()} report`}
                  </span>
                )}
                {exportStatus === 'error' && (
                  <span className="export-status-text export-status-text--error" role="alert" data-testid="export-error-message">
                    {exportError || 'Export failed'}
                  </span>
                )}
              </div>

              <details className="provenance-block" style={{ marginTop: '1rem' }}>
                <summary>Export provenance &amp; technical metadata</summary>
                <div className="provenance-details-grid">
                  <div><span>Candidate ID:</span> <code>{selectedCandidateId}</code></div>
                  <div><span>Analysis ID:</span> <code>{candidate?.analysis_id ?? 'N/A'}</code></div>
                  <div><span>Signal ID:</span> <code>{candidate?.signal_id ?? 'N/A'}</code></div>
                  <div><span>Review Timestamp:</span> <code>{candidateReview?.updated_at ?? candidateReview?.created_at ?? 'N/A'}</code></div>
                </div>
              </details>
            </div>
          </>
        )
      }
      default:
        return null
    }
  }

  return (
    <main className="workstation">
      <header className="workstation__header">
        <div className="brand-lockup">
          <p className="eyebrow">TerraWatch V2</p>
          <h1>Change Detection for Karnataka</h1>
        </div>
        <div className="header-context">
          <div className="header-context__block">
            <span className="header-context__label">Karnataka analysis</span>
            <span
              className="header-context__aoi"
              data-testid="aoi-state"
              data-state={aoiState}
            >
              {activeAoiId ? `Area active · #${activeAoiId}` : 'No area selected'}
            </span>
          </div>
        </div>
      </header>

      <WorkflowProgressIndicator steps={workflowStages} currentStage={currentStage} />

      <section className="workstation__workspace" aria-label="AOI workstation">
        <MapCanvas
          geometry={geometry}
          draftGeometry={draftGeometry}
          editing={isEditing}
          onDraftChange={handleDraftChange}
          draftPositions={draftPositions}
          drawing={isDrawing}
          changeRegions={
            candidates.length
              ? candidates.map((candidate) => ({
                  region_id: candidate.signal_id,
                  geometry: candidate.geometry,
                  selected: candidate.candidate_id === selectedCandidateId,
                  candidate_id: candidate.candidate_id,
                }))
              : temporal?.signals
                ? temporal.signals.map((signal) => ({ region_id: signal.signal_id, geometry: signal.geometry }))
                : detection?.regions
          }
          onSelectCandidate={selectCandidate}
          onMapClick={(position) => setDraftPositions((current) => {
            if (current.length >= 1) return [current[0], position]
            return [position]
          })}
          satellite={activeSatelliteUrl && activeSatelliteBounds && activeSatelliteBounds.length === 4 && activeSatelliteAcquisition ? { url: activeSatelliteUrl, bounds: activeSatelliteBounds, role: imageryLayerRole, itemId: activeSatelliteAcquisition.item_id, date: activeSatelliteAcquisition.acquisition_datetime } : null}
        />
        <div className="map-overlay" aria-label="Active map layers">
          <strong>{activeSatelliteAcquisition && activeSatelliteUrl ? `Sentinel-2 · ${imageryLayerRole}` : 'Basemap context'}</strong>
          {activeSatelliteAcquisition && <span>{activeSatelliteAcquisition.item_id} · {new Date(activeSatelliteAcquisition.acquisition_datetime).toLocaleDateString()}</span>}
          <div className="map-legend">
            <span><i className="legend-swatch legend-swatch--satellite" />Satellite imagery</span>
            <span><i className="legend-swatch legend-swatch--aoi" />Active AOI</span>
            <span><i className="legend-swatch legend-swatch--change" />Detected change</span>
            <span><i className="legend-swatch legend-swatch--selected" />Selected candidate</span>
            <span><i className="legend-swatch legend-swatch--boundary" />Karnataka boundary</span>
          </div>
        </div>

        <aside className="workstation__panel">

          {stageOverride !== null && STAGE_ORDER.indexOf(stageOverride) < STAGE_ORDER.indexOf(derivedCurrentStage) && (
            <div className="upstream-inspection-banner" role="status">
              <span>Inspecting stage: <strong>{STAGE_TITLES[stageOverride]}</strong> (read-only)</span>
              <button
                type="button"
                className="button--secondary button--mini"
                onClick={() => setStageOverride(null)}
              >
                Return to {STAGE_TITLES[derivedCurrentStage]}
              </button>
            </div>
          )}

          {error && <p className="error" role="alert">{error}</p>}
          {renderCurrentStage()}
          <WorkflowNavigation
            currentStage={currentStage}
            canPrevious={navigationConfig.canPrevious}
            canNext={navigationConfig.canNext}
            onPrevious={navigationConfig.onPrevious}
            onNext={navigationConfig.onNext}
            previousTestId={navigationConfig.previousTestId}
            nextTestId={navigationConfig.nextTestId}
            nextRequirementHint={navigationConfig.nextRequirementHint}
            nextAriaLabel={navigationConfig.nextAriaLabel}
          />
        </aside>
      </section>
    </main>
  )
}

export default App
