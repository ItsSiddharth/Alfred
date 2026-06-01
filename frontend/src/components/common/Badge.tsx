import React from 'react'

interface BadgeProps {
  color?: 'accent' | 'running' | 'warn' | 'danger' | 'info' | 'muted'
  children: React.ReactNode
  className?: string
}

export function Badge({ color = 'muted', children, className = '' }: BadgeProps) {
  const colors = {
    accent: 'text-accent border-accent',
    running: 'text-running border-running',
    warn: 'text-warn border-warn',
    danger: 'text-danger border-danger',
    info: 'text-info border-info',
    muted: 'text-text-tertiary border-border',
  }

  return (
    <span
      className={`inline-flex items-center px-1.5 py-0.5 text-sm font-mono rounded border ${colors[color]} ${className}`}
    >
      {children}
    </span>
  )
}