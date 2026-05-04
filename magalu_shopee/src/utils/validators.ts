const BLACKLIST_KEYWORDS = [
  'ventilador',
  'climatizador',
  'aquecedor',
  'peça',
  'acessório',
  'controle remoto',
  'suporte',
  'kit instalação',
  'dreno',
  'cassete',
  'duto',
  'multi-split',
  'vrf',
  'chiller',
  'evaporativo',
  'cobertura',
  'capa proteção',
];

const WHITELIST_KEYWORDS = [
  'split',
  'janela',
  'portátil',
  'portatil',
  'piso teto',
  'piso-teto',
  'ar condicionado',
  'ar-condicionado',
  'btu',
  'inverter',
  'hi-wall',
  'hiwall',
];

export function isValidRacProduct(title: string): boolean {
  const lower = title.toLowerCase();

  if (BLACKLIST_KEYWORDS.some((kw) => lower.includes(kw))) {
    return false;
  }

  return WHITELIST_KEYWORDS.some((kw) => lower.includes(kw));
}

export function extractBTU(title: string): number | null {
  // Matches: "12000 BTUs", "12.000 BTUs", "12k BTU", "12000BTU"
  const match = title.match(/(\d{1,2}[.,]?\d{0,3})\s*k?\s*btus?/i);
  if (!match) return null;

  const raw = match[1].replace(/[.,]/g, '');
  const value = parseInt(raw, 10);

  // Se valor < 100, assume que é em milhares (12k → 12000)
  if (value > 0 && value < 100) return value * 1000;
  if (value >= 1000 && value <= 60000) return value;

  return null;
}

export function extractProductType(title: string): string | null {
  const lower = title.toLowerCase();

  if (/piso[\s-]teto/.test(lower)) return 'Piso-Teto';
  if (/janela/.test(lower)) return 'Janela';
  if (/portát|portati/.test(lower)) return 'Portátil';
  if (/split|hi[\s-]?wall/.test(lower)) return 'Split Hi-Wall';

  return null;
}

export function parsePrice(raw: string | null | undefined): number | null {
  if (!raw) return null;
  const cleaned = raw
    .replace(/[R$\s\xa0]/g, '')  // Remove R$, espaços, non-breaking space
    .replace(/\./g, '')           // Remove separador de milhar
    .replace(',', '.');           // Vírgula → ponto decimal
  const value = parseFloat(cleaned);
  return isNaN(value) ? null : value;
}
