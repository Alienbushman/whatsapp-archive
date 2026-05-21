import React, { useEffect, useState } from 'react'
import { TweetCard } from './LinkPreview.jsx'

function ActivitySparkline({ arc }) {
  if (!arc || arc.length === 0) return null
  const max = Math.max(...arc.map(m => m.count), 1)
  return (
    <div className="topic-sparkline">
      {arc.map(m => (
        <div
          key={m.month}
          className="topic-spark-bar"
          style={{ height: `${Math.round(24 * m.count / max)}px` }}
          title={`${m.month}: ${m.count}`}
        />
      ))}
    </div>
  )
}

function TopicCard({ topic, onClick, onPinToggle }) {
  // Pinned topics survive future reclusterings (the cluster job re-inserts them
  // rather than wiping them). Custom names override the LLM-generated name.
  const isPinned = !!topic.pinned
  async function handlePinClick(e) {
    e.stopPropagation()
    if (onPinToggle) onPinToggle(topic.id, !isPinned)
  }
  return (
    <div className={`topic-card${isPinned ? ' pinned' : ''}`} onClick={() => onClick(topic.id)}>
      <div className="topic-card-header">
        <span className="topic-card-name">
          {isPinned && <span className="topic-pin-icon" title="Pinned — survives reclustering">📌</span>}
          {topic.name}
        </span>
        <span className="topic-card-count">{topic.article_count}</span>
      </div>
      {topic.description && (
        <p className="topic-card-desc">{topic.description}</p>
      )}
      {topic.top_hashtags && topic.top_hashtags.length > 0 && (
        <div className="topic-card-tags">
          {topic.top_hashtags.map(h => (
            <span key={h} className="topic-hashtag-chip">#{h}</span>
          ))}
        </div>
      )}
      <ActivitySparkline arc={topic.activity_arc} />
      {onPinToggle && (
        <button
          className={`topic-pin-btn${isPinned ? ' active' : ''}`}
          onClick={handlePinClick}
          title={isPinned ? 'Unpin (allow reclustering to replace it)' : 'Pin (preserve across reclustering)'}
        >
          {isPinned ? '📌 Pinned' : 'Pin'}
        </button>
      )}
    </div>
  )
}

function TopicDetail({ topicId, onBack, onAuthorClick, onSearch }) {
  const [topic, setTopic] = useState(null)
  const [articles, setArticles] = useState([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [order, setOrder] = useState('distance')
  const [loading, setLoading] = useState(true)
  const PAGE_SIZE = 20

  useEffect(() => {
    setLoading(true)
    Promise.all([
      fetch(`/api/topics/${topicId}`).then(r => r.json()),
      fetch(`/api/topics/${topicId}/articles?page=${page}&page_size=${PAGE_SIZE}&order=${order}`).then(r => r.json()),
    ]).then(([t, a]) => {
      setTopic(t)
      setArticles(a.articles || [])
      setTotal(a.total || 0)
    }).finally(() => setLoading(false))
  }, [topicId, page, order])

  if (loading) return <div className="entity-home-loading">Loading topic…</div>
  if (!topic) return <div className="banner error">Topic not found</div>

  const totalPages = Math.ceil(total / PAGE_SIZE)

  return (
    <div className="topic-detail-page">
      <button className="btn-back" onClick={onBack}>← Topics</button>
      <h2 className="topic-detail-title">{topic.name}</h2>
      {topic.description && <p className="topic-detail-desc">{topic.description}</p>}

      <div className="topic-detail-meta">
        <span>{topic.article_count} articles</span>
        {topic.top_hashtags?.length > 0 && (
          <span>{topic.top_hashtags.map(h => `#${h}`).join(' ')}</span>
        )}
      </div>

      <div className="topic-detail-columns">
        <div className="topic-articles-col">
          <div className="topic-articles-toolbar">
            <select
              value={order}
              onChange={e => { setOrder(e.target.value); setPage(1) }}
              className="entity-home-sort-select"
            >
              <option value="distance">Most representative</option>
              <option value="date">Most recent</option>
            </select>
          </div>
          {articles.map(a => {
            let tweet = null
            try { tweet = a.tweet_meta ? JSON.parse(a.tweet_meta) : null } catch {}
            if (!tweet) return null
            return (
              <TweetCard
                key={a.id}
                tweet={tweet}
                articleId={a.id}
                sourceUrl={a.url}
                onSearch={onSearch}
                onAuthorClick={onAuthorClick}
              />
            )
          })}
          {totalPages > 1 && (
            <div className="entity-home-pagination">
              <button disabled={page <= 1} onClick={() => setPage(p => p - 1)}>← Prev</button>
              <span>{page} / {totalPages}</span>
              <button disabled={page >= totalPages} onClick={() => setPage(p => p + 1)}>Next →</button>
            </div>
          )}
        </div>

        <div className="topic-sidebar-col">
          {topic.top_entities?.length > 0 && (
            <div className="topic-sidebar-section">
              <h4 className="topic-sidebar-title">Top entities</h4>
              {topic.top_entities.map((e, i) => (
                <div key={i} className="topic-entity-row">
                  <span className="entity-home-kind-badge">{e.kind}</span>
                  <span className="topic-entity-name">{e.name}</span>
                  <span className="topic-entity-count">{e.cnt}</span>
                </div>
              ))}
            </div>
          )}
          <ActivitySparkline arc={topic.activity_arc} />
        </div>
      </div>
    </div>
  )
}

export default function TopicsPage({ onAuthorClick, onSearch }) {
  const [topics, setTopics] = useState(null)
  const [order, setOrder] = useState('size')
  const [selectedTopic, setSelectedTopic] = useState(null)
  const [loading, setLoading] = useState(true)
  const [reclustering, setReclustering] = useState(false)
  const [reclusterMsg, setReclusterMsg] = useState(null)

  function loadTopics() {
    setLoading(true)
    fetch(`/api/topics?order=${order}`)
      .then(r => r.json())
      .then(data => { setTopics(data); setLoading(false) })
      .catch(() => { setTopics([]); setLoading(false) })
  }

  useEffect(() => { loadTopics() }, [order]) // eslint-disable-line react-hooks/exhaustive-deps

  async function handleRecluster() {
    setReclustering(true)
    setReclusterMsg(null)
    try {
      const r = await fetch('/api/topics/recluster', { method: 'POST' })
      const d = await r.json()
      if (d.skipped) {
        setReclusterMsg(`Skipped: ${d.reason}`)
      } else {
        setReclusterMsg(`Created ${d.topics} topics covering ${d.articles} articles`)
        loadTopics()
      }
    } catch (err) {
      setReclusterMsg(`Error: ${err.message}`)
    } finally {
      setReclustering(false)
    }
  }

  if (selectedTopic) {
    return (
      <TopicDetail
        topicId={selectedTopic}
        onBack={() => setSelectedTopic(null)}
        onAuthorClick={onAuthorClick}
        onSearch={onSearch}
      />
    )
  }

  return (
    <div className="topics-page">
      <div className="topics-topbar">
        <h2 className="topics-title">Archive topics</h2>
        <select
          className="entity-home-sort-select"
          value={order}
          onChange={e => setOrder(e.target.value)}
        >
          <option value="size">Largest first</option>
          <option value="recent">Recently generated</option>
        </select>
        <button
          className="btn-ghost topics-recluster-btn"
          onClick={handleRecluster}
          disabled={reclustering}
          title="Re-run topic clustering over all article vectors"
        >
          {reclustering ? '⏳ Clustering…' : '🔄 Recluster'}
        </button>
      </div>
      {reclusterMsg && (
        <div className="banner topics-recluster-msg">{reclusterMsg}</div>
      )}

      {loading && <div className="entity-home-loading">Loading topics…</div>}
      {!loading && topics?.length === 0 && (
        <div className="entity-home-empty text-muted">
          No topics yet. Click <strong>🔄 Recluster</strong> to run topic clustering, or enable
          <code>BACKGROUND_CLUSTERING=true</code>.
        </div>
      )}
      {!loading && topics && topics.length > 0 && (
        <div className="topics-grid">
          {topics.map(t => (
            <TopicCard
              key={t.id}
              topic={t}
              onClick={setSelectedTopic}
              onPinToggle={async (id, nextPinned) => {
                const r = await fetch(`/api/topics/${id}`, {
                  method: 'PATCH',
                  headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify({ pinned: nextPinned }),
                })
                if (r.ok) {
                  setTopics(prev => prev.map(x => x.id === id ? { ...x, pinned: nextPinned } : x))
                }
              }}
            />
          ))}
        </div>
      )}
    </div>
  )
}
