# pricetrack_dashboard — Dashboard de Preços RAC 9K/12K (PriceTrack)

Dashboard Streamlit com os **insights de preço** do mercado de ar-condicionado
**9.000 e 12.000 BTU (Só Frio / CO)**, organizados por tier competitivo
(peer to peer), no espírito do briefing diário do projeto.

**Status:** ✅ Pronto para rodar

## Fontes de dados (3)

| Fonte | Velocidade | Observação |
|-------|-----------|------------|
| 🟢 **Supabase** (padrão) | rápida | Lê `pricetrack_daily` — o import diário da API já mora lá. **Recomendada.** |
| 🔴 **API ao vivo** | lenta (~2min/consulta) | Bate direto no PriceTrack. Só quando precisa do dado do minuto e há paciência. |
| 🟡 **Demo** | instantânea | Dados sintéticos, para ver o layout sem credencial. |

As três entregam o mesmo formato de oferta, então as análises (tiers + variação
Midea) rodam iguais sobre qualquer uma. Seletor de data em **calendário**
(default: data mais recente disponível).

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

Credenciais — o **Supabase** é a fonte padrão; a `PRICETRACK_API_KEY` só é
necessária para a fonte "API ao vivo". Melhor caminho (vale Windows/Linux/Mac e
Streamlit Cloud): `.streamlit/secrets.toml` na raiz do projeto —

```toml
SUPABASE_URL       = "https://SEU-PROJETO.supabase.co"
SUPABASE_KEY       = "sua-chave-anon-ou-service-role"
PRICETRACK_API_KEY = "sua-key"     # opcional (só p/ a fonte API ao vivo)
```

Ou por variável de ambiente:

```powershell
# Windows / PowerShell
$env:SUPABASE_URL = "https://SEU-PROJETO.supabase.co"; $env:SUPABASE_KEY = "..."
```
```bash
# Linux / Mac
export SUPABASE_URL=https://SEU-PROJETO.supabase.co SUPABASE_KEY=...
```

**No painel do projeto (recomendado)** — a página vive dentro do dashboard
principal, no grupo **INSIGHTS → 💰 Preços 9K/12K**:

```bash
pip install -r requirements_app.txt          # streamlit, plotly, pandas, requests, supabase
streamlit run app.py
```

**Standalone** (só esta página):

```bash
streamlit run pricetrack_dashboard/app.py
```

- **Supabase (padrão):** lê `pricetrack_daily` da data escolhida no calendário
  (default: mais recente). Cada linha do dia vira uma observação de preço
  (usa `min_price` como melhor à vista). Rápido. Cache de 15 min.
- **API ao vivo:** bate no PriceTrack; a sonda de data é filtrada por marca e o
  read timeout tem piso de 60s, mas o endpoint responde em ~2min por consulta.
- **Demo:** sem credencial, cai em dados sintéticos (marcados como demo).
- **Streamlit Cloud:** os mesmos nomes em Settings → Secrets.

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
