export function trimLlmKey(value: string): string {
  return value.trim()
}

export function isLlmKeyFormatValid(value: string): boolean {
  const trimmed = trimLlmKey(value)
  return trimmed.startsWith('sk-') && trimmed.length > 3
}

export const LLM_KEY_PROBE_WAIT_MS = 350

export function waitForLlmKeyProbe(): Promise<void> {
  return new Promise((resolve) => {
    window.setTimeout(resolve, LLM_KEY_PROBE_WAIT_MS)
  })
}
