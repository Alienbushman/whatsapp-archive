import React, { useCallback, useEffect, useRef, useState } from 'react'
import { TweetCard } from './LinkPreview.jsx'

function EntityDetailPanel({ entityId, onMergeTarget, onClose, onOpenResearch }) {
  const [entity, setEntity] = useState(null)
  const [renaming, setRenaming] = useState(false)
  const [newName, setNewName] = useState('')
  const [saving, setSaving] = useState(false)
  const [articles, setArticles] = useState(null)
  const [artPage, setArtPage] = useState(1)
  const [artOrder, setArtOrder] = useState('date')

  useEffect(() => {
    if (!entityId) return
    setEntity(null); setArticles(null); setArtPage(1)
    fetch(`/api/entities/${entityId}`)
      .then(r => r.ok ? r.json() : null)
      .then(data => {
        setEntity(data)
        setNewName(data?.name || '')
      })
      .catch(() => {})
  }, [entityId])

  useEffect(() => {
    if (!entityId) return
    fetch(`/api/entities/${entityId}/articles?page=${artPage}&page_size=20&order=${artOrder}`)
      .then(r => r.ok ? r.json() : null)
      .then(setArticles)
      .catch(() => {})
  }, [entityId, artPage, artOrder])

  async function handleRename(e) {
    e.preventDefault()
    if (!newName.trim()) return
    setSaving(true)
    try {
      const r = await fetch(`/api/entities/${entityId}/rename`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: newName }),
      })
      if (r.ok) {
        const updated = await r.json()
        setEntity(updated)
        setRenaming(false)
      }
    } finally {
      setSaving(false)
    }
  }

  async function handleUnmerge(sourceId) {
    const r = await fetch(`/api/entities/${entityId}/unmerge`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ source_id: sourceId }),
    })
    if (r.ok) {
      setEntity(prev => ({
        ...prev,
        aliases: (prev?.aliases || []).filter(a => a.id !== sourceId),
      }))
    }
  }

  if (!entity) return <div className="entities-detail-empty"><span className="text-muted">Loading…</span></div>

  return (
    <div className="entities-detail-panel">
      <div className="entities-detail-header">
        {renaming ? (
          <form onSubmit={handleRename} className="entities-rename-form">
            <input
              className="entities-rename-input"
              value={newName}
              onChange={e => setNewName(e.target.value)}
              autoFocus
            />
            <button className="btn-primary" type="submit" disabled={saving}>Save</button>
            <button className="btn-secondary" type="button" onClick={() => setRenaming(false)}>Cancel</button>
          </form>
        ) : (
          <>
            <h3 className="entities-detail-name">{entity.name}</h3>
            <span className={`entity-kind-badge entity-kind-${entity.kind}`}>{entity.kind}</span>
            <button className="btn-ghost" onClick={() => setRenaming(true)}>✏ Rename</button>
            {onMergeTarget && (
              <button className="btn-ghost" onClick={() => onMergeTarget(entity.id, entity.name)}>
                → Merge into…
              </button>
            )}
            <a
              className="btn-ghost"
              href={`/api/export/entity/${encodeURIComponent(entity.name)}?format=md`}
              target="_blank"
              rel="noreferrer"
            >↓ Export</a>
          </>
        )}
      </div>
      <div className="entities-detail-meta">
        <span>{entity.article_count} article{entity.article_count !== 1 ? 's' : ''}</span>
        {entity.canonical_id && <span className="text-muted"> · alias of #{entity.canonical_id}</span>}
      </div>

      {entity.aliases && entity.aliases.length > 0 && (
        <div className="entities-aliases">
          <h4 className="entities-aliases-title">Aliases ({entity.aliases.length})</h4>
          {entity.aliases.map(a => (
            <div key={a.id} className="entities-alias-row">
              <span className="entities-alias-name">{a.name}</span>
              <span className={`entity-kind-badge entity-kind-${a.kind}`}>{a.kind}</span>
              <button className="btn-ghost entities-alias-unmerge" onClick={() => handleUnmerge(a.id)}>
                Unmerge
              </button>
            </div>
          ))}
        </div>
      )}

      {onOpenResearch && (
        <button
          className="btn-primary entities-research-btn"
          onClick={() => onOpenResearch(entity.name)}
        >
          Open research workspace →
        </button>
      )}

      <div className="entities-articles-section">
        <div className="entities-articles-header">
          <span className="entities-articles-title">Articles ({articles?.total ?? '…'})</span>
          <div className="entities-articles-sort">
            {['date', 'engagement'].map(o => (
              <button key={o}
                className={`kind-btn${artOrder === o ? ' active' : ''}`}
                onClick={() => { setArtOrder(o); setArtPage(1) }}>
                {o}
              </button>
            ))}
          </div>
        </div>

        {articles === null && <div className="loading">Loading articles…</div>}
        {articles?.articles?.length === 0 && (
          <p className="text-muted entities-articles-empty">
            No articles linked yet. Run entity backfill to populate.
          </p>
        )}

        <div className="entities-articles-list">
          {articles?.articles?.map((article, i) => (
            article.tweet
              ? <TweetCard key={article.id || i} tweet={article.tweet} sourceUrl={article.url} articleId={article.id} />
              : (
                <div key={article.id || i} className="entities-article-plain">
                  <a href={article.url} target="_blank" rel="noreferrer" className="entities-article-url">
                    {article.title || article.url}
                  </a>
                  {article.summary && <p className="entities-article-summary">{article.summary}</p>}
                </div>
              )
          ))}
        </div>

        {articles && articles.total > 20 && (
          <div className="pagination">
            <button onClick={() => setArtPage(p => Math.max(1, p - 1))} disabled={artPage <= 1}>← Prev</button>
            <span>Page {artPage} of {Math.ceil(articles.total / 20)}</span>
            <button onClick={() => setArtPage(p => p + 1)} disabled={artPage >= Math.ceil(articles.total / 20)}>Next →</button>
          </div>
        )}
      </div>
    </div>
  )
}

function MergeModal({ selectedIds, selectedNames, onConfirm, onCancel }) {
  const [targetSearch, setTargetSearch] = useState('')
  const [candidates, setCandidates] = useState([])
  const [targetId, setTargetId] = useState(null)

  useEffect(() => {
    if (!targetSearch.trim()) { setCandidates([]); return }
    fetch(`/api/entities?search=${encodeURIComponent(targetSearch)}&limit=20`)
      .then(r => r.ok ? r.json() : [])
      .then(setCandidates)
      .catch(() => {})
  }, [targetSearch])

  return (
    <div className="modal-overlay" onClick={e => e.target === e.currentTarget && onCancel()}>
      <div className="modal-box">
        <h3 className="modal-title">Merge {selectedIds.length} entities</h3>
        <p className="text-muted">
          Sources: <em>{selectedNames.slice(0, 3).join(', ')}{selectedNames.length > 3 ? ` +${selectedNames.length - 3} more` : ''}</em>
        </p>
        <p>Search for target entity:</p>
        <input
          className="entities-rename-input"
          placeholder="Target entity name…"
          value={targetSearch}
          onChange={e => setTargetSearch(e.target.value)}
          autoFocus
        />
        {candidates.length > 0 && (
          <ul className="entities-merge-candidates">
            {candidates.filter(c => !selectedIds.includes(c.id)).map(c => (
              <li key={c.id}>
                <button
                  className={`entities-candidate-btn${targetId === c.id ? ' selected' : ''}`}
                  onClick={() => setTargetId(c.id)}
                >
                  {c.name} <span className={`entity-kind-badge entity-kind-${c.kind}`}>{c.kind}</span>
                  <span className="text-muted"> ({c.article_count})</span>
                </button>
              </li>
            ))}
          </ul>
        )}
        <div className="modal-actions">
          <button className="btn-primary" disabled={!targetId} onClick={() => onConfirm(targetId)}>
            Merge
          </button>
          <button className="btn-secondary" onClick={onCancel}>Cancel</button>
        </div>
      </div>
    </div>
  )
}

export default function EntitiesPage({ onOpenResearch }) {
  const [entities, setEntities] = useState(null)
  const [search, setSearch] = useState('')
  const [kindFilter, setKindFilter] = useState('')
  const [order, setOrder] = useState('count')
  const [showAliases, setShowAliases] = useState(false)
  const [selectedIds, setSelectedIds] = useState(new Set())
  const [detailId, setDetailId] = useState(null)
  const [showMergeModal, setShowMergeModal] = useState(false)
  const [merging, setMerging] = useState(false)
  const [backfilling, setBackfilling] = useState(false)
  const [backfillMsg, setBackfillMsg] = useState('')
  const searchTimer = useRef(null)

  const loadEntities = useCallback(() => {
    const params = new URLSearchParams({ limit: '200', order })
    if (search) params.set('search', search)
    if (kindFilter) params.set('kind', kindFilter)
    if (showAliases) params.set('include_aliases', 'true')
    fetch(`/api/entities?${params}`)
      .then(r => r.ok ? r.json() : [])
      .then(setEntities)
      .catch(() => setEntities([]))
  }, [search, kindFilter, order, showAliases])

  useEffect(() => { loadEntities() }, [loadEntities])

  function handleSearchChange(e) {
    const v = e.target.value
    clearTimeout(searchTimer.current)
    searchTimer.current = setTimeout(() => setSearch(v), 300)
  }

  function toggleSelect(id) {
    setSelectedIds(prev => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }

  async function handleBackfill() {
    setBackfilling(true)
    setBackfillMsg('')
    try {
      const r = await fetch('/api/entities/backfill', { method: 'POST' })
      const d = await r.json()
      setBackfillMsg(`Backfilled: ${d.inserted} new links`)
      loadEntities()
    } finally {
      setBackfilling(false)
    }
  }

  async function handleMerge(targetId) {
    setMerging(true)
    try {
      const r = await fetch('/api/entities/merge', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ source_ids: [...selectedIds], target_id: targetId }),
      })
      if (r.ok) {
        setSelectedIds(new Set())
        setShowMergeModal(false)
        loadEntities()
        if (selectedIds.has(detailId)) setDetailId(targetId)
      }
    } finally {
      setMerging(false)
    }
  }

  const selectedNames = entities
    ? [...selectedIds].map(id => entities.find(e => e.id === id)?.name).filter(Boolean)
    : []

  return (
    <div className="entities-page">
      <div className="entities-sidebar">
        <h2 className="entities-title">⚙ Entities</h2>

        <div className="entities-controls">
          <input
            className="entities-search"
            type="search"
            placeholder="Search entities…"
            onChange={handleSearchChange}
          />
          <select className="entities-kind-filter" value={kindFilter} onChange={e => setKindFilter(e.target.value)}>
            <option value="">All kinds</option>
            <option value="company">Company</option>
            <option value="ticker">Ticker</option>
            <option value="person">Person</option>
            <option value="other">Other</option>
          </select>
          <select className="entities-order" value={order} onChange={e => setOrder(e.target.value)}>
            <option value="count">By count</option>
            <option value="name">By name</option>
          </select>
        </div>

        <div className="entities-toolbar">
          {selectedIds.size > 0 && (
            <button className="btn-primary" onClick={() => setShowMergeModal(true)}>
              Merge {selectedIds.size} selected…
            </button>
          )}
          <button className="btn-ghost" onClick={handleBackfill} disabled={backfilling}>
            {backfilling ? 'Backfilling…' : '↩ Backfill from enrichments'}
          </button>
          <label className="entities-aliases-toggle" title="Show entities that have been merged into another">
            <input
              type="checkbox"
              checked={showAliases}
              onChange={e => setShowAliases(e.target.checked)}
            />
            {' '}Show merged aliases
          </label>
          {backfillMsg && <span className="text-muted" style={{ fontSize: '0.8rem' }}>{backfillMsg}</span>}
        </div>

        {entities === null && <div className="loading">Loading…</div>}
        {entities && entities.length === 0 && (
          <p className="text-muted">No entities found. Try backfilling from enrichments first.</p>
        )}

        {entities && entities.length > 0 && (
          <table className="entities-table">
            <thead>
              <tr>
                <th></th>
                <th>Name</th>
                <th>Kind</th>
                <th>#</th>
              </tr>
            </thead>
            <tbody>
              {entities.map(e => (
                <tr
                  key={e.id}
                  className={`entities-row${detailId === e.id ? ' active' : ''}${e.canonical_id ? ' entities-row-alias' : ''}`}
                  onClick={() => setDetailId(e.id)}
                >
                  <td onClick={ev => ev.stopPropagation()}>
                    <input
                      type="checkbox"
                      checked={selectedIds.has(e.id)}
                      onChange={() => toggleSelect(e.id)}
                    />
                  </td>
                  <td className="entities-row-name">
                    {e.name}
                    {e.canonical_id && <span className="entities-alias-badge" title={`Alias of entity #${e.canonical_id}`}> → alias</span>}
                  </td>
                  <td><span className={`entity-kind-badge entity-kind-${e.kind}`}>{e.kind}</span></td>
                  <td className="entities-row-count">{e.article_count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="entities-content">
        {detailId ? (
          <EntityDetailPanel
            entityId={detailId}
            onMergeTarget={(id, name) => {
              setSelectedIds(prev => new Set([...prev, id]))
              setShowMergeModal(true)
            }}
            onClose={() => setDetailId(null)}
            onOpenResearch={onOpenResearch}
          />
        ) : (
          <div className="entities-empty">
            <p className="text-muted">Select an entity to view details, rename, or manage aliases.</p>
          </div>
        )}
      </div>

      {showMergeModal && (
        <MergeModal
          selectedIds={[...selectedIds]}
          selectedNames={selectedNames}
          onConfirm={handleMerge}
          onCancel={() => setShowMergeModal(false)}
        />
      )}
    </div>
  )
}
