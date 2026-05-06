import { createClient, SupabaseClient } from '@supabase/supabase-js';
import { randomUUID } from 'crypto';
import { RacProduct } from '../types';
import { KEYWORDS_LIST } from '../config/queries';
import { SUPABASE_BATCH_SIZE } from '../config/constants';
import { logger } from '../utils/logger';

const COLETAS_TABLE = 'coletas';

const CATEGORY_MAP = new Map(KEYWORDS_LIST.map((k) => [k.term, k.category]));

export function determineTurno(): string {
  const brtHour = new Date(Date.now() - 3 * 60 * 60 * 1000).getUTCHours();
  return brtHour >= 5 && brtHour < 18 ? 'Abertura' : 'Fechamento';
}

function toColetasRecord(
  product: RacProduct,
  turno: string,
  runId: string
): Record<string, unknown> {
  const brt = new Date(new Date(product.collected_at).getTime() - 3 * 60 * 60 * 1000);
  const data = brt.toISOString().slice(0, 10);
  const horario = brt.toISOString().slice(11, 16);

  return {
    data,
    turno,
    horario,
    plataforma: product.marketplace,
    tipo: 'Marketplace',
    keyword: product.search_query,
    categoria: CATEGORY_MAP.get(product.search_query) ?? 'Genérica',
    marca: product.brand ?? 'Desconhecida',
    produto: product.product_name,
    posicao_organica: product.position,
    posicao_patrocinada: null,
    posicao_geral: product.position,
    preco: product.current_price,
    seller: product.seller ?? product.marketplace,
    fulfillment: product.is_official,
    avaliacao: product.rating,
    qtd_avaliacoes: product.review_count || null,
    tag: null,
    run_id: runId,
  };
}

let client: SupabaseClient | null = null;

function getClient(): SupabaseClient {
  if (!client) {
    const url = process.env.SUPABASE_URL;
    const key = process.env.SUPABASE_ANON_KEY || process.env.SUPABASE_KEY;

    if (!url || !key) {
      throw new Error('SUPABASE_URL e SUPABASE_ANON_KEY (ou SUPABASE_KEY) são obrigatórios no .env');
    }

    client = createClient(url, key);
  }
  return client;
}

export async function uploadToSupabase(
  products: RacProduct[],
  turno?: string,
  runId?: string
): Promise<number> {
  if (!products.length) {
    logger.info('Supabase: Nenhum produto para enviar.');
    return 0;
  }

  const resolvedTurno = turno ?? determineTurno();
  const resolvedRunId = runId ?? randomUUID();
  const supabase = getClient();
  let inserted = 0;

  const allRecords = products.map((p) => toColetasRecord(p, resolvedTurno, resolvedRunId));

  // Deduplica pela chave única antes de enviar — evita ON CONFLICT DO UPDATE intra-batch
  const seen = new Set<string>();
  const records = allRecords.filter((r) => {
    const key = `${r.data}|${r.turno}|${r.plataforma}|${r.keyword}|${r.produto}|${r.run_id}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });

  if (records.length < allRecords.length) {
    logger.warn(`Supabase: ${allRecords.length - records.length} duplicatas removidas antes do upload`);
  }

  for (let i = 0; i < records.length; i += SUPABASE_BATCH_SIZE) {
    const batch = records.slice(i, i + SUPABASE_BATCH_SIZE);
    const batchNum = Math.floor(i / SUPABASE_BATCH_SIZE) + 1;

    const { error } = await supabase
      .from(COLETAS_TABLE)
      .upsert(batch, { onConflict: 'data,turno,plataforma,keyword,produto,run_id' });

    if (error) {
      logger.error(`Supabase: Erro no batch ${batchNum} — ${error.message}`);
    } else {
      inserted += batch.length;
      logger.info(`Supabase: Batch ${batchNum} — ${batch.length} registros inseridos`);
    }
  }

  logger.info(`Supabase: Total inserido: ${inserted}/${products.length}`);
  return inserted;
}

export async function testConnection(): Promise<boolean> {
  try {
    const supabase = getClient();
    const { error } = await supabase.from(COLETAS_TABLE).select('id').limit(1);
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
