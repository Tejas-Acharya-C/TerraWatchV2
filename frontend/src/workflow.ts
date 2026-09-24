export type WorkflowStage = 'AOI' | 'IMAGERY' | 'CHANGE' | 'TEMPORAL' | 'CANDIDATES' | 'EVIDENCE' | 'REVIEW'
export type StageStatus = 'locked' | 'ready' | 'running' | 'complete' | 'failed'

export const STAGE_ORDER: WorkflowStage[] = ['AOI', 'IMAGERY', 'CHANGE', 'TEMPORAL', 'CANDIDATES', 'EVIDENCE', 'REVIEW']

export const STAGE_TITLES: Record<WorkflowStage, string> = {
  AOI: 'AREA',
  IMAGERY: 'OBSERVATIONS',
  CHANGE: 'CHANGES',
  TEMPORAL: 'CHANGE HISTORY',
  CANDIDATES: 'CANDIDATES',
  EVIDENCE: 'EVIDENCE',
  REVIEW: 'REVIEW',
}