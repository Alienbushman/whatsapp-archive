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

export default function TagBrowsePage({ kind, value, onBack, onSelectChat, onSearch, onAuthorClick, onHashtagClick, onMentionClick }) {
  const [summary, setSummary] = useState(null)
  const [tweets, setTweets] = useState(null)
  const [page, setPage] = useState(1)
  const [order, setOrder] = useState('date')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [timeseries, setTimeseries] = useState(null)

  const apiBase = kind === 'hashtag' ? `/api/hashtags/${encodeURIComponent(value)}` : `/api/mentions/${encodeURIComponent(value)}`
  const label = kind === 'hashtag' ? `#${value}` : `@${value}`

  useEffect(() => {
    setLoading(true)
    setError('')
    setSummary(null)
    fetch(apiBase)
      .then(r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        return r.json()
      })
      .then(setSummary)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [apiBase])

  useEffect(() => {
    setTweets(null)
    fetch(`${apiBase}/tweets?page=${page}&page_size=20&order=${order}`)
      .then(r => r.ok ? r.json() : null)
      .then(setTweets)
      .catch(() => {})
  }, [apiBase, page, order])

  useEffect(() => {
    if (kind !== 'hashtag') return
    fetch(`/api/timeseries/tweets?hashtag=${encodeURIComponent(value)}&group_by=month`)
      .then(r => r.ok ? r.json() : null)
      .then(setTimeseries)
      .catch(() => {})
  }, [kind, value])

  if (loading) return <div className="thread-empty">Loading…</div>
  if (error) return <div className="thread-empty banner error">{error}</div>
  if (!summary) return null

  const totalPages = tweets ? Math.max(1, Math.ceil(tweets.total / 20)) : 1

  return (
    <div className="author-page">
      <div className="author-page-header">
        <button className="btn-link" onClick={onBack}>← Back</button>
        <div className="author-page-identity">
          <span className="author-page-name">{label}</span>
        </div>
        <ExportDropdown scope={kind} value={value} />
        <div className="author-page-stats">
          <span><strong>{summary.count}</strong> tweet{summary.count !== 1 ? 's' : ''}</span>
          {summary.first_seen && (
            <span className="author-date-range">
              {summary.first_seen.slice(0, 10)} – {summary.last_seen?.slice(0, 10)}
            </span>
          )}
        </div>
      </div>

      {timeseries && timeseries.length > 1 && (
        <TimeseriesChart data={timeseries} height={80} label="Activity" />
      )}

      {kind === 'hashtag' && summary.top_authors?.length > 0 && (
        <div className="author-page-section">
          <span className="author-section-label">Top authors using this tag</span>
          <div className="chip-row">
            {summary.top_authors.map(({ handle, count }) => (
              <button key={handle} className="chip chip-mention chip-clickable"
                onClick={() => onAuthorClick && onAuthorClick(handle)}>
                @{handle} <span className="chip-count">{count}</span>
              </button>
            ))}
          </div>
        </div>
      )}

      {kind === 'mention' && summary.top_mentioners?.length > 0 && (
        <div className="author-page-section">
          <span className="author-section-label">Top authors mentioning {label}</span>
          <div className="chip-row">
            {summary.top_mentioners.map(({ handle, count }) => (
              <button key={handle} className="chip chip-mention chip-clickable"
                onClick={() => onAuthorClick && onAuthorClick(handle)}>
                @{handle} <span className="chip-count">{count}</span>
              </button>
            ))}
          </div>
        </div>
      )}

      {kind === 'hashtag' && summary.top_chats?.length > 0 && (
        <div className="author-page-section">
          <span className="author-section-label">Shared in</span>
          <div className="chip-row">
            {summary.top_chats.map(({ chat_id, chat_name, count }) => (
              <button key={chat_id} className="chip chip-clickable"
                onClick={() => onSelectChat && onSelectChat(chat_id, chat_name)}>
                {chat_name} <span className="chip-count">{count}</span>
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

      <RelatedPanel
        seedKind={kind}
        seedValue={value}
        onHashtagClick={onHashtagClick}
        onMentionClick={onMentionClick}
        onAuthorClick={onAuthorClick}
      />
    </div>
  )
}
