export interface QueryConfig {
  term: string;
  category: string;
  priority: 'alta' | 'media' | 'baixa';
}

// Espelho de config.py KEYWORDS_LIST (revisão Jul/2026) — manter em sincronia.
// Rebalanceamento de marcas (viés Midea) + categorias novas:
// "Modelo / Linha" (era "Modelo Midea"), "Segmento" e "Conversacional IA".
export const KEYWORDS_LIST: QueryConfig[] = [
  // ── Head terms genéricos ──────────────────────────────────────
  { term: 'ar condicionado split',                category: 'Genérica',          priority: 'alta'  },
  { term: 'ar condicionado inverter',             category: 'Genérica',          priority: 'alta'  },
  { term: 'ar condicionado',                      category: 'Genérica',          priority: 'alta'  },
  { term: 'ar condicionado split inverter',       category: 'Genérica',          priority: 'alta'  },
  { term: 'ar condicionado quente e frio',        category: 'Genérica',          priority: 'alta'  },

  // ── Segmento emergente ────────────────────────────────────────
  { term: 'ar condicionado portátil',             category: 'Segmento',          priority: 'media' },

  // ── Capacidade / BTU ─────────────────────────────────────────
  { term: 'ar condicionado 9000 btus',            category: 'Capacidade BTU',    priority: 'alta'  },
  { term: 'ar condicionado 12000 btus',           category: 'Capacidade BTU',    priority: 'alta'  },
  { term: 'ar condicionado 18000 btus',           category: 'Capacidade BTU',    priority: 'alta'  },
  { term: 'ar condicionado 24000 btus',           category: 'Capacidade BTU',    priority: 'alta'  },
  { term: 'ar condicionado 9000 btus inverter',   category: 'Capacidade + Tipo', priority: 'alta'  },
  { term: 'ar condicionado 12000 btus inverter',  category: 'Capacidade + Tipo', priority: 'alta'  },
  { term: 'split 12000 btus inverter',            category: 'Capacidade + Tipo', priority: 'alta'  },
  { term: 'split 9000 btus inverter',             category: 'Capacidade + Tipo', priority: 'alta'  },

  // ── Marca monitorada (Midea) ─────────────────────────────────
  { term: 'ar condicionado midea',                category: 'Marca',             priority: 'alta'  },
  { term: 'midea inverter',                       category: 'Marca',             priority: 'alta'  },
  { term: 'midea 12000 btus',                     category: 'Marca',             priority: 'media' },

  // ── Modelo / Linha (própria + concorrentes) ──────────────────
  { term: 'midea ecomaster',                      category: 'Modelo / Linha',    priority: 'alta'  },
  { term: 'midea airvolution',                    category: 'Modelo / Linha',    priority: 'alta'  },
  { term: 'lg dual inverter',                     category: 'Modelo / Linha',    priority: 'alta'  },
  { term: 'samsung windfree',                     category: 'Modelo / Linha',    priority: 'alta'  },

  // ── Concorrentes ─────────────────────────────────────────────
  { term: 'ar condicionado lg',                   category: 'Marca',             priority: 'alta'  },
  { term: 'ar condicionado samsung',              category: 'Marca',             priority: 'alta'  },
  { term: 'ar condicionado gree',                 category: 'Marca',             priority: 'alta'  },
  { term: 'ar condicionado consul',               category: 'Marca',             priority: 'alta'  },
  { term: 'ar condicionado lg dual inverter 12000', category: 'Marca',           priority: 'media' },
  { term: 'ar condicionado electrolux',           category: 'Marca',             priority: 'media' },
  { term: 'ar condicionado elgin',                category: 'Marca',             priority: 'media' },
  { term: 'ar condicionado philco',               category: 'Marca',             priority: 'media' },
  { term: 'ar condicionado tcl',                  category: 'Marca',             priority: 'media' },
  { term: 'ar condicionado daikin',               category: 'Marca',             priority: 'baixa' },
  { term: 'ar condicionado hisense',              category: 'Marca',             priority: 'baixa' },

  // ── Intenção de compra ────────────────────────────────────────
  { term: 'melhor ar condicionado custo benefício', category: 'Intenção Compra', priority: 'alta'  },
  { term: 'melhor ar condicionado 2026',          category: 'Intenção Compra',   priority: 'alta'  },
  { term: 'comprar ar condicionado',              category: 'Intenção Compra',   priority: 'media' },
  { term: 'ar condicionado em promoção',          category: 'Preço / Promoção',  priority: 'media' },

  // ── Conversacional / IA ──────────────────────────────────────
  { term: 'ar condicionado inverter mais econômico', category: 'Conversacional IA', priority: 'media' },
  { term: 'melhor ar condicionado para quarto',   category: 'Conversacional IA', priority: 'media' },
  { term: 'ar condicionado silencioso',           category: 'Conversacional IA', priority: 'media' },
  { term: 'ar condicionado wifi',                 category: 'Conversacional IA', priority: 'baixa' },
];

// Apenas os termos — para uso no scraper
export const SEARCH_QUERIES = KEYWORDS_LIST.map((k) => k.term);

// Filtros por prioridade
export const QUERIES_ALTA    = KEYWORDS_LIST.filter((k) => k.priority === 'alta').map((k) => k.term);
export const QUERIES_MEDIA   = KEYWORDS_LIST.filter((k) => k.priority !== 'baixa').map((k) => k.term);

// 3 primeiras queries para testes rápidos
export const TEST_QUERIES = SEARCH_QUERIES.slice(0, 3);
