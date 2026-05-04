export interface QueryConfig {
  term: string;
  category: string;
  priority: 'alta' | 'media' | 'baixa';
}

export const KEYWORDS_LIST: QueryConfig[] = [
  // ── Head terms genéricos ──────────────────────────────────────
  { term: 'ar condicionado split',               category: 'Genérica',          priority: 'alta'  },
  { term: 'ar condicionado inverter',             category: 'Genérica',          priority: 'alta'  },
  { term: 'ar condicionado',                      category: 'Genérica',          priority: 'alta'  },
  { term: 'ar condicionado split inverter',       category: 'Genérica',          priority: 'alta'  },

  // ── Capacidade / BTU ─────────────────────────────────────────
  { term: 'ar condicionado 9000 btus',            category: 'Capacidade BTU',    priority: 'alta'  },
  { term: 'ar condicionado 12000 btus',           category: 'Capacidade BTU',    priority: 'alta'  },
  { term: 'ar condicionado 18000 btus',           category: 'Capacidade BTU',    priority: 'alta'  },
  { term: 'ar condicionado 24000 btus',           category: 'Capacidade BTU',    priority: 'alta'  },
  { term: 'ar condicionado 9000 btus inverter',   category: 'Capacidade + Tipo', priority: 'alta'  },
  { term: 'ar condicionado 12000 btus inverter',  category: 'Capacidade + Tipo', priority: 'alta'  },
  { term: 'split 12000 btus inverter',            category: 'Capacidade + Tipo', priority: 'alta'  },
  { term: 'split 9000 btus inverter',             category: 'Capacidade + Tipo', priority: 'alta'  },

  // ── Marca Midea ──────────────────────────────────────────────
  { term: 'ar condicionado midea',                category: 'Marca',             priority: 'alta'  },
  { term: 'midea inverter',                       category: 'Marca',             priority: 'alta'  },
  { term: 'midea 12000 btus',                     category: 'Marca',             priority: 'alta'  },
  { term: 'ar condicionado midea 12000',          category: 'Marca',             priority: 'alta'  },
  { term: 'midea ecomaster',                      category: 'Modelo Midea',      priority: 'alta'  },
  { term: 'midea airvolution',                    category: 'Modelo Midea',      priority: 'alta'  },

  // ── Concorrentes ─────────────────────────────────────────────
  { term: 'ar condicionado lg',                   category: 'Marca',             priority: 'alta'  },
  { term: 'lg dual inverter',                     category: 'Marca',             priority: 'alta'  },
  { term: 'ar condicionado lg dual inverter 12000', category: 'Marca',           priority: 'alta'  },
  { term: 'ar condicionado samsung',              category: 'Marca',             priority: 'alta'  },
  { term: 'samsung windfree',                     category: 'Marca',             priority: 'alta'  },
  { term: 'ar condicionado gree',                 category: 'Marca',             priority: 'media' },
  { term: 'ar condicionado elgin',                category: 'Marca',             priority: 'media' },
  { term: 'ar condicionado philco',               category: 'Marca',             priority: 'media' },
  { term: 'ar condicionado tcl',                  category: 'Marca',             priority: 'media' },

  // ── Intenção de compra ────────────────────────────────────────
  { term: 'melhor ar condicionado custo benefício', category: 'Intenção Compra', priority: 'alta'  },
  { term: 'melhor ar condicionado 2026',          category: 'Intenção Compra',   priority: 'alta'  },
  { term: 'comprar ar condicionado',              category: 'Intenção Compra',   priority: 'media' },
  { term: 'ar condicionado em promoção',          category: 'Preço / Promoção',  priority: 'media' },
];

// Apenas os termos — para uso no scraper
export const SEARCH_QUERIES = KEYWORDS_LIST.map((k) => k.term);

// Filtros por prioridade
export const QUERIES_ALTA    = KEYWORDS_LIST.filter((k) => k.priority === 'alta').map((k) => k.term);
export const QUERIES_MEDIA   = KEYWORDS_LIST.filter((k) => k.priority !== 'baixa').map((k) => k.term);

// 3 primeiras queries para testes rápidos
export const TEST_QUERIES = SEARCH_QUERIES.slice(0, 3);
