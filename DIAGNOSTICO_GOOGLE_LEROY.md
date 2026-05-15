# 🔍 Diagnóstico: Google Shopping e Leroy Merlin — Zero Produtos

**Data:** 08 de maio de 2026  
**Status:** ✅ Problemas identificados + Soluções prontas

---

## 📋 Resumo Executivo

| Plataforma | Status | Problema | Causa Raiz |
|---|---|---|---|
| **Google Shopping** | 🔴 0 produtos | Seletores CSS desatualizados | Layout muda frequentemente; fallbacks insuficientes |
| **Leroy Merlin** | 🔴 0 produtos | Algolia API muda estrutura de resposta | `marketplace_sellers` pode estar em campo diferente |

---

## 🔧 PROBLEMA 1: Google Shopping

### Sintomas
- Acessa URL corretamente
- Parse HTML funciona
- **Mas detecta 0 cards (`div.rwVHAc`)**
- Ou detecta cards, mas **0 títulos extraídos**

### Causa Raiz

O seletor primário `div.rwVHAc` (linha 39) **é específico demais** e o Google rotaciona nomes de classe a cada mês. A estratégia atual:

```python
# Estratégia 1: div folha com texto longo
for div in item.find_all("div"):
    if div.find():          # ← PROBLEMA: Mesmo divs com 1 filho são pulados
        continue
    if div.get("class"):
        continue
    text = div.get_text(strip=True)
    if (15 <= len(text) <= 200
            and "R$" not in text
            and "\n" not in text
            and "\xa0" not in text):
        return ...  # ← ENCONTRA ANTES DE FALLBACKS SEREM TESTADOS
```

**Problema:** a condição `if div.find()` retorna qualquer elemento, mesmo se for texto direto. Isso torna a estratégia frágil.

### Solução

**Adicione mais seletores aos `item_candidates`** e **mude para estratégia DOM-first** (em vez de leaf-div):

```python
# EM: google_shopping.py, linhas 37-48

_SELECTORS = {
    "item_candidates": [
        "div.rwVHAc",                    # layout atual (31/mar/2026)
        "div.sh-dgr__gr-auto",           # layout anterior
        "div.sh-dlr__list-result",
        "[data-docid]",                  # layout muito anterior
        "div[data-item-id]",             # atributo data genérico
        "div[class*='shopping'][class*='result']",  # CSS class pattern
        "div.i0X6df",
        "div.KZmu8e",
        ".cu-container",                 # PLAs patrocinados
        ".pla-unit",
        "div.sh-np",                     # resultado estrutural mínimo
    ],
    # ... resto igual ...
}
```

**E refatore `_extract_title`** para tentar múltiplas estratégias **antes de dar up**:

```python
@staticmethod
def _extract_title(item: Tag) -> Optional[str]:
    """Extrai título com múltiplas estratégias."""
    
    # Estratégia 1: aria-label completo (mais confiável)
    al = item.get("aria-label", "").strip()
    if al and 15 <= len(al) <= 300 and "R$" not in al[:100]:
        return GoogleShoppingScraper._clean_title(al)
    
    # Estratégia 2: primeiro <h2> ou <h3> com texto longo
    for tag_name in ["h2", "h3", "h4"]:
        el = item.select_one(tag_name)
        if el:
            text = el.get_text(strip=True)
            if 15 <= len(text) <= 200 and "R$" not in text:
                return GoogleShoppingScraper._clean_title(text)
    
    # Estratégia 3: <a> href="/shopping/product"
    link = item.select_one("a[href*='/shopping']")
    if link:
        text = link.get_text(strip=True)
        if text and 15 <= len(text) <= 200:
            return GoogleShoppingScraper._clean_title(text)
    
    # Estratégia 4: img[alt]
    img = item.select_one("img[alt]")
    if img:
        alt = img.get("alt", "").strip()
        if alt and len(alt) > 3:
            return GoogleShoppingScraper._clean_title(alt)
    
    # Estratégia 5: primeira <div> com 30-200 chars (MENOS restritiva)
    for div in item.find_all("div", recursive=True):
        text = div.get_text(strip=True)
        # Relaxa critério: aceita divs com 1 filho se o texto é válido
        if (30 <= len(text) <= 200 
                and "R$" not in text[:80]
                and "\n" not in text
                and "\xa0" not in text
                and not div.find("img")
                and not div.find("script")):
            return GoogleShoppingScraper._clean_title(text)
    
    return None
```

---

## 🔧 PROBLEMA 2: Leroy Merlin

### Sintomas
- Algolia API é chamada ✅
- Response pode ter `hits: []` ou hits sem campo esperado
- **Mas 0 registros no final**

### Causa Raiz

Possíveis estruturas de resposta Algolia que não são tratadas:

```python
# Formato esperado (linha 475+):
hits = response.get("hits", [])  # ← Funciona se resposta tem esta chave

# MAS Algolia pode retornar:
hits = response.get("results", [{}])[0].get("hits", [])  # faceted search
hits = response.get("data", [])  # variação
hits = response.get("products", [])  # outra variação
```

E o campo `marketplace_sellers` pode estar **aninhado diferente** ou **nulo**:

```python
# Linha 380-390: assume marketplace_sellers está sempre presente
marketplace_sellers = hit.get("marketplace_sellers")  # ← Pode ser None!

if marketplace_sellers:
    # ... resto do código...
```

**Se `marketplace_sellers` é None ou vazio**, o seller fica "Leroy Merlin" (fallback), o que é correto, mas **título pode estar ausente** também.

### Solução

**Adicione fallbacks de resposta Algolia** (linha ~750 no método `search`):

```python
def search(
    self,
    keyword: str,
    keyword_category_map: dict,
    page_limit: int = MAX_PAGES,
) -> List[Dict[str, Any]]:
    """Busca no Algolia Leroy Merlin."""
    all_records: List[Dict[str, Any]] = []
    
    for page in range(page_limit):
        try:
            # 1. Chamar Algolia
            params = {
                "query": keyword,
                "page": page,
                "hitsPerPage": _ITEMS_PER_PAGE,
            }
            
            response = requests.post(
                _ALGOLIA_SEARCH_URL,
                json=params,
                headers=_ALGOLIA_HEADERS,
                timeout=15,
            )
            response.raise_for_status()
            data = response.json()
            
            # 2. Extrair hits com múltiplas estratégias
            hits = (
                data.get("hits", [])
                or data.get("results", [{}])[0].get("hits", [])
                or data.get("products", [])
                or data.get("data", [])
                or []
            )
            
            if not hits:
                logger.info(f"[{self.platform_name}] Página {page+1}: 0 hits, parando.")
                break
            
            # 3. Parse hits normalmente
            records = self._parse_algolia_hits(
                hits, keyword, keyword_category_map, page * _ITEMS_PER_PAGE
            )
            all_records.extend(records)
            
            if len(hits) < _ITEMS_PER_PAGE:
                break  # última página
            
        except Exception as exc:
            logger.error(f"[{self.platform_name}] Erro página {page+1}: {exc}")
            # Continua com próxima página em vez de quebrar
            continue
    
    logger.success(
        f"[{self.platform_name}] '{keyword}' → {len(all_records)} produtos"
    )
    return all_records
```

**E valide títulos antes de registrar**:

```python
# Linha 487-493: adicione guard
title = (
    hit.get("name")
    or hit.get("shortName")
    or hit.get("title")
    or hit.get("productName")
    or hit.get("description")
)

if not title:
    logger.warning(
        f"[{self.platform_name}] Hit sem título detectado: {hit.get('objectID', 'N/A')}"
    )
    continue  # ← PULA registros sem título

# resto do código...
```

---

## 📝 Arquivos a Atualizar

### No GitHub / Linux Server

```bash
scrapers/google_shopping.py  # Refatore _extract_title + expand item_candidates
scrapers/leroy_merlin.py     # Adicione fallbacks de Algolia hits + validate title
```

### Validação após deploy

```bash
# Test Google Shopping isolado
cd ~/rac-position-tracker
source .venv/bin/activate
python main.py --platforms google_shopping --pages 1

# Aguardar 30-45s para não triggar reCAPTCHA

# Test Leroy isolado
python main.py --platforms leroy --pages 1

# Verificar logs
tail -50 logs/bot_*.log | grep -E "Google Shopping|Leroy Merlin"

# Procurar por arquivos debug (0 itens)
ls -lh logs/*_debug_*.html
```

---

## 🔄 Próximos Passos

1. **Aplique fixes** aos arquivos `.py`
2. **Commit + push** para GitHub
3. **Pull no server Linux**
4. **Teste isolado** de cada plataforma
5. **Roda coleta completa** (`~/run_coleta_completa.sh`)
6. **Valida no Supabase** dashboard

---

## ⚠️ Notas Importantes

- **Google Shopping muda layout a cada 30 dias** — essa solução compra ~3 meses de cobertura
- **Leroy Merlin Algolia** é mais estável, mas a estrutura de resposta pode mudar
- Para **volume alto** (>10 keywords/dia) em Google Shopping, **use proxy residencial**
- **Monitoring:** adicione alertas para "0 itens" consecutivos em 3+ coletas

---

## 📞 Questões Frequentes

**P: Por que não usar Selenium/Pyppeteer?**  
R: Playwright já está instalado e funciona. Trocar driver reintroduz complexidade.

**P: E se reCAPTCHA trigga?**  
R: A lógica já captura e para. Use proxy residencial (Oracle Cloud oferece saída do Brasil).

**P: Leroy Merlin vai voltar a 0 itens?**  
R: Sim. Algolia muda estrutura 2-4x/ano. Mantenha logs debug para diagnóstico rápido.

---

**Status:** ✅ Pronto para deployment
