#!/usr/bin/env python3
"""
Diagnóstico de scraping para dealers especializados em AC.
Testa: disponibilidade, extração DOM, JSON-LD, problemas de bloqueio.
"""

import json
import re
from typing import Dict, List, Tuple
from datetime import datetime
import requests
from bs4 import BeautifulSoup

# Configurações dos dealers (extraído de scrapers/dealers.py)
DEALER_CONFIGS = {
    "Frigelar": {"url": "https://www.frigelar.com.br/split-inverter/c", "pagination": "vtex"},
    "CentralAr": {"url": "https://www.centralar.com.br/ar-condicionado/inverter/c/INVERTER", "pagination": "vtex"},
    "PoloAr": {"url": "https://www.poloar.com.br/ar-condicionado/inverter?category-1=ar-condicionado&category-2=inverter&fuzzy=0&operator=and&facets=category-1%2Ccategory-2%2Cfuzzy%2Coperator&sort=score_desc&page=0", "pagination": "param_zero"},
    "GoCompras": {"url": "https://www.gocompras.com.br/ar-condicionado/split-hi-wall/", "pagination": "query"},
    "FrioPecas": {"url": "https://www.friopecas.com.br/ar-condicionado/ar-condicionado-split-inverter", "pagination": "vtex"},
    "WebContinental": {"url": "https://www.webcontinental.com.br/climatizacao/ar-condicionado/ar-condicionado-split-hi-wall", "pagination": "vtex"},
    "Dufrio": {"url": "https://www.dufrio.com.br/ar-condicionado/ar-condicionado-split-inverter", "pagination": "vtex"},
    "Leveros": {"url": "https://www.leveros.com.br/ar-condicionado/inverter", "pagination": "vtex"},
    "ArCerto": {"url": "https://www.arcerto.com/categoria/ar-condicionado-inverter/", "pagination": "woocommerce"},
    "Climario": {"url": "https://www.climario.com.br/ar-condicionado?initialMap=c&initialQuery=ar-condicionado&map=category-1,category-2&query=/ar-condicionado/ar-condicionado-hi-wall-inverter&order=OrderByTopSaleDESC&searchState", "pagination": "vtex"},
    "NorteRefrigeracao": {"url": "https://www.norterefrigeracao.com.br/ar-condicionado/", "pagination": "query"},
    "UnicaAR": {"url": "https://www.unicaarcondicionado.com.br/ar-condicionado", "pagination": "vtex", "on_hold": True},
}

DEALERS_TO_TEST = list(DEALER_CONFIGS.keys())

def test_url_availability(url: str) -> Tuple[bool, str, int]:
    """Testa se URL é acessível via HEAD request."""
    try:
        resp = requests.head(url, timeout=10, allow_redirects=True)
        return resp.status_code == 200, resp.url, resp.status_code
    except requests.ConnectionError:
        return False, url, 0
    except Exception as e:
        return False, url, -1

def test_html_extraction(url: str) -> Tuple[int, int, List[str], str]:
    """
    Testa extração HTML básica:
    - Número de produtos encontrados
    - JSON-LD Products
    - Problemas detectados
    """
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        resp = requests.get(url, timeout=15, headers=headers)
        soup = BeautifulSoup(resp.text, "html.parser")

        # Detectar produtos por seletores genéricos
        product_items = soup.select(
            '[class*="product-card"], [class*="ProductCard"], li[class*="product-item"], '
            'div[class*="product-item"], [data-product-id], [data-sku], '
            '.pdc_product-item, .cardprod, article[class*="product"]'
        )

        # Extrair JSON-LD
        jsonld_count = 0
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "{}")
                if isinstance(data, dict):
                    if data.get("@type") == "Product":
                        jsonld_count += 1
                    elif data.get("@type") == "ItemList":
                        items = data.get("itemListElement", [])
                        jsonld_count += len(items)
                elif isinstance(data, list):
                    jsonld_count += sum(1 for item in data if item.get("@type") == "Product")
            except:
                pass

        # Detectar problemas
        problems = []
        text = soup.get_text().lower()

        if "access denied" in text or "please wait" in text:
            problems.append("WAF/Anti-bot detectado (Access Denied)")
        if "valide seu acesso" in text or "insira um cep" in text:
            problems.append("CEP/Sessão exigida (Frigelar OCC)")
        if "cloudflare" in text:
            problems.append("Cloudflare Challenge detectado")
        if "403" in resp.text or "forbidden" in text:
            problems.append("HTTP 403 Forbidden")
        if len(product_items) == 0 and jsonld_count == 0:
            problems.append("Nenhum produto detectado (DOM + JSON-LD vazios)")
        if len(product_items) < 3 and jsonld_count < 3:
            problems.append(f"Baixa contagem de produtos (DOM: {len(product_items)}, JSON-LD: {jsonld_count})")

        return len(product_items), jsonld_count, problems, resp.url

    except requests.Timeout:
        return 0, 0, ["Timeout na requisição (>15s)"], url
    except Exception as e:
        return 0, 0, [f"Erro na requisição: {str(e)}"], url

def main():
    print("\n" + "="*90)
    print("DIAGNÓSTICO DE SCRAPERS DE DEALERS - RAC Position Tracker")
    print(f"Data: {datetime.now().strftime('%Y-%m-%d %H:%M:%S BRT')}")
    print("="*90 + "\n")

    results = {}

    for dealer_name in DEALERS_TO_TEST:
        config = DEALER_CONFIGS.get(dealer_name)
        if not config:
            print(f"❌ {dealer_name}: Dealer não encontrado")
            continue

        url = config.get("url", "")
        if not url:
            print(f"❌ {dealer_name}: URL não configurada")
            continue

        # Status "on_hold"
        if config.get("on_hold"):
            print(f"⏸️  {dealer_name}: Em HOLD (domínio não resolvido)")
            results[dealer_name] = {
                "status": "on_hold",
                "url": url,
                "reason": "Domínio não resolvido ou não validado"
            }
            continue

        print(f"🔍 Testando {dealer_name}...")

        # 1. Teste de disponibilidade
        available, actual_url, status_code = test_url_availability(url)

        if not available:
            print(f"   ❌ URL indisponível (HTTP {status_code})")
            results[dealer_name] = {
                "status": "unavailable",
                "url": url,
                "status_code": status_code,
                "reason": f"HTTP {status_code} - URL não acessível"
            }
            continue

        print(f"   ✅ URL acessível (HTTP {status_code})")

        # 2. Teste de extração HTML
        dom_count, jsonld_count, problems, final_url = test_html_extraction(url)

        # Determinar status
        status = "unknown"
        if problems:
            if "Access Denied" in problems or "WAF" in problems:
                status = "blocked"
            elif "nenhum produto" in problems[0].lower():
                status = "extraction_failed"
            elif any("baixa contagem" in p.lower() for p in problems):
                status = "low_extraction"
            else:
                status = "problematic"
        elif dom_count >= 10 or jsonld_count >= 10:
            status = "working"
        elif dom_count >= 3 or jsonld_count >= 3:
            status = "partial"
        else:
            status = "extraction_failed"

        results[dealer_name] = {
            "status": status,
            "url": url,
            "dom_items": dom_count,
            "jsonld_items": jsonld_count,
            "problems": problems,
            "pagination": config.get("pagination", "unknown"),
            "max_pages": config.get("max_pages", 1),
            "special_config": {
                k: v for k, v in config.items()
                if k in ["requires_cep", "ajax_prices", "wait_for_js", "prefer_jsonld", "vtex_split_price"]
                and v is not False
            }
        }

        # Log resultado
        emoji = "✅" if status == "working" else "⚠️ " if status == "partial" else "❌"
        print(f"   {emoji} Status: {status}")
        print(f"   📊 DOM: {dom_count} itens | JSON-LD: {jsonld_count} itens")
        if problems:
            for problem in problems:
                print(f"   ⚠️  {problem}")
        print()

    # Resumo por status
    print("\n" + "="*90)
    print("RESUMO POR STATUS")
    print("="*90)

    by_status = {}
    for dealer, result in results.items():
        status = result["status"]
        if status not in by_status:
            by_status[status] = []
        by_status[status].append(dealer)

    for status in ["working", "partial", "low_extraction", "extraction_failed", "blocked", "unavailable", "on_hold", "unknown"]:
        if status in by_status:
            dealers = by_status[status]
            emoji_map = {
                "working": "✅",
                "partial": "⚠️ ",
                "low_extraction": "⚠️ ",
                "extraction_failed": "❌",
                "blocked": "🚫",
                "unavailable": "🔴",
                "on_hold": "⏸️ ",
                "unknown": "❓"
            }
            print(f"\n{emoji_map.get(status, '?')} {status.upper()} ({len(dealers)})")
            for dealer in dealers:
                print(f"   • {dealer}")

    # Detalhes por dealer
    print("\n" + "="*90)
    print("DETALHES POR DEALER")
    print("="*90)

    for dealer in DEALERS_TO_TEST:
        if dealer not in results:
            continue

        result = results[dealer]
        print(f"\n{dealer}")
        print("-" * 50)
        print(f"Status: {result['status']}")
        print(f"URL: {result['url']}")

        if "dom_items" in result:
            print(f"DOM Products: {result['dom_items']}")
            print(f"JSON-LD Products: {result['jsonld_items']}")
            print(f"Pagination: {result['pagination']}")
            print(f"Max Pages: {result['max_pages']}")

        if result.get("special_config"):
            print(f"Special Config: {result['special_config']}")

        if result.get("problems"):
            print("Problems:")
            for p in result["problems"]:
                print(f"  • {p}")

        if result.get("reason"):
            print(f"Reason: {result['reason']}")

    # Gerar relatório JSON
    report_file = "diagnostico_dealers_report.json"
    with open(report_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"\n✅ Relatório salvo em: {report_file}")

if __name__ == "__main__":
    main()
