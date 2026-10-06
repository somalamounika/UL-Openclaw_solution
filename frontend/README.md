# UL Atlas — frontend

**Every compliance claim, traced back to the page it came from.**

React 18 + TypeScript (strict) UI over the FastAPI + Neo4j compliance pipeline in
`../backend`.

## Running

```bash
npm install
npm run dev        # http://localhost:5173
```

The dev server proxies `/api` and `/health` to `http://localhost:8000`, so start the
backend first:

```bash
cd ../backend && .venv/bin/uvicorn main:app --reload
```

`VITE_API_BASE_URL` only matters when a built bundle is served from a different origin
than the API. In dev, leave it unset.

```bash
npm run build      # tsc -b && vite build
npm run lint       # tsc --noEmit
```

## The three pages

Client-side routing (`react-router-dom`); switching pages never reloads the app, and the
loaded graph, the conversation and all focus state survive navigation.

| Route | Page | Purpose |
|---|---|---|
| `/` | Upload & Process | Upload PDFs, set a target product, build the graph, manage saved graphs |
| `/assistant` | AI Assistant | Full-page grounded chat; relationship answers render as lineage |
| `/graph` | Knowledge Graph | Full-screen Neo4j-style exploration |

Assistant and Knowledge Graph stay locked until a graph exists.

## Two focus modes — deliberately distinct

**Node Focus** (double-click a node, `F`, or the inspector's *Focus node*) *isolates*:
unrelated nodes and edges are removed from the visible set, not dimmed. A depth control
sets the BFS radius (1–4 hops). Double-clicking the focused node again, `Esc`, or *Show
Full Graph* restores the previous graph and camera.

**Evidence Focus** (*Show on Knowledge Graph* in a chat answer) highlights the subgraph an
answer reasoned over: cited nodes halo, cited edges animate, everything else drops to 8%.
The banner reports exact counts and says so when some cited nodes are not in this graph.

**Expand** is a third, separate thing: it *reveals* more of the graph around a node
(additive). Focus removes; Expand adds.

## Things worth knowing before changing this code

- **`/process` reports no progress.** One blocking POST, minutes long, no SSE or job
  endpoint. The 0→100 number is an elapsed-time estimate over a weighted 8-stage model
  (`hooks/useProcessingProgress.ts`), capped at 95% until the real response lands, and
  labelled as an estimate everywhere. Real counts replace the estimates on completion.
- **There is no node-expansion endpoint.** `/graph` returns everything (server caps:
  2000 nodes / 4000 edges, no pagination). Expansion, focus and hop distance are pure
  client-side set operations over the adjacency index built once in `lib/graphAdapter.ts`.
- **Chat is not streamed.** The thinking state names the backend's real phases rather
  than faking a typewriter.
- **`warnings[]` is routinely non-empty on success.** Surface it; never treat it as failure.
- **`neo4j_ingested: false` means the graph will be empty** and gets its own message.
- **Reference PDFs** (`standards.pdf`, `certifications_tests.pdf`) are always indexed for
  the assistant but never used as graph-extraction evidence — which is why "2 files
  uploaded, 4 documents indexed" is correct. The UI says so explicitly.
- **There is one global UL Knowledge Graph.** New documents are merged into it by canonical entity identity. `graph_id` is provenance, not a separate graph.
- **Lineage is never invented.** `lib/lineage.ts` only orders and groups the hops the
  backend returned in `graph_evidence`, using its own relationship names.

## Layout

```
src/
  pages/          UploadPage  AssistantPage  KnowledgeGraphPage
  components/
    navigation/   AppHeader  PageNavigation  GraphStatus
    upload/       UploadDropzone  UploadedFiles  TargetProduct  ReferenceDocs
                  ProcessingPage  ProcessingComplete  SavedGraphs
    assistant/    ChatPanel  MessageList  MessageBubble  ThinkingState
                  LineageView  LineageNode  LineageEdge
                  DocumentEvidence  GraphEvidence  ShowOnGraphButton  Composer
    graph/        GraphCanvas  GraphToolbar  GraphSearch  GraphLegend  Minimap
                  NodeInspector  NodeFocusMode  EvidenceFocusMode  FocusBreadcrumb
                  FocusDepthControl  NodeBadgeLayer  ContextMenu  GraphEmptyState
    ui/           Radix wrappers
  lib/            ontology  graphAdapter  adjacency  focus  lineage  layout  lod
                  elements  cytoscapeStyle  fisheye  canvasApi  referenceDocs  utils
  state/          graphStore  chatStore  processingStore
  hooks/          useGraphData  useChat*  useProcessingProgress  useKeyboard  useTheme
  api/            client  documents  processing  graph  chat
  types/          api  graph
```

\* chat and processing state live in stores rather than hooks so they survive navigation.

`lib/ontology.ts` mirrors `backend/ul/hierarchy.py::HIERARCHY_LEVEL` verbatim — the 25
node types and their tiers. That table is the semantic-zoom ladder and the colour system;
change it there and nowhere else. Unknown labels fall back to a neutral `Entity` rather
than crashing.

`GraphCanvas` owns the Cytoscape instance in a ref and never re-creates it on state
change: React state decides *what* is visible, Cytoscape owns *where* things are.

## Keyboard

`/` search · `F` focus selection · `E` expand · `C` collapse · `Esc` unwind one mode
(evidence → node focus → selection) · `0` fit · `+` / `-` zoom · `Space` (hold) fisheye
lens · `?` shortcuts.
