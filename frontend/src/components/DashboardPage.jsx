import React, { useEffect, useState } from 'react'
import { TweetCard } from './LinkPreview.jsx'

function _fmt(n) {
  if (!n && n !== 0) return '—'
  if (n < 1000) return String(n)
  if (n < 1_000_000) return (n / 1000).toFixed(n < 10_000 ? 1 : 0) + 'K'
  return (n / 1_000_000).toFixed(1) + 'M'
}

function StatTile({ label, value }) {
  return (
    <div className="stat-tile">
      <span className="stat-value">{_fmt(value)}</span>
      <span className="stat-label">{label}</span>
    </div>
  )
}

function ProgressBar({ label, done, total }) {
  const pct = total > 0 ? Math.round((done / total) * 100) : 0
  return (
    <div className="dashboard-progress-row">
      <span className="dashboard-progress-label">{label}</span>
      <div className="dashboard-progress-track">
        <div className="dashboard-progress-fill" style={{ width: `${pct}%` }} />
      </div>
      <span className="dashboard-progress-pct">{done}/{total}</span>
    </div>
  )
}

export default function DashboardPage({ onAuthorClick, onHashtagClick, onMentionClick, onSearch }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => {
    fetch('/api/dashboard')
      .then(r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        return r.json()
      })
      .then(setData)
      .catch(e => setError(e.message))
  }, [])

  if (error) return <div className="thread-empty banner error">{error}</div>
  if (!data) return <div className="thread-empty">Loading…</div>

  return (
    <div className="dashboard-page">
      <h2 className="dashboard-title">Archive Overview</h2>

      <div className="dashboard-stats">
        <StatTile label="Chats" value={data.total_chats} />
        <StatTile label="Messages" value={data.total_messages} />
        <StatTile label="Tweets" value={data.total_tweets} />
        <StatTile label="Authors" value={data.distinct_authors} />
      </div>

      {data.date_range?.from && (
        <div className="dashboard-date-range">
          {data.date_range.from} – {data.date_range.to}
        </div>
      )}

      {data.scrape.total > 0 && (
        <div className="dashboard-progress-section">
          <ProgressBar label="Scraped" done={data.scrape.ok} total={data.scrape.total} />
          {data.enrich.total > 0 && (
            <ProgressBar label="Enriched" done={data.enrich.enriched} total={data.enrich.total} />
          )}
        </div>
      )}

      <div className="dashboard-columns">
        {data.top_authors.length > 0 && (
          <div className="dashboard-col">
            <h3 className="dashboard-col-title">Top Authors</h3>
            <ul className="dashboard-list">
              {data.top_authors.map(a => (
                <li key={a.handle}>
                  <button className="dashboard-list-item" onClick={() => onAuthorClick && onAuthorClick(a.handle)}>
                    <span className="dashboard-list-label">@{a.handle}</span>
                    <span className="dashboard-list-count">{_fmt(a.tweet_count)}</span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}

        {data.top_hashtags.length > 0 && (
          <div className="dashboard-col">
            <h3 className="dashboard-col-title">Top Hashtags</h3>
            <div className="chip-row">
              {data.top_hashtags.map(h => (
                <button key={h.tag} className="chip chip-hashtag chip-clickable"
                  onClick={() => onHashtagClick && onHashtagClick(h.tag)}>
                  #{h.tag} <span className="chip-count">{h.count}</span>
                </button>
              ))}
            </div>
          </div>
        )}
      </div>

      {data.latest_tweet?.tweet && (
        <div className="dashboard-recent">
          <h3 className="dashboard-col-title">Most recent tweet</h3>
          <TweetCard
            tweet={data.latest_tweet.tweet}
            sourceUrl={data.latest_tweet.url}
            onSearch={onSearch}
            onAuthorClick={onAuthorClick}
            onHashtagClick={onHashtagClick}
            onMentionClick={onMentionClick}
          />
        </div>
      )}
    </div>
  )
}
