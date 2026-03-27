/**
 * ══════════════════════════════════════════════════════════════
 * CONFIGURAÇÃO CENTRAL — RAC Position Tracker v3
 * ══════════════════════════════════════════════════════════════
 * Atualizado Mar/2026 após diagnóstico real de plataformas.
 *
 * Status:
 *   ✅ Magalu       — 100% funcional (seletores confirmados)
 *   ✅ Mercado Livre — funcional (popup de CEP tratado)
 *   ✅ Amazon        — funcional
 *   ❌ Shopee        — exige login, não funciona headless
 *   ❌ Casas Bahia   — WAF Akamai bloqueia (usar Distill)
 *   ❌ Google Shop.  — reCAPTCHA (usar Distill)
 */

module.exports = {

  // ── PLATAFORMAS ──
  platforms: [
    {
      id: 'meli',
      name: 'Mercado Livre',
      type: 'Nacional Retail',
      active: true,  // ✅ Funciona
    },
    {
      id: 'amazon',
      name: 'Amazon Brasil',
      type: 'Nacional Retail',
      active: true,  // ✅ Funciona
    },
    {
      id: 'magalu',
      name: 'Magazine Luiza',
      type: 'Nacional Retail',
      active: true,  // ✅ Seletores confirmados via diagnóstico
    },
    {
      id: 'shopee',
      name: 'Shopee',
      type: 'Marketplace',
      active: true,   // ⚡ Requer cookies: node salvar-cookies.js shopee
    },
    {
      id: 'casasbahia',
      name: 'Casas Bahia',
      type: 'Nacional Retail',
      active: true,   // ⚡ URL corrigida + stealth — pode falhar se WAF estiver agressivo
    },
    {
      id: 'google_shopping',
      name: 'Google Shopping',
      type: 'Comparador',
      active: true,   // ⚡ Stealth — pode falhar com reCAPTCHA
    },
  ],

  // ── KEYWORDS ──
  // Prioridade: 'alta' = diário, 'media' = 3x/semana, 'baixa' = semanal
  keywords: [
    // Head terms
    { term: 'ar condicionado split', category: 'Genérica', priority: 'alta' },
    { term: 'ar condicionado inverter', category: 'Genérica', priority: 'alta' },
    { term: 'ar condicionado', category: 'Genérica', priority: 'alta' },
    { term: 'ar condicionado split inverter', category: 'Genérica', priority: 'alta' },

    // Capacidade / BTU
    { term: 'ar condicionado 9000 btus', category: 'Capacidade BTU', priority: 'alta' },
    { term: 'ar condicionado 12000 btus', category: 'Capacidade BTU', priority: 'alta' },
    { term: 'ar condicionado 18000 btus', category: 'Capacidade BTU', priority: 'alta' },
    { term: 'ar condicionado 24000 btus', category: 'Capacidade BTU', priority: 'alta' },
    { term: 'split 12000 btus inverter', category: 'Capacidade + Tipo', priority: 'alta' },
    { term: 'split 9000 btus inverter', category: 'Capacidade + Tipo', priority: 'alta' },

    // Marca Midea
    { term: 'ar condicionado midea', category: 'Marca', priority: 'alta' },
    { term: 'midea inverter', category: 'Marca', priority: 'alta' },
    { term: 'midea 12000 btus', category: 'Marca', priority: 'alta' },
    { term: 'midea ecomaster', category: 'Modelo Midea', priority: 'alta' },
    { term: 'midea airvolution', category: 'Modelo Midea', priority: 'alta' },

    // Concorrentes
    { term: 'ar condicionado lg', category: 'Marca', priority: 'alta' },
    { term: 'lg dual inverter', category: 'Marca', priority: 'alta' },
    { term: 'ar condicionado samsung', category: 'Marca', priority: 'alta' },
    { term: 'samsung windfree', category: 'Marca', priority: 'alta' },
    { term: 'ar condicionado gree', category: 'Marca', priority: 'media' },
    { term: 'ar condicionado elgin', category: 'Marca', priority: 'media' },
    { term: 'ar condicionado philco', category: 'Marca', priority: 'media' },
    { term: 'ar condicionado tcl', category: 'Marca', priority: 'media' },

    // Intenção de compra
    { term: 'melhor ar condicionado custo benefício', category: 'Intenção Compra', priority: 'alta' },
    { term: 'melhor ar condicionado 2026', category: 'Intenção Compra', priority: 'alta' },
    { term: 'comprar ar condicionado', category: 'Intenção Compra', priority: 'media' },
    { term: 'ar condicionado em promoção', category: 'Preço / Promoção', priority: 'media' },

    // Comparação
    { term: 'midea vs lg', category: 'Comparação', priority: 'media' },
  ],

  // ── MARCAS PARA DETECÇÃO AUTOMÁTICA ──
  brands: [
    'Midea', 'Springer Midea', 'Carrier', 'Midea Carrier',
    'LG', 'Samsung', 'Gree', 'TCL', 'Hisense',
    'Elgin', 'Consul', 'Philco', 'Daikin', 'Fujitsu',
    'Komeco', 'Agratto', 'Electrolux', 'EOS',
  ],

  // ── SCRAPING ──
  scraping: {
    analystName: 'Bot Automático',
    maxResultsPerPage: 20,
    delayBetweenRequests: 4000,     // 4s entre requests
    randomDelayMax: 3000,           // + até 3s aleatório
    pageTimeout: 45000,             // 45s timeout por página
    retryAttempts: 2,
    priorityFilter: null,           // null = todas, ['alta'] = só alta prioridade
  },

  // ── BROWSER ──
  browser: {
    headless: true,   // Mude para false para debug visual
    userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
  },

  // ── GOOGLE SHEETS ──
  googleSheets: {
    enabled: false,   // Mude para true após configurar credentials
    credentialsPath: './credentials.json',
    spreadsheetId: 'SEU_SPREADSHEET_ID_AQUI',
    sheetName: 'Registro Diário',
    headerRow: 1,
  },

  // ── EXCEL LOCAL ──
  excel: {
    enabled: true,
    outputDir: './output',
    fileNamePattern: 'posicionamento_{date}.xlsx',
    appendMode: true,
  },

  // ── LOGS ──
  logging: {
    dir: './logs',
    level: 'info',
  },
};
