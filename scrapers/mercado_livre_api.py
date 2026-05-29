"""
scrapers/mercado_livre_api.py — Mercado Livre via API REST oficial (OAuth).

Usa api.mercadolibre.com em vez de Playwright — sem bloqueio de IP de cloud,
sem browser, 10x mais rápido. Funciona em Oracle VM, GitHub Actions, etc.

SETUP (único, gratuito):
  1. Acesse developers.mercadolivre.com.br → Criar aplicação
  2. Copie App ID e Secret
  3. Adicione ao .env:   ML_APP_ID=...  ML_APP_SECRET=...
  4. Adicione ao GitHub: Settings → Secrets → ML_APP_ID + ML_APP_SECRET

Endpoints:
  Token:  POST https://api.mercadolibre.com/oauth/token (client_credentials)
  Busca:  GET  https://api.mercadolibre.com/sites/MLB/search?q={kw}&limit=50
  Seller: GET  https://api.mercadolibre.com/users/{seller_id}  (cache local)

Limitações conhecidas:
  - Posição Patrocinada não disponível na API pública (todos marcados como orgânicos)
  - Tags de destaque mapeadas a partir do campo `tags` da resposta
"""

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from config import MAX_PAGES
from scrapers.base import BaseScraper

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
_API_BASE    = "https://api.mercadolibre.com"
_SITE_ID     = "MLB"
_PAGE_SIZE   = 50   # máximo permitido pela API
_DELAY_SECS  = 0.8  # pausa entre requests (evita 429)

# Mapeamento de tags da API → texto legível para "Tag Destaque"
_TAG_MAP: Dict[str, str] = {
    "best_seller_item":       "MAIS VENDIDO",
    "good_value_item":        "BOM VALOR",
    "loyalty_discount_item":  "DESCONTO FIDELIDADE",
    "brand_verified":         "MARCA VERIFICADA",
    "deal_of_the_day_item":   "OFERTA DO DIA",
    "lightning_deal_item":    "OFERTA RELÂMPAGO",
}


class MLAPIScraper(BaseScraper):
    """
    Scraper do Mercado Livre usando a API REST com OAuth client_credentials.

    Não inicia browser — substitui Playwright por requests HTTP.
    Compatível com a interface BaseScraper (_build_record, context manager).
    Requer ML_APP_ID e ML_APP_SECRET no .env ou variáveis de ambiente.
    """

    platform_name = "Mercado Livre"

    def __init__(self, headless: bool = True) -> None:
        # headless ignorado — este scraper não usa browser
        super().__init__(headless=True)
        self._session = requests.Session()
        self._session.headers.update({
            "Accept":          "application/json",
            "Accept-Language": "pt-BR,pt;q=0.9",
            "User-Agent":      "RACPositionTracker/1.0",
        })

    # ------------------------------------------------------------------
    # OAuth — authorization_code flow com auto-refresh
    # ------------------------------------------------------------------

    _OAUTH_SESSION_PATH = (
        Path(__file__).parent.parent / "utils" / "sessions" / "mercadolivre_oauth.json"
    )

    def _get_access_token(self) -> Optional[str]:
        """
        Obtém access_token válido para busca.

        Prioridade:
          1. refresh_token salvo em utils/sessions/mercadolivre_oauth.json
          2. Falha com instrução clara se sessão não existir

        Setup único: python scripts/ml_oauth_setup.py (abre browser para autorização)
        """
        app_id     = os.environ.get("ML_APP_ID", "").strip()
        app_secret = os.environ.get("ML_APP_SECRET", "").strip()

        if not app_id or not app_secret:
            logger.error(
                f"[{self.platform_name}] ML_APP_ID / ML_APP_SECRET não configurados no .env"
            )
            return None

        if not self._OAUTH_SESSION_PATH.exists():
            logger.error(
                f"[{self.platform_name}] Sessão OAuth não encontrada. "
                "Execute UMA VEZ: python scripts/ml_oauth_setup.py"
            )
            return None

        try:
            data = json.loads(self._OAUTH_SESSION_PATH.read_text())
        except Exception as exc:
            logger.error(f"[{self.platform_name}] Erro ao ler sessão OAuth: {exc}")
            return None

        refresh_token = data.get("refresh_token")
        if not refresh_token:
            logger.error(
                f"[{self.platform_name}] refresh_token ausente na sessão. "
                "Execute novamente: python scripts/ml_oauth_setup.py"
            )
            return None

        try:
            resp = self._session.post(
                f"{_API_BASE}/oauth/token",
                data={
                    "grant_type":    "refresh_token",
                    "client_id":     app_id,
                    "client_secret": app_secret,
                    "refresh_token": refresh_token,
                },
                timeout=15,
            )
            resp.raise_for_status()
            tokens = resp.json()
            new_access  = tokens["access_token"]
            new_refresh = tokens.get("refresh_token", refresh_token)

            # Persiste o novo refresh_token (ML rotaciona em cada uso)
            data["access_token"]  = new_access
            data["refresh_token"] = new_refresh
            data["refreshed_at"]  = datetime.now().isoformat()
            self._OAUTH_SESSION_PATH.write_text(json.dumps(data, indent=2))

            logger.info(f"[{self.platform_name}] OAuth token renovado via refresh_token")
            return new_access

        except Exception as exc:
            logger.error(
                f"[{self.platform_name}] Falha ao renovar token: {exc}. "
                "Execute novamente: python scripts/ml_oauth_setup.py"
            )
            return None

    # ------------------------------------------------------------------
    # Context manager — sem browser para iniciar/fechar
    # ------------------------------------------------------------------

    def _launch(self) -> None:
        logger.info(f"[{self.platform_name}] API REST — obtendo OAuth token...")
        token = self._get_access_token()
        if token:
            self._session.headers["Authorization"] = f"Bearer {token}"
        else:
            logger.error(
                f"[{self.platform_name}] Sem token OAuth — coleta vai falhar com 403. "
                "Configure ML_APP_ID e ML_APP_SECRET."
            )

    def _close(self) -> None:
        self._session.close()

    # ------------------------------------------------------------------
    # Chamadas à API com retry
    # ------------------------------------------------------------------

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10), reraise=True)
    def _get_json(self, url: str, params: Optional[dict] = None) -> dict:
        resp = self._session.get(url, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Parse de um item da API → record
    # ------------------------------------------------------------------

    def _parse_item(
        self,
        item: dict,
        position_general: int,
        organic_counter: int,
        keyword: str,
        keyword_category_map: dict,
    ) -> Dict[str, Any]:
        title = item.get("title")
        price = item.get("price")  # já é float na API

        # Seller — usa nickname diretamente da resposta de busca.
        # Chamar /users/{id} por item dobraria o número de requests sem ganho real.
        seller_info = item.get("seller") or {}
        seller_name = seller_info.get("nickname") or None

        # Tipo de seller: official_store_id != null → Loja Oficial (1P/marca).
        # caso contrário, vendedor 3P comum do marketplace.
        official_store = item.get("official_store_id")
        tipo_seller = "Loja Oficial" if official_store else "3P"

        # Reputação do seller (quando a busca traz seller.seller_reputation)
        reputation = (seller_info.get("seller_reputation") or {})
        reputacao_seller = (
            reputation.get("level_id") or reputation.get("power_seller_status") or None
        )

        # Fulfillment: logistic_type="fulfillment" = Mercado Envios Full
        shipping       = item.get("shipping") or {}
        is_fulfillment = shipping.get("logistic_type") == "fulfillment"

        # Rating
        reviews      = item.get("reviews") or {}
        rating       = reviews.get("rating_average")
        review_count = reviews.get("total_ratings")

        # Tag de destaque — usa o primeiro tag reconhecido
        tags: list = item.get("tags") or []
        tag_destaque = next((_TAG_MAP[t] for t in tags if t in _TAG_MAP), None)

        # Posição — API não expõe patrocinados; todos tratados como orgânicos
        return self._build_record(
            keyword=keyword,
            keyword_category_map=keyword_category_map,
            title=title,
            position_general=position_general,
            position_organic=organic_counter,
            position_sponsored=None,
            price_float=float(price) if price is not None else None,
            seller=seller_name,
            buy_box_seller=seller_name,
            tipo_seller=tipo_seller,
            reputacao_seller=reputacao_seller,
            is_fulfillment=is_fulfillment,
            rating=float(rating) if rating is not None else None,
            review_count=int(review_count) if review_count is not None else None,
            tag_destaque=tag_destaque,
        )

    # ------------------------------------------------------------------
    # Método público — ponto de entrada
    # ------------------------------------------------------------------

    def search(
        self,
        keyword: str,
        keyword_category_map: dict,
        page_limit: int = MAX_PAGES,
    ) -> List[Dict[str, Any]]:
        """
        Busca uma keyword no ML via API REST por até `page_limit` páginas.

        Returns:
            Lista de records no formato padrão do DataFrame.
        """
        all_records: List[Dict[str, Any]] = []

        for page in range(page_limit):
            offset = page * _PAGE_SIZE
            url    = f"{_API_BASE}/sites/{_SITE_ID}/search"
            params = {"q": keyword, "limit": _PAGE_SIZE, "offset": offset}

            logger.info(
                f"[{self.platform_name}] Página {page+1}/{page_limit} "
                f"(offset={offset}) → {keyword!r}"
            )

            try:
                data    = self._get_json(url, params)
                results = data.get("results", [])
            except Exception as exc:
                logger.error(f"[{self.platform_name}] Erro na API: {exc}")
                break

            if not results:
                logger.warning(
                    f"[{self.platform_name}] Página {page+1} sem resultados. Encerrando."
                )
                break

            for idx, item in enumerate(results):
                pos_general = offset + idx + 1
                pos_organic = pos_general  # API não distingue patrocinados
                record = self._parse_item(
                    item=item,
                    position_general=pos_general,
                    organic_counter=pos_organic,
                    keyword=keyword,
                    keyword_category_map=keyword_category_map,
                )
                all_records.append(record)

            logger.debug(
                f"[{self.platform_name}] {len(results)} itens parseados "
                f"(página {page+1})"
            )

            if page < page_limit - 1:
                time.sleep(_DELAY_SECS)

        logger.success(
            f"[{self.platform_name}] '{keyword}' → {len(all_records)} produtos coletados"
        )
        return all_records
