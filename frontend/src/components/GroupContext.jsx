import React, { createContext, useContext, useState } from 'react'
import useGroup from '../hooks/useGroup.js'

const GroupCtx = createContext(null)
const SelectModeCtx = createContext(false)

export function GroupProvider({ children }) {
  const group = useGroup()
  const [selectMode, setSelectMode] = useState(() => {
    try { return localStorage.getItem('wa-archive:select-mode') === 'true' } catch { return false }
  })

  function toggleSelectMode() {
    setSelectMode(v => {
      const next = !v
      try { localStorage.setItem('wa-archive:select-mode', String(next)) } catch {}
      return next
    })
  }

  return (
    <GroupCtx.Provider value={{ ...group, selectMode, toggleSelectMode }}>
      {children}
    </GroupCtx.Provider>
  )
}

export function useGroupContext() {
  return useContext(GroupCtx)
}
