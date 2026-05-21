import React, { useEffect, useState } from 'react'

// Polls the four background-pipeline status endpoints every 3s while ANY phase is
// active and renders a slim progress bar at the top of the app. Auto-hides when
// all four are idle/done. Click to toggle a panel showing per-phase counts.

const PHASES = [
  { key: 'scrape',  label: 'Scraping links',     endpoint: '/api/scrape/status',  color: '#3b82f6' },
  { key: 'enrich',  label: 'Enriching articles', endpoint: '/api/enrich/status',  color: '#8b5cf6' },
  { key: 'ocr',     label: 'OCR on images',      endpoint: '/api/ocr/status',     color: '#f59e0b' },
  { key: 'topics',  label: 'Clustering topics',  endpoint: '/api/topics/status',  color: '#10b981' },
]

function isActive(s) {
  if (!s) return false
  const phase = (s.phase || '').toLowerCase()
  if (phase === 'running' || phase === 'pending' || phase === 'waiting_for_model') return true
  return false
}

function ratio(s) {
  if (!s || !s.total) return 0
  return Math.min(1, (s.done || 0) / s.total)
}

export default function PipelineProgressBar() {
  const [statuses, setStatuses] = useState({})
  const [expanded, setExpanded] = useState(false)

  useEffect(() => {
    let cancelled = false
    async function poll() {
      const next = {}
      await Promise.all(PHASES.map(async (p) => {
        try {
          const r = await fetch(p.endpoint)
          if (r.ok) next[p.key] = await r.json()
        } catch { /* ignore */ }
      }))
      if (!cancelled) setStatuses(next)
    }
    poll()
    // 3s when active, 10s when idle — re-evaluated on each tick.
    let timer = null
    function tick() {
      poll().then(() => {
        if (cancelled) return
        const anyActive = PHASES.some(p => isActive(statuses[p.key]))
        timer = setTimeout(tick, anyActive ? 3000 : 10000)
      })
    }
    timer = setTimeout(tick, 3000)
    return () => { cancelled = true; if (timer) clearTimeout(timer) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const activePhases = PHASES.filter(p => isActive(statuses[p.key]))
  if (activePhases.length === 0) return null

  // Pick the phase with the most work to show in the headline bar.
  const headline = activePhases.reduce((best, p) => {
    const s = statuses[p.key]
    const t = s?.total || 0
    return t > (statuses[best.key]?.total || 0) ? p : best
  }, activePhases[0])
  const hs = statuses[headline.key] || {}
  const pct = Math.round(ratio(hs) * 100)
  const label = `${headline.label}: ${hs.done || 0}/${hs.total || 0} (${pct}%)`

  return (
    <div className="pipeline-progress-wrap">
      <button
        className="pipeline-progress-bar"
        onClick={() => setExpanded(e => !e)}
        title="Click to see all pipeline phases"
        style={{ '--progress-color': headline.color }}
      >
        <div
          className="pipeline-progress-fill"
          style={{ width: `${pct}%`, backgroundColor: headline.color }}
        />
        <span className="pipeline-progress-label">
          {activePhases.length > 1 ? `${label} · +${activePhases.length - 1} more` : label}
          <span className="pipeline-progress-chevron">{expanded ? '▴' : '▾'}</span>
        </span>
      </button>
      {expanded && (
        <div className="pipeline-progress-detail">
          {PHASES.map(p => {
            const s = statuses[p.key]
            const active = isActive(s)
            const pctP = Math.round(ratio(s) * 100)
            return (
              <div key={p.key} className={`pipeline-row${active ? ' active' : ''}`}>
                <span className="pipeline-row-dot" style={{ backgroundColor: p.color }} />
                <span className="pipeline-row-label">{p.label}</span>
                <span className="pipeline-row-stats">
                  {active
                    ? `${s.done || 0}/${s.total || 0} (${pctP}%)`
                    : (s?.phase || 'idle')}
                  {s?.failed ? ` · ${s.failed} failed` : ''}
                </span>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
