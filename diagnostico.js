/**
 * ══════════════════════════════════════════════════════════════
 * DIAGNÓSTICO DE SELETORES — RAC Position Tracker
 * ══════════════════════════════════════════════════════════════
 * 
 * Este script navega para cada plataforma, tira screenshot e
 * salva o HTML da página para você inspecionar os seletores
 * corretos. Rode uma vez e abra os arquivos gerados na pasta
 * ./diagnostico/
 *
 * Uso:
 *   node diagnostico.js                  → Todas as plataformas
 *   node diagnostico.js --platform meli  → Só Mercado Livre
 *   node diagnostico.js --visible        → Abre o browser visível
 */

const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const KEYWORD = 'ar condicionado 12000 btus inverter';
const OUTPUT_DIR = './diagnostico';

const args = process.argv.slice(2);
const isVisible = args.includes('--visible');
const platformFilter = args.includes('--platform')
  ? args[args.indexOf('--platform') + 1]
  : null;

const PLATFORMS = [
  {
    id: 'meli',
    name: 'Mercado Livre',
    url: `https://lista.mercadolivre.com.br/${encodeURIComponent(KEYWORD).replace(/%20/g, '-')}`,
  },
  {
    id: 'amazon',
    name: 'Amazon Brasil',
    url: `https://www.amazon.com.br/s?k=${encodeURIComponent(KEYWORD)}`,
  },
  {
    id: 'shopee',
    name: 'Shopee',
    url: `https://shopee.com.br/search?keyword=${encodeURIComponent(KEYWORD)}`,
  },
  {
    id: 'magalu',
    name: 'Magazine Luiza',
    url: `https://www.magazineluiza.com.br/busca/${encodeURIComponent(KEYWORD)}/`,
  },
  {
    id: 'casasbahia',
    name: 'Casas Bahia',
    url: `https://www.casasbahia.com.br/${encodeURIComponent(KEYWORD)}/b`,
  },
  {
    id: 'google_shopping',
    name: 'Google Shopping',
    url: `https://www.google.com/search?tbm=shop&q=${encodeURIComponent(KEYWORD)}&gl=br&hl=pt-BR`,
  },
];

async function diagnose() {
  if (!fs.existsSync(OUTPUT_DIR)) fs.mkdirSync(OUTPUT_DIR, { recursive: true });

  let platforms = PLATFORMS;
  if (platformFilter) {
    platforms = platforms.filter(p => p.id.includes(platformFilter));
  }

  console.log('\n══════════════════════════════════════════════════');
  console.log('  DIAGNÓSTICO DE SELETORES');
  console.log('══════════════════════════════════════════════════');
  console.log(`  Keyword de teste: "${KEYWORD}"`);
  console.log(`  Browser visível: ${isVisible ? 'SIM' : 'NÃO'}`);
  console.log(`  Plataformas: ${platforms.map(p => p.name).join(', ')}`);
  console.log('══════════════════════════════════════════════════\n');

  const browser = await chromium.launch({
    headless: !isVisible,
    args: ['--no-sandbox', '--disable-setuid-sandbox'],
  });

  for (const platform of platforms) {
    console.log(`\n▸ ${platform.name}`);
    console.log(`  URL: ${platform.url}`);

    const context = await browser.newContext({
      userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
      viewport: { width: 1366, height: 768 },
      locale: 'pt-BR',
      timezoneId: 'America/Sao_Paulo',
    });
    const page = await context.newPage();

    try {
      // Navegar e esperar bastante para tudo carregar
      await page.goto(platform.url, { waitUntil: 'networkidle', timeout: 60000 });
      
      // Esperar extra para JS renderizar
      await page.waitForTimeout(5000);

      // Scroll para triggerar lazy loading
      await page.evaluate(async () => {
        for (let i = 0; i < 5; i++) {
          window.scrollBy(0, 800);
          await new Promise(r => setTimeout(r, 500));
        }
        window.scrollTo(0, 0);
      });
      await page.waitForTimeout(2000);

      // Screenshot
      const screenshotPath = path.join(OUTPUT_DIR, `${platform.id}_screenshot.png`);
      await page.screenshot({ path: screenshotPath, fullPage: false });
      console.log(`  ✓ Screenshot salvo: ${screenshotPath}`);

      // HTML completo
      const html = await page.content();
      const htmlPath = path.join(OUTPUT_DIR, `${platform.id}_page.html`);
      fs.writeFileSync(htmlPath, html, 'utf8');
      console.log(`  ✓ HTML salvo: ${htmlPath} (${(html.length / 1024).toFixed(0)} KB)`);

      // Análise automática de seletores candidatos
      const analysis = await page.evaluate(() => {
        const results = {};

        // Procurar containers de produto comuns
        const candidateSelectors = [
          // Genéricos
          '[data-testid*="product"]',
          '[data-testid*="card"]',
          '[data-component-type*="search-result"]',
          'article',
          '.product-card',
          '.product',
          '.search-result',
          // Mercado Livre
          '.ui-search-layout__item',
          '.ui-search-result',
          '.andes-card',
          'li.ui-search-layout__item',
          '.poly-card',
          'ol.ui-search-layout li',
          '.ui-search-layout--grid li',
          // Amazon
          'div[data-component-type="s-search-result"]',
          '.s-result-item',
          '.s-main-slot .s-result-item',
          // Shopee
          '.shopee-search-item-result__item',
          '[data-sqe="item"]',
          '.col-xs-2-4',
          // Magalu
          '[data-testid="product-card"]',
          'li[data-testid]',
          'a[data-testid="product-card-container"]',
          // Casas Bahia
          '.product-card',
          '[class*="ProductCard"]',
          '[class*="product-card"]',
          '[class*="productCard"]',
          'a[class*="product"]',
          // Google Shopping
          '.sh-dgr__gr-auto',
          '.sh-dlr__list-result',
          '.sh-pr__product-result',
          '[data-docid]',
        ];

        results.containerCandidates = {};
        for (const sel of candidateSelectors) {
          try {
            const count = document.querySelectorAll(sel).length;
            if (count > 0) {
              results.containerCandidates[sel] = count;
            }
          } catch (e) { /* seletor inválido */ }
        }

        // Procurar textos de preço
        const pricePattern = /R\$\s?[\d.]+[,.]?\d*/;
        const priceElements = [];
        document.querySelectorAll('*').forEach(el => {
          const text = el.textContent?.trim();
          if (text && pricePattern.test(text) && text.length < 30 && el.children.length === 0) {
            const classes = el.className?.toString().substring(0, 80) || '';
            const tag = el.tagName;
            if (!priceElements.some(p => p.classes === classes)) {
              priceElements.push({ tag, classes, sample: text.substring(0, 25) });
            }
          }
        });
        results.priceElements = priceElements.slice(0, 10);

        // Estatísticas gerais
        results.stats = {
          totalElements: document.querySelectorAll('*').length,
          links: document.querySelectorAll('a').length,
          images: document.querySelectorAll('img').length,
          h2Count: document.querySelectorAll('h2').length,
          h3Count: document.querySelectorAll('h3').length,
          title: document.title,
          url: window.location.href,
        };

        return results;
      });

      // Salvar análise
      const analysisPath = path.join(OUTPUT_DIR, `${platform.id}_analysis.json`);
      fs.writeFileSync(analysisPath, JSON.stringify(analysis, null, 2), 'utf8');
      console.log(`  ✓ Análise salvo: ${analysisPath}`);

      // Mostrar seletores encontrados
      const containers = analysis.containerCandidates;
      const found = Object.entries(containers).filter(([, count]) => count >= 3);
      if (found.length > 0) {
        console.log(`  ✓ SELETORES DE CONTAINER ENCONTRADOS:`);
        found.sort((a, b) => b[1] - a[1]);
        for (const [sel, count] of found.slice(0, 5)) {
          console.log(`      ${sel}  →  ${count} elementos`);
        }
      } else {
        console.log(`  ✗ Nenhum seletor padrão encontrado — verifique o HTML manualmente`);
      }

      // Mostrar preços encontrados
      if (analysis.priceElements.length > 0) {
        console.log(`  ✓ ELEMENTOS DE PREÇO ENCONTRADOS:`);
        for (const p of analysis.priceElements.slice(0, 3)) {
          console.log(`      <${p.tag} class="${p.classes}">  →  "${p.sample}"`);
        }
      }

      console.log(`  ℹ Página: ${analysis.stats.title}`);
      console.log(`  ℹ ${analysis.stats.totalElements} elementos, ${analysis.stats.links} links, ${analysis.stats.h2Count} h2`);

    } catch (err) {
      console.log(`  ✗ ERRO: ${err.message}`);

      // Tentar tirar screenshot mesmo com erro
      try {
        const errScreenshotPath = path.join(OUTPUT_DIR, `${platform.id}_error.png`);
        await page.screenshot({ path: errScreenshotPath });
        console.log(`  ✓ Screenshot de erro salvo: ${errScreenshotPath}`);
      } catch (_) {}
    }

    await context.close();

    // Delay entre plataformas
    await new Promise(r => setTimeout(r, 3000));
  }

  await browser.close();

  console.log('\n══════════════════════════════════════════════════');
  console.log('  DIAGNÓSTICO COMPLETO');
  console.log('══════════════════════════════════════════════════');
  console.log(`  Arquivos salvos em: ${path.resolve(OUTPUT_DIR)}`);
  console.log('');
  console.log('  PRÓXIMOS PASSOS:');
  console.log('  1. Abra os screenshots para verificar se a página carregou');
  console.log('  2. Abra os _analysis.json para ver seletores detectados');
  console.log('  3. Abra os _page.html no browser para inspecionar o HTML');
  console.log('  4. Atualize os seletores no config.js com os valores corretos');
  console.log('  5. Rode novamente: node diagnostico.js --platform XXXX');
  console.log('');
  console.log('  DICA: Use --visible para ver o browser em tempo real:');
  console.log('  node diagnostico.js --visible --platform meli');
  console.log('══════════════════════════════════════════════════\n');
}

diagnose().catch(err => {
  console.error('Erro fatal:', err.message);
  process.exit(1);
});
