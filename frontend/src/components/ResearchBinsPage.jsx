import React, { useEffect, useState, useCallback } from 'react'
import { TweetCard } from './LinkPreview.jsx'
import { useCollectionsContext } from './CollectionsContext.jsx'

function daysUntil(iso) {
  if (!iso) return null
  const diff = new Date(iso) - new Date()
  return Math.ceil(diff / (1000 * 60 * 60 * 24))
}

function ExpiryBadge({ expiresAt }) {
  const days = daysUntil(expiresAt)
  if (days === null) return null
  if (days <= 0) return <span className="rb-expiry expired">expired</span>
  if (days <= 2) return <span className="rb-expiry soon">expires in {days}d</span>
  return <span className="rb-expiry">{days}d left</span>
}

function NewBinModal({ onClose, onCreated, initialQuery = '' }) {
  const [name, setName] = useState('')
  const [hypothesis, setHypothesis] = useState('')
  const [seedQuery, setSeedQuery] = useState(initialQuery)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  async function handleSubmit(e) {
    e.preventDefault()
    if (!name.trim()) return
    setSaving(true)
    setError('')
    try {
      const r = await fetch('/api/research_bins', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: name.trim(),
          hypothesis: hypothesis.trim() || null,
          seed_query: seedQuery.trim() || null,
        }),
      })
      if (!r.ok) { setError('Failed to create'); return }
      const bin = await r.json()
      onCreated(bin)
    } catch (err) {
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="modal-backdrop" onClick={e => { if (e.target === e.currentTarget) onClose() }}>
      <div className="modal">
        <div className="modal-header">
          <h2>New research bin</h2>
          <button className="link-preview-close" onClick={onClose}>✕</button>
        </div>
        <form onSubmit={handleSubmit}>
          <div className="modal-body">
            <p className="text-muted" style={{ fontSize: '0.82rem', marginBottom: 14 }}>
              Research bins are temporary scratchpads tied to a search session.
              Pin interesting items, riff with the chatbot, then promote to a Collection or discard.
            </p>
            <label className="form-label">
              Name (required)
              <input className="form-input" value={name} onChange={e => setName(e.target.value)} placeholder="e.g. Vista Gold thesis" autoFocus />
            </label>
            <label className="form-label">
              Hypothesis / question (optional)
              <textarea className="form-input" rows={3} value={hypothesis} onChange={e => setHypothesis(e.target.value)} placeholder="What are you investigating? e.g. Is Vista Gold undervalued relative to peers?" />
            </label>
            <label className="form-label">
              Seed query (optional — re-runs live when you open the bin)
              <input className="form-input" value={seedQuery} onChange={e => setSeedQuery(e.target.value)} placeholder="e.g. gold supercycle" />
            </label>
            {error && <div className="banner error">{error}</div>}
          </div>
          <div className="modal-footer">
            <button type="button" className="btn-secondary" onClick={onClose}>Cancel</button>
            <button type="submit" className="btn-primary" disabled={saving || !name.trim()}>
              {saving ? 'Creating…' : 'Create bin'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

function BinDetail({ bin, onBack, onPromoted, onDeleted }) {
  const [detail, setDetail] = useState(null)
  const [loading, setLoading] = useState(true)
  const [editingHyp, setEditingHyp] = useState(false)
  const [hypDraft, setHypDraft] = useState('')
  const [promoting, setPromoting] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const collectionsCtx = useCollectionsContext()

  const load = useCallback(() => {
    setLoading(true)
    fetch(`/api/research_bins/${bin.id}`)
      .then(r => r.ok ? r.json() : null)
      .then(data => { setDetail(data); setLoading(false) })
      .catch(() => setLoading(false))
  }, [bin.id])

  useEffect(() => { load() }, [load])

  async function handleSaveHyp() {
    await fetch(`/api/research_bins/${bin.id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ hypothesis: hypDraft }),
    })
    setDetail(prev => ({ ...prev, hypothesis: hypDraft }))
    setEditingHyp(false)
  }

  async function handlePin(hit) {
    await fetch(`/api/research_bins/${bin.id}/items`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ target_kind: 'article', target_id: String(hit.id) }),
    })
    load()
  }

  async function handleUnpin(itemId) {
    await fetch(`/api/research_bins/${bin.id}/items/${itemId}`, { method: 'DELETE' })
    load()
  }

  async function handlePromote() {
    setPromoting(true)
    const r = await fetch(`/api/research_bins/${bin.id}/promote`, { method: 'POST' })
    if (r.ok) {
      const col = await r.json()
      collectionsCtx?.refresh()
      onPromoted?.(col)
    }
    setPromoting(false)
  }

  async function handleDelete() {
    if (!confirm(`Delete "${bin.name}"? This cannot be undone.`)) return
    setDeleting(true)
    await fetch(`/api/research_bins/${bin.id}`, { method: 'DELETE' })
    onDeleted?.()
  }

  function handleExport() {
    const a = document.createElement('a')
    a.href = `/api/export/research_bin/${bin.id}?format=md`
    a.target = '_blank'
    a.click()
  }

  if (loading) return <div className="loading">Loading bin…</div>
  if (!detail) return <div className="banner error">Failed to load bin.</div>

  const pinnedIds = new Set(detail.items?.map(i => i.target_id) ?? [])

  return (
    <div className="rb-detail">
      <div className="rb-detail-header">
        <button className="btn-link rb-back-btn" onClick={onBack}>← All bins</button>
        <h2 className="rb-detail-title">{detail.name}</h2>
        <ExpiryBadge expiresAt={detail.expires_at} />
        <div className="rb-detail-actions">
          <button className="btn-secondary btn-sm" onClick={handleExport}>↓ Export MD</button>
          <button className="btn-secondary btn-sm" onClick={handlePromote} disabled={promoting}>
            {promoting ? 'Promoting…' : '📁 Promote to collection'}
          </button>
          <button className="btn-secondary btn-sm rb-delete-btn" onClick={handleDelete} disabled={deleting}>
            🗑 Delete
          </button>
        </div>
      </div>

      <div className="rb-hypothesis-row">
        {editingHyp ? (
          <div className="rb-hyp-edit">
            <textarea
              className="form-input"
              rows={3}
              value={hypDraft}
              onChange={e => setHypDraft(e.target.value)}
              autoFocus
            />
            <div style={{ display: 'flex', gap: 8, marginTop: 6 }}>
              <button className="btn-primary btn-sm" onClick={handleSaveHyp}>Save</button>
              <button className="btn-secondary btn-sm" onClick={() => setEditingHyp(false)}>Cancel</button>
            </div>
          </div>
        ) : (
          <p
            className="rb-hypothesis text-muted"
            onClick={() => { setHypDraft(detail.hypothesis || ''); setEditingHyp(true) }}
            title="Click to edit"
          >
            {detail.hypothesis || <em>No hypothesis yet — click to add one.</em>}
          </p>
        )}
      </div>

      <div className="rb-columns">
        <div className="rb-column rb-pinned-col">
          <h3 className="rb-col-title">📌 Pinned ({detail.items?.length ?? 0})</h3>
          {detail.items?.length === 0 && (
            <p className="text-muted" style={{ fontSize: '0.82rem' }}>
              No pinned items yet. Pin items from the live results on the right.
            </p>
          )}
          {detail.items?.map(item => {
            const art = item.article
            const tweet = art?.tweet_meta ? (() => { try { return JSON.parse(art.tweet_meta) } catch { return null } })() : null
            return (
              <div key={item.id} className="rb-pinned-item">
                {tweet ? (
                  <TweetCard tweet={tweet} articleId={art?.id} sourceUrl={art?.url} compact />
                ) : (
                  <div className="rb-plain-item">
                    <a href={art?.url} target="_blank" rel="noopener noreferrer" className="rb-item-url">
                      {art?.title || art?.url}
                    </a>
                  </div>
                )}
                {item.note && <p className="rb-item-note">{item.note}</p>}
                <button className="rb-unpin-btn btn-link" onClick={() => handleUnpin(item.id)}>✕ unpin</button>
              </div>
            )
          })}
        </div>

        <div className="rb-column rb-live-col">
          <h3 className="rb-col-title">
            🔍 Live results
            {detail.seed_query && <span className="rb-seed-query"> for "{detail.seed_query}"</span>}
          </h3>
          {!detail.seed_query && (
            <p className="text-muted" style={{ fontSize: '0.82rem' }}>
              No seed query set. Recreate this bin with a seed query to see live hits here.
            </p>
          )}
          {detail.live_hits?.map(hit => {
            let tweet = null
            try { tweet = hit.tweet_meta ? JSON.parse(hit.tweet_meta) : null } catch {}
            const isP = pinnedIds.has(String(hit.id))
            return (
              <div key={hit.id} className="rb-live-item">
                {tweet ? (
                  <TweetCard tweet={tweet} articleId={hit.id} sourceUrl={hit.url} compact />
                ) : (
                  <div className="rb-plain-item">
                    <a href={hit.url} target="_blank" rel="noopener noreferrer" className="rb-item-url">
                      {hit.title || hit.url}
                    </a>
                  </div>
                )}
                {!isP && (
                  <button className="btn-link rb-pin-btn" onClick={() => handlePin(hit)}>+ pin</button>
                )}
                {isP && <span className="rb-pinned-badge">✓ pinned</span>}
              </div>
            )
          })}
          {detail.seed_query && detail.live_hits?.length === 0 && (
            <p className="text-muted" style={{ fontSize: '0.82rem' }}>No results for this query.</p>
          )}
        </div>
      </div>
    </div>
  )
}

export default function ResearchBinsPage({ initialQuery = '' }) {
  const [bins, setBins] = useState(null)
  const [showNew, setShowNew] = useState(false)
  const [selected, setSelected] = useState(null)
  const [includeExpired, setIncludeExpired] = useState(false)

  function loadBins(expired = includeExpired) {
    fetch(`/api/research_bins?include_expired=${expired ? 1 : 0}`)
      .then(r => r.ok ? r.json() : [])
      .then(setBins)
      .catch(() => setBins([]))
  }

  useEffect(() => { loadBins() }, [includeExpired])

  if (selected) {
    return (
      <div className="rb-page">
        <BinDetail
          bin={selected}
          onBack={() => { setSelected(null); loadBins() }}
          onPromoted={() => { setSelected(null); loadBins() }}
          onDeleted={() => { setSelected(null); loadBins() }}
        />
      </div>
    )
  }

  const isEmpty = bins !== null && bins.length === 0

  return (
    <div className="rb-page">
      <div className="rb-list-header">
        <div>
          <h2 className="rb-list-title">🔍 Research Bins</h2>
          <p className="rb-list-caption text-muted">Temporary scratchpads tied to a search session.</p>
        </div>
        <div className="rb-list-header-actions">
          <label className="form-check rb-expired-toggle">
            <input type="checkbox" checked={includeExpired} onChange={e => setIncludeExpired(e.target.checked)} />
            Show expired
          </label>
          <button className="btn-primary" onClick={() => setShowNew(true)}>+ New bin</button>
        </div>
      </div>

      {bins === null && <div className="loading">Loading…</div>}

      {isEmpty && (
        <div className="rb-explainer">
          <div className="rb-explainer-icon">🔍</div>
          <h3 className="rb-explainer-title">No research bins yet</h3>
          <p className="rb-explainer-body">
            Research bins are temporary, topic-organized scratchpads tied to a search session.
            Use them to investigate a specific topic — pin the most interesting hits, test your
            hypothesis with the chatbot, then either promote the bin to a Collection or discard it.
          </p>
          <p className="rb-explainer-body">
            <strong>Distinct from Bookmarks</strong> (single quick saves) and{' '}
            <strong>Collections</strong> (curated, long-lived buckets). Bins expire in 7 days
            unless you promote them.
          </p>
          <button className="btn-primary" style={{ marginTop: 16 }} onClick={() => setShowNew(true)}>
            + Create your first research bin
          </button>
        </div>
      )}

      {bins && bins.map(bin => (
        <button
          key={bin.id}
          className="rb-list-item"
          onClick={() => setSelected(bin)}
        >
          <div className="rb-list-item-main">
            <span className="rb-list-item-name">{bin.name}</span>
            {bin.seed_query && <span className="rb-list-item-query">"{bin.seed_query}"</span>}
          </div>
          <div className="rb-list-item-meta">
            <span className="rb-list-item-count">{bin.item_count ?? 0} pinned</span>
            {bin.hypothesis && (
              <span className="rb-list-item-hyp">{bin.hypothesis.slice(0, 80)}</span>
            )}
            <ExpiryBadge expiresAt={bin.expires_at} />
            {bin.promoted_collection_id && (
              <span className="rb-promoted-badge">promoted</span>
            )}
          </div>
        </button>
      ))}

      {showNew && (
        <NewBinModal
          initialQuery={initialQuery}
          onClose={() => setShowNew(false)}
          onCreated={bin => {
            setShowNew(false)
            setBins(prev => [bin, ...(prev || [])])
            setSelected(bin)
          }}
        />
      )}
    </div>
  )
}
