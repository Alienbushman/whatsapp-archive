import React, { useEffect, useState } from 'react'
import { TweetCard } from './LinkPreview.jsx'

const WINDOWS = ['7d', '30d', '90d', 'all']

function useAgg(path, window) {
  const [data, setData] = useState(null)
  useEffect(() => {
    fetch(`/api/aggregations/${path}?window=${window}`)
      .then(r => r.ok ? r.json() : null)
      .then(setData)
      .catch(() => {})
  }, [path, window])
  return data
}

function SentimentBar({ label, value, total, color }) {
  const pct = total > 0 ? Math.round((value / total) * 100) : 0
  return (
    <div className="sentiment-bar-row">
      <span className="sentiment-bar-label">{label}</span>
      <div className="sentiment-bar-track">
        <div className="sentiment-bar-fill" style={{ width: `${pct}%`, background: color }} />
      </div>
      <span className="sentiment-bar-pct">{pct}% <span className="sentiment-bar-n">({value})</span></span>
    </div>
  )
}

function SentimentCard({ window }) {
  const data = useAgg('sentiment?group_by=author&', window)
  if (!data || data.length === 0) return null

  const totals = data.reduce((acc, g) => ({
    positive: acc.positive + (g.positive || 0),
    negative: acc.negative + (g.negative || 0),
    neutral: acc.neutral + (g.neutral || 0),
  }), { positive: 0, negative: 0, neutral: 0 })
  const total = totals.positive + totals.negative + totals.neutral

  return (
    <div className="agg-card">
      <div className="agg-card-title">Sentiment Mix</div>
      <div className="agg-sentiment-bars">
        <SentimentBar label="Positive" value={totals.positive} total={total} color="#16a34a" />
        <SentimentBar label="Neutral" value={totals.neutral} total={total} color="#6b7280" />
        <SentimentBar label="Negative" value={totals.negative} total={total} color="#dc2626" />
      </div>
      <div className="agg-card-footnote">{total} enriched articles</div>
    </div>
  )
}

export default function AggregationsPage({ onBack, onAuthorClick, onHashtagClick, onMentionClick }) {
  const [window, setWindow] = useState('30d')

  const topAuthors = useAgg('top-authors?limit=10&', window)
  const topHashtags = useAgg('top-hashtags?limit=20&', window)
  const topEntities = useAgg('top-entities?limit=20&', window)
  const mostLiked = useAgg('top-tweets?by=favorite_count&limit=5&', window)
  const mostRTed = useAgg('top-tweets?by=retweet_count&limit=5&', window)

  return (
    <div className="agg-page">
      <div className="agg-header">
        <button className="btn-link" onClick={onBack}>← Back</button>
        <h2 className="agg-title">Trending</h2>
        <div className="agg-window-selector">
          {WINDOWS.map(w => (
            <button key={w} className={`kind-btn${window === w ? ' active' : ''}`} onClick={() => setWindow(w)}>{w}</button>
          ))}
        </div>
      </div>

      <div className="agg-grid">

        {topAuthors && topAuthors.length > 0 && (
          <div className="agg-card">
            <div className="agg-card-title">Top Authors</div>
            <ol className="agg-leaderboard">
              {topAuthors.map((a, i) => (
                <li key={a.handle} className="agg-leaderboard-row">
                  <span className="agg-rank">{i + 1}</span>
                  <button className="agg-author-btn" onClick={() => onAuthorClick && onAuthorClick(a.handle)}>
                    <span className="agg-author-name">{a.display_name || a.handle}</span>
                    <span className="agg-author-handle">@{a.handle}</span>
                  </button>
                  <span className="agg-stat">{a.tweet_count} tweets</span>
                  <span className="agg-stat-muted">❤ {a.total_favorites}</span>
                </li>
              ))}
            </ol>
          </div>
        )}

        {topHashtags && topHashtags.length > 0 && (
          <div className="agg-card">
            <div className="agg-card-title">Top Hashtags</div>
            <div className="agg-chip-cloud">
              {topHashtags.map(h => {
                const max = topHashtags[0]?.count || 1
                const size = 0.75 + (h.count / max) * 0.6
                return (
                  <button key={h.tag}
                    className="chip chip-hashtag chip-clickable"
                    style={{ fontSize: `${size}rem` }}
                    onClick={() => onHashtagClick && onHashtagClick(h.tag)}
                  >#{h.tag} <span className="chip-count">{h.count}</span></button>
                )
              })}
            </div>
          </div>
        )}

        {mostLiked && mostLiked.length > 0 && (
          <div className="agg-card agg-card-wide">
            <div className="agg-card-title">Most Liked Tweets</div>
            <div className="agg-tweet-list">
              {mostLiked.map((article, i) => article.tweet && (
                <TweetCard key={i} tweet={article.tweet} sourceUrl={article.url}
                  onAuthorClick={onAuthorClick} onHashtagClick={onHashtagClick} onMentionClick={onMentionClick} />
              ))}
            </div>
          </div>
        )}

        {mostRTed && mostRTed.length > 0 && (
          <div className="agg-card agg-card-wide">
            <div className="agg-card-title">Most Retweeted</div>
            <div className="agg-tweet-list">
              {mostRTed.map((article, i) => article.tweet && (
                <TweetCard key={i} tweet={article.tweet} sourceUrl={article.url}
                  onAuthorClick={onAuthorClick} onHashtagClick={onHashtagClick} onMentionClick={onMentionClick} />
              ))}
            </div>
          </div>
        )}

        {topEntities && topEntities.length > 0 && (
          <div className="agg-card">
            <div className="agg-card-title">Top Entities</div>
            <div className="agg-entity-list">
              {topEntities.map((e, i) => (
                <div key={i} className="agg-entity-row">
                  <span className="agg-entity-name">{e.entity_name}</span>
                  {e.entity_kind && <span className="agg-entity-kind">{e.entity_kind}</span>}
                  <span className="agg-entity-count">{e.count}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        <SentimentCard window={window} />

      </div>
    </div>
  )
}
