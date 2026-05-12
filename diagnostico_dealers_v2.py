#!/usr/bin/env python3
"""
Diagnóstico v2 — Testa dealers com métodos avançados:
1. curl com User-Agent (WAF bypass)
2. Análise de HTML estático quando possível
3. Detecção de padrões de bloqueio
"""

import json
import re
import subprocess
from typing import Dict, List, Tuple
from datetime import datetime
from bs4 import BeautifulSoup

# Configurações dos dealers
DEALER_CONFIGS = {
    "Frigelar": {"url": "https://www.frigelar.com.br/split-inverter/c", "pagination": "vtex", "type": "OCC/Knockout.js"},
    "CentralAr": {"url": "https://www.centralar.com.br/ar-condicionado/inverter/c/INVERTER", "pagination": "vtex", "type": "VTEX IO (SAP Hybris)"},
    "PoloAr": {"url": "https://www.poloar.com.br/ar-condicionado/inverter?category-1=ar-condicionado&category-2=inverter&fuzzy=0&operator=and&facets=category-1%2Ccategory-2%2Cfuzzy%2Coperator&sort=score_desc&page=0", "pagination": "param_zero", "type": "Custom"},
    "GoCompras": {"url": "https://www.gocompras.com.br/ar-condicionado/split-hi-wall/", "pagination": "query", "type": "WooCommerce"},
    "FrioPecas": {"url": "https://www.friopecas.com.br/ar-condicionado/ar-condicionado-split-inverter", "pagination": "vtex", "type": "VTEX"},
    "WebContinental": {"url": "https://www.webcontinental.com.br/climatizacao/ar-condicionado/ar-condicionado-split-hi-wall", "pagination": "vtex", "type": "VTEX"},
    "Dufrio": {"url": "https://www.dufrio.com.br/ar-condicionado/ar-condicionado-split-inverter", "pagination": "vtex", "type": "VTEX", "special": "VTEX split price"},
    "Leveros": {"url": "https://www.leveros.com.br/ar-condicionado/inverter", "pagination": "vtex", "type": "VTEX", "special": "JSON-LD priority"},
    "ArCerto": {"url": "https://www.arcerto.com/categoria/ar-condicionado-inverter/", "pagination": "woocommerce", "type": "WooCommerce", "special": "CF challenge on page 2+"},
    "Climario": {"url": "https://www.climario.com.br/ar-condicionado?initialMap=c&initialQuery=ar-condicionado&map=category-1,category-2&query=/ar-condicionado/ar-condicionado-hi-wall-inverter&order=OrderByTopSaleDESC&searchState", "pagination": "vtex", "type": "VTEX"},
    "NorteRefrigeracao": {"url": "https://www.norterefrigeracao.com.br/ar-condicionado/", "pagination": "query", "type": "Custom"},
    "UnicaAR": {"url": "https://www.unicaarcondicionado.com.br/ar-condicionado", "pagination": "vtex", "type": "VTEX", "on_hold": True},
}

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"

def test_with_curl(url: str) -> Tuple[int, str, str]:
    """Testa URL com curl (melhor suporte a WAF)."""
    try:
        result = subprocess.run(
            [
                "curl",
                "-s",
                "-w", "%{http_code}",
                "-L",
                "-A", USER_AGENT,
                "--max-time", "10",
                url
            ],
            capture_output=True,
            text=True,
            timeout=15
        )

        status_code = int(result.stdout[-3:]) if len(result.stdout) >= 3 else 0
        html = result.stdout[:-3] if len(result.stdout) > 3 else ""

        return status_code, html, ""
    except subprocess.TimeoutExpired:
        return 0, "", "Timeout"
    except Exception as e:
        return 0, "", str(e)

def analyze_html(html: str) -> Tuple[int, int, List[str]]:
    """Analisa HTML para detectar produtos e problemas."""
    try:
        soup = BeautifulSoup(html, "html.parser")
    except:
        return 0, 0, ["Erro ao fazer parse do HTML"]

    # Detectar produtos
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

    if "403" in html or "forbidden" in text:
        problems.append("HTTP 403 Forbidden detectado")
    if "access denied" in text or "please wait" in text:
        problems.append("WAF Block: Access Denied")
    if "cloudflare" in html or "cloudflare challenge" in text:
        problems.append("Cloudflare Challenge (JS necessário)")
    if "valide seu acesso" in text or "insira um cep" in text:
        problems.append("CEP/Sessão exigida (OCC)")
    if "bot" in text and len(product_items) < 3:
        problems.append("Possível bot detection")
    if len(product_items) == 0 and jsonld_count == 0:
        problems.append("Nenhum produto detectado (DOM + JSON-LD vazios)")
    if len(product_items) > 0:
        problems.append(f"Detectados {len(product_items)} containers (verificar seletores)")
    if jsonld_count > 0:
        problems.append(f"JSON-LD: {jsonld_count} produtos")

    return len(product_items), jsonld_count, problems

def determine_status(status_code: int, dom_items: int, jsonld_items: int, problems: List[str]) -> str:
    """Determina status baseado em análise."""
    if status_code == 403:
        return "blocked_waf"
    if status_code == 0:
        return "timeout_or_connection_error"
    if status_code not in [200, 301, 302]:
        return "http_error"
    if any("JSON-LD" in p and "produto" in p.lower() for p in problems):
        if jsonld_items > 0:
            return "working_via_jsonld"
    if dom_items >= 10 or jsonld_items >= 10:
        return "working"
    if dom_items >= 3 or jsonld_items >= 3:
        return "partial"
    if "nenhum produto" in str(problems).lower():
        return "extraction_failed"
    return "unknown"

def main():
    print("\n" + "="*100)
    print("DIAGNÓSTICO DE DEALERS v2 — Com curl + Análise de WAF")
    print(f"Data: {datetime.now().strftime('%Y-%m-%d %H:%M:%S BRT')}")
    print("="*100 + "\n")

    results = {}

    for dealer_name, config in DEALER_CONFIGS.items():
        if config.get("on_hold"):
            print(f"⏸️  {dealer_name}: Em HOLD - {config.get('url')}")
            results[dealer_name] = {
                "status": "on_hold",
                "url": config["url"],
                "reason": "Domínio não resolvido ou não validado"
            }
            continue

        url = config["url"]
        print(f"\n🔍 {dealer_name}")
        print(f"   URL: {url[:70]}...")
        print(f"   Tipo: {config.get('type', 'Desconhecido')}")

        # Teste com curl
        status_code, html, error = test_with_curl(url)

        if error:
            print(f"   ❌ Erro: {error}")
            results[dealer_name] = {
                "status": "connection_error",
                "url": url,
                "reason": error,
                "type": config.get("type")
            }
            continue

        print(f"   Status HTTP: {status_code}")

        # Análise do HTML
        dom_items, jsonld_items, problems = analyze_html(html)

        # Determinar status
        status = determine_status(status_code, dom_items, jsonld_items, problems)

        results[dealer_name] = {
            "status": status,
            "url": url,
            "http_status": status_code,
            "dom_items": dom_items,
            "jsonld_items": jsonld_items,
            "problems": problems,
            "type": config.get("type"),
            "pagination": config.get("pagination"),
            "special": config.get("special", "")
        }

        # Log resultado
        emoji_map = {
            "working": "✅",
            "working_via_jsonld": "✅",
            "partial": "⚠️ ",
            "extraction_failed": "❌",
            "blocked_waf": "🚫",
            "http_error": "⚠️ ",
            "timeout_or_connection_error": "🔴",
            "on_hold": "⏸️ ",
            "connection_error": "🔴",
            "unknown": "❓"
        }

        emoji = emoji_map.get(status, "?")
        print(f"   {emoji} Status: {status}")
        print(f"   📊 DOM: {dom_items} | JSON-LD: {jsonld_items}")

        if problems:
            print(f"   Issues:")
            for p in problems:
                print(f"     • {p}")

    # Resumo
    print("\n\n" + "="*100)
    print("RESUMO POR STATUS")
    print("="*100)

    by_status = {}
    for dealer, result in results.items():
        status = result["status"]
        if status not in by_status:
            by_status[status] = []
        by_status[status].append(dealer)

    for status in ["working", "working_via_jsonld", "partial", "extraction_failed", "blocked_waf", "http_error", "timeout_or_connection_error", "connection_error", "on_hold", "unknown"]:
        if status in by_status:
            dealers = by_status[status]
            emoji_map = {
                "working": "✅",
                "working_via_jsonld": "✅",
                "partial": "⚠️ ",
                "extraction_failed": "❌",
                "blocked_waf": "🚫",
                "http_error": "⚠️ ",
                "timeout_or_connection_error": "🔴",
                "on_hold": "⏸️ ",
                "connection_error": "🔴",
                "unknown": "❓"
            }
            print(f"\n{emoji_map.get(status, '?')} {status.upper()} ({len(dealers)})")
            for dealer in dealers:
                print(f"   • {dealer}")

    # Salvar relatório
    report_file = "diagnostico_dealers_report.json"
    with open(report_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"\n✅ Relatório salvo em: {report_file}\n")

if __name__ == "__main__":
    main()
