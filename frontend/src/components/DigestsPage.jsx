import React, { useEffect, useState } from 'react'
import LinkPreview, { TweetCard } from './LinkPreview.jsx'

function parseBodySegments(body_md) {
  const parts = []
  // Matches [source ↗](url) patterns emitted by digest.py
  const re = /\[source ↗\]\(([^)]+)\)/g
  let lastIndex = 0
  let match
  while ((match = re.exec(body_md)) !== null) {
    if (match.index > lastIndex) {
      parts.push({ type: 'text', content: body_md.slice(lastIndex, match.index) })
    }
    parts.push({ type: 'source', url: match[1] })
    lastIndex = match.index + match[0].length
  }
  if (lastIndex < body_md.length) {
    parts.push({ type: 'text', content: body_md.slice(lastIndex) })
  }
  return parts.length ? parts : [{ type: 'text', content: body_md }]
}

function DigestDetail({ digest }) {
  if (!digest) return null
  const citations = Array.isArray(digest.citations) ? digest.citations : []
  const citationsByUrl = Object.fromEntries(citations.map(c => [c.url, c]))
  const [skimMode, setSkimMode] = React.useState(false)
  const [previewUrl, setPreviewUrl] = useState(null)

  const segments = parseBodySegments(digest.body_md || '')

  return (
    <div className="digest-detail">
      <div className="digest-detail-meta">
        <span className="digest-period-badge digest-period-{digest.period}">{digest.period}</span>
        <span className="digest-date-range">{digest.period_start} → {digest.period_end}</span>
        {digest.chat_id && <span className="digest-chat-id">chat: {digest.chat_id}</span>}
        <span className="digest-generated-at">{digest.generated_at?.slice(0, 16)} UTC</span>
        <button
          className={`digest-skim-toggle${skimMode ? ' active' : ''}`}
          onClick={() => setSkimMode(m => !m)}
        >{skimMode ? 'Full view' : 'Skim mode'}</button>
      </div>

      <div className="digest-body">
        {segments.map((seg, i) => {
          if (seg.type === 'text') {
            return <div key={i} dangerouslySetInnerHTML={{ __html: _mdToHtml(seg.content) }} />
          }
          const c = citationsByUrl[seg.url]
          if (c && c.tweet_text) {
            const tweet = {
              author_handle: c.author,
              author_name: c.author,
              text: skimMode ? (c.tweet_text.slice(0, 100) + (c.tweet_text.length > 100 ? '…' : '')) : c.tweet_text,
              favorite_count: c.likes,
              hashtags: c.hashtags || [],
              created_at: c.date,
            }
            return (
              <div key={i} className="digest-source-card">
                <TweetCard
                  tweet={tweet}
                  sourceUrl={seg.url}
                  articleId={c.article_id ?? null}
                  compact={true}
                  onOpen={() => setPreviewUrl(seg.url)}
                />
              </div>
            )
          }
          return (
            <a key={i} href={seg.url} target="_blank" rel="noreferrer" className="digest-source-link">
              source ↗
            </a>
          )
        })}
      </div>

      {previewUrl && (
        <div className="digest-preview-overlay" onClick={() => setPreviewUrl(null)}>
          <div className="digest-preview-panel" onClick={e => e.stopPropagation()}>
            <LinkPreview url={previewUrl} onClose={() => setPreviewUrl(null)} />
          </div>
        </div>
      )}
    </div>
  )
}

function _mdToHtml(md) {
  if (!md) return ''
  return md
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/^#{1} (.+)$/gm, '<h2>$1</h2>')
    .replace(/^#{2,3} (.+)$/gm, '<h3>$1</h3>')
    .replace(/^- (.+)$/gm, '<li>$1</li>')
    .replace(/(<li>.*<\/li>)/s, '<ul>$1</ul>')
    .replace(/\n\n/g, '</p><p>')
    .replace(/^(?!<[hul])(.+)$/gm, '$1')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    .replace(/_(.+?)_/g, '<em>$1</em>')
}

function GenerateForm({ onGenerated }) {
  const [period, setPeriod] = useState('daily')
  const [endDate, setEndDate] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  async function handleSubmit(e) {
    e.preventDefault()
    setLoading(true)
    setError('')
    try {
      const body = { period }
      if (endDate) body.end_date = endDate
      const r = await fetch('/api/digests/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!r.ok) {
        const d = await r.json()
        setError(d.detail || 'Generation failed')
        return
      }
      const digest = await r.json()
      onGenerated(digest)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <form className="digest-generate-form" onSubmit={handleSubmit}>
      <select className="digest-period-select" value={period} onChange={e => setPeriod(e.target.value)}>
        <option value="daily">Daily</option>
        <option value="weekly">Weekly</option>
      </select>
      <input
        type="date"
        className="digest-date-input"
        value={endDate}
        onChange={e => setEndDate(e.target.value)}
        placeholder="End date (optional)"
      />
      <button className="btn-primary" type="submit" disabled={loading}>
        {loading ? 'Generating…' : 'Generate now'}
      </button>
      {error && <span className="digest-generate-error">{error}</span>}
    </form>
  )
}

export default function DigestsPage() {
  const [digests, setDigests] = useState(null)
  const [selected, setSelected] = useState(null)

  useEffect(() => {
    fetch('/api/digests?limit=50')
      .then(r => r.ok ? r.json() : [])
      .then(data => {
        setDigests(data)
        if (data.length > 0) setSelected(data[0])
      })
      .catch(() => setDigests([]))
  }, [])

  function handleGenerated(digest) {
    setDigests(prev => {
      const filtered = (prev || []).filter(d => d.id !== digest.id)
      return [digest, ...filtered]
    })
    setSelected(digest)
  }

  return (
    <div className="digests-page">
      <div className="digests-sidebar">
        <h2 className="digests-title">📰 Digests</h2>
        <GenerateForm onGenerated={handleGenerated} />

        {digests === null && <div className="loading">Loading…</div>}
        {digests && digests.length === 0 && (
          <p className="text-muted">No digests yet. Generate one above.</p>
        )}
        {digests && digests.map(d => (
          <button
            key={d.id}
            className={`digests-list-item${selected?.id === d.id ? ' active' : ''}`}
            onClick={() => setSelected(d)}
          >
            <span className="digest-list-period">{d.period}</span>
            <span className="digest-list-range">{d.period_start}</span>
          </button>
        ))}
      </div>

      <div className="digests-content">
        {!selected && (
          <div className="digests-empty">
            <p className="text-muted">Select a digest to read it, or generate a new one.</p>
          </div>
        )}
        {selected && <DigestDetail digest={selected} />}
      </div>
    </div>
  )
}
