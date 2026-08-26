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
# Referência da lista — a MEDIÇÃO que a lista representa
# ---------------------------------------------------------------------------
# Duas medições convivem na série, e NÃO são a mesma população:
#
#   * MAIS_VENDIDOS — a lista está ordenada por VENDAS (o varejista expõe o
#                     sort por vendas, e o endpoint carrega o parâmetro que
#                     prova isso). É a variável de RESULTADO.
#   * RELEVANCIA    — o varejista NÃO expõe ordenação por vendas; a única
#                     lista navegável é a de "relevância" (ordenação própria
#                     do algoritmo da loja: mix de venda, margem, curadoria e
#                     disponibilidade). Serve como PROXY de destaque, nunca
#                     como medida de venda.
#
# REGRA DURA desta separação: as duas referências NUNCA se misturam num mesmo
# número. Não se compara o % do top 10 de uma lista de vendas com o de uma
# lista de relevância, não se agregam juntas e não se somam. Cada referência
# é uma série própria — o painel as mantém em abas/segmentos separados.
REFERENCIA_MAIS_VENDIDOS = "mais_vendidos"
REFERENCIA_RELEVANCIA = "relevancia"

REFERENCIAS = (REFERENCIA_MAIS_VENDIDOS, REFERENCIA_RELEVANCIA)

# Rótulos de exibição de cada referência (painel e relatórios).
ROTULO_REFERENCIA: Dict[str, str] = {
    REFERENCIA_MAIS_VENDIDOS: "Mais Vendidos",
    REFERENCIA_RELEVANCIA: "Relevância",
}

# ---------------------------------------------------------------------------
# Mecânicas de ordenação
# ---------------------------------------------------------------------------
MECANICA_VELOCIDADE = "velocidade"
MECANICA_ACUMULADO = "acumulado"
MECANICA_VENDAS_MES = "vendas_mes"
MECANICA_DECLARADO = "declarado"
# Ordenação de RELEVÂNCIA do próprio varejista — não é venda. Todo source de
# referência RELEVANCIA usa esta mecânica; medi-la como venda é o erro que a
# separação de referência existe para impedir.
MECANICA_RELEVANCIA = "relevancia"

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
        referencia:          MAIS_VENDIDOS ou RELEVANCIA — a MEDIÇÃO que esta
                             lista representa. Determina em qual série/segmento
                             ela entra; as duas referências nunca se comparam
                             (ver REFERENCIA_* acima). Default MAIS_VENDIDOS
                             para não mudar o significado das fontes legadas.
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
    referencia: str = REFERENCIA_MAIS_VENDIDOS

    @property
    def parametro_ordenacao(self) -> str:
        """Rótulo canônico da ordenação, para exibição."""
        return self.parametros_ordenacao[0]

    @property
    def referencia_rotulo(self) -> str:
        """Nome de exibição da referência (Mais Vendidos / Relevância)."""
        return ROTULO_REFERENCIA.get(self.referencia, self.referencia)

    @property
    def eh_mais_vendidos(self) -> bool:
        return self.referencia == REFERENCIA_MAIS_VENDIDOS

    def ordenacao_comprovada(self, alvo: str) -> bool:
        """True se `alvo` (o endpoint chamado) carrega alguma das grafias."""
        return any(p in (alvo or "") for p in self.parametros_ordenacao)


# Prateleira de categoria NAVEGÁVEL que ancora a lista da Leroy — a mesma que o
# cliente abre. Definida UMA vez aqui e importada pela fonte
# (`bestsellers/sources/leroy_merlin.py`) para que o link auditável gravado na
# série e a referência usada na descoberta de facet não possam divergir quando a
# prateleira for renomeada (este módulo já enviou um `url_publica` quebrado antes).
LEROY_CATEGORIA_URL = (
    "https://www.leroymerlin.com.br/ar-condicionado-inverter/"
    "tipo-de-ar-condicionado/Split_Inverter"
)


# ---------------------------------------------------------------------------
# Como cada dealer é coletado (separado do "o que a série significa")
# ---------------------------------------------------------------------------
# `SourceSpec` diz o que o número REPRESENTA (referência, mecânica, armadilha).
# `ColetaSpec` diz COMO buscá-lo. Ficam separados de propósito: as fontes
# legadas (Amazon, ML, Shopee, Magalu, Leroy) têm coletor artesanal próprio e
# NÃO aparecem aqui; os dealers novos compartilham dois coletores genéricos
# (`sources/vtex_generic.py` e `sources/html_generic.py`) dirigidos por este
# registro, para não replicar 15 arquivos quase idênticos.
TIPO_VTEX_CATALOG = "vtex_catalog"   # API legada /api/catalog_system/pub/...
TIPO_HTML = "html"                   # navega a URL ordenada e parseia o DOM

# Ordenações da API legada de catálogo VTEX (parâmetro `O`).
VTEX_O_MAIS_VENDIDOS = "OrderByTopSaleDESC"
VTEX_O_RELEVANCIA = "OrderByScoreDESC"


@dataclass(frozen=True)
class ColetaSpec:
    """
    Instruções de busca de um dealer para os coletores genéricos.

    Attributes:
        tipo:      TIPO_VTEX_CATALOG (HTTP na API de catálogo) ou TIPO_HTML
                   (browser navega a URL ordenada e parseia o DOM/JSON-LD).
        host:      domínio da loja (sem esquema), ex. "www.dufrio.com.br".
        categoria: para VTEX, o caminho da árvore de categoria usado na API
                   legada (ex. "ar-condicionado/ar-condicionado-split-inverter").
                   Ignorado no tipo HTML (que navega a `url_publica`).
        ordem:     valor do parâmetro `O` (VTEX). Vazio = ordem padrão da loja.
        paginacao: itens por página pedidos por chamada.
    """

    tipo: str
    host: str
    categoria: str = ""
    ordem: str = ""
    paginacao: int = 24


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
        # vivendo no `endpoint` — o índice `production_products`, com query vazia
        # e facetFilters aplicados à categoria Split Inverter.
        url_publica=LEROY_CATEGORIA_URL,
        parametros_ordenacao=("production_products",),
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

    # -----------------------------------------------------------------------
    # DEALERS — MAIS VENDIDOS (ordenação por vendas exposta pela loja)
    #
    # Todos VTEX (parâmetro `O=OrderByTopSaleDESC` na API de catálogo), exceto
    # Ar Certo (loja não-VTEX, ordenação `ordem=compras_desc` sobre o DOM).
    # ATENÇÃO: dealers novos (Ago/2026), ainda SEM as 3 leituras de validação —
    # tratar o número como provisório e conferir a estabilidade antes de usar
    # em decisão (regra de `sources/__init__.py`). De IP de datacenter (VM /
    # GitHub Actions) muitos respondem bloqueio; a coleta oficial roda do PC
    # coletor (IP residencial).
    # -----------------------------------------------------------------------
    "webcontinental": SourceSpec(
        key="webcontinental",
        nome="Web Continental",
        url_publica=(
            "https://www.webcontinental.com.br/climatizacao/ar-condicionado/"
            "ar-condicionado-split-hi-wall?order=OrderByTopSaleDESC"
        ),
        parametros_ordenacao=("OrderByTopSaleDESC", "orders_desc"),
        mecanica=MECANICA_ACUMULADO,
        base_vendidos=None,
        itens_esperados=24,
        ativo=True,
        armadilha=(
            "VTEX `OrderByTopSaleDESC` é acumulado do anúncio (viés a favor de "
            "SKU antigo). Dealer novo — validar 3 leituras antes de decidir."
        ),
    ),
    "friopecas": SourceSpec(
        key="friopecas",
        nome="Frio Peças",
        url_publica=(
            "https://www.friopecas.com.br/ar-condicionado/"
            "ar-condicionado-split-inverter?order=OrderByTopSaleDESC"
        ),
        parametros_ordenacao=("OrderByTopSaleDESC", "orders_desc"),
        mecanica=MECANICA_ACUMULADO,
        base_vendidos=None,
        itens_esperados=24,
        ativo=True,
        armadilha=(
            "VTEX `OrderByTopSaleDESC` (acumulado). Recorte já é split "
            "inverter — contaminação menor, mas validar 3 leituras."
        ),
    ),
    "climario": SourceSpec(
        key="climario",
        nome="Clima Rio",
        url_publica=(
            "https://www.climario.com.br/ar-condicionado/"
            "ar-condicionado-hi-wall-inverter?order=OrderByTopSaleDESC"
        ),
        parametros_ordenacao=("OrderByTopSaleDESC", "orders_desc"),
        mecanica=MECANICA_ACUMULADO,
        base_vendidos=None,
        itens_esperados=24,
        ativo=True,
        armadilha=(
            "VTEX `OrderByTopSaleDESC` (acumulado). A prateleira navegável usa "
            "map de 2 categorias; a coleta ancora em hi-wall inverter."
        ),
    ),
    "arcerto": SourceSpec(
        key="arcerto",
        nome="Ar Certo",
        url_publica=(
            "https://www.arcerto.com/categoria/ar-condicionado-inverter/"
            "?ordem=compras_desc"
        ),
        parametros_ordenacao=("ordem=compras_desc", "compras_desc"),
        mecanica=MECANICA_ACUMULADO,
        base_vendidos=None,
        itens_esperados=20,
        ativo=True,
        armadilha=(
            "Loja NÃO-VTEX: ordenação `compras_desc` (compras acumuladas) só "
            "existe no DOM — sem API de sellers, buy box fica vazia. Parser de "
            "cards é frágil a redesign; conferir cobertura de preço/título."
        ),
    ),
    "poloar": SourceSpec(
        key="poloar",
        nome="Polo Ar",
        url_publica=(
            "https://www.poloar.com.br/ar-condicionado/inverter?"
            "category-1=ar-condicionado&category-2=inverter&sort=orders_desc"
        ),
        parametros_ordenacao=("orders_desc", "OrderByTopSaleDESC"),
        mecanica=MECANICA_ACUMULADO,
        base_vendidos=None,
        itens_esperados=24,
        ativo=True,
        armadilha=(
            "VTEX Intelligent Search `sort=orders_desc` (acumulado). Dealer "
            "novo — validar 3 leituras antes de decidir."
        ),
    ),
    "belmicro": SourceSpec(
        key="belmicro",
        nome="Bel Micro",
        url_publica=(
            "https://www.belmicro.com.br/climatizacao/Ar-Condicionado-Split"
            "?order=OrderByTopSaleDESC"
        ),
        parametros_ordenacao=("OrderByTopSaleDESC", "orders_desc"),
        mecanica=MECANICA_ACUMULADO,
        base_vendidos=None,
        itens_esperados=24,
        ativo=True,
        armadilha=(
            "VTEX `OrderByTopSaleDESC` (acumulado). Recorte 'Ar-Condicionado-"
            "Split' arrasta janela/piso-teto — o KPI filtra por tipo."
        ),
    ),
    "fastshop": SourceSpec(
        key="fastshop",
        nome="Fast Shop",
        url_publica=(
            "https://site.fastshop.com.br/ar-e-ventilacao/ar-condicionado?"
            "category-1=ar-e-ventilacao&category-2=ar-condicionado&sort=orders_desc"
        ),
        parametros_ordenacao=("orders_desc", "OrderByTopSaleDESC"),
        mecanica=MECANICA_ACUMULADO,
        base_vendidos=None,
        itens_esperados=24,
        ativo=True,
        armadilha=(
            "VTEX IS `sort=orders_desc` (acumulado). Fast Shop tem PerimeterX "
            "pesado — de IP de datacenter costuma vir 0 itens; coletar do PC."
        ),
    ),
    "bemol": SourceSpec(
        key="bemol",
        nome="Bemol",
        url_publica=(
            "https://www.bemol.com.br/ar-e-ventilacao/ar-condicionado/"
            "ar-condicionado-split?order=OrderByTopSaleDESC"
        ),
        parametros_ordenacao=("OrderByTopSaleDESC", "orders_desc"),
        mecanica=MECANICA_ACUMULADO,
        base_vendidos=None,
        itens_esperados=24,
        ativo=True,
        armadilha=(
            "VTEX `OrderByTopSaleDESC` (acumulado). Loja com forte pegada "
            "regional (Norte) — mix pode divergir do resto da série."
        ),
    ),
    "frigelar": SourceSpec(
        key="frigelar",
        nome="Frigelar",
        url_publica="https://www.frigelar.com.br/split-inverter/c",
        parametros_ordenacao=("OrderByTopSaleDESC", "orders_desc"),
        mecanica=MECANICA_ACUMULADO,
        base_vendidos=None,
        itens_esperados=24,
        ativo=True,
        armadilha=(
            "VTEX: a UI expõe 'Mais Vendidos' num <select> dentro do container "
            "de ordenação, mas a API de catálogo reproduz com "
            "`O=OrderByTopSaleDESC` sem precisar clicar. Acumulado do anúncio."
        ),
    ),

    # -----------------------------------------------------------------------
    # DEALERS — RELEVÂNCIA (a loja NÃO expõe ordenação por vendas)
    #
    # Referência RELEVANCIA: a lista é a ordem padrão/relevância do algoritmo
    # da loja — PROXY de destaque, NUNCA medida de venda. Entra numa série
    # própria, jamais comparada com as listas de mais vendidos.
    # -----------------------------------------------------------------------
    "dufrio": SourceSpec(
        key="dufrio",
        nome="Dufrio",
        url_publica=(
            "https://www.dufrio.com.br/ar-condicionado/"
            "ar-condicionado-split-inverter"
        ),
        parametros_ordenacao=("OrderByScoreDESC", "relevance", "relevancia"),
        mecanica=MECANICA_RELEVANCIA,
        base_vendidos=None,
        itens_esperados=24,
        ativo=True,
        referencia=REFERENCIA_RELEVANCIA,
        armadilha=(
            "RELEVÂNCIA, não vendas: VTEX `OrderByScoreDESC` é o score do "
            "algoritmo (mix de venda, margem, estoque, curadoria). Não somar "
            "nem comparar com as listas de mais vendidos."
        ),
    ),
    "centralar": SourceSpec(
        key="centralar",
        nome="Central Ar",
        url_publica=(
            "https://www.centralar.com.br/ar-condicionado/inverter/c/INVERTER"
            "?sort=relevance"
        ),
        parametros_ordenacao=("sort=relevance", "relevance", "relevancia"),
        mecanica=MECANICA_RELEVANCIA,
        base_vendidos=None,
        itens_esperados=20,
        ativo=True,
        referencia=REFERENCIA_RELEVANCIA,
        armadilha=(
            "RELEVÂNCIA. Loja SAP Hybris (não-VTEX): `sort=relevance` no DOM, "
            "sem API de sellers. Proxy de destaque, nunca medida de venda."
        ),
    ),
    "leveros": SourceSpec(
        key="leveros",
        nome="Leveros",
        url_publica="https://www.leveros.com.br/ar-condicionado/inverter/",
        parametros_ordenacao=("OrderByScoreDESC", "relevance", "relevancia"),
        mecanica=MECANICA_RELEVANCIA,
        base_vendidos=None,
        itens_esperados=24,
        ativo=True,
        referencia=REFERENCIA_RELEVANCIA,
        armadilha=(
            "RELEVÂNCIA: VTEX ordem padrão (score). Leveros é atacado/B2B — o "
            "destaque reflete sortimento profissional, não sell-out ao consumo."
        ),
    ),
    "ferreiracosta": SourceSpec(
        key="ferreiracosta",
        nome="Ferreira Costa",
        url_publica=(
            "https://www.ferreiracosta.com/Destaque/split-inverter-subcategoria"
        ),
        parametros_ordenacao=("Destaque", "relevancia", "relevance"),
        mecanica=MECANICA_RELEVANCIA,
        base_vendidos=None,
        itens_esperados=20,
        ativo=True,
        referencia=REFERENCIA_RELEVANCIA,
        armadilha=(
            "RELEVÂNCIA/DESTAQUE: página 'Destaque' curada pela loja, não "
            "ordenação por vendas. Parser de DOM; conferir cobertura de campos."
        ),
    ),
    "engage": SourceSpec(
        key="engage",
        nome="Engage",
        url_publica="https://www.engageeletro.com.br/ar-e-clima/ar-condicionado/",
        parametros_ordenacao=("ar-condicionado", "relevancia", "relevance"),
        mecanica=MECANICA_RELEVANCIA,
        base_vendidos=None,
        itens_esperados=20,
        ativo=True,
        referencia=REFERENCIA_RELEVANCIA,
        armadilha=(
            "RELEVÂNCIA: sem parâmetro de ordenação na URL — é a ordem padrão "
            "da categoria. Proxy de destaque; validar cobertura do parser."
        ),
    ),
}

# Registro de COMO buscar cada dealer genérico (ver ColetaSpec). As fontes
# legadas com coletor próprio (amazon, mercadolivre, magazineluiza, shopee,
# leroymerlin, casasbahia) NÃO entram aqui.
COLETA: Dict[str, ColetaSpec] = {
    "webcontinental": ColetaSpec(
        TIPO_VTEX_CATALOG, "www.webcontinental.com.br",
        "climatizacao/ar-condicionado/ar-condicionado-split-hi-wall",
        VTEX_O_MAIS_VENDIDOS,
    ),
    "friopecas": ColetaSpec(
        TIPO_VTEX_CATALOG, "www.friopecas.com.br",
        "ar-condicionado/ar-condicionado-split-inverter", VTEX_O_MAIS_VENDIDOS,
    ),
    "climario": ColetaSpec(
        TIPO_VTEX_CATALOG, "www.climario.com.br",
        "ar-condicionado/ar-condicionado-hi-wall-inverter", VTEX_O_MAIS_VENDIDOS,
    ),
    "arcerto": ColetaSpec(TIPO_HTML, "www.arcerto.com"),
    "poloar": ColetaSpec(
        TIPO_VTEX_CATALOG, "www.poloar.com.br",
        "ar-condicionado/inverter", VTEX_O_MAIS_VENDIDOS,
    ),
    "belmicro": ColetaSpec(
        TIPO_VTEX_CATALOG, "www.belmicro.com.br",
        "climatizacao/Ar-Condicionado-Split", VTEX_O_MAIS_VENDIDOS,
    ),
    "fastshop": ColetaSpec(
        TIPO_VTEX_CATALOG, "site.fastshop.com.br",
        "ar-e-ventilacao/ar-condicionado", VTEX_O_MAIS_VENDIDOS,
    ),
    "bemol": ColetaSpec(
        TIPO_VTEX_CATALOG, "www.bemol.com.br",
        "ar-e-ventilacao/ar-condicionado/ar-condicionado-split",
        VTEX_O_MAIS_VENDIDOS,
    ),
    "frigelar": ColetaSpec(
        TIPO_VTEX_CATALOG, "www.frigelar.com.br",
        "split-inverter", VTEX_O_MAIS_VENDIDOS,
    ),
    "dufrio": ColetaSpec(
        TIPO_VTEX_CATALOG, "www.dufrio.com.br",
        "ar-condicionado/ar-condicionado-split-inverter", VTEX_O_RELEVANCIA,
    ),
    "centralar": ColetaSpec(TIPO_HTML, "www.centralar.com.br"),
    "leveros": ColetaSpec(
        TIPO_VTEX_CATALOG, "www.leveros.com.br",
        "ar-condicionado/inverter", VTEX_O_RELEVANCIA,
    ),
    "ferreiracosta": ColetaSpec(TIPO_HTML, "www.ferreiracosta.com"),
    "engage": ColetaSpec(TIPO_HTML, "www.engageeletro.com.br"),
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


def referencia_de(plataforma: str) -> str:
    """
    Referência (MAIS_VENDIDOS/RELEVANCIA) de uma chave de plataforma.

    Cai em MAIS_VENDIDOS para chaves não registradas — é o significado das
    fontes legadas e o default histórico da série, então uma leitura antiga
    sem a coluna não muda de referência ao ser reprocessada.
    """
    spec = SOURCES.get(plataforma)
    return spec.referencia if spec is not None else REFERENCIA_MAIS_VENDIDOS


def coleta_de(plataforma: str) -> Optional[ColetaSpec]:
    """Instruções de busca de um dealer genérico, ou None se não registrado."""
    return COLETA.get(plataforma)


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
