/**
 * Base button — C5 design system.
 * Variants: primary (accent), ghost (text-only), danger.
 * Flat design — no shadows, no gradients.
 */

import React from 'react'

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'primary' | 'ghost' | 'danger'
  size?: 'sm' | 'md'
}

export function Button({
  variant = 'primary',
  size = 'md',
  className = '',
  children,
  ...props
}: ButtonProps) {
  const base =
    'inline-flex items-center gap-1.5 font-medium rounded border transition-colors duration-100 cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed select-none'

  const sizes = {
    sm: 'px-2.5 py-1 text-sm',
    md: 'px-3.5 py-1.5 text-base',
  }

  const variants = {
    primary:
      'bg-accent text-bg-base border-accent hover:bg-accent-hover hover:border-accent-hover',
    ghost:
      'bg-transparent text-text-secondary border-border hover:text-text-primary hover:border-border-strong',
    danger:
      'bg-transparent text-danger border-danger hover:bg-danger hover:text-bg-base',
  }

  return (
    <button
      className={`${base} ${sizes[size]} ${variants[variant]} ${className}`}
      {...props}
    >
      {children}
    </button>
  )
}