"""Cliente MCP falso: responde cada tool com a amostra real de catalog/samples/."""
import copy
import json
from pathlib import Path

SAMPLES = Path(__file__).resolve().parent.parent / "nubimetrics" / "catalog" / "samples"


def amostra(nome: str):
    return json.loads((SAMPLES / f"{nome}.json").read_text(encoding="utf-8"))["payload"]


class FakeNubi:
    """Mesma interface de `NubiClient.call` (async)."""

    def __init__(self):
        self.chamadas = 0
        self.log = []
        self.do_cache = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def call(self, tool, args):
        self.chamadas += 1
        self.log.append((tool, args))
        inp = args.get("inp", {})
        if tool == "meli_market_rankings":
            p = copy.deepcopy(amostra(f"meli_market_rankings__{inp['topic']}"))
            p["period_fallback"] = False   # o ETL pede mês fechado
            return p
        if tool == "meli_search_trends_rankings":
            p = copy.deepcopy(amostra(tool)); p["period_fallback"] = False
            return p
        if tool == "meli_publication_explorer":
            # Paginação de verdade: o `total` real (4.057) fica; a página 1 traz
            # 5 anúncios, a 2 traz os outros 5 + 1 repetido, a 3 vem vazia.
            p = copy.deepcopy(amostra(tool))
            pubs = p["publications"]
            pagina = inp.get("page", 1)
            p["publications"] = {1: pubs[:5], 2: pubs[5:] + pubs[:1]}.get(pagina, [])
            return p
        if tool == "meli_category_detail":
            p = copy.deepcopy(amostra(tool)); p["period_applied"] = inp["date"]
            return p
        return amostra(tool)
