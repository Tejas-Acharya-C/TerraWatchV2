import type { WorkflowStage } from './workflow'

type WorkflowNavigationProps = {
  currentStage: WorkflowStage
  onPrevious: () => void
  onNext: () => void
  canPrevious: boolean
  canNext: boolean
  previousTestId?: string
  nextTestId?: string
  nextRequirementHint?: string
  nextAriaLabel?: string
}

export default function WorkflowNavigation({
  currentStage,
  onPrevious,
  onNext,
  canPrevious,
  canNext,
  previousTestId,
  nextTestId,
  nextRequirementHint,
  nextAriaLabel,
}: WorkflowNavigationProps) {
  return (
    <footer className="workflow-navigation-footer" aria-label="Workflow stage navigation">
      {currentStage !== 'AOI' ? (
        <button
          type="button"
          className="workflow-nav-button workflow-nav-button--prev"
          data-testid={previousTestId}
          data-direction="previous"
          disabled={!canPrevious}
          onClick={onPrevious}
        >
          ← Previous
        </button>
      ) : (
        <div className="workflow-nav-spacer" aria-hidden="true" />
      )}

      <div className="workflow-nav-center">
        {!canNext && nextRequirementHint ? (
          <span className="workflow-nav-hint" role="status">
            {nextRequirementHint}
          </span>
        ) : null}
      </div>

      {currentStage !== 'EXPORT' ? (
        <button
          type="button"
          className="workflow-nav-button workflow-nav-button--next"
          data-testid={nextTestId}
          data-direction="next"
          aria-label={nextAriaLabel}
          disabled={!canNext}
          onClick={onNext}
        >
          Next →
        </button>
      ) : null}
    </footer>
  )
}
