import React, { createContext, useContext, useEffect, useState, useCallback } from 'react'

const CollectionsCtx = createContext(null)

export function CollectionsProvider({ children }) {
  const [collections, setCollections] = useState([])

  const refresh = useCallback(() => {
    fetch('/api/collections')
      .then(r => r.ok ? r.json() : [])
      .then(setCollections)
      .catch(() => {})
  }, [])

  useEffect(() => { refresh() }, [refresh])

  function addToCollection(collectionId, articleId) {
    return fetch(`/api/collections/${collectionId}/items`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ article_id: articleId }),
    }).then(r => {
      if (r.ok) {
        setCollections(prev => prev.map(c =>
          c.id === collectionId ? { ...c, item_count: (c.item_count || 0) + 1 } : c
        ))
      }
      return r
    })
  }

  function createCollection(name) {
    return fetch('/api/collections', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name }),
    }).then(async r => {
      if (r.ok) {
        const col = await r.json()
        setCollections(prev => [col, ...prev])
        return col
      }
      return null
    })
  }

  return (
    <CollectionsCtx.Provider value={{ collections, addToCollection, createCollection, refresh }}>
      {children}
    </CollectionsCtx.Provider>
  )
}

export function useCollectionsContext() {
  return useContext(CollectionsCtx)
}
