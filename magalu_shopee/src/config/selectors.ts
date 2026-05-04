export const SELECTORS = {
  magalu: {
    // Confirmado via dump:magalu — a[data-testid="product-card-container"] → 96 elementos
    // O card É o elemento <a>, então link e card apontam para o mesmo elemento
    productCard: 'a[data-testid="product-card-container"]',
    link: 'a[data-testid="product-card-container"]', // o card já é o link
    title: '[data-testid="product-title"]',
    price: '[data-testid="price-value"]',
    oldPrice: '[data-testid="price-original"]',
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
