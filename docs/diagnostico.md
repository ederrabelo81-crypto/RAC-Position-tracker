# Painel Diagnóstico — fórmulas

Reprodução, no nosso Streamlit, da aba **Diagnóstico** do PriceTrack, focada em
ar-condicionado. Código: `utils/diagnostico.py` (métricas, pandas puro) e
`utils/ac_attributes.py` (atributos e escopo). Testes:
`tests/test_diagnostico.py`, `tests/test_ac_attributes.py`. Descoberta e
decisões: `docs/diagnostico_fase0.md`.

## Escopo (regra dura)

- Só **ar-condicionado**. Nenhuma outra linha de produto aparece.
- Recorte padrão: **Split Hi-Wall Inverter até 30.000 BTU**
  (`subcategory = HI-WALL`, `INVERTER` no nome, BTU ≤ 30.000). Outras
  subcategorias de AC só por escolha no filtro; o teto de 30.000 BTU vale
  sempre.
- Subcategoria vem do campo da API (migração 007). Linhas anteriores a
  05/11/2026 são classificadas pelo de-para SKU → subcategoria; SKU sem
  de-para fica **fora** (na dúvida, não entra).

## Fonte

Histórico Parquet de `pricetrack_daily` no Drive, turno **Diário**, lido dia a
dia (`load_frame`). O Supabase guarda só 2 dias (janela quente).

Uma linha do frame = **anúncio-dia**: (dia, marketplace, SKU, seller) com
`preco` = `min_price`, o menor preço à vista do dia (menor entre spot e PIX,
base `best_cash`). Duplicatas de partição ficam com a importação mais recente.
Seller = `seller_canonical`, senão `seller`. Linha sem preço sai.

O PriceTrack só exporta oferta à venda (0 indisponíveis observados), então
**anúncio ativo = anúncio presente no dia**.

## Blocos

| # | Bloco | Função | Fórmula |
|---|---|---|---|
| 1 | Quantidade de ofertas — foco vs. mercado | `ofertas_por_marketplace` | por (dia, marketplace): nº de pares SKU × seller distintos. Top 10 marketplaces pelo total do período, **sempre** incluindo o foco |
| 3 | Anúncios por subcategoria (foco) | `anuncios_por_dimensao(…, "subcategoria")` | pares SKU × seller distintos por (dia, subcategoria) no foco; `pct` = qtd / total do dia no foco |
| 4 | Anúncios por marca (foco) | `anuncios_por_dimensao(…, "marca")` | idem por marca, com a linha `TODAS` (total do dia, 100%) |
| 5 | Preço mínimo praticado | `preco_minimo_por_marca` | por (dia, marca) no foco: o anúncio de menor preço, com SKU, seller e título |
| — | Mediana de mercado (base de 6 e 7) | `mediana_mercado` | por (dia, SKU): menor preço em **cada** marketplace, depois a mediana entre marketplaces. Padrão: **sem** o foco e **sem** lojas próprias de fabricante (MIDEA, LG, SAMSUNG…) |
| 6 | Profundidade vs. mercado | `profundidade_por_marca` | por (dia, SKU) no foco: `pct = (mediana − preço_foco) / mediana × 100` (positivo = foco mais barato). Por (dia, marca): mediana dos `pct` dos SKUs da marca |
| 7 | Produtos mais agressivos | `produtos_agressivos` | por SKU no foco: média dos `pct` diários; exige presença em ≥ ⌈dias com coleta no foco / 2⌉. Menor preço, seller e nº de sellers são do dia do menor preço; mediana = mediana das medianas diárias; série = preço do foco dia a dia |
| 8 | Comparador de produtos | `historico_produtos` | por (dia, SKU, marketplace): menor preço; opcionalmente só o foco |
| 9 | Exportar | `anuncios_dia` | linhas do recorte, datas ISO 8601 |

O bloco 2 do PriceTrack ("anúncios por categoria") não existe aqui: só há uma
categoria.

## Escolhas que o PriceTrack não documenta

- **Mediana sem o foco**: o marketplace analisado não entra na régua contra a
  qual é medido. `incluir_foco=True` inverte, para comparar com a tela.
- **Lojas de fabricante fora da mediana**: ali o fabricante controla o preço
  sozinho (decisão de 05/11/2026). `excluir_lojas_marca=False` inverte.
- **Marca**: comparada como o PriceTrack grava (`MIDEA` ≠ `SPRINGER MIDEA`).

Validação contra a tela do PriceTrack: tolerância de ±2% nas contagens, preço
exato e ±0,5 p.p. nos percentuais, sempre com âncoras de ar-condicionado.

## Desempenho

31 dias sintéticos (403 mil linhas): preparação < 1 s, cada bloco < 0,4 s.
Atributos são calculados por título único, não por linha.
