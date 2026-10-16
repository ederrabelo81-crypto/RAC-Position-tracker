"""
utils/pricetrack_platforms.py — marketplace do PriceTrack × plataforma das coletas.

O PriceTrack publica o marketplace em CAIXA ALTA com espaços ("MERCADO LIVRE",
"MAGAZINE LUIZA"); as coletas usam o nome canônico ("Mercado Livre", "Magalu").
Morava em `app.py`; saiu para cá (Out/2026) para a auditoria do PriceTrack
(`scripts/pricetrack_audit.py`) usar o MESMO mapa do painel, sem importar o
Streamlit.
"""
from typing import Dict

PT_TO_CANONICAL_PLATFORM: Dict[str, str] = {
    # 7 marketplaces no foco
    "MERCADO LIVRE":  "Mercado Livre",
    "AMAZON":         "Amazon",
    "MAGAZINE LUIZA": "Magalu",
    "CASAS BAHIA":    "Casas Bahia",
    "LEROY MERLIN":   "Leroy Merlin",
    "SHOPEE":         "Shopee",
    # dealers (fora do foco, mas presentes nas coletas — evita split)
    "FERREIRA COSTA": "FerreiraCosta",
    "WEB CONTINENTAL": "WebContinental",
    "CENTRAL AR":     "CentralAr",
    "POLO AR":        "PoloAr",
    "FRIOPEÇAS":      "FrioPecas",
    "CLIMA RIO":      "Climario",
    "G BARBOSA":      "GBarbosa",
    "ADIAS":          "ADias",
    "AR CERTO":       "ArCerto",
    "FRIGELAR":       "Frigelar",
    "DUFRIO":         "Dufrio",
    "LEVEROS":        "Leveros",
    "BEMOL":          "Bemol",
    "FUJIOKA":        "Fujioka",
}

#: Inverso: plataforma das coletas → marketplace do PriceTrack.
CANONICAL_TO_PT_PLATFORM: Dict[str, str] = {
    canon: pt for pt, canon in PT_TO_CANONICAL_PLATFORM.items()
}
