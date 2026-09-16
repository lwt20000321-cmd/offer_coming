import type { ApiEnvelope } from '../types/session'

export class ApiError extends Error {
  constructor(
    public status: number,
    public errorCode: string | null,
    public errorText: string | null,
  ) {
    super(errorText || '请求失败')
    this.name = 'ApiError'
  }
}

export function unwrapEnvelope<T>(status: number, body: ApiEnvelope<T>): T {
  if (status >= 400 || !body.success || body.data == null) {
    throw new ApiError(status, body.error_code, body.error)
  }
  return body.data
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError
}
