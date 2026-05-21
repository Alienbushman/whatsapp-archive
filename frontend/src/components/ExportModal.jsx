import React, { useState } from 'react'

const SCOPES = ['keyword', 'author', 'hashtag', 'mention', 'ticker', 'timerange', 'chat']

const SCOPE_LABEL = {
  keyword: 'Keyword',
  author: 'Author',
  hashtag: 'Hashtag',
  mention: 'Mention',
  ticker: 'Ticker',
  timerange: 'Time range',
  chat: 'Chat',
}

export default function ExportModal({ chatId, defaultQuery = '', onClose, chats = [] }) {
  const [scope, setScope] = useState('keyword')
  const [q, setQ] = useState(defaultQuery)
  const [format, setFormat] = useState('md')
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [includeArticles, setIncludeArticles] = useState(true)
  const [useFuzzy, setUseFuzzy] = useState(false)
  const [loading, setLoading] = useState(false)

  function buildUrl() {
    const params = new URLSearchParams({ format })
    if (from) params.set('from_date', from)
    if (to) params.set('to_date', to)
    if (!includeArticles) params.set('include_articles', '0')

    if (scope === 'keyword') {
      if (q.trim()) params.set('q', q.trim())
      if (chatId) params.set('chat_id', chatId)
      if (useFuzzy) params.set('fuzzy', '1')
      return `/api/export/keyword?${params}`
    }
    if (scope === 'timerange') {
      if (chatId) params.set('chat_id', chatId)
      return `/api/export/timerange?${params}`
    }
    if (scope === 'chat') {
      const cid = q.trim() || chatId || ''
      if (!cid) return null
      return `/api/export/chat/${encodeURIComponent(cid)}?${params}`
    }
    const val = q.trim()
    if (!val) return null
    return `/api/export/${scope}/${encodeURIComponent(val)}?${params}`
  }

  function handleExport() {
    const url = buildUrl()
    if (!url) return
    setLoading(true)
    const a = document.createElement('a')
    a.href = url
    a.target = '_blank'
    a.click()
    setTimeout(() => setLoading(false), 1000)
  }

  const needsValue = scope !== 'timerange'
  const valuePlaceholder = {
    keyword: 'e.g. gold etf — or leave blank',
    author: 'e.g. charliebilello',
    hashtag: 'e.g. Gold (without #)',
    mention: 'e.g. federalreserve (without @)',
    ticker: 'e.g. SPY (without $)',
    chat: 'Chat ID — leave blank for current chat',
  }[scope]

  const valueLabel = {
    keyword: 'Keyword / phrase',
    author: 'Author handle',
    hashtag: 'Hashtag',
    mention: 'Mention handle',
    ticker: 'Ticker symbol',
    chat: 'Chat ID',
  }[scope]

  return (
    <div className="modal-backdrop" onClick={e => { if (e.target === e.currentTarget) onClose() }}>
      <div className="modal">
        <div className="modal-header">
          <h2>Export bundle</h2>
          <button className="link-preview-close" onClick={onClose}>✕</button>
        </div>

        <div className="modal-body">
          <div className="form-label">
            Scope
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
              {valueLabel}
              {scope === 'keyword' && <span className="form-optional"> (optional — blank exports all)</span>}
              <input
                className="form-input"
                value={q}
                onChange={e => setQ(e.target.value)}
                placeholder={valuePlaceholder}
              />
            </label>
          )}

          <div className="form-row">
            <label className="form-label">From
              <input className="form-input" type="date" value={from} onChange={e => setFrom(e.target.value)} />
            </label>
            <label className="form-label">To
              <input className="form-input" type="date" value={to} onChange={e => setTo(e.target.value)} />
            </label>
          </div>

          <fieldset className="form-fieldset">
            <legend>Format</legend>
            {['md', 'csv', 'json'].map(f => (
              <label key={f} className="form-radio">
                <input type="radio" name="format" value={f} checked={format === f} onChange={() => setFormat(f)} />
                {f.toUpperCase()}
              </label>
            ))}
          </fieldset>

          <label className="form-check">
            <input type="checkbox" checked={includeArticles} onChange={e => setIncludeArticles(e.target.checked)} />
            Include scraped articles
          </label>

          {scope === 'keyword' && (
            <label className="form-check">
              <input type="checkbox" checked={useFuzzy} onChange={e => setUseFuzzy(e.target.checked)} />
              Fuzzy match (typo-tolerant)
            </label>
          )}
        </div>

        <div className="modal-footer">
          <button className="btn-secondary" onClick={onClose}>Cancel</button>
          <button className="btn-primary" onClick={handleExport} disabled={loading || (needsValue && scope !== 'keyword' && !q.trim())}>
            {loading ? 'Downloading…' : 'Download'}
          </button>
        </div>
      </div>
    </div>
  )
}
