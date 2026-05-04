export interface RacProduct {
  marketplace: 'Magalu' | 'Shopee';
  product_id: string;
  sku: string | null;
  search_query: string;
  page_number: number;
  position: number;
  product_name: string;
  brand: string | null;
  product_type: string | null;
  capacity_btu: number | null;
  current_price: number | null;
  original_price: number | null;
  discount_percentage: number | null;
  rating: number | null;
  review_count: number;
  stock_status: string;
  seller: string | null;
  is_official: boolean;
  product_url: string;
  image_url: string | null;
  collected_at: string;
}

export interface ScraperConfig {
  headless: boolean;
  maxPages: number;
  delayMs: number;
  retryAttempts: number;
}

export interface CollectionSummary {
  platform: string;
  query: string;
  productsFound: number;
  pagesScraped: number;
  durationMs: number;
  errors: string[];
}
