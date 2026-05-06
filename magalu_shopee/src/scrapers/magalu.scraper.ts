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

interface PageResult {
  items: Partial<RacProduct>[];
  hasNextPage: boolean;
  firstCardDiag: string;
  priceStrategy: string;
}

export class MagaluScraper extends BaseScraper {
  protected readonly siteDomain = 'magazineluiza.com.br';

  constructor(config?: Partial<ScraperConfig>) {
    super({ delayMs: DELAYS.MAGALU_MS, ...config });
  }

  protected async launch(_isMobile = false): Promise<void> {
    await super.launch(true);
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
        const result = await this.withRetry(
          () => this.scrapePage(query, pageNum),
          `Magalu p${pageNum} "${query}"`
        );

        if (result === null) continue;

        allProducts.push(...result.products);
        logger.info(
          `Magalu: Página ${pageNum}/${pages} — ${result.products.length} produtos (total: ${allProducts.length})`
        );

        if (!result.hasNextPage) {
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

  private async scrapePage(
    query: string,
    pageNum: number
  ): Promise<{ products: RacProduct[]; hasNextPage: boolean }> {
    if (!this.page) throw new Error('Browser não inicializado');

    await this.respectRateLimit();

    const url = buildMagaluUrl(query, pageNum);
    logger.debug(`Magalu: GET ${url}`);

    // 'load' espera DOMContentLoaded + recursos — dá mais tempo ao React renderizar preços
    await this.page.goto(url, {
      waitUntil: 'load',
      timeout: PAGE_TIMEOUT_MS,
    });

    await this.sleep(this.randomDelay(2000, 3500));
    await this.humanScroll();

    // Espera explícita pelo primeiro elemento de preço (qualquer seletor da lista)
    await this.waitForPriceElement();

    await this.sleep(1000);

    const html = await this.page.content();

    if (this.isSoftBlocked(html)) {
      logger.warn(`Magalu: Bloqueio real detectado em "${query}" p${pageNum}. Aguardando ${DELAYS.SOFT_BLOCK_MS / 1000}s...`);
      await this.sleep(DELAYS.SOFT_BLOCK_MS);
      await this.page.goto(url, { waitUntil: 'load', timeout: PAGE_TIMEOUT_MS });
      await this.waitForPriceElement();
      await this.sleep(3000);
    }

    const cardCount = await this.page.$$eval(
      SELECTORS.magalu.productCard,
      (els) => els.length
    ).catch(() => -1);
    logger.debug(`Magalu: ${cardCount} cards encontrados`);

    if (cardCount === 0) {
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

    const pageResult = await this.extractFromDOM(query, pageNum);

    if (pageResult.firstCardDiag) {
      logger.debug(`Magalu: HTML 1º card (p${pageNum}):\n${pageResult.firstCardDiag}`);
    }

    const withPrice = pageResult.items.filter(
      (r) => (r as Record<string, unknown>).current_price_raw
    ).length;
    const pct = pageResult.items.length > 0
      ? Math.round((withPrice / pageResult.items.length) * 100)
      : 0;

    if (pct < 50 && pageResult.items.length > 0) {
      logger.warn(`Magalu: ${withPrice}/${pageResult.items.length} com preço (${pct}%) [estratégia: ${pageResult.priceStrategy}]`);
    } else if (pageResult.items.length > 0) {
      logger.info(`Magalu: ${withPrice}/${pageResult.items.length} com preço (${pct}%) [estratégia: ${pageResult.priceStrategy}]`);
    }

    logger.debug(`Magalu: hasNextPage=${pageResult.hasNextPage} (p${pageNum})`);

    return {
      products: this.enrichProducts(pageResult.items),
      hasNextPage: pageResult.hasNextPage,
    };
  }

  // Espera até 8s por qualquer elemento de preço; não lança erro se não achar
  private async waitForPriceElement(): Promise<void> {
    if (!this.page) return;
    const priceSelectors = SELECTORS.magalu.priceFallbacks.slice(0, 5);
    for (const sel of priceSelectors) {
      try {
        await this.page.waitForSelector(sel, { timeout: 8000 });
        logger.debug(`Magalu: elemento de preço encontrado com "${sel}"`);
        return;
      } catch { /* tenta próximo */ }
    }
    logger.debug('Magalu: nenhum seletor de preço encontrado após espera — prosseguindo');
  }

  private async extractFromDOM(query: string, pageNum: number): Promise<PageResult> {
    if (!this.page) return { items: [], hasNextPage: false, firstCardDiag: '', priceStrategy: 'none' };

    const sel = SELECTORS.magalu;

    return this.page.evaluate(
      (selectors, searchQuery, page) => {
        // ── Utilitários ──────────────────────────────────────────────────────

        function trySelectors(parent: Element, candidates: string[]): string | null {
          for (const s of candidates) {
            try {
              const el = parent.querySelector(s) as HTMLElement | null;
              const text = el?.innerText?.trim();
              if (text) return text;
            } catch { /* seletor inválido */ }
          }
          return null;
        }

        function extractPriceFromText(text: string): string | null {
          // Tenta R$ X.XXX,XX primeiro (com símbolo)
          const withSymbol = text.match(/R\$\s*[\d.]+,\d{2}/);
          if (withSymbol) return withSymbol[0];
          // Tenta X.XXX,XX (com separador de milhar, sem símbolo)
          const noSymbol = text.match(/\b\d{1,3}(?:\.\d{3})+,\d{2}\b/);
          if (noSymbol) return noSymbol[0];
          return null;
        }

        function detectNextPage(candidates: string[]): boolean {
          for (const s of candidates) {
            try {
              if (document.querySelector(s)) return true;
            } catch { /* seletor inválido */ }
          }
          return false;
        }

        // ── Estratégia 1: __NEXT_DATA__ (Next.js — preços no JSON do servidor) ──
        let nextDataPrices: Map<string, string> | null = null;
        try {
          const nextDataEl = document.getElementById('__NEXT_DATA__');
          if (nextDataEl?.textContent) {
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            const data = JSON.parse(nextDataEl.textContent) as any;
            // Percorre a árvore procurando arrays de produtos com price/id
            const extractPricesFromObj = (obj: unknown): Map<string, string> => {
              const map = new Map<string, string>();
              const walk = (node: unknown): void => {
                if (!node || typeof node !== 'object') return;
                if (Array.isArray(node)) { node.forEach(walk); return; }
                const o = node as Record<string, unknown>;
                // Produto com id e price
                const id = (o['id'] || o['productId'] || o['sku']) as string | undefined;
                const price = (o['price'] || o['bestPrice'] || o['salesPrice']) as number | string | undefined;
                if (id && price !== undefined) {
                  // CRÍTICO: preço do __NEXT_DATA__ vem como número JS (1994.91).
                  // parsePrice() remove pontos achando que são milhar — inflaria 100x.
                  // Converte para formato BR ("1994,91") antes de retornar.
                  let priceStr: string;
                  if (typeof price === 'number' && isFinite(price)) {
                    priceStr = price.toFixed(2).replace('.', ',');
                  } else {
                    const num = Number(price);
                    priceStr = isFinite(num) ? num.toFixed(2).replace('.', ',') : String(price);
                  }
                  map.set(String(id).toUpperCase(), priceStr);
                }
                Object.values(o).forEach(walk);
              };
              walk(data);
              return map;
            };
            nextDataPrices = extractPricesFromObj(data);
          }
        } catch { /* __NEXT_DATA__ não disponível ou estrutura inesperada */ }

        // ── Estratégia 2: preços globais por posição ─────────────────────────
        // Coleta todos os elementos de preço visíveis na página e mapeia por índice
        const globalPriceEls: string[] = [];
        for (const s of selectors.priceFallbacks) {
          try {
            const els = Array.from(document.querySelectorAll(s)) as HTMLElement[];
            if (els.length > 0) {
              els.forEach((el) => {
                const text = el.innerText?.trim();
                if (text) globalPriceEls.push(text);
              });
              break; // usa o primeiro seletor que retornar elementos
            }
          } catch { /* seletor inválido */ }
        }

        // ── Extração principal ───────────────────────────────────────────────
        const items: Record<string, unknown>[] = [];
        const cards = document.querySelectorAll(selectors.productCard);
        let firstCardDiag = '';
        let priceStrategy = 'none';
        let priceHits = 0;

        cards.forEach((card, index) => {
          const cardAnchor = card as HTMLAnchorElement;
          const titleEl = card.querySelector(selectors.title) as HTMLElement | null;
          const href = cardAnchor.href || '';
          if (!href || !titleEl) return;

          if (index === 0) firstCardDiag = card.innerHTML.slice(0, 2000);

          const idMatch = href.match(/\/p\/([a-z0-9]+)\//i);
          const productId = idMatch ? idMatch[1].toUpperCase() : '';
          const sellerMatch = href.match(/[?&]seller_id=([^&]+)/);
          const sellerFromUrl = sellerMatch ? sellerMatch[1] : null;

          let currentPriceRaw: string | null = null;
          let strategy = 'none';

          // Estratégia 1: __NEXT_DATA__
          if (!currentPriceRaw && nextDataPrices && productId && nextDataPrices.has(productId)) {
            currentPriceRaw = nextDataPrices.get(productId) || null;
            if (currentPriceRaw) strategy = 'next_data';
          }

          // Estratégia 2: preços globais por posição
          if (!currentPriceRaw && index < globalPriceEls.length) {
            currentPriceRaw = globalPriceEls[index] || null;
            if (currentPriceRaw) strategy = 'global_by_index';
          }

          // Estratégia 3: seletores dentro do card
          if (!currentPriceRaw) {
            currentPriceRaw = trySelectors(card, selectors.priceFallbacks);
            if (currentPriceRaw) strategy = 'card_selector';
          }

          // Estratégia 4: regex no textContent do card
          if (!currentPriceRaw) {
            currentPriceRaw = extractPriceFromText(card.textContent || '');
            if (currentPriceRaw) strategy = 'regex_text';
          }

          if (currentPriceRaw) {
            priceHits++;
            priceStrategy = strategy;
          }

          const originalPriceRaw = trySelectors(card, selectors.oldPriceFallbacks);
          const ratingEl = card.querySelector(selectors.rating) as HTMLElement | null;
          const reviewEl = card.querySelector(selectors.reviewCount) as HTMLElement | null;
          const sellerEl = card.querySelector(selectors.seller) as HTMLElement | null;
          const imgEl = card.querySelector(selectors.productImage) as HTMLImageElement | null;

          items.push({
            marketplace: 'Magalu',
            product_id: productId,
            product_url: href,
            product_name: titleEl.innerText.trim(),
            search_query: searchQuery,
            page_number: page,
            position: index + 1,
            current_price_raw: currentPriceRaw,
            original_price_raw: originalPriceRaw,
            rating_raw: ratingEl?.innerText.trim() || null,
            review_count_raw: reviewEl?.innerText.trim() || null,
            seller: sellerEl?.innerText.trim() || sellerFromUrl || null,
            image_url: imgEl?.src || null,
            collected_at: new Date().toISOString(),
          });
        });

        // Se nenhuma estratégia funcionou, loga diagnóstico de preços globais
        if (priceHits === 0 && items.length > 0) {
          priceStrategy = `failed (global_found=${globalPriceEls.length}, next_data=${nextDataPrices ? nextDataPrices.size : 'n/a'})`;
        }

        const hasNextPage = detectNextPage(selectors.nextButtonFallbacks);
        return { items, hasNextPage, firstCardDiag, priceStrategy };
      },
      sel,
      query,
      pageNum
    ) as Promise<PageResult>;
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
}
