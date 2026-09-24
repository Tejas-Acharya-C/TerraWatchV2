export type WorkflowStage = 'AOI' | 'IMAGERY' | 'CHANGE' | 'TEMPORAL' | 'CANDIDATES' | 'EVIDENCE' | 'REVIEW' | 'EXPORT'
export type StageStatus = 'locked' | 'ready' | 'running' | 'complete' | 'failed'

export const STAGE_ORDER: WorkflowStage[] = ['AOI', 'IMAGERY', 'CHANGE', 'TEMPORAL', 'CANDIDATES', 'EVIDENCE', 'REVIEW', 'EXPORT']

export const STAGE_TITLES: Record<WorkflowStage, string> = {
  AOI: 'AREA',
  IMAGERY: 'OBSERVATIONS',
  CHANGE: 'CHANGES',
  TEMPORAL: 'CHANGE HISTORY',
  CANDIDATES: 'CANDIDATES',
  EVIDENCE: 'EVIDENCE',
  REVIEW: 'REVIEW',
  EXPORT: 'EXPORT',
}