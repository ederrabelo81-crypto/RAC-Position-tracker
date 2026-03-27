/**
 * ══════════════════════════════════════════════════════════════
 * RAC E-COMMERCE POSITION TRACKER v5 — Midea Brasil
 * ══════════════════════════════════════════════════════════════
 * Uso:
 *   node index.js                    → Coleta completa
 *   node index.js --platform meli    → Apenas Mercado Livre
 *   node index.js --dry-run          → Testa sem salvar
 *   node index.js --output excel     → Salva em Excel local
 *   node index.js --output sheets    → Salva em Google Sheets
 *   node index.js --output both      → Ambos (padrão)
 */

const { chromium } = require('playwright');
const config = require('./config');
const { log, logError, logSuccess, logWarn } = require('./utils/logger');
const { scrapeplatform, applyStealthScripts } = require('./scrapers/dispatcher');
const { writeToGoogleSheets } = require('./exporters/google-sheets');
const { writeToExcel } = require('./exporters/excel-local');
const { generateReport } = require('./utils/report');

const args = process.argv.slice(2);
const isDryRun = args.includes('--dry-run');
const platformFilter = args.includes('--platform') ? args[args.indexOf('--platform') + 1] : null;
const outputMode = args.includes('--output') ? args[args.indexOf('--output') + 1] : 'both';

async function main() {
  const startTime = Date.now();
  const turno = new Date().getHours() < 12 ? 'Abertura' : 'Fechamento';
  const today = new Date().toISOString().split('T')[0];

  log(`\n${'═'.repeat(60)}`);
  log(`  RAC POSITION TRACKER v5 — ${today} — Turno: ${turno}`);
  log(`${'═'.repeat(60)}\n`);
  if (isDryRun) logWarn('MODO DRY-RUN — dados NÃO serão salvos');

  let platforms = config.platforms.filter(p => p.active);
  if (platformFilter) {
    platforms = platforms.filter(p => p.id.toLowerCase().includes(platformFilter.toLowerCase()));
    if (platforms.length === 0) { logError(`Nenhuma plataforma para filtro: "${platformFilter}"`); process.exit(1); }
  }
  log(`Plataformas: ${platforms.map(p => p.name).join(', ')}`);
  log(`Keywords: ${config.keywords.length} | Marcas: ${config.brands.length}\n`);

  let browser;
  try {
    browser = await chromium.launch({
      headless: config.browser.headless,
      args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-blink-features=AutomationControlled'],
    });
    log('Browser iniciado');
  } catch (err) {
    logError(`Falha ao iniciar browser: ${err.message}`);
    logError('Rode: npx playwright install chromium');
    process.exit(1);
  }

  const allResults = [];
  let totalErrors = 0;

  for (const platform of platforms) {
    log(`\n${'─'.repeat(40)}`);
    log(`▸ Coletando: ${platform.name}`);
    log(`${'─'.repeat(40)}`);

    for (const keyword of config.keywords) {
      if (config.scraping.priorityFilter && !config.scraping.priorityFilter.includes(keyword.priority)) continue;

      try {
        const context = await browser.newContext({
          userAgent: config.browser.userAgent,
          viewport: { width: 1366, height: 768 },
          locale: 'pt-BR',
          timezoneId: 'America/Sao_Paulo',
        });
        const page = await context.newPage();
        page.setDefaultTimeout(config.scraping.pageTimeout);

        // Aplicar stealth scripts ANTES de navegar
        await applyStealthScripts(page);

        // Passar context (necessário para Shopee carregar cookies)
        const results = await scrapeplatform(page, platform, keyword, config.brands, context);

        for (const result of results) {
          allResults.push({
            data: today, turno,
            horario: new Date().toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' }),
            analista: config.scraping.analystName,
            plataforma: platform.name, tipoPlataforma: platform.type,
            keyword: keyword.term, categoriaKeyword: keyword.category,
            marca: result.brand, produto: result.productName,
            posicaoOrganica: result.organicPosition, posicaoPatrocinada: result.sponsoredPosition,
            posicaoGeral: result.generalPosition, preco: result.price,
            seller: result.seller, fulfillment: result.fulfillment,
            avaliacao: result.rating, qtdAvaliacoes: result.reviewCount,
            tagDestaque: result.tag, observacoes: result.notes,
          });
        }

        logSuccess(`  ✓ "${keyword.term}" — ${results.length} resultados`);
        await context.close();

        const delay = config.scraping.delayBetweenRequests + Math.random() * config.scraping.randomDelayMax;
        await new Promise(r => setTimeout(r, delay));

      } catch (err) {
        totalErrors++;
        logError(`  ✗ "${keyword.term}" em ${platform.name}: ${err.message}`);
      }
    }
  }

  await browser.close();
  log(`\nBrowser fechado. Total: ${allResults.length} registros`);

  if (allResults.length === 0) { logWarn('Nenhum resultado coletado.'); process.exit(0); }

  if (!isDryRun) {
    if (outputMode === 'sheets' || outputMode === 'both') {
      try { await writeToGoogleSheets(allResults, config.googleSheets); logSuccess('Exportado para Google Sheets'); }
      catch (err) { logError(`Sheets falhou: ${err.message}`); }
    }
    if (outputMode === 'excel' || outputMode === 'both') {
      try { const fp = await writeToExcel(allResults, config.excel); logSuccess(`Exportado: ${fp}`); }
      catch (err) { logError(`Excel falhou: ${err.message}`); }
    }
  }

  const elapsed = ((Date.now() - startTime) / 1000).toFixed(1);
  generateReport(allResults, totalErrors, elapsed, turno);
}

main().catch(err => { logError(`Erro fatal: ${err.message}`); process.exit(1); });
