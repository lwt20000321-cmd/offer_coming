const DRAFT_KEY = 'chat_draft'

export function readChatDraft(): string {
  try {
    return sessionStorage.getItem(DRAFT_KEY) ?? ''
  } catch {
    return ''
  }
}

export function writeChatDraft(value: string): void {
  try {
    if (value) {
      sessionStorage.setItem(DRAFT_KEY, value)
    } else {
      sessionStorage.removeItem(DRAFT_KEY)
    }
  } catch {
    // ignore quota / private mode
  }
}
