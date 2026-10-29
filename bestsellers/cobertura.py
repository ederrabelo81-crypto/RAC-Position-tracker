"""
bestsellers/cobertura.py — Fonte de Mais Vendidos que parou de gravar.

O job ``local_bestsellers`` do `utils/pipeline_registry.py` cobra o dia
inteiro: se NADA foi coletado, alarma. Ele não vê uma fonte que some sozinha
enquanto as outras seguem gravando — e foi assim que a Leroy Merlin parou em
07/10/2026 sem que ninguém soubesse (conferido no banco em 29/10/2026), o mesmo
modo de falha do Google Shopping em agosto.

Definição (lógica pura, testada em ``tests/test_bestsellers_cobertura.py``):

* **dia de coleta** = dia em que ALGUMA fonte gravou. Dia em que nada rodou é
  problema do job, não da fonte — contá-lo aqui geraria dois alertas para o
  mesmo buraco (regra 1 de `pipeline_registry`).
* **fonte MUDA** = ficou fora dos últimos ``DIAS_PARA_MUDA`` dias de coleta
  seguidos, depois de já ter gravado. **CRÔNICA** a partir de
  ``DIAS_PARA_CRONICA``.
* **nunca coletou** = nenhuma linha na história da tabela. Vai para o relatório,
  não para o alerta: são as 7 fontes conhecidas desde Set/2026 (decisão do
  mantenedor: só ``casasbahia`` importa), e alerta diário sobre o já sabido é o
  primeiro passo para alerta ignorado.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

#: Dias de coleta seguidos sem a fonte para virar alerta. Dois, porque um dia
#: isolado acontece (sessão do ML, timeout) e se resolve na próxima rodada.
DIAS_PARA_MUDA = 2

#: A partir daqui não há explicação inocente: vira issue, não linha de alerta.
DIAS_PARA_CRONICA = 5

#: Quantos dias para trás a varredura olha.
JANELA_DIAS = 30


@dataclass
class FonteMuda:
    """Fonte que parou de gravar enquanto a coleta seguia rodando."""

    plataforma: str
    nome: str
    ultimo_dia: Optional[date]
    dias_sem_dado: int
    dias_perdidos: List[date] = field(default_factory=list)

    @property
    def cronica(self) -> bool:
        """Fora há ``DIAS_PARA_CRONICA`` dias de coleta ou mais."""
        return self.dias_sem_dado >= DIAS_PARA_CRONICA

    def to_dict(self) -> Dict[str, object]:
        """Forma serializável (JSON do supervisor)."""
        return {
            "plataforma": self.plataforma,
            "nome": self.nome,
            "ultimo_dia": self.ultimo_dia.isoformat() if self.ultimo_dia else None,
            "dias_sem_dado": self.dias_sem_dado,
            "cronica": self.cronica,
        }


@dataclass
class RelatorioCobertura:
    """Resultado da varredura: fontes mudas + as que nunca coletaram."""

    mudas: List[FonteMuda]
    nunca_coletaram: List[str]
    dias_de_coleta: int

    def to_dict(self) -> Dict[str, object]:
        """Forma serializável (JSON do supervisor)."""
        return {
            "fontes_mudas": [m.to_dict() for m in self.mudas],
            "nunca_coletaram": list(self.nunca_coletaram),
            "dias_de_coleta_avaliados": self.dias_de_coleta,
        }


def classificar_fontes(
    leituras: Iterable[Tuple[date, str]],
    fontes: Dict[str, str],
    ate: date,
    ultimos_dias: Optional[Dict[str, Optional[date]]] = None,
) -> RelatorioCobertura:
    """
    Classifica cada fonte configurada a partir dos dias em que ela gravou.

    Args:
        leituras: pares ``(dia, plataforma)`` com dado na janela avaliada
            (repetições são indiferentes).
        fontes: ``{chave: nome de exibição}`` das fontes ativas.
        ate: último dia que deveria ter dado (inclusive).
        ultimos_dias: último dia com dado de cada fonte em TODA a história,
            para as que não aparecem na janela. Ausente ou None = nunca gravou.

    Returns:
        `RelatorioCobertura`, com as fontes mudas da pior para a melhor.
    """
    por_fonte: Dict[str, Set[date]] = {}
    dias_coleta: Set[date] = set()
    for dia, plataforma in leituras:
        if dia > ate:
            continue
        dias_coleta.add(dia)
        por_fonte.setdefault(str(plataforma), set()).add(dia)

    ordenados: Sequence[date] = sorted(dias_coleta)
    ultimos_dias = ultimos_dias or {}
    mudas: List[FonteMuda] = []
    nunca: List[str] = []

    for chave, nome in fontes.items():
        dias = por_fonte.get(chave)
        ultimo = max(dias) if dias else ultimos_dias.get(chave)
        if ultimo is None:
            nunca.append(chave)
            continue
        perdidos = [d for d in ordenados if d > ultimo]
        if len(perdidos) >= DIAS_PARA_MUDA:
            mudas.append(FonteMuda(chave, nome, ultimo, len(perdidos), perdidos))

    mudas.sort(key=lambda m: (-m.dias_sem_dado, m.plataforma))
    return RelatorioCobertura(mudas, sorted(nunca), len(ordenados))
