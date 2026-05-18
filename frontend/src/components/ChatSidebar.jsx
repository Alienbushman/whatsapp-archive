import React, { useRef, useState } from 'react'
import { useChats } from '../hooks/useChats.js'

async function doSearch(q, fuzzy) {
  if (fuzzy) {
    const r = await fetch(`/api/search/fuzzy?q=${encodeURIComponent(q)}&kind=all&limit=30`)
    if (!r.ok) return []
    const hits = await r.json()
    // Normalise to legacy shape for SearchResults — keep messages AND articles.
    return hits
      .filter(h => h.kind === 'message' || h.kind === 'article')
      .map(h => {
        if (h.kind === 'article') {
          return {
            kind: 'article',
            article_id: h.article_id,
            url: h.url,
            title: h.title,
            snippet: h.snippet,
            chat_id: h.chat_id,
            chat_name: h.chat_name,
            match_source: h.match_source,
            score: h.score,
          }
        }
        return {
          kind: 'message',
          chat_id: h.chat_id,
          chat_name: h.chat_name,
          entry: { type: 'message', timestamp: h.timestamp, sender: '', body: h.body, is_deleted: false },
          score: h.score,
        }
      })
  }
  const r = await fetch(`/api/search?q=${encodeURIComponent(q)}&kind=all`)
  if (!r.ok) return []
  const data = await r.json()
  return data.results || []
}

async function uploadChatFile(file) {
  const formData = new FormData()
  formData.append('file', file)
  const r = await fetch('/api/chats/upload', { method: 'POST', body: formData })
  if (!r.ok) {
    let detail = `Upload failed (${r.status})`
    try {
      const data = await r.json()
      if (data?.detail) detail = data.detail
    } catch { /* ignore JSON parse errors */ }
    throw new Error(detail)
  }
  return r.json()
}

async function deleteChatById(chatId) {
  const r = await fetch(`/api/chats/${encodeURIComponent(chatId)}`, { method: 'DELETE' })
  if (!r.ok) {
    let detail = `Delete failed (${r.status})`
    try {
      const data = await r.json()
      if (data?.detail) detail = data.detail
    } catch { /* ignore */ }
    throw new Error(detail)
  }
  return r.json()
}

export default function ChatSidebar({ selectedId, onSelectChat, onSearchResults, onClearSearch, onTrending, showAggregations, onHome, onCollections, onBookmarks, onDigests, onEntities, onScrapeRecovery, onExports, onResearchBins, showCollections, showBookmarks, showDigests, showEntities, showScrapeRecovery, showExports, showResearchBins }) {
  const { chats, loading, error, refresh } = useChats()
  const [globalQuery, setGlobalQuery] = useState('')
  const [fuzzyMode, setFuzzyMode] = useState(false)
  const [searching, setSearching] = useState(false)
  const [didYouMean, setDidYouMean] = useState(null)

  const fileInputRef = useRef(null)
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState(null)
  const [uploadDuplicate, setUploadDuplicate] = useState(null) // {id, name} if replaced=false
  const [deletingId, setDeletingId] = useState(null)
  const [deleteError, setDeleteError] = useState(null)

  async function handleGlobalSearch(e) {
    const q = e.target.value
    setGlobalQuery(q)
    if (!q.trim()) {
      onClearSearch()
      setDidYouMean(null)
      return
    }
    setSearching(true)
    try {
      const results = await doSearch(q, fuzzyMode)
      // For non-fuzzy, also get did_you_mean
      if (!fuzzyMode) {
        const r2 = await fetch(`/api/search?q=${encodeURIComponent(q)}&kind=all`)
        if (r2.ok) {
          const data = await r2.json()
          setDidYouMean(data.did_you_mean && data.did_you_mean.length > 0 ? data.did_you_mean : null)
        }
      } else {
        setDidYouMean(null)
      }
      onSearchResults(results, q)
    } finally {
      setSearching(false)
    }
  }

  async function handleUploadChange(e) {
    const file = e.target.files?.[0]
    e.target.value = '' // reset so re-uploading the same file fires onChange
    if (!file) return
    setUploadError(null)
    setUploadDuplicate(null)
    setUploading(true)
    try {
      const data = await uploadChatFile(file)
      await refresh()
      if (data.replaced === false) {
        setUploadDuplicate({ id: data.id, name: data.name })
      } else {
        onSelectChat(data.id, data.name)
      }
    } catch (err) {
      setUploadError(err.message || String(err))
    } finally {
      setUploading(false)
    }
  }

  async function handleDelete(e, chatId) {
    e.stopPropagation()
    if (!window.confirm('Remove this chat from the archive?')) return
    setDeleteError(null)
    setDeletingId(chatId)
    try {
      await deleteChatById(chatId)
      await refresh()
      if (selectedId === chatId) onSelectChat(null, null)
    } catch (err) {
      setDeleteError(err.message || String(err))
    } finally {
      setDeletingId(null)
    }
  }

  return (
    <aside className="sidebar">
      <div className="sidebar-header">
        <div className="sidebar-title-row">
          <h1 className="app-title">WhatsApp Archive</h1>
        </div>
        <div className="search-row">
          <input
            className="sidebar-search"
            type="search"
            placeholder="Search all chats…"
            value={globalQuery}
            onChange={handleGlobalSearch}
          />
          <button
            className={`fuzzy-toggle${fuzzyMode ? ' active' : ''}`}
            title="Fuzzy search (typo-tolerant)"
            onClick={() => setFuzzyMode(f => !f)}
          >~</button>
        </div>
        <div className="upload-row">
          <input
            ref={fileInputRef}
            type="file"
            accept=".txt,text/plain"
            style={{ display: 'none' }}
            onChange={handleUploadChange}
          />
          <button
            className="upload-btn"
            onClick={() => fileInputRef.current?.click()}
            disabled={uploading}
            title="Upload a WhatsApp chat .txt export"
          >
            {uploading ? 'Uploading…' : 'Upload chat…'}
          </button>
        </div>
        {uploadDuplicate && (
          <div className="banner info upload-duplicate">
            This chat already exists in the archive.{' '}
            <button
              className="btn-link"
              onClick={() => {
                onSelectChat(uploadDuplicate.id, uploadDuplicate.name)
                setUploadDuplicate(null)
              }}
            >Open existing</button>
            {' '}
            <button className="btn-link" onClick={() => setUploadDuplicate(null)}>✕</button>
          </div>
        )}
        {uploadError && <div className="banner error upload-error">{uploadError}</div>}
        {deleteError && <div className="banner error">{deleteError}</div>}
        {searching && <div className="sidebar-searching">Searching…</div>}
        {globalQuery.trim() && !searching && onResearchBins && (
          <button
            className="rb-save-search-btn btn-link"
            title="Save this search as a research bin"
            onClick={() => onResearchBins(globalQuery.trim())}
          >📌 Save as research bin</button>
        )}
        {didYouMean && didYouMean.length > 0 && (
          <div className="did-you-mean">
            <span>Did you mean: </span>
            {didYouMean.map(h => (
              <button
                key={h.chat_id}
                className="dym-btn"
                onClick={() => onSelectChat(h.chat_id, h.chat_name)}
              >{h.chat_name}</button>
            ))}
          </div>
        )}
      </div>

      <div className="sidebar-nav-row">
        {onHome && (
          <button className="sidebar-home-btn" onClick={onHome}>🏠 Home</button>
        )}
        {onTrending && (
          <button
            className={`trending-btn${showAggregations ? ' active' : ''}`}
            onClick={onTrending}
          >⚡ Trending</button>
        )}
      </div>

      {(onBookmarks || onCollections) && (
        <div className="sidebar-curate-row">
          {onBookmarks && (
            <button
              className={`sidebar-curate-btn${showBookmarks ? ' active' : ''}`}
              onClick={onBookmarks}
              title="Quick saves of individual items"
            >★ Bookmarks</button>
          )}
          {onCollections && (
            <button
              className={`sidebar-curate-btn${showCollections ? ' active' : ''}`}
              onClick={onCollections}
              title="Named, persistent buckets you curate over time"
            >📁 Collections</button>
          )}
          {onDigests && (
            <button
              className={`sidebar-curate-btn${showDigests ? ' active' : ''}`}
              onClick={onDigests}
            >📰 Digests</button>
          )}
          {onEntities && (
            <button
              className={`sidebar-curate-btn${showEntities ? ' active' : ''}`}
              onClick={onEntities}
            >⚙ Entities</button>
          )}
          {onScrapeRecovery && (
            <button
              className={`sidebar-curate-btn${showScrapeRecovery ? ' active' : ''}`}
              onClick={onScrapeRecovery}
            >🔧 Scrape errors</button>
          )}
          {onResearchBins && (
            <button
              className={`sidebar-curate-btn${showResearchBins ? ' active' : ''}`}
              onClick={() => onResearchBins('')}
              title="Temporary scratchpads tied to a search session"
            >🔍 Research bins</button>
          )}
          {onExports && (
            <button
              className={`sidebar-curate-btn${showExports ? ' active' : ''}`}
              onClick={onExports}
            >📤 Exports</button>
          )}
        </div>
      )}

      {error && <div className="banner error">Failed to load chats.</div>}

      <ul className="chat-list">
        {loading && <li className="loading">Loading…</li>}
        {chats.map(chat => (
          <li key={chat.id} className="chat-list-item">
            <button
              className={`chat-item${selectedId === chat.id ? ' active' : ''}`}
              onClick={() => { setGlobalQuery(''); onClearSearch(); setDidYouMean(null); onSelectChat(chat.id, chat.name) }}
            >
              <span className="chat-name">{chat.name}</span>
              <span className="chat-count">{chat.message_count}</span>
            </button>
            <button
              className="chat-delete-btn"
              title="Remove this chat"
              disabled={deletingId === chat.id}
              onClick={(e) => handleDelete(e, chat.id)}
            >🗑</button>
          </li>
        ))}
      </ul>
    </aside>
  )
}
