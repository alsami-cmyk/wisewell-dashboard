import type { Config } from 'tailwindcss'

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Poppins', 'sans-serif'],
      },
      colors: {
        adnoc: {
          DEFAULT: '#0047BA',
          dark:    '#003A9A',
          light:   '#1A5EC7',
          pale:    '#3372D1',
          faint:   '#E8F0FB',
        },
        surface:   '#F2F5FA',
        'off-white': '#F6F8FC',
      },
      boxShadow: {
        sm: '0 1px 4px rgba(0,71,186,0.06)',
        md: '0 4px 20px rgba(0,71,186,0.10)',
      },
      keyframes: {
        fadeUp: {
          from: { opacity: '0', transform: 'translateY(12px)' },
          to:   { opacity: '1', transform: 'translateY(0)' },
        },
        kpiPulse: {
          '0%':   { boxShadow: '0 0 0 0 rgba(0,71,186,0.25)' },
          '60%':  { boxShadow: '0 0 0 8px rgba(0,71,186,0)' },
          '100%': { boxShadow: '0 0 0 0 rgba(0,71,186,0)' },
        },
      },
      animation: {
        'fade-up':   'fadeUp 0.4s ease both',
        'fade-up-1': 'fadeUp 0.4s 0.07s ease both',
        'fade-up-2': 'fadeUp 0.4s 0.13s ease both',
        'fade-up-3': 'fadeUp 0.4s 0.19s ease both',
        'kpi-pulse': 'kpiPulse 0.55s ease-out',
      },
    },
  },
  plugins: [],
} satisfies Config
