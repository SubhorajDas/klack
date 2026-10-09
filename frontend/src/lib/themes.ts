export const themes = [
  { id: 'light', name: 'Light', description: 'Bright and familiar', dark: false },
  { id: 'dark', name: 'Dark', description: 'Soft charcoal and violet', dark: true },
  { id: 'midnight', name: 'Midnight', description: 'Deep navy and starlight', dark: true },
  { id: 'ocean', name: 'Ocean', description: 'Cool blues and sea glass', dark: false },
  { id: 'forest', name: 'Forest', description: 'Evergreen after hours', dark: true },
  { id: 'rose', name: 'Rose', description: 'Warm pinks and berry', dark: false },
  { id: 'sand', name: 'Sand', description: 'Paper, honey, and earth', dark: false },
] as const;

export type ThemeId = (typeof themes)[number]['id'];
export const themeStorageKey = 'klack:theme';

export function readTheme(): ThemeId {
  try {
    const saved = localStorage.getItem(themeStorageKey);
    return themes.find((theme) => theme.id === saved)?.id ?? 'light';
  } catch {
    return 'light';
  }
}

export function applyTheme(theme: ThemeId) {
  document.documentElement.dataset.theme = theme;
}

export function saveTheme(theme: ThemeId) {
  applyTheme(theme);
  try {
    localStorage.setItem(themeStorageKey, theme);
  } catch {
    // Still apply the selection when browser storage is unavailable.
  }
}

// Apply the saved palette before the first paint, including on the sign-in screen.
export const themeBootstrap = `try{var t=localStorage.getItem('${themeStorageKey}');document.documentElement.dataset.theme=${JSON.stringify(themes.map((theme) => theme.id))}.includes(t)?t:'light'}catch(e){document.documentElement.dataset.theme='light'}`;
