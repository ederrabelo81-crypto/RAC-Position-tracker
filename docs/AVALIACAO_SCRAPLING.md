# Avaliação do Scrapling para a coleta (Set/2026)

> Repositório avaliado: [D4Vinci/Scrapling](https://github.com/D4Vinci/Scrapling)
> v0.4.15, licença BSD-3, Python ≥ 3.10. Leitura do código em 30/09/2026.
> **Decisão:** não entra como dependência; quatro ideias entram no nosso código.

## 1. O que é

Três camadas independentes:

| Camada | O que faz | Equivalente no nosso repositório |
|---|---|---|
| `Fetcher` / `FetcherSession` | HTTP com `curl_cffi` (impersonate de TLS) + headers gerados pelo `browserforge` | `curl_cffi` direto em Magalu/Casas Bahia/Shopee |
| `DynamicFetcher` / `StealthyFetcher` (+ `*Session`) | Playwright/Patchright com **pool de abas** (`max_pages`), `disable_resources`, `blocked_domains`/`block_ads`, `capture_xhr`, `cdp_url`, `solve_cloudflare` | `BaseScraper` + `LocalBrowser` + `PlaywrightRuntime` |
| `Selector` (parser) | lxml, CSS/XPath, busca por texto/regex, **seletor adaptativo** (`auto_save`/`adaptive`: guarda a impressão do elemento em SQLite e o reencontra depois de um redesign) | BeautifulSoup + cadeias de fallback de seletores |
| `spiders/` | Framework no estilo Scrapy: concorrência, **AutoThrottle**, detecção de bloqueio com retry, checkpoint e cache de respostas em modo dev | Loop sequencial por keyword em `main.py` |

## 2. Por que não entra como dependência

1. **Conflito de runtime do Playwright.** Os fetchers de browser do Scrapling
   abrem o próprio `sync_playwright()`. O projeto exige **um handle só por
   thread** (`scrapers/playwright_runtime.acquire()`, COMMON_MISTAKES #21), e um
   segundo handle é exatamente o erro `Sync API inside the asyncio loop`.
2. **Versões.** O Scrapling pede `playwright>=1.62`, `patchright>=1.62` e
   `curl_cffi>=0.16`. O Magalu depende de `rebrowser-playwright` (patch do
   `Runtime.enable`) e o `requirements.txt` fixa `curl-cffi>=0.6`. Trocar o motor de
   browser de todas as plataformas para ganhar funções que cabem em poucas
   linhas não compensa.
3. **O anti-bot é outro.** O `StealthyFetcher` foi calibrado para Cloudflare
   Turnstile. Nossos bloqueios são Akamai (Magalu, Casas Bahia, Leroy), a
   PerimeterX da Fast Shop e o captcha da Amazon, e a causa raiz é o **IP de
   datacenter**, não o fingerprint (ver `docs/learnings/anti-bot-strategies.md`).
4. **O seletor adaptativo resolve um problema que já contornamos.** As fontes
   principais são JSON (Algolia, VTEX IS, `_next/data`, API v4). E um seletor
   que se "recupera" sozinho depois de um redesign pode devolver outro elemento
   com a mesma cara: um dado plausível e errado, o modo de falha que o projeto
   mais teme.

## 3. O que vale adaptar (sem a dependência)

| # | Ideia do Scrapling | Onde aplicar | Ganho esperado | Custo |
|---|---|---|---|---|
| A | `disable_resources`: aborta imagem, mídia, fonte e CSS via `page.route` | PDP da Amazon (`scrapers/amazon.py::_fetch_pdp_seller`, `bestsellers/sources/amazon.py`) e PDP da Leroy | Menos banda e `domcontentloaded` mais rápido. O "Vendido por" está no HTML servido e não depende de CSS nem de imagem | Baixo: ~15 linhas, ligado só durante a passada de PDP |
| B | Pool de abas (`max_pages`) + **AutoThrottle** (o intervalo sobe quando aparece captcha/429 e desce quando para) | Passada de PDP da Amazon no Actions (`RAC_AMAZON_PDP_FRESH`) | Duas ou três abas em paralelo cortam o tempo de parede da passada. O throttle adaptativo troca o `random(2–5s)` fixo | Médio: exige async ou abas intercaladas no mesmo handle. Validar a taxa de captcha antes de subir o paralelismo |
| C | HTTP primeiro, browser como fallback (`Fetcher` → `DynamicFetcher`) | PDP da Amazon: `curl_cffi` com `impersonate="chrome124"` **reaproveitando os cookies do contexto do browser** | Um GET de HTML custa uma fração de um `goto` completo. A Leroy já faz isso (`_fetch_pdp_requests`) | Médio: a Amazon desafia mais o HTTP puro vindo de IP de datacenter. Precisa de A/B no Actions |
| D | `capture_xhr`: guardar as respostas de API que a página dispara | PDP da Leroy: capturar o JSON de ofertas/preço que o PDP chama, em vez de ler o texto "Vendido e entregue por" | Buy box e preço **por produto** direto da fonte, sem regex sobre o texto | Baixo: já existe `_setup_xhr_intercept` na busca |

### Amazon: o endpoint AOD (qualidade, não só velocidade)

A buy box só existe no PDP, mas o painel "Outras ofertas" tem um endpoint
próprio, bem mais leve que o PDP:
`https://www.amazon.com.br/gp/product/ajax/aodAjaxMain/?asin=<ASIN>&pc=dp`.
Ele devolve a **oferta fixada (buy box)** e **todas as outras ofertas** com
seller, preço e condição. Isso daria um `Qtd Sellers` real e a lista de quem
disputa a buy box, em vez de só o vencedor. Precisa ser validado no Actions
(cookies e taxa de captcha) antes de substituir o PDP. Não foi possível testar
a partir do ambiente desta análise, que bloqueia a saída de rede para a Amazon.

## 4. Leroy Merlin: o que o print mostrou (corrigido neste PR)

Caso: item 3962339062 (Elgin Eco Inverter III 12K). No PDP aparece "Vendido e
entregue por LEROY MERLIN". Na coleta aparece `3P (não identificado)`.

Conferido no Supabase (29/09/2026):

- ~**34%** das linhas da Leroy (~1,1 mil/dia) saem como `3P (não
  identificado)`, e isso vem de apenas **12 a 14 seller IDs**.
- O item do print tem `5be5eb765cb50968730358f5` como **seller único** no
  índice (`qtd_sellers = 1`). Esse ID aparece em 25 produtos de 7 marcas. É a
  **própria Leroy como seller do marketplace**.
- O resolver descartava a resposta "Leroy Merlin" do PDP ("3P não pode ser a
  Leroy"), julgava a falha definitiva e punha o ID em quarentena por 7 dias.
  Resultado: ~200 linhas/dia de 1P gravadas como 3P, inflando o 3P no share.
- O seller `5f61118b7dc9a636d40113a2` estava gravado como **"Saiba mais"**
  (texto de link da tela), em 39 linhas/dia.

**Correção:**

1. O ID da Leroy entra no mapa estático e é classificado como **1P**.
2. Quando o ID é o **único** seller do produto e o PDP diz "Leroy Merlin", o ID
   é identificado como a Leroy (1P) e vai para o cache. Com dois ou mais
   sellers, a resposta continua sendo tratada como ambígua.
3. Na passada de PDP, prefere-se a URL de um produto em que o ID é seller único.
4. `clean_seller_name` rejeita texto de interface ("Saiba mais", "Conheça…").
   O cache em disco é revalidado ao carregar, então o nome inválido sai e o ID
   volta a ser resolvido.

**Pendências (fora deste PR):**

- **`5d449157e6c3b40792552a05`**: ~620 linhas/dia, 47 produtos, 10 marcas,
  quase sempre seller único. O padrão é o mesmo do ID da Leroy. Depois do
  deploy, rode `python scripts/leroy_seller_probe.py --clear-quarantine` no PC
  coletor para a próxima coleta tentar o PDP de novo.
- **Preço:** a coleta lê `averagePromotionalPrice`, uma **média** do índice.
  No item do print ficou em R$ 2.290,53 nos quatro turnos, contra R$ 2.339,00
  à vista no PDP. O preço da buy box precisa vir do PDP (ideia D).
- **Buy box com 2+ sellers:** hoje se assume que o primeiro item de
  `marketplaceSellers` é o vencedor. Isso nunca foi validado contra o PDP.
