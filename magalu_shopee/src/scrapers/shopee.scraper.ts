/**
 * Shopee Scraper — usa a API interna do Shopee (mesma usada pelo app mobile).
 *
 * Não usa Puppeteer/browser. Faz requisições HTTP diretas com os cookies
 * exportados do Chrome via Cookie-Editor.
 *
 * Se não houver cookies salvos, tenta sem autenticação (funciona para
 * buscas públicas na maioria dos casos).
 */

import https from 'https';
import { RacProduct, ScraperConfig } from '../types';
import { detectBrand } from '../utils/brand-detector';
import { isValidRacProduct, extractBTU, extractProductType } from '../utils/validators';
import { loadCookies } from '../utils/session-manager';
import { DELAYS } from '../config/constants';
import { logger } from '../utils/logger';

const SHOPEE_API = 'https://shopee.com.br/api/v4/search/search_items';
const ITEMS_PER_PAGE = 60;

interface ShopeeItem {
  itemid: number;
  shopid: number;
  name: string;
  price: number;         // em centavos × 100000 (dividir por 100000)
  price_before_discount: number;
  item_rating: { rating_star: number; rating_count: number[] };
  stock: number;
  shop_name?: string;
  image: string;
  is_official_shop: boolean;
  sold: number;
}

interface ShopeeApiResponse {
  error?: number;
  items?: Array<{ item_basic: ShopeeItem }>;
  total_count?: number;
}

interface SessionData {
  cookieHeader: string;
  csrfToken: string;
}

function buildSession(domain: string): SessionData {
  const cookies = loadCookies(domain);
  if (!cookies || cookies.length === 0) return { cookieHeader: '', csrfToken: '' };

  const cookieHeader = cookies
    .filter((c) => c.name && c.value)
    .map((c) => `${c.name}=${c.value}`)
    .join('; ');

  // Shopee exige x-csrftoken header além do cookie
  const csrfCookie = cookies.find((c) => c.name === 'csrftoken');
  const csrfToken = (csrfCookie?.value as string) || '';

  return { cookieHeader, csrfToken };
}

function fetchJson(url: string, session: SessionData): Promise<ShopeeApiResponse> {
  return new Promise((resolve, reject) => {
    const options = {
      headers: {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        Accept: 'application/json',
        'Accept-Language': 'pt-BR,pt;q=0.9',
        Referer: 'https://shopee.com.br/search',
        'x-requested-with': 'XMLHttpRequest',
        'x-shopee-language': 'pt-BR',
        ...(session.csrfToken ? { 'x-csrftoken': session.csrfToken } : {}),
        ...(session.cookieHeader ? { Cookie: session.cookieHeader } : {}),
      },
    };

    https.get(url, options, (res) => {
      let data = '';
      res.on('data', (chunk) => { data += chunk; });
      res.on('end', () => {
        try {
          resolve(JSON.parse(data) as ShopeeApiResponse);
        } catch {
          reject(new Error(`JSON inválido (status ${res.statusCode}): ${data.slice(0, 200)}`));
        }
      });
    }).on('error', reject);
  });
}

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

export class ShopeeScraper {
  protected readonly siteDomain = 'shopee.com.br';
  private readonly delayMs: number;
  private readonly retryAttempts: number;

  constructor(config?: Partial<ScraperConfig>) {
    this.delayMs = config?.delayMs ?? DELAYS.SHOPEE_MS;
    this.retryAttempts = config?.retryAttempts ?? 3;
  }

  async scrape(query: string, maxPages = 5): Promise<RacProduct[]> {
    const allProducts: RacProduct[] = [];
    const startTime = Date.now();
    const session = buildSession(this.siteDomain);

    if (!session.cookieHeader) {
      logger.warn('Shopee: Sem cookies de sessão — tentando sem autenticação (pode ser bloqueado)');
      logger.warn('  Para exportar cookies: npm run import-cookies:shopee');
    } else if (!session.csrfToken) {
      logger.warn('Shopee: Cookie "csrftoken" não encontrado — API pode rejeitar a requisição');
      logger.warn('  Exporte os cookies novamente pelo Cookie-Editor (certifique-se de estar logado)');
    } else {
      logger.debug(`Shopee: csrftoken encontrado (${session.csrfToken.slice(0, 8)}...)`);
    }

    logger.info(`Shopee API: Iniciando coleta — query="${query}" (${maxPages} páginas)`);

    for (let page = 0; page < maxPages; page++) {
      const newest = page * ITEMS_PER_PAGE;

      const url =
        `${SHOPEE_API}?by=relevancy` +
        `&keyword=${encodeURIComponent(query)}` +
        `&limit=${ITEMS_PER_PAGE}` +
        `&newest=${newest}` +
        `&order=desc` +
        `&page_type=search` +
        `&scenario=PAGE_GLOBAL_SEARCH` +
        `&version=2`;

      let response: ShopeeApiResponse | null = null;

      for (let attempt = 1; attempt <= this.retryAttempts; attempt++) {
        try {
          response = await fetchJson(url, session);
          break;
        } catch (err) {
          logger.warn(`Shopee API: Tentativa ${attempt}/${this.retryAttempts} falhou: ${err}`);
          if (attempt < this.retryAttempts) await sleep(attempt * 3000);
        }
      }

      if (!response || response.error) {
        const errCode = response?.error;
        if (errCode === 90309999) {
          logger.error(`Shopee API: Bloqueio anti-fraude (90309999) na página ${page + 1}`);
          logger.error('  Causa: header "af-ac-enc-dat" ausente — gerado dinamicamente pelo JS do browser');
          logger.error('  Solução 1: Re-exporte cookies do Chrome APÓS navegar pela busca e faça login');
          logger.error('  Solução 2: Use a Shopee Open Platform API (https://open.shopee.com/)');
        } else {
          logger.error(`Shopee API: Erro na página ${page + 1} — ${JSON.stringify(response)}`);
        }
        break;
      }

      const items = response.items || [];
      if (items.length === 0) {
        logger.info(`Shopee API: Sem mais resultados na página ${page + 1}`);
        break;
      }

      const products = this.parseItems(items, query, page + 1);
      allProducts.push(...products);
      logger.info(`Shopee API: Página ${page + 1}/${maxPages} — ${products.length} produtos válidos de ${items.length} retornados`);

      if (items.length < ITEMS_PER_PAGE) break; // última página

      await sleep(this.delayMs + Math.floor(Math.random() * 1000));
    }

    const duration = ((Date.now() - startTime) / 1000).toFixed(1);
    logger.info(`Shopee API: Finalizado — ${allProducts.length} produtos em ${duration}s`);

    return allProducts;
  }

  private parseItems(
    items: Array<{ item_basic: ShopeeItem }>,
    query: string,
    pageNum: number
  ): RacProduct[] {
    const results: RacProduct[] = [];

    items.forEach(({ item_basic }, index) => {
      const name = item_basic.name || '';

      if (!isValidRacProduct(name)) return;

      // Preço: Shopee armazena em centavos × 100000
      const currentPrice = item_basic.price > 0
        ? Math.round(item_basic.price / 100000 * 100) / 100
        : null;
      const originalPrice = item_basic.price_before_discount > 0
        ? Math.round(item_basic.price_before_discount / 100000 * 100) / 100
        : null;

      const discount =
        currentPrice && originalPrice && originalPrice > currentPrice
          ? Math.round(((originalPrice - currentPrice) / originalPrice) * 100)
          : null;

      const ratingCounts = item_basic.item_rating?.rating_count || [];
      const totalReviews = ratingCounts.reduce((sum, n) => sum + n, 0);

      results.push({
        marketplace: 'Shopee',
        product_id: `${item_basic.shopid}_${item_basic.itemid}`,
        sku: String(item_basic.itemid),
        search_query: query,
        page_number: pageNum,
        position: (pageNum - 1) * ITEMS_PER_PAGE + index + 1,
        product_name: name,
        brand: detectBrand(name),
        product_type: extractProductType(name),
        capacity_btu: extractBTU(name),
        current_price: currentPrice,
        original_price: originalPrice,
        discount_percentage: discount,
        rating: item_basic.item_rating?.rating_star || null,
        review_count: totalReviews,
        stock_status: item_basic.stock > 0 ? 'Em estoque' : 'Indisponível',
        seller: item_basic.shop_name || null,
        is_official: item_basic.is_official_shop || false,
        product_url: `https://shopee.com.br/product/${item_basic.shopid}/${item_basic.itemid}`,
        image_url: item_basic.image
          ? `https://cf.shopee.com.br/file/${item_basic.image}`
          : null,
        collected_at: new Date().toISOString(),
      });
    });

    return results;
  }
}
