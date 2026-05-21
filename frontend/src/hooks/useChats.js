import { useEffect, useState, useCallback } from 'react'
import { getChats } from '../api.js'

export function useChats() {
  const [chats, setChats] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const refresh = useCallback(() => {
    setLoading(true)
    return getChats()
      .then(next => {
        setChats(next)
        setError(null)
        return next
      })
      .catch(err => {
        setError(err)
        throw err
      })
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  return { chats, loading, error, refresh }
}
