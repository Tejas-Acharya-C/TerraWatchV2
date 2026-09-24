import type { CandidateReview, ReviewDecision } from './lib/api'

export type AOIInteractionState =
  | 'NO_AOI'
  | 'AOI_SELECTED'
  | 'EDITING'
  | 'EDIT_DIRTY'
  | 'SAVING'
  | 'SAVE_FAILED'
  | 'SAVE_SUCCESS'

export const aoiStateLabels: Record<AOIInteractionState, string> = {
  NO_AOI: 'No area selected',
  AOI_SELECTED: 'Area defined',
  EDITING: 'Editing area',
  EDIT_DIRTY: 'Draft area',
  SAVING: 'Saving',
  SAVE_FAILED: 'Save failed',
  SAVE_SUCCESS: 'Area saved',
}

export function hasUnsavedReviewChanges(
  draftDecision: ReviewDecision | null,
  draftNote: string,
  candidateReview: CandidateReview | null
): boolean {
  if (!candidateReview) {
    return draftDecision !== null || draftNote.trim() !== ''
  }
  const savedDecision: ReviewDecision | null =
    candidateReview.decision === 'unreviewed' ? null : (candidateReview.decision as ReviewDecision)
  const savedNote = candidateReview.note ?? ''
  return draftDecision !== savedDecision || draftNote !== savedNote
}
