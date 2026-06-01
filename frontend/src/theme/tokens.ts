/**
 * C5 design tokens — single source of truth for all colours and typography.
 * Import these wherever raw values are needed (e.g. inline styles, canvas).
 * Tailwind classes should use the names defined in tailwind.config.ts.
 */

export const colors = {
  bgBase: '#0A0E14',
  bgSurface: '#111721',
  bgElevated: '#1A2230',
  bgInset: '#070A0F',
  border: '#232B3A',
  borderStrong: '#313C4F',

  textPrimary: '#E6EDF3',
  textSecondary: '#8B98A9',
  textTertiary: '#5A6577',

  accent: '#38BDF8',
  accentHover: '#7DD3FC',
  running: '#34D399',
  warn: '#F59E0B',
  danger: '#EF4444',
  success: '#22C55E',
  info: '#A78BFA',
} as const

export const fonts = {
  sans: 'Inter, system-ui, sans-serif',
  mono: 'JetBrains Mono, Menlo, Monaco, monospace',
} as const

export const fontSize = {
  base: '14px',
  sm: '12px',
  lg: '16px',
  xl: '18px',
} as const

/** CSS variable declarations — injected into :root by index.css */
export const cssVariables = `
  --bg-base:       ${colors.bgBase};
  --bg-surface:    ${colors.bgSurface};
  --bg-elevated:   ${colors.bgElevated};
  --bg-inset:      ${colors.bgInset};
  --border:        ${colors.border};
  --border-strong: ${colors.borderStrong};

  --text-primary:   ${colors.textPrimary};
  --text-secondary: ${colors.textSecondary};
  --text-tertiary:  ${colors.textTertiary};

  --accent:        ${colors.accent};
  --accent-hover:  ${colors.accentHover};
  --running:       ${colors.running};
  --warn:          ${colors.warn};
  --danger:        ${colors.danger};
  --success:       ${colors.success};
  --info:          ${colors.info};
`