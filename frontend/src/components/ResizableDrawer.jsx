import React, { useEffect, useRef, useState } from 'react'

/**
 * A right-anchored panel with a drag handle on its left edge that lets the
 * user resize the width. Used by the tweet drawer (MessageThread,
 * SearchResults) and the research article drawer. Width is component-local
 * state — no localStorage persistence, so each open starts at defaultWidth.
 *
 * The math `window.innerWidth - e.clientX` assumes the drawer is anchored to
 * the right edge of either the viewport (overlay/modal style) or its flex
 * container — both work because in both cases the cursor's distance from the
 * right edge equals the drawer width.
 *
 * Width is clamped to [minWidth, maxWidth]; on narrow viewports the CSS
 * media query in components.css hides .drawer-resize-handle so users on
 * mobile aren't trying to drag a sub-pixel target.
 */
export default function ResizableDrawer({
  children,
  className = '',
  defaultWidth = 360,
  minWidth = 280,
  maxWidth = 800,
}) {
  const [width, setWidth] = useState(defaultWidth)
  const draggingRef = useRef(false)

  const onMouseDown = (e) => {
    e.preventDefault()
    draggingRef.current = true
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
  }

  useEffect(() => {
    function onMove(e) {
      if (!draggingRef.current) return
      const newWidth = window.innerWidth - e.clientX
      setWidth(Math.max(minWidth, Math.min(newWidth, maxWidth)))
    }
    function onUp() {
      if (!draggingRef.current) return
      draggingRef.current = false
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
    return () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
  }, [minWidth, maxWidth])

  return (
    <div className={className} style={{ width: `${width}px` }}>
      <div
        className="drawer-resize-handle"
        onMouseDown={onMouseDown}
        title="Drag to resize"
      />
      {children}
    </div>
  )
}
