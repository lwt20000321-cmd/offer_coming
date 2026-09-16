export const SESSION_TOKEN_KEY = 'session_token'

export function readSessionToken(): string | null {
  return localStorage.getItem(SESSION_TOKEN_KEY)
}

export function writeSessionToken(token: string): void {
  localStorage.setItem(SESSION_TOKEN_KEY, token)
}

export function clearSessionToken(): void {
  localStorage.removeItem(SESSION_TOKEN_KEY)
}
