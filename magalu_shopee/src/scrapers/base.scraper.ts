import puppeteer from 'puppeteer-extra';
import StealthPlugin from 'puppeteer-extra-plugin-stealth';
import { Browser, Page } from 'puppeteer';
import { RacProduct, ScraperConfig } from '../types';
import { USER_AGENTS } from '../config/constants';
import { loadCookies } from '../utils/session-manager';
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
      maxPages: config?.maxPages ?? 5,
      delayMs: config?.delayMs ?? 3000,
      retryAttempts: config?.retryAttempts ?? 3,
    };
  }

  protected randomUserAgent(): string {
    return USER_AGENTS[Math.floor(Math.random() * USER_AGENTS.length)];
  }

  protected async launch(isMobile = false): Promise<void> {
    this.browser = await puppeteer.launch({
      headless: true,
      args: [
        '--no-sandbox',
        '--disable-setuid-sandbox',
        '--disable-dev-shm-usage',
        '--disable-accelerated-2d-canvas',
        '--disable-gpu',
        '--window-size=1920,1080',
      ],
    });

    this.page = await this.browser.newPage();

    await this.page.setUserAgent(this.randomUserAgent());
    await this.page.setExtraHTTPHeaders({
      'Accept-Language': 'pt-BR,pt;q=0.9,en;q=0.8',
      'Accept-Encoding': 'gzip, deflate, br',
      Referer: 'https://www.google.com.br/',
      'sec-ch-ua': '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
      'sec-ch-ua-mobile': isMobile ? '?1' : '?0',
      'sec-ch-ua-platform': '"Windows"',
    });

    await this.page.setViewport(
      isMobile
        ? { width: 390, height: 844, isMobile: true }
        : { width: 1920, height: 1080 }
    );

    // Injeta cookies de sessão salvos (se houver)
    const savedCookies = loadCookies(this.siteDomain);
    if (savedCookies && savedCookies.length > 0) {
      // setCookie aceita CookieParam[] — cast necessário por incompatibilidade de versão do puppeteer
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      await (this.page as any).setCookie(...savedCookies);
      logger.debug(`Cookies carregados para ${this.siteDomain}: ${savedCookies.length}`);
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

    // Página completamente vazia ou com menos de 5KB (site não carregou)
    const isTooShort = html.length < 5000;

    // Status de bloqueio explícito na URL ou body
    const hasExplicitBlock =
      lower.includes('access denied') ||
      lower.includes('you have been blocked') ||
      lower.includes('403 forbidden');

    return hasCaptchaChallenge || isTooShort || hasExplicitBlock;
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
