import type { Config } from 'tailwindcss'

const config: Config = {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      // C5 colour tokens — exact hex values, never modify
      colors: {
        'bg-base': '#0A0E14',
        'bg-surface': '#111721',
        'bg-elevated': '#1A2230',
        'bg-inset': '#070A0F',
        border: '#232B3A',
        'border-strong': '#313C4F',
        'text-primary': '#E6EDF3',
        'text-secondary': '#8B98A9',
        'text-tertiary': '#5A6577',
        accent: '#38BDF8',
        'accent-hover': '#7DD3FC',
        running: '#34D399',
        warn: '#F59E0B',
        danger: '#EF4444',
        success: '#22C55E',
        info: '#A78BFA',
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
        mono: ['JetBrains Mono', 'Menlo', 'Monaco', 'monospace'],
      },
      fontSize: {
        // Body 14px per C5
        base: ['14px', { lineHeight: '1.6' }],
        sm: ['12px', { lineHeight: '1.5' }],
        lg: ['16px', { lineHeight: '1.6' }],
        xl: ['18px', { lineHeight: '1.5' }],
      },
      fontWeight: {
        // Only 400 and 500 per C5
        normal: '400',
        medium: '500',
      },
      borderWidth: {
        DEFAULT: '1px',
      },
      // Flat design — no box shadows
      boxShadow: {
        none: 'none',
      },
    },
  },
  plugins: [],
}

export default config