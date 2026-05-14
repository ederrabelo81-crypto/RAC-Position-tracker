import puppeteer from 'puppeteer-extra';
import StealthPlugin from 'puppeteer-extra-plugin-stealth';
import { Browser, Page } from 'puppeteer';
import fs from 'fs';
import path from 'path';
import { RacProduct, ScraperConfig } from '../types';
import { USER_AGENTS } from '../config/constants';
import { loadCookies, stripVolatileCookies } from '../utils/session-manager';
import { logger } from '../utils/logger';

puppeteer.use(StealthPlugin());

export abstract class BaseScraper {
  protected browser: Browser | null = null;
  protected page: Page | null = null;
  protected lastRequestTime = 0;

  protected readonly config: ScraperConfig;
  protected abstract readonly siteDomain: string;

  constructor(config?: Partial<ScraperConfig>) {
    this.config = {
      headless: config?.headless ?? true,
      maxPages: config?.maxPages ?? 2,
      delayMs: config?.delayMs ?? 3000,
      retryAttempts: config?.retryAttempts ?? 3,
    };
  }

  protected randomUserAgent(): string {
    return USER_AGENTS[Math.floor(Math.random() * USER_AGENTS.length)];
  }

  protected async launch(isMobile = false): Promise<void> {
    this.browser = await puppeteer.launch({
      headless: this.config.headless,
      args: [
        '--no-sandbox',
        '--disable-setuid-sandbox',
        '--disable-dev-shm-usage',
        '--disable-accelerated-2d-canvas',
        '--disable-gpu',
        '--disable-blink-features=AutomationControlled',
        '--window-size=1920,1080',
      ],
    });

    this.page = await this.browser.newPage();

    await this.page.setUserAgent(this.randomUserAgent());
    await this.page.setExtraHTTPHeaders({
      'Accept-Language': 'pt-BR,pt;q=0.9,en;q=0.8',
      'Accept-Encoding': 'gzip, deflate, br',
      'sec-ch-ua': '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
      'sec-ch-ua-mobile': isMobile ? '?1' : '?0',
      'sec-ch-ua-platform': isMobile ? '"Android"' : '"Windows"',
    });

    await this.page.setViewport(
      isMobile
        ? { width: 390, height: 844, isMobile: true }
        : { width: 1920, height: 1080 }
    );

    // Injeta cookies de sessão salvos — descartando os cookies voláteis do
    // Akamai (_abck, bm_*, etc.) e os já expirados. Reinjetar um _abck/bm_sz
    // antigo faz o Akamai bloquear na hora; o browser ganha os seus próprios
    // na navegação de aquecimento (warmUp).
    const savedCookies = loadCookies(this.siteDomain);
    if (savedCookies && savedCookies.length > 0) {
      const { kept, droppedVolatile, droppedExpired } = stripVolatileCookies(savedCookies);
      if (kept.length > 0) {
        // setCookie aceita CookieParam[] — cast necessário por incompatibilidade de versão do puppeteer
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        await (this.page as any).setCookie(...kept);
      }
      logger.debug(
        `Cookies ${this.siteDomain}: ${kept.length} aplicados, ` +
        `${droppedVolatile} voláteis (Akamai) + ${droppedExpired} expirados descartados`
      );
    }

    // Bloqueia recursos desnecessários para acelerar
    await this.page.setRequestInterception(true);
    this.page.on('request', (req) => {
      const type = req.resourceType();
      if (['media', 'font', 'websocket'].includes(type)) {
        req.abort();
      } else {
        req.continue();
      }
    });
  }

  protected async close(): Promise<void> {
    try {
      await this.browser?.close();
    } catch {
      // Silencia erros de fechamento
    }
    this.browser = null;
    this.page = null;
  }

  /**
   * Navegação de aquecimento — chamada após launch(), antes da 1ª busca.
   * Subclasses que enfrentam anti-bot (ex.: Akamai) sobrescrevem para visitar
   * a home e deixar o site emitir uma sessão fresca. Base: no-op.
   */
  protected async warmUp(): Promise<void> {
    // no-op — sobrescrito por scrapers que precisam de sessão dinâmica
  }

  protected async respectRateLimit(): Promise<void> {
    const now = Date.now();
    const elapsed = now - this.lastRequestTime;
    const jitter = Math.floor(Math.random() * 1000);
    const waitTime = Math.max(0, this.config.delayMs + jitter - elapsed);

    if (waitTime > 0) {
      await this.sleep(waitTime);
    }

    this.lastRequestTime = Date.now();
  }

  protected sleep(ms: number): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  protected randomDelay(min: number, max: number): number {
    return Math.floor(Math.random() * (max - min + 1)) + min;
  }

  protected async humanScroll(): Promise<void> {
    if (!this.page) return;
    await this.page.evaluate(async () => {
      await new Promise<void>((resolve) => {
        let totalHeight = 0;
        const distance = 300;
        const timer = setInterval(() => {
          window.scrollBy(0, distance);
          totalHeight += distance;
          if (totalHeight >= document.body.scrollHeight * 0.8) {
            clearInterval(timer);
            resolve();
          }
        }, 200);
      });
    });
  }

  /**
   * Detecção de bloqueio real — evita falsos positivos.
   * A palavra "verificação" aparece em HTML normal (ex: "Verificar disponibilidade").
   * Só marca como bloqueado se encontrar padrões ESPECÍFICOS de página de desafio.
   */
  protected isSoftBlocked(html: string): boolean {
    const lower = html.toLowerCase();

    // Página de challenge com formulário ou iframe de CAPTCHA
    const hasCaptchaChallenge =
      /(<title[^>]*>.*?(captcha|verificação de segurança|acesso bloqueado).*?<\/title>)/i.test(html) ||
      /iframe[^>]+recaptcha/i.test(html) ||
      /g-recaptcha/i.test(html) ||
      /cf-challenge-running/i.test(html); // Cloudflare

    // Página de "Access Denied" / desafio do Akamai Bot Manager
    const hasAkamaiBlock =
      lower.includes('reference&#32;#') ||
      lower.includes('reference #') ||
      lower.includes('pardon our interruption') ||
      lower.includes('errors.edgesuite.net');

    // Página completamente vazia ou com menos de 5KB (site não carregou)
    const isTooShort = html.length < 5000;

    // Status de bloqueio explícito na URL ou body
    const hasExplicitBlock =
      lower.includes('access denied') ||
      lower.includes('you have been blocked') ||
      lower.includes('403 forbidden');

    return hasCaptchaChallenge || hasAkamaiBlock || isTooShort || hasExplicitBlock;
  }

  /**
   * Salva o HTML de uma página (ex.: página de bloqueio) em logs/ para
   * diagnóstico posterior. Retorna o caminho do arquivo, ou null se falhar.
   */
  protected dumpHtml(html: string, label: string): string | null {
    try {
      const dir = path.resolve(process.cwd(), 'logs');
      if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
      const safeLabel = label.replace(/[^a-z0-9_-]+/gi, '_').slice(0, 60);
      const stamp = new Date().toISOString().replace(/[:.]/g, '-');
      const file = path.join(dir, `block_${safeLabel}_${stamp}.html`);
      fs.writeFileSync(file, html, 'utf-8');
      return file;
    } catch (err) {
      logger.warn(`Falha ao salvar dump HTML: ${err instanceof Error ? err.message : String(err)}`);
      return null;
    }
  }

  protected async withRetry<T>(
    fn: () => Promise<T>,
    label: string
  ): Promise<T | null> {
    for (let attempt = 1; attempt <= this.config.retryAttempts; attempt++) {
      try {
        return await fn();
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        logger.warn(`${label} — tentativa ${attempt}/${this.config.retryAttempts}: ${msg}`);
        if (attempt < this.config.retryAttempts) {
          await this.sleep(attempt * 5000);
        }
      }
    }
    logger.error(`${label} — falhou após ${this.config.retryAttempts} tentativas`);
    return null;
  }

  abstract scrape(query: string, maxPages?: number): Promise<RacProduct[]>;
}
