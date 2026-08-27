"""
bestsellers/sources/leroy_ordenacao.py — prova de ordenação lida DA TELA.

Por que existe (Ago/2026)
------------------------
Até aqui, a prova de que a lista da Leroy estava ordenada por VENDAS morava no
nome de uma réplica Algolia (`production_products_most_sales`), sob a premissa,
escrita no código, de que "a UI da Leroy não expõe sort por vendas".

**A premissa é falsa.** A prateleira de categoria tem um controle "Ordenar por"
e ele imprime a ordenação vigente em texto — inclusive "Mais vendidos". Isso
muda a natureza da prova: em vez de um nome de índice que ninguém consegue
conferir abrindo o navegador, a ordenação passa a ser AUDITÁVEL NA TELA, que é
o que o resto deste módulo sempre quis (ver `url_publica` em `bestsellers.config`).

Como a leitura é ancorada
-------------------------
O controle é um Select do Radix, renderizado com Tailwind de valor arbitrário:
as classes carregam hash (`radix-:R10mqcvffff4qdkq:`, `f-direction-[var(--...)]`)
e mudam a cada build. Ancorar em classe aqui seria repetir o erro que este
repositório já documenta em `.claude/COMMON_MISTAKES.md`.

Por isso a âncora é SEMÂNTICA e em duas partes, ambas estáveis porque existem
para acessibilidade, não para estilo:

1. o rótulo visível "Ordenar por";
2. o `role="combobox"` do controle correspondente.

O valor selecionado é o texto do próprio controle (o Radix o renderiza num
`<span>` irmão do ícone). O `<svg>` é descartado — ele não tem texto, mas
`get_text()` de um controle com ícone inline pode trazer sujeira.
"""

import re
import unicodedata
from typing import Optional

from bs4 import BeautifulSoup, Tag

# Rótulo do controle. Regex e não igualdade: a Leroy já imprimiu "Ordenar por:"
# e o texto pode vir com espaço não-quebrável no meio.
_RE_ORDENAR_POR = re.compile(r"ordenar\s+por", re.I)

# Rótulos de ordenação conhecidos, normalizados (sem acento, minúsculo) → rótulo
# canônico. Serve de duas coisas: reconhecer o valor lido e, no fallback, achar
# o combobox certo quando o rótulo "Ordenar por" não for encontrado.
_ORDENACOES = {
    "mais vendidos": "Mais vendidos",
    "relevancia": "Relevância",
    "menor preco": "Menor preço",
    "maior preco": "Maior preço",
    "mais recentes": "Mais recentes",
    "melhor avaliados": "Melhor avaliados",
    "mais bem avaliados": "Mais bem avaliados",
    "maior desconto": "Maior desconto",
}

#: A ÚNICA ordenação que sustenta a série `mais_vendidos` da Leroy.
ORDENACAO_MAIS_VENDIDOS = "Mais vendidos"


def normalizar(texto: Optional[str]) -> str:
    """
    Minúsculo, sem acento e com espaços colapsados.

    Args:
        texto: rótulo cru lido da página.

    Returns:
        Forma normalizada, ou string vazia se a entrada for vazia/None.

    Example:
        >>> normalizar("  Mais   Vendidos ")
        'mais vendidos'
    """
    if not texto:
        return ""
    sem_acento = unicodedata.normalize("NFKD", texto)
    sem_acento = "".join(c for c in sem_acento if not unicodedata.combining(c))
    # \xa0 (espaço não-quebrável) não casa \s em todas as versões; troca antes.
    return " ".join(sem_acento.replace("\xa0", " ").split()).lower()


def _valor_do_combobox(combobox: Tag) -> Optional[str]:
    """Texto do valor selecionado dentro do controle, ignorando o ícone."""
    # `data-placeholder` marca "nada selecionado" no Radix — não é ordenação.
    if combobox.has_attr("data-placeholder"):
        return None
    for span in combobox.find_all("span"):
        texto = span.get_text(" ", strip=True)
        if texto:
            return texto
    texto = combobox.get_text(" ", strip=True)
    return texto or None


def _combobox_junto_do_rotulo(soup: BeautifulSoup) -> Optional[Tag]:
    """
    O combobox que pertence ao rótulo "Ordenar por".

    Sobe do rótulo pelos ancestrais até achar um que contenha um combobox — é
    assim que o cartão de ordenação se distingue de outros selects da página
    (filtros de marca, de faixa de preço) sem depender de classe.
    """
    rotulo = soup.find(string=_RE_ORDENAR_POR)
    if rotulo is None:
        return None
    atual = rotulo.parent
    while atual is not None:
        if isinstance(atual, Tag):
            combobox = atual.select_one('[role="combobox"]')
            if combobox is not None:
                return combobox
        atual = atual.parent
    return None


def ordenacao_da_pagina(html: str) -> Optional[str]:
    """
    Ordenação selecionada no controle "Ordenar por" da prateleira.

    Args:
        html: HTML da página de categoria, já renderizada.

    Returns:
        O rótulo como a Leroy o imprime (ex.: "Mais vendidos"), ou None quando
        a página não traz o controle — página não renderizada, layout novo ou
        bloqueio. None é "não sei", NUNCA "está ordenado por vendas".

    Example:
        >>> ordenacao_da_pagina('<span>Ordenar por</span>'
        ...                     '<button role="combobox"><span>Mais vendidos</span></button>')
        'Mais vendidos'
    """
    if not html:
        return None
    soup = BeautifulSoup(html, "html.parser")

    combobox = _combobox_junto_do_rotulo(soup)
    if combobox is not None:
        valor = _valor_do_combobox(combobox)
        if valor:
            return _ORDENACOES.get(normalizar(valor), valor)

    # Fallback: sem o rótulo, aceita um combobox cujo VALOR seja uma ordenação
    # conhecida. Reconhecer o valor é o que impede pegar um select de filtro.
    for candidato in soup.select('[role="combobox"]'):
        valor = _valor_do_combobox(candidato)
        canonico = _ORDENACOES.get(normalizar(valor))
        if canonico:
            return canonico
    return None


def ordenado_por_vendas(html: str) -> bool:
    """
    True só quando a página PROVA estar ordenada por "Mais vendidos".

    Ausência de prova é False, não True: gravar relevância dentro da série
    `mais_vendidos` é o erro que este módulo inteiro existe para evitar.
    """
    lida = ordenacao_da_pagina(html)
    return normalizar(lida) == normalizar(ORDENACAO_MAIS_VENDIDOS)
