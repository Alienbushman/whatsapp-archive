import React, { useEffect, useState, useCallback, useRef } from 'react'

const KIND_OPTIONS = ['All', 'company', 'ticker', 'person', 'other']
const SORT_OPTIONS = [
  { value: 'count', label: 'Most mentioned' },
  { value: 'recent', label: 'Recently active' },
  { value: 'sentiment', label: 'Most bullish' },
  { value: 'trending', label: 'Trending' },
]

function sentimentDot(s) {
  const color = s === 'bullish' ? '#16a34a' : s === 'bearish' ? '#dc2626' : '#6b7280'
  return <span className="entity-home-sentiment-dot" style={{ background: color }} title={s} />
}

function EntityCard({ entity, onOpenWorkspace, onAuthorClick }) {
  return (
    <div className="entity-home-card">
      <div className="entity-home-card-header">
        {sentimentDot(entity.sentiment_majority)}
        <button
          className="entity-home-card-name"
          onClick={() => onOpenWorkspace(entity.name)}
        >
          {entity.name}
        </button>
        <span className="entity-home-kind-badge">{entity.kind}</span>
        {entity.trend && (
          <span className={`entity-home-trend ${entity.trend.startsWith('+') ? 'trend-up' : 'trend-down'}`}>
            {entity.trend}
          </span>
        )}
      </div>

      <div className="entity-home-card-stats">
        <span>{entity.article_count} articles</span>
        {entity.last_seen && (
          <span>last {entity.last_seen.slice(0, 10)}</span>
        )}
        {entity.recent_count > 0 && (
          <span className="entity-home-recent">{entity.recent_count} in 30d</span>
        )}
      </div>

      {entity.top_authors?.length > 0 && (
        <div className="entity-home-authors">
          {entity.top_authors.map(a => (
            <button
              key={a.handle}
              className="entity-home-author-chip"
              onClick={() => onAuthorClick(a.handle)}
            >@{a.handle}</button>
          ))}
        </div>
      )}

      {entity.top_cooccurring_entities?.length > 0 && (
        <div className="entity-home-cooc">
          {entity.top_cooccurring_entities.map(e => (
            <button
              key={e.name}
              className="entity-home-cooc-chip"
              onClick={() => onOpenWorkspace(e.name)}
            >{e.name}</button>
          ))}
        </div>
      )}

      <div className="entity-home-card-footer">
        <button
          className="entity-home-workspace-btn"
          onClick={() => onOpenWorkspace(entity.name)}
        >
          Open workspace →
        </button>
        {entity.has_deepdive && (
          <span className="entity-home-deepdive-badge" title="Deep-dive available">📊</span>
        )}
      </div>
    </div>
  )
}

export default function EntityHomePage({ onOpenWorkspace, onAuthorClick, onShowStats }) {
  const [entities, setEntities] = useState([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [kind, setKind] = useState('All')
  const [sort, setSort] = useState('count')
  const [search, setSearch] = useState('')
  const [offset, setOffset] = useState(0)
  const PAGE_SIZE = 50
  const searchTimer = useRef(null)

  const load = useCallback(async (params = {}) => {
    setLoading(true)
    setError(null)
    const qs = new URLSearchParams({
      sort: params.sort ?? sort,
      limit: PAGE_SIZE,
      offset: params.offset ?? offset,
      ...(( params.kind ?? kind) !== 'All' ? { kind: params.kind ?? kind } : {}),
      ...(( params.search ?? search) ? { search: params.search ?? search } : {}),
    })
    try {
      const r = await fetch(`/api/home/entities?${qs}`)
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      const data = await r.json()
      setEntities(data.entities || [])
      setTotal(data.total || 0)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [sort, offset, kind, search])

  useEffect(() => { load() }, [load])

  function handleKindChange(k) {
    setKind(k)
    setOffset(0)
    load({ kind: k, offset: 0 })
  }

  function handleSortChange(s) {
    setSort(s)
    setOffset(0)
    load({ sort: s, offset: 0 })
  }

  function handleSearchInput(e) {
    const q = e.target.value
    setSearch(q)
    setOffset(0)
    clearTimeout(searchTimer.current)
    searchTimer.current = setTimeout(() => load({ search: q, offset: 0 }), 300)
  }

  const totalPages = Math.ceil(total / PAGE_SIZE)
  const page = Math.floor(offset / PAGE_SIZE) + 1

  return (
    <div className="entity-home-page">
      <div className="entity-home-topbar">
        <div className="entity-home-filter-row">
          <div className="entity-home-kind-tabs">
            {KIND_OPTIONS.map(k => (
              <button
                key={k}
                className={`entity-home-kind-tab${kind === k ? ' active' : ''}`}
                onClick={() => handleKindChange(k)}
              >{k === 'All' ? 'All' : k.charAt(0).toUpperCase() + k.slice(1)}</button>
            ))}
          </div>
          <select
            className="entity-home-sort-select"
            value={sort}
            onChange={e => handleSortChange(e.target.value)}
          >
            {SORT_OPTIONS.map(o => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
          <input
            className="entity-home-search"
            type="search"
            placeholder="Search entities…"
            value={search}
            onChange={handleSearchInput}
          />
        </div>
        <div className="entity-home-meta">
          <span className="entity-home-count">{total} entities</span>
          {onShowStats && (
            <button className="entity-home-stats-link" onClick={onShowStats}>
              Switch to date view →
            </button>
          )}
        </div>
      </div>

      {error && <div className="banner error entity-home-error">{error}</div>}

      {loading ? (
        <div className="entity-home-loading">Loading entities…</div>
      ) : entities.length === 0 ? (
        <div className="entity-home-empty text-muted">
          No entities found.{' '}
          {entities.length === 0 && total === 0 && (
            <span>Use the Entities admin panel to backfill from enrichments.</span>
          )}
        </div>
      ) : (
        <div className="entity-home-grid">
          {entities.map(e => (
            <EntityCard
              key={e.canonical_id}
              entity={e}
              onOpenWorkspace={onOpenWorkspace}
              onAuthorClick={onAuthorClick}
            />
          ))}
        </div>
      )}

      {totalPages > 1 && (
        <div className="entity-home-pagination">
          <button
            disabled={page <= 1}
            onClick={() => { const o = offset - PAGE_SIZE; setOffset(o); load({ offset: o }) }}
          >← Prev</button>
          <span>{page} / {totalPages}</span>
          <button
            disabled={page >= totalPages}
            onClick={() => { const o = offset + PAGE_SIZE; setOffset(o); load({ offset: o }) }}
          >Next →</button>
        </div>
      )}
    </div>
  )
}
