import React, { useEffect, useRef, useState } from 'react'
import { TweetCard } from './LinkPreview.jsx'
import TimeseriesChart from './TimeseriesChart.jsx'
import RelatedPanel from './RelatedPanel.jsx'

function ExportDropdown({ scope, value }) {
  const [open, setOpen] = useState(false)
  const ref = useRef(null)
  useEffect(() => {
    function onClick(e) { if (ref.current && !ref.current.contains(e.target)) setOpen(false) }
    document.addEventListener('mousedown', onClick)
    return () => document.removeEventListener('mousedown', onClick)
  }, [])
  function open_url(fmt) {
    window.open(`/api/export/${scope}/${encodeURIComponent(value)}?format=${fmt}`, '_blank')
    setOpen(false)
  }
  return (
    <div className="export-dropdown" ref={ref}>
      <button className="export-dropdown-btn" onClick={() => setOpen(o => !o)}>Export ▾</button>
      {open && (
        <div className="export-dropdown-menu">
          {['md', 'csv', 'json'].map(f => (
            <button key={f} className="export-dropdown-item" onClick={() => open_url(f)}>{f.toUpperCase()}</button>
          ))}
        </div>
      )}
    </div>
  )
}

function _fmt(n) {
  if (!n) return '0'
  if (n < 1000) return String(n)
  if (n < 1_000_000) return (n / 1000).toFixed(n < 10_000 ? 1 : 0) + 'K'
  return (n / 1_000_000).toFixed(1) + 'M'
}

export default function AuthorPage({ handle, onBack, onSelectChat, onSearch, onAuthorClick, onHashtagClick, onMentionClick }) {
  const [summary, setSummary] = useState(null)
  const [tweets, setTweets] = useState(null)
  const [page, setPage] = useState(1)
  const [order, setOrder] = useState('date')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [timeseries, setTimeseries] = useState(null)
  const [influence, setInfluence] = useState(null)

  useEffect(() => {
    setLoading(true)
    setError('')
    fetch(`/api/authors/${encodeURIComponent(handle)}`)
      .then(r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        return r.json()
      })
      .then(setSummary)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [handle])

  useEffect(() => {
    fetch(`/api/authors/${encodeURIComponent(handle)}/tweets?page=${page}&page_size=20&order=${order}`)
      .then(r => r.ok ? r.json() : null)
      .then(setTweets)
      .catch(() => {})
  }, [handle, page, order])

  useEffect(() => {
    fetch(`/api/timeseries/tweets?author=${encodeURIComponent(handle)}&group_by=month`)
      .then(r => r.ok ? r.json() : null)
      .then(setTimeseries)
      .catch(() => {})
  }, [handle])

  useEffect(() => {
    fetch(`/api/authors/${encodeURIComponent(handle)}/influence`)
      .then(r => r.ok ? r.json() : null)
      .then(setInfluence)
      .catch(() => {})
  }, [handle])

  if (loading) return <div className="thread-empty">Loading…</div>
  if (error) return <div className="thread-empty banner error">{error}</div>
  if (!summary) return null

  const totalPages = tweets ? Math.max(1, Math.ceil(tweets.total / 20)) : 1

  return (
    <div className="author-page author-page-with-related">
      <div className="author-page-header">
        <button className="btn-link" onClick={onBack}>← Back</button>
        <div className="author-page-identity">
          <span className="author-page-name">{summary.display_name}</span>
          {summary.is_verified && <span className="tweet-card-verified" title="Verified">✓</span>}
          <span className="author-page-handle">@{summary.handle}</span>
        </div>
        <ExportDropdown scope="author" value={handle} />
        <div className="author-page-stats">
          <span><strong>{summary.tweet_count}</strong> tweets</span>
          <span><strong>{_fmt(summary.total_favorites)}</strong> ❤</span>
          <span><strong>{_fmt(summary.total_retweets)}</strong> 🔁</span>
          {summary.total_views > 0 && <span><strong>{_fmt(summary.total_views)}</strong> 👁</span>}
          {summary.first_seen && (
            <span className="author-date-range">
              {summary.first_seen.slice(0, 10)} – {summary.last_seen?.slice(0, 10)}
            </span>
          )}
        </div>
      </div>

      {timeseries && timeseries.length > 1 && (
        <TimeseriesChart data={timeseries} height={80} label="Tweet activity" />
      )}

      {summary.top_hashtags.length > 0 && (
        <div className="author-page-section">
          <span className="author-section-label">Top hashtags</span>
          <div className="chip-row">
            {summary.top_hashtags.map(({ tag, count }) => (
              <button key={tag} className="chip chip-hashtag chip-clickable"
                onClick={() => onHashtagClick ? onHashtagClick(tag) : onSearch && onSearch([], '#' + tag, '#' + tag)}>
                #{tag} <span className="chip-count">{count}</span>
              </button>
            ))}
          </div>
        </div>
      )}

      {summary.top_mentioned.length > 0 && (
        <div className="author-page-section">
          <span className="author-section-label">Top mentions</span>
          <div className="chip-row">
            {summary.top_mentioned.map(({ handle: h, count }) => (
              <button key={h} className="chip chip-mention chip-clickable"
                onClick={() => onAuthorClick && onAuthorClick(h)}>
                @{h} <span className="chip-count">{count}</span>
              </button>
            ))}
          </div>
        </div>
      )}

      {summary.chats_shared_in.length > 0 && (
        <div className="author-page-section">
          <span className="author-section-label">Shared in</span>
          <div className="chip-row">
            {summary.chats_shared_in.map(({ chat_id, chat_name, share_count }) => (
              <button key={chat_id} className="chip chip-clickable"
                onClick={() => onSelectChat && onSelectChat(chat_id, chat_name)}>
                {chat_name} <span className="chip-count">{share_count}</span>
              </button>
            ))}
          </div>
        </div>
      )}

      <div className="author-page-tweets-header">
        <span className="author-section-label">Tweets</span>
        <div className="author-sort">
          {['date', 'likes', 'retweets'].map(o => (
            <button key={o} className={`kind-btn${order === o ? ' active' : ''}`}
              onClick={() => { setOrder(o); setPage(1) }}>
              {o}
            </button>
          ))}
        </div>
      </div>

      <div className="author-page-tweet-list">
        {tweets?.tweets.map((article, i) => article.tweet && (
          <TweetCard
            key={article.id || i}
            tweet={article.tweet}
            sourceUrl={article.url}
            onSearch={onSearch}
            onAuthorClick={onAuthorClick}
            onHashtagClick={onHashtagClick}
            onMentionClick={onMentionClick}
          />
        ))}
      </div>

      {tweets && totalPages > 1 && (
        <div className="pagination">
          <button onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page <= 1}>← Prev</button>
          <span>Page {page} of {totalPages}</span>
          <button onClick={() => setPage(p => Math.min(totalPages, p + 1))} disabled={page >= totalPages}>Next →</button>
        </div>
      )}

      {influence && (influence.in_degree > 0 || influence.out_degree > 0) && (
        <div className="author-page-section author-influence-section">
          <span className="author-section-label">Influence</span>
          {influence.centrality_rank && (
            <p className="author-influence-rank">
              #{influence.centrality_rank} most-connected of {influence.total_authors} authors
            </p>
          )}
          {influence.top_inbound.length > 0 && (
            <div className="author-influence-group">
              <span className="author-influence-label">Who quotes/mentions @{handle}</span>
              <div className="chip-row">
                {influence.top_inbound.map(({ from: h, weight }) => (
                  <button key={h} className="chip chip-mention chip-clickable"
                    onClick={() => onAuthorClick && onAuthorClick(h)}>
                    @{h} <span className="chip-count">{weight}</span>
                  </button>
                ))}
              </div>
            </div>
          )}
          {influence.top_outbound.length > 0 && (
            <div className="author-influence-group">
              <span className="author-influence-label">@{handle} quotes/mentions</span>
              <div className="chip-row">
                {influence.top_outbound.map(({ to: h, weight }) => (
                  <button key={h} className="chip chip-mention chip-clickable"
                    onClick={() => onAuthorClick && onAuthorClick(h)}>
                    @{h} <span className="chip-count">{weight}</span>
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      <RelatedPanel
        seedKind="author"
        seedValue={handle}
        onHashtagClick={onHashtagClick}
        onMentionClick={onMentionClick}
        onAuthorClick={onAuthorClick}
      />
    </div>
  )
}
