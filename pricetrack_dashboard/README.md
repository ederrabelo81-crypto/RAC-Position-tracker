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

## Filtros

Multiselects que recortam **todas** as seções (aplicados client-side, antes da
agregação — inclusive na série de evolução, dia a dia):

- **Marcas (vs Midea)** — os concorrentes do peer. A **Midea está sempre
  presente** (é a âncora da comparação) e não aparece na lista. Selecionar só
  Elgin, por exemplo, faz todas as seções lerem **Midea vs Elgin**.
- **Marketplace** — recorta por marketplace; vale para todas as marcas, Midea
  inclusa.
- **Vendedor** — recorta por lojista (usa `seller_canonical`, que colapsa
  grafias do mesmo dealer).

Convenção "Todos" (como no PriceTrack): nada ou tudo selecionado = sem filtro.

> **Grupo (1P / Lojas Oficiais / 3P Marketplace / Outros 3P) não é oferecido** —
> o export do PriceTrack que alimenta `pricetrack_daily` traz só `marketplace` e
> `seller`, sem a classificação de grupo (ela existe apenas no painel web do
> PriceTrack). Para tê-lo aqui seria preciso incluí-lo no pipeline de import.

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

3. **Lista peer-to-peer.** Uma linha por **modelo exato** do peer (não agregado
   por marca — uma marca pode ter mais de um modelo no mesmo tier, ex.: Philco
   PAC9FC e PAC9FB no Low/9K), com mín/média/moda/máx/n. Midea sempre primeiro
   em cada capacidade; os demais em moda crescente.

4. **Evolução — Midea (moda) × Peers (mediana).** Série diária por tier/capacidade
   (janela de 7/15/30 dias): linha azul cheia é a moda Midea, linha laranja
   tracejada é a mediana dos peers. Legenda calcula o "Delta%" do período e o
   gap Midea vs peers no último dia (negativo = Midea mais barata). Só nas
   fontes 🟢 Supabase/🟡 Demo — a 🔴 API ao vivo levaria ~2min por dia da janela.

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

- **Supabase (padrão):** lê `pricetrack_daily` da data e do **turno** escolhidos
  (default: data mais recente, turno Diário). Cada linha do dia vira uma
  observação de preço, usando `last_price` — o preço da **última coleta** da
  janela, que é o que o painel do PriceTrack exibe ("Preço exibido: última
  coleta"). Linhas anteriores à migração 006 caem para `min_price` (piso do
  dia). Rápido. Cache de 15 min.
- **API ao vivo:** bate no PriceTrack; a sonda de data é filtrada por marca e o
  read timeout tem piso de 60s, mas o endpoint responde em ~2min por consulta.
- **Demo:** sem credencial, cai em dados sintéticos (marcados como demo).
- **Streamlit Cloud:** os mesmos nomes em Settings → Secrets.

## Arquitetura

```
pricetrack_dashboard/
├── peer.py         # contrato do peer (SKUs por tier/capacidade) + matching por código
├── analytics.py    # funções puras: classifica ofertas → tiers, peers, modelos, série temporal
├── data_source.py  # Supabase (dia único + intervalo) + API ao vivo + amostra demo
├── app.py          # página Streamlit (cards, lista peer-to-peer, gráficos de evolução)
└── tests/          # testes herméticos (peer, analytics, data_source, app), sem rede
```

- **Casamento oferta → tier** é por **código de modelo do fabricante**
  (ex.: `42EBVCA09M5`), procurado como substring no texto normalizado da oferta
  (sku + título + nome). Nunca por preço — o objetivo é medir o preço *dado* o
  tier. A cobertura (quantos modelos do peer casaram) aparece no expander de
  diagnóstico; se a cobertura vier baixa ao vivo, ajuste os códigos em
  `peer.py` contra os títulos reais da coleta.
- **Preço** usado é o melhor à vista (`effective_price`: PIX vs spot, só se
  `AVAILABLE`). Oferta indisponível não entra em mínimo nem média.
- **Base de preço (`price_basis`).** Linhas gravadas até 01/09/2026 estão em
  `spot_legacy`: o preço é o `spotPrice` (à vista cheio), **não** o menor entre
  à vista e PIX — em marketplace com desconto PIX fica ~10% acima do que o
  painel mostra. A página avisa quando lê essas linhas e **dá erro** quando a
  janela mistura as duas bases (a série emendaria dado certo com dado errado).
  Diagnóstico completo e receita de reimport: [`docs/PRICETRACK_FIDELIDADE.md`](../docs/PRICETRACK_FIDELIDADE.md).
- ⚠️ **Uma linha de `pricetrack_daily` não é uma oferta** — é uma listagem-dia
  agregada (N coletas colapsadas em min/média/moda/máx, contadas em
  `obs_count`). A página reduz cada linha a **um** preço representativo
  (`last_price`; `min_price` no legado), então o "máximo" da variação Midea é o
  maior preço **representativo entre as listagens**, não o maior preço
  observado no dia — este último está em `max_price`, que a página não usa.

## Testes

```bash
python -m pytest pricetrack_dashboard/tests/ -q
```

## Atualizar o peer

Quando o peer mudar de trimestre, edite `_PEER_RAW` em `peer.py` (espelho da
aba *Peer to Peer CO*). O índice de casamento e as marcas do filtro se
resolvem sozinhos a partir dele.
