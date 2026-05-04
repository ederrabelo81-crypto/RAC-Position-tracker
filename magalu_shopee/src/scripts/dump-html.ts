/**
 * Salva o HTML real de uma página de busca para diagnóstico de seletores.
 *
 * Uso:
 *   npx ts-node src/scripts/dump-html.ts magalu
 *   npx ts-node src/scripts/dump-html.ts shopee
 *
 * Gera: logs/dump_magalu.html  (abra no VS Code ou editor de texto para inspecionar)
 */

import 'dotenv/config';
import puppeteer from 'puppeteer-extra';
import StealthPlugin from 'puppeteer-extra-plugin-stealth';
import fs from 'fs';
import path from 'path';
import { loadCookies } from '../utils/session-manager';
import { MOBILE_USER_AGENTS, USER_AGENTS } from '../config/constants';
import { logger } from '../utils/logger';

puppeteer.use(StealthPlugin());

const TARGETS: Record<string, {
  url: string;
  domain: string;
  mobile: boolean;
  waitEvent: 'networkidle2' | 'domcontentloaded';
}> = {
  magalu: {
    // Mobile version — desktop (www.) retorna 403 Akamai
    url: 'https://m.magazineluiza.com.br/busca/ar+condicionado+split+12000+btus/',
    domain: 'magazineluiza.com.br',
    mobile: true,
    waitEvent: 'networkidle2',
  },
  shopee: {
    url: 'https://shopee.com.br/search?keyword=ar+condicionado+split+12000+btus',
    domain: 'shopee.com.br',
    mobile: false,
    waitEvent: 'networkidle2',
  },
};

// Todos os seletores candidatos a testar no DOM
const CANDIDATE_SELECTORS_MAGALU = [
  // data-testid — desktop e mobile podem compartilhar
  'li[data-testid="product-card"]',
  'a[data-testid="product-card-container"]',
  '[data-testid="product-card"]',
  // classes comuns no mobile Magalu
  'li[class*="ProductCard"]',
  'li[class*="product-card"]',
  'div[class*="ProductCard"]',
  'section[class*="product"]',
  // links de produto — mais resiliente
  'a[href*="/p/"]',
  'a[href*="magazineluiza.com.br/p/"]',
  // estrutura genérica de lista
  'ol li a[href]',
  'ul li a[href]',
  // React styled-components (classes geradas sc-*)
  'li[class*="sc-"]',
  'div[class*="sc-"] a[href]',
];

const CANDIDATE_SELECTORS_SHOPEE = [
  'div.col-xs-2-4.shopee-search-item-result__item',
  'div[class*="shopee-search-item"]',
  'li[class*="shopee-search-item"]',
  'div[data-sqe="item"]',
  'a[data-sqe="link"]',
  'div[class*="item-card"]',
];

async function dumpHtml(target: string): Promise<void> {
  const config = TARGETS[target];
  if (!config) {
    logger.error(`Alvo inválido: "${target}". Use: ${Object.keys(TARGETS).join(', ')}`);
    process.exit(1);
  }

  const ua = config.mobile
    ? MOBILE_USER_AGENTS[0]
    : USER_AGENTS[0];

  const browser = await puppeteer.launch({
    headless: true,
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage'],
  });

  const page = await browser.newPage();
  await page.setUserAgent(ua);
  await page.setExtraHTTPHeaders({
    'Accept-Language': 'pt-BR,pt;q=0.9',
    Referer: 'https://www.google.com.br/',
  });

  if (config.mobile) {
    await page.setViewport({ width: 390, height: 844, isMobile: true, hasTouch: true });
  } else {
    await page.setViewport({ width: 1920, height: 1080 });
  }

  // Carrega cookies salvos se existirem
  const cookies = loadCookies(config.domain);
  if (cookies) {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    await (page as any).setCookie(...cookies);
    logger.info(`Cookies carregados: ${cookies.length}`);
  }

  logger.info(`Navegando para: ${config.url}`);
  logger.info(`User-Agent: ${ua.slice(0, 60)}...`);

  await page.goto(config.url, { waitUntil: config.waitEvent, timeout: 60_000 });
  await new Promise((r) => setTimeout(r, 4000));

  // Scroll para carregar lazy content
  await page.evaluate(async () => {
    await new Promise<void>((resolve) => {
      let total = 0;
      const timer = setInterval(() => {
        window.scrollBy(0, 400);
        total += 400;
        if (total >= document.body.scrollHeight) { clearInterval(timer); resolve(); }
      }, 200);
    });
  });

  await new Promise((r) => setTimeout(r, 2000));

  const html = await page.content();
  const finalUrl = page.url();
  const outDir = path.resolve(process.cwd(), 'logs');
  if (!fs.existsSync(outDir)) fs.mkdirSync(outDir, { recursive: true });

  const outFile = path.join(outDir, `dump_${target}.html`);
  fs.writeFileSync(outFile, html, 'utf-8');

  logger.info(`HTML salvo: ${outFile} (${(html.length / 1024).toFixed(1)} KB)`);
  logger.info(`URL final (após redirects): ${finalUrl}`);

  if (html.length < 10000) {
    logger.warn('⚠ HTML muito pequeno (<10KB) — provavelmente página de bloqueio ou redirecionamento');
    logger.warn('  Tente primeiro capturar a sessão: npm run session:magalu');
  }

  const candidates = target === 'magalu'
    ? CANDIDATE_SELECTORS_MAGALU
    : CANDIDATE_SELECTORS_SHOPEE;

  // Conta elementos com cada seletor candidato
  const counts = await page.evaluate((sels: string[]) => {
    return sels.map((sel) => {
      try {
        return { sel, count: document.querySelectorAll(sel).length };
      } catch {
        return { sel, count: -1 };
      }
    });
  }, candidates);

  logger.info('\n=== CONTAGEM DE ELEMENTOS (diagnóstico de seletores) ===');
  counts.forEach(({ sel, count }) => {
    const status = count > 0 ? '✓' : count === -1 ? '?' : '✗';
    const highlight = count > 0 ? ' ◄ VÁLIDO' : '';
    logger.info(`  ${status} ${sel.padEnd(55)} → ${count}${highlight}`);
  });

  // Mostra primeiros links de produto encontrados
  const sampleLinks = await page.evaluate(() => {
    const links = Array.from(document.querySelectorAll('a[href]'))
      .map((a) => (a as HTMLAnchorElement).href)
      .filter((h) => h.includes('/p/') || h.includes('shopee.com.br/'))
      .slice(0, 5);
    return links;
  });

  if (sampleLinks.length > 0) {
    logger.info('\n=== PRIMEIROS LINKS DE PRODUTO ENCONTRADOS ===');
    sampleLinks.forEach((l) => logger.info(`  ${l}`));
  }

  await browser.close();
}

const target = process.argv[2] || 'magalu';
dumpHtml(target).catch((err) => {
  logger.error(`Erro: ${err}`);
  process.exit(1);
});
