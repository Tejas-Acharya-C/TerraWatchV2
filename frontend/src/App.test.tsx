import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import App from './App'

vi.mock('./MapCanvas', () => ({
  default: () => <div data-testid="mock-map">Map Canvas</div>,
}))

describe('App root component', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input: RequestInfo | URL) => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.toString() : input.url
      if (url.includes('/api/v1/aoi')) {
        return new Response(JSON.stringify([]), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }
      return new Response(JSON.stringify({}), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    })
  })

  it('renders the neutral product heading and branding', () => {
    render(<App />)
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Change Detection for Karnataka')
  })

  it('starts in clean data-free state with no active AOI', async () => {
    render(<App />)
    expect(screen.getByTestId('aoi-state')).toHaveTextContent('No area selected')
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Draw area/i })).toBeInTheDocument()
    })
  })

  it('renders the workflow progress rail with stage indicators', () => {
    render(<App />)
    expect(screen.getByRole('group', { name: /Analysis workflow status/i })).toBeInTheDocument()
    expect(screen.getByLabelText(/01 AREA/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/02 OBSERVATIONS/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/03 CHANGES/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/04 CHANGE HISTORY/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/05 CANDIDATES/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/06 EVIDENCE/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/07 REVIEW/i)).toBeInTheDocument()
  })
})
