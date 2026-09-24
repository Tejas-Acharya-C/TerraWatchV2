import type { WorkflowStage, StageStatus } from './workflow'

type WorkflowStep = {
  stage: WorkflowStage
  label: string
  status: StageStatus
}

type WorkflowProgressIndicatorProps = {
  steps: WorkflowStep[]
  currentStage: WorkflowStage
}

const markers: Record<StageStatus, string> = {
  complete: '✓',
  failed: '⚠',
  running: '◌',
  ready: '●',
  locked: '○',
}

export default function WorkflowProgressIndicator({ steps, currentStage }: WorkflowProgressIndicatorProps) {
  return (
    <div className="workflow-rail" aria-label="Analysis workflow status" role="group">
      {steps.map(({ stage, label, status }, index) => {
        const isCurrent = currentStage === stage
        const stepNumber = String(index + 1).padStart(2, '0')

        return (
          <div
            key={stage}
            className={`workflow-step workflow-step--${status} ${isCurrent ? 'workflow-step--current' : ''}`}
            aria-current={isCurrent ? 'step' : undefined}
            aria-label={`${stepNumber} ${label}`}
          >
            <span className="workflow-marker" aria-hidden="true">{markers[status]}</span>
            <div className="workflow-step__label">
              <strong>{stepNumber}</strong>
              <span>{label}</span>
            </div>
          </div>
        )
      })}
    </div>
  )
}
