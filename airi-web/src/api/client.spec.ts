import { describe, expect, it } from 'vitest'
import { ApiError, describeApiError, normalizeApiError } from './client'

describe('normalizeApiError', () => {
  it('keeps the AIRI governance error code and message', () => {
    const error = normalizeApiError(409, {
      error: { code: 'approval_required', message: 'Human approval required', request_id: 'req-1' },
    })
    expect(error).toBeInstanceOf(ApiError)
    expect(error.status).toBe(409)
    expect(error.code).toBe('approval_required')
    expect(error.message).toBe('Human approval required')
    expect(error.requestId).toBe('req-1')
  })

  it('maps FastAPI validation details to a readable validation_error', () => {
    const error = normalizeApiError(422, {
      detail: [{ msg: 'Field required', loc: ['body', 'requirement'] }],
    })
    expect(error.status).toBe(422)
    expect(error.code).toBe('validation_error')
    expect(error.message).toContain('body.requirement')
  })

  it('falls back to http_error for unknown bodies', () => {
    const error = normalizeApiError(500, null)
    expect(error.code).toBe('http_error')
    expect(error.status).toBe(500)
  })
})

describe('describeApiError', () => {
  it('names 409 rejections as governance refusals', () => {
    const error = new ApiError(409, 'production_reconciliation_not_required', 'not required')
    expect(describeApiError(error)).toContain('Governance check refused')
    expect(describeApiError(error)).toContain('production_reconciliation_not_required')
  })

  it('keeps status and code for other errors', () => {
    const error = new ApiError(404, 'experiment_resource_not_found', 'missing')
    expect(describeApiError(error)).toBe('[404 experiment_resource_not_found] missing')
  })

  it('falls back to plain messages', () => {
    expect(describeApiError(new Error('boom'))).toBe('boom')
    expect(describeApiError('weird')).toBe('Unknown error')
  })
})
