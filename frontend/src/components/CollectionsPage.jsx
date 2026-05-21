import React, { useEffect, useRef, useState } from 'react'
import { TweetCard } from './LinkPreview.jsx'
import { useCollectionsContext } from './CollectionsContext.jsx'

export default function CollectionsPage({ onSearch, onAuthorClick, onHashtagClick, onMentionClick }) {
  const collectionsCtx = useCollectionsContext()
  const collections = collectionsCtx?.collections ?? null
  const [selected, setSelected] = useState(null)  // {id, name}
  const [items, setItems] = useState([])
  const [loadingItems, setLoadingItems] = useState(false)
  const [newName, setNewName] = useState('')
  const [creating, setCreating] = useState(false)
  const [error, setError] = useState('')
  const [exportingId, setExportingId] = useState(null)

  function loadItems(col) {
    setSelected(col)
    setLoadingItems(true)
    fetch(`/api/collections/${col.id}/items?page_size=100`)
      .then(r => r.ok ? r.json() : [])
      .then(data => { setItems(data); setLoadingItems(false) })
      .catch(() => { setItems([]); setLoadingItems(false) })
  }

  async function handleCreate(e) {
    e.preventDefault()
    if (!newName.trim()) return
    setCreating(true)
    setError('')
    try {
      const col = await collectionsCtx?.createCollection(newName.trim())
      if (!col) { setError('Failed to create'); return }
      setNewName('')
    } catch (err) {
      setError(err.message)
    } finally {
      setCreating(false)
    }
  }

  function handleRemoveItem(articleId) {
    if (!selected) return
    fetch(`/api/collections/${selected.id}/items/${articleId}`, { method: 'DELETE' })
      .then(() => setItems(prev => prev.filter(i => i.id !== articleId)))
      .catch(() => {})
  }

  function handleExport(col) {
    setExportingId(col.id)
    const a = document.createElement('a')
    a.href = `/api/collections/${col.id}/export?format=md`
    a.target = '_blank'
    a.click()
    setTimeout(() => setExportingId(null), 1500)
  }

  const isEmpty = collections !== null && collections.length === 0

  return (
    <div className="collections-page">
      <div className="collections-sidebar">
        <h2 className="collections-title">📁 Collections</h2>
        <p className="collections-caption text-muted">Named, persistent buckets you curate over time.</p>

        <form className="collections-new-form" onSubmit={handleCreate}>
          <input
            className="collections-new-input"
            value={newName}
            onChange={e => setNewName(e.target.value)}
            placeholder="New collection name…"
          />
          <button className="btn-primary" type="submit" disabled={creating || !newName.trim()}>
            {creating ? '…' : '+'}
          </button>
        </form>
        {error && <div className="banner error" style={{ marginBottom: 8 }}>{error}</div>}

        {collections === null && <div className="loading">Loading…</div>}
        {collections && collections.map(col => (
          <button
            key={col.id}
            className={`collections-item${selected?.id === col.id ? ' active' : ''}`}
            onClick={() => loadItems(col)}
          >
            <span className="collections-item-name">{col.name}</span>
            <span className="collections-item-count">{col.item_count ?? 0}</span>
          </button>
        ))}
      </div>

      <div className="collections-content">
        {isEmpty && !selected && (
          <div className="collections-explainer">
            <div className="collections-explainer-icon">📁</div>
            <h3 className="collections-explainer-title">No collections yet</h3>
            <p className="collections-explainer-body">
              Collections are curated, persistent groups of tweets and articles you assemble for
              ongoing research. Use them to track topics like <em>Gold supercycle</em> or{' '}
              <em>Fed rate decisions</em> across many chats.
            </p>
            <p className="collections-explainer-body">
              Unlike Bookmarks (quick saves of individual items), a collection is a named bucket —
              drop tweets in, search across them, and export the whole thing as Markdown for an LLM.
            </p>
            <p className="collections-explainer-how">
              <strong>How to add items:</strong> Click the 📁 folder icon on any tweet card in a
              chat, search result, or bookmark.
            </p>
          </div>
        )}

        {!isEmpty && !selected && (
          <div className="collections-empty">
            <p className="text-muted">Select a collection from the left to view its tweets.</p>
            <p className="text-muted" style={{ fontSize: '0.8rem', marginTop: 8 }}>
              Tip: click 📁 on any tweet card to add it to a collection.
            </p>
          </div>
        )}

        {selected && (
          <>
            <div className="collections-content-header">
              <h3 className="collections-content-title">{selected.name}</h3>
              <div className="collections-content-actions">
                <span className="collections-content-count">{items.length} item{items.length !== 1 ? 's' : ''}</span>
                <button
                  className="btn-secondary btn-sm"
                  title="Export this collection as Markdown"
                  onClick={() => handleExport(selected)}
                  disabled={exportingId === selected.id}
                >
                  {exportingId === selected.id ? 'Downloading…' : '↓ Export MD'}
                </button>
              </div>
            </div>

            {loadingItems && <div className="loading">Loading…</div>}

            {!loadingItems && items.length === 0 && (
              <div className="collections-empty">
                <p className="text-muted">This collection is empty.</p>
                <p className="text-muted" style={{ fontSize: '0.8rem', marginTop: 8 }}>
                  Drop tweets here by clicking 📁 on any tweet card in a chat or search result.
                </p>
              </div>
            )}

            {!loadingItems && items.map(item => {
              const tweet = item.tweet || null
              if (!tweet) return null
              return (
                <div key={item.id} className="collections-tweet-row">
                  <TweetCard
                    tweet={tweet}
                    articleId={item.id}
                    sourceUrl={item.url}
                    onSearch={onSearch}
                    onAuthorClick={onAuthorClick}
                    onHashtagClick={onHashtagClick}
                    onMentionClick={onMentionClick}
                  />
                  <button
                    className="collections-remove-btn"
                    title="Remove from collection"
                    onClick={() => handleRemoveItem(item.id)}
                  >✕</button>
                </div>
              )
            })}
          </>
        )}
      </div>
    </div>
  )
}

export function useCollections() {
  const [collections, setCollections] = useState([])

  useEffect(() => {
    fetch('/api/collections')
      .then(r => r.ok ? r.json() : [])
      .then(setCollections)
      .catch(() => {})
  }, [])

  function addToCollection(collectionId, articleId) {
    return fetch(`/api/collections/${collectionId}/items`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ article_id: articleId }),
    })
  }

  return { collections, addToCollection, setCollections }
}

export function AddToCollectionDropdown({ articleId, collections, addToCollection }) {
  const [open, setOpen] = useState(false)
  const [added, setAdded] = useState(null)
  const ref = useRef(null)

  useEffect(() => {
    function handler(e) {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  function handleAdd(col) {
    addToCollection(col.id, articleId)
      .then(() => { setAdded(col.name); setOpen(false) })
      .catch(() => {})
  }

  return (
    <span className="add-to-collection-wrap" ref={ref} onClick={e => e.stopPropagation()}>
      <button
        className="tweet-card-collect-btn"
        title="Add to collection"
        onClick={() => setOpen(v => !v)}
      >📁</button>
      {added && <span className="add-to-collection-confirm">Added to {added}</span>}
      {open && (
        <div className="add-to-collection-menu">
          {collections.length === 0 && (
            <span className="add-to-collection-empty">No collections</span>
          )}
          {collections.map(col => (
            <button key={col.id} className="add-to-collection-item" onClick={() => handleAdd(col)}>
              {col.name}
            </button>
          ))}
        </div>
      )}
    </span>
  )
}
