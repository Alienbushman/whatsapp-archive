import React, { useEffect, useRef, useState } from 'react'
import TickerChip from './TickerChip.jsx'
import { useGroupContext } from './GroupContext.jsx'
import { useCollectionsContext } from './CollectionsContext.jsx'
import AddToBinButton from './AddToBinButton.jsx'

const STATUS_ICON = { fresh: '🔗', ok: '📄', enriched: '✨', blocked: '🔒', failed: '⚠️' }

async function fetchArticle(url) {
  const r = await fetch(`/api/articles?url=${encodeURIComponent(url)}&include_enrichment=1`)
  if (r.status === 404) return null
  if (!r.ok) throw new Error(`HTTP ${r.status}`)
  return r.json()
}

async function scrapeArticle(url) {
  const r = await fetch('/api/articles/fetch', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url }),
  })
  if (!r.ok) throw new Error(`HTTP ${r.status}`)
  return r.json()
}

function _fmt(n) {
  if (n === null || n === undefined) return null
  if (n < 1000) return String(n)
  if (n < 1_000_000) return (n / 1000).toFixed(n < 10_000 ? 1 : 0) + 'K'
  return (n / 1_000_000).toFixed(1) + 'M'
}

async function _triggerSearch(q, onSearch) {
  const r = await fetch(`/api/search?q=${encodeURIComponent(q)}&kind=article`)
  if (!r.ok) return
  const data = await r.json()
  onSearch(data.results || [], q)
}

function _refreshedAgo(refreshedAt) {
  if (!refreshedAt) return null
  const diff = Date.now() - new Date(refreshedAt).getTime()
  const h = Math.floor(diff / 3600000)
  const d = Math.floor(h / 24)
  if (d > 0) return `${d}d ago`
  if (h > 0) return `${h}h ago`
  return 'just now'
}

function _highlightQuery(text, query) {
  if (!query || !text) return text
  const idx = text.toLowerCase().indexOf(query.toLowerCase())
  if (idx === -1) return text
  return (
    <>
      {text.slice(0, idx)}
      <mark>{text.slice(idx, idx + query.length)}</mark>
      {_highlightQuery(text.slice(idx + query.length), query)}
    </>
  )
}

function SimilarOpenInChat({ hit, onOpenInChat }) {
  // Multi-location aware: if the URL was shared in just one chat, render a
  // direct button. If in multiple, render a dropdown so the user picks which
  // location to jump to.
  const [open, setOpen] = useState(false)
  const locs = Array.isArray(hit.locations) && hit.locations.length > 0
    ? hit.locations
    : (hit.chat_id ? [{ chat_id: hit.chat_id, chat_name: hit.chat_name, ts: hit.ts_first_seen }] : [])
  if (locs.length === 0) return null
  if (locs.length === 1) {
    const loc = locs[0]
    return (
      <button
        className="similar-open-in-chat-btn"
        onClick={(e) => { e.stopPropagation(); onOpenInChat(loc.chat_id, loc.chat_name, loc.ts) }}
        title={`Jump to this tweet in ${loc.chat_name || 'chat'}`}
      >
        📍 View in {loc.chat_name || 'chat'}
      </button>
    )
  }
  return (
    <div className="similar-open-in-chat-wrap">
      <button
        className="similar-open-in-chat-btn"
        onClick={(e) => { e.stopPropagation(); setOpen(o => !o) }}
        title={`Shared in ${locs.length} chats`}
      >
        📍 View in {locs.length} chats {open ? '▴' : '▾'}
      </button>
      {open && (
        <div className="similar-open-in-chat-menu" onClick={e => e.stopPropagation()}>
          {locs.map((loc, idx) => (
            <button
              key={idx}
              className="similar-open-in-chat-menu-item"
              onClick={(e) => {
                e.stopPropagation()
                setOpen(false)
                onOpenInChat(loc.chat_id, loc.chat_name, loc.ts)
              }}
            >
              <span className="loc-chat">{loc.chat_name || loc.chat_id}</span>
              <span className="loc-ts">{(loc.ts || '').slice(0, 16).replace('T', ' ')}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

export function TweetCard({ tweet, sourceUrl, nested = false, onSearch, compact = false, onOpen, onAuthorClick, onHashtagClick, onMentionClick, articleId = null, onCollectionAdd, highlightQuery = null, onOpenInChat = null }) {
  const group = useGroupContext()
  const collectionsCtx = useCollectionsContext()
  const [collectDropdownOpen, setCollectDropdownOpen] = useState(false)
  const [collectAdded, setCollectAdded] = useState(null)
  const collectRef = useRef(null)

  useEffect(() => {
    if (!collectDropdownOpen) return
    function handler(e) {
      if (collectRef.current && !collectRef.current.contains(e.target)) setCollectDropdownOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [collectDropdownOpen])
  const [showSimilar, setShowSimilar] = useState(false)
  const [similar, setSimilar] = useState(null)
  const [loadingSimilar, setLoadingSimilar] = useState(false)
  const [showAllSimilar, setShowAllSimilar] = useState(false)
  const [ocrData, setOcrData] = useState(null)
  const [showOcr, setShowOcr] = useState(false)
  const [bookmark, setBookmark] = useState(null)
  const [note, setNote] = useState('')
  const [showNote, setShowNote] = useState(false)
  const noteTimer = React.useRef(null)
  const [refreshedAt, setRefreshedAt] = useState(tweet.refreshed_at || null)
  const [refreshing, setRefreshing] = useState(false)
  const [quotedBy, setQuotedBy] = useState(null)
  const [showQuotedBy, setShowQuotedBy] = useState(false)

  function handleRefreshNow(e) {
    e.stopPropagation()
    if (!articleId || refreshing) return
    setRefreshing(true)
    fetch(`/api/articles/${articleId}/refresh`, { method: 'POST' })
      .then(r => r.ok ? r.json() : null)
      .then(data => {
        if (data?.tweet_meta?.refreshed_at) setRefreshedAt(data.tweet_meta.refreshed_at)
      })
      .catch(() => {})
      .finally(() => setRefreshing(false))
  }

  useEffect(() => {
    if (!articleId || compact || nested) return
    fetch(`/api/articles/${articleId}/ocr`)
      .then(r => r.ok ? r.json() : null)
      .then(data => { if (data && data.some(d => d.ocr_text)) setOcrData(data) })
      .catch(() => {})
  }, [articleId, compact, nested])

  useEffect(() => {
    if (!articleId || compact || nested) return
    fetch(`/api/bookmarks`)
      .then(r => r.ok ? r.json() : [])
      .then(rows => {
        const bm = rows.find(r => r.article_id === articleId)
        if (bm) { setBookmark(bm); setNote(bm.note || '') }
      })
      .catch(() => {})
  }, [articleId, compact, nested])

  useEffect(() => {
    if (!articleId || compact || nested) return
    fetch(`/api/articles/${articleId}/quoted_by`)
      .then(r => r.ok ? r.json() : [])
      .then(data => { if (data && data.length > 0) setQuotedBy(data) })
      .catch(() => {})
  }, [articleId, compact, nested])

  function handleStar(e) {
    e.stopPropagation()
    if (bookmark) {
      fetch(`/api/bookmarks/${articleId}`, { method: 'DELETE' })
        .then(() => { setBookmark(null); setNote(''); setShowNote(false) })
        .catch(() => {})
    } else {
      fetch(`/api/bookmarks/${articleId}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ starred: 1 }),
      })
        .then(r => r.ok ? r.json() : null)
        .then(bm => { if (bm) setBookmark(bm) })
        .catch(() => {})
    }
  }

  function handleNoteChange(e) {
    const val = e.target.value
    setNote(val)
    clearTimeout(noteTimer.current)
    noteTimer.current = setTimeout(() => {
      fetch(`/api/bookmarks/${articleId}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ starred: 1, note: val }),
      })
        .then(r => r.ok ? r.json() : null)
        .then(bm => { if (bm) setBookmark(bm) })
        .catch(() => {})
    }, 600)
  }

  function handleSimilarClick(e) {
    e.stopPropagation()
    if (showSimilar) { setShowSimilar(false); return }
    if (similar !== null) { setShowSimilar(true); return }
    setLoadingSimilar(true)
    fetch(`/api/articles/${articleId}/similar?limit=5`)
      .then(r => r.ok ? r.json() : [])
      .then(data => { setSimilar(data); setShowSimilar(true) })
      .catch(() => setSimilar([]))
      .finally(() => setLoadingSimilar(false))
  }
  const stats = [
    { icon: '❤', value: tweet.favorite_count },
    { icon: '🔁', value: tweet.retweet_count },
    { icon: '💬', value: tweet.reply_count },
    { icon: '👁', value: tweet.view_count },
  ].filter(s => s.value !== null && s.value !== undefined)

  const displayName = tweet.author_name || tweet.author_handle
  const handle = tweet.author_handle ? `@${tweet.author_handle}` : ''
  const date = tweet.created_at ? tweet.created_at.slice(0, 10) : ''

  const inGroup = group && articleId && group.has(articleId)
  const cardClass = [
    nested ? 'tweet-card tweet-card-quoted' : compact ? 'tweet-card tweet-card-compact' : 'tweet-card',
    inGroup ? 'tweet-card-in-group' : '',
  ].filter(Boolean).join(' ')

  return (
    <div className={cardClass} onClick={compact && onOpen ? onOpen : undefined}>
      <div className="tweet-card-header">
        {onAuthorClick && tweet.author_handle ? (
          <button className="tweet-card-author tweet-card-author-link"
            onClick={e => { e.stopPropagation(); onAuthorClick(tweet.author_handle) }}>
            {displayName}
          </button>
        ) : (
          <span className="tweet-card-author">{displayName}</span>
        )}
        {tweet.is_verified && <span className="tweet-card-verified" title="Verified">✓</span>}
        {handle && <span className="tweet-card-handle">{handle}</span>}
        {date && <span className="tweet-card-date">· {date}</span>}
        {!compact && !nested && articleId && (
          <span className="tweet-card-actions">
            <button
              className={`tweet-card-star${bookmark ? ' starred' : ''}`}
              title={bookmark ? 'Remove bookmark' : 'Bookmark'}
              onClick={handleStar}
            >{bookmark ? '★' : '☆'}</button>
            {bookmark && (
              <button
                className="tweet-card-note-btn"
                title="Add note"
                onClick={e => { e.stopPropagation(); setShowNote(v => !v) }}
              >✏</button>
            )}
            {(collectionsCtx || onCollectionAdd) && (
              <span className="tweet-card-collect-wrap" ref={collectRef} onClick={e => e.stopPropagation()}>
                <button
                  className="tweet-card-collect-btn"
                  title="Add to collection"
                  onClick={() => {
                    if (onCollectionAdd) { onCollectionAdd(articleId); return }
                    setCollectDropdownOpen(v => !v)
                  }}
                >📁</button>
                {collectAdded && <span className="add-to-collection-confirm">+{collectAdded}</span>}
                {collectDropdownOpen && collectionsCtx && (
                  <div className="add-to-collection-menu">
                    {collectionsCtx.collections.length === 0 && (
                      <span className="add-to-collection-empty">No collections yet</span>
                    )}
                    {collectionsCtx.collections.map(col => (
                      <button key={col.id} className="add-to-collection-item" onClick={() => {
                        collectionsCtx.addToCollection(col.id, articleId)
                          .then(() => { setCollectAdded(col.name); setCollectDropdownOpen(false) })
                          .catch(() => {})
                      }}>{col.name}</button>
                    ))}
                  </div>
                )}
              </span>
            )}
            {group && group.selectMode && articleId && (
              <button
                className={`tweet-card-group-btn${group.has(articleId) ? ' in-group' : ''}`}
                title={group.has(articleId) ? 'Remove from group' : 'Add to group'}
                onClick={e => {
                  e.stopPropagation()
                  if (group.has(articleId)) {
                    group.remove(articleId)
                  } else {
                    group.addTweet(articleId, sourceUrl, tweet)
                  }
                }}
              >{group.has(articleId) ? '✓' : '+'}</button>
            )}
            <a
              className="tweet-card-export-btn"
              title="Export as Markdown"
              href={`/api/export/article/${articleId}?format=md`}
              target="_blank"
              rel="noreferrer"
              onClick={e => e.stopPropagation()}
            >↓</a>
            <AddToBinButton targetKind="article" targetId={articleId} />
          </span>
        )}
      </div>

      {tweet.text && (
        <p className="tweet-card-text">
          {highlightQuery ? _highlightQuery(tweet.text, highlightQuery) : tweet.text}
        </p>
      )}

      {tweet.media_urls && tweet.media_urls.length > 0 && (
        compact ? (
          <div className="tweet-card-media-single">
            <img src={tweet.media_urls[0]} alt="" loading="lazy" />
          </div>
        ) : (
          <div className={`tweet-card-media-grid count-${Math.min(tweet.media_urls.length, 4)}`}>
            {tweet.media_urls.slice(0, 4).map((src, i) => (
              <img key={i} src={src} alt="" loading="lazy" />
            ))}
          </div>
        )
      )}

      {!compact && !nested && ocrData && (
        <div className="tweet-card-ocr">
          <button className="tweet-card-ocr-badge" onClick={e => { e.stopPropagation(); setShowOcr(v => !v) }}>
            📷 OCR {showOcr ? '▲' : '▼'}
          </button>
          {showOcr && (
            <div className="tweet-card-ocr-text">
              {ocrData.map((d, i) => d.ocr_text ? (
                <pre key={i}>{d.ocr_text}</pre>
              ) : null)}
            </div>
          )}
        </div>
      )}

      {stats.length > 0 && (
        <div className="tweet-card-stats">
          {stats.map((s, i) => (
            <span key={i}>{s.icon} {_fmt(s.value)}</span>
          ))}
        </div>
      )}

      {!compact && !nested && articleId && (
        <div className="tweet-card-refresh-row">
          {refreshedAt ? (
            <span className="tweet-card-refreshed-at">refreshed {_refreshedAgo(refreshedAt)}</span>
          ) : (
            <span className="tweet-card-refreshed-at tweet-card-refreshed-stale">never refreshed</span>
          )}
          {(!refreshedAt || (Date.now() - new Date(refreshedAt).getTime()) > 7 * 24 * 3600000) && (
            <button
              className="tweet-card-refresh-btn"
              onClick={handleRefreshNow}
              disabled={refreshing}
              title="Re-fetch engagement stats"
            >{refreshing ? 'Refreshing…' : 'Refresh now'}</button>
          )}
        </div>
      )}

      {!compact && !nested && bookmark && showNote && (
        <div className="tweet-card-note-area" onClick={e => e.stopPropagation()}>
          <textarea
            className="tweet-card-note-input"
            placeholder="Add a note…"
            value={note}
            onChange={handleNoteChange}
            rows={3}
          />
        </div>
      )}

      {tweet.hashtags && tweet.hashtags.length > 0 && (
        <div className="chip-row">
          {tweet.hashtags.map(h => (onHashtagClick || onSearch) ? (
            <button key={h} className="chip chip-hashtag chip-clickable"
              onClick={e => {
                e.stopPropagation()
                onHashtagClick ? onHashtagClick(h) : _triggerSearch('#' + h, onSearch)
              }}>#{h}</button>
          ) : (
            <span key={h} className="chip chip-hashtag">#{h}</span>
          ))}
        </div>
      )}

      {tweet.mentioned_handles && tweet.mentioned_handles.length > 0 && (
        <div className="chip-row">
          {tweet.mentioned_handles.slice(0, 6).map(h => (onMentionClick || onSearch) ? (
            <button key={h} className="chip chip-mention chip-clickable"
              onClick={e => {
                e.stopPropagation()
                onMentionClick ? onMentionClick(h) : _triggerSearch('@' + h, onSearch)
              }}>@{h}</button>
          ) : (
            <span key={h} className="chip chip-mention">@{h}</span>
          ))}
        </div>
      )}

      {tweet.tickers && tweet.tickers.length > 0 && (
        <div className="chip-row" onClick={e => e.stopPropagation()}>
          {tweet.tickers.map(t => (
            <TickerChip key={t} symbol={t} onSearch={onSearch} />
          ))}
        </div>
      )}

      {!compact && tweet.embedded_urls && tweet.embedded_urls.length > 0 && (
        <div className="tweet-card-urls">
          {tweet.embedded_urls.map((u, i) => (
            <a key={i} href={u.expanded_url || '#'} target="_blank" rel="noreferrer" className="tweet-card-url">
              {u.display_url || u.expanded_url}
            </a>
          ))}
        </div>
      )}

      {!compact && tweet.quoted_tweet && (
        <TweetCard tweet={tweet.quoted_tweet} sourceUrl={null} nested onSearch={onSearch} onAuthorClick={onAuthorClick} onHashtagClick={onHashtagClick} onMentionClick={onMentionClick} />
      )}

      {!nested && !compact && articleId && (
        <div className="tweet-card-similar-section">
          <button className="tweet-card-similar-btn" onClick={handleSimilarClick}>
            {loadingSimilar ? 'Loading…' : showSimilar ? 'Hide similar' : '≈ Similar tweets'}
          </button>
          {showSimilar && similar && similar.length > 0 && (
            <div className="tweet-card-similar-list">
              {(showAllSimilar ? similar : similar.slice(0, 5)).map((a, i) => {
                let meta = null
                try { meta = a.tweet_meta ? JSON.parse(a.tweet_meta) : null } catch {}
                const canOpen = onOpenInChat && a.chat_id && a.ts_first_seen
                return (
                  <div key={i} className="tweet-card-similar-item-wrap">
                    <span className="similar-score-badge" title="Similarity score">
                      {(a.similarity_score * 100).toFixed(0)}%
                    </span>
                    {meta ? (
                      <TweetCard
                        tweet={meta}
                        sourceUrl={a.url}
                        articleId={a.id}
                        onSearch={onSearch}
                        onAuthorClick={onAuthorClick}
                        onHashtagClick={onHashtagClick}
                        onMentionClick={onMentionClick}
                        onOpenInChat={onOpenInChat}
                      />
                    ) : (
                      <div className="tweet-card-similar-fallback">
                        <span className="similar-text">{(a.title || a.url || '').slice(0, 120)}</span>
                      </div>
                    )}
                    {canOpen && <SimilarOpenInChat hit={a} onOpenInChat={onOpenInChat} />}
                  </div>
                )
              })}
              {!showAllSimilar && similar.length >= 5 && (
                <button className="tweet-card-similar-more-btn" onClick={() => {
                  setShowAllSimilar(true)
                  setLoadingSimilar(true)
                  fetch(`/api/articles/${articleId}/similar?limit=20`)
                    .then(r => r.ok ? r.json() : similar)
                    .then(data => setSimilar(data))
                    .catch(() => {})
                    .finally(() => setLoadingSimilar(false))
                }}>
                  {loadingSimilar ? 'Loading…' : 'Show more similar…'}
                </button>
              )}
            </div>
          )}
          {showSimilar && similar && similar.length === 0 && (
            <div className="tweet-card-similar-list">
              <span className="similar-text" style={{ color: 'var(--text-muted)' }}>No similar articles found.</span>
            </div>
          )}
        </div>
      )}

      {!nested && !compact && quotedBy && quotedBy.length > 0 && (
        <div className="tweet-card-quoted-by-section">
          <button
            className="tweet-card-quoted-by-btn"
            onClick={() => setShowQuotedBy(v => !v)}
          >
            {showQuotedBy ? '▲' : '▼'} Quoted by {quotedBy.length} tweet{quotedBy.length !== 1 ? 's' : ''} in archive
          </button>
          {showQuotedBy && (
            <div className="tweet-card-quoted-by-list">
              {quotedBy.map((q, i) => {
                let meta = null
                try { meta = q.tweet_meta ? JSON.parse(q.tweet_meta) : null } catch {}
                return (
                  <div key={i} className="tweet-card-quoted-by-item">
                    {meta?.author_handle && (
                      <span className="quoted-by-handle">@{meta.author_handle}</span>
                    )}
                    <span className="quoted-by-text">
                      {(meta?.text || q.title || '').slice(0, 120)}
                    </span>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      )}

      {!nested && !compact && sourceUrl && (
        <a className="link-preview-open" href={sourceUrl} target="_blank" rel="noreferrer">
          Open on x.com ↗
        </a>
      )}

      {compact && (
        <span className="tweet-card-compact-hint">View in drawer ↗</span>
      )}
    </div>
  )
}

function TweetThreadView({ thread, sourceUrl, onSearch, onAuthorClick, onHashtagClick, onMentionClick }) {
  return (
    <div className="tweet-thread">
      {thread.map((item, i) => (
        <div key={item.id || item.tweet_id || i} className={`tweet-thread-item tweet-thread-${item.position}`}>
          {item.missing ? (
            <div className="tweet-thread-missing">
              (tweet missing from archive —{' '}
              <a href={`https://x.com/i/status/${item.tweet_id}`} target="_blank" rel="noreferrer">
                open on x.com ↗
              </a>)
            </div>
          ) : (
            <TweetCard
              tweet={item.tweet}
              sourceUrl={item.position === 'self' ? sourceUrl : item.url}
              onSearch={onSearch}
              onAuthorClick={onAuthorClick}
              onHashtagClick={onHashtagClick}
              onMentionClick={onMentionClick}
              articleId={item.id}
            />
          )}
        </div>
      ))}
    </div>
  )
}

export default function LinkPreview({ url, onClose, onSearch, onAuthorClick, onHashtagClick, onMentionClick, onOpenInChat = null }) {
  const [article, setArticle] = useState(undefined) // undefined=loading, null=not scraped
  const [error, setError] = useState('')
  const [scraping, setScraping] = useState(false)
  const [thread, setThread] = useState(null)

  useEffect(() => {
    setArticle(undefined)
    setError('')
    setThread(null)
    fetchArticle(url).then(setArticle).catch(e => setError(e.message))
  }, [url])

  useEffect(() => {
    if (!article?.id) return
    fetch(`/api/articles/${article.id}/thread`)
      .then(r => r.ok ? r.json() : null)
      .then(data => { if (data && data.length > 1) setThread(data) })
      .catch(() => {})
  }, [article?.id])

  async function handleFetch() {
    setScraping(true)
    setError('')
    try {
      const result = await scrapeArticle(url)
      setArticle(result)
    } catch (e) {
      setError(`Fetch failed: ${e.message}`)
    } finally {
      setScraping(false)
    }
  }

  const enrich = article?.enrichment
  const tweet = article?.tweet

  return (
    <div className="link-preview">
      <div className="link-preview-header">
        <span className="link-preview-url" title={url}>{new URL(url).hostname}</span>
        <button className="link-preview-close" onClick={onClose} aria-label="Close preview">✕</button>
      </div>

      {article === undefined && !error && (
        <div className="loading">Loading…</div>
      )}

      {error && (
        <div className="banner error">{error}</div>
      )}

      {article === null && !scraping && (
        <div className="link-preview-unscraped">
          <p className="text-muted">Not yet scraped.</p>
          <button className="btn-primary" onClick={handleFetch}>Fetch preview</button>
        </div>
      )}

      {scraping && <div className="loading">Fetching…</div>}

      {article && tweet && (
        <div className="link-preview-body">
          {thread ? (
            <TweetThreadView thread={thread} sourceUrl={url} onSearch={onSearch} onAuthorClick={onAuthorClick} onHashtagClick={onHashtagClick} onMentionClick={onMentionClick} />
          ) : (
            <TweetCard tweet={tweet} sourceUrl={url} onSearch={onSearch} onAuthorClick={onAuthorClick} onHashtagClick={onHashtagClick} onMentionClick={onMentionClick} articleId={article.id} onOpenInChat={onOpenInChat} />
          )}

          {enrich?.summary && (
            <p className="link-preview-summary">{enrich.summary}</p>
          )}

          {enrich?.categories?.length > 0 && (
            <div className="chip-row">
              {enrich.categories.map(c => <span key={c} className="chip chip-category">{c}</span>)}
            </div>
          )}

          {enrich?.entities?.length > 0 && (
            <div className="chip-row">
              {enrich.entities.slice(0, 6).map((e, i) => (
                <span key={i} className={`chip chip-entity chip-${e.kind}`}>{e.name}</span>
              ))}
            </div>
          )}

          {enrich?.sentiment && enrich.sentiment !== 'n/a' && (
            <span className={`sentiment-badge sentiment-${enrich.sentiment}`}>{enrich.sentiment}</span>
          )}
        </div>
      )}

      {article && !tweet && (
        <div className="link-preview-body">
          {article.og_image && (
            <img className="link-preview-image" src={article.og_image} alt="" loading="lazy" />
          )}

          <h3 className="link-preview-title">{article.title || url}</h3>

          {(article.author || article.published_at) && (
            <p className="link-preview-meta">
              {article.author && <span>{article.author}</span>}
              {article.author && article.published_at && ' · '}
              {article.published_at && <span>{article.published_at.slice(0, 10)}</span>}
            </p>
          )}

          {enrich?.summary && (
            <p className="link-preview-summary">{enrich.summary}</p>
          )}

          {!enrich?.summary && article.raw_text && (
            <p className="link-preview-excerpt">{article.raw_text.slice(0, 300)}…</p>
          )}

          {enrich?.categories?.length > 0 && (
            <div className="chip-row">
              {enrich.categories.map(c => <span key={c} className="chip chip-category">{c}</span>)}
            </div>
          )}

          {enrich?.entities?.length > 0 && (
            <div className="chip-row">
              {enrich.entities.slice(0, 6).map((e, i) => (
                <span key={i} className={`chip chip-entity chip-${e.kind}`}>{e.name}</span>
              ))}
            </div>
          )}

          {enrich?.sentiment && enrich.sentiment !== 'n/a' && (
            <span className={`sentiment-badge sentiment-${enrich.sentiment}`}>{enrich.sentiment}</span>
          )}

          <a className="link-preview-open" href={url} target="_blank" rel="noreferrer">
            Open original ↗
          </a>
        </div>
      )}
    </div>
  )
}

export function useTweetCache(urls) {
  const [cache, setCache] = useState({})

  useEffect(() => {
    if (!urls || urls.length === 0) return
    fetch('/api/articles/bulk', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ urls }),
    })
      .then(r => r.ok ? r.json() : {})
      .then(setCache)
      .catch(() => {})
  }, [urls.join('|')])

  return cache
}

export function useBulkStatus(urls) {
  const [statusMap, setStatusMap] = useState({})

  useEffect(() => {
    if (!urls || urls.length === 0) return
    fetch('/api/articles/bulk_status', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ urls }),
    })
      .then(r => r.ok ? r.json() : {})
      .then(setStatusMap)
      .catch(() => {})
  }, [urls.join('|')])

  return statusMap
}

export function LinkStatusIcon({ status }) {
  if (!status || status.status === 'fresh') return null
  const icon = status.has_enrichment ? STATUS_ICON.enriched : STATUS_ICON[status.status] || STATUS_ICON.ok
  return <span className="link-status-icon" title={status.has_enrichment ? 'enriched' : status.status}>{icon}</span>
}
