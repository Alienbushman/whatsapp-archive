import React, { useCallback, useEffect, useRef, useState } from 'react'

const WINDOWS = [
  { value: 'all', label: 'All time' },
  { value: '365d', label: '1 year' },
  { value: '180d', label: '6 months' },
  { value: '90d', label: '3 months' },
]
const KIND_COLOR = {
  company: '#6366f1',
  ticker: '#10b981',
  person: '#f59e0b',
  hashtag: '#3b82f6',
  mention: '#8b5cf6',
  other: '#6b7280',
}

// Minimal Fruchterman-Reingold layout
function forceLayout(nodes, edges, { width = 600, height = 400, iters = 80 } = {}) {
  const n = nodes.length
  if (n === 0) return []
  const area = width * height
  const k = Math.sqrt(area / n)
  const repulse = (d) => (k * k) / d
  const attract = (d) => (d * d) / k

  const pos = nodes.map((_, i) => ({
    x: width / 2 + (Math.random() - 0.5) * width * 0.6,
    y: height / 2 + (Math.random() - 0.5) * height * 0.6,
    dx: 0,
    dy: 0,
  }))
  const idIdx = Object.fromEntries(nodes.map((n, i) => [n.id, i]))

  for (let iter = 0; iter < iters; iter++) {
    const temp = (width / 10) * (1 - iter / iters)
    // Reset displacement
    for (const p of pos) { p.dx = 0; p.dy = 0 }
    // Repulsion
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        let dx = pos[i].x - pos[j].x
        let dy = pos[i].y - pos[j].y
        let dist = Math.sqrt(dx * dx + dy * dy) || 0.01
        const f = repulse(dist) / dist
        pos[i].dx += dx * f; pos[i].dy += dy * f
        pos[j].dx -= dx * f; pos[j].dy -= dy * f
      }
    }
    // Attraction
    for (const e of edges) {
      const si = idIdx[e.source], ti = idIdx[e.target]
      if (si === undefined || ti === undefined) continue
      let dx = pos[si].x - pos[ti].x
      let dy = pos[si].y - pos[ti].y
      let dist = Math.sqrt(dx * dx + dy * dy) || 0.01
      const f = attract(dist) / dist
      pos[si].dx -= dx * f; pos[si].dy -= dy * f
      pos[ti].dx += dx * f; pos[ti].dy += dy * f
    }
    // Clamp and apply
    for (const p of pos) {
      const dist = Math.sqrt(p.dx * p.dx + p.dy * p.dy) || 0.01
      p.x += (p.dx / dist) * Math.min(Math.abs(p.dx), temp)
      p.y += (p.dy / dist) * Math.min(Math.abs(p.dy), temp)
      p.x = Math.max(30, Math.min(width - 30, p.x))
      p.y = Math.max(20, Math.min(height - 20, p.y))
    }
  }
  return pos
}

function useGraphData(seed, window, minEdge) {
  const [graph, setGraph] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    if (!seed) return
    setLoading(true)
    setError(null)
    const params = new URLSearchParams({ seed, window, min_edge_weight: minEdge, depth: 2 })
    fetch(`/api/research/graph?${params}`)
      .then(r => { if (!r.ok) throw new Error(r.statusText); return r.json() })
      .then(setGraph)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [seed, window, minEdge])

  return { graph, loading, error }
}

export default function NarrativeGraphView({ entityName, onEntityClick }) {
  const [window, setWindow] = useState('all')
  const [minEdge, setMinEdge] = useState(2)
  const [tooltip, setTooltip] = useState(null) // {x, y, edge}
  const { graph, loading, error } = useGraphData(entityName, window, minEdge)
  const svgRef = useRef(null)

  const W = 640, H = 420

  const layout = React.useMemo(() => {
    if (!graph?.nodes?.length) return []
    return forceLayout(graph.nodes, graph.edges || [], { width: W, height: H })
  }, [graph])

  const idIdx = React.useMemo(() => {
    if (!graph?.nodes) return {}
    return Object.fromEntries(graph.nodes.map((n, i) => [n.id, i]))
  }, [graph])

  function nodeRadius(size) {
    return Math.max(8, Math.min(22, 6 + Math.sqrt(size) * 1.5))
  }

  return (
    <div className="ngraph-wrap">
      <div className="ngraph-toolbar">
        <div className="ngraph-window-tabs">
          {WINDOWS.map(w => (
            <button
              key={w.value}
              className={`ngraph-window-btn${window === w.value ? ' active' : ''}`}
              onClick={() => setWindow(w.value)}
            >{w.label}</button>
          ))}
        </div>
        <label className="ngraph-edge-label">
          Min co-occurrences:
          <input
            type="number" min={1} max={20} value={minEdge}
            className="ngraph-edge-input"
            onChange={e => setMinEdge(Math.max(1, Number(e.target.value)))}
          />
        </label>
      </div>

      {loading && <div className="ngraph-loading">Loading graph…</div>}
      {error && <div className="ngraph-error">Error: {error}</div>}
      {!loading && !error && graph && graph.nodes.length === 0 && (
        <div className="ngraph-empty">No entity connections found. Try lowering min co-occurrences or widening the time window.</div>
      )}

      {!loading && !error && graph && graph.nodes.length > 0 && (
        <div className="ngraph-svg-wrap">
          <svg
            ref={svgRef}
            className="ngraph-svg"
            viewBox={`0 0 ${W} ${H}`}
            width={W} height={H}
          >
            {/* Edges */}
            {(graph.edges || []).map((edge, i) => {
              const si = idIdx[edge.source], ti = idIdx[edge.target]
              if (si === undefined || ti === undefined) return null
              const sx = layout[si]?.x, sy = layout[si]?.y
              const tx = layout[ti]?.x, ty = layout[ti]?.y
              if (!sx || !tx) return null
              const mx = (sx + tx) / 2, my = (sy + ty) / 2
              const strokeW = Math.max(1, Math.min(6, edge.weight / 2))
              return (
                <g key={i}>
                  <line
                    x1={sx} y1={sy} x2={tx} y2={ty}
                    stroke="var(--border)" strokeWidth={strokeW}
                    strokeOpacity={0.5}
                  />
                  <circle
                    cx={mx} cy={my} r={8} fill="transparent"
                    className="ngraph-edge-hover"
                    onMouseEnter={e => setTooltip({ x: e.clientX, y: e.clientY, edge })}
                    onMouseLeave={() => setTooltip(null)}
                  />
                  <text x={mx} y={my - 4} textAnchor="middle" className="ngraph-edge-label-svg">
                    {edge.weight}
                  </text>
                </g>
              )
            })}
            {/* Nodes */}
            {graph.nodes.map((node, i) => {
              const p = layout[i]
              if (!p) return null
              const r = nodeRadius(node.size)
              const color = KIND_COLOR[node.kind] || KIND_COLOR.other
              const isSeed = node.id === graph.seed
              return (
                <g
                  key={node.id}
                  className="ngraph-node"
                  onClick={() => onEntityClick && onEntityClick('entity', node.id)}
                  transform={`translate(${p.x},${p.y})`}
                >
                  <circle
                    r={r}
                    fill={color}
                    fillOpacity={0.85}
                    stroke={isSeed ? '#fff' : color}
                    strokeWidth={isSeed ? 2.5 : 1}
                  />
                  <text
                    dy={r + 10}
                    textAnchor="middle"
                    className="ngraph-node-label"
                  >{node.id}</text>
                </g>
              )
            })}
          </svg>

          <div className="ngraph-legend">
            {Object.entries(KIND_COLOR).filter(([k]) => k !== 'other').map(([kind, color]) => (
              <span key={kind} className="ngraph-legend-item">
                <span className="ngraph-legend-dot" style={{ background: color }} />
                {kind}
              </span>
            ))}
          </div>
        </div>
      )}

      {tooltip && (
        <div
          className="ngraph-tooltip"
          style={{ left: tooltip.x + 12, top: tooltip.y - 8 }}
        >
          <div className="ngraph-tooltip-title">
            {tooltip.edge.source} ↔ {tooltip.edge.target} · {tooltip.edge.weight}×
          </div>
          {(tooltip.edge.supporting_articles || []).slice(0, 3).map((a, i) => (
            <div key={i} className="ngraph-tooltip-article">
              {a.title || a.url}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
