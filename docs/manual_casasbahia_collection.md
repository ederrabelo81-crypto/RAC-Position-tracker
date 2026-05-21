# Coleta Casas Bahia via Claude Chrome Extension — 31 Keywords

**Uso:** Casas Bahia está em **stand-by** no scraper automático (`scrapers/casas_bahia.py`)
por causa do WAF Akamai. A extensão Claude usa o seu Chrome real (IP residencial +
perfil com histórico), então passa pelo Akamai onde o curl_cffi/Playwright falha.

**Output:** CSV semicolon-separated (`;`) → `reenviar_csv.py` → tabela `coletas` do Supabase.

---

## Quando usar

- Scraper automático bloqueado pelo Akamai WAF (redireciona para página de bloqueio)
- Sem sessão válida capturada por `session_grabber.py`
- Coleta pontual para reativar a cobertura da Casas Bahia

---

## Pré-requisitos

1. Chrome com a extensão Claude ativa (login não é obrigatório)
2. `reenviar_csv.py` funcional na raiz do projeto
3. Credenciais Supabase em `.env` (`SUPABASE_URL` + `SUPABASE_KEY`)

---

## Prompt para a Claude Chrome Extension

Abra `https://www.casasbahia.com.br`, abra a extensão Claude e cole o prompt
abaixo. Substitua os 3 placeholders do `CONTEXTO FIXO` antes de enviar.

````text
Você é um agente de coleta de dados de e-commerce para o projeto RAC Position
Tracker (monitoramento de preços de ar-condicionado no Brasil). Sua tarefa:
percorrer o site da Casas Bahia, buscar 31 keywords e gerar UM único arquivo
CSV pronto para subir ao Supabase.

═══════════════════════════════════════════════════════════════════
CONTEXTO FIXO — repita estes valores em TODAS as linhas do CSV:
  Data            = <DATA>        (formato YYYY-MM-DD, ex: 2026-05-21)
  Horário         = <HORARIO>     (formato HH:MM, ex: 10:30)
  Turno           = <TURNO>       ("Abertura" se a hora <= 12:00, senão "Fechamento")
  Analista        = Claude Chrome Extension
  Plataforma      = Casas Bahia
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
  1. Vá para https://www.casasbahia.com.br
  2. Clique no campo de busca, digite a keyword EXATAMENTE como escrita
     acima e pressione Enter (digitar no campo evita o bloqueio anti-bot).
  3. Espere a grade de resultados carregar e role a página até o fim para
     disparar o lazy-load de todos os produtos da primeira página.
  4. Extraia TODOS os produtos da grade principal de resultados.
     IGNORE os blocos "Quem viu, viu também", "Você também pode gostar",
     banners e qualquer carrossel fora da grade de busca.

EXTRAÇÃO — para cada produto da grade:
  • Produto / SKU      : nome completo do produto, como exibido no card.
  • Preço (R$)         : preço à vista / preço principal exibido (NÃO use o
                         preço "de" riscado nem o valor da parcela). Formato
                         numérico puro: sem "R$", sem ponto de milhar,
                         vírgula vira ponto (ex: "R$ 1.994,91" -> 1994.91).
                         Se o card não tiver preço, NÃO inclua o produto.
  • Seller / Vendedor  : loja que vende. Use "Casas Bahia" para venda direta;
                         se o card disser "Vendido por XYZ", use XYZ.
  • Posição Geral      : ordem do card na listagem (1, 2, 3, ...), de cima
                         para baixo e da esquerda para a direita, INCLUINDO
                         os patrocinados.
  • patrocinado?       : verdadeiro se o card tem selo "Patrocinado" / "Anúncio".
  • Avaliação          : nota em estrelas, de 0 a 5 (ex: 4.6). Vazio se o
                         card não exibir nota.
  • Qtd Avaliações     : número inteiro de avaliações (ex: 212). Vazio se não houver.

CAMPOS DERIVADOS — calcule por keyword:
  • Posição Orgânica    : contador apenas dos produtos NÃO patrocinados
                          (1, 2, 3, ...). Vazio se o produto for patrocinado.
  • Posição Patrocinada : contador apenas dos produtos patrocinados
                          (1, 2, 3, ...). Vazio se o produto for orgânico.
  • Tag Destaque        : "Patrocinado" se patrocinado; senão o selo do card
                          ("Frete grátis", "Oferta do dia", "Cupom", ...) ou vazio.
  • Fulfillment?        : "Sim" se vendido pela própria Casas Bahia (sem
                          "Vendido por" de terceiro); senão "Não".
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
  • "Preço (R$)" sempre com ponto decimal e sem milhar: 1994.91.
  • Não envolva os valores em aspas.

ENTREGA:
  • Forneça o CSV completo e, se possível, como arquivo para download com o nome:
      rac_monitoramento_<DATA-SEM-TRACOS>_<HORARIO-SEM-DOIS-PONTOS>_casasbahia.csv
      (ex: rac_monitoramento_20260521_1030_casasbahia.csv)
  • Comece pela keyword 1 e siga até a 31. Se o site exibir captcha,
    "Pardon Our Interruption" ou verificação anti-bot, PARE e me avise —
    não tente burlar.
  • Ao final, informe um resumo: total de linhas e quantas keywords ficaram
    com 0 produtos.
````

> **Coleta parcial:** se a sessão não aguentar as 31 keywords, rode em blocos
> (1–11, 12–22, 23–31) e junte os CSVs mantendo um único cabeçalho.

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
| **Conceder permissão só ao domínio da coleta** (casasbahia.com.br) | Evita a extensão carregar contexto de outras abas | Neutro — apenas escopo |

Não economize cortando o scroll, pulando keywords ou reduzindo a página coletada:
isso reduz cobertura, que é o objetivo da coleta.

---

## Upload para o Supabase

Salve o CSV em `output/` (encoding UTF-8 BOM) com o nome
`rac_monitoramento_YYYYMMDD_HHMM_casasbahia.csv` e envie:

```bash
source venv/bin/activate            # Linux/Mac  (venv\Scripts\activate no Windows)
python reenviar_csv.py output/rac_monitoramento_20260521_1030_casasbahia.csv
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
       .eq("plataforma", "Casas Bahia")
       .eq("data", "2026-05-21")
       .execute())
print(f"Registros Casas Bahia em 2026-05-21: {r.count}")
EOF
```

---

## Notas

- `Plataforma` = `Casas Bahia` e `Tipo Plataforma` = `Nacional Retail` espelham
  `CasasBahiaScraper.platform_name` e `config.PLATFORM_TYPE["Casas Bahia"]`.
- A Casas Bahia exibe um preço "de" riscado e o preço "por": use sempre o
  preço final à vista, nunca o riscado nem o valor de parcela.
- `reenviar_csv.py` gera `run_id` novo a cada execução — não reimporte o mesmo CSV.
