import React, { useState } from 'react'
import { LinkStatusIcon, TweetCard } from './LinkPreview.jsx'

const URL_RE = /https?:\/\/[^\s<>"']+/g
const TWEET_URL_RE = /https?:\/\/(x\.com|twitter\.com)\/\S+\/status\//

function senderColor(sender) {
  let hash = 0
  for (let i = 0; i < sender.length; i++) hash = sender.charCodeAt(i) + ((hash << 5) - hash)
  const hue = ((hash % 360) + 360) % 360
  return `hsl(${hue} 55% 42%)`
}

function highlight(text, query) {
  if (!query) return text
  const idx = text.toLowerCase().indexOf(query.toLowerCase())
  if (idx === -1) return text
  return (
    <>
      {text.slice(0, idx)}
      <mark>{text.slice(idx, idx + query.length)}</mark>
      {highlight(text.slice(idx + query.length), query)}
    </>
  )
}

function formatTs(iso) {
  const d = new Date(iso)
  return d.toLocaleString(undefined, {
    year: '2-digit', month: 'numeric', day: 'numeric',
    hour: '2-digit', minute: '2-digit',
  })
}

function renderBody(text, highlightQuery, statusMap, onLinkClick) {
  if (!onLinkClick) return highlight(text, highlightQuery)

  const parts = []
  let last = 0
  let match
  URL_RE.lastIndex = 0
  while ((match = URL_RE.exec(text)) !== null) {
    const url = match[0]
    const start = match.index
    if (start > last) {
      parts.push(highlight(text.slice(last, start), highlightQuery))
    }
    const urlStatus = statusMap?.[url]
    parts.push(
      <span key={start} className="url-token">
        <a
          href="#"
          className="inline-link"
          onClick={e => { e.preventDefault(); onLinkClick(url) }}
          title={url}
        >
          {highlight(url, highlightQuery)}
        </a>
        <LinkStatusIcon status={urlStatus} />
      </span>
    )
    last = start + url.length
  }
  if (last < text.length) {
    parts.push(highlight(text.slice(last), highlightQuery))
  }
  return parts
}

export function extractUrls(text) {
  return [...(text.matchAll(URL_RE) || [])].map(m => m[0])
}

function SimilarTweetStrip({ items, onOpen }) {
  return (
    <div className="similar-strip">
      {items.map((a, i) => {
        let tweet = null
        try { tweet = a.tweet_meta ? JSON.parse(a.tweet_meta) : null } catch {}
        return (
          <button key={i} className="similar-strip-card" onClick={() => onOpen && onOpen(a.url)}>
            <span className="similar-strip-handle">@{tweet?.author_handle || '?'}</span>
            <span className="similar-strip-text">{(tweet?.text || a.title || '').slice(0, 80)}</span>
            <span className="similar-strip-score">{Math.round((a.similarity_score || 0) * 100)}%</span>
          </button>
        )
      })}
    </div>
  )
}

export default function MessageBubble({ entry, chatId = null, msgKey = null, isBookmarked = false, onBookmarkToggle = null, highlightQuery = '', statusMap = {}, onLinkClick = null, tweetCache = {}, similarCache = {}, onSearch = null, onAuthorClick = null, onHashtagClick = null, onMentionClick = null, onOpenInChat = null }) {
  const [bookmarked, setBookmarked] = useState(isBookmarked)

  // Sync external isBookmarked prop (e.g. after parent refreshes)
  React.useEffect(() => { setBookmarked(isBookmarked) }, [isBookmarked])

  function handleStarClick(e) {
    e.stopPropagation()
    if (!msgKey) return
    if (bookmarked) {
      fetch(`/api/message-bookmarks/${encodeURIComponent(msgKey)}`, { method: 'DELETE' })
        .then(() => {
          setBookmarked(false)
          onBookmarkToggle?.(msgKey, false)
        })
        .catch(() => {})
    } else {
      fetch('/api/message-bookmarks', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ chat_id: chatId, sender: entry.sender, ts: entry.timestamp, body: entry.body }),
      })
        .then(r => r.ok ? r.json() : null)
        .then(bm => {
          if (bm) {
            setBookmarked(true)
            onBookmarkToggle?.(msgKey, true)
          }
        })
        .catch(() => {})
    }
  }

  if (entry.type === 'system') {
    return (
      <div className="system-event">
        <span>{highlight(entry.text, highlightQuery)}</span>
      </div>
    )
  }

  if (entry.is_deleted) {
    return (
      <div className="message deleted" data-msg-ts={entry.timestamp}>
        <div className="meta">
          <span className="sender" style={{ color: senderColor(entry.sender) }}>{entry.sender}</span>
          <span className="ts">{formatTs(entry.timestamp)}</span>
        </div>
        <div className="body deleted-text">This message was deleted</div>
      </div>
    )
  }

  const inlineTweetUrls = [...new Set(extractUrls(entry.body).filter(u => TWEET_URL_RE.test(u)))]
  const inlineTweets = inlineTweetUrls
    .map(u => tweetCache[u] ? { url: u, article: tweetCache[u] } : null)
    .filter(x => x && x.article && x.article.tweet)

  return (
    <div className="message" data-msg-ts={entry.timestamp}>
      <div className="meta">
        <span className="sender" style={{ color: senderColor(entry.sender) }}>{entry.sender}</span>
        <span className="ts">{formatTs(entry.timestamp)}</span>
        {msgKey && (
          <button
            className={`msg-star-btn${bookmarked ? ' starred' : ''}`}
            title={bookmarked ? 'Remove bookmark' : 'Bookmark message'}
            onClick={handleStarClick}
          >{bookmarked ? '★' : '☆'}</button>
        )}
      </div>
      <div className="body">
        {renderBody(entry.body, highlightQuery, statusMap, onLinkClick)}
      </div>
      {inlineTweets.length > 0 && (
        <div className="inline-tweet-cards">
          {inlineTweets.map(({ url, article }) => (
            <InlineTweetWithSimilar
              key={url}
              url={url}
              article={article}
              similarItems={similarCache[String(article.id)] || null}
              onLinkClick={onLinkClick}
              onSearch={onSearch}
              onAuthorClick={onAuthorClick}
              onHashtagClick={onHashtagClick}
              onMentionClick={onMentionClick}
              onOpenInChat={onOpenInChat}
            />
          ))}
        </div>
      )}
    </div>
  )
}

function InlineTweetWithSimilar({ url, article, similarItems, onLinkClick, onSearch, onAuthorClick, onHashtagClick, onMentionClick, onOpenInChat }) {
  const [showStrip, setShowStrip] = useState(false)
  const count = similarItems ? similarItems.length : 0
  return (
    <div className="inline-tweet-wrapper">
      <TweetCard
        tweet={article.tweet}
        sourceUrl={url}
        articleId={article.id}
        compact
        onOpen={() => onLinkClick && onLinkClick(url)}
        onSearch={onSearch}
        onAuthorClick={onAuthorClick}
        onHashtagClick={onHashtagClick}
        onMentionClick={onMentionClick}
        onOpenInChat={onOpenInChat}
      />
      {count > 0 && (
        <div className="similar-footer">
          <button
            className="similar-footer-btn"
            onClick={() => setShowStrip(v => !v)}
          >
            🔍 {count} similar tweet{count !== 1 ? 's' : ''} in archive {showStrip ? '▲' : '▼'}
          </button>
          {showStrip && (
            <SimilarTweetStrip items={similarItems} onOpen={onLinkClick} />
          )}
        </div>
      )}
    </div>
  )
}
