import { useMemo } from 'react'
import { useGraphStore } from '@/state/graphStore'
import { topDegreeNodes } from '@/lib/adjacency'
import { typeDisplay } from '@/lib/ontology'

/**
 * Screen-reader structural summary sitting beside the canvas. A canvas element is
 * opaque to assistive tech, so the graph's shape is described in text.
 */
export function GraphA11ySummary() {
  const model = useGraphStore((s) => s.model)
  const visible = useGraphStore((s) => s.visible)

  const summary = useMemo(() => {
    if (model.nodes.length === 0) return 'No knowledge graph is loaded.'
    const types = [...model.typeCounts.entries()]
      .sort((a, b) => b[1] - a[1])
      .map(([label, count]) => `${count} ${typeDisplay(label)}`)
      .join(', ')
    const hubs = topDegreeNodes(model, 5)
      .map((node) => `${node.name} (${typeDisplay(node.label)}, ${node.degree} connections)`)
      .join('; ')
    return `Knowledge graph with ${model.nodes.length} nodes and ${model.edges.length} relationships, ${visible.size} currently shown. Node types: ${types}. Most connected: ${hubs}.`
  }, [model, visible])

  return (
    <p id="graph-a11y-summary" className="sr-only">
      {summary}
    </p>
  )
}
