"""
bestsellers/config.py — Registro das listas "Mais Vendidos" monitoradas.

Por que este módulo existe separado de ``config.py``:
    A coleta principal (``main.py``) é dirigida por KEYWORD — mede presença,
    posição e buy box numa SERP de busca. A coleta de mais vendidos é dirigida
    por LISTA: cada plataforma tem UMA ordenação por vendas, e o que interessa
    é o ranking inteiro dela. São duas populações diferentes e não devem
    compartilhar configuração nem tabela.

REGRA DURA desta coleta (herdada da rotina manual que ela automatiza):
    Ranking é medida ORDINAL. Posição #3 não é "3% de share" nem "3× melhor
    que #9". Não somar posições entre plataformas, não converter em share de
    mercado (isso vem de GfK/Neotrust) e não somar `vendidos` mensal com
    `vendidos` acumulado — são unidades distintas.

Cada plataforma declara qual é a MECÂNICA da sua ordenação, porque elas não
medem a mesma coisa:
    * ``velocidade``  — vendas recentes, recalculado com frequência (Amazon).
    * ``acumulado``   — total vendido no anúncio desde sempre (Magalu, ML).
                        Enviesado a favor de anúncio velho.
    * ``vendas_mes``  — unidades declaradas por mês por anúncio (Shopee).
    * ``declarado``   — a plataforma diz "mais vendidos" mas o comportamento
                        estatístico não confirma (Leroy Merlin, sob suspeita).
"""

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

# ---------------------------------------------------------------------------
# Mecânicas de ordenação
# ---------------------------------------------------------------------------
MECANICA_VELOCIDADE = "velocidade"
MECANICA_ACUMULADO = "acumulado"
MECANICA_VENDAS_MES = "vendas_mes"
MECANICA_DECLARADO = "declarado"

# Base do campo `vendidos`. NUNCA somar bases diferentes (ver metrics.py).
BASE_VENDIDOS_MES = "mes"
BASE_VENDIDOS_ACUMULADO = "acumulado"


@dataclass(frozen=True)
class SourceSpec:
    """
    Contrato de uma lista de mais vendidos.

    Attributes:
        key:                 Identificador estável usado em CSV/banco. NUNCA
                             renomear — é a chave da série histórica.
        nome:                Nome de exibição (igual ao `platform_name` do
                             scraper correspondente, quando existe).
        url_publica:         URL navegável que um humano abre para auditar a
                             lista. É o que fica gravado em `url_coleta`.
        parametros_ordenacao: Trechos que provam que a lista está ordenada por
                             vendas. O portão de validação exige que o
                             endpoint efetivamente chamado contenha PELO MENOS
                             UM deles. São vários porque a mesma ordenação tem
                             grafias diferentes na UI e na API — a Casas Bahia
                             manda `ordenacao=maisvendidos` na URL do site e
                             `sort=orders_desc` para a VTEX. O primeiro da
                             tupla é o rótulo canônico exibido nos relatórios.
        mecanica:            Uma das constantes MECANICA_* acima.
        base_vendidos:       Base do campo `vendidos` desta fonte, ou None
                             quando a plataforma não declara volume.
        itens_esperados:     Tamanho típico da lista — abaixo disso a coleta
                             provavelmente veio truncada.
        ativo:               Entra em `--plataformas all`.
        armadilha:           O erro que esta fonte já causou. Lido pelo
                             relatório e pelo humano que interpreta o número.
    """

    key: str
    nome: str
    url_publica: str
    parametros_ordenacao: Tuple[str, ...]
    mecanica: str
    base_vendidos: Optional[str]
    itens_esperados: int
    ativo: bool
    armadilha: str

    @property
    def parametro_ordenacao(self) -> str:
        """Rótulo canônico da ordenação, para exibição."""
        return self.parametros_ordenacao[0]

    def ordenacao_comprovada(self, alvo: str) -> bool:
        """True se `alvo` (o endpoint chamado) carrega alguma das grafias."""
        return any(p in (alvo or "") for p in self.parametros_ordenacao)


SOURCES: Dict[str, SourceSpec] = {
    "amazon": SourceSpec(
        key="amazon",
        nome="Amazon",
        url_publica="https://www.amazon.com.br/gp/bestsellers/home/17125373011/",
        parametros_ordenacao=("gp/bestsellers",),
        mecanica=MECANICA_VELOCIDADE,
        base_vendidos=None,
        itens_esperados=30,
        ativo=True,
        armadilha=(
            "O nó 17125373011 fica dentro do departamento Casa e mistura "
            "split, janela e portátil — sempre filtrar por `tipo`."
        ),
    ),
    "mercadolivre": SourceSpec(
        key="mercadolivre",
        nome="Mercado Livre",
        url_publica="https://www.mercadolivre.com.br/mais-vendidos/MLB1646",
        parametros_ordenacao=("mais-vendidos/MLB1646",),
        mecanica=MECANICA_ACUMULADO,
        base_vendidos=BASE_VENDIDOS_ACUMULADO,
        itens_esperados=20,
        ativo=True,
        armadilha=(
            "'+5mil vendidos' é acumulado VITALÍCIO do anúncio, não "
            "velocidade. Nunca somar com o campo mensal da Shopee."
        ),
    ),
    "magazineluiza": SourceSpec(
        key="magazineluiza",
        nome="Magalu",
        url_publica=(
            "https://www.magazineluiza.com.br/busca/ar+condicionado/"
            "?page=1&sortOrientation=desc&sortType=soldQuantity"
        ),
        parametros_ordenacao=("sortType=soldQuantity",),
        mecanica=MECANICA_ACUMULADO,
        base_vendidos=None,
        itens_esperados=20,
        ativo=True,
        armadilha=(
            "Quantidade ACUMULADA do anúncio e busca (não categoria): "
            "viés estrutural para anúncio velho e sensível ao algoritmo "
            "de busca."
        ),
    ),
    "shopee": SourceSpec(
        key="shopee",
        nome="Shopee",
        url_publica="https://shopee.com.br/search?keyword=ar%20condicionado&sortBy=sales",
        parametros_ordenacao=("sortBy=sales", "by=sales"),
        mecanica=MECANICA_VENDAS_MES,
        base_vendidos=BASE_VENDIDOS_MES,
        itens_esperados=30,
        ativo=True,
        armadilha=(
            "SEM a ordenação por vendas a lista é RELEVÂNCIA — universo "
            "diferente, não comparável (erro cometido em 08/08/2026). "
            "Única fonte com unidades/mês por anúncio."
        ),
    ),
    "leroymerlin": SourceSpec(
        key="leroymerlin",
        nome="Leroy Merlin",
        # URL pública NAVEGÁVEL que ancora a lista: a prateleira de categoria
        # Split Inverter, a MESMA que o cliente abre. Desde Ago/2026 a coleta
        # deixou de rankear uma busca por texto ("ar condicionado" — população
        # que ninguém acessa numa tela) e passou a recortar o índice de vendas
        # a esta categoria via `facetFilters` (ver `_descobrir_facet` na fonte).
        # O ESCOPO agora é reproduzível na tela; a ORDEM por vendas, porém, ainda
        # só existe via o índice Algolia (a UI da Leroy não expõe sort por
        # vendas), então a prova de ordenação (`parametros_ordenacao`) segue
        # vivendo no `endpoint` — o índice `production_products_most_sales`,
        # sempre registrado em `_coletar`.
        url_publica=(
            "https://www.leroymerlin.com.br/ar-condicionado-inverter/"
            "tipo-de-ar-condicionado/Split_Inverter"
        ),
        parametros_ordenacao=("production_products_most_sales",),
        mecanica=MECANICA_DECLARADO,
        base_vendidos=None,
        itens_esperados=30,
        ativo=True,
        armadilha=(
            "Declarada como mais vendidos, mas 41% dos itens não mudaram de "
            "posição em 48h (contra 12–19% nas demais) — a prateleira Midea "
            "desproporcional é sintoma dessa curadoria suspeita, não sinal de "
            "venda. A lista é ancorada na categoria Split Inverter via facet "
            "sobre o índice de vendas; se o atributo de facet sumir, a coleta "
            "cai para a busca por texto + recorte de tipo e AVISA. A ordenação "
            "por vendas só existe via o índice Algolia; a UI do site não a "
            "reproduz. Enquanto a mecânica não for confirmada com o varejista, "
            "não sustenta decisão de corte de verba isoladamente."
        ),
    ),
    "casasbahia": SourceSpec(
        key="casasbahia",
        nome="Casas Bahia",
        url_publica="https://www.casasbahia.com.br/ar-condicionado/b?ordenacao=maisvendidos",
        parametros_ordenacao=("orders_desc", "ordenacao=maisvendidos"),
        mecanica=MECANICA_ACUMULADO,
        base_vendidos=None,
        itens_esperados=20,
        ativo=True,
        armadilha=(
            "Contaminação alta: em 10/08/2026, 6 de 20 itens eram "
            "umidificador/depurador — inclusive nas posições #3, #4 e #5. "
            "Preços 'de' são lixo (desconto fantasma de -79%)."
        ),
    ),
}

# ---------------------------------------------------------------------------
# Grupo Midea — mesmo corte de grupo do GfK.
#
# `utils.brands.extract_brand` devolve rótulos compostos ("Springer Midea",
# "Midea Carrier"), então a checagem é por conjunto normalizado e não por
# igualdade com "MIDEA".
# ---------------------------------------------------------------------------
GRUPO_MIDEA: Tuple[str, ...] = (
    "MIDEA",
    "CARRIER",
    "SPRINGER",
    "COMFEE",
    "SPRINGER MIDEA",
    "MIDEA CARRIER",
)

# Linhas comerciais que identificam a Midea sem citar a marca no título.
# Sem isto, "Ar Condicionado Ecomaster 12000 BTUs" cai como marca desconhecida
# e some do KPI — foi observado nos rankings de Magalu e Amazon.
LINHAS_MIDEA: Tuple[str, ...] = (
    "ECOMASTER",
    "AIRVOLUTION",
    "XTREME SAVE",
    "SPRINGER MIDEA",
)

# ---------------------------------------------------------------------------
# Parâmetros da rotina
# ---------------------------------------------------------------------------
# Tamanho do topo do ranking usado no KPI principal.
TOP_N: int = 10

# Escopo do KPI: apenas split hi-wall. Janela e portátil ficam na base para
# diagnóstico, mas não entram no KPI — são categorias com dinâmica própria.
TIPO_ESCOPO_KPI: str = "SPLIT_HW"

# Diretórios de saída desta rotina.
OUTPUT_DIR: str = "output/bestsellers"
HISTORICO_PATH: str = "data/bestsellers/master_bestsellers.csv"

# Limiares dos portões de validação (ver validate.py).
LIMITE_CONTAMINACAO: float = 0.15    # >15% fora de escopo RAC → aviso
LIMITE_ITENS_MINIMO: int = 10        # piso absoluto de itens
# Piso relativo ao tamanho declarado da lista (`SourceSpec.itens_esperados`).
# Uma Amazon com 12 de 30 posições passa no piso absoluto, mas não é a mesma
# população dos dias de lista cheia.
FRACAO_MINIMA_DA_LISTA: float = 0.6
LIMITE_COBERTURA_CAMPO: float = 0.85  # < 85% de `preco`/`titulo` → parser suspeito
LIMITE_ESTABILIDADE: float = 0.35    # >35% parados em 48h → curadoria suspeita
LIMITE_MOVIMENTO_PRECO: float = 2.0  # movimento de piso relevante, em %

# Nº mínimo de leituras para uma agregação virar tendência. Uma leitura
# isolada não forma nada; a rotina manual fixou 3 leituras do mesmo dia da
# semana como piso.
MINIMO_LEITURAS_TENDENCIA: int = 3


def sources_ativos() -> Dict[str, SourceSpec]:
    """Fontes que entram em `--plataformas all`."""
    return {k: v for k, v in SOURCES.items() if v.ativo}


def resolver_keys(selecao) -> list:
    """
    Resolve a seleção da linha de comando para uma lista de chaves de fonte.

    Args:
        selecao: lista de chaves, ou ``["all"]`` / None para todas as ativas.

    Returns:
        Lista de chaves válidas, na ordem canônica de ``SOURCES``.

    Raises:
        ValueError: se alguma chave não existir no registro.

    Example:
        >>> resolver_keys(["amazon", "shopee"])
        ['amazon', 'shopee']
    """
    if not selecao:
        return list(sources_ativos().keys())

    # A validação vem ANTES do atalho de `all`: com o atalho primeiro,
    # `--plataformas all amazonn` engolia o erro de digitação e disparava a
    # coleta das seis plataformas como se nada tivesse acontecido.
    desconhecidas = [k for k in selecao if k != "all" and k not in SOURCES]
    if desconhecidas:
        raise ValueError(
            f"Plataforma(s) desconhecida(s): {', '.join(desconhecidas)}. "
            f"Disponíveis: {', '.join(SOURCES)}"
        )

    if "all" in selecao:
        if len(selecao) > 1:
            raise ValueError(
                "'all' não se combina com outras plataformas — use 'all' "
                "sozinho ou liste as chaves desejadas."
            )
        return list(sources_ativos().keys())

    return [k for k in SOURCES if k in selecao]
