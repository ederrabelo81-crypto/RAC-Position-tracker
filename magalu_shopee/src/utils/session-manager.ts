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
