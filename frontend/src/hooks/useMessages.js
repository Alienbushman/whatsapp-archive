import { useEffect, useRef, useState } from 'react'
import { getMessages } from '../api.js'

// `revision` changes when the chat's content changed on the server (e.g. a newer
// export was uploaded), forcing a refetch without a new chatId.
export function useMessages(chatId, page, query, revision = 0) {
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
  }, [chatId, page, query, revision])

  return { data, loading, error }
}
