import React, { useEffect, useRef, useState } from 'react'

const STORAGE_KEY = id => `wa-ask-history-${id}`

const PHASE_LABELS = {
  retrieving: '💬 Retrieving from archive…',
  thinking:   '🧠 Thinking…',
  composing:  '✏️ Composing…',
}

function CitationCard({ citation, index }) {
  return (
    <a
      href={citation.url}
      target="_blank"
      rel="noreferrer"
      className="ask-citation-card"
    >
      <span className="ask-citation-num">[{index + 1}]</span>
      {citation.author_handle && (
        <span className="ask-citation-author">@{citation.author_handle}</span>
      )}
      <span className="ask-citation-title">{citation.title || citation.url}</span>
      {citation.snippet && (
        <span className="ask-citation-snippet">{citation.snippet}</span>
      )}
    </a>
  )
}

export default function AskPanel({ chatId, chatName }) {
  const [question, setQuestion] = useState('')
  const [history, setHistory] = useState(() => {
    try { return JSON.parse(localStorage.getItem(STORAGE_KEY(chatId)) || '[]') } catch { return [] }
  })
  const [streaming, setStreaming] = useState(false)
  const [streamPhase, setStreamPhase] = useState(null)
  const [error, setError] = useState('')
  const bottomRef = useRef(null)

  useEffect(() => {
    try { setHistory(JSON.parse(localStorage.getItem(STORAGE_KEY(chatId)) || '[]')) } catch { setHistory([]) }
    setQuestion('')
    setError('')
    setStreamPhase(null)
  }, [chatId])

  function saveHistory(h) {
    setHistory(h)
    try { localStorage.setItem(STORAGE_KEY(chatId), JSON.stringify(h.slice(-20))) } catch {}
  }

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [history, streaming, streamPhase])

  async function handleAsk(e) {
    e.preventDefault()
    const q = question.trim()
    if (!q || streaming) return
    setQuestion('')
    setError('')
    setStreamPhase(null)

    const newHistory = [
      ...history,
      { role: 'user', content: q },
      { role: 'assistant', content: '', citations: [] },
    ]
    saveHistory(newHistory)
    const answerIdx = newHistory.length - 1

    setStreaming(true)
    setStreamPhase('retrieving')

    try {
      const resp = await fetch('/api/chat/ask', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question: q, chat_id: chatId, k: 12 }),
      })

      if (!resp.ok) {
        const detail = await resp.json().then(d => d.detail).catch(() => `HTTP ${resp.status}`)
        setError(detail)
        const h2 = [...newHistory]
        h2[answerIdx] = { role: 'assistant', content: '', citations: [], error: detail }
        saveHistory(h2)
        return
      }

      const reader = resp.body.getReader()
      const dec = new TextDecoder()
      let buf = ''
      let answer = ''
      let citations = []
      let currentPhase = 'retrieving'

      outer: while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buf += dec.decode(value, { stream: true })
        const lines = buf.split('\n')
        buf = lines.pop()
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          const payload = line.slice(6)
          if (payload === '[DONE]') break outer
          try {
            const evt = JSON.parse(payload)
            if (evt.phase === 'retrieving') {
              currentPhase = 'retrieving'
              setStreamPhase('retrieving')
            } else if (evt.phase === 'thinking') {
              currentPhase = 'thinking'
              setStreamPhase('thinking')
              citations = evt.citations || []
              const h2 = [...newHistory]
              h2[answerIdx] = { role: 'assistant', content: '', citations }
              saveHistory(h2)
            } else if (evt.token) {
              if (currentPhase !== 'composing') {
                currentPhase = 'composing'
                setStreamPhase('composing')
              }
              answer += evt.token
              const h2 = [...newHistory]
              h2[answerIdx] = { role: 'assistant', content: answer, citations }
              saveHistory(h2)
            } else if (evt.error) {
              setError(evt.error)
            }
          } catch {}
        }
      }
    } catch (err) {
      setError(err.message)
    } finally {
      setStreaming(false)
      setStreamPhase(null)
    }
  }

  return (
    <div className="ask-panel">
      <div className="ask-header">
        <h2 className="thread-title">Ask about {chatName || 'all chats'}</h2>
        <button className="btn-link small" onClick={() => saveHistory([])}>Clear history</button>
      </div>

      <div className="ask-messages">
        {history.length === 0 && (
          <div className="thread-empty">Ask a question about the chat content.</div>
        )}
        {history.map((msg, i) => {
          const isActiveAssistant = msg.role === 'assistant' && streaming && i === history.length - 1
          return (
            <div key={i} className={`ask-bubble ${msg.role}`}>
              <div className="ask-bubble-content">
                {msg.citations && msg.citations.length > 0 && (
                  <div className="ask-citations">
                    <div className="ask-citations-label">Sources</div>
                    {msg.citations.map((c, ci) => (
                      <CitationCard key={ci} citation={c} index={ci} />
                    ))}
                  </div>
                )}
                {isActiveAssistant && streamPhase && !msg.content && (
                  <span className="ask-phase-indicator">
                    {PHASE_LABELS[streamPhase] || '…'}
                  </span>
                )}
                {msg.error
                  ? <span className="ask-error">{msg.error}</span>
                  : msg.content || (!isActiveAssistant ? '' : null)}
              </div>
            </div>
          )
        })}
        <div ref={bottomRef} />
      </div>

      {error && <div className="banner error">{error}</div>}

      <form className="ask-form" onSubmit={handleAsk}>
        <input
          className="ask-input"
          value={question}
          onChange={e => setQuestion(e.target.value)}
          placeholder="Ask a question about the chat…"
          disabled={streaming}
        />
        <button className="btn-primary" type="submit" disabled={!question.trim() || streaming}>
          {streaming ? (PHASE_LABELS[streamPhase] ? '…' : '…') : 'Ask'}
        </button>
      </form>
    </div>
  )
}
