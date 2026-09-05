/**
 * Colours and spacing.
 *
 * Deliberately neutral: the only saturated colours in the palette are the
 * three verdicts, so a BUY reads as information rather than as decoration.
 * Everything else is greyscale, which keeps a screen full of numbers legible.
 */

import { useColorScheme } from 'react-native';

export interface Theme {
  dark: boolean;
  bg: string;
  card: string;
  border: string;
  text: string;
  muted: string;
  faint: string;
  accent: string;
  buy: string;
  hold: string;
  sell: string;
  unknown: string;
  warn: string;
  warnBg: string;
}

const light: Theme = {
  dark: false,
  bg: '#f6f6f5',
  card: '#ffffff',
  border: '#e2e2df',
  text: '#16160f',
  muted: '#5f5f58',
  faint: '#8b8b83',
  accent: '#1f5f4f',
  buy: '#1a6b4a',
  hold: '#7a6a2e',
  sell: '#9b3232',
  unknown: '#8b8b83',
  warn: '#7a4a12',
  warnBg: '#fdf3e3',
};

const dark: Theme = {
  dark: true,
  bg: '#121211',
  card: '#1c1c1a',
  border: '#2e2e2b',
  text: '#eeeee8',
  muted: '#a5a59c',
  faint: '#77776f',
  accent: '#6cc4a5',
  buy: '#5fbf90',
  hold: '#c3ab5c',
  sell: '#e08585',
  unknown: '#77776f',
  warn: '#e0b878',
  warnBg: '#2a2113',
};

export function useTheme(): Theme {
  return useColorScheme() === 'dark' ? dark : light;
}

export function verdictColour(theme: Theme, verdict: string): string {
  switch (verdict) {
    case 'BUY':
      return theme.buy;
    case 'SELL':
      return theme.sell;
    case 'HOLD':
      return theme.hold;
    default:
      // UNKNOWN is not a neutral reading -- the input was missing -- so it is
      // greyed rather than given the HOLD colour.
      return theme.unknown;
  }
}

export const spacing = { xs: 4, sm: 8, md: 12, lg: 16, xl: 24 };
export const radius = 10;
