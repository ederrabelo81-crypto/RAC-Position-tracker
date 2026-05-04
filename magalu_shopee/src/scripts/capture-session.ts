/**
 * Script de captura de sessão.
 *
 * Abre o browser VISÍVEL para você interagir com o site e salva os cookies.
 *
 * Uso:
 *   npm run session:magalu   → captura sessão do Magalu mobile
 *   npm run session:shopee   → captura sessão do Shopee
 */

import 'dotenv/config';
import puppeteer from 'puppeteer-extra';
import StealthPlugin from 'puppeteer-extra-plugin-stealth';
import { saveCookies } from '../utils/session-manager';
import { MOBILE_USER_AGENTS, USER_AGENTS } from '../config/constants';
import { logger } from '../utils/logger';

puppeteer.use(StealthPlugin());

const TARGETS: Record<string, {
  startUrl: string;
  domain: string;
  waitMs: number;
  mobile: boolean;
  instructions: string[];
}> = {
  magalu: {
    // Usamos m. (mobile) porque www. retorna 403 Akamai para headless
    startUrl: 'https://m.magazineluiza.com.br/busca/ar+condicionado/',
    domain: 'magazineluiza.com.br',
    waitMs: 90_000,
    mobile: true,
    instructions: [
      '1. A página de busca do Magalu (mobile) vai abrir',
      '2. Aceite o banner de cookies se aparecer',
      '3. Role a página para baixo até os produtos aparecerem',
      '4. Confirme que vê cards de produtos na tela',
      '5. Os cookies são salvos automaticamente após 90s ou ao fechar',
    ],
  },
  shopee: {
    // Começa na HOME, não na busca — evita trigger imediato do bot-challenge
    startUrl: 'https://shopee.com.br',
    domain: 'shopee.com.br',
    waitMs: 240_000,
    mobile: false,
    instructions: [
      '1. A HOME do Shopee vai abrir',
      '',
      '   ⚠ SE aparecer uma página de verificação (/verify/traffic/error):',
      '      → NÃO feche o browser',
      '      → Clique no botão "Verificar" ou resolva o slider/puzzle',
      '      → Aguarde redirecionar para a home normalmente',
      '',
      '2. Faça login com seu usuário e senha',
      '3. Após logar, clique na busca e pesquise "ar condicionado"',
      '4. Confirme que vê produtos na tela',
      '5. Os cookies são salvos automaticamente após 4min ou ao fechar',
    ],
  },
};

async function captureSession(target: string): Promise<void> {
  const config = TARGETS[target];
  if (!config) {
    logger.error(`Alvo inválido: "${target}". Use: ${Object.keys(TARGETS).join(', ')}`);
    process.exit(1);
  }

  logger.info(`=== CAPTURA DE SESSÃO: ${target.toUpperCase()} ===`);
  logger.info(`Tempo disponível: ${config.waitMs / 1000}s`);
  logger.info('\nInstruções:');
  config.instructions.forEach((line) => logger.info(`  ${line}`));
  logger.info('');

  const ua = config.mobile ? MOBILE_USER_AGENTS[0] : USER_AGENTS[0];

  const browser = await puppeteer.launch({
    headless: false,
    defaultViewport: null,
    args: [
      '--no-sandbox',
      '--start-maximized',
      '--disable-blink-features=AutomationControlled',
      // Sem --disable-extensions para parecer mais humano
    ],
  });

  const page = await browser.newPage();

  await page.setUserAgent(ua);
  await page.setExtraHTTPHeaders({ 'Accept-Language': 'pt-BR,pt;q=0.9' });

  if (config.mobile) {
    await page.setViewport({ width: 390, height: 844, isMobile: true, hasTouch: true });
  }

  await page.goto(config.startUrl, { waitUntil: 'domcontentloaded', timeout: 30_000 });

  logger.info('Browser aberto. Siga as instruções acima...');
  logger.info(`(Você tem ${config.waitMs / 1000} segundos — os cookies são salvos ao final)`);

  // Aguarda o tempo configurado OU fechamento manual do browser
  let browserClosed = false;

  await new Promise<void>((resolve) => {
    const timeout = setTimeout(resolve, config.waitMs);

    browser.on('disconnected', () => {
      browserClosed = true;
      clearTimeout(timeout);
      resolve();
    });
  });

  if (browserClosed) {
    logger.warn('Browser fechado manualmente antes do tempo limite.');
    logger.warn('Os cookies NÃO foram salvos. Execute novamente e aguarde o tempo completo.');
    return;
  }

  // Salva os cookies
  let saved = false;
  try {
    const cookies = await page.cookies();
    if (cookies.length > 0) {
      saveCookies(config.domain, cookies);
      saved = true;
      logger.info(`✓ ${cookies.length} cookies salvos para ${config.domain}`);
    } else {
      logger.warn('Nenhum cookie encontrado — o site pode não ter carregado corretamente.');
    }
  } catch {
    logger.warn('Falha ao capturar cookies (browser pode ter sido fechado).');
  }

  try {
    await browser.close();
  } catch {
    // já fechado
  }

  if (saved) {
    logger.info(`\n✓ Sessão capturada com sucesso!`);
    logger.info(`  Execute agora: npm run dump:${target}  (para confirmar seletores)`);
    logger.info(`  Depois: npm run scrape:test -- --platforms ${target}`);
  } else {
    logger.error('\n✗ Sessão NÃO salva.');
    logger.info('  Dica: Execute novamente, interaja com o site e aguarde o tempo completo.');
    logger.info('  Para Shopee: NÃO feche o browser ao ver a página de verificação — resolva o desafio.');
  }
}

const target = process.argv[2] || 'magalu';
captureSession(target).catch((err) => {
  logger.error(`Erro na captura de sessão: ${err}`);
  process.exit(1);
});
