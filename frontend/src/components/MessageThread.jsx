import React, { useEffect, useMemo, useState, useRef } from 'react'
import { useMessages } from '../hooks/useMessages.js'
import MessageBubble, { extractUrls } from './MessageBubble.jsx'
import LinkPreview, { useBulkStatus, useTweetCache } from './LinkPreview.jsx'
import { useGroupContext } from './GroupContext.jsx'
import ResizableDrawer from './ResizableDrawer.jsx'

function useSimilarBulk(tweetCache) {
  const [similarCache, setSimilarCache] = useState({})
  const fetchedRef = useRef(new Set())

  useEffect(() => {
    const articleIds = Object.values(tweetCache)
      .filter(a => a && a.id)
      .map(a => a.id)
      .filter(id => !fetchedRef.current.has(id))
    if (articleIds.length === 0) return

    articleIds.forEach(id => fetchedRef.current.add(id))
    fetch('/api/articles/similar_bulk', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ids: articleIds, limit: 5, min_score: 0.6 }),
    })
      .then(r => r.ok ? r.json() : {})
      .then(data => setSimilarCache(prev => ({ ...prev, ...data })))
      .catch(() => {})
  }, [tweetCache])

  return similarCache
}

function useMsgBookmarks(chatId) {
  const [bookmarkedKeys, setBookmarkedKeys] = useState(new Set())

  useEffect(() => {
    if (!chatId) return
    fetch('/api/message-bookmarks?limit=2000')
      .then(r => r.ok ? r.json() : [])
      .then(rows => setBookmarkedKeys(new Set(rows.map(r => r.msg_key))))
      .catch(() => {})
  }, [chatId])

  function toggleBookmark(msgKey, nowBookmarked) {
    setBookmarkedKeys(prev => {
      const next = new Set(prev)
      if (nowBookmarked) next.add(msgKey)
      else next.delete(msgKey)
      return next
    })
  }

  return { bookmarkedKeys, toggleBookmark }
}

export default function MessageThread({ chatId, chatName, onSearch, onAuthorClick, onHashtagClick, onMentionClick, scrollToMessage, onConsumeScrollTarget, onOpenInChat }) {
  const [page, setPage] = useState(1)
  const [query, setQuery] = useState('')
  const [inputVal, setInputVal] = useState('')
  const [previewUrl, setPreviewUrl] = useState(null)
  const { data, loading, error } = useMessages(chatId, page, query)
  // Stash the ts we're trying to scroll to; cleared after the bubble flashes.
  const [pendingScrollTs, setPendingScrollTs] = useState(null)

  // When scrollToMessage prop arrives (or changes), find the right page first.
  useEffect(() => {
    if (!scrollToMessage || scrollToMessage.chatId !== chatId) return
    const ts = scrollToMessage.ts
    if (!ts) return
    setPendingScrollTs(ts)
    fetch(`/api/chats/${encodeURIComponent(chatId)}/find-message?ts=${encodeURIComponent(ts)}&page_size=50${query ? `&q=${encodeURIComponent(query)}` : ''}`)
      .then(r => r.ok ? r.json() : null)
      .then(d => {
        if (d && d.found) setPage(d.page)
      })
      .catch(() => {})
      .finally(() => { if (onConsumeScrollTarget) onConsumeScrollTarget() })
  }, [scrollToMessage, chatId, query, onConsumeScrollTarget])

  // After messages render, scroll the matching bubble into view + flash highlight.
  useEffect(() => {
    if (!pendingScrollTs || loading) return
    // Wait one frame so the DOM has the new entries.
    const t = setTimeout(() => {
      const el = document.querySelector(`[data-msg-ts="${pendingScrollTs}"]`)
      if (el) {
        el.scrollIntoView({ behavior: 'smooth', block: 'center' })
        el.classList.add('msg-flash')
        setTimeout(() => el.classList.remove('msg-flash'), 2200)
        setPendingScrollTs(null)
      }
    }, 50)
    return () => clearTimeout(t)
  }, [pendingScrollTs, loading, data])
  const group = useGroupContext()
  const { bookmarkedKeys, toggleBookmark } = useMsgBookmarks(chatId)

  // Reset page when chat changes
  useEffect(() => { setPage(1); setQuery(''); setInputVal('') }, [chatId])

  // Close drawer on ESC; toggle select mode with 'g'
  useEffect(() => {
    function onKey(e) {
      if (e.key === 'Escape') setPreviewUrl(null)
      if (e.key === 'g' && !e.ctrlKey && !e.metaKey && document.activeElement.tagName !== 'INPUT') {
        group?.toggleSelectMode?.()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [group])

  // Collect all URLs visible on this page for bulk status + tweet cache
  const pageUrls = useMemo(() => {
    if (!data?.entries) return []
    const urls = []
    for (const e of data.entries) {
      if (e.type === 'message') urls.push(...extractUrls(e.body))
    }
    return [...new Set(urls)]
  }, [data?.entries])

  const tweetUrls = useMemo(
    () => pageUrls.filter(u => /https?:\/\/(x\.com|twitter\.com)\//.test(u)),
    [pageUrls]
  )

  const statusMap = useBulkStatus(pageUrls)
  const tweetCache = useTweetCache(tweetUrls)
  const similarCache = useSimilarBulk(tweetCache)

  function handleSearch(e) {
    const val = e.target.value
    setInputVal(val)
    setQuery(val)
    setPage(1)
  }

  if (!chatId) {
    return <div className="thread-empty">Select a chat to start browsing.</div>
  }

  const totalPages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1

  return (
    <div className={`thread-container${previewUrl ? ' with-drawer' : ''}`}>
      <div className="thread">
        <div className="thread-header">
          <h2 className="thread-title">{chatName}</h2>
          <input
            className="thread-search"
            type="search"
            placeholder="Filter messages…"
            value={inputVal}
            onChange={handleSearch}
          />
          {group && (
            <button
              className={`thread-select-mode-btn${group.selectMode ? ' active' : ''}`}
              onClick={group.toggleSelectMode}
              title="Toggle select mode (g)"
            >
              {group.selectMode ? 'Select: on' : 'Select'}
            </button>
          )}
        </div>

        {error && <div className="banner error">Failed to load messages.</div>}

        <div className="thread-messages">
          {loading && <div className="loading">Loading…</div>}
          {data && data.entries.map((entry, i) => {
            const msgKey = entry.type === 'message'
              ? `${chatId}|${entry.timestamp}|${entry.sender}`
              : null
            return (
              <MessageBubble
                key={i}
                entry={entry}
                chatId={chatId}
                msgKey={msgKey}
                isBookmarked={msgKey ? bookmarkedKeys.has(msgKey) : false}
                onBookmarkToggle={toggleBookmark}
                highlightQuery={query}
                statusMap={statusMap}
                onLinkClick={setPreviewUrl}
                tweetCache={tweetCache}
                similarCache={similarCache}
                onSearch={onSearch}
                onAuthorClick={onAuthorClick}
                onHashtagClick={onHashtagClick}
                onMentionClick={onMentionClick}
                onOpenInChat={onOpenInChat}
              />
            )
          })}
          {data && data.entries.length === 0 && !loading && (
            <div className="thread-empty">No messages match your filter.</div>
          )}
        </div>

        {data && (
          <div className="pagination">
            <button onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page <= 1}>
              ← Prev
            </button>
            <span>Page {page} of {totalPages} ({data.total} entries)</span>
            <button onClick={() => setPage(p => Math.min(totalPages, p + 1))} disabled={page >= totalPages}>
              Next →
            </button>
          </div>
        )}
      </div>

      {previewUrl && (
        <ResizableDrawer className="preview-drawer">
          <LinkPreview url={previewUrl} onClose={() => setPreviewUrl(null)} onSearch={onSearch} onAuthorClick={onAuthorClick} onHashtagClick={onHashtagClick} onMentionClick={onMentionClick} onOpenInChat={onOpenInChat} />
        </ResizableDrawer>
      )}
    </div>
  )
}
