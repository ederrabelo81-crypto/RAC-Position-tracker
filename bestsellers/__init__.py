"""
bestsellers — Rotina diária de "Mais Vendidos" RAC.

Transforma as listas de mais vendidos dos marketplaces numa série temporal
confiável. Existe porque a coleta de posição/preço **não contém volume de
venda**: ela mede oferta, preço e presença. As listas de mais vendidos são a
única variável de RESULTADO disponível no nível do SKU, a custo zero.

Uso:
    python scripts/collect_bestsellers.py --plataformas all
    python scripts/collect_bestsellers.py --relatorio semanal

Módulos:
    config     Registro das listas monitoradas (URL, ordenação, mecânica).
    models     Item de ranking e classificação (marca, grupo, BTU, tipo).
    base       Contrato de uma fonte + host de browser reutilizável.
    sources/   Um coletor por plataforma.
    validate   Portões de qualidade que rodam antes de qualquer análise.
    metrics    KPI de topo, séries diária/semanal/mensal, diagnósticos.
    storage    CSV do dia, master histórico e Supabase.
    report     Briefs em markdown.

REGRA DURA: ranking é ordinal. Não vira share de mercado, não soma entre
plataformas, e campo sem dado fica vazio.
"""

__all__ = [
    "base", "config", "metrics", "models", "report", "sources", "storage",
    "validate",
]
