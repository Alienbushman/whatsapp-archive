import React, { useCallback, useEffect, useState } from 'react'

const STATUS_TABS = [
  { key: 'failed', label: 'Failed' },
  { key: 'blocked', label: 'Blocked' },
  { key: 'pending', label: 'Pending' },
]

function ErrorClusterPanel() {
  const [clusters, setClusters] = useState(null)

  useEffect(() => {
    fetch('/api/articles/scrape_errors/summary?limit=15')
      .then(r => r.ok ? r.json() : [])
      .then(setClusters)
      .catch(() => setClusters([]))
  }, [])

  return (
    <div className="scrape-clusters">
      <h4 className="scrape-clusters-title">Error clusters</h4>
      {clusters === null && <div className="loading">Loading…</div>}
      {clusters && clusters.length === 0 && <p className="text-muted">No errors.</p>}
      {clusters && clusters.map((c, i) => (
        <div key={i} className="scrape-cluster-row">
          <span className="scrape-cluster-count">{c.count}×</span>
          <div className="scrape-cluster-detail">
            <code className="scrape-cluster-sig">{c.error_signature}</code>
            <div className="scrape-cluster-samples">
              {c.sample_urls.map((u, j) => (
                <a key={j} href={u} target="_blank" rel="noreferrer" className="scrape-cluster-url">{u.slice(0, 60)}</a>
              ))}
            </div>
          </div>
        </div>
      ))}
    </div>
  )
}

function ArticleRow({ item, onRetry, onMarkBlocked, selected, onToggleSelect }) {
  const [retrying, setRetrying] = useState(false)
  const [blocking, setBlocking] = useState(false)
  const [result, setResult] = useState(null)

  async function handleRetry() {
    setRetrying(true)
    try {
      const r = await fetch(`/api/articles/${item.id}/retry`, { method: 'POST' })
      setResult(r.ok ? 'ok' : 'failed')
      if (r.ok) onRetry(item.id)
    } finally {
      setRetrying(false)
    }
  }

  async function handleBlock() {
    const reason = window.prompt('Block reason:', 'blocked_manual')
    if (!reason) return
    setBlocking(true)
    try {
      const r = await fetch(`/api/articles/${item.id}/mark_blocked`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reason }),
      })
      if (r.ok) onMarkBlocked(item.id)
    } finally {
      setBlocking(false)
    }
  }

  return (
    <tr className={`scrape-row${result === 'ok' ? ' scrape-row-ok' : result === 'failed' ? ' scrape-row-failed' : ''}`}>
      <td>
        <input type="checkbox" checked={selected} onChange={() => onToggleSelect(item.id)} />
      </td>
      <td className="scrape-row-status">
        <span className={`scrape-status-badge scrape-status-${item.status}`}>{item.status}</span>
      </td>
      <td className="scrape-row-url">
        <a href={item.url} target="_blank" rel="noreferrer" title={item.url}>
          {item.url.slice(0, 70)}{item.url.length > 70 ? '…' : ''}
        </a>
      </td>
      <td className="scrape-row-error" title={item.error}>{(item.error || '').slice(0, 60)}</td>
      <td className="scrape-row-date">{(item.fetched_at || '').slice(0, 16)}</td>
      <td className="scrape-row-actions">
        <button className="btn-ghost scrape-retry-btn" onClick={handleRetry} disabled={retrying || result === 'ok'}>
          {retrying ? '…' : result === 'ok' ? '✓' : 'Retry'}
        </button>
        <button className="btn-ghost scrape-block-btn" onClick={handleBlock} disabled={blocking}>
          🚫
        </button>
      </td>
    </tr>
  )
}

export default function ScrapeRecoveryPage() {
  const [activeTab, setActiveTab] = useState('failed')
  const [data, setData] = useState(null)
  const [page, setPage] = useState(1)
  const [selected, setSelected] = useState(new Set())
  const [bulkRetrying, setBulkRetrying] = useState(false)
  const [bulkMsg, setBulkMsg] = useState('')
  const [backfillingEmbeds, setBackfillingEmbeds] = useState(false)
  const [embedMsg, setEmbedMsg] = useState('')

  const loadData = useCallback(() => {
    setData(null)
    fetch(`/api/articles/by_status?status=${activeTab}&page=${page}&page_size=50`)
      .then(r => r.ok ? r.json() : null)
      .then(setData)
      .catch(() => setData({ items: [], total: 0 }))
  }, [activeTab, page])

  useEffect(() => {
    setSelected(new Set())
    setBulkMsg('')
    loadData()
  }, [loadData])

  function handleRetry(id) {
    setData(prev => prev ? {
      ...prev,
      items: prev.items.filter(i => i.id !== id),
      total: prev.total - 1,
    } : prev)
  }

  function handleMarkBlocked(id) {
    setData(prev => prev ? {
      ...prev,
      items: prev.items.filter(i => i.id !== id),
      total: prev.total - 1,
    } : prev)
  }

  function toggleSelect(id) {
    setSelected(prev => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }

  function selectAll() {
    if (!data) return
    setSelected(new Set(data.items.map(i => i.id)))
  }

  async function handleBackfillEmbeds() {
    setBackfillingEmbeds(true)
    setEmbedMsg('')
    try {
      const r = await fetch('/api/embeddings/backfill', { method: 'POST' })
      if (r.status === 503) {
        setEmbedMsg('Qdrant unavailable — start the vector store first')
        return
      }
      const d = await r.json()
      setEmbedMsg(`Embedded ${d.embedded}, skipped ${d.skipped}, failed ${d.failed}`)
    } finally {
      setBackfillingEmbeds(false)
    }
  }

  async function handleBulkRetry() {
    const ids = [...selected]
    if (!ids.length) return
    setBulkRetrying(true)
    setBulkMsg('')
    try {
      const r = await fetch('/api/articles/retry_bulk', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids }),
      })
      const d = await r.json()
      setBulkMsg(`Enqueued ${d.enqueued} articles for background re-scrape`)
      setSelected(new Set())
    } finally {
      setBulkRetrying(false)
    }
  }

  const totalPages = data ? Math.ceil(data.total / 50) : 1

  return (
    <div className="scrape-recovery-page">
      <div className="scrape-main">
        <h2 className="scrape-title">⚙ Scrape Recovery</h2>

        <div className="scrape-tabs">
          {STATUS_TABS.map(t => (
            <button
              key={t.key}
              className={`scrape-tab${activeTab === t.key ? ' active' : ''}`}
              onClick={() => { setActiveTab(t.key); setPage(1) }}
            >{t.label}</button>
          ))}
        </div>

        <div className="scrape-toolbar">
          {data && <span className="text-muted">{data.total} article{data.total !== 1 ? 's' : ''}</span>}
          <button className="btn-ghost" onClick={selectAll}>Select all</button>
          {selected.size > 0 && (
            <button className="btn-primary" onClick={handleBulkRetry} disabled={bulkRetrying}>
              {bulkRetrying ? 'Enqueueing…' : `Retry ${selected.size} selected`}
            </button>
          )}
          {bulkMsg && <span className="text-muted" style={{ fontSize: '0.8rem' }}>{bulkMsg}</span>}
          <button className="btn-ghost" onClick={handleBackfillEmbeds} disabled={backfillingEmbeds}>
            {backfillingEmbeds ? 'Embedding…' : '⟳ Backfill embeddings'}
          </button>
          {embedMsg && <span className="text-muted" style={{ fontSize: '0.8rem' }}>{embedMsg}</span>}
        </div>

        {data === null && <div className="loading">Loading…</div>}
        {data && data.items.length === 0 && (
          <p className="text-muted">No {activeTab} articles.</p>
        )}

        {data && data.items.length > 0 && (
          <>
            <table className="scrape-table">
              <thead>
                <tr>
                  <th></th>
                  <th>Status</th>
                  <th>URL</th>
                  <th>Error</th>
                  <th>Fetched</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map(item => (
                  <ArticleRow
                    key={item.id}
                    item={item}
                    onRetry={handleRetry}
                    onMarkBlocked={handleMarkBlocked}
                    selected={selected.has(item.id)}
                    onToggleSelect={toggleSelect}
                  />
                ))}
              </tbody>
            </table>

            {totalPages > 1 && (
              <div className="scrape-pagination">
                <button className="btn-ghost" disabled={page === 1} onClick={() => setPage(p => p - 1)}>← Prev</button>
                <span className="text-muted">Page {page} / {totalPages}</span>
                <button className="btn-ghost" disabled={page >= totalPages} onClick={() => setPage(p => p + 1)}>Next →</button>
              </div>
            )}
          </>
        )}
      </div>

      <div className="scrape-sidebar">
        <ErrorClusterPanel />
      </div>
    </div>
  )
}
