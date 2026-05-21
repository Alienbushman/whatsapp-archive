import { useEffect, useRef, useState } from 'react'
import { getMessages } from '../api.js'

export function useMessages(chatId, page, query) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const debounceRef = useRef(null)

  useEffect(() => {
    if (!chatId) { setData(null); return }

    setLoading(true)
    clearTimeout(debounceRef.current)

    const doFetch = () => {
      getMessages(chatId, { page, q: query })
        .then(setData)
        .catch(setError)
        .finally(() => setLoading(false))
    }

    if (query) {
      debounceRef.current = setTimeout(doFetch, 300)
    } else {
      doFetch()
    }

    return () => clearTimeout(debounceRef.current)
  }, [chatId, page, query])

  return { data, loading, error }
}
