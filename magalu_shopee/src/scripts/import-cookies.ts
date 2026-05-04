/**
 * Importa cookies exportados do Chrome para o formato usado pelo scraper.
 *
 * Uso:
 *   1. No Chrome, instale a extensão "Cookie-Editor" (gratuita)
 *      Link: https://chrome.google.com/webstore/detail/cookie-editor/hlkenndednhfkekhgcdicdfddnkalmdm
 *
 *   2. Navegue para shopee.com.br e faça login normalmente
 *
 *   3. Clique no ícone da extensão → botão "Export" → "Export as JSON"
 *      Isso copia o JSON para o clipboard
 *
 *   4. Cole o JSON em um arquivo: sessions/shopee_chrome.json
 *      (Ctrl+V no Notepad, salve como shopee_chrome.json na pasta sessions/)
 *
 *   5. Execute:
 *      npx ts-node src/scripts/import-cookies.ts shopee
 *
 * O script converte o formato Cookie-Editor → formato Puppeteer e salva em sessions/shopee.json
 */

import 'dotenv/config';
import fs from 'fs';
import path from 'path';
import { logger } from '../utils/logger';

const SESSIONS_DIR = path.resolve(process.cwd(), 'sessions');

// Cookie-Editor exporta neste formato
interface ChromeCookie {
  name: string;
  value: string;
  domain: string;
  path: string;
  expires: number;
  httpOnly: boolean;
  secure: boolean;
  sameSite: string;
  hostOnly?: boolean;
  session?: boolean;
  storeId?: string;
}

// Puppeteer CookieParam
interface PuppeteerCookie {
  name: string;
  value: string;
  domain: string;
  path: string;
  expires: number;
  httpOnly: boolean;
  secure: boolean;
  sameSite?: 'Strict' | 'Lax' | 'None';
}

function convertSameSite(raw: string | undefined): 'Strict' | 'Lax' | 'None' | undefined {
  if (!raw) return undefined;
  const lower = raw.toLowerCase();
  if (lower === 'strict') return 'Strict';
  if (lower === 'lax') return 'Lax';
  if (lower === 'no_restriction' || lower === 'none') return 'None';
  return undefined;
}

function convertCookies(chromeCookies: ChromeCookie[]): PuppeteerCookie[] {
  return chromeCookies.map((c) => ({
    name: c.name,
    value: c.value,
    domain: c.domain.startsWith('.') ? c.domain : `.${c.domain}`,
    path: c.path || '/',
    expires: c.session ? -1 : (c.expires || -1),
    httpOnly: c.httpOnly || false,
    secure: c.secure || false,
    sameSite: convertSameSite(c.sameSite),
  }));
}

function importCookies(target: string): void {
  const sourceFile = path.join(SESSIONS_DIR, `${target}_chrome.json`);
  const destFile = path.join(SESSIONS_DIR, `${target}.json`);

  if (!fs.existsSync(sourceFile)) {
    logger.error(`Arquivo não encontrado: ${sourceFile}`);
    logger.info('');
    logger.info('Siga as instruções no topo deste arquivo para exportar cookies do Chrome.');
    logger.info(`Depois salve o JSON em: ${sourceFile}`);
    process.exit(1);
  }

  let raw: unknown;
  try {
    raw = JSON.parse(fs.readFileSync(sourceFile, 'utf-8'));
  } catch {
    logger.error(`Erro ao ler ${sourceFile} — verifique se é um JSON válido`);
    process.exit(1);
  }

  if (!Array.isArray(raw)) {
    logger.error('O arquivo deve conter um array JSON de cookies');
    process.exit(1);
  }

  const converted = convertCookies(raw as ChromeCookie[]);
  fs.writeFileSync(destFile, JSON.stringify(converted, null, 2), 'utf-8');

  logger.info(`✓ ${converted.length} cookies convertidos → ${destFile}`);
  logger.info(`\n  Execute agora: npm run dump:${target}`);
}

const target = process.argv[2] || 'shopee';
importCookies(target);
