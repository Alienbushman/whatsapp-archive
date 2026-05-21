import { useState, useCallback, useEffect } from 'react'

const STORAGE_KEY = 'wa-archive:active-group'
const MAX_SIZE = 50

function load() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw ? JSON.parse(raw) : []
  } catch {
    return []
  }
}

function save(items) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(items))
  } catch {}
}

export default function useGroup() {
  const [items, setItems] = useState(() => load())

  useEffect(() => { save(items) }, [items])

  const has = useCallback((articleId) => {
    return items.some(i => i.article_id === articleId)
  }, [items])

  const add = useCallback((article) => {
    if (!article || !article.id) return
    setItems(prev => {
      if (prev.length >= MAX_SIZE) return prev
      if (prev.some(i => i.article_id === article.id)) return prev
      let tweet = null
      try { tweet = article.tweet_meta ? JSON.parse(article.tweet_meta) : null } catch {}
      return [...prev, {
        article_id: article.id,
        url: article.url,
        tweet,
        addedAt: new Date().toISOString(),
      }]
    })
  }, [])

  const addTweet = useCallback((articleId, url, tweet) => {
    if (!articleId) return
    setItems(prev => {
      if (prev.length >= MAX_SIZE) return prev
      if (prev.some(i => i.article_id === articleId)) return prev
      return [...prev, { article_id: articleId, url, tweet, addedAt: new Date().toISOString() }]
    })
  }, [])

  const remove = useCallback((articleId) => {
    setItems(prev => prev.filter(i => i.article_id !== articleId))
  }, [])

  const clear = useCallback(() => setItems([]), [])

  return { items, add, addTweet, remove, clear, has, size: items.length, maxSize: MAX_SIZE }
}
