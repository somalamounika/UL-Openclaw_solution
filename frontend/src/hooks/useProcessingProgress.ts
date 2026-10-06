import { create } from 'zustand'
import { useEffect, useRef, useState } from 'react'
import { prefersReducedMotion } from '@/lib/motion'

/**
 * The 0→100 progress model for POST /api/ul/process.
 *
 * The request is one blocking call with no intermediate events, so this number is an
 * estimate derived from elapsed time. It is deliberately slow and decelerating: it
 * always advances, never jumps, and never reaches 100 until the run actually returns.
 *
 * This is a store, not per-component state, so every surface that renders progress
 * shows the same number.
 */

/** Time constant of the curve. Larger = slower climb. */
const TAU_MS = 55_000
/** Never claim completion on a guess. */
const CEILING = 96

/**
 * Captions describe the work from the user's side of the glass — what is being made,
 * never which service is doing it or how long the backend takes.
 */
const CAPTIONS: Array<{ fromMs: number; text: string }> = [
  { fromMs: 0, text: 'Reading your documents' },
  { fromMs: 12_000, text: 'Organising the content' },
  { fromMs: 35_000, text: 'Identifying entities' },
  { fromMs: 70_000, text: 'Mapping relationships' },
  { fromMs: 120_000, text: 'Assembling the knowledge graph' },
  { fromMs: 180_000, text: 'Finalising' },
]

export function captionForElapsed(elapsedMs: number): string {
  let caption = CAPTIONS[0].text
  for (const entry of CAPTIONS) {
    if (elapsedMs >= entry.fromMs) caption = entry.text
  }
  return caption
}

export type ProgressPhase = 'idle' | 'running' | 'done' | 'error'

interface ProgressState {
  phase: ProgressPhase
  /** 0–100, monotonically non-decreasing for the lifetime of one run. */
  percent: number
  startedAt: number | null
  start: () => void
  /** Re-derive from stored elapsed time. Never rewinds. */
  tick: (now: number) => void
  complete: () => void
  fail: () => void
  reset: () => void
}

/**
 * Pure function of elapsed time — this is why a re-render or a resize can never rewind
 * the bar: the displayed value is always re-derived, never re-animated.
 *
 * An exponential approach to the ceiling gives fast-feeling early movement and an
 * ever-slower tail, so a long run keeps creeping instead of stalling at a round number.
 */
export function progressForElapsed(elapsedMs: number): number {
  if (elapsedMs <= 0) return 0
  return CEILING * (1 - Math.exp(-elapsedMs / TAU_MS))
}

export const useProcessingProgressStore = create<ProgressState>((set, get) => ({
  phase: 'idle',
  percent: 0,
  startedAt: null,

  start: () => set({ phase: 'running', percent: 0, startedAt: Date.now() }),

  tick: (now) => {
    const { phase, startedAt, percent } = get()
    if (phase !== 'running' || startedAt === null) return
    const next = progressForElapsed(now - startedAt)
    // Monotonic by construction, but clamp anyway — clock changes are not our problem.
    if (next <= percent) return
    set({ percent: next })
  },

  complete: () => set({ phase: 'done', percent: 100 }),

  /** On failure the fill holds where it stopped; it never resets to 0. */
  fail: () => set({ phase: 'error' }),

  reset: () => set({ phase: 'idle', percent: 0, startedAt: null }),
}))

/**
 * Drives the store's clock. Mount this once (AppShell does); every other component
 * simply reads the store, so they always render the same number.
 */
export function useProcessingProgressClock(): void {
  const phase = useProcessingProgressStore((s) => s.phase)
  const tick = useProcessingProgressStore((s) => s.tick)
  const frame = useRef<number | null>(null)

  useEffect(() => {
    if (phase !== 'running') return

    // Reduced motion: discrete updates every ~2s instead of a continuous sweep.
    if (prefersReducedMotion()) {
      const id = window.setInterval(() => tick(Date.now()), 2000)
      tick(Date.now())
      return () => window.clearInterval(id)
    }

    const loop = () => {
      tick(Date.now())
      frame.current = window.requestAnimationFrame(loop)
    }
    frame.current = window.requestAnimationFrame(loop)
    return () => {
      if (frame.current !== null) window.cancelAnimationFrame(frame.current)
    }
  }, [phase, tick])
}

/** Read-only view for rendering surfaces. */
export function useProcessingProgress() {
  const phase = useProcessingProgressStore((s) => s.phase)
  const percent = useProcessingProgressStore((s) => s.percent)
  const startedAt = useProcessingProgressStore((s) => s.startedAt)

  // The clock above drives `percent` on every frame; the elapsed label only needs to
  // change once a second, so it ticks on its own timer rather than re-rendering at 60Hz.
  const [elapsedMs, setElapsedMs] = useState(0)
  useEffect(() => {
    if (phase !== 'running' || startedAt === null) return
    const update = () => setElapsedMs(Date.now() - startedAt)
    update()
    const id = window.setInterval(update, 1000)
    return () => window.clearInterval(id)
  }, [phase, startedAt])

  return {
    phase,
    percent,
    elapsedMs,
    caption: captionForElapsed(elapsedMs),
    /** True while the number is a heuristic rather than a reported fact. */
    isEstimate: phase === 'running',
  }
}

/** mm:ss, the only time the UI ever puts on screen. */
export function formatElapsed(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000))
  const minutes = Math.floor(total / 60)
  const seconds = total % 60
  return `${minutes}:${String(seconds).padStart(2, '0')}`
}
