import type cytoscape from 'cytoscape'
import type { StylesheetStyle } from 'cytoscape'
import { LOD_SPECS, type Lod } from './lod'
import { ONTOLOGY, ENTITY_FALLBACK, NODE_INK } from './ontology'

export interface ThemeTokens {
  isDark: boolean
  fg: string
  fgMuted: string
  outline: string
  accent: string
  halo: string
  /** Neo4j draws every relationship in one neutral grey, whatever it connects. */
  edgeLine: string
  edgeLabel: string
  /** The canvas ground. Edge labels are painted on it so the line reads behind them. */
  canvasBg: string
}

export const DARK_TOKENS: ThemeTokens = {
  isDark: true,
  fg: '#e8ecf4',
  fgMuted: '#8b97ad',
  outline: '#0b0e14',
  accent: '#38bdf8',
  halo: '#8fe3e8',
  edgeLine: '#5b6472',
  edgeLabel: '#98a1b0',
  canvasBg: '#0a0d13',
}

export const LIGHT_TOKENS: ThemeTokens = {
  isDark: false,
  fg: '#0f172a',
  fgMuted: '#5b6779',
  outline: '#ffffff',
  accent: '#0284c7',
  halo: '#8fe3e8',
  edgeLine: '#a5abb6',
  edgeLabel: '#6f757e',
  canvasBg: '#f7f8f9',
}

/** Caption type size follows the circle: big hubs get bigger text, leaves stay small. */
function captionFontSize(node: cytoscape.NodeSingular): number {
  const size = Number(node.data('size'))
  if (!Number.isFinite(size)) return 10
  return Math.max(8.5, Math.min(13, size * 0.17))
}

/** Wrap width inside the circle. 0.74 of the diameter keeps text off the rim. */
function captionWrapWidth(node: cytoscape.NodeSingular): string {
  const size = Number(node.data('size'))
  return `${Math.max(24, (Number.isFinite(size) ? size : 48) * 0.74)}px`
}

/**
 * The stylesheet is rebuilt only when the theme or the LOD changes — never per frame.
 * Everything zoom-dependent that Cytoscape can express natively (min-zoomed-font-size,
 * data-comparison selectors on `tier`) is expressed here so the renderer does the
 * gating, not JavaScript.
 *
 * The look is Neo4j's: pastel discs carrying their caption inside, joined by thin grey
 * arrows whose relationship type is written on the line at all times.
 */
export function buildStylesheet(theme: ThemeTokens, lod: Lod): StylesheetStyle[] {
  const spec = LOD_SPECS[lod]
  const showCaptions = spec.captionChars > 0
  const showEdgeLabels = spec.edgeLabels === 'always'

  const sheet: StylesheetStyle[] = [
    {
      selector: 'node',
      style: {
        'background-color': ENTITY_FALLBACK.fill,
        'border-color': ENTITY_FALLBACK.border,
        'border-width': 1.4,
        'border-opacity': 0.55,
        width: 'data(size)',
        height: 'data(size)',
        shape: 'ellipse',
        label: showCaptions ? 'data(caption)' : '',
        // Neo4j-style: the caption lives inside the disc, in dark ink on a pastel fill.
        'text-valign': 'center',
        'text-halign': 'center',
        color: NODE_INK,
        'font-family': 'Inter, "Open Sans", ui-sans-serif, system-ui, sans-serif',
        'font-size': captionFontSize,
        'font-weight': 500,
        'text-wrap': 'wrap',
        'text-max-width': captionWrapWidth,
        // Screen-space clamp: the renderer drops captions that would be unreadable.
        'min-zoomed-font-size': showCaptions ? 6 : 999,
        'text-outline-width': 0,
        'overlay-opacity': 0,
        'transition-property': 'opacity, border-width, background-color',
        'transition-duration': 200,
        'z-index': 10,
      },
    },
    {
      selector: 'edge',
      style: {
        width: 1.1,
        'line-color': theme.edgeLine,
        'target-arrow-color': theme.edgeLine,
        'target-arrow-shape': 'triangle',
        'arrow-scale': 0.85,
        'curve-style': 'bezier',
        // Parallel edges fan out instead of stacking into an unreadable stripe.
        'control-point-step-size': 48,
        'control-point-distance': (edge: cytoscape.EdgeSingular) => Number(edge.data('bundleOffset') ?? 0),
        'control-point-weight': 0.5,
        opacity: 1,
        label: showEdgeLabels ? 'data(caption)' : '',
        'font-size': 8.5,
        'font-family': 'Inter, "Open Sans", ui-sans-serif, system-ui, sans-serif',
        color: theme.edgeLabel,
        'text-rotation': 'autorotate',
        // Painted on the canvas ground so the line does not strike through the type name.
        'text-background-color': theme.canvasBg,
        'text-background-opacity': 1,
        'text-background-padding': '2px',
        'text-background-shape': 'roundrectangle',
        'text-outline-width': 0,
        // Also keeps labels off edges that are shorter than their own text on screen.
        'min-zoomed-font-size': showEdgeLabels ? 6 : 999,
        'text-max-width': '200px',
        'text-wrap': 'none',
        'overlay-opacity': 0,
        'transition-property': 'opacity, width, line-color',
        'transition-duration': 200,
        'z-index': 1,
      },
    },

    /* Aggregated edges: one thick edge per type-pair at L0, thickness = member count. */
    {
      selector: 'edge.aggregate',
      style: {
        width: 'data(weight)',
        'curve-style': 'straight',
        opacity: 0.55,
        'target-arrow-shape': 'none',
        label: '',
        'z-index': 1,
      },
    },

    /* Tier gating — a data-comparison selector, evaluated by the renderer. */
    {
      selector: `node[tier > ${spec.maxTier}]`,
      style: { display: 'none' },
    },

    /* Progressive disclosure: not-yet-expanded nodes and their edges are absent. */
    { selector: '.collapsed-hidden', style: { display: 'none' } },
    { selector: '.type-filtered', style: { display: 'none' } },

    /* Hidden-neighbour ring badge ("+n"). Zero hidden -> no badge class, no badge. */
    {
      selector: 'node.has-hidden',
      style: {
        'border-style': 'double',
        'border-width': 4,
        'border-opacity': 0.75,
      },
    },

    /* Selection: Neo4j's double ring — a light gap, then the halo. */
    {
      selector: 'node:selected',
      style: {
        'border-width': 2.5,
        'border-opacity': 1,
        'border-color': theme.isDark ? '#11151c' : '#ffffff',
        'underlay-color': theme.halo,
        'underlay-opacity': 1,
        'underlay-padding': 5,
        'underlay-shape': 'ellipse',
        'z-index': 40,
      },
    },
    {
      selector: 'node.hovered',
      style: {
        'border-width': 3,
        'border-opacity': 1,
        'z-index': 45,
      },
    },
    {
      selector: 'edge.hovered',
      style: {
        width: 2.4,
        'line-color': theme.fgMuted,
        'target-arrow-color': theme.fgMuted,
        opacity: 1,
        label: 'data(caption)',
        'min-zoomed-font-size': 4,
        color: theme.fg,
        'z-index': 50,
      },
    },

    /* Degree-of-interest: focus dims by hop distance, it never deletes. */
    { selector: 'node.hop-0', style: { opacity: 1 } },
    { selector: 'node.hop-1', style: { opacity: 0.85 } },
    { selector: 'node.hop-2', style: { opacity: 0.45 } },
    { selector: 'node.hop-far', style: { opacity: 0.15, label: '' } },
    { selector: 'edge.hop-far', style: { opacity: 0.08, label: '' } },
    { selector: 'edge.hop-dim', style: { opacity: 0.28 } },

    /* Single-selection neighbourhood dimming. */
    { selector: 'node.dimmed', style: { opacity: 0.25 } },
    { selector: 'edge.dimmed', style: { opacity: 0.12, label: '' } },

    /* Evidence-focus mode: cited elements halo, everything else drops to 8%.
       These rules sit after the tier gate and the collapse rule on purpose: a node the
       assistant cited must be drawn even when the current LOD would gate its tier away,
       otherwise "show me the evidence" silently omits the deepest evidence. */
    {
      selector: 'node.evidence',
      style: {
        display: 'element',
        'border-width': 3,
        'border-opacity': 1,
        'border-color': theme.isDark ? '#11151c' : '#ffffff',
        'underlay-color': theme.halo,
        'underlay-opacity': 0.9,
        'underlay-padding': 7,
        'underlay-shape': 'ellipse',
        opacity: 1,
        'z-index': 60,
      },
    },
    {
      selector: 'edge.evidence',
      style: {
        display: 'element',
        width: 2.6,
        'line-color': theme.halo,
        'target-arrow-color': theme.halo,
        color: theme.fg,
        opacity: 1,
        'line-dash-pattern': [7, 5],
        'z-index': 55,
      },
    },
    { selector: 'node.evidence-muted', style: { opacity: 0.12, label: '' } },
    { selector: 'edge.evidence-muted', style: { opacity: 0.08, label: '' } },

    /* Search / chat-chip hover pulse. */
    {
      selector: 'node.pulse',
      style: {
        'underlay-color': theme.accent,
        'underlay-opacity': 0.5,
        'underlay-padding': 12,
        'underlay-shape': 'ellipse',
        'z-index': 70,
      },
    },

    /* Entrance staggering for freshly expanded nodes. */
    { selector: 'node.entering', style: { opacity: 0 } },
  ]

  // One rule per ontology type. Written once at build time, not per element.
  for (const type of ONTOLOGY) {
    sheet.push({
      selector: `node[label="${type.label}"]`,
      style: {
        'background-color': type.fill,
        'border-color': type.border,
      },
    })
  }

  return sheet
}
