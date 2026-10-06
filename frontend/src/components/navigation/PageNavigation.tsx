import { NavLink } from 'react-router-dom'
import { Inbox, MessagesSquare, Network, Upload } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Tooltip } from '@/components/ui/tooltip'

export interface NavItem {
  to: string
  label: string
  icon: typeof Upload
}

const ITEMS: NavItem[] = [
  { to: '/', label: 'Upload & Process', icon: Upload },
  { to: '/openclaw', label: 'Overview', icon: Inbox },
  { to: '/assistant', label: 'AI Assistant', icon: MessagesSquare },
  { to: '/graph', label: 'Knowledge Graph', icon: Network },
]

/**
 * The Assistant and Graph pages answer questions *about a graph*, so they stay locked
 * until one exists. The lock explains itself rather than just going grey.
 */
export function PageNavigation({ hasGraph }: { hasGraph: boolean }) {
  return (
    <nav className="flex items-center gap-1" aria-label="Main">
      {ITEMS.map((item) => {
        const locked = !hasGraph && item.to !== '/' && item.to !== '/openclaw'
        const Icon = item.icon

        if (locked) {
          return (
            <Tooltip key={item.to} content="Build a knowledge graph first.">
              <span
                aria-disabled
                className="flex cursor-not-allowed items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium text-fg-subtle opacity-60"
              >
                <Icon className="h-3.5 w-3.5" />
                {item.label}
              </span>
            </Tooltip>
          )
        }

        return (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.to === '/'}
            className={({ isActive }) =>
              cn(
                'flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition-colors',
                'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]',
                isActive
                  ? 'bg-[var(--accent-soft)] text-accent'
                  : 'text-fg-muted hover:bg-[var(--surface-3)] hover:text-fg',
              )
            }
          >
            <Icon className="h-3.5 w-3.5" />
            {item.label}
          </NavLink>
        )
      })}
    </nav>
  )
}
