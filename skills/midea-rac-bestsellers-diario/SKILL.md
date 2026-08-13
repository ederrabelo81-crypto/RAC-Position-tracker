---
name: midea-rac-bestsellers-diario
description: >
  Roda e interpreta a coleta diaria de listas "Mais Vendidos" de ar condicionado nos varejistas
  brasileiros (Amazon, Mercado Livre, Magazine Luiza, Shopee, Leroy Merlin, Casas Bahia). Use SEMPRE
  no pedido: rodar o mais vendidos, coleta de bestsellers, brief diario de mais vendidos, ranking do
  dia, quem esta vendendo mais online, ganho ou perda de share de topo de ranking, evolucao semanal
  ou mensal do ranking, checar se a coleta veio certa, comparar com ontem. Tambem quando chegarem
  planilhas xlsx de mais vendidos exportadas do Web Scraper (padrao varejista-AAAA-MM-DD.xlsx), que
  entram como backfill do historico. Entrega: KPI de percentual do top 10 da Midea por plataforma,
  delta em pontos percentuais entre periodos, validacao (quarentena por ordenacao errada,
  contaminacao de categoria, drift de parser), unidades por mes declaradas, movimento de piso 9K/12K
  e estabilidade do ranking. REGRA DURA: ranking e ordinal, nunca vira share de mercado; campo sem
  dado fica vazio. NAO usar para relatorio semanal de sell-out (midea-rac-weekly-report) nem para
  dealer, co-op ou campanha (midea-digital-trade-marketing).
---

# Mais Vendidos RAC · rotina diária

Esta skill opera a série temporal de "Mais Vendidos". Ela existe porque a coleta
de posição/preço do RAC Position Tracker **não contém volume de venda**: mede
oferta, preço e presença. As listas de mais vendidos são a única variável de
**resultado** disponível no nível do SKU, a custo zero.

**O que esta skill protege contra:** o erro de tratar seis ordenações diferentes
como se fossem a mesma medida, e o erro de virar ranking em share.

> **A coleta é automatizada desde Ago/2026.** O fluxo manual de exportar cada
> lista pelo Web Scraper foi substituído pelo pacote `bestsellers/` do
> repositório RAC Position Tracker. Não escreva parser novo nem script de
> análise: o pipeline já carrega os portões de validação e as regras duras.

---

## Fluxo ao ser acionada

1. **Rodar a coleta do dia.**

   ```bash
   python scripts/collect_bestsellers.py
   ```

   Isso coleta as seis plataformas, valida, grava a série e escreve o brief em
   `output/bestsellers/brief_bestsellers_AAAA-MM-DD.md`.

2. **Ler o brief e escrever a leitura de negócio.** O script produz os números;
   a interpretação é do analista. Não entregar o markdown cru sem comentário —
   a seção "Leitura de negócio" abaixo diz o que procurar.

3. **Para evolução no tempo**, usar os relatórios agregados (não coletam nada,
   leem o histórico):

   ```bash
   python scripts/collect_bestsellers.py --relatorio semanal
   python scripts/collect_bestsellers.py --relatorio mensal --ultimos 6
   ```

4. **Se chegarem planilhas do Web Scraper** (backfill do histórico manual):

   ```bash
   python scripts/collect_bestsellers.py --importar-xlsx CAMINHO_DA_PASTA --data AAAA-MM-DD
   ```

   O importador detecta cada campo por CONTEÚDO, não por posição de coluna — o
   Web Scraper renomeia e reordena colunas entre execuções.

5. **Entregar** o brief no chat. DOCX só se pedido.

Opções úteis: `--plataformas amazon` (uma só), `--no-headless` (browser visível,
para depurar seletores), `--paginas 2` (Amazon vai até a posição #50),
`--sem-supabase`, `--notificar`.

---

## Cadência obrigatória

- **Todo dia útil, entre 9h e 10h.** O ranking da Amazon é recalculado de hora
  em hora; comparar sábado 22h com segunda 9h é ruído, não movimento.
- Coletar sempre as seis plataformas. Faltou uma, o brief registra a ausência
  em vez de comparar bases desiguais.
- **Nunca alterar a URL de uma lista sem registrar.** O endpoint efetivamente
  chamado é gravado em toda linha; é o que permite auditar a série depois.

O agendamento oficial é a tarefa **`RAC_Bestsellers`** no PC coletor Windows
(09:30, segunda a sexta, com catch-up no logon), instalada por
`scripts\setup_local_scheduler.ps1`. Roda no notebook porque é lá que estão o
IP residencial e o Chrome logado — em IP de datacenter a Amazon cai em CAPTCHA
e a Shopee some, e o dia registraria ausência em duas das seis listas.

**Uma execução por dia, e só uma.** Recoletar SUBSTITUI a leitura do dia
(idempotência por data + plataforma), então uma segunda rodada troca a foto das
9h pela do horário em que rodou. Não ligar o cron da VM junto com a tarefa do
Windows.

---

## Regras duras

1. **Ranking é ordinal.** Posição #3 não é "3% de share" nem "3× melhor que
   #9". Não somar posições entre plataformas, não converter em share de
   mercado. O que a rotina produz é **share de topo de ranking** — medida de
   prateleira. Share de mercado vem de GfK ou Neotrust, nunca daqui.
2. **Unidades declaradas não são unidades vendidas.** A Shopee traz
   "948 Vendido/Mês", o único campo de velocidade real do conjunto. O Mercado
   Livre traz "+5mil vendidos", acumulado vitalício do anúncio, que favorece
   anúncio velho. O campo `base_vendidos` marca `mes` ou `acumulado`.
   **Nunca somar os dois.**
3. **Comparação diária só entre o mesmo dia da semana.** Sábado promocional
   contra segunda produziu +8,6% de mediana de alta de preço em 10/08/2026 que
   era efeito de calendário, não movimento competitivo.
4. **Não preencher lacuna.** Campo sem dado fica vazio. Sem estimativa, sem
   interpolação.
5. **Tendência exige 3 leituras** do mesmo período. Uma leitura isolada não
   forma nada — o relatório marca isso em `tendencia_valida`.
6. **O grupo Midea** é Midea, Carrier, Springer e Comfee. Consistente com o
   corte de grupo do GfK.

---

## KPI único da rotina

**% do top 10 ocupado pelo grupo Midea, por plataforma, por dia.**
Escopo: apenas split hi-wall (`tipo = SPLIT_HW`).

É a métrica que a coleta de posição não consegue produzir e que antecipa share.
Tudo o mais no brief é diagnóstico de apoio.

Baseline registrado em 10/08/2026 (segunda):

| Plataforma | % top 10 | Melhor posição |
|---|---|---|
| Magazine Luiza | 40% | #1 |
| Amazon | 20% | #3 |
| Mercado Livre | 11% | #3 |
| Leroy Merlin | 0% | #33 |
| Casas Bahia | 0% | ausente |
| Shopee | 0% | #14 |

---

## Leitura de negócio: o que procurar no brief

- **`itens_topN` pequeno.** É o denominador. Numa lista contaminada, "50%" pode
  ser 1 split em 2 — e isso muda completamente a leitura.
- **`veredito = CURADORIA SUSPEITA`** na tabela de estabilidade. Ranking com
  mais de 35% dos itens parados em 48h não é ranking de venda. Leroy Merlin:
  41% parados, deslocamento mediano de 1 posição, contra 12 a 19% e 3 a 4
  posições nas demais. Enquanto isso não for esclarecido com o varejista, o
  número do Leroy não sustenta decisão de corte de verba.
- **`direcao = endpoint mudou`** no relatório periódico. A URL da lista mudou
  entre os períodos: são populações diferentes e o delta fica vazio de
  propósito.
- **Movimento de piso acima de 10%** em marca concorrente. Fim ou início de
  campanha.
- **Marca fora do painel monitorado aparecendo no top 10.** Em 10/08/2026: HQ,
  Hisense, Consul, Britânia, Daikin, Fujitsu. Cada aparição recorrente é um
  pedido de inclusão no painel.
- **Faixa de preço do líder.** Se o #1 estiver abaixo de R$ 1.800 em 9K ou 12K,
  a disputa daquele dia é de entrada, e a linha de entrada é a arma, não a
  premium.

---

## Quando a coleta falha

O brief nomeia a causa; o log e os dumps ficam em `logs/`.

| Sintoma | Ação |
|---|---|
| Plataforma com 0 posições | Ver `logs/bestsellers_*.log` e o dump HTML da plataforma |
| Amazon vazia | CAPTCHA em IP de datacenter — rodar local com `--no-headless` |
| Mercado Livre em gate | `python scripts/setup_local_profile.py --site mercadolivre` |
| Magalu com muro de login | `python scripts/setup_local_profile.py --site magalu` |
| Shopee HTTP 403 | Sessão expirada: `python utils/session_grabber.py --site shopee` |
| Casas Bahia com HTML no lugar de JSON | Akamai: `python utils/session_grabber.py --site casasbahia` |
| Leroy HTTP 404 no Algolia | Índice renomeado — conferir o sortBy na UI antes de trocar a constante |
| CSV e brief saem, mas o banco fica vazio | A tabela `bestsellers` tem RLS ligada e sem policy: só a chave `service_role` escreve. Com a `anon` no `.env` a coleta roda inteira e só o upload falha. Conferir com `scripts\check_local_scheduler.ps1` |

Uma plataforma em **QUARENTENA** não entra na análise: sem o parâmetro de
ordenação, a lista não é de mais vendidos (provavelmente relevância) e usá-la
contamina a série inteira.

Se o dia inteiro falhou, o CSV bruto ainda foi gravado em
`output/bestsellers/` — ele sai ANTES da validação justamente para ser a
evidência de por que cada plataforma caiu.

---

## Arquivos

Código e documentação vivem no repositório RAC Position Tracker:

```
scripts/collect_bestsellers.py   CLI: coleta, relatórios e import de xlsx
bestsellers/config.py            registro das listas (URL, ordenação, mecânica)
bestsellers/metrics.py           KPI de topo e séries diária/semanal/mensal
bestsellers/validate.py          portões de qualidade
bestsellers/sources/             um coletor por varejista
docs/BESTSELLERS.md              documentação completa da rotina
```

Nesta skill:

```
references/plataformas.md        mecânica e armadilha de cada varejista
references/metricas.md           definições, fórmulas e o que nunca fazer
```

Ler `references/plataformas.md` antes de interpretar um resultado estranho de
uma plataforma específica ou de propor a inclusão de um varejista novo.
