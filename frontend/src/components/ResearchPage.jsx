import React, { useEffect, useState, useCallback } from 'react'
import TimeseriesChart from './TimeseriesChart.jsx'
import NarrativeGraphView from './NarrativeGraphView.jsx'

// ── Helpers ───────────────────────────────────────────────────────────────────

function sentimentBadge(s) {
  const cls = s === 'bullish' ? 'sentiment-bullish' : s === 'bearish' ? 'sentiment-bearish' : 'sentiment-neutral'
  return <span className={`sentiment-badge ${cls}`}>{s}</span>
}

function engagementLabel(n) {
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`
  return String(n)
}

// ── Article drawer ────────────────────────────────────────────────────────────

function ArticleChip({ article, onClick }) {
  return (
    <button
      className="research-article-chip"
      onClick={() => onClick(article)}
      title={article.summary || article.title || article.url}
    >
      <span className={`chip-dot sentiment-dot-${article.sentiment || 'neutral'}`} />
      {article.title || article.url?.split('/').pop() || 'Article'}
    </button>
  )
}

function ArticleDrawer({ article, onClose }) {
  if (!article) return null
  return (
    <div className="research-drawer-overlay" onClick={onClose}>
      <div className="research-drawer" onClick={e => e.stopPropagation()}>
        <div className="research-drawer-header">
          <h3 className="research-drawer-title">{article.title || 'Article'}</h3>
          <button className="research-drawer-close" onClick={onClose}>✕</button>
        </div>
        <div className="research-drawer-meta">
          {article.author_handle && <span>@{article.author_handle}</span>}
          {article.published_at && <span>{article.published_at?.slice(0, 10)}</span>}
          {article.sentiment && sentimentBadge(article.sentiment)}
          {article.favorite_count > 0 && (
            <span className="research-drawer-engagement">♥ {engagementLabel(article.favorite_count)}</span>
          )}
        </div>
        {article.summary && <p className="research-drawer-summary">{article.summary}</p>}
        <a
          className="research-drawer-link"
          href={article.url}
          target="_blank"
          rel="noopener noreferrer"
        >
          Open source ↗
        </a>
      </div>
    </div>
  )
}

// ── Claim bucket card ─────────────────────────────────────────────────────────

function ClaimBucket({ bucket, onArticleClick }) {
  const [expanded, setExpanded] = useState(false)
  const bullish = bucket.articles?.filter(a => a.sentiment === 'bullish').length || 0
  const bearish = bucket.articles?.filter(a => a.sentiment === 'bearish').length || 0

  return (
    <div className="research-bucket">
      <div className="research-bucket-header" onClick={() => setExpanded(e => !e)}>
        <span className="research-bucket-theme">{bucket.theme}</span>
        <span className="research-bucket-count">{bucket.article_ids?.length || 0} articles</span>
        <span className="research-bucket-expand">{expanded ? '▲' : '▼'}</span>
      </div>

      {bucket.sample_quotes?.length > 0 && (
        <div className="research-bucket-quotes">
          {bucket.sample_quotes.map((q, i) => (
            <blockquote key={i} className="research-bucket-quote">"{q}"</blockquote>
          ))}
        </div>
      )}

      {expanded && (
        <div className="research-bucket-articles">
          {bucket.articles?.map(a => (
            <ArticleChip key={a.id} article={a} onClick={onArticleClick} />
          ))}
        </div>
      )}

      {(bullish > 0 || bearish > 0) && bullish > 0 && bearish > 0 && (
        <div className="research-contradiction">
          <span className="research-contradiction-label">⚡ Contradictions:</span>
          <span className="sentiment-bullish">{bullish} bullish</span>
          {' vs '}
          <span className="sentiment-bearish">{bearish} bearish</span>
          {' in this cluster'}
        </div>
      )}
    </div>
  )
}

// ── Stats tiles ───────────────────────────────────────────────────────────────

function StatTile({ label, value }) {
  return (
    <div className="research-stat-tile">
      <span className="research-stat-value">{value ?? '—'}</span>
      <span className="research-stat-label">{label}</span>
    </div>
  )
}

// ── Deep-dive view ────────────────────────────────────────────────────────────

function DeepDiveView({ entityName }) {
  const [report, setReport] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  async function generate(force = false) {
    setLoading(true)
    setError(null)
    try {
      const url = `/api/research/deepdive/${encodeURIComponent(entityName)}${force ? '?force_refresh=true' : ''}`
      const r = await fetch(url, { method: 'POST' })
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        throw new Error(d.detail || `HTTP ${r.status}`)
      }
      setReport(await r.json())
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    // Try cached first
    fetch(`/api/research/deepdive/${encodeURIComponent(entityName)}`)
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d) setReport(d) })
      .catch(() => {})
  }, [entityName])

  if (loading) return <div className="deepdive-loading">Generating deep-dive report… (5 LLM passes)</div>

  return (
    <div className="deepdive-panel">
      <div className="deepdive-header">
        <h3 className="deepdive-title">Deep-dive report</h3>
        <div className="deepdive-actions">
          {report && (
            <span className="deepdive-age">
              Generated {report.generated_at?.slice(0, 10)} · {report.article_count} articles
            </span>
          )}
          <button className="deepdive-gen-btn" onClick={() => generate(false)} disabled={loading}>
            {report ? '↻ Refresh' : 'Generate'}
          </button>
          {report && (
            <button className="deepdive-gen-btn deepdive-force-btn" onClick={() => generate(true)} disabled={loading}>
              Force refresh
            </button>
          )}
        </div>
      </div>

      {error && <div className="deepdive-error banner error">{error}</div>}

      {report && (
        <div className="deepdive-sections">

          {/* Claims */}
          {report.sections?.claims?.length > 0 && (
            <div className="deepdive-section">
              <h4 className="deepdive-section-title">Key claims</h4>
              <ul className="deepdive-claims-list">
                {report.sections.claims.map((c, i) => (
                  <li key={i} className="deepdive-claim">
                    <span className="deepdive-claim-text">{c.claim}</span>
                    {c.mention_count > 1 && (
                      <span className="deepdive-claim-count">{c.mention_count}×</span>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Time arc */}
          {report.sections?.time_arc?.length > 0 && (
            <div className="deepdive-section">
              <h4 className="deepdive-section-title">Narrative timeline</h4>
              <ol className="deepdive-arc-list">
                {report.sections.time_arc.map((t, i) => (
                  <li key={i} className="deepdive-arc-item">
                    <span className="deepdive-arc-month">{t.month}</span>
                    <span className="deepdive-arc-event">{t.event}</span>
                  </li>
                ))}
              </ol>
            </div>
          )}

          {/* Authors */}
          {report.sections?.authors?.length > 0 && (
            <div className="deepdive-section">
              <h4 className="deepdive-section-title">Key voices</h4>
              <ul className="deepdive-authors-list">
                {report.sections.authors.map((a, i) => (
                  <li key={i} className="deepdive-author">
                    <span className="deepdive-author-handle">@{a.handle}</span>
                    <span className={`sentiment-badge sentiment-${a.stance}`}>{a.stance}</span>
                    {a.sample_quote && (
                      <blockquote className="deepdive-author-quote">"{a.sample_quote}"</blockquote>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Contradictions */}
          {report.sections?.contradictions?.length > 0 && (
            <div className="deepdive-section">
              <h4 className="deepdive-section-title">Contradictions</h4>
              <ul className="deepdive-contradictions-list">
                {report.sections.contradictions.map((c, i) => (
                  <li key={i} className="deepdive-contradiction">
                    <span className="deepdive-claim-a">{c.claim_a}</span>
                    <span className="deepdive-vs">vs</span>
                    <span className="deepdive-claim-b">{c.claim_b}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Open questions */}
          {report.sections?.open_questions?.length > 0 && (
            <div className="deepdive-section">
              <h4 className="deepdive-section-title">Open questions</h4>
              <ul className="deepdive-questions-list">
                {report.sections.open_questions.map((q, i) => (
                  <li key={i} className="deepdive-question">{q}</li>
                ))}
              </ul>
            </div>
          )}

        </div>
      )}

      {!report && !loading && !error && (
        <p className="text-muted deepdive-empty">
          Click <strong>Generate</strong> to run a structured 5-section analysis using local LLM.
        </p>
      )}
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function ResearchPage({ entityName, onEntityClick, onCompare }) {
  const [dossier, setDossier] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [refreshing, setRefreshing] = useState(false)
  const [drawerArticle, setDrawerArticle] = useState(null)
  const [showDeepDive, setShowDeepDive] = useState(false)
  const [showGraph, setShowGraph] = useState(false)

  const loadDossier = useCallback(async (forceRefresh = false) => {
    if (forceRefresh) setRefreshing(true)
    else setLoading(true)
    setError(null)
    try {
      const url = `/api/research/entity/${encodeURIComponent(entityName)}${forceRefresh ? '?refresh=true' : ''}`
      const r = await fetch(url)
      if (!r.ok) {
        const data = await r.json().catch(() => ({}))
        throw new Error(data.detail || `HTTP ${r.status}`)
      }
      setDossier(await r.json())
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }, [entityName])

  useEffect(() => { loadDossier() }, [loadDossier])

  if (loading) return <div className="research-loading">Building research workspace for <strong>{entityName}</strong>…</div>
  if (error) return <div className="research-error banner error">{error}</div>
  if (!dossier) return null

  const { entity, stats, narrative_arc, key_authors, related_entities, key_articles, claim_buckets } = dossier

  // Adapt narrative_arc to TimeseriesChart format (bullish→positive, bearish→negative)
  const arcChartData = (narrative_arc || []).map(d => ({
    bucket: d.month,
    positive: d.sentiment?.bullish || 0,
    negative: d.sentiment?.bearish || 0,
    neutral: d.sentiment?.neutral || 0,
  }))

  return (
    <div className="research-page">
      <ArticleDrawer article={drawerArticle} onClose={() => setDrawerArticle(null)} />

      {/* Header */}
      <div className="research-header">
        <div className="research-header-left">
          <span className="research-entity-kind">{entity.kind}</span>
          <h2 className="research-entity-name">{entity.name}</h2>
          {entity.aliases?.length > 0 && (
            <div className="research-aliases">
              {entity.aliases.map(a => <span key={a} className="research-alias-chip">{a}</span>)}
            </div>
          )}
        </div>
        <div className="research-header-actions">
          <button
            className="research-refresh-btn"
            onClick={() => loadDossier(true)}
            disabled={refreshing}
          >
            {refreshing ? 'Refreshing…' : '↻ Refresh'}
          </button>
          {onCompare && (
            <button
              className="research-compare-btn"
              onClick={() => onCompare([entityName])}
            >
              Compare…
            </button>
          )}
          <button
            className={`research-deepdive-btn${showGraph ? ' active' : ''}`}
            onClick={() => setShowGraph(v => !v)}
          >
            {showGraph ? 'Hide graph' : 'Entity graph ▾'}
          </button>
          <button
            className={`research-deepdive-btn${showDeepDive ? ' active' : ''}`}
            onClick={() => setShowDeepDive(v => !v)}
          >
            {showDeepDive ? 'Hide deep-dive' : 'Deep-dive ▾'}
          </button>
        </div>
      </div>

      {/* Stats tiles */}
      <div className="research-stats-row">
        <StatTile label="Articles" value={stats.article_count} />
        <StatTile label="Total engagement" value={engagementLabel(stats.total_engagement)} />
        <StatTile label="First seen" value={stats.first_seen?.slice(0, 10)} />
        <StatTile label="Last seen" value={stats.last_seen?.slice(0, 10)} />
      </div>

      {/* Three-column layout */}
      <div className="research-columns">

        {/* Left: Narrative arc */}
        <div className="research-col research-col-left">
          <h3 className="research-section-title">Narrative arc</h3>
          {arcChartData.length > 0 ? (
            <TimeseriesChart data={arcChartData} height={140} sentiment={true} label="" />
          ) : (
            <p className="text-muted">No dated articles yet.</p>
          )}
          <div className="research-sentiment-legend">
            <span className="legend-dot legend-dot-bullish" /> bullish
            <span className="legend-dot legend-dot-bearish" /> bearish
            <span className="legend-dot legend-dot-neutral" /> neutral
          </div>
        </div>

        {/* Middle: Claim buckets */}
        <div className="research-col research-col-mid">
          <h3 className="research-section-title">Claim buckets</h3>
          {claim_buckets?.length > 0 ? (
            claim_buckets.map((b, i) => (
              <ClaimBucket key={i} bucket={b} onArticleClick={setDrawerArticle} />
            ))
          ) : (
            <p className="text-muted">No enriched articles to cluster yet.</p>
          )}
        </div>

        {/* Right: Related entities + key authors */}
        <div className="research-col research-col-right">
          <h3 className="research-section-title">Key authors</h3>
          {key_authors?.length > 0 ? (
            <ul className="research-authors-list">
              {key_authors.map(a => (
                <li key={a.handle} className="research-author-row">
                  <button
                    className="research-author-handle"
                    onClick={() => onEntityClick?.('author', a.handle)}
                  >@{a.handle}</button>
                  <span className="research-author-count">{a.article_count} posts</span>
                  {sentimentBadge(a.avg_sentiment)}
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-muted">No author data.</p>
          )}

          <h3 className="research-section-title" style={{ marginTop: '1.5rem' }}>Related entities</h3>
          {related_entities?.length > 0 ? (
            <ul className="research-related-list">
              {related_entities.map(e => (
                <li key={e.name} className="research-related-row">
                  <button
                    className="research-related-name"
                    onClick={() => onEntityClick?.('entity', e.name)}
                  >{e.name}</button>
                  <span className="research-related-kind">{e.kind}</span>
                  <span className="research-related-count">{e.cooccurrence_count}×</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-muted">No related entities.</p>
          )}
        </div>
      </div>

      {/* Bottom: Key articles */}
      <div className="research-key-articles">
        <h3 className="research-section-title">Key articles</h3>
        <div className="research-key-articles-cols">
          <div className="research-key-articles-group">
            <h4 className="research-key-subtitle">Most engaged</h4>
            {key_articles?.most_engaged?.map(a => (
              <ArticleChip key={a.id} article={a} onClick={setDrawerArticle} />
            ))}
          </div>
          <div className="research-key-articles-group">
            <h4 className="research-key-subtitle">Most recent</h4>
            {key_articles?.most_recent?.map(a => (
              <ArticleChip key={a.id} article={a} onClick={setDrawerArticle} />
            ))}
          </div>
          {key_articles?.most_quoted?.length > 0 && (
            <div className="research-key-articles-group">
              <h4 className="research-key-subtitle">Most quoted</h4>
              {key_articles.most_quoted.map(a => (
                <ArticleChip key={a.id} article={a} onClick={setDrawerArticle} />
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Entity graph panel */}
      {showGraph && (
        <NarrativeGraphView
          entityName={entityName}
          onEntityClick={(kind, value) => onEntityClick && onEntityClick(kind, value)}
        />
      )}

      {/* Deep-dive panel */}
      {showDeepDive && <DeepDiveView entityName={entityName} />}
    </div>
  )
}
