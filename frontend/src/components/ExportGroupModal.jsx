import React, { useCallback, useEffect, useRef, useState } from 'react'

const TEMPLATES = [
  { value: 'analyse', label: 'Analyse claims & contradictions' },
  { value: 'summarise', label: 'Summarise in bullets' },
  { value: 'extract_claims', label: 'Extract all factual claims' },
  { value: 'custom', label: 'Custom prompt…' },
]

function useDebounced(value, delay) {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), delay)
    return () => clearTimeout(t)
  }, [value, delay])
  return debounced
}

export default function ExportGroupModal({ articleIds, onClose }) {
  const [format, setFormat] = useState('prompt')
  const [template, setTemplate] = useState('analyse')
  const [customPrompt, setCustomPrompt] = useState('')
  const [maxChars, setMaxChars] = useState(500)
  const [includeSimilar, setIncludeSimilar] = useState(false)
  const [preview, setPreview] = useState('')
  const [previewLoading, setPreviewLoading] = useState(false)
  const [copying, setCopying] = useState(false)
  const previewRef = useRef(null)

  const bodyFor = useCallback(() => ({
    article_ids: articleIds,
    format,
    prompt_template: template,
    custom_prompt: template === 'custom' ? customPrompt : null,
    max_tweet_chars: maxChars,
    include_similar_context: includeSimilar,
  }), [articleIds, format, template, customPrompt, maxChars, includeSimilar])

  const debouncedBody = useDebounced(JSON.stringify(bodyFor()), 400)

  useEffect(() => {
    if (!articleIds.length) return
    setPreviewLoading(true)
    fetch('/api/export/group?inline=1', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: debouncedBody,
    })
      .then(r => r.ok ? r.json() : { body: '(preview error)' })
      .then(d => setPreview(d.body || ''))
      .catch(() => setPreview('(preview error)'))
      .finally(() => setPreviewLoading(false))
  }, [debouncedBody, articleIds.length])

  async function handleCopy() {
    const body = bodyFor()
    setCopying(true)
    try {
      const r = await fetch('/api/export/group?inline=1', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      const d = r.ok ? await r.json() : null
      if (d?.body) {
        await navigator.clipboard.writeText(d.body)
      }
    } finally {
      setCopying(false)
    }
  }

  async function handleDownload() {
    const body = bodyFor()
    const r = await fetch('/api/export/group', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (!r.ok) return
    const blob = await r.blob()
    const cd = r.headers.get('content-disposition') || ''
    const match = cd.match(/filename=([^\s;]+)/)
    const filename = match ? match[1] : `group-export.${format === 'json' ? 'json' : format === 'txt' || format === 'prompt' ? 'txt' : 'md'}`
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url; a.download = filename; a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className="export-group-overlay" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="export-group-modal">
        <div className="export-group-header">
          <h3 className="export-group-title">Export group · {articleIds.length} tweet{articleIds.length !== 1 ? 's' : ''}</h3>
          <button className="export-group-close" onClick={onClose}>×</button>
        </div>

        <div className="export-group-body">
          <div className="export-group-options">
            <div className="export-group-field">
              <label className="export-group-label">Format</label>
              <div className="export-group-radios">
                {['prompt', 'md', 'txt', 'json'].map(f => (
                  <label key={f} className={`export-group-radio${format === f ? ' selected' : ''}`}>
                    <input type="radio" name="format" value={f} checked={format === f} onChange={() => setFormat(f)} />
                    {f === 'prompt' ? 'LLM prompt' : f === 'md' ? 'Markdown' : f === 'txt' ? 'Plain text' : 'JSON'}
                  </label>
                ))}
              </div>
            </div>

            {format === 'prompt' && (
              <div className="export-group-field">
                <label className="export-group-label">Prompt template</label>
                <select
                  className="export-group-select"
                  value={template}
                  onChange={e => setTemplate(e.target.value)}
                >
                  {TEMPLATES.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
                </select>
              </div>
            )}

            {format === 'prompt' && template === 'custom' && (
              <div className="export-group-field">
                <label className="export-group-label">Custom task instruction</label>
                <textarea
                  className="export-group-textarea"
                  value={customPrompt}
                  onChange={e => setCustomPrompt(e.target.value)}
                  placeholder="E.g. Extract all price targets mentioned and group by ticker…"
                  rows={3}
                />
              </div>
            )}

            <div className="export-group-field">
              <label className="export-group-label">
                Max chars per tweet: <strong>{maxChars}</strong>
              </label>
              <input
                type="range"
                min={200} max={2000} step={100}
                value={maxChars}
                onChange={e => setMaxChars(Number(e.target.value))}
                className="export-group-slider"
              />
            </div>

            <div className="export-group-field export-group-checkbox-row">
              <label className="export-group-checkbox-label">
                <input
                  type="checkbox"
                  checked={includeSimilar}
                  onChange={e => setIncludeSimilar(e.target.checked)}
                />
                Include related-tweet context appendix
              </label>
            </div>
          </div>

          <div className="export-group-preview-wrap">
            <div className="export-group-preview-header">
              Preview {previewLoading && <span className="export-group-preview-loading">…</span>}
            </div>
            <pre className="export-group-preview" ref={previewRef}>
              {preview.split('\n').slice(0, 50).join('\n')}
            </pre>
          </div>
        </div>

        <div className="export-group-footer">
          <button className="export-group-btn primary" onClick={handleCopy} disabled={copying}>
            {copying ? 'Copying…' : 'Copy to clipboard'}
          </button>
          <button className="export-group-btn" onClick={handleDownload}>
            Download
          </button>
          <button className="export-group-btn cancel" onClick={onClose}>Cancel</button>
        </div>
      </div>
    </div>
  )
}
