import { describe, it, expect } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import WorkflowProgressIndicator from './WorkflowProgressIndicator'
import type { WorkflowStage, StageStatus } from './workflow'

type WorkflowStep = {
  stage: WorkflowStage
  label: string
  status: StageStatus
}

describe('WorkflowProgressIndicator (Passive Workflow Rail - Phase 19D)', () => {
  const defaultSteps: WorkflowStep[] = [
    { stage: 'AOI', label: 'AREA', status: 'complete' },
    { stage: 'IMAGERY', label: 'OBSERVATIONS', status: 'complete' },
    { stage: 'CHANGE', label: 'CHANGES', status: 'ready' },
    { stage: 'TEMPORAL', label: 'CHANGE HISTORY', status: 'locked' },
    { stage: 'CANDIDATES', label: 'CANDIDATES', status: 'locked' },
    { stage: 'EVIDENCE', label: 'EVIDENCE', status: 'locked' },
    { stage: 'REVIEW', label: 'REVIEW', status: 'locked' },
    { stage: 'EXPORT', label: 'EXPORT', status: 'locked' },
  ]

  it('1. Renders all eight workflow steps in order with step numbers', () => {
    render(<WorkflowProgressIndicator steps={defaultSteps} currentStage="CHANGE" />)

    const rail = screen.getByRole('group', { name: 'Analysis workflow status' })
    expect(rail).toBeInTheDocument()

    expect(screen.getByLabelText('01 AREA')).toBeInTheDocument()
    expect(screen.getByLabelText('02 OBSERVATIONS')).toBeInTheDocument()
    expect(screen.getByLabelText('03 CHANGES')).toBeInTheDocument()
    expect(screen.getByLabelText('04 CHANGE HISTORY')).toBeInTheDocument()
    expect(screen.getByLabelText('05 CANDIDATES')).toBeInTheDocument()
    expect(screen.getByLabelText('06 EVIDENCE')).toBeInTheDocument()
    expect(screen.getByLabelText('07 REVIEW')).toBeInTheDocument()
    expect(screen.getByLabelText('08 EXPORT')).toBeInTheDocument()
  })

  it('2. Correctly indicates the active stage with aria-current="step" and current modifier class', () => {
    render(<WorkflowProgressIndicator steps={defaultSteps} currentStage="CHANGE" />)

    const currentStep = screen.getByLabelText('03 CHANGES')
    expect(currentStep).toHaveAttribute('aria-current', 'step')
    expect(currentStep).toHaveClass('workflow-step--current')

    const otherStep = screen.getByLabelText('01 AREA')
    expect(otherStep).not.toHaveAttribute('aria-current')
    expect(otherStep).not.toHaveClass('workflow-step--current')
  })

  it('3. Renders status markers accurately for each status', () => {
    const variedSteps: WorkflowStep[] = [
      { stage: 'AOI', label: 'AREA', status: 'complete' },
      { stage: 'IMAGERY', label: 'OBSERVATIONS', status: 'running' },
      { stage: 'CHANGE', label: 'CHANGES', status: 'ready' },
      { stage: 'TEMPORAL', label: 'CHANGE HISTORY', status: 'failed' },
      { stage: 'CANDIDATES', label: 'CANDIDATES', status: 'locked' },
      { stage: 'EVIDENCE', label: 'EVIDENCE', status: 'locked' },
      { stage: 'REVIEW', label: 'REVIEW', status: 'locked' },
      { stage: 'EXPORT', label: 'EXPORT', status: 'locked' },
    ]

    render(<WorkflowProgressIndicator steps={variedSteps} currentStage="IMAGERY" />)

    expect(screen.getByLabelText('01 AREA')).toHaveTextContent('✓')
    expect(screen.getByLabelText('02 OBSERVATIONS')).toHaveTextContent('◌')
    expect(screen.getByLabelText('03 CHANGES')).toHaveTextContent('●')
    expect(screen.getByLabelText('04 CHANGE HISTORY')).toHaveTextContent('⚠')
    expect(screen.getByLabelText('05 CANDIDATES')).toHaveTextContent('○')
  })

  it('4. Has NO role="button" on any workflow rail steps', () => {
    render(<WorkflowProgressIndicator steps={defaultSteps} currentStage="CHANGE" />)

    // None of the steps should have button semantics
    expect(screen.queryByRole('button', { name: /01 AREA/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /02 OBSERVATIONS/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /03 CHANGES/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /04 CHANGE HISTORY/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /05 CANDIDATES/i })).not.toBeInTheDocument()
  })

  it('5. Has NO tabIndex on any workflow rail steps (not focusable)', () => {
    render(<WorkflowProgressIndicator steps={defaultSteps} currentStage="CHANGE" />)

    defaultSteps.forEach((step, idx) => {
      const stepNumber = String(idx + 1).padStart(2, '0')
      const el = screen.getByLabelText(`${stepNumber} ${step.label}`)
      expect(el).not.toHaveAttribute('tabIndex')
      expect(el).not.toHaveAttribute('tabindex')
    })
  })

  it('6. Does not apply workflow-step--interactive class to any step', () => {
    render(<WorkflowProgressIndicator steps={defaultSteps} currentStage="CHANGE" />)

    defaultSteps.forEach((step, idx) => {
      const stepNumber = String(idx + 1).padStart(2, '0')
      const el = screen.getByLabelText(`${stepNumber} ${step.label}`)
      expect(el).not.toHaveClass('workflow-step--interactive')
    })
  })

  it('7. Clicking a step does not throw and has no interactive side-effects', () => {
    render(<WorkflowProgressIndicator steps={defaultSteps} currentStage="CHANGE" />)

    const aoiStep = screen.getByLabelText('01 AREA')
    // Clicking should be a harmless no-op on non-interactive div
    expect(() => fireEvent.click(aoiStep)).not.toThrow()
    // Still CHANGE is current
    expect(aoiStep).not.toHaveAttribute('aria-current')
  })

  it('8. Keyboard interactions (Enter/Space) on steps have no effect and do not throw', () => {
    render(<WorkflowProgressIndicator steps={defaultSteps} currentStage="CHANGE" />)

    const aoiStep = screen.getByLabelText('01 AREA')
    expect(() => fireEvent.keyDown(aoiStep, { key: 'Enter' })).not.toThrow()
    expect(() => fireEvent.keyDown(aoiStep, { key: ' ' })).not.toThrow()
    expect(aoiStep).not.toHaveAttribute('aria-current')
  })
})
