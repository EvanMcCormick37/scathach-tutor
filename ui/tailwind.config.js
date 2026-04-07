/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // scathach brand palette — dark scholarly tones
        brand: {
          50:  "#f0f4ff",
          100: "#dce6ff",
          200: "#b9ccff",
          300: "#85a8ff",
          400: "#4e7bff",
          500: "#2355f5",
          600: "#1237eb",
          700: "#1030d8",
          800: "#1229ae",
          900: "#142789",
          950: "#0e1852",
        },
      },
      fontFamily: {
        mono: ["JetBrains Mono", "Fira Code", "ui-monospace", "monospace"],
      },
    },
  },
  plugins: [],
};
