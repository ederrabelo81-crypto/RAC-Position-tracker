# Track Position Seller — arquitetura e estratégia do spin-off

> **Documento:** arquitetura de produto e plataforma
> **Origem:** spin-off do RAC Position Tracker (v5.2)
> **Público:** stakeholder técnico do projeto; time comercial; futuros tenants
> **Data:** 04 de Setembro de 2026
> **Status:** proposta para decisão — nada aqui está implementado

---

## 0. Sumário executivo

O RAC Position Tracker responde **"como a Midea está indo?"**. O spin-off
responde **"como EU estou indo contra quem vende a mesma coisa que eu?"**. A
mudança parece um recorte de filtro e não é: ela **inverte o sujeito do fato**,
e com isso muda o grão da tabela, a unidade de cobrança, o perímetro legal e o
modo de falha que mata o produto.

A tese em cinco linhas:

1. A coleta **não** se multiplica por tenant. Ela é e continua **uma só** —
   forkar é o caminho mais rápido para queimar o único IP residencial que faz
   o produto existir.
2. O que se multiplica é o **plano do tenant**: identidade, custo, estoque,
   alertas, apresentação. Instância de seller = projeção + plano privado.
3. O valor não está no dado público (qualquer um vê a vitrine). Está em
   **juntar o dado público contínuo com o dado privado do seller** — e essa
   junção nunca pode sair do plano do tenant.
4. Não conseguimos medir elasticidade de demanda (não temos conversão).
   Conseguimos medir **elasticidade de buy box**, que é observável, honesta e
   suficiente para calibrar repricing.
5. O modo de falha que mata o produto no primeiro mês não é dado errado — é
   **ausência de coleta lida como mudança de mercado**. O antídoto já existe
   no repositório (`pipeline_heartbeat`) e precisa virar portão do alerta.

---

## 0.1 Três discordâncias com o enunciado — ler antes do resto

O pedido original diz "criar instâncias individuais e dedicadas de projeto
para cada seller". Três ressalvas, cada uma com consequência de arquitetura:

**(a) "Instância dedicada" não pode significar coletor dedicado.**
A coleta hoje roda de **um** PC Windows com IP residencial, em 3 turnos, contra
sites protegidos por Akamai (Magalu, Casas Bahia), PerimeterX (Fast Shop) e
reCAPTCHA (Google Shopping). Dezesseis tenants com coletor próprio = 16× o
volume de requisição contra os mesmos alvos, saindo do mesmo bloco residencial.
O resultado não é lentidão, é **bloqueio permanente do ativo**. Instância
dedicada = apresentação, dados privados e configuração. Coleta = singular,
compartilhada, com orçamento racionado (§2.5, §2.6).

**(b) "Elasticidade de preço vs. concorrentes" não é computável com o dado
que temos.** Elasticidade exige quantidade vendida, e não coletamos conversão.

*Correção de 04/09/2026:* a primeira versão deste documento justificava isso
dizendo que o único proxy de resultado — Mais Vendidos — tinha sido
descontinuado. **Está errado: a coleta roda e grava todo dia** (§3.3). Mas a
divergência continua de pé, por um motivo melhor: **ranking é ordinal e nunca
vira quantidade.** Saber que um SKU subiu de 7º para 3º não diz quantas
unidades vendeu, nem quantas venderia a outro preço. O proxy serve para medir
*direção* de resultado, não elasticidade. Entregar "elasticidade" com ele seria
inventar autoridade que o número não tem. O substituto defensável está em §1.4.

**(c) Quatro dos dezesseis sellers nomeados não existem na plataforma hoje.**
Levantamento contra o código:

| Seller | Loja própria (`DEALER_CONFIGS`) | Identidade em marketplace (`SELLER_GROUPS`) |
|---|---|---|
| Frigelar, Dufrio, Central Ar, Web Continental, Polo Ar, Leveros, Ar Certo, Frio Peças, Clima Rio, Norte Refrigeração, A.Dias | ✅ | ✅ |
| Denteck | ❌ | ✅ |
| **Livaar, Duzzi, BHP, Futura Climatização** | ❌ | ❌ |

Onboarding de tenant, portanto, **não é criar um login** — é um trabalho de
coleta (novo dealer no `DEALER_CONFIGS` e/ou nova entrada em `SELLER_GROUPS`
com identidade confirmada). Isso precisa estar no preço e no prazo comercial.

---

## 1. Reposicionamento estratégico

### 1.1 A inversão do sujeito

Hoje o sujeito analítico é `marca_monitorada`. A pergunta canônica é *"onde a
Midea aparece na keyword X, e quem ganhou a buy box dela?"* — o seller é
**atributo da oferta vencedora**.

No spin-off o sujeito é o **seller**, e a marca vira **atributo do portfólio**.
A pergunta canônica passa a ser *"em que células (marca × BTU × plataforma) eu
apareço, contra quem, em que posição, e onde eu não apareço?"*

Consequência direta no esquema: `coletas` já tem os índices certos
(`idx_coletas_seller_id`, `idx_coletas_buybox_seller`) mas **não tem o fato com
sujeito seller**. Nasce uma camada derivada com grão
`(tenant_seller_id, offer_key, data, turno)` — §2.10.

Consequência menos óbvia, e mais cara: **o universo de keywords está errado
para o novo sujeito.** As keywords de hoje foram escolhidas para achar Midea.
O catálogo do Dufrio tem marcas, BTUs e categorias que a lista atual não
cobre. Ampliar a lista é o item que consome o orçamento de anti-bot, e por isso
é o item que define os planos comerciais (§4.3).

### 1.2 A assimetria de conhecimento — o que cada lado sabe

| | Indústria (hoje) | Seller (spin-off) | Plataforma (nós) |
|---|---|---|---|
| Sell-in próprio | sabe | — | não vê |
| Custo de aquisição | não vê o do dealer | sabe o seu | **nunca deve ver** |
| Estoque | não vê | sabe o seu | só via ERP do tenant |
| Política MAP | define | sofre/reporta | observa o preço, não a política |
| Vitrine pública | vê | vê | **vê melhor que os dois — continuamente** |

A plataforma só enxerga a **superfície pública**. É por isso que o produto
não é "dados de mercado" (commodity, qualquer um abre a página) e sim:

1. **continuidade** — 3 leituras/dia com identidade de oferta estável
   (`offer_key`), o que transforma fotografia em série; e
2. **a junção** do público contínuo com o privado do tenant (custo, estoque).

A junção é o produto. E é exatamente o que não pode vazar. Toda a §2 é
consequência dessa frase.

### 1.3 As perguntas do gerente de e-commerce — o que dá, o que não dá

| Pergunta | Dá hoje? | O que falta |
|---|---|---|
| Qual meu **buy box win rate** por SKU/plataforma/dia? | ✅ | rollup com sujeito seller |
| Quem me tirou a buy box, quando, e por quanto? | ✅ | `offer_key` já entrega; falta o evento |
| Meu **share of search** por célula de portfólio | ⚠️ | universo de keywords do catálogo do tenant |
| **Gap de preço** vs. vencedor da buy box | ⚠️ | bloqueado pelo `price_basis` (§6) |
| Quem **entrou/saiu** da vitrine (offer churn) | ✅ | `offer_key` + série; falta o detector |
| Estou **abaixo do MAP** / alguém está? | ⚠️ | tabela de referência MAP por SKU (não existe) |
| **Elasticidade de preço** | ❌ | conversão. Mais Vendidos é proxy de *direção*, ordinal — nunca vira quantidade (§1.4) |
| **Giro de estoque** e benchmark de giro | ❌ | ERP do tenant; benchmark cruzado tem trava legal (§4.1) |
| **Alerta de margem** | ⚠️ | custo é privado; cálculo fica no plano do tenant |
| **Alavanca de negociação com fornecedor** | ⚠️ | ver abaixo |

Sobre a alavanca de negociação, vale a precisão: o dado sustenta
*"perdi a buy box do 12k Inverter da marca M para o concorrente C em 61% dos
turnos de agosto, com preço 8,2% abaixo do meu"*. **Não** sustenta
*"porque C compra mais barato"* — isso é inferência sobre a política do
fornecedor, e o produto deve rotulá-la como tal. Confundir observação com
inferência é o que faz o número perder credibilidade na primeira reunião em
que o fornecedor está na sala.

### 1.4 Elasticidade: o substituto honesto

Não medimos ΔQuantidade/ΔPreço. Medimos, com 3 observações/dia e `offer_key`
estável, a **função-degrau de virada de buy box**:

> Para a oferta *k*, qual o **delta de preço** contra o vencedor corrente a
> partir do qual a buy box vira? E quanto tempo ela fica virada?

Isso é observável, é o que o repricer precisa saber, e evita a promessa falsa.
Dois produtos derivam daí:

- **Limiar de virada** por oferta/célula — a régua que o bot de repricing do
  tenant deve usar, em vez de "sempre R$ 0,01 abaixo".
- **Custo da vitória** — quanto de preço se abre mão por ponto de win rate.
  Cruzado com o custo *privado* do tenant, vira **margem por ponto de buy box**.
  Esse cruzamento só acontece dentro do plano do tenant.

Limite duro a declarar ao cliente: com 3 turnos, medimos o **regime** (o
limiar), não a **reação** (o movimento do concorrente em minutos). Quem quer
reação precisa da watchlist horária (§2.8) e ainda assim não terá segundos.

---

## 2. Arquitetura técnica

### 2.1 Dois planos, e a fronteira entre eles

```
┌──────────────────────────────────────────────────────────────────────┐
│  SOL — Shared Observation Lake  (o que já existe)                    │
│  coletas · pricetrack_daily · bestsellers · pipeline_heartbeat       │
│  100% superfície pública. Nenhum tenant é dono. Ninguém escreve       │
│  aqui a partir de dado de tenant. Chave: service_role (coletor).      │
└──────────────────────────────────────────────────────────────────────┘
             │ projeções somente-leitura, mediadas por views
             ▼
┌──────────────────────────────────────────────────────────────────────┐
│  Camada de projeção — views + registro de métricas                   │
│  v_market_public · v_seller_facts · v_benchmark_kanon                │
│  Aplica: canonicalização de seller, portão de heartbeat, k-anonimato │
└──────────────────────────────────────────────────────────────────────┘
             │ RLS por tenant_id (claim do JWT)
             ▼
┌──────────────────────────────────────────────────────────────────────┐
│  TPS — Tenant Private Store  (um schema por tenant, ou RLS por linha) │
│  tenant_catalog · tenant_cost · tenant_stock · tenant_alert_rule     │
│  tenant_metric_cache · tenant_erp_credential (KMS)                    │
│  Métrica que mistura privado + público nasce e MORRE aqui.            │
└──────────────────────────────────────────────────────────────────────┘
```

**A regra que sustenta tudo:** *nenhuma métrica derivada de dado privado de
tenant volta para o SOL.* Não por elegância — é simultaneamente o firewall
concorrencial (§4.1) e a fronteira de operador/controlador da LGPD (§4.2). Uma
regra, dois problemas resolvidos.

Implementação: schema-per-tenant para o TPS (isolamento forte, migração
independente, `pg_dump` por tenant no offboarding) e RLS por linha apenas nas
tabelas de controle (`tenants`, `tenant_users`, `tenant_seller_claim`).
Schema-per-tenant escala mal acima de ~algumas centenas de schemas no
Postgres; para o horizonte declarado (dezenas de sellers) é a escolha certa, e
o ponto de reversão deve ser documentado antes de chegar nele.

### 2.2 `tenant` ≠ `seller` — a confusão que quebra o produto

- `seller_canonical` (de `utils/seller_names.py`) é **identidade pública
  observada**. Existe para qualquer lojista, cliente ou não.
- `tenant` é **cliente contratado**.
- Um tenant **reivindica** N identidades públicas: o Dufrio é `dufrio` no ML,
  `Dufrio` na Amazon e opera `dufrio.com.br` como 1P.

Daí `tenant_seller_claim`, com verificação. O risco da reivindicação sem
verificação não é vazamento (o dado é público) — é **erro de atribuição**: o
tenant A vê o painel "dele" cheio de linhas do concorrente B e toma decisão em
cima disso. Verificação mínima aceitável: e-mail no domínio da loja + conferência
manual contra a página do seller no marketplace, registrada com autor e data.

Duas armadilhas herdadas, ambas já documentadas no `CLAUDE.md` e que o
spin-off **repete se não prestar atenção**:

1. `plataforma` e `seller` são **namespaces diferentes** — `WebContinental`
   (plataforma) ≠ `Web Continental` (seller). Unificar quebra o filtro.
2. O filtro precisa **re-expandir** o canônico para as grafias brutas antes de
   ir ao PostgREST (`_expand_sellers` em `app.py`), senão o recorte volta
   vazio. Toda query do plano do tenant que filtra por seller passa
   obrigatoriamente por `variants_for()`.

### 2.3 Segmentação: "observado-público" vs "contribuído"

Esta é a régua de segmentação, e ela é mais simples e mais defensável do que
"esconder métrica de concorrente":

| Classe | Origem | O que o tenant pode ver |
|---|---|---|
| **Observado-público** | vitrine (`coletas`, `pricetrack`) | **Tudo, nomeando o concorrente.** Preço, posição, buy box e reputação estão na tela para qualquer um. Esconder não protege ninguém e destrói o produto. |
| **Contribuído** | ERP/upload do tenant | **Só o próprio.** Nunca nomeado, nunca cruzado. |
| **Derivado misto** | contribuído × público | Só dentro do TPS de quem contribuiu. Nunca republicado. |
| **Benchmark agregado** | contribuído de vários | Só sob k-anonimato + teto de dominância (§4.1). Por padrão: **desligado**. |

Nomear o concorrente em dado observado não é só permitido — é o produto. O que
não pode existir é o caminho pelo qual o custo do Duzzi influencia o número que
o Frigelar lê.

### 2.4 RLS e o registro de métricas — contrato em código

O repositório já tem o idioma certo: `utils/pipeline_registry.py` põe o
contrato de execução em código, com `validar_registro()` reprovando o PR no CI.
O spin-off replica isso para a política de dado.

```python
# tps/metric_registry.py — esboço
from dataclasses import dataclass
from typing import Literal, Optional

Classe = Literal["observado_publico", "contribuido", "derivado_misto", "benchmark"]

@dataclass(frozen=True)
class Metrica:
    id: str
    classe: Classe
    fontes: tuple[str, ...]          # tabelas/views de origem
    nomeia_concorrente: bool         # True só é legal em observado_publico
    k_minimo: Optional[int] = None   # obrigatório em benchmark
    teto_dominancia: Optional[float] = None  # nenhum tenant > X do agregado
    exige_heartbeat: bool = True     # §2.9 — silêncio não é mudança

def validar_registro() -> None:
    """Reprova no CI:
      * benchmark sem k_minimo ou sem teto_dominancia;
      * nomeia_concorrente=True fora de observado_publico;
      * metrica cujas `fontes` incluem tabela do TPS e classe != derivado_misto;
      * derivado_misto exposto em endpoint compartilhado.
    """
```

O ganho é o mesmo do `pipeline_registry`: a política deixa de ser folclore e
vira teste. `tests/test_metric_registry.py` roda em todo PR, junto da suíte que
já existe em `.github/workflows/tests.yml`.

RLS propriamente dita segue o padrão já validado na migração 012 (leitura
liberada, escrita negada por ausência de policy), agora com claim de JWT. Ela
se aplica às **tabelas de controle**, que são compartilhadas por definição — o
isolamento do TPS é o schema, não a policy (§2.1), e por isso `tenant_cost` e
`tenant_catalog` não aparecem aqui:

```sql
ALTER TABLE tenant_seller_claim ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenant_seller_claim_isolamento ON tenant_seller_claim
    FOR ALL
    USING      (tenant_id = (auth.jwt() ->> 'tenant_id')::uuid)
    WITH CHECK (tenant_id = (auth.jwt() ->> 'tenant_id')::uuid);
```

Lição da 012 a não repetir: **RLS ligada sem policy responde `[]` com HTTP
200** — sem erro, sem log. Num painel de tenant isso é indistinguível de "o
mercado não teve movimento". Todo endpoint do TPS precisa distinguir
*vazio-porque-não-há* de *vazio-porque-negado*, e o teste de fumaça de
onboarding tem que provar isso.

### 2.5 Coleta: por que NÃO forkar o repositório por seller

Já em §0.1(a), aqui a consequência de arquitetura. O coletor permanece único e
ganha três coisas:

1. **Universo de keywords por tenant** — `tenant_keyword` alimenta o
   `KEYWORDS_LIST` efetivo; a união deduplicada é o que roda. Dois tenants
   pedindo "ar condicionado 12000 inverter" custam uma coleta, não duas.
2. **Contabilidade de origem** — cada keyword sabe quais tenants a pediram,
   para rateio de custo e para saber o que cortar quando o orçamento apertar.
3. **Dono único por (plataforma, turno)** — a regra dura do
   `pipeline_registry` vale igual: plataforma órfã ninguém cobra; plataforma com
   dois donos gera alerta duplicado, e alerta duplicado vira alerta ignorado.

O que **pode** ser por tenant sem tocar o coletor: o front (branding, domínio,
paleta), o TPS, as regras de alerta, as integrações de ERP e as chaves de API.

### 2.6 O orçamento de coleta é o recurso escasso — e a base do preço

O gargalo real não é CPU nem Postgres. É **quantas requisições o IP
residencial aguenta antes do Akamai marcar**. Isso torna o orçamento de coleta
um recurso finito e rateável, e faz os planos comerciais decorrerem de um custo
marginal real em vez de serem arbitrários (§4.3).

Unidades a orçar e medir:

| Recurso | Unidade | Escassez |
|---|---|---|
| Keywords no crawl compartilhado | keyword × plataforma × turno | **alta** — direto no anti-bot |
| Slots de watchlist horária | SKU × hora | **alta** |
| PDPs (Amazon buy box, Leroy seller) | PDP/run | **alta** — já custa minutos hoje |
| Profundidade de histórico | meses | baixa (armazenamento) |
| Chamadas de API / webhooks | req/min | baixa |
| Assentos | usuário | zero |

Instrumentação: estender `pipeline_heartbeat` com contagem de requisição por
plataforma e taxa de bloqueio, para que o orçamento seja **observado**, não
estimado.

### 2.7 API e integração com ERP

**Superfície pública (v1), toda com escopo de tenant no token:**

```
GET  /v1/market/offers          ?platform=&offer_key=&from=&to=&cursor=
GET  /v1/market/buybox/history  ?offer_key=&from=&to=
GET  /v1/me/position            ?platform=&cell=&turno=        # sujeito seller
GET  /v1/me/buybox/winrate      ?granularity=day|turno
GET  /v1/me/gaps                # células onde não apareço e a buy box é batível
POST /v1/me/catalog             # de-para SKU interno ↔ offer_key
POST /v1/me/cost                # custo — TPS, nunca sai
GET  /v1/me/alerts  |  POST /v1/me/alert-rules
POST /v1/webhooks   (registro)  → entrega assinada (HMAC), com replay
```

**Contratos que não são negociáveis:**

- **Paginação por cursor**, nunca offset — a série cresce por baixo, offset
  duplica e pula linha.
- **Idempotência na escrita** (`Idempotency-Key`). O repositório já pagou essa
  conta: exports órfãos do PriceTrack seguravam os 3 slots da organização até o
  `journal.py` passar a **adotar** o export anterior em vez de duplicá-lo. Mesmo
  padrão para push de ERP e entrega de webhook.
- **Toda resposta carimba a proveniência**: `turno`, `coletado_em`,
  `price_basis` e `heartbeat_ok`. Um número sem base explícita se lê como base
  antiga (a regra dura do `price_basis`), e num produto pago isso é dívida
  contratual, não detalhe.
- **Versionamento explícito da chave derivada**: `offer_key` tem prefixo `v1|`.
  Se a regra de derivação mudar, séries de versões diferentes **não se
  comparam** — a API tem que recusar o cruzamento, não silenciosamente uni-lo.

**Adapters de ERP (Bling, Tiny, próprios):** pull-based, credencial por tenant
guardada cifrada (KMS/Vault, nunca em `.env` compartilhado), com um *port*
único e adapters finos:

```python
class ERPPort(Protocol):
    def pull_catalog(self, since: datetime) -> Iterable[CatalogItem]: ...
    def pull_cost(self, since: datetime)    -> Iterable[CostPoint]: ...
    def pull_stock(self, since: datetime)   -> Iterable[StockPoint]: ...
```

O modo de autenticação varia por ERP e por versão da API (OAuth2 nas gerações
atuais de Bling e Tiny; token estático em integrações legadas e próprias) — o
adapter deve suportar os dois modos, e a versão exata de cada API precisa ser
confirmada na implementação, não presumida deste documento.

**O problema difícil do adapter não é HTTP, é o de-para.** O SKU interno do
seller não é o `marketplace_product_id`. O repositório já tem as peças —
`utils/sku_matcher.py`, `utils/depara_resolver.py`, `utils/normalize_product.py`
e a tabela de de-para/suspeitos da migração 010. Reusar, com uma regra dura
importada da experiência do `seller_names.py`: **casamento por semelhança de
string só entra com confirmação humana**; automático transfere custo de um SKU
para outro e envenena a margem calculada.

### 2.8 Alertas: três classes e o piso de 8 horas

| Classe | Gatilho | Latência real | Entrega |
|---|---|---|---|
| **A — observacional** | perda de buy box, queda de rank, novo entrante, preço abaixo do MAP | **≤ 8h** (turno) | push/e-mail/webhook |
| **B — privado** | preço próprio abaixo do piso de margem, ruptura de estoque | **segundos** (no push do ERP) | idem |
| **C — reativo** | guerra de preço em curso | **fora de escopo** na cadência atual | — |

Ser explícito sobre a classe C é requisito comercial, não modéstia. Repricers
de marketplace se movem em minutos; nós fotografamos 3× ao dia. Vender
"repricing dinâmico" com esse dado gera churn no segundo mês.

**A ponte é a watchlist horária:** um conjunto pequeno de SKUs por tenant
(ordem de 50) coletado de hora em hora. É acessível ao orçamento de anti-bot,
é vendável por si só, e é honesto — "hora em hora nestes 50", não "tempo real
em tudo".

**Contra fadiga de alerta:** posição oscila; queda de 2 posições é ruído.
Usar controle estatístico (EWMA sobre a posição por `offer_key`, banda por
volatilidade histórica da célula) e disparar só fora de banda. A lição do
`pipeline_registry` é literal aqui: alerta duplicado é o primeiro passo para
alerta ignorado.

### 2.9 Silêncio ≠ mudança — o portão de heartbeat

**Este é o requisito que mais importa e o mais fácil de esquecer.**

Se a coleta do Magalu é bloqueada às 14:00, as ofertas do Magalu somem daquele
turno. Sem portão, o painel do tenant lê: *"seus 12 concorrentes saíram da
vitrine e você ganhou 100% da buy box"*. O alerta dispara, o gerente age, e o
produto perdeu a confiança para sempre.

O repositório já resolveu exatamente esse problema do lado da pipeline: o
`pipeline_heartbeat` (migração 015) é um *dead man's switch* — a ausência de
batida é que dispara o alarme. O spin-off precisa **consumir** esse livro-razão
como pré-condição de toda leitura do tenant:

```
para cada (plataforma, data, turno) exibido ou alertado:
    se não houve heartbeat de sucesso  →  a célula é INDETERMINADA
    INDETERMINADA nunca vira zero, nunca vira "concorrente saiu",
    nunca dispara alerta, e é rotulada na UI e na resposta da API.
```

Corolário para o SLA contratual: o compromisso vendável é sobre
**disponibilidade de leitura por plataforma/turno**, não sobre "dado completo".
Prometer completude de uma coleta anti-bot é prometer o que não se controla.

**Isto não é hipótese — está acontecendo agora, no próprio repositório.** A
coleta de `bestsellers` roda todo dia e grava no Supabase, mas **não tem job no
`pipeline_registry.py`**. Sem job não há batida de ponto; sem batida, ausência
de fonte não é evento. Resultado conferido no banco em 04/09/2026: **7 das 20
fontes declaradas nunca gravaram uma linha**, o job termina verde, o painel
mostra 13 fontes e nada nem ninguém cobra as 7. É o modo de falha do Google
Shopping de agosto se repetindo numa coleta que ninguém sabia que estava
descoberta. Um tenant pagante lendo esse painel concluiria que aqueles
concorrentes não estão no ranking — quando na verdade nós é que não olhamos.

**Regra dura que decorre:** nenhuma fonte de dado entra no produto do seller
antes de ter dono declarado no registro de execução. Coleta sem job é coleta
que ninguém cobra.

### 2.10 DDL proposta (esboço — nada aplicado)

```sql
-- ── Controle ────────────────────────────────────────────────────────────
CREATE TABLE tenants (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    slug          text UNIQUE NOT NULL,          -- 'dufrio', 'duzzi'
    razao_social  text NOT NULL,
    cnpj          text,
    plano         text NOT NULL,                  -- essencial|profissional|enterprise
    criado_em     timestamptz NOT NULL DEFAULT now(),
    encerrado_em  timestamptz
);

-- Identidade pública reivindicada. Verificação é obrigatória e auditada.
CREATE TABLE tenant_seller_claim (
    tenant_id        uuid REFERENCES tenants(id),
    seller_canonical text NOT NULL,               -- chave de utils/seller_names
    plataforma       text,                        -- NULL = todas
    modelo           text NOT NULL,               -- '1P' | '3P'
    verificado_em    timestamptz,
    verificado_por   text
);

-- PRIMARY KEY/UNIQUE de tabela só aceita NOME DE COLUNA — `COALESCE(...)` ali
-- é erro de sintaxe. O índice por expressão é o que funciona, e é também o
-- único jeito de `plataforma IS NULL` ("todas") colidir consigo mesma: num
-- UNIQUE comum, dois NULL são distintos e a linha duplicaria em silêncio.
CREATE UNIQUE INDEX uq_tenant_seller_claim
    ON tenant_seller_claim (tenant_id, seller_canonical, COALESCE(plataforma, '*'));

-- Universo de keywords: origem por tenant, execução compartilhada.
CREATE TABLE tenant_keyword (
    tenant_id  uuid REFERENCES tenants(id),
    keyword    text NOT NULL,
    categoria  text,
    prioridade smallint NOT NULL DEFAULT 2,
    PRIMARY KEY (tenant_id, keyword)
);

-- ── Fato com sujeito seller (derivado do SOL, sem dado privado) ─────────
CREATE TABLE seller_offer_daily (
    data                date    NOT NULL,
    turno               text    NOT NULL,
    plataforma          text    NOT NULL,
    seller_canonical    text    NOT NULL,
    offer_key           text    NOT NULL,        -- v1|... (versionada)
    marketplace_product_id text,
    marca               text,
    posicao_geral       integer,
    posicao_organica    integer,
    patrocinado         boolean,
    venceu_buybox       boolean,
    buybox_seller       text,                    -- observado-público: pode nomear
    preco               numeric(12,2),
    price_basis         text,                    -- NUNCA implícito
    qtd_sellers         integer,
    gap_vs_buybox_pct   numeric(6,3),
    heartbeat_ok        boolean NOT NULL,        -- §2.9 — false ⇒ INDETERMINADO
    PRIMARY KEY (data, turno, plataforma, offer_key, seller_canonical)
);

CREATE INDEX ON seller_offer_daily (seller_canonical, plataforma, data);
CREATE INDEX ON seller_offer_daily (offer_key, data);

-- ── Plano privado — UM SCHEMA POR TENANT ────────────────────────────────
-- O schema É o isolamento (§2.1): aqui não há coluna `tenant_id` e não há
-- policy de RLS. Quem chega com o schema errado não filtra mal — não alcança.
CREATE SCHEMA tenant_dufrio;             -- um por tenant, nomeado pelo slug

CREATE TABLE tenant_dufrio.catalog (     -- de-para SKU interno ↔ oferta
    sku_interno   text NOT NULL,
    offer_key     text,                  -- NULL = ainda não casado
    plataforma    text,
    origem_match  text NOT NULL,         -- 'erp'|'manual'|'sugerido'
    confirmado_em timestamptz            -- NULL ⇒ sugestão, não usar em margem
);

-- Mesma razão do claim acima: `offer_key` NULL precisa colidir consigo mesmo.
CREATE UNIQUE INDEX uq_catalog_sku
    ON tenant_dufrio.catalog (sku_interno, COALESCE(offer_key, '*'));

CREATE TABLE tenant_dufrio.cost (        -- NUNCA sai deste schema
    sku_interno  text NOT NULL,
    vigente_de   date NOT NULL,
    custo        numeric(12,2) NOT NULL,
    moeda        text NOT NULL DEFAULT 'BRL',
    fonte        text NOT NULL,          -- 'bling'|'tiny'|'upload'|'api'
    PRIMARY KEY (sku_interno, vigente_de)
);
```

**Se o ponto de reversão de §2.1 for atingido** (schemas demais para o
Postgres administrar), `catalog` e `cost` viram tabelas compartilhadas com
coluna `tenant_id` e ganham a mesma policy de RLS das tabelas de controle. As
duas variantes são excludentes: implementar as duas dá dois caminhos de
isolamento para a mesma linha, e o mais fraco é o que vale.

---

## 3. Priorização por realidade operacional: 1P vs 3P

O corte 1P/3P não é cosmético — os dois modelos otimizam **funções-objetivo
diferentes**. O 1P protege margem e marca num ativo que ele controla (a própria
loja). O 3P disputa uma vitrine que não é dele, sob regras que não escreve.

### 3.1 O que o 1P precisa (Frigelar, Dufrio, Central Ar, Leveros — loja própria)

| Prioridade | Feature | Dado hoje | Trabalho |
|---|---|---|---|
| P0 | **Vigilância de MAP** — quem está abaixo do piso, onde, há quanto tempo | preço e seller ✅ | falta a **tabela MAP** (fornecida por marca ou tenant) |
| P0 | **Share no comparador** — Google Shopping é a superfície de aquisição do 1P; `qtd_sellers` já é o nº de lojas comparando | ✅ parcial | GS é frágil (reCAPTCHA); SLA precisa refletir isso |
| P1 | **Gap de conteúdo** — título/atributos da minha PDP vs. a do concorrente e vs. o PXM da marca | `attr_parser` + `normalize_product` ✅ | score de completude; sem integração PXM não há "verdade" da marca |
| P1 | **Posicionamento de garantia/oficialidade** — presença de selo de loja oficial/autorizado | `tipo_seller` ✅ | rollup |
| P2 | **Canibalização** — meu preço na minha loja vs. o meu mesmo preço no marketplace | ✅ (mesmo tenant, 2 identidades) | requer claim das duas identidades |
| — | Busca interna da própria loja (rankeio bem meus próprios SKUs?) | ❌ | não coletamos; exigiria coleta da busca do site do tenant, **com autorização dele** |

A canibalização é subestimada e é a feature que mais rápido paga a assinatura
de um 1P: o dealer com loja própria **e** operação 3P frequentemente compete
consigo mesmo e não enxerga isso porque os dois números moram em times
diferentes.

**O que o site próprio NÃO entrega — e por que isso não é uma lacuna.**
Na loja própria o lojista é o único jogador: ele define o sortimento, a ordem
da vitrine, o preço e o destaque. Ranking ali mede a **decisão dele**, não a
competição — um "mais vendidos" do site do Dufrio diz o que o Dufrio escolheu
empurrar, e comparar isso com o de outro dealer soma duas vitrines que nunca se
enfrentaram. **A guerra acontece no marketplace**, onde N lojistas disputam a
mesma página com a mesma régua.

Daí a assimetria deliberada do produto: no marketplace o valor é **posição
relativa** (buy box, rank, share de vitrine); no site próprio o valor é
**consistência** — preço e sortimento da loja própria contra a operação do
mesmo dealer nos marketplaces (canibalização), e MAP contra o que os 3P fazem
com o preço. É por isso que a ausência de Frigelar, Ar Certo, Dufrio, Central
Ar, Leveros e Ferreira Costa no `bestsellers` **não é prioridade**, enquanto a
de Casas Bahia é (bloqueador 5).

### 3.2 O que o 3P precisa (Web Continental, Polo Ar, Ar Certo, Duzzi, Denteck, BHP…)

| Prioridade | Feature | Dado hoje | Trabalho |
|---|---|---|---|
| P0 | **Win rate de buy box + decomposição da perda** (preço / fulfillment / reputação) | preço ✅, `tipo_seller` ✅, `reputacao_seller` ✅ | decomposição é **modelo, não observação** — rotular como inferência |
| P0 | **Calibração de repricing** — limiar de virada por célula (§1.4) | ✅ com 3 turnos | detector de degrau; watchlist horária refina |
| P0 | **Churn de oferta/seller** — quem entrou, quem sumiu | `offer_key` ✅ | detector de evento |
| P1 | **Otimização de portfólio multimarca** — células contestadas × células livres onde a buy box é batível | marca ✅ + posição ✅ | precisa do catálogo do tenant para saber o que ele *pode* vender |
| P1 | **Comparação de custo de fulfillment entre marketplaces** | ❌ | **construir**: tabela de comissão/frete por marketplace × categoria × faixa de preço |
| P2 | **Pressão de patrocinado** — quanto do topo é pago na minha célula | `patrocinado` ✅ | rollup |

O item de fulfillment merece destaque porque é o que os sellers mais pedem e o
único da lista P0/P1 que **não** é reprocessamento: comissão, frete e regras de
programa (Full/FBA/Envios) não estão em lugar nenhum da base. É construção de
dataset de referência, com manutenção recorrente quando os marketplaces mudam
tabela — e portanto é candidato natural a ficar no plano mais caro ou a ser
adiado para a fase 3.

### 3.3 Mapa dos fluxos existentes: reusar, reprocessar, enriquecer, construir

| Fluxo atual | Serve ao seller? | Ação |
|---|---|---|
| `coletas` (3 turnos, buy box, posição, `offer_key`) | **sim, é a espinha dorsal** | **reprocessar** com sujeito seller → `seller_offer_daily` |
| `utils/seller_names.py` (de-para canônico) | sim — é o que torna o tenant identificável | **reusar**; virar `tenant_seller_claim` |
| `utils/offer_identity.py` (`offer_key` v1) | sim — é o que torna série possível | **reusar**; expor a versão na API |
| `pricetrack_daily` (preço diário, `price_basis`) | sim | **corrigir primeiro** (§6) — 36 dias inconsistentes |
| `scrapers/dealers.py` (36 lojas próprias) | sim — é a visão 1P | **reusar**; cobre 11 dos 16 sellers citados |
| `bestsellers` (rank ordinal, **coletando todo dia**) | é o único sinal de resultado que existe | **reusar, só marketplace** — conferido no banco 04/09: 20 dos últimos 21 dias. Ver o buraco de cobertura no bloqueador 5 |
| `pipeline_heartbeat` (livro-razão) | sim — vira portão de qualidade | **reusar** como pré-condição de toda leitura (§2.9) |
| `screenshots` (retenção 15 dias) | evidência de MAP e de vitrine | **reusar com cuidado** — implicação de LGPD (§4.2) |
| Custo / estoque / vendas do seller | — | **construir** via ERP (§2.7) |
| Comissão e frete por marketplace | — | **construir** (dataset de referência) |
| Referência de MAP por SKU | — | **construir** (fornecido por marca/tenant) |
| Conversão / quantidade vendida | — | **não existe** — só do tenant, e mesmo assim só o dele (§4.1) |

---

## 4. Guardrails comerciais e éticos

### 4.1 Antitruste: o risco real não é mostrar preço público

Preço de vitrine é público; coletá-lo e mostrá-lo é lícito e é o negócio. O
risco de verdade é a plataforma virar **hub de sinalização entre concorrentes**
— o padrão *hub-and-spoke*, em que um terceiro comum coordena, sem que os
concorrentes precisem falar entre si. No Brasil isso cai sob a Lei 12.529/2011
e a competência do CADE, e a jurisprudência internacional sobre troca de
informação sensível entre concorrentes é robusta o bastante para tratar o tema
como restrição de projeto, não como nota de rodapé. **Este documento não
substitui parecer jurídico; as regras abaixo são de engenharia, para que o
parecer tenha o que aprovar.**

Cinco regras duras, todas verificáveis pelo `metric_registry` (§2.4):

1. **Recomendação é sempre unilateral.** Entrada permitida = dado público +
   dado privado do *próprio* tenant. Nunca dado privado de outro tenant, nem
   direto nem via agregado. Se dois tenants concorrentes recebem preço
   recomendado derivado dos custos um do outro, a plataforma coordenou preço.
2. **Nada prospectivo.** Só observação realizada. Nunca intenção, plano de
   preço, calendário de campanha ou estoque futuro de um tenant para outro.
3. **Benchmark contribuído: desligado por padrão.** Se ligado, exige
   k ≥ 5 tenants distintos **e** teto de dominância (nenhum tenant > 25% do
   agregado) **e** defasagem temporal. Sem os três, não publica.
4. **Sem broker de acordo.** A plataforma jamais transmite proposta,
   solicitação ou "convite" de preço entre tenants — nem como feature, nem como
   campo livre em relatório compartilhado.
5. **Fiscalização de MAP é evidência, não execução.** Reportar ao 1P e à marca
   *"a oferta X está a R$ Y, abaixo do piso Z"* é observação. A plataforma não
   media, não notifica o concorrente em nome de ninguém e não sugere retaliação.
   A fronteira entre política vertical legítima de MAP e coordenação horizontal
   é fina o bastante para exigir que o produto fique do lado da evidência.

Corolário direto ao pedido do enunciado: **"benchmark de giro de estoque" entre
sellers concorrentes é a feature de maior risco do documento inteiro.** Giro é
dado contribuído e sensível. Ou fica sob a regra 3 (k-anonimato + defasagem +
teto), ou não existe. Recomendação: fase 3, com parecer jurídico prévio.

### 4.2 LGPD (Lei 13.709/2018)

O reflexo é "são dados de empresa, LGPD não se aplica". Está errado em quatro
pontos concretos deste sistema:

1. **Seller de marketplace é frequentemente pessoa física.** Boa parte dos
   3P menores é MEI ou pessoa natural; um apelido de loja associável a uma
   pessoa **é dado pessoal**. Como o produto ranqueia, perfila e alerta sobre
   sellers nomeados, isso é tratamento — e no caso de perfis pequenos pode
   configurar avaliação de aspectos pessoais.
2. **Screenshots.** `Screenshot Busca` e `Screenshot Produto` capturam a tela
   inteira: nome de vendedor, avaliação com nome de comprador, às vezes
   endereço de retirada. Hoje a retenção é de 15 dias e o bucket é
   `rac-screenshots`. Para um produto multi-tenant é preciso: retenção
   declarada, controle de acesso por tenant, e proibição de exportação em massa.
3. **Texto de avaliação — não coletar.** Hoje guardamos `reputacao_seller`
   (nota/nível) e `qtd_avaliacoes`, não o corpo da avaliação. **Manter assim
   como regra dura**: texto de review é o vetor mais fácil de dado pessoal
   entrar sem ninguém decidir que entrou.
4. **Usuários do tenant** (o gerente de e-commerce) — conta, log de acesso,
   trilha de auditoria são dados pessoais nossos de tratar.

Papéis, que precisam estar no contrato antes do primeiro tenant:

| Dado | Papel da plataforma | Base legal candidata |
|---|---|---|
| Vitrine pública coletada | **controladora** | legítimo interesse (art. 7º, IX) + teste de balanceamento documentado |
| Catálogo/custo/estoque via ERP | **operadora** do tenant | execução de contrato (art. 7º, V) |
| Contas e logs de usuários do tenant | **controladora** | execução de contrato / obrigação legal |

Obrigações operacionais que decorrem: registro de operações de tratamento
(art. 37), encarregado/DPO nomeado (art. 41), lista de suboperadores no
contrato, e — ponto concreto e frequentemente esquecido — **transferência
internacional (art. 33)**: Supabase é infraestrutura hospedada fora do Brasil,
então a região do projeto precisa ser fixada, documentada e coberta por
cláusulas contratuais. Além disso: TTL explícito para screenshots e dumps de
HTML de debug (que hoje existem no PC coletor, em `logs/*.html`), e um
procedimento de offboarding que devolve e apaga o TPS do tenant.

Ponto de tensão a decidir explicitamente, não por omissão: um seller
concorrente **não-cliente** pode pedir remoção dos próprios dados? Se ele for
pessoa física, o pedido de oposição ao tratamento é legítimo e precisa de um
fluxo — e o produto perde uma linha do ranking. Melhor projetar isso agora do
que descobrir com a petição na mão.

### 4.3 Planos comerciais derivados do custo marginal real

Os planos precificam o recurso escasso de §2.6 — não "número de features",
porque o custo marginal de uma feature de leitura sobre o lago compartilhado é
aproximadamente zero, enquanto o de uma keyword nova é real e arriscado.

| | **Essencial** (Duzzi, BHP, Futura) | **Profissional** (Polo Ar, Ar Certo, Denteck, Clima Rio) | **Enterprise** (Frigelar, Web Continental, Dufrio) |
|---|---|---|---|
| Keywords próprias | até ~50 | até ~250 | negociado |
| Plataformas | 3 à escolha | todas as ativas | todas + loja própria (1P) |
| Cadência | 3 turnos | 3 turnos + watchlist 25 SKUs/h | 3 turnos + watchlist 100 SKUs/h |
| Histórico | 6 meses | 24 meses | completo |
| Identidades reivindicadas | 1 | até 3 | ilimitadas (1P + 3P) |
| API / webhook | leitura, limite baixo | leitura + escrita, ERP | dedicado, SLA |
| Alertas | e-mail/push | + webhook e regras próprias | + integração e canal dedicado |
| Onboarding de coleta nova | não incluso | 1 fonte inclusa | inclusa |
| Benchmark contribuído | — | — | sob k-anonimato, opt-in explícito |

Dois princípios comerciais:

- **O piso do Essencial existe para o dado, não para a caridade.** Cada tenant
  pequeno adiciona keywords e identidades ao mapa; a densidade da cobertura é
  o ativo. Preço de entrada baixo é estratégia de dado, e deve ser dito assim
  internamente para não virar desconto sem critério.
- **Onboarding é custo variável.** Livaar, Duzzi, BHP e Futura não existem na
  coleta (§0.1c). Cada um é trabalho de scraper e de verificação de identidade.
  Cobrar setup, ou incluí-lo só a partir do Profissional, é o que impede o
  plano barato de dar prejuízo.

### 4.4 O modelo co-op — oportunidade real, com trava obrigatória

A indústria (Midea Carrier) já financia o tracker do lado industrial e já opera
verba de co-op com esses mesmos dealers. Subsidiar assentos de dealer com verba
de co-op é o caminho de menor atrito para chegar a dezenas de tenants sem
vender um a um.

**A trava, técnica e contratual, é inegociável:** quem paga o assento **não
ganha acesso ao plano privado do dealer**. A marca continua vendo o que sempre
viu — a superfície pública. Custo, estoque, margem e alertas do dealer ficam no
TPS, e o `metric_registry` reprova qualquer endpoint que exponha
`derivado_misto` fora do tenant dono. Sem essa separação, o dealer não sobe
custo nenhum — e sem custo o produto vira um painel de preço a mais.

---

## 5. Roadmap por fases

**Fase 0 — destravar (pré-requisito, não é produto).**
Reimport do PriceTrack com `price_basis` correto; portão de heartbeat
especificado; `bestsellers` registrado no `pipeline_registry.py` (hoje coleta
sem job, logo sem batida de ponto — e por isso 7 fontes estão mudas sem ninguém
cobrar); Casas Bahia destravada no Mais Vendidos. Sem a fase 0 o produto vende
número errado.

**Fase 1 — o fato com sujeito seller.**
`seller_offer_daily`, `tenants`/`tenant_seller_claim`, RLS, `metric_registry`
com teste no CI. Entregável verificável: win rate de buy box e churn de oferta
para **um** tenant piloto, contra a tela do marketplace.

**Fase 2 — plano do tenant e alertas classe A.**
TPS, upload manual de custo (antes do ERP), alertas com EWMA e portão de
heartbeat, front branded. Piloto pago com 2–3 dealers de perfis diferentes
(um 1P forte, um 3P puro, um pequeno).

**Fase 3 — integração e cadência.**
Adapters de ERP (Bling/Tiny), watchlist horária, API pública versionada,
webhooks idempotentes.

**Fase 4 — datasets de referência.**
Comissão/frete por marketplace, MAP por SKU, decomposição de perda de buy box.
Benchmark contribuído **apenas** aqui, com parecer jurídico e opt-in.

Escolha do piloto: um 1P com loja própria já coberta (Dufrio ou Frigelar), um
3P puro de porte médio (Polo Ar ou Ar Certo) e um pequeno **sem cobertura
atual** (Duzzi), porque só o terceiro mede o custo real de onboarding — que é
o número que decide se o plano Essencial fecha a conta.

---

## 6. Bloqueadores conhecidos

1. **`price_basis` — 36 dias / ~1,0 M linhas na base antiga.** Onde há PIX o
   preço está ~10% alto, e isso contamina buy box, moda, mediana e a análise de
   MAP. Vender gap de preço a seller antes do reimport é vender erro conhecido.
   *Dono: PC coletor. É o item nº 1 da fase 0.*
2. **Buy box da Amazon só existe no PDP.** Na SERP o campo vem vazio de
   propósito (para não inventar vitória 1P). Win rate de Amazon com o dado atual
   é parcial — ou se paga o custo de PDP por item, ou se declara a limitação.
3. **Google Shopping é frágil** (reCAPTCHA, dependente do Chrome logado). É a
   superfície mais importante para o 1P e a menos confiável. Não entra em SLA
   sem redundância.
4. **Cadência de 3 turnos** é o teto do que se pode prometer em alerta (§2.8).
5. **O sinal de resultado existe, mas está fora do livro-razão — e vaza.**
   `bestsellers` coleta e grava todo dia (20 dos últimos 21 dias em 04/09; o
   único buraco, domingo 30/08, não é padrão de fim de semana — domingo 23/08
   veio cheio). O problema é que ela **não tem job no `pipeline_registry.py`**:
   sem job não há batida de ponto, e sem batida ninguém cobra quem some. E some
   — **7 das 20 fontes de `bestsellers/config.py` nunca gravaram uma linha em
   toda a história da tabela**: `casasbahia`, `frigelar`, `arcerto`, `dufrio`,
   `centralar`, `leveros`, `ferreiracosta`. Das 5 fontes de `relevancia`, só
   `engage` responde.
   **Só uma dessas ausências é prioridade: `casasbahia`** — é marketplace, e é
   onde a disputa por vitrine de fato acontece. As outras 6 são site próprio,
   onde o lojista joga sozinho: ranking ali mede a decisão dele, não a
   competição (§3.1). Decisão do mantenedor, Set/2026.
   Continua valendo o limite de leitura: ranking é **ordinal**, mede direção de
   resultado, nunca quantidade — o produto mede **exposição e competição**, e o
   Mais Vendidos é a única pista de **resultado**, não a medida dele.
6. **Ponto único de falha físico.** Toda a coleta depende de um PC Windows
   com IP residencial. Como produto industrial-interno isso é um risco
   aceito; como SaaS com SLA para terceiros, é o risco estrutural do negócio,
   e precisa de plano (segundo coletor residencial, proxy residencial BR) antes
   do primeiro contrato com cláusula de disponibilidade.

---

*Documento de proposta — nenhuma migração aplicada, nenhum código de produção alterado.*
