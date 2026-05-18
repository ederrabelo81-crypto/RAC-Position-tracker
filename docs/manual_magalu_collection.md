# Coleta Manual Magalu — Claude Chrome Extension

**Uso:** Fallback quando o scraper automático (`scrapers/magalu.py`) estiver bloqueado pelo Akamai.  
**Ferramenta:** Extensão Claude no Chrome (claude.ai/chat com acesso à aba ativa)  
**Output:** CSV semicolon-separated → `upload_csv.py` → Supabase

---

## Quando usar

- Scraper automático retorna `len=1074` (página "Pardon Our Interruption" do Akamai)
- VM Oracle bloqueada por ausência de `xvfb` ou perfil Chrome ainda sem histórico acumulado
- Coleta de emergência fora do horário de cron

---

## Pré-requisitos

1. Chrome instalado com extensão Claude ativa (claude.ai)
2. Python + `upload_csv.py` funcional no ambiente local
3. Supabase credentials em `.env` (ou variáveis de ambiente)

---

## Prompt para a extensão Claude

Cole este prompt exatamente no Claude Chrome Extension, com a aba do Magalu aberta na página de resultados da busca:

```
Você é um extrator de dados de e-commerce. Analise esta página de resultados do Magalu e extraia TODOS os produtos visíveis.

Para cada produto, extraia:
1. Nome completo do produto (Produto/SKU)
2. Preço atual (formato: 1994.91 — sem R$, sem pontos de milhar, vírgula → ponto)
3. Seller/Vendedor (quem vende — "Magazine Luiza" se for direto)
4. Posição na página (1, 2, 3... contando da esquerda para direita, linha a linha)
5. Se é patrocinado/destaque (sim/não)
6. Avaliação (ex: 4.8) e quantidade de avaliações (ex: 1250) — coloque 0 se não houver

Gere um CSV com separador ponto-e-vírgula (;) e este cabeçalho exato:
Produto / SKU;Preço (R$);Seller / Vendedor;Posição Geral;Posição Patrocinada;Tag Destaque;Avaliação;Qtd Avaliações

Regras:
- Inclua TODOS os produtos visíveis na página, incluindo patrocinados
- Para "Posição Patrocinada": número da posição SE for anúncio patrocinado, senão deixe vazio
- Para "Tag Destaque": "Patrocinado" se for anúncio, senão deixe vazio
- Preço sem símbolo de moeda, sem pontos de milhar — use ponto decimal (ex: 1994.91)
- Se um campo não estiver disponível, deixe a célula vazia (não escreva "N/A")
- Não inclua cabeçalho de coluna adicional, apenas os dados

Responda APENAS com o CSV, sem explicações.
```

---

## Procedimento passo a passo

### 1. Preparar as keywords

Keywords prioritárias (alta) — colete estas primeiro:

```
ar condicionado 9000 btus
ar condicionado 12000 btus
ar condicionado 18000 btus
ar condicionado 24000 btus
ar condicionado inverter 9000
ar condicionado inverter 12000
ar condicionado inverter 18000
ar condicionado split
ar condicionado portatil
ar condicionado janela
mini split 9000
mini split 12000
split 9000 btus
split 12000 btus
```

### 2. Coletar cada keyword

Para cada keyword:

1. Abra o Chrome e acesse: `https://www.magazineluiza.com.br/busca/<keyword-com-hifens>/`  
   Exemplo: `https://www.magazineluiza.com.br/busca/ar-condicionado-12000-btus/`

2. Aguarde a página carregar completamente (scroll até o fim para lazy-load)

3. Abra a extensão Claude no Chrome

4. Cole o prompt acima e envie

5. Copie o CSV gerado pelo Claude para um arquivo `.txt` ou `.csv`

6. Repita para a próxima página se necessário:  
   `https://www.magazineluiza.com.br/busca/ar-condicionado-12000-btus/?page=2`

### 3. Montar o CSV final

Crie um arquivo CSV com o cabeçalho completo e adicione os campos fixos manualmente:

**Cabeçalho completo:**
```
Data;Turno;Horário;Analista;Plataforma;Tipo Plataforma;Keyword Buscada;Categoria Keyword;Marca Monitorada;Produto / SKU;Posição Orgânica;Posição Patrocinada;Posição Geral;Preço (R$);Seller / Vendedor;Fulfillment?;Avaliação;Qtd Avaliações;Tag Destaque
```

**Campos fixos por linha:**
| Campo | Valor |
|-------|-------|
| Data | Data da coleta (ex: `2026-05-18`) |
| Turno | `Abertura` (coleta manhã) ou `Fechamento` (coleta noite) |
| Horário | Hora da coleta (ex: `10:30`) |
| Analista | `Coleta Manual Claude` |
| Plataforma | `Magazine Luiza` |
| Tipo Plataforma | `Marketplace` |
| Keyword Buscada | Keyword exata (ex: `ar condicionado 12000 btus`) |
| Categoria Keyword | Categoria (ex: `Split`, `Janela`, `Portátil`) |
| Marca Monitorada | Extraída automaticamente pelo `upload_csv.py` |
| Posição Orgânica | Posição se não patrocinado; vazio se patrocinado |
| Fulfillment? | `Magalu` se fulfillment próprio, senão vazio |

**Prompt auxiliar para montar o CSV completo** (use com Claude desktop ou web):

```
Tenho os dados extraídos de uma busca no Magalu para a keyword "[KEYWORD]".
Hoje é [DATA], turno [TURNO], horário [HORÁRIO].

Dados extraídos (CSV parcial):
[COLE O CSV PARCIAL AQUI]

Complete adicionando as colunas obrigatórias e retorne o CSV completo com este cabeçalho:
Data;Turno;Horário;Analista;Plataforma;Tipo Plataforma;Keyword Buscada;Categoria Keyword;Marca Monitorada;Produto / SKU;Posição Orgânica;Posição Patrocinada;Posição Geral;Preço (R$);Seller / Vendedor;Fulfillment?;Avaliação;Qtd Avaliações;Tag Destaque

Use:
- Data: [DATA]
- Turno: [TURNO]
- Horário: [HORÁRIO]
- Analista: "Coleta Manual Claude"
- Plataforma: "Magazine Luiza"
- Tipo Plataforma: "Marketplace"
- Keyword Buscada: "[KEYWORD]"
- Categoria Keyword: "[CATEGORIA]"
- Marca Monitorada: detecte pela marca no nome do produto (Midea, Carrier, Elgin, Electrolux, Springer, Consul, Daikin, Fujitsu, Hitachi, York, Gree, LG, Samsung — use "Desconhecida" se não identificar)
- Posição Orgânica: número da posição SE não for patrocinado; vazio se patrocinado
- Fulfillment?: "Magalu" se fulfillment próprio Magazine Luiza, senão vazio

Retorne APENAS o CSV completo, sem explicações.
```

### 4. Salvar o arquivo CSV

Salve com encoding UTF-8 BOM e nome no formato:
```
rac_monitoramento_YYYYMMDD_HHMM_magalu_manual.csv
```

Exemplo: `rac_monitoramento_20260518_1037_magalu_manual.csv`

### 5. Fazer upload para o Supabase

```bash
# Ativa o ambiente virtual
source .venv/bin/activate          # Linux/Mac
.venv\Scripts\activate             # Windows

# Valida antes de enviar
python scripts/upload_csv.py output/rac_monitoramento_20260518_1037_magalu_manual.csv --dry-run

# Envia (run_id derivado automaticamente do nome do arquivo — idempotente)
python scripts/upload_csv.py output/rac_monitoramento_20260518_1037_magalu_manual.csv
```

**Saída esperada:**
```
INFO  | Lendo: output/rac_monitoramento_20260518_1037_magalu_manual.csv
INFO  | 570 registros carregados do CSV.
INFO  | run_id derivado do arquivo: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
INFO  | Inserindo 570 registros...
INFO  | Upload concluído: 570 inseridos, 0 ignorados (duplicatas).
```

Se aparecer "X já existentes (dedup)": normal, significa que re-importou o mesmo CSV ou dados do turno anterior.

---

## Validação pós-upload

```bash
# Verifica no Supabase quantos registros Magalu foram inseridos hoje
python - <<'EOF'
import os; from supabase import create_client
from dotenv import load_dotenv; load_dotenv()
sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
r = sb.table("coletas").select("id", count="exact") \
    .eq("plataforma", "Magazine Luiza") \
    .gte("created_at", "2026-05-18") \
    .execute()
print(f"Registros Magalu hoje: {r.count}")
EOF
```

---

## Notas

- **Idempotência**: o `run_id` é derivado do nome do arquivo (UUID v5). Re-importar o mesmo CSV não duplica dados.
- **Páginas**: Para coleta completa, colete 2 páginas por keyword no turno manhã, 1 página no turno noite.
- **Cobertura**: 14 keywords alta-prioridade × 2 páginas × ~30 produtos/página ≈ 840 registros esperados.
- **Quando NÃO usar**: Se o scraper automático estiver funcionando (sem bloqueio Akamai), prefira a coleta automática para garantir consistência de horário e cobertura total.
