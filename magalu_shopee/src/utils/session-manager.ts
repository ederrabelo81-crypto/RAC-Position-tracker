import fs from 'fs';
import path from 'path';
import { CookieParam } from 'puppeteer';
import { logger } from './logger';

const SESSIONS_DIR = path.resolve(process.cwd(), 'sessions');

function sessionPath(domain: string): string {
  const slug = domain
    .replace(/^www\./, '')
    .replace(/\./g, '_')
    .replace(/_com_br$/, '')
    .replace(/_$/, '');
  return path.join(SESSIONS_DIR, `${slug}.json`);
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function saveCookies(domain: string, cookies: any[]): void {
  if (!fs.existsSync(SESSIONS_DIR)) {
    fs.mkdirSync(SESSIONS_DIR, { recursive: true });
  }
  const file = sessionPath(domain);
  fs.writeFileSync(file, JSON.stringify(cookies, null, 2), 'utf-8');
  logger.info(`Session: ${cookies.length} cookies salvos em ${file}`);
}

export function loadCookies(domain: string): CookieParam[] | null {
  const file = sessionPath(domain);
  if (!fs.existsSync(file)) {
    logger.debug(`Session: Sem cookies para ${domain}`);
    return null;
  }

  try {
    const raw = fs.readFileSync(file, 'utf-8');
    const cookies = JSON.parse(raw) as CookieParam[];
    logger.info(`Session: ${cookies.length} cookies carregados de ${file}`);
    return cookies;
  } catch (err) {
    logger.warn(`Session: Falha ao ler cookies de ${file}: ${err}`);
    return null;
  }
}

/**
 * Identifica cookies voláteis que NÃO devem ser reaproveitados de uma sessão
 * salva em disco — basicamente os do Akamai Bot Manager.
 *
 * `_abck`, `bm_*`, `ak_bmsc` e `AKA_A2` têm vida curta e ficam atrelados ao
 * fingerprint do browser + IP que os emitiu. Reinjetar um `_abck` antigo (ou
 * um `bm_sz` já expirado) faz o Akamai marcar a sessão como bot logo na
 * primeira requisição — é pior do que não mandar cookie nenhum. O browser
 * deve ganhar os seus próprios via navegação de aquecimento (warm-up).
 */
function isVolatileCookie(name: string): boolean {
  return (
    name === '_abck' ||
    name === 'AKA_A2' ||
    name.startsWith('bm_') ||
    name.startsWith('ak_')
  );
}

export interface CookieFilterResult {
  kept: CookieParam[];
  droppedVolatile: number;
  droppedExpired: number;
}

/**
 * Remove cookies do Akamai Bot Manager e cookies já expirados de uma lista
 * carregada do disco. Mantém os cookies duráveis e inofensivos (consentimento,
 * CEP/região, analytics) para que a busca continue localizada.
 */
export function stripVolatileCookies(cookies: CookieParam[]): CookieFilterResult {
  const nowSec = Date.now() / 1000;
  let droppedVolatile = 0;
  let droppedExpired = 0;

  const kept = cookies.filter((c) => {
    const name = c.name || '';
    if (isVolatileCookie(name)) {
      droppedVolatile++;
      return false;
    }
    if (typeof c.expires === 'number' && c.expires > 0 && c.expires < nowSec) {
      droppedExpired++;
      return false;
    }
    return true;
  });

  return { kept, droppedVolatile, droppedExpired };
}

export function cookiesExist(domain: string): boolean {
  return fs.existsSync(sessionPath(domain));
}

export function deleteCookies(domain: string): void {
  const file = sessionPath(domain);
  if (fs.existsSync(file)) {
    fs.unlinkSync(file);
    logger.info(`Session: Cookies de ${domain} removidos`);
  }
}
