const BRAND_PATTERNS: Record<string, RegExp> = {
  // Midea Group — prioridade máxima (mais específicos primeiro)
  'Springer Midea': /springer\s?midea|midea\s?springer/i,
  'Midea': /\bmidea\b/i,
  'Carrier': /\bcarrier\b/i,

  // Concorrentes Tier 1
  'LG': /\blg\b/i,
  'Samsung': /\bsamsung\b/i,
  'Gree': /\bgree\b/i,

  // Concorrentes Tier 2
  'Elgin': /\belgin\b/i,
  'TCL': /\btcl\b/i,
  'Philco': /\bphilco\b/i,
  'Consul': /\bconsul\b/i,
  'Agratto': /\bagratto\b/i,

  // Concorrentes Tier 3
  'Fujitsu': /\bfujitsu\b/i,
  'Hisense': /\bhisense\b/i,
  'Daikin': /\bdaikin\b/i,
  'Electrolux': /\belectrolux\b/i,
  'Komeco': /\bkomeco\b/i,
  'Springer': /\bspringer\b/i,
  'Hitachi': /\bhitachi\b/i,
  'York': /\byork\b/i,
};

export function detectBrand(title: string): string | null {
  for (const [brand, pattern] of Object.entries(BRAND_PATTERNS)) {
    if (pattern.test(title)) return brand;
  }
  return null;
}

export function isMideaGroup(brand: string | null): boolean {
  return brand === 'Midea' || brand === 'Springer Midea' || brand === 'Carrier';
}
