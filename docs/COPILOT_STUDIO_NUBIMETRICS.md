# Agente "Sell-out ML — Nubimetrics" no Microsoft Copilot Studio

> Derivado do que já está mapeado e validado neste repositório:
> `nubimetrics/catalog/MAPPING.md` (24 tools reais, servidor
> `nubimetrics-mcp 20261029-c5c7e44`, testadas em 13/11/2026 na conta Midea
> Brasil), `nubimetrics/README.md` (decisões de Nov/2026) e `.mcp.json`
> (conexão usada pelo Claude Code). Nada aqui é suposição sobre a API.

Conteúdo:

1. [Prompt (instruções do agente)](#1-prompt--colar-em-instruções-do-agente) — colar no campo **Instruções**
2. [Descrição curta e prompts iniciais](#2-descrição-curta-e-prompts-iniciais)
3. [Guia passo a passo da conexão](#3-guia-passo-a-passo--conexão-com-o-mcp-da-nubimetrics) — Opção A (MCP nativo) e Opção B (conector personalizado)
4. [Teste de aceitação](#4-teste-de-aceitação)
5. [Problemas comuns](#5-problemas-comuns)

---

## 1. Prompt — colar em "Instruções" do agente

> O campo de instruções do Copilot Studio aceita até **8.000 caracteres**. O
> bloco abaixo tem ~5.800, então cabe inteiro. Copie só o que está entre as
> linhas de código.

```text
# PAPEL
Você é o analista de sell-out de Ar Condicionado da Midea Carrier no Mercado Livre Brasil. Responde em português do Brasil, de forma objetiva, usando SOMENTE dados das ferramentas Nubimetrics (MCP). Nunca invente número, marca, seller, mês ou nome de ferramenta.

# ESCOPO FIXO
- Site: MLB (Brasil). Moeda: BRL ("local"); use "usd" só se pedirem comparação sem inflação.
- Categoria padrão: Ar Condicionado = MLB1646 (filha de "Ar e Ventilação" MLB252358). Se o usuário não citar categoria, use MLB1646. Para outra categoria, descubra o id com meli_category_explorer — nunca adivinhe.
- Conta: Midea Brasil, plano b2b com 1 slot de concorrência. A conta não vende no ML: nubi_evaluate_categories volta vazio — não conclua que "a Midea não vendeu".

# QUAL FERRAMENTA USAR
- Tamanho/evolução do mercado (R$, unidades, ticket, YoY, 13 meses): nubi_category_market_size.
- Nº de sellers e anúncios ativos: nubi_category_market_structure (não traz visitas).
- Saúde da categoria (saturação, oportunidade, preço médio, mês de pico, % frete grátis/Mercado Envios/catálogo/desconto — sem % Full; Full por anúncio sai de meli_publication_explorer): meli_category_detail. Saturação só existe por CATEGORIA, nunca por marca.
- Ranking de marcas, sellers, produtos, catálogo ou lojas oficiais: meli_market_rankings (topic = brands | seller | items | catalog | official_store; ranking_type = gmv para receita, si para unidades; limit=100).
- O que se busca: meli_search_trends_rankings (posição e tendência; NÃO tem volume de busca — nunca diga "X buscas").
- Previsão: nubi_category_forecast (12 meses à frente + acumulado do ano).
- Anúncios, preço real, Full, catálogo, exposição: meli_publication_explorer com title="ar condicionado", category_id="MLB1646", sort_by="units" ou "gmv" (sem sort_by a ordem é relevância, não venda), page_size=50.
- Concorrente acompanhado (visitas, conversão, série diária): nubi_competition_seller_groups → nubi_competition_seller_group_members → nubi_competition_seller_metrics / _evolution / _items. Passe group_id (exatamente como veio) só em _group_members; nas três seguintes use o member_code do membro como seller_id_competitor e ignore membros com is_self=true.
- Grupos de anúncios seguidos: nubi_competition_publication_groups → _group_members → _detail / _evolution / _log.
- Conta e categorias liberadas: nubi_user_info, nubi_seller_info.
- PROIBIDO: nubi_user_feedback (grava do lado da Nubimetrics). Se notar dado estranho, avise o usuário em vez de chamar.

# REGRAS DE DADO (obrigatórias)
1. Mês fechado. Rankings, buscas e detalhe de categoria só existem para mês FECHADO. Passe date = primeiro dia do mês (AAAA-MM-01). Se a resposta vier com period_fallback=true, o dado é do mês ANTERIOR: diga isso explicitamente e rotule pelo mês real (period_applied), nunca pelo mês pedido. market_size é a exceção: inclui o mês corrente PARCIAL — sinalize "parcial".
2. Midea = soma das grafias. No ranking de marcas a Midea vem fatiada (MIDEA, SPRINGER MIDEA, MIDEA BRANCA, "CARRIER | MIDEA"). Ao falar de share da Midea, SOME todas as linhas que contêm "MIDEA" e mostre a quebra. Carrier sozinha e Springer sozinha seguem como marcas próprias. Ex.: out/2026 = 17,6% somado vs 13,3% só a linha MIDEA.
3. Top 100. Rankings e buscas têm no máximo 100 posições. Marca/seller fora do top 100 = "fora do top 100", não "zero".
4. Ranking é ORDINAL dentro da sua dimensão. Liderar ranking de marcas não é "dominar o mercado"; nunca some posições entre rankings ou plataformas. Share de mercado vem do campo share ou de gmv da marca ÷ market_size do mesmo mês.
5. Números arredondados. Nas tools de concorrência gmv/units vêm como texto ("3M", "1.3K"): reporte como aproximado (~). cum_units do explorador é arredondado em centenas — não use em conta.
6. Identificadores. item_id, seller_id_hash e member_code são hashes internos, NÃO são MLB nem id de seller do ML. Nunca os apresente como código do anúncio.
7. Títulos truncados. topic=items traz título cortado em 40 caracteres; para nome completo use topic=catalog.
8. Segmento High Wall Inverter e BTU NÃO são atributos da API: infira primeiro do título (ex.: "12000", "12.000 BTUs", "inverter", "inv"); se o título truncado não trouxer o BTU, use o campo model (ex.: 38EZVCC12M5 → 12.000). Informe a fonte (título ou modelo) e quantos ficaram sem classificação. Exclua janela, portátil, piso-teto, cassete, multi-split e condensadora avulsa de "High Wall".
9. Visitas e conversão por anúncio NÃO existem no explorador; só para o concorrente acompanhado (1 slot). Não estime.
10. Nomes mascarados (ex.: LOJA.OFICIAL.12, castor.magenta.abstrato) = "não identificado"; não tente adivinhar quem é.
11. Estimativas. Todo dado é ESTIMATIVA da Nubimetrics, não venda auditada. Mencione isso no rodapé.
12. Comparações (MoM, YoY, YTD) só com períodos completos e equivalentes. YTD = soma dos meses fechados do ano vs os MESMOS meses do ano anterior; se faltar mês, não calcule o delta e explique.
13. Não ratear mensal em diário. Série diária só existe para concorrente/publicação seguidos.

# COMO RESPONDER
- Comece pela resposta (1–2 frases com o número principal), depois tabela curta e, por fim, "Leitura" com 2–4 bullets acionáveis para trade/e-commerce (seller, BTU, preço, Full/catálogo, busca).
- Formato: R$ com milhar em ponto e decimal em vírgula (R$ 29,7 mi); share com 1 casa (17,6%); deltas em p.p. para share e % para receita.
- Sempre informe: período usado (e se houve fallback), categoria, ferramenta(s) consultada(s) e limitações relevantes.
- Benchmark padrão da Midea: LG (líder em out/2026). Ao falar de share, mostre a distância para a LG.
- Se a ferramenta falhar ou voltar vazia: diga qual falhou e por quê (permissão de categoria, mês sem dado, grupo vazio). Nunca preencha com estimativa própria.
- Pergunta fora do alcance da API (atributo de anúncio, código MLB, venda diária por anúncio, relatórios dinâmicos, Reportes IA): diga que o MCP não fornece e sugira o export XLSX do relatório dinâmico na interface da Nubimetrics.
- Nunca peça, mostre ou repita a chave de API.
```

---

## 2. Descrição curta e prompts iniciais

**Nome do agente:** `Sell-out ML — Nubimetrics (Midea RAC)`

**Descrição (campo "Descrição"):**

```text
Responde perguntas sobre vendas estimadas de ar condicionado no Mercado Livre (categoria MLB1646) com dados da Nubimetrics: tamanho do mercado, share de marcas e sellers, buscas, previsão, anúncios e concorrente acompanhado. Foco em Midea Carrier vs concorrência.
```

**Prompts iniciais sugeridos (campo "Prompts sugeridos"):**

| Título | Prompt |
|---|---|
| Share do mês | Qual foi o share da Midea (somando todas as grafias) em ar condicionado no último mês fechado, e a distância para a LG? |
| Top sellers | Quais os 10 maiores sellers de ar condicionado no último mês fechado, por receita, e quanto cada um cresceu? |
| Mercado 13 meses | Mostre a evolução da receita e das unidades da categoria nos últimos 13 meses, com o YoY de cada mês. |
| Buscas | Quais termos de busca em ar condicionado estão subindo no último mês fechado? |
| Anúncios Midea | Liste os 15 anúncios Midea que mais vendem agora, com preço, seller, Full e catálogo. |
| Previsão | Qual a previsão de receita da categoria para os próximos 3 meses? |

---

## 3. Guia passo a passo — conexão com o MCP da Nubimetrics

### Dados da conexão (os mesmos do `.mcp.json` deste repositório)

| Item | Valor |
|---|---|
| Servidor | `https://mcp.nubimetrics.com/mcp` |
| Transporte | **Streamable HTTP** (não é SSE) |
| Autenticação | Cabeçalho `Authorization: Bearer <NUBIMETRICS_API_KEY>` |
| Onde gerar a chave | Nubimetrics → menu **"Ativar o MCP"** (`app.nubimetrics.com/mcp`), conta Midea Brasil |
| Ferramentas | 24 (lista na §3.4) |

> **Segurança:** a chave é a mesma do `.env` / secret `NUBIMETRICS_API_KEY`
> do Actions. Recomendo **gerar uma chave separada** para o Copilot Studio —
> se ela vazar ou precisar ser revogada, o ETL diário (`nubimetrics_daily.yml`)
> não para. Nunca cole a chave no prompt, em tópicos, em mensagens ou em
> arquivos de conhecimento.

### Pré-requisitos

- Licença do Copilot Studio e acesso a um **ambiente** do Power Platform onde
  você possa criar agentes e conectores (ambiente com **orquestração
  generativa** — as ferramentas MCP só funcionam nesse modo).
- Política de DLP do ambiente permitindo conectores personalizados /
  servidores MCP (se não permitir, peça ao admin do Power Platform para
  classificar o conector como "Business").
- A chave da Nubimetrics em mãos.

---

### Opção A — Servidor MCP nativo (recomendada, mais simples)

O Copilot Studio tem assistente próprio para MCP; ele cria por baixo um
conector personalizado com o protocolo `mcp-streamable-1.0`.

1. **Criar o agente**
   1. Acesse `copilotstudio.microsoft.com` e confira, no canto superior
      direito, se está no ambiente certo.
   2. **Agentes → + Criar → Novo agente** → aba **Configurar** (pule a
      conversa de criação).
   3. Preencha **Nome** e **Descrição** (§2) e cole o prompt da §1 em
      **Instruções**.
   4. Em **Configurações → IA generativa / Orquestração**, deixe
      **Orquestração generativa = Ativada**.
   5. Em **Conhecimento**, desligue "Usar conhecimento geral / pesquisa na
      Web" — o agente deve responder só com a Nubimetrics. Clique em **Criar**.

2. **Adicionar o servidor MCP**
   1. No agente, abra a aba **Ferramentas** → **+ Adicionar uma ferramenta**
      → **+ Nova ferramenta** → **Model Context Protocol**.
   2. Preencha o assistente:
      - **Nome do servidor:** `Nubimetrics`
      - **Descrição do servidor:** `Dados de mercado do Mercado Livre (vendas estimadas, rankings, buscas, previsão e concorrência) da conta Midea Brasil na Nubimetrics.`
      - **URL do servidor:** `https://mcp.nubimetrics.com/mcp`
   3. **Autenticação:** escolha **Chave de API**:
      - **Tipo:** `Cabeçalho` (Header)
      - **Nome do cabeçalho:** `Authorization`
   4. Clique em **Criar**. O Copilot Studio gera o conector.

3. **Criar a conexão (onde a chave entra)**
   1. Na tela seguinte, em **Conexão**, abra a lista → **Criar nova conexão**.
   2. No campo da chave, digite **`Bearer ` + a chave** (com a palavra
      `Bearer`, um espaço e o token). O Copilot Studio envia o valor
      literalmente no cabeçalho, e a Nubimetrics exige o prefixo `Bearer`.
   3. **Criar** → **Adicionar ao agente** (ou "Adicionar e configurar").

4. **Revisar as ferramentas**
   1. Abra a ferramenta **Nubimetrics** na aba **Ferramentas**. O Copilot
      Studio lista as 24 tools descobertas no servidor.
   2. **Desligue `nubi_user_feedback`** (ela grava feedback do lado da
      Nubimetrics — regra dura do projeto).
   3. Opcional: desligue as tools de grupo de publicações
      (`nubi_competition_publication_*`) enquanto o grupo "Publicaciones
      Favoritas" estiver vazio — evita chamadas inúteis.
   4. Em **Credenciais a usar**, escolha **Credenciais do autor (maker)** para
      que todos os usuários usem a sua conexão. Com "credenciais do usuário",
      cada pessoa precisaria da própria chave Nubimetrics.

5. **Testar** no painel **Testar** (ver §4) e **Publicar** (canto superior
   direito). Em **Canais**, habilite **Teams e Microsoft 365 Copilot** se
   quiser usar no Teams.

---

### Opção B — Conector personalizado (Power Apps) com o protocolo MCP

Use quando o assistente da Opção A não estiver disponível no seu tenant, ou se
o admin exigir que todo conector passe pelo Power Apps / ALM (soluções).

1. **Criar o conector**
   1. Acesse `make.powerapps.com` (mesmo ambiente do agente) → **Mais →
      Descobrir tudo → Conectores personalizados** (ou **Soluções** → sua
      solução → **+ Novo → Automação → Conector personalizado**, se usar ALM).
   2. **+ Novo conector personalizado → Importar do GitHub** → Tipo de
      conector **Custom**, Branch **dev**, Conector **MCP-Streamable-HTTP**
      → **Continuar**. Esse modelo vem do repositório oficial
      `microsoft/PowerPlatformConnectors` (pasta `custom-connectors/MCP-Streamable-HTTP`).
      *(Alternativas: salvar o YAML do passo 4 como `nubimetrics-mcp.yaml` e
      usar **Importar um arquivo OpenAPI**; ou **Criar do zero**, ativar
      **Editor de Swagger** e colar o YAML.)*

2. **Geral**
   - **Nome:** `Nubimetrics MCP`
   - **Host:** `mcp.nubimetrics.com`
   - **URL base:** `/`
   - Esquema **HTTPS**.

3. **Segurança**
   - **Tipo de autenticação:** `Chave de API`
   - **Rótulo do parâmetro:** `Chave Nubimetrics (Bearer ...)`
   - **Nome do parâmetro:** `Authorization`
   - **Local do parâmetro:** `Cabeçalho`

4. **Definição** — confirme (ou cole no Editor de Swagger):

   ```yaml
   swagger: '2.0'
   info:
     title: Nubimetrics MCP
     description: Servidor MCP da Nubimetrics (dados de mercado do Mercado Livre).
     version: 1.0.0
   host: mcp.nubimetrics.com
   basePath: /
   schemes:
     - https
   consumes:
     - application/json
   produces:
     - application/json
   paths:
     /mcp:
       post:
         summary: Nubimetrics MCP Server
         description: Ferramentas Nubimetrics para vendas estimadas, rankings, buscas, previsão e concorrência no Mercado Livre.
         x-ms-agentic-protocol: mcp-streamable-1.0
         operationId: InvokeNubimetricsMCP
         responses:
           '200':
             description: Success
   securityDefinitions:
     api_key:
       type: apiKey
       in: header
       name: Authorization
   security:
     - api_key: []
   ```

   O ponto essencial é `x-ms-agentic-protocol: mcp-streamable-1.0` na
   operação `POST /mcp` — é ele que faz o Copilot Studio tratar o conector
   como servidor MCP e descobrir as tools sozinho.

5. **Criar conector** → aba **Testar** → **+ Nova conexão** → no campo da
   chave digite **`Bearer <sua chave>`** → **Criar conexão**.

6. **Ligar ao agente**
   1. No Copilot Studio, abra o agente → **Ferramentas → + Adicionar uma
      ferramenta**.
   2. Filtre por **Model Context Protocol** (ou pesquise "Nubimetrics MCP") →
      selecione o conector → escolha a conexão criada → **Adicionar ao agente**.
   3. Siga os passos 4 e 5 da Opção A (desligar `nubi_user_feedback`,
      credenciais do autor, testar, publicar).

---

### 3.4 Ferramentas que devem aparecer (24)

| Família | Tools | Uso no agente |
|---|---|---|
| Mercado (público) | `meli_category_explorer`, `meli_category_detail`, `meli_market_rankings`, `meli_publication_explorer`, `meli_search_trends_rankings` | ✅ |
| Categoria | `nubi_category_market_size`, `nubi_category_market_structure`, `nubi_category_forecast` | ✅ |
| Conta | `nubi_user_info`, `nubi_seller_info`, `nubi_evaluate_categories` | ✅ (a última volta vazia para a Midea Brasil) |
| Concorrente (sellers) | `nubi_competition_seller_groups`, `_group_members`, `_group_metrics`, `_metrics`, `_evolution`, `_items` | ✅ 1 slot |
| Concorrente (anúncios) | `nubi_competition_publication_groups`, `_group_members`, `_group_metrics`, `_detail`, `_evolution`, `_log` | ⚠️ grupo vazio hoje |
| Feedback | `nubi_user_feedback` | 🚫 **desligar** |

Se aparecerem tools diferentes, o servidor mudou: rode
`python -m nubimetrics.discover` no repositório e revise o
`nubimetrics/catalog/MAPPING.md` antes de mexer no prompt.

### O que o MCP NÃO entrega (não adianta pedir ao agente)

- Relatórios dinâmicos por publicação × dia e atributos do anúncio (BTU,
  inverter, voltagem) — só pelo export XLSX da interface.
- Código MLB do anúncio (o `item_id` é hash), visitas/conversão por anúncio,
  saturação ou ranking de demanda por marca, Reportes Estratégicos IA, análise
  de lojas oficiais com nome.

---

## 4. Teste de aceitação

Rode no painel **Testar** antes de publicar. Os valores de referência são os
validados no repositório (out/2026).

| # | Pergunta | Esperado |
|---|---|---|
| 1 | "Quem sou eu na Nubimetrics e qual meu plano?" | Chama `nubi_user_info`/`nubi_seller_info`; plano BUSINESS CORPORATE FULL, b2b, 1 slot |
| 2 | "Qual o tamanho do mercado de ar condicionado em outubro de 2026?" | ~R$ 223,5 mi (`nubi_category_market_size`) |
| 3 | "Share das marcas em outubro de 2026" | LG ~R$ 44,6 mi / 19,96%; Midea **somada** ~17,6% (MIDEA sozinha 13,3%) |
| 4 | "Ranking de marcas de novembro de 2026" (mês corrente) | Avisa `period_fallback` e rotula como **outubro** |
| 5 | "Quantas buscas teve 'ar condicionado inverter'?" | Diz que a ferramenta não traz volume de busca |
| 6 | "Qual o código MLB do anúncio mais vendido?" | Diz que o MCP só entrega hash, não MLB |

Se o teste 3 vier com a Midea em 13,3% sem somar as grafias, o agente não está
seguindo a regra 2 do prompt — reforce-a ou mova-a para o topo das instruções.

---

## 5. Problemas comuns

| Sintoma | Causa provável | Solução |
|---|---|---|
| `401 Unauthorized` ao criar a conexão ou testar | Faltou `Bearer ` antes da chave, ou chave revogada | Edite a conexão (Power Apps → **Conexões**) e digite `Bearer <chave>`; gere nova chave em `app.nubimetrics.com/mcp` se necessário |
| Ferramentas não aparecem após criar | Orquestração generativa desligada | Configurações do agente → ative **Orquestração generativa** |
| "Server not found" / erro de transporte | URL sem `/mcp` ou conector com SSE | Use exatamente `https://mcp.nubimetrics.com/mcp` e `mcp-streamable-1.0` |
| Conector bloqueado | Política de DLP do ambiente | Admin do Power Platform → Políticas de dados → mover o conector para "Business" |
| Erro "category not allowed" | Categoria fora das liberadas (b2b não aceita pai de categoria liberada) | Use `MLB1646` ou outra categoria abaixo de `MLB252358`; confira com `nubi_seller_info` |
| Respostas com números "redondos" ("3M") | Tools de concorrência devolvem texto arredondado | Comportamento da API — o agente deve marcar como aproximado |
| Erro 429 / lentidão | Limite de chamadas da Nubimetrics | Pedir menos páginas por pergunta (`page_size=50`, poucas páginas); evitar perguntas que varram os 4 mil anúncios |
| Usuário do Teams pede login/conexão | Ferramenta configurada com credenciais do usuário | Ferramentas → Nubimetrics → **Credenciais do autor** e republicar |

> Os nomes de menus do Copilot Studio mudam com frequência; se algum rótulo
> acima não bater, procure pelo equivalente ("Tools/Ferramentas", "Add a
> tool/Adicionar ferramenta", "Model Context Protocol"). A documentação oficial
> é "Connect your agent to an existing MCP server" em `learn.microsoft.com`.
