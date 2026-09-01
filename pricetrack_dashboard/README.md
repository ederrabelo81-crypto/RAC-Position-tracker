# pricetrack_dashboard — Dashboard de Preços RAC 9K/12K (PriceTrack ao vivo)

Dashboard Streamlit com os **insights de preço** do mercado de ar-condicionado
**9.000 e 12.000 BTU (Só Frio / CO)**, lidos **direto da API do PriceTrack** e
organizados por tier competitivo (peer to peer), no espírito do briefing diário
do projeto.

**Status:** ✅ Pronto para rodar · dados ao vivo na máquina com acesso à API

## O que a página mostra

1. **Preços por tier — Low / Mid / High.** Cada tier é a linha Midea e o grupo
   de concorrentes do mesmo ponto de preço, conforme a planilha *Peer to Peer*:

   | Tier | Linha Midea      | 9K (código Midea)          | 12K (código Midea)         |
   |------|------------------|----------------------------|----------------------------|
   | Low  | Inverter Lite    | `42EBVCA09M5/38TBVCA09M5`  | `42EBVCA12M5/38TBVCA12M5`  |
   | Mid  | AI AirVolution   | `38TAVCA09M5+42EFVCA09M5`  | `38TAVCA12M5+42EFVCA12M5`  |
   | High | AI Ecomaster     | `38EZVCA09M5+42EZVCA09M5`  | `38EZVCA12M5+42EZVCA12M5`  |

   Por faixa: **modal** (preço mais frequente) e **piso** (mínimo) do mercado,
   e o quanto a Midea está acima/abaixo do modal do mercado.

2. **Variação de preço Midea.** Por capacidade × linha: **mínimo, máximo, moda
   (modal) e média** — barra de faixa mín→máx com marcadores de moda e média.

## Como rodar

Numa máquina com acesso de rede a `api.pricetrack.com.br` (ex.: o PC coletor):

```bash
pip install -r requirements_app.txt          # streamlit, plotly, pandas, requests
export PRICETRACK_API_KEY=...                 # ou em .streamlit/secrets.toml
streamlit run pricetrack_dashboard/app.py
```

- **Hook ao vivo:** cada refresh (botão *Atualizar agora* na barra lateral)
  descobre a coleta mais recente e puxa as ofertas das marcas do peer via
  `pricetrack_api`. Cache de 15 min para não martelar a API.
- **Modo Demo:** sem `PRICETRACK_API_KEY`, a página cai em dados sintéticos
  (marcados como demo) só para visualizar o layout.
- **Secret no Streamlit Cloud:** `.streamlit/secrets.toml` →
  `PRICETRACK_API_KEY = "..."` (o `os.getenv` lê o secret injetado).

## Arquitetura

```
pricetrack_dashboard/
├── peer.py         # contrato do peer (SKUs por tier/capacidade) + matching por código
├── analytics.py    # funções puras: classifica ofertas → mín/máx/moda/média/modal/piso
├── data_source.py  # hook ao vivo (pricetrack_api) + amostra demo offline
├── app.py          # página Streamlit
└── tests/          # 27 testes herméticos (peer + analytics), sem rede
```

- **Casamento oferta → tier** é por **código de modelo do fabricante**
  (ex.: `42EBVCA09M5`), procurado como substring no texto normalizado da oferta
  (sku + título + nome). Nunca por preço — o objetivo é medir o preço *dado* o
  tier. A cobertura (quantos modelos do peer casaram) aparece no expander de
  diagnóstico; se a cobertura vier baixa ao vivo, ajuste os códigos em
  `peer.py` contra os títulos reais da coleta.
- **Preço** usado é o melhor à vista (`effective_price`: PIX vs spot, só se
  `AVAILABLE`). Oferta indisponível não entra em mínimo nem média.

## Testes

```bash
python -m pytest pricetrack_dashboard/tests/ -q
```

## Atualizar o peer

Quando o peer mudar de trimestre, edite `_PEER_RAW` em `peer.py` (espelho da
aba *Peer to Peer CO*). O índice de casamento e as marcas do filtro se
resolvem sozinhos a partir dele.
