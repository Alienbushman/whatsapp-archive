import React, { useState } from 'react'
import { useGroupContext } from './GroupContext.jsx'
import ExportGroupModal from './ExportGroupModal.jsx'

function GroupItem({ item, onRemove }) {
  const handle = item.tweet?.author_handle || '?'
  const text = (item.tweet?.text || item.url || '').slice(0, 100)
  return (
    <div className="group-panel-item">
      <span className="group-item-handle">@{handle}</span>
      <span className="group-item-text">{text}</span>
      <button className="group-item-remove" onClick={() => onRemove(item.article_id)} title="Remove">×</button>
    </div>
  )
}

export default function GroupPanel() {
  const group = useGroupContext()
  const [open, setOpen] = useState(true)
  const [suggestions, setSuggestions] = useState(null)
  const [loadingSuggestions, setLoadingSuggestions] = useState(false)
  const [saveModal, setSaveModal] = useState(false)
  const [exportOpen, setExportOpen] = useState(false)
  const [collectionName, setCollectionName] = useState('')
  const [saving, setSaving] = useState(false)

  if (!group || group.size === 0) return null

  async function findMoreLikeThese() {
    setLoadingSuggestions(true)
    setSuggestions(null)
    try {
      const ids = group.items.map(i => i.article_id)
      const r = await fetch('/api/articles/similar_bulk', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids, limit: 5, min_score: 0.6 }),
      })
      if (!r.ok) { setSuggestions([]); return }
      const data = await r.json()
      const seen = new Set(ids)
      const candidates = []
      for (const items of Object.values(data)) {
        for (const a of items) {
          if (!seen.has(a.id)) { seen.add(a.id); candidates.push(a) }
        }
      }
      candidates.sort((a, b) => (b.similarity_score || 0) - (a.similarity_score || 0))
      setSuggestions(candidates.slice(0, 10))
    } finally {
      setLoadingSuggestions(false)
    }
  }

  async function saveAsCollection() {
    const name = collectionName.trim()
    if (!name) return
    setSaving(true)
    try {
      const r = await fetch('/api/collections', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      })
      if (!r.ok) return
      const col = await r.json()
      for (const item of group.items) {
        await fetch(`/api/collections/${col.id}/items`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ article_id: item.article_id }),
        }).catch(() => {})
      }
      group.clear()
      setSaveModal(false)
      setCollectionName('')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className={`group-panel${open ? ' open' : ' collapsed'}`}>
      <div className="group-panel-header" onClick={() => setOpen(v => !v)}>
        <span className="group-panel-title">Working group · {group.size} tweets</span>
        <span className="group-panel-toggle">{open ? '▼' : '▲'}</span>
      </div>

      {open && (
        <>
          <div className="group-panel-body">
            {group.items.map(item => (
              <GroupItem key={item.article_id} item={item} onRemove={group.remove} />
            ))}
            {group.size >= group.maxSize && (
              <div className="group-panel-warning">Max {group.maxSize} tweets reached.</div>
            )}
          </div>

          {suggestions && suggestions.length > 0 && (
            <div className="group-suggestions">
              <div className="group-suggestions-header">
                <span>Suggested</span>
                <button className="group-add-all-btn" onClick={() => {
                  suggestions.forEach(a => group.add(a))
                  setSuggestions(null)
                }}>+ Add all</button>
              </div>
              {suggestions.map((a, i) => {
                let tweet = null
                try { tweet = a.tweet_meta ? JSON.parse(a.tweet_meta) : null } catch {}
                return (
                  <div key={i} className="group-suggestion-item">
                    <span className="group-item-handle">@{tweet?.author_handle || '?'}</span>
                    <span className="group-item-text">{(tweet?.text || a.title || '').slice(0, 80)}</span>
                    <button className="group-suggestion-add" onClick={() => group.add(a)}>+</button>
                  </div>
                )
              })}
            </div>
          )}

          <div className="group-panel-footer">
            <button className="group-action-btn" onClick={findMoreLikeThese} disabled={loadingSuggestions}>
              {loadingSuggestions ? '…' : '≈ Find more like these'}
            </button>
            <button className="group-action-btn" onClick={() => setExportOpen(true)}>Export…</button>
            <button className="group-action-btn" onClick={() => setSaveModal(true)}>
              Save as collection
            </button>
            <button className="group-action-btn group-clear-btn" onClick={group.clear}>Clear</button>
          </div>

          {saveModal && (
            <div className="group-save-modal">
              <input
                className="group-save-input"
                placeholder="Collection name…"
                value={collectionName}
                onChange={e => setCollectionName(e.target.value)}
                autoFocus
              />
              <div className="group-save-actions">
                <button className="group-action-btn" onClick={saveAsCollection} disabled={saving || !collectionName.trim()}>
                  {saving ? 'Saving…' : 'Save'}
                </button>
                <button className="group-action-btn" onClick={() => setSaveModal(false)}>Cancel</button>
              </div>
            </div>
          )}
        </>
      )}

      {exportOpen && (
        <ExportGroupModal
          articleIds={group.items.map(i => i.article_id)}
          onClose={() => setExportOpen(false)}
        />
      )}
    </div>
  )
}
