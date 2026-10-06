import { CircleDot } from 'lucide-react'
import { useGraphStore } from '@/state/graphStore'
import { useBackendStatus } from '@/hooks/useGraphData'
import { formatNumber, cn } from '@/lib/utils'
import { Tooltip } from '@/components/ui/tooltip'

/** Whether a graph is loaded, and how big it is — visible from every page. */
export function GraphStatus({ graphId }: { graphId: string | null }) {
  const model = useGraphStore((s) => s.model)
  const backend = useBackendStatus()
  const hasGraph = model.nodes.length > 0

  return (
    <div className="flex items-center gap-3 text-2xs">
      <Tooltip
        content={
          backend === 'online'
            ? 'Backend reachable'
            : backend === 'checking'
              ? 'Checking the backend…'
              : 'The FastAPI service is not reachable'
        }
      >
        <span className="flex items-center gap-1.5 text-fg-muted">
          <CircleDot
            className={cn(
              'h-2.5 w-2.5',
              backend === 'online'
                ? 'text-[var(--success)]'
                : backend === 'checking'
                  ? 'text-fg-subtle'
                  : 'text-[var(--danger)]',
            )}
          />
          <span className="hidden sm:inline">
            {hasGraph ? 'Graph loaded' : 'No graph loaded'}
          </span>
        </span>
      </Tooltip>

      {graphId ? (
        <span className="hidden font-mono text-fg-muted md:inline">{graphId}</span>
      ) : null}

      {hasGraph ? (
        <span className="hidden items-center gap-2 tabular-nums text-fg-muted lg:flex">
          <span>
            <span className="font-medium text-fg">{formatNumber(model.nodes.length)}</span> nodes
          </span>
          <span>
            <span className="font-medium text-fg">{formatNumber(model.edges.length)}</span> relationships
          </span>
        </span>
      ) : null}
    </div>
  )
}
