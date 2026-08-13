# Plataformas: ordenação canônica, mecânica e armadilhas

Registro auditado em 08 e 10 de agosto de 2026, atualizado com a automação de
agosto. A fonte de verdade do registro é `bestsellers/config.py` — este
documento explica o PORQUÊ de cada entrada; o código carrega os valores.

---

## Amazon
- **URL:** `https://www.amazon.com.br/gp/bestsellers/home/17125373011/`
- **Parâmetro canônico:** `gp/bestsellers`
- **Mecânica:** ranking de velocidade de vendas, recalculado de hora em hora.
  É o melhor sinal de demanda do conjunto — e a razão de a rotina exigir
  horário fixo.
- **Traz:** posição explícita, título, preço, avaliações, rating.
- **Não traz:** seller, unidades.
- **Coleta:** browser (Playwright com stealth). 30 itens por página; a segunda
  página traz de #31 a #50 e sai com `--paginas 2`.
- **Armadilhas:**
  - O nó 17125373011 fica dentro do departamento Casa. A lista mistura split,
    janela e portátil. O KPI filtra por `tipo = SPLIT_HW`.
  - A Amazon reescreve as classes CSS com hash a cada deploy; o parser tenta
    três cadeias de seletores e cai no `alt` da imagem antes de desistir.
  - CAPTCHA é esperado em IP de datacenter. Rodar local com `--no-headless`.

## Mercado Livre
- **URL:** `https://www.mercadolivre.com.br/mais-vendidos/MLB1646`
- **Parâmetro canônico:** `mais-vendidos/MLB1646`
- **Mecânica:** ranking orgânico da categoria, 20 posições.
- **Traz:** seller — é a única lista do conjunto que expõe a leitura de Buy Box
  diretamente. Também rating, preço pix e de lista, faixa de vendidos.
- **Coleta:** browser, reaproveitando a detecção de login gate do scraper de
  keyword. Gate é o modo de falha nº 1 em IP de datacenter.
- **Armadilhas:**
  - `+5mil vendidos` é **acumulado vitalício do anúncio**, não velocidade. Um
    anúncio antigo com +50mil pode estar na posição 9 e vender pouco hoje.
    Nunca somar com o campo mensal da Shopee.
  - Inclui portátil.
  - Sellers observados no top 20: Centralar.com, Webcontinental, Dufrio,
    Comprebel, ClimaRio, e lojas oficiais LG, Samsung, Britânia.

## Magazine Luiza
- **URL:** `/busca/ar+condicionado/?sortType=soldQuantity&sortOrientation=desc`
- **Parâmetro canônico:** `sortType=soldQuantity`
- **Mecânica:** quantidade **acumulada** vendida no anúncio, ordem decrescente.
  Não é velocidade.
- **Coleta:** browser persistente + curl_cffi do scraper de Magalu (Akamai),
  com os três parsers sobre o mesmo HTML: `__NEXT_DATA__`, RSC do App Router e
  cards do DOM.
- **Armadilhas:**
  - Viés estrutural para anúncio velho. Um SKU novo excelente demora a subir.
  - É busca, não categoria: sensível a mudança do algoritmo de busca.
  - Hidratação lenta do lado do cliente pode parecer lista vazia; o coletor
    espera a marcação de produto antes de ler o HTML.

## Shopee
- **URL:** `https://shopee.com.br/search?keyword=ar%20condicionado&sortBy=sales`
- **Parâmetro canônico:** `sortBy=sales` (na API, `by=sales`)
- **Mecânica:** ordenação por vendas. **Único varejista que declara unidades
  por mês por anúncio.**
- **Coleta:** com `RAC_LOCAL_CHROME=1`, dentro do Chrome logado, interceptando
  a chamada nativa da API v4 (que carrega o header anti-fraude). Sem ele,
  replay via curl_cffi com a sessão capturada.
- **Armadilhas:**
  - **Sem a ordenação por vendas a lista é RELEVÂNCIA**, mistura de anúncio
    patrocinado e engajamento. Erro cometido em 08/08/2026: pela relevância a
    Midea aparecia com 17 de 55 posições e melhor rank #4; pela ordenação de
    vendas caiu para 1 de 35 e rank #14. São dois universos, não comparáveis.
  - O campo numérico de vendas do payload é acumulado; só o texto de exibição
    carrega o "/Mês". O coletor lê o texto primeiro por causa disso.
  - `shop_location` é a CIDADE de origem do envio, não o vendedor.
  - Fora do painel PriceTrack: não há cruzamento de prateleira para esta
    plataforma.
  - Best-effort sem proxy residencial BR; a sessão expira em horas.

## Leroy Merlin
- **Índice Algolia:** `production_products_most_sales`
- **Parâmetro canônico:** `production_products_most_sales`
- **Mecânica:** declarada como mais vendidos. **Comportamento estatístico não
  confirma.**
- **Coleta:** API Algolia direta, sem browser.
- **Armadilhas:**
  - Em 48h, 41% dos itens não mudaram de posição e o deslocamento mediano foi
    de 1 posição, contra 12 a 19% parados e 3 a 4 posições nas demais
    plataformas. Ranking de venda real não é tão estável.
  - Hipótese aberta: a ordenação pondera curadoria 1P, margem ou
    disponibilidade. **Pendente de confirmação com o contato do Leroy.** Até
    lá, não usar isoladamente para decisão de corte de verba.
  - O lojista 3P vem como ObjectId opaco, resolvido via PDP com cache
    persistente — 1 PDP por seller novo, não por produto.
  - Traz Daikin e Fujitsu, ausentes do painel PriceTrack.

## Casas Bahia
- **URL do site:** `/ar-condicionado/b?ordenacao=maisvendidos`
- **Parâmetro canônico:** `orders_desc` na API VTEX (a UI envia
  `ordenacao=maisvendidos`); o portão aceita as duas grafias.
- **Mecânica:** mais vendidos da categoria.
- **Coleta:** VTEX Intelligent Search com warm-up de cookies Akamai. A API é
  usada porque o array `sellers[]` expõe o vencedor da buy box diretamente — o
  DOM não traz isso.
- **Armadilhas:**
  - **Contaminação alta.** Em 10/08/2026, 6 de 20 itens eram umidificador,
    depurador de ar e afins — nas posições #3, #4 e #5. Os itens ficam na base
    marcados como fora de escopo, para o portão poder medir a contaminação; o
    KPI os ignora.
  - Preços de referência lixo: item com "de R$ 9.999" e desconto de -79%. O
    coletor não grava `preco_de` desta fonte.

---

## Como adicionar varejista novo

1. Achar a URL com a ordenação **por vendas** e testar no navegador. Se a
   ordenação não existir de forma explícita, **não adicionar**: relevância não
   serve e não é comparável com o resto da série.
2. Registrar a fonte em `bestsellers/config.py` — URL pública, parâmetros de
   ordenação aceitos, mecânica e base de `vendidos`.
3. Implementar a classe em `bestsellers/sources/` e registrá-la em
   `SOURCE_CLASSES`.
4. Rodar 3 dias antes de usar em decisão.

Prioridade de expansão, por volume de ofertas no painel PriceTrack de
04/08/2026: Extra (2.442), Ponto (2.064), Carrefour (1.843), Americanas
(1.164), Web Continental (684).
