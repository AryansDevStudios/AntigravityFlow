const path = require('path');

/** @type {import('tailwindcss').Config} */
module.exports = {
  darkMode: 'class',
  content: [
    path.join(__dirname, 'index.html'),
    path.join(__dirname, 'assistant.html')
  ],
  theme: {
    extend: {}
  },
  plugins: []
};
