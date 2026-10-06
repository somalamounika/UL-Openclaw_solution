/**
 * The ontology is the design system.
 *
 * Tiers mirror `HIERARCHY_LEVEL` in backend/ul/hierarchy.py verbatim — they are a
 * real backend field, and they drive the semantic-zoom ladder on the canvas.
 * Colours are keyed on the Neo4j label (PascalCase) that `/api/ul/graph` returns.
 *
 * The palette is Neo4j's own: pastel fills carrying dark ink, so the caption sits
 * *inside* the circle and stays readable without an outline. Borders are derived from
 * the fill rather than hand-picked, which keeps a new type one line of work.
 *
 * Any label not in this table renders through ENTITY_FALLBACK rather than crashing.
 */

export type OntologyTier = 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9

export interface OntologyType {
  /** Neo4j label as it arrives on the wire, e.g. "Location". */
  label: string
  /** Backend token from HIERARCHY_LEVEL, e.g. "manufacturinglocation". */
  token: string
  /** Human-facing name. */
  display: string
  tier: OntologyTier
  fill: string
  border: string
}

/** Darken a hex toward black by `amount` (0–1). Used for every border. */
function darken(hex: string, amount: number): string {
  const h = hex.replace('#', '')
  const n = parseInt(h.length === 3 ? h.split('').map((c) => c + c).join('') : h, 16)
  const mix = (channel: number) => Math.round(channel * (1 - amount))
  const r = mix((n >> 16) & 255)
  const g = mix((n >> 8) & 255)
  const b = mix(n & 255)
  return `#${((1 << 24) | (r << 16) | (g << 8) | b).toString(16).slice(1)}`
}

const T = (
  label: string,
  token: string,
  display: string,
  tier: OntologyTier,
  fill: string,
): OntologyType => ({ label, token, display, tier, fill, border: darken(fill, 0.22) })

/** Caption ink. Every fill above is pastel, so one dark ink works for all of them. */
export const NODE_INK = '#1a1b1d'

/** 26 node types — backend labels plus UL as the canonical root. */
export const ONTOLOGY: OntologyType[] = [
  T('UL', 'ul', 'UL', 1, '#a7ebff'),
  T('Company', 'company', 'Company', 1, '#ffbfb5'),
  T('PartyRole', 'partyrole', 'Party Role', 2, '#faca9d'),
  T('Location', 'location', 'Location', 2, '#e4a8ff'),
  T('Certification', 'certification', 'Certification', 2, '#8fe3e8'),
  T('ProductFamily', 'productfamily', 'Product Family', 2, '#ffa2fd'),
  T('Address', 'address', 'Address', 3, '#cdd9ff'),
  T('FileNumber', 'filenumber', 'File Number', 3, '#d7d99a'),
  T('Certificate', 'certificate', 'Certificate', 3, '#a5e6c6'),
  T('Deliverable', 'deliverable', 'Deliverable', 3, '#d7d2c8'),
  T('Product', 'product', 'Product', 3, '#efbbe3'),
  T('Service', 'service', 'Service', 3, '#afafff'),
  T('Opportunity', 'opportunity', 'Opportunity', 3, '#bdb9dc'),
  T('Volume', 'volume', 'Volume', 4, '#c3e2a8'),
  T('Model', 'model', 'Model', 4, '#b186ff'),
  T('Quote', 'quote', 'Quote', 4, '#e0c3fc'),
  T('Section', 'section', 'Section', 5, '#ffe0a3'),
  T('Variant', 'variant', 'Variant', 5, '#d3a8ff'),
  T('Component', 'component', 'Component', 6, '#a8bfe5'),
  T('Material', 'material', 'Material', 7, '#ffc581'),
  T('ConditionOfAcceptability', 'conditionofacceptability', 'Condition of Acceptability', 7, '#ffe27a'),
  T('Standard', 'standard', 'Standard', 7, '#df907e'),
  T('TestPlan', 'testplan', 'Test Plan', 7, '#f2a2a2'),
  T('Clause', 'clause', 'Clause', 8, '#abae8a'),
  T('TestRecord', 'testrecord', 'Test Record', 8, '#f7b2c6'),
  T('Test', 'test', 'Test', 9, '#c3b345'),
]

/** Neutral rendering for any label outside the 25 — never crash on an unknown type. */
export const ENTITY_FALLBACK: OntologyType = T('Entity', 'entity', 'Entity', 6, '#d5dae1')

const BY_TOKEN = new Map<string, OntologyType>(ONTOLOGY.map((t) => [t.token, t]))

/** Lookup is case/punctuation-insensitive so "Manufacturing Location" also resolves. */
function tokenize(label: string): string {
  return label.toLowerCase().replace(/[^a-z0-9]/g, '')
}

export function resolveType(label: string | null | undefined): OntologyType {
  if (!label) return ENTITY_FALLBACK
  return BY_TOKEN.get(tokenize(label)) ?? ENTITY_FALLBACK
}

export function typeTier(label: string): OntologyTier {
  return resolveType(label).tier
}

export function typeFill(label: string): string {
  return resolveType(label).fill
}

export function typeBorder(label: string): string {
  return resolveType(label).border
}

export function typeDisplay(label: string): string {
  return resolveType(label).display
}

export const MAX_TIER: OntologyTier = 9

/**
 * Tier base radius, in graph units. Neo4j's circles are near-uniform and always big
 * enough to hold two lines of caption, so the tier spread here is deliberately narrow.
 */
const TIER_BASE_RADIUS: Record<OntologyTier, number> = {
  1: 34,
  2: 30,
  3: 28,
  4: 26,
  5: 25,
  6: 24,
  7: 22,
  8: 21,
  9: 20,
}

export function tierBase(tier: OntologyTier): number {
  return TIER_BASE_RADIUS[tier]
}

/**
 * radius = tierBase(level) * (1 + 0.11 * log2(1 + degree)), clamped.
 * A hub still reads as a hub, but never so large that its neighbours look like dust —
 * and never so small that the caption inside it has to be cut to nothing.
 */
export function nodeRadius(label: string, degree: number): number {
  const base = tierBase(typeTier(label))
  const scaled = base * (1 + 0.11 * Math.log2(1 + Math.max(0, degree)))
  return Math.max(20, Math.min(52, scaled))
}

/** Canonical relationship types the pipeline emits (SCREAMING_SNAKE on the wire). */
export const RELATIONSHIP_TYPES = [
  'PARTY_ROLE_APPLICANT',
  'HAS_ROLE',
  'HAS_PRODUCT',
  'HAS_LOCATION',
  'HAS_ADDRESS',
  'HAS_FILE_NUMBER',
  'COVERS_PRODUCT',
  'COVERS_MODEL',
  'HAS_VOLUME',
  'ASSOCIATED_WITH_LOCATION',
  'HAS_MODEL',
  'HAS_VARIANT',
  'COMPLIES_WITH',
  'HAS_CLAUSE',
  'REQUIRES_TEST',
  'HAS_COMPONENT',
  'SUPPLIED_BY',
  'MANUFACTURED_BY',
  'MANUFACTURED_AT',
  'MADE_OF',
  'HAS_CONDITION_OF_ACCEPTABILITY',
  'SUBJECT_TO',
  'CONTAINS',
  'HAS_TEST_PLAN',
  'HAS_TEST_RECORD',
  'FOR_SERVICE',
  'BASED_ON_STANDARD',
  'USES_TEST',
  'GENERATES',
  'HAS_DELIVERABLE',
  'HAS_CERTIFICATION',
  'APPLIES_FOR',
  'SERVES',
  'HAS_OPPORTUNITY',
  'HAS_QUOTE',
  'REQUESTS_SERVICE',
  'FOR_PRODUCT',
  'INCLUDES_SERVICE',
  'RELATED_TO',
] as const

/** HAS_COMPONENT -> "has component". */
export function humanizeRelationship(rel: string): string {
  return rel.toLowerCase().replace(/_/g, ' ').trim()
}

/** Split `<Label>:<name>` — the join key shared by /graph and chat graph_evidence. */
export function splitNodeId(id: string): { label: string; name: string } {
  const idx = id.indexOf(':')
  if (idx === -1) return { label: 'Entity', name: id }
  return { label: id.slice(0, idx), name: id.slice(idx + 1) }
}

/** Hex -> rgba, used for halos, dimming and edge blends. */
export function withAlpha(hex: string, alpha: number): string {
  const h = hex.replace('#', '')
  const full = h.length === 3 ? h.split('').map((c) => c + c).join('') : h
  const n = parseInt(full, 16)
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`
}
