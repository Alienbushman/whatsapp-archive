import React, { useEffect, useRef, useState } from 'react'

export default function AddToBinButton({ targetKind, targetId }) {
  const [open, setOpen] = useState(false)
  const [bins, setBins] = useState(null)
  const [adding, setAdding] = useState(null)
  const [creating, setCreating] = useState(false)
  const [newBinName, setNewBinName] = useState('')
  const ref = useRef(null)

  useEffect(() => {
    if (!open) return
    setBins(null)
    fetch(`/api/research_bins?with_pinned_for=${encodeURIComponent(targetKind + ':' + targetId)}`)
      .then(r => r.ok ? r.json() : [])
      .then(setBins)
      .catch(() => setBins([]))
  }, [open, targetKind, targetId])

  useEffect(() => {
    if (!open) return
    function handler(e) {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  async function handlePin(binId) {
    setAdding(binId)
    try {
      const r = await fetch(`/api/research_bins/${binId}/items`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ target_kind: targetKind, target_id: String(targetId) }),
      })
      if (r.ok) {
        setBins(prev => prev?.map(b => b.id === binId ? { ...b, is_pinned_here: true } : b))
      }
    } finally {
      setAdding(null)
    }
  }

  async function handleCreateBin(e) {
    e.preventDefault()
    const name = newBinName.trim()
    if (!name) return
    const r = await fetch('/api/research_bins', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name }),
    })
    if (r.ok) {
      const bin = await r.json()
      setBins(prev => [...(prev || []), { ...bin, is_pinned_here: false }])
      setCreating(false)
      setNewBinName('')
      await handlePin(bin.id)
    }
  }

  const anyPinned = bins?.some(b => b.is_pinned_here)

  return (
    <span className="add-to-bin-wrap" ref={ref} onClick={e => e.stopPropagation()}>
      <button
        className={`add-to-bin-btn${anyPinned ? ' pinned' : ''}`}
        title={anyPinned ? 'Pinned to a research bin' : 'Pin to research bin'}
        onClick={() => setOpen(v => !v)}
      >📌</button>
      {open && (
        <div className="add-to-bin-menu">
          {bins === null && <span className="add-to-bin-loading">Loading…</span>}
          {bins !== null && bins.length === 0 && !creating && (
            <span className="add-to-bin-empty">No bins yet</span>
          )}
          {bins !== null && bins.map(b => (
            <button
              key={b.id}
              className={`add-to-bin-item${b.is_pinned_here ? ' pinned' : ''}`}
              onClick={() => !b.is_pinned_here && handlePin(b.id)}
              disabled={!!b.is_pinned_here || adding === b.id}
            >
              {b.is_pinned_here ? '✓ ' : ''}{b.name}
              {adding === b.id ? ' …' : ''}
            </button>
          ))}
          {creating ? (
            <form className="add-to-bin-create-form" onSubmit={handleCreateBin}>
              <input
                className="add-to-bin-name-input"
                value={newBinName}
                onChange={e => setNewBinName(e.target.value)}
                placeholder="Bin name…"
                autoFocus
              />
              <button type="submit" className="btn-primary btn-xs">Create</button>
              <button type="button" className="btn-ghost btn-xs" onClick={() => setCreating(false)}>✕</button>
            </form>
          ) : (
            <button className="add-to-bin-new" onClick={() => setCreating(true)}>+ New bin</button>
          )}
        </div>
      )}
    </span>
  )
}
