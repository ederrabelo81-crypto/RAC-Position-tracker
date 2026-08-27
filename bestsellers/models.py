"""
bestsellers/models.py — Item de ranking e sua classificação.

Um `BestSellerItem` é UMA posição de UMA lista de mais vendidos num dia.
A classificação (marca, grupo, BTU, tipo) é derivada do título e acontece
aqui, num único lugar, para que Amazon e Shopee produzam exatamente a mesma
taxonomia — sem isso, "% do top 10 da Midea" mede coisas diferentes em cada
plataforma e a série histórica não fecha.

REGRA DURA: campo sem dado fica None. Nada de estimativa, nada de
interpolação — uma célula vazia é informação; um número inventado, não.
"""

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from bestsellers.config import GRUPO_MIDEA, LINHAS_MIDEA, SourceSpec
from utils.seller_names import normalize_seller_name
from utils.attr_parser import norm as _norm_titulo
from utils.attr_parser import parse_btu
from utils.brands import extract_brand

# ---------------------------------------------------------------------------
# Taxonomia de tipo de produto
# ---------------------------------------------------------------------------
# Itens que a categoria "ar condicionado" dos varejistas arrasta junto.
# Casas Bahia e Americanas contaminam o topo do ranking com estes.
#
# A lista é dividida em duas porque casar estes termos como substring solta
# derruba splits de verdade: "…com Controle Remoto" tem CONTROLE, "…Filtro
# Anti-Alérgico" tem FILTRO, "…Autolimpante" tem LIMPA e "Bomba de Calor" tem
# BOMBA. Como títulos Midea/Springer carregam essas palavras com frequência,
# o erro sairia ASSIMÉTRICO — subestimando justamente o KPI do grupo.

# Outro produto, não um ar condicionado. Nunca aparecem num título de AC
# legítimo, então valem em qualquer posição (com fronteira de palavra).
_PRODUTOS_NAO_RAC = (
    "UMIDIFICADOR", "DEPURADOR", "PURIFICADOR", "VENTILADOR", "CLIMATIZADOR",
    "CIRCULADOR", "EXAUSTOR",
)

# Acessório ou insumo. Estes só desqualificam quando são o SUBSTANTIVO
# PRINCIPAL do anúncio — isto é, quando aparecem ANTES do núcleo "ar
# condicionado" no título:
#     "Suporte para Ar Condicionado Split"        → acessório (SUPORTE antes)
#     "Springer Midea Ar Condicionado com Suporte" → ar condicionado
# Testar apenas se o título COMEÇA com o núcleo não serve: em Amazon e Shopee
# o padrão dominante é marca-primeiro ("Springer Midea Ar Condicionado …"), e
# todo item com "Controle Remoto" no fim cairia fora do escopo.
#
# Os fragmentos são regex: o `\w*` marca onde o casamento por prefixo é
# intencional (TUBULAÇÃO, HIGIENIZADOR). As fronteiras dos dois lados também
# são necessárias — `\bCAPA` sem a fronteira final casa CAPACIDADE.
_ACESSORIOS_NAO_RAC = (
    r"SUPORTE", r"CAPA", r"CONTROLE", r"HIGIENIZA\w*", r"LIMPA\w*",
    r"TUBULA\w*", r"DRENO", r"SILICONE", r"FILTRO", r"SPRAY", r"BOMBA",
    r"MANGUEIRA", r"AQUECEDOR", r"MANIFOLD", r"GAS REFRIGERANTE",
    r"KIT\s+INSTALA\w*", r"VACUO", r"MOTOR",
)

# Núcleo que identifica um ar condicionado de verdade, em qualquer posição.
_RE_NUCLEO_AC = re.compile(
    r"\b(?:AR\s*[-\s]?CONDICIONADO|AR\s*[-\s]?COND|CONDICIONADOR\s+DE\s+AR|"
    r"SPLIT|AIR\s+CONDITIONER|HI\s*[-\s]?WALL|HIGH\s*WALL)\b"
)


# Frases que CONTÊM um termo de acessório mas descrevem o próprio aparelho.
# "Bomba de Calor" é como o ciclo reverso (quente/frio) é anunciado, e vem
# na frente do título com frequência: "Bomba de Calor 12000 BTUs Midea Split".
# Sem esta exceção, o split reversível inteiro sairia do KPI — exatamente o
# erro assimétrico que a classificação posicional existe para evitar.
#
# Só entram aqui frases que contenham de fato um termo de
# `_ACESSORIOS_NAO_RAC`; "ciclo reverso", por exemplo, não precisa de exceção
# porque nenhuma das duas palavras é termo de acessório.
_RE_FALSOS_ACESSORIOS = re.compile(r"\bBOMBA\s+DE\s+CALOR\b")


def _regex_termos(termos) -> "re.Pattern":
    """Alternância com fronteira de palavra dos dois lados."""
    return re.compile(r"\b(?:" + "|".join(termos) + r")\b")


_RE_PRODUTOS_NAO_RAC = _regex_termos(_PRODUTOS_NAO_RAC)
_RE_ACESSORIOS_NAO_RAC = _regex_termos(_ACESSORIOS_NAO_RAC)


def _e_acessorio(texto_normalizado: str) -> bool:
    """
    True quando o termo de acessório é o substantivo principal do título.

    O critério é POSICIONAL: acessório antes do núcleo "ar condicionado" (ou
    núcleo ausente) significa que o anúncio é do acessório. Depois do núcleo,
    é apenas um atributo do aparelho.

    Antes disso, as frases de `_RE_FALSOS_ACESSORIOS` são removidas do texto:
    elas carregam um termo da lista ("Bomba de Calor" contém BOMBA) mas
    descrevem o aparelho, não um acessório.
    """
    texto = _RE_FALSOS_ACESSORIOS.sub(" ", texto_normalizado)
    acessorio = _RE_ACESSORIOS_NAO_RAC.search(texto)
    if acessorio is None:
        return False
    nucleo = _RE_NUCLEO_AC.search(texto)
    return nucleo is None or acessorio.start() < nucleo.start()

TIPO_SPLIT_HW = "SPLIT_HW"
TIPO_JANELA = "JANELA"
TIPO_PORTATIL = "PORTATIL"
TIPO_OUTROS_RAC = "OUTROS_RAC"
TIPO_FORA_ESCOPO = "FORA_ESCOPO"
TIPO_INDEFINIDO = "IND"

# Tipos que contam como ar condicionado de verdade (entram na base).
TIPOS_RAC = (TIPO_SPLIT_HW, TIPO_JANELA, TIPO_PORTATIL, TIPO_OUTROS_RAC)

_RE_PORTATIL = re.compile(r"PORT[ÁA]TIL|PORTATIL")
_RE_JANELA = re.compile(r"\bJANELA\b|\bACJ\b")
_RE_OUTROS_RAC = re.compile(
    r"CASSETE|PISO\s*[-\s]?TETO|MULTI\s*[-\s]?SPLIT|DUTAD|BUILT\s*IN|"
    r"\bVRF\b|\bCHILLER\b"
)
_RE_SPLIT = re.compile(r"\bSPLIT\b|HI\s*[-\s]?WALL|HIGH\s*WALL|\bHW\b|INVERTER")
_RE_AR_CONDICIONADO = re.compile(r"AR\s*[-\s]?CONDICIONADO|AIR\s+CONDITIONER")


def classificar_tipo(titulo: Optional[str]) -> str:
    """
    Classifica o produto pelo título.

    A ordem dos testes importa: "fora de escopo" vem primeiro porque
    "Suporte para Ar Condicionado Split" contém a palavra SPLIT e viraria um
    split hi-wall fantasma no topo do ranking. Mas o teste de acessório é
    POSICIONAL (ver `_e_acessorio`) — senão "Springer Midea Ar Condicionado
    com Controle Remoto" cairia fora do escopo por causa de CONTROLE.

    Args:
        titulo: título cru do anúncio.

    Returns:
        Uma das constantes TIPO_*.

    Example:
        >>> classificar_tipo("Ar Condicionado Split Inverter 12000 BTUs")
        'SPLIT_HW'
        >>> classificar_tipo("Umidificador de Ar Ultrassônico")
        'FORA_ESCOPO'
        >>> classificar_tipo("Suporte para Ar Condicionado Split")
        'FORA_ESCOPO'
    """
    if not titulo:
        return TIPO_INDEFINIDO

    t = _norm_titulo(titulo)
    if _RE_PRODUTOS_NAO_RAC.search(t):
        return TIPO_FORA_ESCOPO
    if _e_acessorio(t):
        return TIPO_FORA_ESCOPO
    if _RE_PORTATIL.search(t):
        return TIPO_PORTATIL
    if _RE_JANELA.search(t):
        return TIPO_JANELA
    if _RE_OUTROS_RAC.search(t):
        return TIPO_OUTROS_RAC
    if _RE_SPLIT.search(t):
        return TIPO_SPLIT_HW
    # Sem palavra de formato: só assume split se o título é de fato um ar
    # condicionado E declara capacidade. "Ar condicionado 12000 BTUs" é split
    # em ~toda a prateleira brasileira; "Kit instalação 3m" não é nada.
    if _RE_AR_CONDICIONADO.search(t) and parse_btu(t) is not None:
        return TIPO_SPLIT_HW
    return TIPO_INDEFINIDO


# Fallbacks do grupo Midea, aplicados só quando `extract_brand` não resolve.
# Duas lacunas distintas:
#   * LINHAS_MIDEA — o anúncio cita só a linha ("Ar Condicionado Ecomaster
#     12000") e omite a marca;
#   * "COMFEE" — marca do grupo que hoje NÃO está em `config.BRANDS`. Sem esta
#     entrada, todo Comfee que rankear cai como "Desconhecida" e some do KPI
#     do grupo. (Vale considerar acrescentá-la a `config.BRANDS`, o que
#     beneficiaria também a coleta por keyword.)
_FALLBACK_MARCA = tuple(
    [(linha, "Midea") for linha in LINHAS_MIDEA] + [("COMFEE", "Comfee")]
)


def classificar_marca(titulo: Optional[str]) -> str:
    """
    Marca do produto, com fallback para as marcas e linhas do grupo Midea.

    `extract_brand` cobre o caso normal (a marca aparece no título e está em
    `config.BRANDS`). O fallback existe porque itens do grupo Midea sumiriam
    do KPI em dois casos reais: anúncio que cita só a linha comercial, e marca
    do grupo ausente da lista monitorada.

    Args:
        titulo: título cru do anúncio.

    Returns:
        Nome da marca, ou "Desconhecida".
    """
    marca = extract_brand(titulo)
    if marca != "Desconhecida" or not titulo:
        return marca

    t = _norm_titulo(titulo)
    for token, canonica in _FALLBACK_MARCA:
        if token in t:
            return canonica
    return "Desconhecida"


def eh_grupo_midea(marca: Optional[str]) -> bool:
    """
    True se a marca pertence ao grupo Midea (Midea, Carrier, Springer, Comfee).

    Mesmo corte de grupo usado pelo GfK — é o que permite ler o KPI ao lado
    do share oficial sem reconciliar taxonomia.
    """
    if not marca:
        return False
    return marca.strip().upper() in GRUPO_MIDEA


@dataclass
class BestSellerItem:
    """
    Uma posição de uma lista de mais vendidos.

    Os campos de classificação (`marca`, `grupo_midea`, `btu`, `tipo`,
    `no_escopo`) são derivados de `titulo` em `__post_init__` — o coletor de
    cada plataforma não os preenche, garantindo taxonomia idêntica entre
    fontes.

    Attributes:
        rank:            Posição na lista, 1-based. Ordinal — não somar.
        titulo:          Título cru do anúncio.
        preco:           Preço praticado (o "por"), em R$.
        preco_de:        Preço de referência riscado, quando houver. Na Casas
                         Bahia costuma ser lixo — não usar para desconto.
        vendidos:        Volume declarado pela plataforma.
        base_vendidos:   'mes' ou 'acumulado'. NUNCA somar bases diferentes.
        sku_plataforma:  Identificador do anúncio na plataforma (ASIN, MLB,
                         itemid). É a chave de pareamento entre dias — muito
                         mais estável que o título, que os sellers editam.
        patrocinado:     True/False quando a plataforma sinaliza; None quando
                         não dá para saber (≠ orgânico).
    """

    rank: int
    titulo: Optional[str]
    preco: Optional[float] = None
    preco_de: Optional[float] = None
    rating: Optional[float] = None
    reviews: Optional[int] = None
    vendidos: Optional[float] = None
    base_vendidos: Optional[str] = None
    seller: Optional[str] = None
    sku_plataforma: Optional[str] = None
    url_produto: Optional[str] = None
    patrocinado: Optional[bool] = None

    # Derivados — preenchidos em __post_init__.
    marca: str = field(init=False, default="Desconhecida")
    grupo_midea: bool = field(init=False, default=False)
    btu: Optional[int] = field(init=False, default=None)
    tipo: str = field(init=False, default=TIPO_INDEFINIDO)
    no_escopo: bool = field(init=False, default=False)

    def __post_init__(self) -> None:
        # O seller do PDP da Amazon chega com a grafia daquela loja
        # ("Belmicro Oficial", "friopecas"); canonizar aqui mantém a série de
        # buy box do ranking comparável com a da coleta de oferta, que
        # canoniza no `_build_record`. Ver utils/seller_names.py.
        self.seller = normalize_seller_name(self.seller)
        self.classificar()

    def classificar(self) -> None:
        """(Re)deriva marca, grupo, BTU e tipo a partir do título."""
        self.marca = classificar_marca(self.titulo)
        self.grupo_midea = eh_grupo_midea(self.marca)
        self.btu = parse_btu(_norm_titulo(self.titulo)) if self.titulo else None
        self.tipo = classificar_tipo(self.titulo)
        self.no_escopo = self.tipo in TIPOS_RAC

    def to_row(
        self,
        *,
        data: str,
        horario: str,
        run_id: Optional[str],
        spec: SourceSpec,
        url_coleta: str,
        endpoint: str,
    ) -> Dict[str, Any]:
        """
        Converte o item numa linha da série histórica.

        `url_coleta` e `endpoint` são gravados em TODA linha de propósito: são
        o que permite auditar a série depois e detectar que duas datas não
        compararam a mesma população.
        """
        return {
            "data": data,
            "horario": horario,
            "run_id": run_id,
            "plataforma": spec.key,
            "plataforma_nome": spec.nome,
            "referencia": spec.referencia,
            "mecanica": spec.mecanica,
            "url_coleta": url_coleta,
            "endpoint": endpoint,
            "parametro_ordenacao": spec.parametro_ordenacao,
            "rank": self.rank,
            "titulo": self.titulo,
            "marca": self.marca,
            "grupo_midea": self.grupo_midea,
            "btu": self.btu,
            "tipo": self.tipo,
            "no_escopo": self.no_escopo,
            "preco": self.preco,
            "preco_de": self.preco_de,
            "rating": self.rating,
            "reviews": self.reviews,
            "vendidos": self.vendidos,
            "base_vendidos": self.base_vendidos or spec.base_vendidos,
            "seller": self.seller,
            "sku_plataforma": self.sku_plataforma,
            "url_produto": self.url_produto,
            "patrocinado": self.patrocinado,
        }


# Ordem canônica das colunas da série histórica. Fonte única para CSV,
# Parquet e Supabase — divergência entre eles quebra o merge do histórico.
COLUNAS: List[str] = [
    "data", "horario", "run_id", "plataforma", "plataforma_nome", "referencia",
    "mecanica",
    "url_coleta", "endpoint", "parametro_ordenacao", "rank", "titulo",
    "marca", "grupo_midea", "btu", "tipo", "no_escopo", "preco", "preco_de",
    "rating", "reviews", "vendidos", "base_vendidos", "seller",
    "sku_plataforma", "url_produto", "patrocinado",
]
