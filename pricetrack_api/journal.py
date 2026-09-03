"""
ExportJournal — o diário dos exports criados, para que Ctrl+C não vire zumbi.

O limite da API é de **3 exports concorrentes por organização** e não existe
endpoint para cancelar um export. Enquanto o id do export criado vivia só na
memória do processo, qualquer interrupção (Ctrl+C, queda de rede, o PC
dormindo) largava um export rodando que ninguém mais conhecia: ele continuava
segurando um dos 3 slots, e a execução seguinte — que não tinha como saber que
aquele export era *dela* — criava outro para a mesma data e tomava 429. Duas ou
três interrupções bastavam para ocupar os 3 slots com exports órfãos e travar
todo import seguinte, que é exatamente o loop de "aguardando slot" que se via
no console.

O diário quebra esse ciclo: o id é gravado em disco **assim que o POST
retorna**, antes de qualquer espera. A execução seguinte lê o diário, pergunta
o status do export à API e o **adota** em vez de criar um novo — se já estiver
DONE, só baixa; se ainda estiver rodando, entra na fila de polling.

Regra dura: falha do diário NUNCA derruba o import. Toda a I/O é absorvida com
log — perder o diário custa um export duplicado, perder a coleta custa o dia.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from loguru import logger

#: Nome do arquivo dentro de ``settings.data_dir``.
JOURNAL_FILENAME = "exports_state.json"
#: Formato do arquivo. Um número diferente = arquivo de outra versão, ignorado.
_SCHEMA_VERSION = 1
#: Idade máxima de uma entrada no diário (48h). Acima disso o export já não
#: existe mais do lado da API — manter a linha só polui o próximo diagnóstico.
_DEFAULT_MAX_AGE_SECONDS = 48 * 3600.0


@dataclass(frozen=True, slots=True)
class JournalEntry:
    """Um export criado por esta máquina e ainda não concluído."""

    export_id: str
    dataset: str
    collection_date: str
    body_key: str
    created_at: float          # epoch (time.time), para calcular idade real

    def age_seconds(self, now: float) -> float:
        return max(0.0, now - self.created_at)


def journal_key(dataset: str, request: Any) -> str:
    """Chave estável de um pedido de export: dataset + corpo do POST.

    Usa o corpo inteiro (não só a data) porque dois exports do mesmo dia com
    filtros diferentes — ``marketplaces``, ``collectionHourExecutionRange`` —
    são exports DIFERENTES e não podem se adotar mutuamente: o arquivo baixado
    teria menos linhas do que o pedido, em silêncio.
    """
    body = json.dumps(request.to_body(), sort_keys=True, ensure_ascii=False)
    digest = hashlib.sha1(body.encode("utf-8")).hexdigest()[:12]
    return f"{dataset}|{digest}"


class ExportJournal:
    """Registro em disco dos exports criados e ainda não concluídos.

    Args:
        path: arquivo JSON do diário.
        clock: relógio de parede (epoch) — injetável para teste.
        max_age_seconds: idade acima da qual a entrada é descartada na leitura.
    """

    def __init__(
        self,
        path: Path,
        clock: Callable[[], float] = time.time,
        max_age_seconds: float = _DEFAULT_MAX_AGE_SECONDS,
    ):
        self.path = Path(path)
        self._clock = clock
        self._max_age = max_age_seconds

    # ── leitura ──────────────────────────────────────────────────────────

    def _load(self) -> Dict[str, JournalEntry]:
        """Lê o diário. Arquivo ausente/corrompido devolve diário vazio."""
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, json.JSONDecodeError) as e:
            logger.warning(
                f"PriceTrack: diário de exports ilegível ({e}) — seguindo sem "
                f"adoção. Exports já criados podem virar órfãos desta vez."
            )
            return {}
        if not isinstance(raw, dict) or raw.get("version") != _SCHEMA_VERSION:
            return {}

        now = self._clock()
        entries: Dict[str, JournalEntry] = {}
        for key, item in (raw.get("entries") or {}).items():
            try:
                entry = JournalEntry(
                    export_id=str(item["export_id"]),
                    dataset=str(item.get("dataset", "")),
                    collection_date=str(item.get("collection_date", "")),
                    body_key=str(key),
                    created_at=float(item.get("created_at") or 0.0),
                )
            except (KeyError, TypeError, ValueError):
                continue
            if not entry.export_id:
                continue
            if entry.age_seconds(now) > self._max_age:
                continue
            entries[key] = entry
        return entries

    def get(self, dataset: str, request: Any) -> Optional[JournalEntry]:
        """Entrada do diário para este pedido, se houver."""
        return self._load().get(journal_key(dataset, request))

    def age_of(self, entry: JournalEntry) -> float:
        """Idade real da entrada, em segundos de relógio de parede."""
        return entry.age_seconds(self._clock())

    def entries(self) -> List[JournalEntry]:
        """Todas as entradas vivas, da mais recente para a mais antiga."""
        return sorted(self._load().values(), key=lambda e: -e.created_at)

    def by_export_id(self) -> Dict[str, JournalEntry]:
        """Índice ``export_id → entrada`` (diagnóstico do CLI)."""
        return {entry.export_id: entry for entry in self._load().values()}

    # ── escrita ──────────────────────────────────────────────────────────

    def record(self, dataset: str, request: Any, export_id: str) -> None:
        """Grava o export recém-criado. Chamar ANTES de qualquer espera."""
        if not export_id:
            return
        key = journal_key(dataset, request)
        collection_date = getattr(request, "collection_date", "")
        entry = JournalEntry(
            export_id=export_id,
            dataset=dataset,
            collection_date=str(collection_date),
            body_key=key,
            created_at=self._clock(),
        )
        entries = self._load()
        entries[key] = entry
        self._save(entries)

    def forget(self, export_id: str) -> None:
        """Remove a entrada de um export que já não interessa (DONE/FAILED)."""
        entries = self._load()
        alvo = [k for k, e in entries.items() if e.export_id == export_id]
        if not alvo:
            return
        for key in alvo:
            entries.pop(key, None)
        self._save(entries)

    def _save(self, entries: Dict[str, JournalEntry]) -> None:
        """Escrita atômica (tmp + replace): um Ctrl+C no meio não corrompe."""
        payload = {
            "version": _SCHEMA_VERSION,
            "entries": {
                key: {
                    k: v for k, v in asdict(entry).items() if k != "body_key"
                }
                for key, entry in entries.items()
            },
        }
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(tmp, self.path)
        except OSError as e:
            # Diário é rede de segurança, não pré-requisito: perder a gravação
            # custa um export duplicado; abortar aqui custaria a importação.
            logger.warning(f"PriceTrack: não deu para gravar o diário de exports ({e})")
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
