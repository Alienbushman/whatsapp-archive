import React, { useEffect, useState } from 'react'

const KIND_LABELS = {
  hashtag: 'Hashtags',
  mention: 'Mentions',
  ticker: 'Tickers',
  author: 'Authors',
}

function RelatedSection({ seedKind, seedValue, kind, onHashtagClick, onMentionClick, onAuthorClick }) {
  const [items, setItems] = useState(null)

  useEffect(() => {
    fetch(`/api/cooccurrence?seed_kind=${seedKind}&seed_value=${encodeURIComponent(seedValue)}&kind=${kind}&limit=10`)
      .then(r => r.ok ? r.json() : [])
      .then(setItems)
      .catch(() => setItems([]))
  }, [seedKind, seedValue, kind])

  if (!items || items.length === 0) return null

  function handleClick(value) {
    if (kind === 'hashtag' && onHashtagClick) onHashtagClick(value)
    else if (kind === 'mention' && onMentionClick) onMentionClick(value)
    else if (kind === 'author' && onAuthorClick) onAuthorClick(value)
  }

  return (
    <div className="related-section">
      <span className="related-section-label">{KIND_LABELS[kind]}</span>
      <div className="related-item-list">
        {items.map(item => {
          const clickable = kind === 'hashtag' || kind === 'mention' || kind === 'author'
          const prefix = kind === 'hashtag' ? '#' : kind === 'mention' ? '@' : kind === 'ticker' ? '$' : '@'
          return clickable ? (
            <button key={item.value} className="related-item related-item-clickable" onClick={() => handleClick(item.value)}>
              <span className="related-item-value">{prefix}{item.value}</span>
              <span className="related-item-count">{item.count}</span>
            </button>
          ) : (
            <span key={item.value} className="related-item">
              <span className="related-item-value">{prefix}{item.value}</span>
              <span className="related-item-count">{item.count}</span>
            </span>
          )
        })}
      </div>
    </div>
  )
}

export default function RelatedPanel({ seedKind, seedValue, onHashtagClick, onMentionClick, onAuthorClick }) {
  const kindsToShow = ['hashtag', 'mention', 'ticker', 'author'].filter(k => k !== seedKind)

  return (
    <div className="related-panel">
      <h3 className="related-panel-title">Related</h3>
      {kindsToShow.map(kind => (
        <RelatedSection
          key={kind}
          seedKind={seedKind}
          seedValue={seedValue}
          kind={kind}
          onHashtagClick={onHashtagClick}
          onMentionClick={onMentionClick}
          onAuthorClick={onAuthorClick}
        />
      ))}
    </div>
  )
}
