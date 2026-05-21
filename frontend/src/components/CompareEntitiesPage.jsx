import React, { useEffect, useState, useRef } from 'react'

function SentimentBar({ sentiment }) {
  if (!sentiment) return null
  const total = (sentiment.bullish || 0) + (sentiment.bearish || 0) + (sentiment.neutral || 0)
  if (!total) return null
  const bPct = Math.round(100 * (sentiment.bullish || 0) / total)
  const brPct = Math.round(100 * (sentiment.bearish || 0) / total)
  return (
    <div className="compare-sentiment-bar" title={`bullish ${bPct}% / neutral ${100-bPct-brPct}% / bearish ${brPct}%`}>
      <div style={{ width: `${bPct}%`, background: '#16a34a', height: '100%' }} />
      <div style={{ width: `${100-bPct-brPct}%`, background: '#9ca3af', height: '100%' }} />
      <div style={{ width: `${brPct}%`, background: '#dc2626', height: '100%' }} />
    </div>
  )
}

function MiniArc({ arc }) {
  if (!arc || arc.length === 0) return <div className="compare-arc-empty">No timeline data</div>
  const max = Math.max(...arc.map(m => m.article_count), 1)
  return (
    <div className="compare-arc">
      {arc.map(m => (
        <div key={m.month} className="compare-arc-month">
          <div
            className="compare-arc-bar"
            style={{ height: `${Math.round(60 * m.article_count / max)}px` }}
            title={`${m.month}: ${m.article_count} articles`}
          />
          <SentimentBar sentiment={m.sentiment} />
          <div className="compare-arc-label">{m.month.slice(5)}</div>
        </div>
      ))}
    </div>
  )
}

function EntityColumn({ dossier, onOpenWorkspace }) {
  const { entity, stats, narrative_arc, claim_buckets } = dossier
  return (
    <div className="compare-entity-col">
      <div className="compare-entity-header">
        <button className="compare-entity-name" onClick={() => onOpenWorkspace(entity.name)}>
          {entity.name}
        </button>
        <span className="entity-home-kind-badge">{entity.kind}</span>
      </div>
      <div className="compare-entity-stats">
        <span>{stats.article_count} articles</span>
        {stats.last_seen && <span>last {stats.last_seen.slice(0, 10)}</span>}
        {stats.total_engagement > 0 && <span>{stats.total_engagement} eng.</span>}
      </div>
      <MiniArc arc={narrative_arc} />
      <div className="compare-buckets">
        {(claim_buckets || []).slice(0, 3).map((b, i) => (
          <div key={i} className="compare-bucket-chip">
            <span className="compare-bucket-theme">{b.theme}</span>
            <span className="compare-bucket-count">{b.article_ids?.length || 0}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

function DivergencePanel({ points }) {
  if (!points || points.length === 0) return null
  return (
    <div className="compare-section">
      <h3 className="compare-section-title">Divergence points</h3>
      <div className="compare-divergence-list">
        {points.map((p, i) => (
          <div key={i} className={`compare-divergence-item type-${p.type}`}>
            <span className="compare-div-month">{p.month}</span>
            <span className={`compare-div-badge badge-${p.type}`}>
              {p.type === 'sentiment_flip' ? 'sentiment' : 'volume'}
            </span>
            <span className="compare-div-narrative">{p.narrative}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

function ThemesPanel({ shared, unique }) {
  return (
    <>
      {shared && shared.length > 0 && (
        <div className="compare-section">
          <h3 className="compare-section-title">Shared themes</h3>
          <div className="compare-themes-grid">
            {shared.map((t, i) => (
              <div key={i} className="compare-theme-card shared">
                <div className="compare-theme-name">{t.theme}</div>
                <div className="compare-theme-entities">
                  {t.entities_present.map(e => (
                    <span key={e} className="compare-theme-entity-chip">{e}</span>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
      {unique && unique.length > 0 && (
        <div className="compare-section">
          <h3 className="compare-section-title">Unique themes</h3>
          <div className="compare-themes-grid">
            {unique.map((t, i) => (
              <div key={i} className="compare-theme-card unique">
                <div className="compare-theme-name">{t.theme}</div>
                <div className="compare-theme-entity-chip">{t.entity}</div>
                <span className="compare-theme-count">{t.article_count} art.</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </>
  )
}

export default function CompareEntitiesPage({ initialEntities = [], onOpenWorkspace }) {
  const [entities, setEntities] = useState(initialEntities)
  const [searchInput, setSearchInput] = useState('')
  const [suggestions, setSuggestions] = useState([])
  const [result, setResult] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const suggestTimer = useRef(null)

  useEffect(() => {
    if (entities.length >= 2) fetchCompare()
  }, [entities]) // eslint-disable-line react-hooks/exhaustive-deps

  async function fetchCompare() {
    setLoading(true)
    setError(null)
    const qs = entities.map(e => `entities=${encodeURIComponent(e)}`).join('&')
    try {
      const r = await fetch(`/api/research/compare?${qs}`)
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      setResult(await r.json())
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  function handleSearchInput(e) {
    const q = e.target.value
    setSearchInput(q)
    clearTimeout(suggestTimer.current)
    if (!q.trim()) { setSuggestions([]); return }
    suggestTimer.current = setTimeout(async () => {
      try {
        const r = await fetch(`/api/home/entities?search=${encodeURIComponent(q)}&limit=8`)
        if (r.ok) {
          const data = await r.json()
          setSuggestions((data.entities || []).map(e => e.name).filter(n => !entities.includes(n)))
        }
      } catch {}
    }, 250)
  }

  function addEntity(name) {
    if (!entities.includes(name) && entities.length < 4) {
      setEntities(prev => [...prev, name])
    }
    setSearchInput('')
    setSuggestions([])
  }

  function removeEntity(name) {
    setEntities(prev => prev.filter(e => e !== name))
    setResult(null)
  }

  return (
    <div className="compare-page">
      <div className="compare-topbar">
        <h2 className="compare-title">Compare entities</h2>
        <div className="compare-tag-row">
          {entities.map(e => (
            <span key={e} className="compare-entity-tag">
              {e}
              <button className="compare-tag-remove" onClick={() => removeEntity(e)}>×</button>
            </span>
          ))}
          {entities.length < 4 && (
            <div className="compare-search-wrap">
              <input
                className="compare-entity-search"
                type="search"
                placeholder="Add entity…"
                value={searchInput}
                onChange={handleSearchInput}
                onKeyDown={e => {
                  if (e.key === 'Enter' && searchInput.trim()) addEntity(searchInput.trim())
                }}
              />
              {suggestions.length > 0 && (
                <div className="compare-suggestions">
                  {suggestions.map(s => (
                    <button key={s} className="compare-suggestion-item" onClick={() => addEntity(s)}>
                      {s}
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
        {entities.length < 2 && (
          <p className="compare-hint text-muted">Add at least 2 entities to compare.</p>
        )}
      </div>

      {error && <div className="banner error">{error}</div>}

      {loading && <div className="entity-home-loading">Loading comparison…</div>}

      {result && !loading && (
        <>
          <div className="compare-columns">
            {result.entities.map(d => (
              <EntityColumn
                key={d.entity.name}
                dossier={d}
                onOpenWorkspace={onOpenWorkspace}
              />
            ))}
          </div>
          <DivergencePanel points={result.divergence_points} />
          <ThemesPanel shared={result.shared_themes} unique={result.unique_themes} />
          {result.not_found && result.not_found.length > 0 && (
            <div className="banner warning">
              Not found: {result.not_found.join(', ')} — check entity names or build dossiers first.
            </div>
          )}
        </>
      )}
    </div>
  )
}
