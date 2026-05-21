import React, { useEffect, useState } from 'react'
import MessageBubble from './MessageBubble.jsx'
import LinkPreview, { TweetCard } from './LinkPreview.jsx'
import AddToBinButton from './AddToBinButton.jsx'

const SENTIMENTS = ['positive', 'negative', 'neutral']

const MATCH_SOURCE_LABELS = {
  tweet_body: 'Matched in tweet text',
  tweet_meta: 'Matched in tweet author / hashtag / mention',
  tweet_title: null,
  article_body: 'Matched in article body',
  article_title: null,
  article_summary: 'Matched in AI summary',
}

function ArticleCard({ hit, onOpenPreview, query }) {
  const matchLabel = MATCH_SOURCE_LABELS[hit.match_source] ?? null

  // Attempt to parse tweet_meta — if present, render a full TweetCard
  let tweetMeta = null
  if (hit.tweet_meta) {
    try { tweetMeta = JSON.parse(hit.tweet_meta) } catch {}
  }

  if (tweetMeta) {
    return (
      <div className="article-card article-card-tweet">
        {matchLabel && <div className="article-card-meta-label">{matchLabel}</div>}
        <TweetCard
          tweet={tweetMeta}
          sourceUrl={hit.url}
          articleId={hit.article_id}
          highlightQuery={query}
          onOpen={() => onOpenPreview(hit.url)}
        />
      </div>
    )
  }

  return (
    <div className="article-card">
      <div className="article-card-title">{hit.title || hit.url}</div>
      {matchLabel && (
        <div className="article-card-meta-label">{matchLabel}</div>
      )}
      {hit.snippet && (
        <div
          className="article-card-snippet"
          dangerouslySetInnerHTML={{ __html: hit.snippet }}
        />
      )}
      <div className="article-card-actions">
        {hit.chat_name && <span className="article-card-chat">{hit.chat_name}</span>}
        <button className="btn-link" onClick={() => onOpenPreview(hit.url)}>Open preview</button>
        <a className="btn-link" href={hit.url} target="_blank" rel="noreferrer">↗</a>
        {hit.article_id && <AddToBinButton targetKind="article" targetId={hit.article_id} />}
      </div>
    </div>
  )
}

const EMPTY_FILTERS = {
  from_date: '', to_date: '', author: '', hashtag: '', mention: '',
  min_likes: '', min_retweets: '', has_media: false, sentiment: '', sender: '',
}

function hasActiveFilters(f) {
  return Object.entries(f).some(([, v]) => v !== '' && v !== false)
}

export default function SearchResults({ results, query, onSelectChat, onSearch, onAuthorClick, onHashtagClick, onMentionClick }) {
  const [kindFilter, setKindFilter] = useState('all')
  const [previewUrl, setPreviewUrl] = useState(null)
  const [showFilters, setShowFilters] = useState(false)
  const [filters, setFilters] = useState(EMPTY_FILTERS)
  const [filteredResults, setFilteredResults] = useState(null)
  const [filtering, setFiltering] = useState(false)

  const active = hasActiveFilters(filters)

  useEffect(() => {
    if (!active) { setFilteredResults(null); return }
    setFiltering(true)
    const params = new URLSearchParams({ q: query })
    if (filters.from_date) params.set('from_date', filters.from_date)
    if (filters.to_date) params.set('to_date', filters.to_date)
    if (filters.author) params.set('author', filters.author)
    if (filters.hashtag) params.set('hashtag', filters.hashtag)
    if (filters.mention) params.set('mention', filters.mention)
    if (filters.min_likes) params.set('min_likes', filters.min_likes)
    if (filters.min_retweets) params.set('min_retweets', filters.min_retweets)
    if (filters.has_media) params.set('has_media', '1')
    if (filters.sentiment) params.set('sentiment', filters.sentiment)
    if (filters.sender) params.set('sender', filters.sender)
    fetch(`/api/search?${params}`)
      .then(r => r.ok ? r.json() : null)
      .then(data => setFilteredResults(data ? data.results : []))
      .catch(() => setFilteredResults([]))
      .finally(() => setFiltering(false))
  }, [query, active, filters.from_date, filters.to_date, filters.author, filters.hashtag,
    filters.mention, filters.min_likes, filters.min_retweets, filters.has_media,
    filters.sentiment, filters.sender])

  function setFilter(key, value) {
    setFilters(prev => ({ ...prev, [key]: value }))
  }

  function clearFilters() {
    setFilters(EMPTY_FILTERS)
  }

  const activeResults = active ? (filteredResults ?? []) : results

  if (!results || results.length === 0) {
    return <div className="thread-empty">No results for "{query}".</div>
  }

  const filtered = kindFilter === 'all' ? activeResults : activeResults.filter(r => r.kind === kindFilter)

  const msgGroups = {}
  const articleHits = []
  for (const r of filtered) {
    if (r.kind === 'article') {
      articleHits.push(r)
    } else {
      const cid = r.chat_id || 'unknown'
      if (!msgGroups[cid]) msgGroups[cid] = { name: r.chat_name, entries: [] }
      msgGroups[cid].entries.push(r.entry)
    }
  }

  return (
    <div className="search-results-wrap">
      <div className="search-results-header">
        <h2 className="thread-title">{filtered.length} result{filtered.length !== 1 ? 's' : ''} for "{query}"</h2>
        <div className="search-results-header-right">
          <div className="kind-filter">
            {['all', 'message', 'article'].map(k => (
              <button
                key={k}
                className={`kind-btn${kindFilter === k ? ' active' : ''}`}
                onClick={() => setKindFilter(k)}
              >{k}</button>
            ))}
          </div>
          <a
            className="btn-secondary btn-sm"
            href={`/api/export/keyword?q=${encodeURIComponent(query)}&format=md`}
            target="_blank"
            rel="noreferrer"
            title="Export results as Markdown"
          >↓ Export</a>
          <button
            className={`filter-toggle-btn${showFilters ? ' active' : ''}${active ? ' has-filters' : ''}`}
            onClick={() => setShowFilters(s => !s)}
            title="Search filters"
          >⚙ Filters{active ? ' ●' : ''}</button>
        </div>
      </div>

      {showFilters && (
        <div className="search-filter-panel">
          <div className="filter-row">
            <label className="filter-label">
              From
              <input type="date" className="filter-input" value={filters.from_date}
                onChange={e => setFilter('from_date', e.target.value)} />
            </label>
            <label className="filter-label">
              To
              <input type="date" className="filter-input" value={filters.to_date}
                onChange={e => setFilter('to_date', e.target.value)} />
            </label>
            <label className="filter-label">
              Sender
              <input type="text" className="filter-input" placeholder="name / phone"
                value={filters.sender} onChange={e => setFilter('sender', e.target.value)} />
            </label>
          </div>
          <div className="filter-row">
            <label className="filter-label">
              Author handle
              <input type="text" className="filter-input" placeholder="@handle"
                value={filters.author} onChange={e => setFilter('author', e.target.value.replace(/^@/, ''))} />
            </label>
            <label className="filter-label">
              Hashtag
              <input type="text" className="filter-input" placeholder="#tag"
                value={filters.hashtag} onChange={e => setFilter('hashtag', e.target.value.replace(/^#/, ''))} />
            </label>
            <label className="filter-label">
              Mention
              <input type="text" className="filter-input" placeholder="@handle"
                value={filters.mention} onChange={e => setFilter('mention', e.target.value.replace(/^@/, ''))} />
            </label>
          </div>
          <div className="filter-row filter-row-sm">
            <label className="filter-label">
              Min likes
              <input type="number" className="filter-input filter-input-num" min="0"
                value={filters.min_likes} onChange={e => setFilter('min_likes', e.target.value)} />
            </label>
            <label className="filter-label">
              Min retweets
              <input type="number" className="filter-input filter-input-num" min="0"
                value={filters.min_retweets} onChange={e => setFilter('min_retweets', e.target.value)} />
            </label>
            <label className="filter-label filter-check-label">
              <input type="checkbox" checked={filters.has_media}
                onChange={e => setFilter('has_media', e.target.checked)} />
              Has media
            </label>
          </div>
          <div className="filter-row filter-row-sentiment">
            <span className="filter-label-text">Sentiment</span>
            <div className="filter-sentiment-pills">
              {SENTIMENTS.map(s => (
                <button key={s}
                  className={`sentiment-pill sentiment-${s}${filters.sentiment === s ? ' active' : ''}`}
                  onClick={() => setFilter('sentiment', filters.sentiment === s ? '' : s)}
                >{s}</button>
              ))}
            </div>
            {active && (
              <button className="filter-clear-btn" onClick={clearFilters}>Clear all</button>
            )}
            {filtering && <span className="filter-loading">…</span>}
          </div>
        </div>
      )}

      <div className={`search-results-body${previewUrl ? ' with-drawer' : ''}`}>
        <div className="search-results">
          {articleHits.length > 0 && (
            <section className="search-group">
              <div className="search-group-name">Articles</div>
              {articleHits.map((hit, i) => (
                <ArticleCard key={i} hit={hit} onOpenPreview={setPreviewUrl} query={query} />
              ))}
            </section>
          )}

          {Object.entries(msgGroups).map(([chatId, { name, entries }]) => (
            <section key={chatId} className="search-group">
              <button className="search-group-name" onClick={() => onSelectChat(chatId, name)}>
                {name} ({entries.length})
              </button>
              {entries.map((entry, i) => (
                <MessageBubble key={i} entry={entry} highlightQuery={query} />
              ))}
            </section>
          ))}

          {active && filtered.length === 0 && !filtering && (
            <div className="thread-empty">No results match the active filters.</div>
          )}
        </div>

        {previewUrl && (
          <div className="preview-drawer">
            <LinkPreview url={previewUrl} onClose={() => setPreviewUrl(null)} onSearch={onSearch} onAuthorClick={onAuthorClick} onHashtagClick={onHashtagClick} onMentionClick={onMentionClick} />
          </div>
        )}
      </div>
    </div>
  )
}
