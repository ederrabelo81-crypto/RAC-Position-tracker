// Desktop bloqueado por Akamai — usar versão mobile
export const MAGALU_BASE_URL = 'https://m.magazineluiza.com.br/busca';
// Home mobile — usada como navegação de aquecimento (warm-up) para o browser
// ganhar uma sessão Akamai fresca antes de ir direto para a busca.
export const MAGALU_HOME_URL = 'https://m.magazineluiza.com.br/';
export const SHOPEE_BASE_URL = 'https://shopee.com.br/search';

export const DELAYS = {
  MAGALU_MS: parseInt(process.env.MAGALU_DELAY_MS || '3000'),
  SHOPEE_MS: parseInt(process.env.SHOPEE_DELAY_MS || '2000'),
  BASE_MS: parseInt(process.env.BASE_DELAY_MS || '2000'),
  MAX_MS: parseInt(process.env.MAX_DELAY_MS || '5000'),
  SOFT_BLOCK_MS: 45000,
  BETWEEN_QUERIES_MS: 5000,
  // Akamai 403: o block page pede "tente novamente em 1 minuto".
  // Reiniciar o browser antes disso só recebe outro 403 imediato.
  BLOCK_RESTART_MIN_MS: 70000,
  BLOCK_RESTART_MAX_MS: 90000,
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

// User-agents mobile — usados pelo MagaluScraper (m.magazineluiza.com.br).
// Apenas Android/Chrome: o engine do Puppeteer é Chromium, então uma UA de
// iPhone/Safari geraria fingerprint inconsistente com os client hints (UA-CH)
// que o browser envia — exatamente o tipo de divergência que o Akamai detecta.
export const MOBILE_USER_AGENTS = [
  'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.6099.144 Mobile Safari/537.36',
  'Mozilla/5.0 (Linux; Android 13; SM-S911B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.6045.163 Mobile Safari/537.36',
  'Mozilla/5.0 (Linux; Android 14; SM-A546E) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.6167.143 Mobile Safari/537.36',
];

export const SUPABASE_BATCH_SIZE = 500;
