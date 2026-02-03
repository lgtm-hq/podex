import { useState, useEffect } from "react";

// Theme configuration
const themes = [
  { id: "bulma-light", name: "Bulma Light", icon: "🟢" },
  { id: "bulma-dark", name: "Bulma Dark", icon: "🟣" },
  { id: "catppuccin-latte", name: "Catppuccin Latte", icon: "🌻" },
  { id: "catppuccin-frappe", name: "Catppuccin Frappé", icon: "🪻" },
  { id: "catppuccin-macchiato", name: "Catppuccin Macchiato", icon: "🌺" },
  { id: "catppuccin-mocha", name: "Catppuccin Mocha", icon: "🌸" },
  { id: "dracula", name: "Dracula", icon: "🧛" },
  { id: "github-light", name: "GitHub Light", icon: "☀️" },
  { id: "github-dark", name: "GitHub Dark", icon: "🌙" },
] as const;

type ThemeId = (typeof themes)[number]["id"];

// Theme color palettes
const themeColors: Record<ThemeId, Record<string, string>> = {
  "bulma-light": {
    "--color-primary": "#00d1b2",
    "--color-secondary": "#3273dc",
    "--color-background": "#ffffff",
    "--color-surface": "#f5f5f5",
    "--color-text": "#363636",
    "--color-text-muted": "#7a7a7a",
    "--color-border": "#dbdbdb",
    "--color-accent": "#ffdd57",
  },
  "bulma-dark": {
    "--color-primary": "#00d1b2",
    "--color-secondary": "#3273dc",
    "--color-background": "#1a1a1a",
    "--color-surface": "#2d2d2d",
    "--color-text": "#f5f5f5",
    "--color-text-muted": "#b5b5b5",
    "--color-border": "#4a4a4a",
    "--color-accent": "#ffdd57",
  },
  "catppuccin-latte": {
    "--color-primary": "#1e66f5",
    "--color-secondary": "#8839ef",
    "--color-background": "#eff1f5",
    "--color-surface": "#e6e9ef",
    "--color-text": "#4c4f69",
    "--color-text-muted": "#6c6f85",
    "--color-border": "#ccd0da",
    "--color-accent": "#df8e1d",
  },
  "catppuccin-frappe": {
    "--color-primary": "#8caaee",
    "--color-secondary": "#ca9ee6",
    "--color-background": "#303446",
    "--color-surface": "#414559",
    "--color-text": "#c6d0f5",
    "--color-text-muted": "#a5adce",
    "--color-border": "#51576d",
    "--color-accent": "#e5c890",
  },
  "catppuccin-macchiato": {
    "--color-primary": "#8aadf4",
    "--color-secondary": "#c6a0f6",
    "--color-background": "#24273a",
    "--color-surface": "#363a4f",
    "--color-text": "#cad3f5",
    "--color-text-muted": "#a5adcb",
    "--color-border": "#494d64",
    "--color-accent": "#eed49f",
  },
  "catppuccin-mocha": {
    "--color-primary": "#89b4fa",
    "--color-secondary": "#cba6f7",
    "--color-background": "#1e1e2e",
    "--color-surface": "#313244",
    "--color-text": "#cdd6f4",
    "--color-text-muted": "#a6adc8",
    "--color-border": "#45475a",
    "--color-accent": "#f9e2af",
  },
  dracula: {
    "--color-primary": "#bd93f9",
    "--color-secondary": "#ff79c6",
    "--color-background": "#282a36",
    "--color-surface": "#44475a",
    "--color-text": "#f8f8f2",
    "--color-text-muted": "#6272a4",
    "--color-border": "#44475a",
    "--color-accent": "#f1fa8c",
  },
  "github-light": {
    "--color-primary": "#0969da",
    "--color-secondary": "#8250df",
    "--color-background": "#ffffff",
    "--color-surface": "#f6f8fa",
    "--color-text": "#1f2328",
    "--color-text-muted": "#656d76",
    "--color-border": "#d0d7de",
    "--color-accent": "#bf8700",
  },
  "github-dark": {
    "--color-primary": "#58a6ff",
    "--color-secondary": "#a371f7",
    "--color-background": "#0d1117",
    "--color-surface": "#161b22",
    "--color-text": "#e6edf3",
    "--color-text-muted": "#8b949e",
    "--color-border": "#30363d",
    "--color-accent": "#d29922",
  },
};

function getThemeColors(themeId: ThemeId): Record<string, string> {
  return themeColors[themeId] || themeColors["bulma-light"];
}

function isDarkTheme(themeId: ThemeId): boolean {
  return (
    themeId.includes("dark") ||
    themeId.includes("mocha") ||
    themeId.includes("macchiato") ||
    themeId.includes("frappe") ||
    themeId === "dracula"
  );
}

export default function ThemeSelector() {
  const [currentTheme, setCurrentTheme] = useState<ThemeId>("bulma-light");
  const [isOpen, setIsOpen] = useState(false);

  useEffect(() => {
    const saved = localStorage.getItem("podex-theme") as ThemeId | null;
    const initial = saved && themes.some((t) => t.id === saved) ? saved : "bulma-light";
    setCurrentTheme(initial);
    applyTheme(initial);
  }, []);

  const applyTheme = (themeId: ThemeId) => {
    const html = document.documentElement;

    // Remove all theme classes
    themes.forEach((t) => html.classList.remove(t.id));

    // Toggle dark mode
    if (isDarkTheme(themeId)) {
      html.classList.add("dark");
    } else {
      html.classList.remove("dark");
    }

    // Add theme class
    html.classList.add(themeId);

    // Apply CSS variables from turbo-themes tokens
    const colors = getThemeColors(themeId);
    Object.entries(colors).forEach(([key, value]) => {
      html.style.setProperty(key, value);
    });
  };

  const selectTheme = (themeId: ThemeId) => {
    setCurrentTheme(themeId);
    localStorage.setItem("podex-theme", themeId);
    applyTheme(themeId);
    setIsOpen(false);
  };

  const currentThemeData = themes.find((t) => t.id === currentTheme) || themes[0];

  return (
    <div className="relative">
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-medium bg-surface text-text-muted hover:text-text hover:bg-border/50 transition-colors"
        aria-label="Select theme"
      >
        <span>{currentThemeData.icon}</span>
        <span className="hidden sm:inline">{currentThemeData.name}</span>
        <svg
          className={`w-4 h-4 transition-transform ${isOpen ? "rotate-180" : ""}`}
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {isOpen && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setIsOpen(false)} />
          <div className="absolute right-0 mt-2 w-56 rounded-lg bg-surface border border-border shadow-lg z-50 py-1 max-h-80 overflow-y-auto">
            {themes.map((theme) => (
              <button
                key={theme.id}
                onClick={() => selectTheme(theme.id)}
                className={`w-full flex items-center gap-3 px-4 py-2 text-sm text-left transition-colors ${
                  currentTheme === theme.id
                    ? "bg-primary/10 text-primary"
                    : "text-text-muted hover:bg-border/50 hover:text-text"
                }`}
              >
                <span>{theme.icon}</span>
                <span>{theme.name}</span>
                {currentTheme === theme.id && (
                  <svg className="w-4 h-4 ml-auto" fill="currentColor" viewBox="0 0 20 20">
                    <path
                      fillRule="evenodd"
                      d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z"
                      clipRule="evenodd"
                    />
                  </svg>
                )}
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
