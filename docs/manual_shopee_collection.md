# Coleta Shopee via Claude Chrome Extension — 10 Keywords

**Uso:** Fallback / alternativa ao scraper Node.js (`magalu_shopee/`), que exige
sessão autenticada e cookies `csrftoken` válidos. A extensão Claude usa o seu
Chrome real **já logado na Shopee**, então enxerga a mesma listagem que um usuário.

**Output:** CSV semicolon-separated (`;`) → `reenviar_csv.py` → tabela `coletas` do Supabase.

---

## Quando usar

- Scraper Node.js da Shopee retorna 0 itens (cookies de sessão expirados)
- Shopee rejeita a API interna (`x-csrftoken` inválido / sem login)
- Coleta de emergência fora do horário de cron

> **Pré-requisito crítico:** o Chrome precisa estar **logado na sua conta Shopee**
> antes de iniciar. Sem login, a Shopee limita a busca e bloqueia a listagem.

---

## Pré-requisitos

1. Chrome logado em `shopee.com.br` com a extensão Claude ativa
2. `reenviar_csv.py` funcional na raiz do projeto
3. Credenciais Supabase em `.env` (`SUPABASE_URL` + `SUPABASE_KEY`)

---

## Prompt para a Claude Chrome Extension

Abra `https://shopee.com.br` (já logado), abra a extensão Claude e cole o prompt
abaixo. Substitua os 3 placeholders do `CONTEXTO FIXO` antes de enviar.

````text
Você é um agente de coleta de dados de e-commerce para o projeto RAC Position
Tracker (monitoramento de preços de ar-condicionado no Brasil). Sua tarefa:
percorrer o site da Shopee Brasil, buscar 10 keywords e gerar UM único arquivo
CSV pronto para subir ao Supabase.

═══════════════════════════════════════════════════════════════════
CONTEXTO FIXO — repita estes valores em TODAS as linhas do CSV:
  Data            = <DATA>        (formato YYYY-MM-DD, ex: 2026-05-21)
  Horário         = <HORARIO>     (formato HH:MM, ex: 10:30)
  Turno           = <TURNO>       ("Abertura" se a hora <= 12:00, senão "Fechamento")
  Analista        = Claude Chrome Extension
  Plataforma      = Shopee
  Tipo Plataforma = Nacional Marketplace
═══════════════════════════════════════════════════════════════════

KEYWORDS (10) — formato "keyword || Categoria Keyword":
 1. ar condicionado split                   || Genérica
 2. ar condicionado inverter                || Genérica
 3. ar condicionado                         || Genérica
 4. ar condicionado split inverter          || Genérica
 5. ar condicionado 9000 btus               || Capacidade BTU
 6. ar condicionado 12000 btus              || Capacidade BTU
 7. ar condicionado 9000 btus inverter      || Capacidade + Tipo
 8. ar condicionado 12000 btus inverter     || Capacidade + Tipo
 9. split 9000 btus inverter                || Capacidade + Tipo
10. split 12000 btus inverter               || Capacidade + Tipo

PROCEDIMENTO — repita para cada uma das 10 keywords, na ordem:
  1. Vá para https://shopee.com.br
  2. Clique no campo de busca, digite a keyword EXATAMENTE como escrita
     acima e pressione Enter.
  3. Espere a grade de resultados carregar e role a página até o fim para
     disparar o lazy-load de todos os produtos da primeira página.
  4. Extraia TODOS os produtos da grade principal de resultados.
     IGNORE os blocos "Buscas relacionadas", "Talvez você goste",
     banners de categoria e qualquer carrossel fora da grade de busca.

EXTRAÇÃO — para cada produto da grade:
  • Produto / SKU      : título completo do produto, como exibido no card.
  • Preço (R$)         : preço exibido. Se houver faixa ("R$ X - R$ Y"), use
                         o MENOR valor. Formato numérico puro: sem "R$", sem
                         ponto de milhar, vírgula vira ponto (ex: 1994.91).
                         Se o card não tiver preço, NÃO inclua o produto.
  • Seller / Vendedor  : nome da loja, se visível no card. Se não aparecer no
                         card, deixe vazio.
  • Posição Geral      : ordem do card na listagem (1, 2, 3, ...), de cima
                         para baixo e da esquerda para a direita, INCLUINDO
                         os patrocinados.
  • patrocinado?       : verdadeiro se o card tem selo "Anúncio" / "Ad" /
                         "Patrocinado".
  • Avaliação          : nota em estrelas, de 0 a 5 (ex: 4.8). Vazio se o
                         card não exibir nota.
  • Qtd Avaliações     : número de avaliações, se exibido. Vazio se o card
                         só mostrar "X vendidos" e não o total de avaliações.

CAMPOS DERIVADOS — calcule por keyword:
  • Posição Orgânica    : contador apenas dos produtos NÃO patrocinados
                          (1, 2, 3, ...). Vazio se o produto for patrocinado.
  • Posição Patrocinada : contador apenas dos produtos patrocinados
                          (1, 2, 3, ...). Vazio se o produto for orgânico.
  • Tag Destaque        : "Patrocinado" se patrocinado; senão o selo do card
                          ("Frete Grátis", "Mais Vendido", "X vendidos", ...)
                          ou vazio.
  • Fulfillment?        : "Sim" se o card tem selo "Shopee Mall" / loja
                          oficial; senão "Não".
  • Marca Monitorada    : detecte a marca no nome do produto usando a lista
                          abaixo. Teste na ordem e use a PRIMEIRA que casar
                          (palavra inteira, ignorando maiúsculas/minúsculas):
                          Springer Midea, Midea Carrier, Midea, Carrier, Elgin,
                          Electrolux, Agratto, Springer, Consul, Daikin, Fujitsu,
                          Hitachi, York, Gree, TCL, Hisense, Haier, Britânia,
                          Komeco, LG, Samsung, Philco, Panasonic.
                          Se nenhuma casar, use "Desconhecida".

SAÍDA — UM único CSV, separador ponto-e-vírgula (;), com EXATAMENTE este
cabeçalho (uma linha, nesta ordem):
Data;Turno;Horário;Analista;Plataforma;Tipo Plataforma;Keyword Buscada;Categoria Keyword;Marca Monitorada;Produto / SKU;Posição Orgânica;Posição Patrocinada;Posição Geral;Preço (R$);Seller / Vendedor;Fulfillment?;Avaliação;Qtd Avaliações;Tag Destaque

REGRAS DO CSV:
  • Uma linha por produto. Todos os produtos das 10 keywords no MESMO arquivo.
  • "Keyword Buscada" = a keyword exata; "Categoria Keyword" = a categoria
    correspondente da lista de keywords.
  • Campo sem valor = célula vazia. NÃO escreva "N/A", "null" ou "-".
  • O separador é ";". Nenhum valor pode conter ";": se um nome de produto
    tiver ";", substitua por espaço.
  • "Preço (R$)" sempre com ponto decimal e sem milhar: 1994.91.
  • Não envolva os valores em aspas.

ENTREGA:
  • Forneça o CSV completo e, se possível, como arquivo para download com o nome:
      rac_monitoramento_<DATA-SEM-TRACOS>_<HORARIO-SEM-DOIS-PONTOS>_shopee.csv
      (ex: rac_monitoramento_20260521_1030_shopee.csv)
  • Comece pela keyword 1 e siga até a 10. Se a Shopee exibir captcha,
    verificação anti-bot ou pedir login, PARE e me avise — não tente burlar.
  • Ao final, informe um resumo: total de linhas e quantas keywords ficaram
    com 0 produtos.
````

> **Lista enxuta:** este guia usa só 10 keywords (genéricas + 9.000/12.000 BTUs)
> — sem keywords de marca, modelo ou intenção de compra. É proposital: 31
> keywords não cabiam numa sessão da extensão. Se ainda assim a sessão apertar,
> rode em 2 blocos (keywords 1–5 e 6–10), com uma conversa nova entre eles, e
> junte os CSVs mantendo um único cabeçalho.

---

## Economia de tokens na Claude Chrome Extension

Estes ajustes reduzem o consumo de tokens **sem perder qualidade de coleta**:

| Ajuste | Por que economiza | Impacto na qualidade |
|--------|-------------------|----------------------|
| **Selecionar só a grade de produtos** antes de acionar a extensão (arraste o mouse pela área da listagem) | A extensão envia apenas o DOM/texto selecionado, não a página inteira (cabeçalho, menus, rodapé, carrosséis de recomendação) | **Melhora** — menos ruído, a extensão não confunde produto de busca com produto recomendado |
| **Rolar a página até o fim ANTES de acionar** | A extensão lê tudo em 1 captura, em vez de rolar+ler N vezes | Neutro — desde que todos os produtos estejam carregados |
| **Coletar em blocos de ~10 keywords por conversa** e iniciar conversa nova entre blocos | Conversa longa reenvia o histórico inteiro a cada passo; reiniciar zera o acúmulo | Neutro — cada bloco é independente |
| **Preferir o modo de leitura de DOM/texto a screenshots** | Imagens custam muito mais tokens que texto; nome/preço/nota são texto puro | Neutro — só use screenshot se a extração por texto falhar |
| **Pedir saída só do CSV, sem explicações** (o prompt já faz isso) | Reduz tokens de saída | Neutro |
| **Conceder permissão só ao domínio da coleta** (shopee.com.br) | Evita a extensão carregar contexto de outras abas | Neutro — apenas escopo |

**Lista enxuta + blocos.** Os "tokens da extensão" são o contexto da conversa,
não os cookies de sessão do site. Este guia já corta a lista para 10 keywords
(genéricas + 9.000/12.000 BTUs) justamente porque 31 não cabiam numa sessão.
Com 10 keywords a coleta costuma caber numa conversa só; se apertar, divida em
2 blocos (1–5 e 6–10) com conversa nova entre eles. Trocar de modelo muda custo
e qualidade, **não** o tamanho do contexto — quem resolve o limite é a lista
menor e os blocos.

**Modelo da extensão — use o mais capaz.** Se a sua versão da extensão Claude
permite escolher o modelo, mantenha o modelo mais capaz disponível (Sonnet/Opus)
para a coleta e **não troque para o Haiku**. A coleta é uma tarefa agêntica —
navegar, buscar, rolar, distinguir patrocinado de orgânico, casar marca em ordem
de prioridade — e modelos menores erram mais nesses passos. Cada erro vira um
retry, que **gasta mais tokens, não menos**. Economize com a estratégia de
blocos e a seleção de grade, nunca rebaixando o modelo.

Não economize cortando o scroll, pulando keywords ou reduzindo a página coletada:
isso reduz cobertura, que é o objetivo da coleta.

---

## Upload para o Supabase

Salve o CSV em `output/` (encoding UTF-8 BOM) com o nome
`rac_monitoramento_YYYYMMDD_HHMM_shopee.csv` e envie:

```bash
source venv/bin/activate            # Linux/Mac  (venv\Scripts\activate no Windows)
python reenviar_csv.py output/rac_monitoramento_20260521_1030_shopee.csv
```

---

## Validação pós-upload

```bash
python - <<'EOF'
import os
from supabase import create_client
from dotenv import load_dotenv
load_dotenv()
sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
r = (sb.table("coletas").select("id", count="exact")
       .eq("plataforma", "Shopee")
       .eq("data", "2026-05-21")
       .execute())
print(f"Registros Shopee em 2026-05-21: {r.count}")
EOF
```

---

## Notas

- `Plataforma` = `Shopee` e `Tipo Plataforma` = `Nacional Marketplace` espelham
  `config.PLATFORM_TYPE["Shopee"]` — não invente outros valores.
- A Shopee mostra "X vendidos", não a contagem de avaliações, em muitos cards;
  nesse caso `Qtd Avaliações` fica vazio (correto) e "X vendidos" vai em `Tag Destaque`.
- `reenviar_csv.py` gera `run_id` novo a cada execução — não reimporte o mesmo CSV.
