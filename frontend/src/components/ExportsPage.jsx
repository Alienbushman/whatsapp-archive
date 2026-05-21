import React, { useState, useCallback } from 'react'

const SCOPES = ['keyword', 'author', 'hashtag', 'mention', 'ticker', 'entity', 'timerange']

const SCOPE_LABEL = {
  keyword: 'Keyword',
  author: 'Author',
  hashtag: 'Hashtag',
  mention: 'Mention',
  ticker: 'Ticker',
  entity: 'Entity',
  timerange: 'Time range',
}

const SCOPE_PLACEHOLDER = {
  keyword: 'e.g. gold etf — or leave blank for all',
  author: 'e.g. charliebilello',
  hashtag: 'e.g. Gold (without #)',
  mention: 'e.g. federalreserve (without @)',
  ticker: 'e.g. SPY (without $)',
  entity: 'e.g. Federal Reserve',
  timerange: '',
}

const TEMPLATES = [
  { id: 'raw_dump', label: 'Raw dump', desc: 'Plain export, no instructions' },
  { id: 'briefing', label: 'Briefing', desc: 'Prompt for a concise research briefing' },
  { id: 'pros_cons', label: 'Pros / Cons', desc: 'Prompt for structured pros-and-cons analysis' },
  { id: 'sentiment_timeline', label: 'Sentiment timeline', desc: 'Prompt for chronological sentiment analysis' },
]

export default function ExportsPage() {
  const [scope, setScope] = useState('keyword')
  const [q, setQ] = useState('')
  const [template, setTemplate] = useState('raw_dump')
  const [fromDate, setFromDate] = useState('')
  const [toDate, setToDate] = useState('')
  const [includeArticles, setIncludeArticles] = useState(true)
  const [fuzzy, setFuzzy] = useState(false)

  const [preview, setPreview] = useState(null) // {markdown, item_count, suggested_filename, template_used}
  const [previewLoading, setPreviewLoading] = useState(false)
  const [previewError, setPreviewError] = useState(null)
  const [rawMode, setRawMode] = useState(false)
  const [copied, setCopied] = useState(false)

  const needsValue = scope !== 'timerange'

  function buildDownloadUrl(fmt) {
    const params = new URLSearchParams({ format: fmt })
    if (fromDate) params.set('from_date', fromDate)
    if (toDate) params.set('to_date', toDate)
    if (!includeArticles) params.set('include_articles', '0')

    if (scope === 'keyword') {
      if (q.trim()) params.set('q', q.trim())
      if (fuzzy) params.set('fuzzy', '1')
      return `/api/export/keyword?${params}`
    }
    if (scope === 'timerange') {
      return `/api/export/timerange?${params}`
    }
    if (scope === 'entity') {
      const val = q.trim()
      if (!val) return null
      return `/api/export/entity/${encodeURIComponent(val)}?${params}`
    }
    const val = q.trim()
    if (!val) return null
    return `/api/export/${scope}/${encodeURIComponent(val)}?${params}`
  }

  const handlePreview = useCallback(async () => {
    setPreviewLoading(true)
    setPreviewError(null)
    setPreview(null)
    try {
      const resp = await fetch('/api/export/preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          scope,
          q: q.trim(),
          template,
          from_date: fromDate,
          to_date: toDate,
          include_articles: includeArticles,
          fuzzy,
        }),
      })
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}))
        throw new Error(err.detail || `HTTP ${resp.status}`)
      }
      setPreview(await resp.json())
    } catch (e) {
      setPreviewError(e.message)
    } finally {
      setPreviewLoading(false)
    }
  }, [scope, q, template, fromDate, toDate, includeArticles, fuzzy])

  function handleDownload(fmt) {
    const url = buildDownloadUrl(fmt)
    if (!url) return
    const a = document.createElement('a')
    a.href = url
    a.target = '_blank'
    a.click()
  }

  function handleCopy() {
    if (!preview?.markdown) return
    navigator.clipboard.writeText(preview.markdown).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }

  function handleSendToChatbot() {
    if (!preview?.markdown) return
    navigator.clipboard.writeText(preview.markdown).then(() => {
      alert('Copied to clipboard. Paste into your chatbot of choice.')
    })
  }

  const canPreview = scope === 'timerange' || !!q.trim()

  return (
    <div className="exports-page">
      <h2 className="exports-title">📤 Exports</h2>
      <p className="exports-subtitle text-muted">Build a context bundle from messages and articles, then download or copy it for use in a chatbot.</p>

      <div className="exports-builder">
        <div className="exports-controls">
          <div className="form-label">
            Source
            <div className="export-scope-row">
              {SCOPES.map(s => (
                <button
                  key={s}
                  className={`export-scope-btn${scope === s ? ' active' : ''}`}
                  onClick={() => { setScope(s); setQ('') }}
                >
                  {SCOPE_LABEL[s]}
                </button>
              ))}
            </div>
          </div>

          {needsValue && (
            <label className="form-label">
              {SCOPE_LABEL[scope]}
              {scope === 'keyword' && <span className="form-optional"> (leave blank for all messages)</span>}
              <input
                className="form-input"
                value={q}
                onChange={e => setQ(e.target.value)}
                placeholder={SCOPE_PLACEHOLDER[scope]}
              />
            </label>
          )}

          <div className="form-row">
            <label className="form-label">From
              <input className="form-input" type="date" value={fromDate} onChange={e => setFromDate(e.target.value)} />
            </label>
            <label className="form-label">To
              <input className="form-input" type="date" value={toDate} onChange={e => setToDate(e.target.value)} />
            </label>
          </div>

          <div className="form-label">
            Template
            <div className="exports-template-grid">
              {TEMPLATES.map(t => (
                <label key={t.id} className={`exports-template-card${template === t.id ? ' active' : ''}`}>
                  <input
                    type="radio"
                    name="template"
                    value={t.id}
                    checked={template === t.id}
                    onChange={() => setTemplate(t.id)}
                  />
                  <span className="exports-template-label">{t.label}</span>
                  <span className="exports-template-desc">{t.desc}</span>
                </label>
              ))}
            </div>
          </div>

          <div className="exports-check-row">
            <label className="form-check">
              <input type="checkbox" checked={includeArticles} onChange={e => setIncludeArticles(e.target.checked)} />
              Include scraped articles
            </label>
            {scope === 'keyword' && (
              <label className="form-check">
                <input type="checkbox" checked={fuzzy} onChange={e => setFuzzy(e.target.checked)} />
                Fuzzy match
              </label>
            )}
          </div>

          <div className="exports-action-row">
            <button
              className="btn-primary"
              onClick={handlePreview}
              disabled={previewLoading || !canPreview}
            >
              {previewLoading ? 'Building…' : 'Preview'}
            </button>
          </div>
        </div>

        <div className="exports-preview-pane">
          {!preview && !previewLoading && !previewError && (
            <div className="exports-preview-empty text-muted">
              Configure a source and click Preview to generate a context bundle.
            </div>
          )}

          {previewLoading && <div className="loading">Building bundle…</div>}

          {previewError && (
            <div className="banner error">Error: {previewError}</div>
          )}

          {preview && (
            <>
              <div className="exports-preview-meta">
                <span className="exports-preview-count">{preview.item_count} item{preview.item_count !== 1 ? 's' : ''}</span>
                <span className="exports-preview-filename">{preview.suggested_filename}</span>
                <div className="exports-preview-actions">
                  <button className="btn-secondary btn-sm" onClick={() => setRawMode(v => !v)}>
                    {rawMode ? 'Rendered' : 'Raw'}
                  </button>
                  <button className="btn-secondary btn-sm" onClick={handleCopy}>
                    {copied ? 'Copied!' : 'Copy'}
                  </button>
                  <button className="btn-secondary btn-sm" onClick={() => handleDownload('md')}>↓ MD</button>
                  <button className="btn-secondary btn-sm" onClick={() => handleDownload('json')}>↓ JSON</button>
                  <button className="btn-secondary btn-sm" onClick={handleSendToChatbot}>
                    🤖 Send to chatbot
                  </button>
                </div>
              </div>

              {rawMode ? (
                <pre className="exports-preview-raw">{preview.markdown}</pre>
              ) : (
                <div className="exports-preview-rendered">
                  <MarkdownPreview text={preview.markdown} />
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}

function MarkdownPreview({ text }) {
  const lines = text.split('\n')
  const elements = []
  let i = 0
  while (i < lines.length) {
    const line = lines[i]
    if (line.startsWith('# ')) {
      elements.push(<h1 key={i}>{line.slice(2)}</h1>)
    } else if (line.startsWith('## ')) {
      elements.push(<h2 key={i}>{line.slice(3)}</h2>)
    } else if (line.startsWith('### ')) {
      elements.push(<h3 key={i}>{line.slice(4)}</h3>)
    } else if (line.startsWith('> ')) {
      elements.push(<blockquote key={i}>{line.slice(2)}</blockquote>)
    } else if (line === '---') {
      elements.push(<hr key={i} />)
    } else if (line.startsWith('_') && line.endsWith('_')) {
      elements.push(<p key={i}><em>{line.slice(1, -1)}</em></p>)
    } else if (line.trim() === '') {
      // skip blank
    } else {
      elements.push(<p key={i}>{line}</p>)
    }
    i++
  }
  return <div className="md-preview">{elements}</div>
}
