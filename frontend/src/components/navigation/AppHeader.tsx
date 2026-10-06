import { Keyboard, Moon, Network, Sun } from 'lucide-react'
import { Link } from 'react-router-dom'
import { PageNavigation } from './PageNavigation'
import { GraphStatus } from './GraphStatus'
import { Button } from '@/components/ui/button'
import { Tooltip } from '@/components/ui/tooltip'
import { useGraphStore } from '@/state/graphStore'
import type { Theme } from '@/hooks/useTheme'

export function AppHeader({
  graphId,
  hasGraph,
  theme,
  onToggleTheme,
}: {
  graphId: string | null
  hasGraph: boolean
  theme: Theme
  onToggleTheme: () => void
}) {
  const setShortcutsOpen = useGraphStore((s) => s.setShortcutsOpen)

  return (
    <header className="flex h-14 shrink-0 items-center gap-4 border-b border-[var(--border)] bg-[var(--surface)] px-4">
      <Link to="/" className="flex items-center gap-2.5 rounded focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]">
        <div className="flex h-7 w-7 items-center justify-center rounded-md bg-accent">
          <Network className="h-4 w-4 text-[var(--accent-fg)]" />
        </div>
        <div className="leading-none">
          <div className="text-sm font-semibold tracking-tight text-fg">UL Atlas</div>
          <div className="mt-1 text-[10px] text-fg-subtle">Compliance Knowledge Graph</div>
        </div>
      </Link>

      <div className="mx-1 hidden h-6 w-px bg-[var(--border)] sm:block" />

      <PageNavigation hasGraph={hasGraph} />

      <div className="ml-auto flex items-center gap-3">
        <GraphStatus graphId={graphId} />
        <div className="flex items-center gap-1">
          <Tooltip content="Keyboard shortcuts  ( ? )">
            <Button variant="ghost" size="icon" onClick={() => setShortcutsOpen(true)} aria-label="Keyboard shortcuts">
              <Keyboard className="h-3.5 w-3.5" />
            </Button>
          </Tooltip>
          <Tooltip content={theme === 'dark' ? 'Light theme' : 'Dark theme'}>
            <Button variant="ghost" size="icon" onClick={onToggleTheme} aria-label="Toggle theme">
              {theme === 'dark' ? <Sun className="h-3.5 w-3.5" /> : <Moon className="h-3.5 w-3.5" />}
            </Button>
          </Tooltip>
        </div>
      </div>
    </header>
  )
}
