import { useEffect, useRef, useState } from 'react'
import { search } from '../api.js'

export function useSearch(query) {
  const [results, setResults] = useState([])
  const [loading, setLoading] = useState(false)
  const debounceRef = useRef(null)

  useEffect(() => {
    if (!query.trim()) { setResults([]); return }

    setLoading(true)
    clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => {
      search(query)
        .then(setResults)
        .catch(() => setResults([]))
        .finally(() => setLoading(false))
    }, 400)

    return () => clearTimeout(debounceRef.current)
  }, [query])

  return { results, loading }
}
