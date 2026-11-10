# Marketplaces: o que muda de um site para outro

O kit lê qualquer página de busca de forma estrutural (links de produto, na
ordem). O que segue é o que ele **não** descobre sozinho: de onde vem o dado
bom em cada site e o que já deu errado. Validade: observado até novembro de
2026 — os sites mudam; confira contra a tela antes de confiar.

## Resumo

| Site | Proteção | Login | Vendedor na busca? | Onde está a buy box |
|---|---|---|---|---|
| Mercado Livre | tela de login / verificação de dispositivo | recomendado | só loja oficial | página do produto |
| Amazon | CAPTCHA | não | não | página do produto |
| Magalu | Akamai + tela de login | recomendado | não confiável | página do produto |
| Casas Bahia | Akamai | não | não | página do produto |
| Shopee | antifraude por sessão | obrigatório | sim, mas o kit não lê | página do produto (`--buy-box`) |
| Leroy Merlin | desafio em página de produto | não | não | página do produto |
| Google Shopping | reCAPTCHA | recomendado | sim (lojas comparadas) | fora do kit |

## Mercado Livre

- Busca em `lista.mercadolivre.com.br/<palavras-com-hifen>`. A paginação é por
  deslocamento (`_Desde_N`), e o tamanho da página muda (já foi 48, depois ~60):
  o kit usa a contagem real de itens vistos.
- O HTML de uma busca normal contém "Para continuar, acesse sua conta" e o
  script `/gz/webdevice`. Um detector por texto já descartou uma coleta inteira
  de páginas cheias de produto. O que indica gate é a URL (verificação de
  conta ou login) ou a página sem nenhum link de produto; frase solta não.
- Verificação de dispositivo: uma vez marcado, toda busca redireciona, e a home
  continua abrindo normalmente — parece "site ok, coletor quebrado". Recarregar
  não resolve. O conserto é logar o perfil.
- Sessão logada expira em dias (já foram três vezes em duas semanas). O sintoma
  é o volume desabar sem zerar: confira a cobertura a cada coleta. Relogue pelo `abrir_perfil.py`.
- O card só imprime o vendedor quando quer destacar a loja (na prática, loja
  oficial). Card sem vendedor **não** é "terceiro": é não informado.
- Patrocinado: texto "Patrocinado" ou link de rastreio (`click1.mercadolivre`,
  `mclics`).
- Em gate em série, pare. O kit freia após 3 páginas negadas seguidas; rode o
  que ficou adiado numa repescagem, depois de esfriar (30+ min).

## Amazon

- Busca em `/s?k=<busca>&page=<n>`. O id do produto é o ASIN (`/dp/<ASIN>`).
- A busca **não mostra "Vendido por"**. Sem abrir a página do produto, não há
  buy box. Não grave "Amazon" como vencedor por falta de dado: 100% das linhas
  virariam vitória da própria Amazon.
- Na página do produto, cuidado com o link "Visite a loja <MARCA>": é a marca,
  não o vendedor.
- "X ofertas" no card é a quantidade de vendedores naquele produto.
- A buy box muda de dono ao longo do dia. Leia a cada coleta; não reaproveite a
  leitura de ontem.

## Magalu

- Akamai em duas camadas: impressão digital da conexão e um sensor em
  JavaScript que valida o cookie `_abck`. A home passa e a busca nega se a
  sessão não foi validada — por isso o aquecimento é obrigatório.
- Se a navegação direta para `/busca/` for negada, entrar pela caixa de busca
  da home (digitar e Enter) costuma passar. O kit não faz isso sozinho.
- Tela de login: vem com HTTP 200 e mais de 1 MB, então passa por qualquer
  detecção de "página pequena". O kit a reconhece pela URL ou pela falta de
  produtos.
- A URL do produto carrega o departamento (`/p/<id>/<depto>/<sub>/`). Anúncio
  (`ads=patrocinado`) pode ser de outro departamento: uma geladeira "Inverter"
  patrocinada já apareceu como líder em busca de ar condicionado.
- Depois de ~25 buscas seguidas a chance de desafio sobe. Poucas keywords por
  rodada.

## Casas Bahia

- Akamai, mesmo tratamento da Magalu.
- O lojista de cada oferta e o vencedor da buy box ficam na página do produto.
  Produto sem buy box lida não entra em ranking de vendedor.

## Shopee

- Exige conta logada no perfil. A sessão vale horas, não dias.
- É marketplace puro: cada resultado é a oferta de uma loja, então o vendedor é
  o do próprio resultado. **O kit não lê o nome da loja no card** (ele não vem
  num rótulo "Vendido por"): sem `--buy-box`, o vendedor fica vazio.
- Selos: Shopee Mall (loja oficial) e Preferred+. "X vendidos" é base mensal.
- É o site mais instável da lista. Trate como melhor esforço.

## Leroy Merlin

- O preço exibido na busca pode ser uma **média** do índice, não o preço
  praticado. Um produto ficou meses com um piso falso por causa disso. Preço
  confiável vem do JSON-LD da página do produto.
- A oferta da própria Leroy não aparece na lista de lojistas do produto: ter
  lojista listado não significa que ele tem a buy box. O rótulo "Vendido e
  entregue por" na página do produto é quem manda.
- A página do produto devolve desafio para cliente HTTP e para browser de
  perfil zerado; no Chrome do perfil dedicado, abre.

## Google Shopping (fora do kit)

- reCAPTCHA em browser automatizado; no Chrome real logado, com resolução manual
  quando aparece, funciona.
- O título não está em nenhuma classe estável nem no `aria-label`. E o nome da
  loja costuma vir com sufixo "e mais" colado. Exige parser próprio.

## Loja própria e dealers

- Muitas lojas de climatização rodam em VTEX: link de produto termina em `/p` e
  a página traz JSON-LD com preço. É o padrão do `--url-busca`.
- Em VTEX o preço do card costuma depender de JavaScript; prefira o JSON-LD.
- Carrossel repete produto: deduplique pelo id, nunca pela posição.
- Seletor genérico demais já contou 775 "produtos" numa página. Confira a
  contagem contra a tela.
- Na loja própria o lojista controla a vitrine inteira: posição ali não é
  inteligência competitiva. A disputa acontece nos marketplaces.
