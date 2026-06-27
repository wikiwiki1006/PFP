/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        bg: {
          base: '#0b0f1a',
          card: '#111827',
          elevated: '#1a2035',
        },
        border: {
          card: '#1e2d40',
        },
        text: {
          primary: '#e2e8f0',
          muted: '#64748b',
        },
        green: {
          pos: '#10b981',
        },
        red: {
          neg: '#ef4444',
        },
        blue: {
          accent: '#3b82f6',
        },
        amber: {
          warn: '#f59e0b',
        },
      },
      fontFamily: {
        mono: ['JetBrains Mono', 'Fira Code', 'Consolas', 'monospace'],
      },
    },
  },
  plugins: [],
}
