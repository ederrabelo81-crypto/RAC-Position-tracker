import { createClient, SupabaseClient } from '@supabase/supabase-js';
import { RacProduct } from '../types';
import { SUPABASE_TABLE, SUPABASE_BATCH_SIZE } from '../config/constants';
import { logger } from '../utils/logger';

let client: SupabaseClient | null = null;

function getClient(): SupabaseClient {
  if (!client) {
    const url = process.env.SUPABASE_URL;
    const key = process.env.SUPABASE_ANON_KEY;

    if (!url || !key) {
      throw new Error('SUPABASE_URL e SUPABASE_ANON_KEY são obrigatórios no .env');
    }

    client = createClient(url, key);
  }
  return client;
}

export async function uploadToSupabase(products: RacProduct[]): Promise<number> {
  if (!products.length) {
    logger.info('Supabase: Nenhum produto para enviar.');
    return 0;
  }

  const supabase = getClient();
  let inserted = 0;

  for (let i = 0; i < products.length; i += SUPABASE_BATCH_SIZE) {
    const batch = products.slice(i, i + SUPABASE_BATCH_SIZE);

    const { error } = await supabase.from(SUPABASE_TABLE).insert(batch);

    if (error) {
      logger.error(
        `Supabase: Erro no batch ${Math.floor(i / SUPABASE_BATCH_SIZE) + 1} — ${error.message}`
      );
    } else {
      inserted += batch.length;
      logger.info(`Supabase: Batch ${Math.floor(i / SUPABASE_BATCH_SIZE) + 1} — ${batch.length} registros inseridos`);
    }
  }

  logger.info(`Supabase: Total inserido: ${inserted}/${products.length}`);
  return inserted;
}

export async function testConnection(): Promise<boolean> {
  try {
    const supabase = getClient();
    const { error } = await supabase.from(SUPABASE_TABLE).select('id').limit(1);
    if (error) {
      logger.error(`Supabase: Falha na conexão — ${error.message}`);
      return false;
    }
    logger.info('Supabase: Conexão OK');
    return true;
  } catch (err) {
    logger.error(`Supabase: Erro inesperado — ${err}`);
    return false;
  }
}
