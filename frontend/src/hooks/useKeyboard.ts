import { useEffect } from 'react'

export interface KeyHandlers {
  [combo: string]: (event: KeyboardEvent) => void
}

function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false
  const tag = target.tagName
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || target.isContentEditable
}

/**
 * Global shortcuts. Keys are matched on `event.key`; a leading "mod+"/"alt+"/"shift+"
 * qualifies. Handlers never fire while the user is typing, except for Escape.
 */
export function useKeyboard(handlers: KeyHandlers, enabled = true): void {
  useEffect(() => {
    if (!enabled) return
    const onKeyDown = (event: KeyboardEvent) => {
      const typing = isTypingTarget(event.target)
      if (typing && event.key !== 'Escape') return

      const parts: string[] = []
      if (event.metaKey || event.ctrlKey) parts.push('mod')
      if (event.altKey) parts.push('alt')
      if (event.shiftKey) parts.push('shift')
      parts.push(event.key.length === 1 ? event.key.toLowerCase() : event.key)
      const combo = parts.join('+')

      const handler = handlers[combo] ?? handlers[event.key.toLowerCase()]
      if (handler) {
        handler(event)
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [handlers, enabled])
}
