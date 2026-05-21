import React, { useEffect, useRef, useState } from 'react'

function PriceSparkline({ history }) {
  if (!history || history.length < 2) return null
  const closes = history.map(h => h.close)
  const min = Math.min(...closes)
  const max = Math.max(...closes)
  const range = max - min || 1
  const W = 200
  const H = 60
  const pts = closes.map((v, i) => {
    const x = (i / (closes.length - 1)) * W
    const y = H - ((v - min) / range) * H
    return `${x.toFixed(1)},${y.toFixed(1)}`
  }).join(' ')
  const last = closes[closes.length - 1]
  const first = closes[0]
  const color = last >= first ? '#16a34a' : '#dc2626'

  return (
    <svg viewBox={`0 0 ${W} ${H}`} width={W} height={H} className="ticker-sparkline">
      <polyline points={pts} fill="none" stroke={color} strokeWidth="1.5" />
    </svg>
  )
}

export default function TickerChip({ symbol, onSearch }) {
  const [quote, setQuote] = useState(null)
  const [hovered, setHovered] = useState(false)
  const [modalOpen, setModalOpen] = useState(false)
  const hoverTimer = useRef(null)
  const modalRef = useRef(null)

  function loadQuote() {
    if (quote) return
    fetch(`/api/quotes/${encodeURIComponent(symbol)}`)
      .then(r => r.ok ? r.json() : null)
      .then(data => { if (data && !data.error) setQuote(data) })
      .catch(() => {})
  }

  function handleMouseEnter() {
    hoverTimer.current = setTimeout(() => {
      setHovered(true)
      loadQuote()
    }, 300)
  }

  function handleMouseLeave() {
    clearTimeout(hoverTimer.current)
    setHovered(false)
  }

  function handleClick(e) {
    e.stopPropagation()
    loadQuote()
    setModalOpen(true)
  }

  useEffect(() => {
    if (!modalOpen) return
    function handler(e) {
      if (modalRef.current && !modalRef.current.contains(e.target)) setModalOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [modalOpen])

  const changeClass = quote
    ? (quote.change_pct >= 0 ? 'ticker-change-pos' : 'ticker-change-neg')
    : ''

  return (
    <span className="ticker-chip-wrap" onMouseEnter={handleMouseEnter} onMouseLeave={handleMouseLeave}>
      <button
        className="chip chip-ticker chip-clickable"
        onClick={handleClick}
      >
        ${symbol}
        {quote && (
          <span className={`ticker-inline-price ${changeClass}`}>
            {' '}{quote.currency !== 'USD' ? `${quote.currency} ` : ''}
            {quote.last_price?.toLocaleString(undefined, { maximumFractionDigits: 2 })}
            {' '}
            <span>{quote.change_pct >= 0 ? '+' : ''}{quote.change_pct?.toFixed(2)}%</span>
          </span>
        )}
      </button>

      {hovered && quote && (
        <div className="ticker-tooltip">
          <span className="ticker-tooltip-symbol">${symbol}</span>
          <span className="ticker-tooltip-price">
            {quote.currency !== 'USD' ? `${quote.currency} ` : ''}
            {quote.last_price?.toLocaleString(undefined, { maximumFractionDigits: 2 })}
          </span>
          <span className={`ticker-tooltip-change ${changeClass}`}>
            {quote.change_pct >= 0 ? '+' : ''}{quote.change_pct?.toFixed(2)}%
          </span>
        </div>
      )}

      {modalOpen && (
        <div className="ticker-modal-overlay" onClick={() => setModalOpen(false)}>
          <div className="ticker-modal" ref={modalRef} onClick={e => e.stopPropagation()}>
            <div className="ticker-modal-header">
              <span className="ticker-modal-symbol">${symbol}</span>
              {quote && (
                <>
                  <span className="ticker-modal-price">
                    {quote.currency !== 'USD' ? `${quote.currency} ` : ''}
                    {quote.last_price?.toLocaleString(undefined, { maximumFractionDigits: 2 })}
                  </span>
                  <span className={`ticker-modal-change ${changeClass}`}>
                    {quote.change_pct >= 0 ? '+' : ''}{quote.change_pct?.toFixed(2)}%
                  </span>
                </>
              )}
              {!quote && <span className="text-muted">Loading…</span>}
              <button className="ticker-modal-close" onClick={() => setModalOpen(false)}>✕</button>
            </div>

            {quote?.history && (
              <div className="ticker-modal-chart">
                <PriceSparkline history={quote.history} />
                <div className="ticker-modal-chart-labels">
                  <span>{quote.history[0]?.date}</span>
                  <span>{quote.history[quote.history.length - 1]?.date}</span>
                </div>
              </div>
            )}

            {onSearch && (
              <div className="ticker-modal-actions">
                <button
                  className="btn-primary"
                  onClick={() => { setModalOpen(false); onSearch([`$${symbol}`], `$${symbol}`) }}
                >
                  Search tweets mentioning ${symbol}
                </button>
              </div>
            )}
          </div>
        </div>
      )}
    </span>
  )
}
