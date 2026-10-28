import type { Config } from "tailwindcss";

export default {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: { 950: "#060a13", 900: "#0a1020", 850: "#0e1528", 800: "#121b31", 700: "#1a2541", 600: "#253253" },
        line: "#1f2b45",
        fg: { DEFAULT: "#e6ecf5", muted: "#9aa7bd", dim: "#6b7891" },
        accent: { DEFAULT: "#22d3ee", soft: "#0e7490" },
        ok: "#34d399",
        warn: "#fbbf24",
        danger: "#f07178",
      },
      fontFamily: {
        sans: ["Inter", "ui-sans-serif", "system-ui", "Segoe UI", "Roboto", "sans-serif"],
        mono: ["JetBrains Mono", "ui-monospace", "Cascadia Code", "Consolas", "monospace"],
      },
      keyframes: {
        shimmer: { "0%": { backgroundPosition: "-400px 0" }, "100%": { backgroundPosition: "400px 0" } },
      },
      animation: { shimmer: "shimmer 1.4s linear infinite" },
    },
  },
  plugins: [],
} satisfies Config;
