---
name: coleta-ar-condicionado-br
description: Monta e conserta coleta de ofertas de ar condicionado nos marketplaces brasileiros (Mercado Livre, Amazon, Magalu, Casas Bahia, Shopee, Leroy Merlin) e em lojas próprias, usando o Chrome local do usuário em perfil dedicado. Use quando pedirem para coletar posição na busca, preço, buy box ou vendedores de ar condicionado; para criar a coleta de um lojista ou dealer; para diagnosticar bloqueio, tela de login, CAPTCHA ou busca que voltou zerada; ou para agendar a coleta no Windows.
---

# Coleta de ar condicionado — marketplaces do Brasil

Esta skill acompanha um kit Python autocontido em `kit/`. Ele roda na máquina
do usuário, no Chrome de verdade, com a conta dele e o IP residencial dele.
Nada aqui depende de serviço pago, proxy ou banco de dados.

## Quando o caminho é outro

- Se você está dentro do repositório de origem (existe `scrapers/local_browser.py`),
  use os módulos do repositório. O kit é um derivado enxuto deles.
- Se a máquina é um servidor em nuvem, pare e avise: IP de datacenter é negado
  pelos antibots antes de qualquer outra coisa. A coleta precisa de IP residencial.

## Fluxo

1. **Instalar** — `pip install -r kit/requirements.txt`. Precisa do Google Chrome
   instalado; não rode `playwright install` (o kit usa o Chrome do usuário).
2. **Logar o perfil, uma vez por site** — `python kit/abrir_perfil.py --site shopee`.
   O usuário loga à mão. Nunca peça nem grave senha. Detalhes e armadilhas em
   `references/navegador-local.md`.
3. **Coletar pouco primeiro** — uma keyword, uma página:
   `python kit/coletar.py --site mercadolivre --keyword "ar condicionado 12000 btus"`.
   Confira o CSV contra a tela antes de aumentar o volume.
4. **Ler a cobertura** — ao lado do CSV sai um `.cobertura.json`. Código de saída
   2 significa que alguma keyword não foi observada. Trate antes de seguir.
5. **Buy box** — a busca quase nunca diz quem vende. Use `--buy-box N` para abrir
   a página dos N primeiros produtos. Dimensione: cada produto é uma navegação.
6. **Agendar** — só depois de alguns dias estáveis. Ver
   `references/agendamento-windows.md`.

Para o que muda de um site para outro (URL de busca, login, onde está o
vendedor, armadilhas conhecidas), leia `references/marketplaces.md` antes de
mexer em qualquer site.

## Quando a coleta volta sem dado

O kit grava o HTML da página em `logs/` com a causa no nome do arquivo. Abra o
arquivo antes de propor conserto; não adivinhe.

| Causa | O que é | Conserto |
|---|---|---|
| `login` | o site quer conta logada | `abrir_perfil.py --site <site>`; o usuário loga |
| `captcha` | desafio humano | o usuário resolve na janela; o kit espera 2 min |
| `bloqueio` | antibot negou (403, página-toco) | parar, esperar 30+ min, reduzir volume |
| `layout` | página carregou, nenhum produto reconhecido | ajustar `re_produto` em `kit/rac_kit/sites.py` com o HTML na mão |
| `vazia` | a busca não tem resultado | nada a consertar |
| `adiada` | site freado após 3 negações seguidas | rodar depois só essas keywords |
| `sem_navegador` | Chrome fechado e não reabriu | `abrir_perfil.py` e rodar de novo |

## Regras que não se negociam

Cada uma veio de um erro real que produziu dado plausível e errado. O porquê de
cada uma está em `references/regras-de-dado.md`.

1. **Evidência antes de string.** Página com links de produto é resultado, mesmo
   que o HTML contenha "faça login" ou "captcha". Nunca decida bloqueio por
   `"texto" in html`.
2. **Silêncio não é mercado.** Keyword bloqueada vira linha na cobertura, nunca
   some. Não compare dias com cobertura diferente.
3. **Ausência de dado é dado ausente.** Vendedor não observado fica vazio. Não
   preencha com o nome do marketplace nem com "3P".
4. **Não insista em bloqueio.** Três negações seguidas freiam o site. Não remova
   o freio, não encurte as pausas, não rode em paralelo no mesmo site.
5. **Só o aparelho entra em série de preço.** Kit de instalação, peça, meia
   unidade e pacote com 2+ aparelhos ficam na coleta com `Tipo Anúncio` próprio;
   o que nem é ar condicionado fica como `FORA_ESCOPO`.
6. **Preço só se compara no mesmo BTU e no mesmo formato.** Split hi-wall não se
   mistura com portátil, janela ou cassete.
7. **Posição é ordinal.** Não some posições nem trate ranking como participação
   de mercado.
8. **Um só Playwright por processo.** Use `ChromeLocal`; não abra outro
   `sync_playwright()` por fora.
9. **Chrome fechado não é bloqueio.** Reconecta e tenta de novo; não conta para
   o freio.

## Limites

- A conta é do usuário e o login é manual. Não automatize senha, não contorne
  CAPTCHA com serviço de terceiros, não burle verificação de dispositivo.
- Colete só dado público de oferta (produto, preço, posição, nome da loja).
  Nada de dado pessoal de comprador ou de vendedor pessoa física.
- Mantenha o volume baixo e as pausas do kit. Os termos de uso de cada site são
  responsabilidade de quem roda a coleta — diga isso ao usuário se ele pedir
  escala.

## Estender

- **Loja própria ou dealer:** `--url-busca "https://loja/busca?q={q}&page={pagina}"`.
  Se nenhum produto for reconhecido, passe `--re-produto` com a regex do link de
  produto daquela loja.
- **Site novo fixo:** registre um `Site` em `kit/rac_kit/sites.py`.
- **Marcas:** edite `MARCAS` em `kit/rac_kit/texto.py` (nome composto antes do
  simples).
- O kit entrega posição, título, link, id e patrocinado de forma estrutural. O
  preço lido do card é heurístico; para preço e vendedor confiáveis, use o PDP.
