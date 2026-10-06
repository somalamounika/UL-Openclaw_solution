import {
  ChevronsDownUp,
  ChevronsUpDown,
  Maximize2,
  Minus,
  Plus,
  Rows3,
  Search,
  Workflow,
} from 'lucide-react'
import { useGraphStore } from '@/state/graphStore'
import { canvas } from '@/lib/canvasApi'
import { LAYOUT_LABELS, type LayoutName } from '@/lib/layout'
import { LOD_SPECS } from '@/lib/lod'
import { Button } from '@/components/ui/button'
import { Tooltip } from '@/components/ui/tooltip'
import { Popover } from '@/components/ui/popover'
import { cn } from '@/lib/utils'

export function GraphToolbar({ arranging }: { arranging: boolean }) {
  const layout = useGraphStore((s) => s.layout)
  const setLayout = useGraphStore((s) => s.setLayout)
  const density = useGraphStore((s) => s.density)
  const setDensity = useGraphStore((s) => s.setDensity)
  const expandAll = useGraphStore((s) => s.expandAll)
  const collapseAll = useGraphStore((s) => s.collapseAll)
  const setSearchOpen = useGraphStore((s) => s.setSearchOpen)
  const lod = useGraphStore((s) => s.lod)
  const visible = useGraphStore((s) => s.visible)
  const model = useGraphStore((s) => s.model)
  const nodeFocus = useGraphStore((s) => s.nodeFocus)

  return (
    <div className="pointer-events-none absolute left-3 top-3 z-20 flex flex-col gap-2">
      <div className="canvas-overlay-panel pointer-events-auto flex items-center gap-1 p-1">
        <Popover
          trigger={
            <Button variant="ghost" size="sm" className="gap-1.5">
              <Workflow className="h-3.5 w-3.5" />
              {LAYOUT_LABELS[layout]}
            </Button>
          }
          className="w-52"
        >
          {(Object.keys(LAYOUT_LABELS) as LayoutName[]).map((name) => (
            <button
              key={name}
              type="button"
              onClick={() => setLayout(name)}
              className={cn(
                'w-full rounded px-2 py-1.5 text-left text-xs hover:bg-[var(--surface-3)]',
                layout === name ? 'text-accent' : 'text-fg',
              )}
            >
              {LAYOUT_LABELS[name]}
            </button>
          ))}
        </Popover>

        <div className="h-4 w-px bg-[var(--border)]" />

        <Tooltip content="Zoom out  ( − )">
          <Button variant="ghost" size="icon" onClick={() => canvas()?.zoomBy(1 / 1.3)} aria-label="Zoom out">
            <Minus className="h-3.5 w-3.5" />
          </Button>
        </Tooltip>
        <Tooltip content="Zoom in  ( + )">
          <Button variant="ghost" size="icon" onClick={() => canvas()?.zoomBy(1.3)} aria-label="Zoom in">
            <Plus className="h-3.5 w-3.5" />
          </Button>
        </Tooltip>
        <Tooltip content="Fit to screen  ( 0 )">
          <Button variant="ghost" size="icon" onClick={() => canvas()?.fit()} aria-label="Fit to screen">
            <Maximize2 className="h-3.5 w-3.5" />
          </Button>
        </Tooltip>

        <div className="h-4 w-px bg-[var(--border)]" />

        <Tooltip content={density === 'compact' ? 'Comfortable density' : 'Compact density'}>
          <Button
            variant="ghost"
            size="icon"
            aria-label="Toggle density"
            aria-pressed={density === 'compact'}
            onClick={() => setDensity(density === 'compact' ? 'comfortable' : 'compact')}
          >
            <Rows3 className="h-3.5 w-3.5" />
          </Button>
        </Tooltip>
        <Tooltip content={nodeFocus ? 'Unavailable while focused on a node' : 'Expand everything'}>
          <Button variant="ghost" size="icon" onClick={expandAll} aria-label="Expand all" disabled={Boolean(nodeFocus)}>
            <ChevronsUpDown className="h-3.5 w-3.5" />
          </Button>
        </Tooltip>
        <Tooltip content={nodeFocus ? 'Unavailable while focused on a node' : 'Collapse to anchors'}>
          <Button variant="ghost" size="icon" onClick={collapseAll} aria-label="Collapse all" disabled={Boolean(nodeFocus)}>
            <ChevronsDownUp className="h-3.5 w-3.5" />
          </Button>
        </Tooltip>

        <div className="h-4 w-px bg-[var(--border)]" />

        <Tooltip content="Search the graph  ( / )">
          <Button variant="ghost" size="sm" className="gap-1.5" onClick={() => setSearchOpen(true)}>
            <Search className="h-3.5 w-3.5" />
            Search
          </Button>
        </Tooltip>
      </div>

      <div className="pointer-events-auto flex items-center gap-2">
        <span className="canvas-overlay-panel px-2 py-1 text-2xs text-fg-muted">
          <span className="text-fg-subtle">LOD</span>{' '}
          <span className="font-medium text-fg">
            L{lod} {LOD_SPECS[lod].name}
          </span>
          <span className="mx-1.5 text-fg-subtle">·</span>
          <span className="tabular-nums">
            {visible.size.toLocaleString()}/{model.nodes.length.toLocaleString()} shown
          </span>
        </span>

        {arranging ? (
          <span className="canvas-overlay-panel flex items-center gap-1.5 px-2 py-1 text-2xs text-fg-muted">
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-accent" />
            arranging…
          </span>
        ) : null}
      </div>
    </div>
  )
}
