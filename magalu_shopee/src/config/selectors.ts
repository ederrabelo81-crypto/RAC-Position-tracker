export const SELECTORS = {
  magalu: {
    // Confirmado via dump:magalu — a[data-testid="product-card-container"] → 96 elementos
    // O card É o elemento <a>, então link e card apontam para o mesmo elemento
    productCard: 'a[data-testid="product-card-container"]',
    link: 'a[data-testid="product-card-container"]', // o card já é o link
    title: '[data-testid="product-title"]',
    // Seletor primário — pode ter mudado no redesign; ver priceFallbacks abaixo
    price: '[data-testid="price-value"]',
    // Fallbacks em ordem de prioridade (tentados quando o primário retorna null)
    priceFallbacks: [
      '[data-testid="price-value"]',
      '[data-testid="price-best"]',
      '[data-testid="best-price"]',
      '[data-testid="current-price"]',
      '[data-testid="price"]',
      'p[class*="Price"]',
      'span[class*="Price"]',
      '[class*="price-value"]',
      '[class*="Price__value"]',
      '[class*="Price__container"] span',
    ] as string[],
    oldPrice: '[data-testid="price-original"]',
    oldPriceFallbacks: [
      '[data-testid="price-original"]',
      '[data-testid="original-price"]',
      '[data-testid="price-from"]',
      'span[class*="OriginalPrice"]',
      'del[class*="Price"]',
    ] as string[],
    rating: '[data-testid="review"] span:first-child',
    reviewCount: '[data-testid="review"] span:last-child',
    seller: '[data-testid="seller-name"]',
    nextButton: '[data-testid="next-page"]',
    noResults: '[data-testid="empty-state"]',
    productImage: 'img[data-testid="product-card-image"]',
  },

  shopee: {
    productCard: 'div.col-xs-2-4.shopee-search-item-result__item',
    link: 'a[data-sqe="link"]',
    title: 'div.Cve6sh',
    price: 'span.ZEgDH9',
    oldPrice: 'span.zPFZJh',
    rating: 'div.OitLRu span',
    reviewCount: 'div.OitLRu + div',
    seller: 'div.mVzHUd',
    nextButton: 'button.shopee-icon-button--right:not([disabled])',
    noResults: 'div.shopee-search-empty-result-section',
    productImage: 'img[class*="pictureImage"]',
  },
} as const;
