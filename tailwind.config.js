/** @type {import('tailwindcss').Config} */
module.exports = {
  darkMode: 'class',
  content: [
    './index.html',
    './templates/**/*.html',
    './static/**/*.js',
  ],
  theme: {
    extend: {
      colors: {
        bg: '#0b0f17',
        surface: '#111723',
        card: '#171f30',
        border: '#232f46',
      },
    },
  },
  plugins: [],
};
