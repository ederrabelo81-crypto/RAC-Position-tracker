import cron from 'node-cron';
import { randomUUID } from 'crypto';
import { MagaluScraper } from '../scrapers/magalu.scraper';
import { ShopeeScraper } from '../scrapers/shopee.scraper';
import { SEARCH_QUERIES } from '../config/queries';
import { uploadToSupabase } from '../storage/supabase-uploader';
import { writeToCsvTimestamped } from '../storage/csv-writer';
import { RacProduct } from '../types';
import { DELAYS } from '../config/constants';
import { logger } from '../utils/logger';

// ── Schedules (horário UTC) ──────────────────────────────────────────────────
// Manhã  (Abertura):  13:00 UTC = 10:00 BRT — 2 páginas por query
// Noite (Fechamento): 00:00 UTC = 21:00 BRT — 1 página por query
const SCHEDULE_MANHA     = '0 13 * * *';   // 10h BRT — 2 páginas
const SCHEDULE_FECHAMENTO = '0 0 * * *';   // 21h BRT — 1 página

async function runCollection(platforms: string[], maxPages: number, turno: string): Promise<void> {
  const allProducts: RacProduct[] = [];
  const startTime = Date.now();

  logger.info(`========== COLETA ${turno.toUpperCase()} INICIADA ==========`);
  logger.info(`Plataformas: ${platforms.join(', ')} | Queries: ${SEARCH_QUERIES.length} | Páginas: ${maxPages}`);

  for (const platform of platforms) {
    const scraper = platform === 'magalu' ? new MagaluScraper() : new ShopeeScraper();

    for (const query of SEARCH_QUERIES) {
      const t0 = Date.now();
      try {
        const products = await scraper.scrape(query, maxPages);
        allProducts.push(...products);
        logger.info(`${platform}: "${query}" → ${products.length} produtos (${((Date.now() - t0) / 1000).toFixed(1)}s)`);
      } catch (err) {
        logger.error(`${platform}: Erro em "${query}": ${err}`);
      }

      await new Promise((r) => setTimeout(r, DELAYS.BETWEEN_QUERIES_MS));
    }
  }

  if (allProducts.length > 0) {
    const csvPath = await writeToCsvTimestamped(allProducts, './data');
    logger.info(`CSV salvo: ${csvPath}`);
    await uploadToSupabase(allProducts, turno, randomUUID());
  }

  const totalMin = ((Date.now() - startTime) / 60000).toFixed(1);
  logger.info(`========== COLETA ${turno.toUpperCase()} FINALIZADA: ${allProducts.length} produtos em ${totalMin}min ==========`);
}

export function startScheduler(platforms: string[] = ['magalu']): void {
  // Valida expressões cron
  if (!cron.validate(SCHEDULE_MANHA) || !cron.validate(SCHEDULE_FECHAMENTO)) {
    logger.error('Scheduler: Expressão cron inválida');
    process.exit(1);
  }

  logger.info('========== SCHEDULER INICIADO ==========');
  logger.info(`Manhã  (Abertura):   ${SCHEDULE_MANHA}  UTC → 10:00 BRT — 2 páginas`);
  logger.info(`Noite (Fechamento): ${SCHEDULE_FECHAMENTO} UTC → 21:00 BRT — 1 página`);
  logger.info(`Plataformas: ${platforms.join(', ')} | Queries: ${SEARCH_QUERIES.length}`);

  // Coleta da manhã — 2 páginas
  cron.schedule(SCHEDULE_MANHA, () => {
    runCollection(platforms, 2, 'Abertura').catch((err) => {
      logger.error(`Scheduler: Erro fatal na coleta Abertura — ${err}`);
    });
  }, { timezone: 'UTC' });

  // Coleta da noite — 1 página
  cron.schedule(SCHEDULE_FECHAMENTO, () => {
    runCollection(platforms, 1, 'Fechamento').catch((err) => {
      logger.error(`Scheduler: Erro fatal na coleta Fechamento — ${err}`);
    });
  }, { timezone: 'UTC' });
}

export { runCollection };
