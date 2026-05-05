// Desktop bloqueado por Akamai — usar versão mobile
export const MAGALU_BASE_URL = 'https://m.magazineluiza.com.br/busca';
export const SHOPEE_BASE_URL = 'https://shopee.com.br/search';

export const DELAYS = {
  MAGALU_MS: parseInt(process.env.MAGALU_DELAY_MS || '3000'),
  SHOPEE_MS: parseInt(process.env.SHOPEE_DELAY_MS || '2000'),
  BASE_MS: parseInt(process.env.BASE_DELAY_MS || '2000'),
  MAX_MS: parseInt(process.env.MAX_DELAY_MS || '5000'),
  SOFT_BLOCK_MS: 45000,
  BETWEEN_QUERIES_MS: 5000,
};

export const RETRY = {
  ATTEMPTS: parseInt(process.env.RETRY_ATTEMPTS || '3'),
  BACKOFF_MS: 10000,
};

export const PAGE_TIMEOUT_MS = 60000;
export const MAX_PAGES = parseInt(process.env.MAX_PAGES_PER_QUERY || '5');

export const USER_AGENTS = [
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
  'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
  'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
];

// User-agents mobile — usados pelo MagaluScraper (m.magazineluiza.com.br)
export const MOBILE_USER_AGENTS = [
  'Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1',
  'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.6099.144 Mobile Safari/537.36',
  'Mozilla/5.0 (Linux; Android 13; Samsung Galaxy S23) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.6045.163 Mobile Safari/537.36',
];

export const SUPABASE_BATCH_SIZE = 500;
