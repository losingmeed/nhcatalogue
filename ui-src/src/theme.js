// Theme system: every colour the UI uses is a CSS variable so it can be
// changed live from Settings. The same values are persisted in
// `settings.ui_prefs.theme` (SQLite) and cached in localStorage so the app
// paints with the saved theme before the settings request finishes.

export const DEFAULT_THEME = {
  // backgrounds
  bg: "#0b0d12",
  surface: "#0e1118",
  panel: "#14171f",
  panelAlt: "#171b25",
  panelDeep: "#0f1218",
  // lines / effects
  border: "#222734",
  borderStrong: "#2a2f3d",
  scrollbar: "#2a2f3d",
  skeleton: "#1c2130",
  overlay: "#000000",
  onAccent: "#ffffff",
  // text
  text: "#e6e9ef",
  textSecondary: "#cbd5e1",
  muted: "#94a3b8",
  subtle: "#64748b",
  faint: "#475569",
  // accent
  accent: "#4f46e5",
  accentHover: "#6366f1",
  accentText: "#c7d2fe",
  accentSoft: "#1e1b4b",
  accentBorder: "#4338ca",
  // danger
  danger: "#be123c",
  dangerHover: "#e11d48",
  dangerText: "#fda4af",
  dangerSoft: "#881337",
  dangerDeep: "#4c0519",
  // success
  success: "#10b981",
  successText: "#6ee7b7",
  successSoft: "#064e3b",
  // warning
  warning: "#f59e0b",
  warningText: "#fcd34d",
  warningSoft: "#451a03",
  // tag-type colours
  tagArtist: "#7c3aed",
  tagCharacter: "#0d9488",
  tagGroup: "#db2777",
  tagLanguage: "#0284c7",
};

// Shown in Settings. Keys must match DEFAULT_THEME.
export const THEME_GROUPS = [
  {
    label: "Backgrounds",
    keys: [
      ["bg", "Page background"],
      ["surface", "Sidebar & bars"],
      ["panel", "Cards & inputs"],
      ["panelAlt", "Menus & raised panels"],
      ["panelDeep", "Image wells"],
    ],
  },
  {
    label: "Lines & effects",
    keys: [
      ["border", "Borders"],
      ["borderStrong", "Strong borders"],
      ["scrollbar", "Scrollbar"],
      ["skeleton", "Loading shimmer"],
      ["overlay", "Image overlays"],
      ["onAccent", "Text on buttons"],
    ],
  },
  {
    label: "Text",
    keys: [
      ["text", "Primary text"],
      ["textSecondary", "Secondary text"],
      ["muted", "Muted text"],
      ["subtle", "Subtle text"],
      ["faint", "Faint text"],
    ],
  },
  {
    label: "Accent",
    keys: [
      ["accent", "Accent"],
      ["accentHover", "Accent hover"],
      ["accentText", "Accent text"],
      ["accentSoft", "Accent background"],
      ["accentBorder", "Accent border"],
    ],
  },
  {
    label: "Danger",
    keys: [
      ["danger", "Danger"],
      ["dangerHover", "Danger hover"],
      ["dangerText", "Danger text"],
      ["dangerSoft", "Danger background"],
      ["dangerDeep", "Danger deep background"],
    ],
  },
  {
    label: "Success & warning",
    keys: [
      ["success", "Success"],
      ["successText", "Success text"],
      ["successSoft", "Success background"],
      ["warning", "Warning"],
      ["warningText", "Warning text"],
      ["warningSoft", "Warning background"],
    ],
  },
  {
    label: "Tag types",
    keys: [
      ["tagArtist", "Artist tags"],
      ["tagCharacter", "Character tags"],
      ["tagGroup", "Group tags"],
      ["tagLanguage", "Language tags"],
    ],
  },
];

export const THEME_KEYS = Object.keys(DEFAULT_THEME);

export const THEME_PRESETS = {
  midnight: { label: "Midnight", colors: { ...DEFAULT_THEME } },
  ocean: {
    label: "Ocean",
    colors: {
      bg: "#061018",
      surface: "#081722",
      panel: "#0c1e2b",
      panelAlt: "#102635",
      panelDeep: "#07131d",
      border: "#163244",
      borderStrong: "#1e455c",
      scrollbar: "#1e455c",
      skeleton: "#123043",
      text: "#e2f2ff",
      textSecondary: "#bfe0f5",
      muted: "#8ab6d1",
      subtle: "#5f8aa6",
      faint: "#40647c",
      accent: "#0284c7",
      accentHover: "#0ea5e9",
      accentText: "#bae6fd",
      accentSoft: "#082f49",
      accentBorder: "#0369a1",
      danger: "#be123c",
      dangerHover: "#e11d48",
      dangerText: "#fda4af",
      dangerSoft: "#881337",
      dangerDeep: "#4c0519",
      success: "#10b981",
      successText: "#6ee7b7",
      successSoft: "#064e3b",
      warning: "#f59e0b",
      warningText: "#fcd34d",
      warningSoft: "#451a03",
      tagArtist: "#8b5cf6",
      tagCharacter: "#14b8a6",
      tagGroup: "#ec4899",
      tagLanguage: "#0ea5e9",
    },
  },
  forest: {
    label: "Forest",
    colors: {
      bg: "#080f0b",
      surface: "#0b1510",
      panel: "#0f1c15",
      panelAlt: "#14251c",
      panelDeep: "#09110d",
      border: "#1c3327",
      borderStrong: "#264536",
      scrollbar: "#264536",
      skeleton: "#183026",
      text: "#e4f2e9",
      textSecondary: "#c4e0cf",
      muted: "#93b8a2",
      subtle: "#6b8f79",
      faint: "#4b6857",
      accent: "#059669",
      accentHover: "#10b981",
      accentText: "#a7f3d0",
      accentSoft: "#022c22",
      accentBorder: "#047857",
      danger: "#be123c",
      dangerHover: "#e11d48",
      dangerText: "#fda4af",
      dangerSoft: "#881337",
      dangerDeep: "#4c0519",
      success: "#22c55e",
      successText: "#86efac",
      successSoft: "#14532d",
      warning: "#f59e0b",
      warningText: "#fcd34d",
      warningSoft: "#451a03",
      tagArtist: "#8b5cf6",
      tagCharacter: "#14b8a6",
      tagGroup: "#ec4899",
      tagLanguage: "#0ea5e9",
    },
  },
  grape: {
    label: "Grape",
    colors: {
      bg: "#0d0814",
      surface: "#120b1b",
      panel: "#171021",
      panelAlt: "#1d1529",
      panelDeep: "#0e0917",
      border: "#2a1f3d",
      borderStrong: "#3a2b54",
      scrollbar: "#3a2b54",
      skeleton: "#241a33",
      text: "#f0e9fb",
      textSecondary: "#d8c8ef",
      muted: "#a992c9",
      subtle: "#7d679b",
      faint: "#5a4870",
      accent: "#9333ea",
      accentHover: "#a855f7",
      accentText: "#e9d5ff",
      accentSoft: "#3b0764",
      accentBorder: "#7e22ce",
      danger: "#be123c",
      dangerHover: "#e11d48",
      dangerText: "#fda4af",
      dangerSoft: "#881337",
      dangerDeep: "#4c0519",
      success: "#10b981",
      successText: "#6ee7b7",
      successSoft: "#064e3b",
      warning: "#f59e0b",
      warningText: "#fcd34d",
      warningSoft: "#451a03",
      tagArtist: "#a855f7",
      tagCharacter: "#14b8a6",
      tagGroup: "#ec4899",
      tagLanguage: "#0ea5e9",
    },
  },
  ember: {
    label: "Ember",
    colors: {
      bg: "#120b08",
      surface: "#18100b",
      panel: "#1e1510",
      panelAlt: "#261b14",
      panelDeep: "#130d0a",
      border: "#38241a",
      borderStrong: "#4d3225",
      scrollbar: "#4d3225",
      skeleton: "#2e2018",
      text: "#fbeee6",
      textSecondary: "#f0d5c5",
      muted: "#c9a28f",
      subtle: "#9c7563",
      faint: "#745346",
      accent: "#ea580c",
      accentHover: "#f97316",
      accentText: "#fed7aa",
      accentSoft: "#431407",
      accentBorder: "#c2410c",
      danger: "#dc2626",
      dangerHover: "#ef4444",
      dangerText: "#fca5a5",
      dangerSoft: "#7f1d1d",
      dangerDeep: "#450a0a",
      success: "#16a34a",
      successText: "#86efac",
      successSoft: "#14532d",
      warning: "#f59e0b",
      warningText: "#fde68a",
      warningSoft: "#451a03",
      tagArtist: "#8b5cf6",
      tagCharacter: "#14b8a6",
      tagGroup: "#ec4899",
      tagLanguage: "#0ea5e9",
    },
  },
  mono: {
    label: "Mono",
    colors: {
      bg: "#0a0a0a",
      surface: "#101010",
      panel: "#161616",
      panelAlt: "#1d1d1d",
      panelDeep: "#0d0d0d",
      border: "#2a2a2a",
      borderStrong: "#3a3a3a",
      scrollbar: "#3a3a3a",
      skeleton: "#242424",
      text: "#ededed",
      textSecondary: "#d4d4d4",
      muted: "#a3a3a3",
      subtle: "#737373",
      faint: "#525252",
      accent: "#525252",
      accentHover: "#6b6b6b",
      accentText: "#e5e5e5",
      accentSoft: "#262626",
      accentBorder: "#525252",
      danger: "#b91c1c",
      dangerHover: "#dc2626",
      dangerText: "#fca5a5",
      dangerSoft: "#7f1d1d",
      dangerDeep: "#450a0a",
      success: "#16a34a",
      successText: "#86efac",
      successSoft: "#14532d",
      warning: "#d97706",
      warningText: "#fcd34d",
      warningSoft: "#451a03",
      tagArtist: "#a3a3a3",
      tagCharacter: "#8a8a8a",
      tagGroup: "#b0b0b0",
      tagLanguage: "#949494",
    },
  },
};

// Presets only override the colours that differ from the default palette;
// fill in the rest so every preset defines every token.
for (const preset of Object.values(THEME_PRESETS)) {
  preset.colors = { ...DEFAULT_THEME, ...preset.colors };
}

const HEX_RE = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i;
const LS_THEME = "nhc_theme";

export function cssVar(key) {
  return `--nhc-${key.replace(/[A-Z]/g, (m) => `-${m.toLowerCase()}`)}`;
}

export function normalizeHex(value) {
  const v = String(value || "").trim();
  if (!HEX_RE.test(v)) return null;
  if (v.length === 4) {
    return `#${v
      .slice(1)
      .split("")
      .map((c) => c + c)
      .join("")}`.toLowerCase();
  }
  return v.toLowerCase();
}

export function normalizeTheme(input) {
  const out = { ...DEFAULT_THEME };
  if (!input || typeof input !== "object") return out;
  for (const key of THEME_KEYS) {
    const hex = normalizeHex(input[key]);
    if (hex) out[key] = hex;
  }
  return out;
}

export function cachedTheme() {
  try {
    const raw = localStorage.getItem(LS_THEME);
    return raw ? normalizeTheme(JSON.parse(raw)) : { ...DEFAULT_THEME };
  } catch {
    return { ...DEFAULT_THEME };
  }
}

// Apply every colour to :root. `persist` keeps a localStorage copy for the
// next launch (the authoritative copy lives in the server settings).
export function applyTheme(input, { persist = true } = {}) {
  const theme = normalizeTheme(input);
  const root = document.documentElement;
  for (const [key, value] of Object.entries(theme)) {
    root.style.setProperty(cssVar(key), value);
  }
  if (persist) {
    try {
      localStorage.setItem(LS_THEME, JSON.stringify(theme));
    } catch {
      /* storage unavailable — theme still applies for this session */
    }
  }
  return theme;
}

export function presetKeyFor(theme) {
  const normalized = normalizeTheme(theme);
  for (const [key, preset] of Object.entries(THEME_PRESETS)) {
    if (THEME_KEYS.every((k) => preset.colors[k] === normalized[k])) return key;
  }
  return "custom";
}
