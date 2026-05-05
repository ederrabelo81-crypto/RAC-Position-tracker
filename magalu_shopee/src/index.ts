import 'dotenv/config';
import { program } from 'commander';
import { startScheduler, runCollection } from './scheduler/cron';
import { MagaluScraper } from './scrapers/magalu.scraper';
import { ShopeeScraper } from './scrapers/shopee.scraper';
import { SEARCH_QUERIES, TEST_QUERIES } from './config/queries';
import { uploadToSupabase, testConnection, determineTurno } from './storage/supabase-uploader';
import { randomUUID } from 'crypto';
import { writeToCsvTimestamped } from './storage/csv-writer';
import { RacProduct } from './types';
import { logger } from './utils/logger';

program
  .name('rac-scraper')
  .description('RAC Marketplace Scraper — Magalu & Shopee')
  .option('--schedule', 'Modo agendado (cron)')
  .option('--platforms <list>', 'Marketplaces separados por vírgula (magalu,shopee)', 'magalu')
  .option('--pages <n>', 'Páginas por query', '5')
  .option('--test', 'Modo teste: 3 queries + 2 páginas')
  .option('--no-headless', 'Abre browser visível (debug)')
  .option('--check-db', 'Testa conexão com Supabase e sai')
  .parse();

const opts = program.opts<{
  schedule: boolean;
  platforms: string;
  pages: string;
  test: boolean;
  headless: boolean;
  checkDb: boolean;
}>();

(async () => {
  logger.info('RAC Marketplace Scraper iniciado');

  if (opts.checkDb) {
    const ok = await testConnection();
    process.exit(ok ? 0 : 1);
  }

  if (opts.schedule) {
    const platforms = opts.platforms.split(',').map((p) => p.trim());
    logger.info('Modo: Agendamento automático (cron)');
    startScheduler(platforms);
    return;
  }

  // Modo manual
  const platforms = opts.platforms.split(',').map((p) => p.trim());
  const maxPages = opts.test ? 2 : parseInt(opts.pages, 10);
  const queries = opts.test ? TEST_QUERIES : SEARCH_QUERIES;

  logger.info(`Modo: Execução manual | Plataformas: ${platforms.join(', ')} | Páginas: ${maxPages} | Queries: ${queries.length}`);

  const allProducts: RacProduct[] = [];

  if (platforms.includes('magalu')) {
    const scraper = new MagaluScraper({ headless: opts.headless, maxPages });
    for (const query of queries) {
      try {
        const products = await scraper.scrape(query);
        allProducts.push(...products);
      } catch (err) {
        logger.error(`Magalu: Erro em "${query}": ${err}`);
      }
    }
  }

  if (platforms.includes('shopee')) {
    const scraper = new ShopeeScraper({ maxPages });
    for (const query of queries) {
      try {
        const products = await scraper.scrape(query);
        allProducts.push(...products);
      } catch (err) {
        logger.error(`Shopee: Erro em "${query}": ${err}`);
      }
    }
  }

  if (allProducts.length > 0) {
    const csvPath = await writeToCsvTimestamped(allProducts, './data');
    logger.info(`CSV salvo: ${csvPath}`);
    await uploadToSupabase(allProducts, determineTurno(), randomUUID());
  }

  logger.info(`Coleta manual finalizada: ${allProducts.length} produtos`);
})();
