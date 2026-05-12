# Soluções Técnicas para Dealers — Guia de Implementação

**Data:** 2026-05-12  
**Versão:** 1.0  
**Status:** Recomendações para implementação

---

## 1. Raiz Causa: WAF Bloqueio Universal

### O Problema Técnico

```
HTTP GET via requests library
├─ Headers padrão → Detectado como bot
├─ Sem cookies sessão → Bloqueado
├─ User-Agent básico → Rejeitado
└─ Sem JavaScript → Access Denied

Resultado: HTTP 403 para todos os 11 dealers
```

### Por que Playwright Resolve

```
HTTP GET via Playwright (Chromium real)
├─ Headers legitimósPlatform
├─ Executa JavaScript → Cookies setting automático
├─ User-Agent real → Legítimo
├─ Simula navegador real → WAF aprova
└─ Renderização completa → Preços e produtos visíveis

Resultado: HTTP 200 + HTML renderizado
```

---

## 2. Implementação: 3 Camadas

### Camada 1: BaseScraper — Setup Playwright

**Arquivo:** `scrapers/base.py`

```python
class BaseScraper:
    async def __aenter__(self):
        # Existente
        self._browser = await self._playwright.chromium.launch(
            headless=self.headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",  # Importante para 1GB RAM
            ]
        )
        
        # NOVO: Adicionar stealth injetável
        self._stealth_js = """
        Object.defineProperty(navigator, 'webdriver', { get: () => false });
        window.chrome = { runtime: {} };
        Object.defineProperty(navigator, 'plugins', { get: () => [] });
        Object.defineProperty(navigator, 'languages', { get: () => ['pt-BR', 'pt'] });
        """
        
        self._context = await self._browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            locale="pt-BR",
            timezone_id="America/Sao_Paulo",
            viewport={"width": 1280, "height": 720},
        )
        
        # Injetar stealth em todas as páginas
        await self._context.add_init_script(self._stealth_js)
        
        # Adicionar listener para WAF detection
        self._context.on("page", lambda page: page.on("framenavigated", self._check_waf_block))
        
        self._page = await self._context.new_page()
        return self

    async def _check_waf_block(self, frame):
        """Detecta bloqueios de WAF e loga para diagnóstico."""
        try:
            content = await frame.content()
            if "403" in content or "Access Denied" in content:
                logger.warning(f"WAF block detectado em {frame.url}")
        except:
            pass

    async def _wait_for_products(self, timeout: int = 10000):
        """Aguarda carregamento de produtos com detecção de seletores."""
        selectors = [
            # VTEX IO
            'article[class*="vtex-product-summary"]',
            # VTEX legacy
            'li.product-summary',
            # WooCommerce
            'ul.products li.product',
            # Genérico
            '[class*="product-card"]',
            '[data-sku]',
            '.pdc_product-item',  # SAP Hybris (CentralAr)
        ]
        
        # Aguardar QUALQUER um dos seletores
        try:
            await self._page.wait_for_selector(
                ", ".join(selectors),
                timeout=timeout
            )
        except:
            logger.warning(f"Nenhum seletor encontrado após {timeout}ms")
```

### Camada 2: DealerScraper — Lógica Específica

**Arquivo:** `scrapers/dealers.py`

```python
class DealerScraper(BaseScraper):
    
    async def search(self, dealer_name: str, keyword: str = "", page_limit: int = 1) -> List[Dict]:
        """
        Busca produtos em dealer específico.
        
        Args:
            dealer_name: Nome do dealer (ex: "Frigelar", "CentralAr")
            keyword: (ignorado para dealers — URL é fixa)
            page_limit: Número de páginas a coletar
        
        Returns:
            Lista de produtos com preços e detalhes
        """
        self._current_dealer = dealer_name
        self.platform_name = f"Dealer - {dealer_name}"
        
        config = DEALER_CONFIGS.get(dealer_name)
        if not config:
            logger.error(f"Dealer {dealer_name} não configurado")
            return []
        
        all_records = []
        
        for page_num in range(1, page_limit + 1):
            try:
                # 1. Construir URL da página
                page_url = self._build_page_url(
                    config["url"],
                    page_num,
                    config["pagination"]
                )
                
                logger.info(f"[{dealer_name}] Page {page_num}: {page_url}")
                
                # 2. Navegar com Playwright
                await self._page.goto(page_url, wait_until="domcontentloaded", timeout=15000)
                
                # 3. Se requer CEP (Frigelar), injetar
                if config.get("requires_cep"):
                    await self._inject_cep(config.get("default_cep", "01310-100"))
                    await self._page.wait_for_timeout(3000)
                
                # 4. Aguardar renderização JS
                if config.get("wait_for_js"):
                    await self._page.wait_for_timeout(config.get("wait_timeout", 5000))
                
                # 5. Se preços via XHR, aguardar (PoloAr)
                if config.get("ajax_prices"):
                    # Aguardar requisição XHR completar
                    await self._page.wait_for_load_state("networkidle", timeout=15000)
                
                # 6. Esperar produtos renderizarem
                await self._wait_for_products(timeout=config.get("wait_timeout", 10000))
                
                # 7. Extrair HTML renderizado
                html = await self._page.content()
                
                # 8. Parse DOM
                items = self._parse_results_dom(html, config)
                
                # 9. Fallback JSON-LD se config pede
                if not items and config.get("prefer_jsonld"):
                    items = self._parse_results_jsonld(html)
                
                if not items:
                    logger.warning(f"[{dealer_name}] Page {page_num}: 0 produtos encontrados")
                    # Debug dump
                    if page_num == 1:
                        await self._dump_debug_html(html, dealer_name, page_num)
                    break
                
                logger.success(f"[{dealer_name}] Page {page_num}: {len(items)} produtos")
                all_records.extend(items)
                
                # Delay anti-bot entre páginas
                if page_num < page_limit:
                    delay = random.randint(3000, 8000)
                    await self._page.wait_for_timeout(delay)
                    
            except Exception as e:
                logger.error(f"[{dealer_name}] Page {page_num} erro: {e}")
                break
        
        return all_records

    async def _inject_cep(self, cep: str):
        """Injeta CEP em input (Frigelar, etc)."""
        try:
            # Tentar diferentes seletores
            for selector in ['input[name="cep"]', 'input[placeholder*="CEP"]', '[type="text"]']:
                elem = await self._page.query_selector(selector)
                if elem:
                    await elem.fill(cep)
                    await elem.press("Enter")
                    logger.debug(f"CEP injetado: {cep}")
                    return
        except Exception as e:
            logger.warning(f"Erro ao injetar CEP: {e}")

    async def _dump_debug_html(self, html: str, dealer: str, page: int):
        """Salva HTML para debug quando 0 produtos."""
        path = Path(LOGS_DIR) / f"dealer_debug_{dealer}_p{page}.html"
        path.write_text(html, encoding="utf-8")
        logger.debug(f"Debug HTML: {path}")

    def _parse_results_dom(self, html: str, config: Dict) -> List[Dict]:
        """Extrai produtos do DOM renderizado."""
        soup = BeautifulSoup(html, "html.parser")
        
        # 1. Detectar items
        items, selector_used = self._detect_items(
            soup,
            item_selector=config.get("item_selector"),
            item_selector_candidates=config.get("item_selector_candidates"),
        )
        
        if not items:
            return []
        
        logger.debug(f"Seletor utilizado: {selector_used} → {len(items)} items")
        
        # 2. Extrair dados de cada item
        records = []
        for item in items:
            try:
                name = self._extract_text(item, config.get("name_selector"), _SELECTORS["title_candidates"])
                price_str = self._extract_text(item, config.get("price_selector"), _SELECTORS["price_candidates"])
                
                # Tratamento especial para preços concatenados (Dufrio)
                if config.get("vtex_split_price"):
                    price = self._extract_vtex_split_price(item)
                else:
                    price = parse_price(price_str)
                
                # Validação
                if not is_valid_product(name, price):
                    continue
                
                record = {
                    "Produto/SKU": name,
                    "Preço (R$)": price,
                    "Seller/Vendedor": self._extract_text(item, "[class*='seller']", []),
                }
                
                records.append(record)
                
            except Exception as e:
                logger.debug(f"Erro ao extrair item: {e}")
                continue
        
        return records

    def _parse_results_jsonld(self, html: str) -> List[Dict]:
        """Fallback: extrai de JSON-LD."""
        soup = BeautifulSoup(html, "html.parser")
        records = []
        
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "{}")
                
                if isinstance(data, dict) and data.get("@type") == "ItemList":
                    items = data.get("itemListElement", [])
                elif isinstance(data, list):
                    items = [item for item in data if item.get("@type") == "Product"]
                else:
                    items = []
                
                for item in items:
                    if item.get("@type") != "Product":
                        continue
                    
                    name = item.get("name", "")
                    price_obj = item.get("offers", [{}])[0]
                    price = parse_price_brazil(str(price_obj.get("price", "")))
                    
                    if is_valid_product(name, price):
                        records.append({
                            "Produto/SKU": name,
                            "Preço (R$)": price,
                        })
                        
            except Exception as e:
                logger.debug(f"Erro ao extrair JSON-LD: {e}")
        
        logger.debug(f"JSON-LD: {len(records)} produtos extraídos")
        return records
```

### Camada 3: Tratamentos Especiais

```python
# Em scrapers/dealers.py

def _extract_vtex_split_price(self, item: Tag) -> Optional[float]:
    """
    Extrai preço concatenado sem separador (Dufrio).
    Ex: 182900 → 1829.00
    """
    # Procurar por estrutura VTEX split (integer + decimal separados)
    try:
        currency = item.select_one('[class*="currencyContainer"]')
        if not currency:
            return None
        
        # VTEX classicamente coloca:
        # <span class="...currencyInteger...">1829</span>
        # <span class="...currencyDecimal...">,00</span>
        
        integer_elem = item.select_one('[class*="currencyInteger"]')
        decimal_elem = item.select_one('[class*="currencyDecimal"]')
        
        if integer_elem and decimal_elem:
            integer = integer_elem.get_text(strip=True)
            decimal = decimal_elem.get_text(strip=True).replace(",", ".")
            price_str = f"{integer}{decimal}"
            return parse_price(price_str)
        
    except:
        pass
    
    return None
```

---

## 3. Configuração: Adicionar Flags por Dealer

**Arquivo:** `scrapers/dealers.py` → `DEALER_CONFIGS`

```python
DEALER_CONFIGS = {
    "Frigelar": {
        "url": "https://www.frigelar.com.br/split-inverter/c",
        "pagination": "vtex",
        "max_pages": 5,
        # NOVO: Flags para Playwright
        "requires_cep": True,
        "default_cep": "01310-100",
        "wait_for_js": True,
        "wait_timeout": 10000,
        "item_selector": ".product-box-container",
    },
    "CentralAr": {
        "url": "https://www.centralar.com.br/ar-condicionado/inverter/c/INVERTER",
        "pagination": "vtex",
        "max_pages": 5,
        "item_selector": ".pdc_product-item",  # SAP Hybris
        "prefer_jsonld": False,  # JSON-LD é Organization, não Product
        "wait_for_js": True,
        "wait_timeout": 10000,
    },
    "PoloAr": {
        "url": "https://www.poloar.com.br/ar-condicionado/inverter?...",
        "pagination": "param_zero",
        "max_pages": 5,
        "ajax_prices": True,  # XHR carrega preços
        "wait_timeout": 15000,
    },
    "Dufrio": {
        "url": "https://www.dufrio.com.br/ar-condicionado/ar-condicionado-split-inverter",
        "pagination": "vtex",
        "max_pages": 5,
        "vtex_split_price": True,  # Preços concatenados
        "item_selector": ".product-item",
        "prefer_jsonld": True,
    },
}
```

---

## 4. Matriz de Configuração por Tipo

| Dealer | Platform | wait_for_js | ajax_prices | requires_cep | prefer_jsonld | vtex_split |
|--------|----------|------------|-------------|-------------|---------------|-----------|
| Frigelar | OCC | ✅ | ❌ | ✅ | ❌ | ❌ |
| CentralAr | SAP Hybris | ✅ | ❌ | ❌ | ❌ | ❌ |
| PoloAr | Custom | ✅ | ✅ | ❌ | ❌ | ❌ |
| GoCompras | WooCommerce | ✅ | ❌ | ❌ | ❌ | ❌ |
| FrioPecas | VTEX | ✅ | ❌ | ❌ | ❌ | ❌ |
| WebContinental | VTEX | ✅ | ❌ | ❌ | ❌ | ❌ |
| Dufrio | VTEX | ✅ | ❌ | ❌ | ✅ | ✅ |
| Leveros | VTEX IO | ✅ | ❌ | ❌ | ✅ | ❌ |
| ArCerto | WooCommerce | ✅ | ❌ | ❌ | ❌ | ❌ |
| Climario | VTEX | ✅ | ❌ | ❌ | ❌ | ❌ |
| NorteRefrigeracao | Custom | ✅ | ❌ | ❌ | ❌ | ❌ |

---

## 5. Testes Unitários

**Arquivo:** `tests/test_dealers.py`

```python
import pytest
from scrapers.dealers import DealerScraper

@pytest.mark.asyncio
class TestDealerScraper:
    
    async def test_frigelar_with_cep_injection(self):
        """Valida injeção de CEP em Frigelar."""
        async with DealerScraper(headless=False) as scraper:
            items = await scraper.search("Frigelar")
            assert len(items) >= 10, "Frigelar deve retornar 10+ produtos"
            assert all("Preço (R$)" in item for item in items)
    
    async def test_centralar_sap_hybris_selector(self):
        """Valida seletor .pdc_product-item em CentralAr."""
        async with DealerScraper() as scraper:
            items = await scraper.search("CentralAr")
            assert len(items) >= 10
    
    async def test_dufrio_split_price_parsing(self):
        """Valida parsing de preço concatenado."""
        async with DealerScraper() as scraper:
            items = await scraper.search("Dufrio")
            assert len(items) >= 5
            # Verificar formato de preço
            for item in items:
                price = item.get("Preço (R$)")
                assert 500 < price < 10000, f"Preço inválido: {price}"
    
    async def test_poloar_ajax_wait(self):
        """Valida espera de XHR em PoloAr."""
        async with DealerScraper() as scraper:
            items = await scraper.search("PoloAr")
            assert len(items) >= 5
```

---

## 6. Validação em Produção

**Checklist antes de deploy:**

```bash
# 1. Teste local com headless=False (ver navegador)
python main.py --platforms dealers --no-headless --pages 1

# 2. Validar logs
tail -f logs/bot_*.log | grep -E "dealer|Frigelar|CentralAr"

# 3. Testar cada dealer individualmente
python -c "
from scrapers.dealers import DealerScraper
import asyncio

async def test():
    for dealer in ['Frigelar', 'CentralAr', 'Leveros']:
        async with DealerScraper() as scraper:
            items = await scraper.search(dealer)
            print(f'{dealer}: {len(items)} itens')

asyncio.run(test())
"

# 4. Verificar CSV output
head -5 output/rac_monitoramento_*.csv

# 5. Supabase upload
grep -E "ERROR|WARNING|upload" logs/bot_*.log | tail -20
```

---

## 7. Fallbacks e Edge Cases

| Situação | Fallback | Prioridade |
|----------|----------|-----------|
| DOM vazioJSONLD disponível | Usar JSON-LD | Alta |
| CEP exigido | Usar CEP padrão (SP) | Alta |
| Cloudflare Challenge | Usar headless=False | Média |
| Timeout na página | Skip + continuar | Alta |
| Preço não encontrado | Marcar como "N/A" | Baixa |
| Selector não existe | Tentar genérico | Alta |

---

*Documento de referência técnica / Claude — 2026-05-12*
