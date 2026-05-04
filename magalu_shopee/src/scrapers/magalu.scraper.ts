import { BaseScraper } from './base.scraper';
import { RacProduct, ScraperConfig } from '../types';
import { SELECTORS } from '../config/selectors';
import { DELAYS, PAGE_TIMEOUT_MS, MOBILE_USER_AGENTS } from '../config/constants';
import { detectBrand } from '../utils/brand-detector';
import { isValidRacProduct, extractBTU, extractProductType, parsePrice } from '../utils/validators';
import { logger } from '../utils/logger';

// m. (mobile) — www. retorna 403 Akamai em headless
function buildMagaluUrl(query: string, page: number): string {
  const slug = query.trim().replace(/\s+/g, '+');
  return `https://m.magazineluiza.com.br/busca/${slug}/?page=${page}`;
}

export class MagaluScraper extends BaseScraper {
  protected readonly siteDomain = 'magazineluiza.com.br';

  constructor(config?: Partial<ScraperConfig>) {
    super({ delayMs: DELAYS.MAGALU_MS, ...config });
  }

  // Sobrescreve o launch para usar viewport mobile e UA mobile
  protected async launch(_isMobile = false): Promise<void> {
    await super.launch(true); // sempre mobile para m.magazineluiza.com.br
    // Substitui o UA por um mobile real
    if (this.page) {
      const mobileUA = MOBILE_USER_AGENTS[Math.floor(Math.random() * MOBILE_USER_AGENTS.length)];
      await this.page.setUserAgent(mobileUA);
      await this.page.setViewport({ width: 390, height: 844, isMobile: true, hasTouch: true });
    }
  }

  async scrape(query: string, maxPages?: number): Promise<RacProduct[]> {
    const pages = maxPages ?? this.config.maxPages;
    const allProducts: RacProduct[] = [];
    const startTime = Date.now();

    logger.info(`Magalu: Iniciando coleta — query="${query}" (${pages} páginas)`);

    await this.launch(false);

    try {
      for (let pageNum = 1; pageNum <= pages; pageNum++) {
        const products = await this.withRetry(
          () => this.scrapePage(query, pageNum),
          `Magalu p${pageNum} "${query}"`
        );

        if (products === null) continue;

        allProducts.push(...products);
        logger.info(
          `Magalu: Página ${pageNum}/${pages} — ${products.length} produtos (total: ${allProducts.length})`
        );

        const hasNext = await this.hasNextPage();
        if (!hasNext) {
          logger.info(`Magalu: Última página atingida na página ${pageNum}`);
          break;
        }

        await this.respectRateLimit();
      }
    } finally {
      await this.close();
    }

    const duration = ((Date.now() - startTime) / 1000).toFixed(1);
    logger.info(`Magalu: Coleta finalizada — ${allProducts.length} produtos em ${duration}s`);

    return allProducts;
  }

  private async scrapePage(query: string, pageNum: number): Promise<RacProduct[]> {
    if (!this.page) throw new Error('Browser não inicializado');

    await this.respectRateLimit();

    const url = buildMagaluUrl(query, pageNum);
    logger.debug(`Magalu: GET ${url}`);

    await this.page.goto(url, {
      waitUntil: 'domcontentloaded',
      timeout: PAGE_TIMEOUT_MS,
    });

    await this.sleep(this.randomDelay(2000, 3500));
    await this.humanScroll();
    await this.sleep(1000);

    const html = await this.page.content();

    if (this.isSoftBlocked(html)) {
      logger.warn(`Magalu: Bloqueio real detectado em "${query}" p${pageNum}. Aguardando ${DELAYS.SOFT_BLOCK_MS / 1000}s...`);
      await this.sleep(DELAYS.SOFT_BLOCK_MS);
      await this.page.goto(url, { waitUntil: 'domcontentloaded', timeout: PAGE_TIMEOUT_MS });
      await this.sleep(3000);
    }

    // Log de diagnóstico: conta cards com o seletor atual
    const cardCount = await this.page.$$eval(
      SELECTORS.magalu.productCard,
      (els) => els.length
    ).catch(() => -1);
    logger.debug(`Magalu: Seletor "${SELECTORS.magalu.productCard}" → ${cardCount} cards`);

    if (cardCount === 0) {
      // Tenta seletores alternativos para diagnóstico
      const alt = await this.page.evaluate(() => {
        const selectors = [
          'a[href*="/p/"]',
          '[data-testid="product-card"]',
          'li[class*="sc-"]',
          'ol[class*="sc-"] li',
        ];
        return selectors.map((s) => `${s}: ${document.querySelectorAll(s).length}`).join(' | ');
      });
      logger.warn(`Magalu: 0 cards — seletores alternativos: ${alt}`);
    }

    const rawItems = await this.extractFromDOM(query, pageNum);
    return this.enrichProducts(rawItems);
  }

  private async extractFromDOM(
    query: string,
    pageNum: number
  ): Promise<Partial<RacProduct>[]> {
    if (!this.page) return [];

    const sel = SELECTORS.magalu;

    return this.page.evaluate(
      (selectors, searchQuery, page) => {
        const items: Record<string, unknown>[] = [];
        const cards = document.querySelectorAll(selectors.productCard);

        cards.forEach((card, index) => {
          // O card É o elemento <a> — href está direto nele, não em filho
          const cardAnchor = card as HTMLAnchorElement;
          const titleEl = card.querySelector(selectors.title) as HTMLElement | null;
          const priceEl = card.querySelector(selectors.price) as HTMLElement | null;
          const oldPriceEl = card.querySelector(selectors.oldPrice) as HTMLElement | null;
          const ratingEl = card.querySelector(selectors.rating) as HTMLElement | null;
          const reviewEl = card.querySelector(selectors.reviewCount) as HTMLElement | null;
          const sellerEl = card.querySelector(selectors.seller) as HTMLElement | null;
          const imgEl = card.querySelector(selectors.productImage) as HTMLImageElement | null;

          const href = cardAnchor.href || '';
          if (!href || !titleEl) return;

          // ID do produto: /p/XXXXXXXX/ no path
          const idMatch = href.match(/\/p\/([a-z0-9]+)\//i);
          // seller_id no query string: ?seller_id=xxx ou ?ads=...&seller_id=xxx
          const sellerMatch = href.match(/[?&]seller_id=([^&]+)/);
          const sellerFromUrl = sellerMatch ? sellerMatch[1] : null;

          items.push({
            marketplace: 'Magalu',
            product_id: idMatch ? idMatch[1].toUpperCase() : '',
            product_url: href,
            product_name: titleEl.innerText.trim(),
            search_query: searchQuery,
            page_number: page,
            position: index + 1,
            current_price_raw: priceEl?.innerText.trim() || null,
            original_price_raw: oldPriceEl?.innerText.trim() || null,
            rating_raw: ratingEl?.innerText.trim() || null,
            review_count_raw: reviewEl?.innerText.trim() || null,
            // seller_id da URL é mais confiável que o texto do card
            seller: sellerEl?.innerText.trim() || sellerFromUrl || null,
            image_url: imgEl?.src || null,
            collected_at: new Date().toISOString(),
          });
        });

        return items;
      },
      sel,
      query,
      pageNum
    ) as Promise<Partial<RacProduct>[]>;
  }

  private enrichProducts(raw: Partial<RacProduct>[]): RacProduct[] {
    const results: RacProduct[] = [];

    for (const item of raw as Record<string, unknown>[]) {
      const name = (item.product_name as string) || '';

      if (!isValidRacProduct(name)) continue;

      const currentPrice = parsePrice(item.current_price_raw as string);
      const originalPrice = parsePrice(item.original_price_raw as string);
      const discount =
        currentPrice && originalPrice && originalPrice > currentPrice
          ? Math.round(((originalPrice - currentPrice) / originalPrice) * 100)
          : null;

      const ratingStr = (item.rating_raw as string) || '';
      const ratingVal = parseFloat(ratingStr.replace(',', '.'));
      const reviewStr = (item.review_count_raw as string) || '0';
      const reviewVal = parseInt(reviewStr.replace(/\D/g, ''), 10) || 0;

      results.push({
        marketplace: 'Magalu',
        product_id: (item.product_id as string) || '',
        sku: null,
        search_query: (item.search_query as string) || '',
        page_number: (item.page_number as number) || 1,
        position: (item.position as number) || 0,
        product_name: name,
        brand: detectBrand(name),
        product_type: extractProductType(name),
        capacity_btu: extractBTU(name),
        current_price: currentPrice,
        original_price: originalPrice,
        discount_percentage: discount,
        rating: isNaN(ratingVal) ? null : ratingVal,
        review_count: reviewVal,
        stock_status: 'Em estoque',
        seller: (item.seller as string) || null,
        is_official: false,
        product_url: (item.product_url as string) || '',
        image_url: (item.image_url as string) || null,
        collected_at: (item.collected_at as string) || new Date().toISOString(),
      });
    }

    return results;
  }

  private async hasNextPage(): Promise<boolean> {
    if (!this.page) return false;
    try {
      const btn = await this.page.$(SELECTORS.magalu.nextButton);
      return btn !== null;
    } catch {
      return false;
    }
  }
}
