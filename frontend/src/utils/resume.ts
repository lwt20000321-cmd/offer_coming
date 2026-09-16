const ALLOWED_EXTENSIONS = ['.pdf', '.docx', '.txt']
export const RESUME_MAX_BYTES = 10 * 1024 * 1024

export function resumeExtension(filename: string): string {
  const match = filename.toLowerCase().match(/\.[^.]+$/)
  return match ? match[0] : ''
}

export function isAllowedResume(file: File): boolean {
  return ALLOWED_EXTENSIONS.includes(resumeExtension(file.name)) && file.size <= RESUME_MAX_BYTES
}

export function resumeHint(): string {
  return '请上传 PDF、DOCX 或 TXT，大小不超过 10MB。'
}
