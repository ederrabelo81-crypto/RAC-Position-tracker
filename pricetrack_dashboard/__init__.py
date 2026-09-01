"""
pricetrack_dashboard — dashboard Streamlit de preços RAC 9K/12K.

Lê ao vivo a API do PriceTrack (via ``pricetrack_api``), classifica as ofertas
no peer competitivo (Low/Mid/High = Inverter Lite / AI AirVolution / AI
Ecomaster) e apresenta os tiers de preço + a variação Midea (mín/máx/moda/média).

Camadas:
    peer.py         — contrato do peer (SKUs por tier/capacidade) + matching
    analytics.py    — funções puras de agregação de preço (testável, sem rede)
    data_source.py  — hook ao vivo na API + amostra demo offline
    app.py          — página Streamlit

Entrada: ``streamlit run pricetrack_dashboard/app.py``.
"""
from __future__ import annotations

__all__ = ["peer", "analytics", "data_source"]
