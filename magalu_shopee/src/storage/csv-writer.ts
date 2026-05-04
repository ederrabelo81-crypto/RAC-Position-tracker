import { createObjectCsvWriter } from 'csv-writer';
import path from 'path';
import fs from 'fs';
import { RacProduct } from '../types';
import { logger } from '../utils/logger';

const CSV_HEADERS = [
  { id: 'collected_at', title: 'Data Coleta' },
  { id: 'marketplace', title: 'Marketplace' },
  { id: 'search_query', title: 'Query Buscada' },
  { id: 'page_number', title: 'Página' },
  { id: 'position', title: 'Posição' },
  { id: 'product_name', title: 'Produto' },
  { id: 'brand', title: 'Marca' },
  { id: 'product_type', title: 'Tipo' },
  { id: 'capacity_btu', title: 'BTU' },
  { id: 'current_price', title: 'Preço Atual (R$)' },
  { id: 'original_price', title: 'Preço Original (R$)' },
  { id: 'discount_percentage', title: 'Desconto (%)' },
  { id: 'rating', title: 'Avaliação' },
  { id: 'review_count', title: 'Qtd Avaliações' },
  { id: 'seller', title: 'Seller' },
  { id: 'is_official', title: 'Oficial?' },
  { id: 'stock_status', title: 'Estoque' },
  { id: 'product_id', title: 'ID Produto' },
  { id: 'product_url', title: 'URL' },
];

export async function writeToCsv(
  products: RacProduct[],
  outputPath?: string
): Promise<string> {
  const csvPath = outputPath || process.env.CSV_OUTPUT_PATH || './data/rac_monitoramento.csv';
  const resolvedPath = path.resolve(csvPath);
  const dir = path.dirname(resolvedPath);

  if (!fs.existsSync(dir)) {
    fs.mkdirSync(dir, { recursive: true });
  }

  const fileExists = fs.existsSync(resolvedPath);

  // Usa BOM UTF-8 para compatibilidade com Excel PT-BR
  if (!fileExists) {
    fs.writeFileSync(resolvedPath, '﻿', { encoding: 'utf8' });
  }

  const writer = createObjectCsvWriter({
    path: resolvedPath,
    header: CSV_HEADERS,
    append: fileExists,
    encoding: 'utf8',
  });

  await writer.writeRecords(products);

  logger.info(`CSV: ${products.length} registros gravados em ${resolvedPath}`);
  return resolvedPath;
}

export async function writeToCsvTimestamped(
  products: RacProduct[],
  dir = './data'
): Promise<string> {
  const ts = new Date()
    .toISOString()
    .replace(/[:.]/g, '-')
    .slice(0, 19);
  const filename = `rac_${ts}.csv`;
  const fullPath = path.join(dir, filename);
  return writeToCsv(products, fullPath);
}
