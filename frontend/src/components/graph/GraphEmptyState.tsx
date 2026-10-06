import { Database, Network, PlugZap, Loader2, ServerCrash } from 'lucide-react'

export type EmptyKind =
  | 'offline'
  | 'store-unreachable'
  | 'no-graphs'
  | 'empty-graph'
  | 'not-ingested'
  | 'loading'
  | 'none'

/** Each distinct failure gets its own copy — never one generic "nothing here". */
export function GraphEmptyState({ kind }: { kind: EmptyKind }) {
  if (kind === 'none') return null

  const content = {
    offline: {
      icon: PlugZap,
      title: 'Backend unreachable',
      body: 'UL Atlas cannot reach the FastAPI service. Start it with `uvicorn main:app --reload` from the backend directory, then this view will recover on its own.',
    },
    'store-unreachable': {
      icon: ServerCrash,
      title: 'Graph store unreachable',
      body: 'The API is up, but it cannot reach Neo4j — so there are no graphs to read or draw. Start Neo4j and confirm NEO4J_URI / NEO4J_PASSWORD in the backend environment. The Sources rail shows the exact error.',
    },
    'no-graphs': {
      icon: Network,
      title: 'No knowledge graph yet',
      body: 'Drop product or supplier PDFs into the Sources rail and build your first graph. Extraction uses your uploads plus any catalogues in backend/documents (standards, certifications, tests).',
    },
    'empty-graph': {
      icon: Network,
      title: 'This graph has no nodes',
      body: 'The run completed but stored nothing in the canonical UL graph. That usually means the target product filter matched no products, or extraction found no entities in these documents.',
    },
    'not-ingested': {
      icon: Database,
      title: 'Graph was not written to Neo4j',
      body: 'Extraction succeeded but the Neo4j MERGE did not run, so there is nothing to draw. Check that Neo4j is up and NEO4J_URI / NEO4J_PASSWORD are set, then build again.',
    },
    loading: {
      icon: Loader2,
      title: 'Loading graph…',
      body: 'Fetching nodes and relationships from Neo4j.',
    },
  }[kind]

  const Icon = content.icon

  return (
    <div className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center p-6">
      <div className="max-w-sm text-center">
        <Icon
          className={`mx-auto h-7 w-7 text-fg-subtle ${kind === 'loading' ? 'animate-spin' : ''}`}
        />
        <h2 className="mt-3 text-sm font-semibold text-fg">{content.title}</h2>
        <p className="mt-1.5 text-xs leading-relaxed text-fg-muted">{content.body}</p>
      </div>
    </div>
  )
}
