import React from 'react'

const COLORS = {
  default: 'var(--accent)',
  positive: '#16a34a',
  negative: '#dc2626',
  neutral: '#6b7280',
}

function shortLabel(bucket) {
  if (!bucket) return ''
  // "2024-03" → "Mar", "2024-03-15" → "15", "2024-W12" → "W12"
  if (/^\d{4}-W\d+$/.test(bucket)) return bucket.slice(5)
  if (/^\d{4}-\d{2}-\d{2}$/.test(bucket)) return bucket.slice(8)
  if (/^\d{4}-\d{2}$/.test(bucket)) {
    const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']
    const m = parseInt(bucket.slice(5)) - 1
    return months[m] || bucket.slice(5)
  }
  return bucket
}

export default function TimeseriesChart({ data, height = 100, sentiment = false, label = '' }) {
  if (!data || data.length === 0) return null

  const W = 100  // viewBox units per bar group
  const BAR_GAP = 4
  const LABEL_H = 14
  const CHART_H = height - LABEL_H
  const n = data.length
  const barW = Math.max(2, (W * n - BAR_GAP * (n - 1)) / n - BAR_GAP)

  let maxVal = 1
  if (sentiment) {
    maxVal = Math.max(1, ...data.map(d => (d.positive || 0) + (d.negative || 0) + (d.neutral || 0)))
  } else {
    maxVal = Math.max(1, ...data.map(d => d.value || 0))
  }

  const totalW = n * (barW + BAR_GAP) - BAR_GAP

  return (
    <div className="timeseries-chart">
      {label && <div className="timeseries-label">{label}</div>}
      <svg
        viewBox={`0 0 ${totalW} ${height}`}
        preserveAspectRatio="none"
        className="timeseries-svg"
        style={{ height: `${height}px` }}
        aria-hidden="true"
      >
        {data.map((d, i) => {
          const x = i * (barW + BAR_GAP)
          const lx = x + barW / 2

          if (sentiment) {
            const pos = ((d.positive || 0) / maxVal) * CHART_H
            const neg = ((d.negative || 0) / maxVal) * CHART_H
            const neu = ((d.neutral || 0) / maxVal) * CHART_H
            return (
              <g key={i}>
                <rect x={x} y={CHART_H - pos - neg - neu} width={barW} height={neu} fill={COLORS.neutral} opacity="0.8">
                  <title>{d.bucket}: neutral {d.neutral}</title>
                </rect>
                <rect x={x} y={CHART_H - pos - neg} width={barW} height={neg} fill={COLORS.negative} opacity="0.85">
                  <title>{d.bucket}: negative {d.negative}</title>
                </rect>
                <rect x={x} y={CHART_H - pos} width={barW} height={pos} fill={COLORS.positive} opacity="0.85">
                  <title>{d.bucket}: positive {d.positive}</title>
                </rect>
                <text x={lx} y={height - 2} textAnchor="middle" fontSize="7" fill="var(--text-muted)">
                  {shortLabel(d.bucket)}
                </text>
              </g>
            )
          }

          const bh = Math.max(1, ((d.value || 0) / maxVal) * CHART_H)
          return (
            <g key={i}>
              <rect x={x} y={CHART_H - bh} width={barW} height={bh} fill={COLORS.default} opacity="0.8">
                <title>{d.bucket}: {d.value}</title>
              </rect>
              <text x={lx} y={height - 2} textAnchor="middle" fontSize="7" fill="var(--text-muted)">
                {shortLabel(d.bucket)}
              </text>
            </g>
          )
        })}
      </svg>
    </div>
  )
}
