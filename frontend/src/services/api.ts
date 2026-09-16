import axios from 'axios'
import { clearSessionToken, readSessionToken } from '../utils/storage'

const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '/api',
  timeout: 10000,
})

type UnauthorizedListener = () => void
const unauthorizedListeners = new Set<UnauthorizedListener>()

export function onUnauthorized(listener: UnauthorizedListener): () => void {
  unauthorizedListeners.add(listener)
  return () => {
    unauthorizedListeners.delete(listener)
  }
}

export function notifyUnauthorized(): void {
  clearSessionToken()
  unauthorizedListeners.forEach((listener) => listener())
}

function requestUrl(config: { baseURL?: string; url?: string }): string {
  const base = config.baseURL ?? ''
  const path = config.url ?? ''
  if (/^https?:\/\//.test(path)) {
    return path
  }
  return `${base}${path}`
}

function isPublicAuthRequest(method: string | undefined, url: string): boolean {
  const normalized = url.split('?')[0].replace(/\/+$/, '')
  const verb = (method ?? 'get').toLowerCase()
  return (
    verb === 'post' &&
    (normalized.endsWith('/candidates') || normalized.endsWith('/sessions'))
  )
}

api.interceptors.request.use((config) => {
  if (!isPublicAuthRequest(config.method, requestUrl(config))) {
    const token = readSessionToken()
    if (token) {
      config.headers.Authorization = `Bearer ${token}`
    }
  }
  return config
})

api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (axios.isAxiosError(error) && error.response?.status === 401) {
      const url = requestUrl(error.config ?? {})
      if (!isPublicAuthRequest(error.config?.method, url)) {
        notifyUnauthorized()
      }
    }
    return Promise.reject(error)
  },
)

export function isMockEnabled(): boolean {
  return String(import.meta.env.VITE_USE_MOCK || '').toLowerCase() === 'true'
}

export default api
