import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs))
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

/** "3 minutes ago" / "just now". Absolute dates once past a week. */
export function relativeTime(iso: string): string {
  if (!iso) return ''
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return iso
  const seconds = Math.round((Date.now() - then) / 1000)
  if (seconds < 45) return 'just now'
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) return `${minutes} min ago`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours} hr ago`
  const days = Math.round(hours / 24)
  if (days < 7) return `${days} d ago`
  return new Date(then).toLocaleDateString()
}

export function formatNumber(value: number): string {
  return value.toLocaleString()
}

/** Case-insensitive subsequence match with a score, for the `/` graph search. */
export function fuzzyScore(query: string, target: string): number {
  if (!query) return 0
  const q = query.toLowerCase()
  const t = target.toLowerCase()
  if (t === q) return 1000
  const exactIndex = t.indexOf(q)
  if (exactIndex === 0) return 800 - t.length
  if (exactIndex > 0) return 600 - exactIndex - t.length * 0.1

  let score = 0
  let ti = 0
  let streak = 0
  for (const char of q) {
    const found = t.indexOf(char, ti)
    if (found === -1) return -1
    streak = found === ti ? streak + 1 : 0
    score += 10 + streak * 4 - Math.min(found - ti, 12)
    ti = found + 1
  }
  return score
}

export function uniqueStrings(values: string[]): string[] {
  return [...new Set(values.filter(Boolean))]
}
