# Coleta Magalu via Claude Chrome Extension — 31 Keywords

**Uso:** Fallback quando o scraper automático (`scrapers/magalu.py`) está bloqueado
pelo Akamai. A extensão Claude usa o seu Chrome real (IP residencial + perfil com
histórico), então passa pelo sensor.js onde o Playwright headless falha.

**Output:** CSV semicolon-separated (`;`) → `reenviar_csv.py` → tabela `coletas` do Supabase.

---

## Quando usar

- Scraper automático retorna `len≈1074` ("Pardon Our Interruption" do Akamai)
- Circuit breaker dispara (`5 keywords seguidas 100% bloqueadas`)
- VM Oracle sem `xvfb` ou perfil Chrome ainda sem histórico acumulado
- Coleta de emergência fora do horário de cron

Se o scraper automático está funcionando, prefira-o — garante horário e cobertura consistentes.

---

## Pré-requisitos

1. Chrome com a extensão Claude ativa, logado em uma conta normal de navegação
2. `reenviar_csv.py` funcional no ambiente local (raiz do projeto)
3. Credenciais Supabase em `.env` (`SUPABASE_URL` + `SUPABASE_KEY`)

---

## Prompt para a Claude Chrome Extension

Abra uma aba em `https://www.magazineluiza.com.br`, abra a extensão Claude e cole
o prompt abaixo **na íntegra**. Antes de colar, substitua os 3 placeholders do
bloco `CONTEXTO FIXO` (`<DATA>`, `<HORARIO>`, `<TURNO>`) pelos valores da coleta.

````text
Você é um agente de coleta de dados de e-commerce para o projeto RAC Position
Tracker (monitoramento de preços de ar-condicionado no Brasil). Sua tarefa:
percorrer o site da Magazine Luiza, buscar 31 keywords e gerar UM único arquivo
CSV pronto para subir ao Supabase.

═══════════════════════════════════════════════════════════════════
CONTEXTO FIXO — repita estes valores em TODAS as linhas do CSV:
  Data            = <DATA>        (formato YYYY-MM-DD, ex: 2026-05-21)
  Horário         = <HORARIO>     (formato HH:MM, ex: 10:30)
  Turno           = <TURNO>       ("Abertura" se a hora <= 12:00, senão "Fechamento")
  Analista        = Claude Chrome Extension
  Plataforma      = Magalu
  Tipo Plataforma = Nacional Retail
═══════════════════════════════════════════════════════════════════

KEYWORDS (31) — formato "keyword || Categoria Keyword":
 1. ar condicionado split                   || Genérica
 2. ar condicionado inverter                || Genérica
 3. ar condicionado                         || Genérica
 4. ar condicionado split inverter          || Genérica
 5. ar condicionado 9000 btus               || Capacidade BTU
 6. ar condicionado 12000 btus              || Capacidade BTU
 7. ar condicionado 18000 btus              || Capacidade BTU
 8. ar condicionado 24000 btus              || Capacidade BTU
 9. ar condicionado 9000 btus inverter      || Capacidade + Tipo
10. ar condicionado 12000 btus inverter     || Capacidade + Tipo
11. split 12000 btus inverter               || Capacidade + Tipo
12. split 9000 btus inverter                || Capacidade + Tipo
13. ar condicionado midea                   || Marca
14. midea inverter                          || Marca
15. midea 12000 btus                        || Marca
16. ar condicionado midea 12000             || Marca
17. midea ecomaster                         || Modelo Midea
18. midea airvolution                       || Modelo Midea
19. ar condicionado lg                      || Marca
20. lg dual inverter                        || Marca
21. ar condicionado lg dual inverter 12000  || Marca
22. ar condicionado samsung                 || Marca
23. samsung windfree                        || Marca
24. ar condicionado gree                    || Marca
25. ar condicionado elgin                   || Marca
26. ar condicionado philco                  || Marca
27. ar condicionado tcl                     || Marca
28. melhor ar condicionado custo benefício  || Intenção Compra
29. melhor ar condicionado 2026             || Intenção Compra
30. comprar ar condicionado                 || Intenção Compra
31. ar condicionado em promoção             || Preço / Promoção

PROCEDIMENTO — repita para cada uma das 31 keywords, na ordem:
  1. Vá para https://www.magazineluiza.com.br
  2. Clique no campo de busca, digite a keyword EXATAMENTE como escrita
     acima e pressione Enter (não monte a URL na mão — digitar no campo
     evita o bloqueio anti-bot).
  3. Espere os resultados carregarem e role a página até o fim para
     disparar o lazy-load de todos os produtos.
  4. Extraia TODOS os produtos da grade principal de resultados (página 1).
     IGNORE carrosséis de "produtos similares", "você também pode gostar",
     banners e qualquer bloco fora da grade principal de busca.

EXTRAÇÃO — para cada produto da grade:
  • Produto / SKU      : nome completo do produto, como exibido no card.
  • Preço (R$)         : preço à vista / preço principal exibido. Formato
                         numérico puro: sem "R$", sem ponto de milhar,
                         vírgula vira ponto decimal (ex: "R$ 1.994,91" -> 1994.91).
                         Se o card não tiver preço, NÃO inclua o produto.
  • Seller / Vendedor  : loja que vende. Use "Magazine Luiza" quando for
                         venda direta da própria Magalu.
  • Posição Geral      : ordem do card na listagem (1, 2, 3, ...), contando
                         de cima para baixo e da esquerda para a direita,
                         INCLUINDO os patrocinados.
  • patrocinado?       : verdadeiro se o card tem selo "Patrocinado" / "Anúncio".
  • Avaliação          : nota de 0 a 5 (ex: 4.7). Vazio se o card não tiver nota.
  • Qtd Avaliações     : número inteiro de avaliações (ex: 1320). Vazio se não houver.

CAMPOS DERIVADOS — calcule por keyword:
  • Posição Orgânica    : contador apenas dos produtos NÃO patrocinados
                          (1, 2, 3, ...). Deixe VAZIO se o produto for patrocinado.
  • Posição Patrocinada : contador apenas dos produtos patrocinados
                          (1, 2, 3, ...). Deixe VAZIO se o produto for orgânico.
  • Tag Destaque        : "Patrocinado" se patrocinado; senão o selo do card
                          ("Frete grátis", "Mais vendido", "Cupom", ...) ou vazio.
  • Fulfillment?        : "Sim" se vendido E entregue pela própria Magazine Luiza;
                          senão "Não".
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
  • Uma linha por produto. Todos os produtos das 31 keywords no MESMO arquivo.
  • "Keyword Buscada" = a keyword exata; "Categoria Keyword" = a categoria
    correspondente da lista de keywords.
  • Campo sem valor = célula vazia. NÃO escreva "N/A", "null" ou "-".
  • O separador é ";". Nenhum valor pode conter ";": se um nome de produto
    tiver ";", substitua por espaço.
  • "Preço (R$)" sempre com ponto decimal e sem milhar: 1994.91 — nunca
    "1.994,91" nem "R$ 1994,91".
  • Não envolva os valores em aspas.

ENTREGA:
  • Forneça o CSV completo e, se possível, como arquivo para download com o nome:
      rac_monitoramento_<DATA-SEM-TRACOS>_<HORARIO-SEM-DOIS-PONTOS>_magalu.csv
      (ex: rac_monitoramento_20260521_1030_magalu.csv)
  • Comece pela keyword 1 e siga até a 31. Se o site exibir captcha ou
    verificação anti-bot, PARE e me avise — não tente burlar.
  • Ao final, informe um resumo: total de linhas e quantas keywords ficaram
    com 0 produtos.
````

> **Coleta parcial:** se a sessão da extensão não aguentar as 31 keywords de uma
> vez, rode o prompt em 3 blocos (keywords 1–11, 12–22, 23–31), abrindo uma
> conversa nova entre cada bloco, e junte os CSVs no final mantendo um único
> cabeçalho.

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
| **Conceder permissão só ao domínio da coleta** (magazineluiza.com.br) | Evita a extensão carregar contexto de outras abas | Neutro — apenas escopo |

**Estratégia de blocos — é isto que faz as 31 keywords caberem.** Os "tokens da
extensão" são o contexto da conversa, não os cookies de sessão do site. Trocar de
modelo muda custo e qualidade, **não** o tamanho do contexto: o que mantém a
coleta dentro do limite é dividir as 31 keywords em 3 blocos e abrir uma conversa
NOVA entre cada bloco (bloco A = 1–11, B = 12–22, C = 23–31). Cada bloco gera seu
CSV; junte os três no final mantendo um único cabeçalho.

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

## Por que estes valores fixos

Os campos fixos têm que bater com o que o scraper Python grava, senão o dashboard
trata a coleta manual como uma plataforma diferente:

| Campo             | Valor               | Origem                                        |
|-------------------|---------------------|-----------------------------------------------|
| `Plataforma`      | `Magalu`            | `MagaluScraper.platform_name`                 |
| `Tipo Plataforma` | `Nacional Retail`   | `config.PLATFORM_TYPE["Magalu"]`              |
| `Analista`        | `Claude Chrome Extension` | distingue a coleta manual da automática |
| `Turno`           | `Abertura` / `Fechamento` | `Abertura` se hora ≤ 12h (`TURNO_ABERTURA_MAX_HOUR`) |

As 31 keywords e suas categorias são o espelho exato de `KEYWORDS_LIST` em `config.py`.

---

## Upload para o Supabase

Salve o CSV em `output/` com encoding **UTF-8 BOM** e nome no padrão
`rac_monitoramento_YYYYMMDD_HHMM_magalu.csv` (o `reenviar_csv.py` extrai a data
do nome do arquivo).

```bash
# Ativa o ambiente virtual
source venv/bin/activate            # Linux/Mac
venv\Scripts\activate               # Windows

# Envia ao Supabase (gera run_id novo, faz dedup pela constraint coletas_unique_run)
python reenviar_csv.py output/rac_monitoramento_20260521_1030_magalu.csv
```

Saída esperada:

```
[CSV] 620 linhas | Colunas: [...]
[UPLOAD] 615 registros válidos.
[INSERT] Lote 1/2 | Inseridas=500 Ignoradas=0
[INSERT] Lote 2/2 | Inseridas=115 Ignoradas=0
Upload concluído sem discrepâncias. ✓
```

`reenviar_csv.py` filtra produtos não-AC (ventilador, climatizador, peças...) e
ignora linhas sem nome de produto — discrepâncias pequenas entre "CSV lido" e
"Após filtros" são esperadas.

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
       .eq("plataforma", "Magalu")
       .eq("data", "2026-05-21")
       .execute())
print(f"Registros Magalu em 2026-05-21: {r.count}")
EOF
```

---

## Notas

- **Cobertura esperada:** 31 keywords × ~20-40 produtos por página ≈ 600-1.200 linhas.
- **Marca:** o prompt já detecta a marca; se sobrar "Desconhecida" demais, rode
  `recalculate_unknown_brands_in_supabase()` em `utils/supabase_client.py` após o upload.
- **Idempotência:** `reenviar_csv.py` gera um `run_id` novo a cada execução — não
  reimporte o mesmo CSV duas vezes ou criará um segundo snapshot do mesmo turno.
- **Páginas:** o prompt coleta a página 1 de cada keyword. Para cobertura maior,
  peça à extensão para repetir o procedimento na página 2 (`?page=2`), mantendo a
  contagem de posição contínua entre as páginas da mesma keyword.
