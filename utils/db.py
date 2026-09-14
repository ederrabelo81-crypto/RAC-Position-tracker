"""
utils/db.py — Camada de acesso ao banco, intercambiável entre fornecedores.

Por que este módulo existe
--------------------------
Em 12/09/2026 o projeto Supabase passou de 1 GB com a cota do free tier em
500 MB. O Postgres seguiu saudável e gravável (``read_only=off``), mas o
PostgREST — a API REST que o ``supabase-py`` consome — passou a devolver 402
em TODAS as operações. A janela quente parou de receber coleta; o histórico
frio (Parquet no Drive) continuou intacto, porque a gravação sempre foi dupla
e independente (ver ``utils/history``).

A janela quente custa ~34 MB/dia: quinze dias são ~510 MB, mais do que
qualquer free tier gerenciado de 2026 entrega com folga. A saída, portanto,
não é "outro Supabase" — é separar o *código* do *fornecedor*.

Este módulo expõe a MESMA API fluente do ``supabase-py``::

    client.table("coletas").select("*").eq("data", "2026-09-14").execute()

implementada sobre psycopg2 contra um Postgres qualquer (Aiven, Neon, VM
própria). As ~112 chamadas espalhadas pelo projeto continuam valendo: muda a
credencial, não o call site.

Regra dura
----------
Este adaptador **imita** o PostgREST, não o reimplementa. Ele cobre exatamente
o subconjunto que este repositório usa — o que está em ``_OPS`` e nos métodos
de :class:`_Query`. Filtro que o PostgREST aceita e este módulo não conhece
levanta :class:`UnsupportedFilterError` na hora da montagem, alto e claro.
Traduzir errado em silêncio seria pior que não traduzir: devolveria um
recorte plausível e errado, que é o modo de falha que este projeto mais teme.

Seleção do backend
------------------
``RAC_DB_BACKEND``:

* ``auto`` (padrão) — usa Postgres direto se ``RAC_DB_DSN`` existir; senão o
  Supabase via REST;
* ``postgres`` — força o adaptador (erra alto se não houver DSN);
* ``supabase`` — força o ``supabase-py``.

``RAC_DB_DSN`` é o DSN do Postgres novo. Provedores gerenciados exigem TLS:
mantenha o ``?sslmode=require`` que vem na string de conexão deles.

USO::

    from utils.db import get_client
    client = get_client()          # não importa quem está atrás
    client.table("coletas").select("*").limit(10).execute()
"""

from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from loguru import logger

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:  # python-dotenv é opcional
    pass

try:
    import psycopg2
    from psycopg2 import sql as _sql
    from psycopg2.extensions import new_array_type, new_type, register_type
    from psycopg2.extras import RealDictCursor, execute_values

    _HAS_PSYCOPG = True
except ImportError:  # pragma: no cover - ambiente sem o driver
    psycopg2 = None  # type: ignore[assignment]
    _sql = None  # type: ignore[assignment]
    _HAS_PSYCOPG = False


# --------------------------------------------------------------------------- #
# Fidelidade de tipo com o PostgREST
# --------------------------------------------------------------------------- #
#
# O PostgREST serializa em JSON, e isso NÃO é detalhe cosmético: `numeric` sai
# como STRING (para não perder precisão de dinheiro em float) e `date`/
# `timestamp` saem como texto ISO. O projeto inteiro foi escrito contra esses
# tipos — o `seller_app` converte numeric→número explicitamente, e há código
# que fatia `linha["data"]` como string.
#
# O psycopg2, no padrão, devolveria `Decimal` e `datetime.date`. Seria a pior
# classe de regressão possível: não quebra na hora, só devolve um resultado
# ligeiramente diferente alguns passos adiante. Então os casters abaixo fazem o
# adaptador falar exatamente a língua do PostgREST.
#
# O registro é por CONEXÃO, nunca global: o `pricetrack_importer` usa psycopg2
# direto e espera os tipos nativos do Python.

_OID_NUMERIC, _OID_NUMERIC_ARRAY = 1700, 1231
_OID_DATE, _OID_DATE_ARRAY = 1082, 1182
_OID_TIMESTAMP, _OID_TIMESTAMP_ARRAY = 1114, 1115
_OID_TIMESTAMPTZ, _OID_TIMESTAMPTZ_ARRAY = 1184, 1185
_OID_UUID, _OID_UUID_ARRAY = 2950, 2951


def _texto_cru(value, cur):
    """Entrega o texto do wire como veio — é o que o JSON do PostgREST traz."""
    return value


def _texto_iso(value, cur):
    """`2026-09-10 08:00:00+00` → `2026-09-10T08:00:00+00`, como o PostgREST."""
    if value is None:
        return None
    return value.replace(" ", "T", 1)


def _registrar_tipos_postgrest(conn) -> None:
    """Faz esta conexão devolver os tipos na forma do PostgREST."""
    numerico = new_type((_OID_NUMERIC,), "RAC_NUMERIC_TEXTO", _texto_cru)
    data = new_type((_OID_DATE,), "RAC_DATE_TEXTO", _texto_cru)
    carimbo = new_type(
        (_OID_TIMESTAMP, _OID_TIMESTAMPTZ), "RAC_TS_TEXTO", _texto_iso
    )
    uuid_ = new_type((_OID_UUID,), "RAC_UUID_TEXTO", _texto_cru)

    for tipo in (numerico, data, carimbo, uuid_):
        register_type(tipo, conn)

    for oid, base, nome in (
        (_OID_NUMERIC_ARRAY, numerico, "RAC_NUMERIC_TEXTO_ARRAY"),
        (_OID_DATE_ARRAY, data, "RAC_DATE_TEXTO_ARRAY"),
        (_OID_TIMESTAMP_ARRAY, carimbo, "RAC_TS_TEXTO_ARRAY"),
        (_OID_TIMESTAMPTZ_ARRAY, carimbo, "RAC_TSTZ_TEXTO_ARRAY"),
        (_OID_UUID_ARRAY, uuid_, "RAC_UUID_TEXTO_ARRAY"),
    ):
        register_type(new_array_type((oid,), nome, base), conn)


class DBError(RuntimeError):
    """Falha de configuração ou de execução na camada de banco."""


class UnsupportedFilterError(DBError):
    """Filtro do PostgREST que este adaptador não sabe traduzir.

    Deliberadamente fatal: traduzir errado devolveria um recorte plausível e
    errado, pior que falhar.
    """


# Operadores do PostgREST → SQL. Chave = nome do método/sufixo usado no
# projeto; valor = operador SQL correspondente.
_OPS: Dict[str, str] = {
    "eq": "=",
    "neq": "<>",
    "gt": ">",
    "gte": ">=",
    "lt": "<",
    "lte": "<=",
    "like": "LIKE",
    "ilike": "ILIKE",
}


@dataclass
class DBResponse:
    """Resposta no formato que o ``supabase-py`` devolve (``.data``/``.count``)."""

    data: List[Dict[str, Any]] = field(default_factory=list)
    count: Optional[int] = None


# --------------------------------------------------------------------------- #
# Tradução do or_() do PostgREST
# --------------------------------------------------------------------------- #


def _split_top_level(expr: str) -> List[str]:
    """Quebra por vírgula respeitando parênteses de grupos ``and(...)``.

    ``"a.eq.1,and(b.eq.2,c.eq.3)"`` → ``["a.eq.1", "and(b.eq.2,c.eq.3)"]``.
    """
    partes: List[str] = []
    profundidade = 0
    atual: List[str] = []
    for ch in expr:
        if ch == "(":
            profundidade += 1
        elif ch == ")":
            profundidade -= 1
        if ch == "," and profundidade == 0:
            partes.append("".join(atual))
            atual = []
            continue
        atual.append(ch)
    if atual:
        partes.append("".join(atual))
    return [p.strip() for p in partes if p.strip()]


def _cond_to_sql(cond: str) -> Tuple[Any, List[Any]]:
    """Traduz UMA condição do PostgREST para ``(sql.Composable, params)``.

    Aceita ``col.op.valor``, ``col.is.null`` e o grupo ``and(c1,c2)``.
    """
    cond = cond.strip()

    if cond.lower().startswith("and(") and cond.endswith(")"):
        internas = _split_top_level(cond[4:-1])
        pedacos, params = [], []
        for interna in internas:
            frag, p = _cond_to_sql(interna)
            pedacos.append(frag)
            params.extend(p)
        return _sql.SQL("({})").format(_sql.SQL(" AND ").join(pedacos)), params

    if cond.lower().startswith("or(") and cond.endswith(")"):
        internas = _split_top_level(cond[3:-1])
        pedacos, params = [], []
        for interna in internas:
            frag, p = _cond_to_sql(interna)
            pedacos.append(frag)
            params.extend(p)
        return _sql.SQL("({})").format(_sql.SQL(" OR ").join(pedacos)), params

    # col.op.valor — o valor pode conter pontos (URL, decimal), então o split
    # é limitado aos dois primeiros separadores.
    peças = cond.split(".", 2)
    if len(peças) < 3:
        # `col.is.null` chega como 3 peças; menos que isso é sintaxe que não
        # conhecemos — falhar é melhor que adivinhar.
        raise UnsupportedFilterError(f"condição or_() não reconhecida: {cond!r}")

    coluna, op, valor = peças[0], peças[1].lower(), peças[2]
    ident = _sql.Identifier(coluna)

    if op == "is":
        if valor.lower() == "null":
            return _sql.SQL("{} IS NULL").format(ident), []
        if valor.lower() in ("true", "false"):
            return _sql.SQL("{} IS {}").format(
                ident, _sql.SQL(valor.upper())
            ), []
        raise UnsupportedFilterError(f"or_(): 'is.{valor}' não suportado")

    if op == "not":
        raise UnsupportedFilterError(f"or_(): negação não suportada em {cond!r}")

    if op == "in":
        bruto = valor.strip()
        if bruto.startswith("(") and bruto.endswith(")"):
            bruto = bruto[1:-1]
        itens = [i.strip().strip('"') for i in bruto.split(",") if i.strip()]
        if not itens:
            return _sql.SQL("FALSE"), []
        return _sql.SQL("{} = ANY(%s)").format(ident), [itens]

    if op in _OPS:
        return _sql.SQL("{} {} %s").format(ident, _sql.SQL(_OPS[op])), [valor]

    raise UnsupportedFilterError(f"or_(): operador {op!r} não suportado")


# --------------------------------------------------------------------------- #
# Query builder
# --------------------------------------------------------------------------- #


class _Query:
    """Construtor fluente que imita o ``postgrest-py`` sobre SQL puro."""

    def __init__(self, client: "PostgresClient", table: str) -> None:
        self._client = client
        self._table = table
        self._verb: Optional[str] = None
        self._columns: str = "*"
        self._rows: List[Dict[str, Any]] = []
        self._values: Dict[str, Any] = {}
        self._on_conflict: Optional[str] = None
        self._ignore_duplicates: bool = False
        self._where: List[Any] = []
        self._params: List[Any] = []
        self._order: List[Tuple[str, bool]] = []
        self._limit: Optional[int] = None
        self._offset: Optional[int] = None
        self._count_mode: Optional[str] = None
        self._head: bool = False
        self._single: bool = False

    # ---- verbos ----------------------------------------------------------- #

    def select(
        self,
        columns: str = "*",
        count: Optional[str] = None,
        head: bool = False,
    ) -> "_Query":
        self._verb = "select"
        self._columns = columns or "*"
        self._count_mode = count
        self._head = head
        return self

    def insert(
        self,
        rows: Any,
        count: Optional[str] = None,
        returning: Optional[str] = None,
    ) -> "_Query":
        self._verb = "insert"
        self._rows = [rows] if isinstance(rows, dict) else list(rows)
        return self

    def upsert(
        self,
        rows: Any,
        on_conflict: Optional[str] = None,
        ignore_duplicates: bool = False,
        count: Optional[str] = None,
        returning: Optional[str] = None,
    ) -> "_Query":
        """Espelha a assinatura do postgrest-py.

        `ignore_duplicates` é o que separa os dois upserts do PostgREST e o
        projeto usa OS DOIS. A coleta chama com ``True`` (ON CONFLICT DO
        NOTHING): uma linha já gravada por outro turno não pode ser reescrita,
        senão a observação do dia perde o que foi observado na hora certa. O
        de-para e as referências chamam com o padrão ``False`` (DO UPDATE),
        porque ali a última versão é que vale.
        """
        self._verb = "upsert"
        self._rows = [rows] if isinstance(rows, dict) else list(rows)
        self._on_conflict = on_conflict
        self._ignore_duplicates = ignore_duplicates
        return self

    def update(self, values: Dict[str, Any]) -> "_Query":
        self._verb = "update"
        self._values = dict(values)
        return self

    def delete(self) -> "_Query":
        self._verb = "delete"
        return self

    # ---- filtros ---------------------------------------------------------- #

    def _add(self, frag: Any, params: Sequence[Any]) -> "_Query":
        self._where.append(frag)
        self._params.extend(params)
        return self

    def _cmp(self, op: str, column: str, value: Any) -> "_Query":
        return self._add(
            _sql.SQL("{} {} %s").format(
                _sql.Identifier(column), _sql.SQL(_OPS[op])
            ),
            [value],
        )

    def eq(self, column: str, value: Any) -> "_Query":
        return self._cmp("eq", column, value)

    def neq(self, column: str, value: Any) -> "_Query":
        return self._cmp("neq", column, value)

    def gt(self, column: str, value: Any) -> "_Query":
        return self._cmp("gt", column, value)

    def gte(self, column: str, value: Any) -> "_Query":
        return self._cmp("gte", column, value)

    def lt(self, column: str, value: Any) -> "_Query":
        return self._cmp("lt", column, value)

    def lte(self, column: str, value: Any) -> "_Query":
        return self._cmp("lte", column, value)

    def like(self, column: str, pattern: str) -> "_Query":
        return self._cmp("like", column, pattern)

    def ilike(self, column: str, pattern: str) -> "_Query":
        return self._cmp("ilike", column, pattern)

    def in_(self, column: str, values: Iterable[Any]) -> "_Query":
        lista = list(values)
        if not lista:
            # PostgREST com lista vazia devolve zero linha; `= ANY('{}')` faz o
            # mesmo, mas ser explícito evita depender do cast de array vazio.
            return self._add(_sql.SQL("FALSE"), [])
        return self._add(
            _sql.SQL("{} = ANY(%s)").format(_sql.Identifier(column)), [lista]
        )

    def is_(self, column: str, value: Any) -> "_Query":
        if value is None or str(value).lower() == "null":
            return self._add(
                _sql.SQL("{} IS NULL").format(_sql.Identifier(column)), []
            )
        if str(value).lower() in ("true", "false"):
            return self._add(
                _sql.SQL("{} IS {}").format(
                    _sql.Identifier(column), _sql.SQL(str(value).upper())
                ),
                [],
            )
        raise UnsupportedFilterError(f"is_({column!r}, {value!r}) não suportado")

    def match(self, criteria: Dict[str, Any]) -> "_Query":
        for coluna, valor in criteria.items():
            self.eq(coluna, valor)
        return self

    def or_(self, expression: str) -> "_Query":
        partes = _split_top_level(expression)
        if not partes:
            return self
        pedacos, params = [], []
        for parte in partes:
            frag, p = _cond_to_sql(parte)
            pedacos.append(frag)
            params.extend(p)
        return self._add(
            _sql.SQL("({})").format(_sql.SQL(" OR ").join(pedacos)), params
        )

    # ---- modificadores ---------------------------------------------------- #

    def order(self, column: str, desc: bool = False) -> "_Query":
        self._order.append((column, bool(desc)))
        return self

    def limit(self, n: int) -> "_Query":
        self._limit = int(n)
        return self

    def range(self, start: int, end: int) -> "_Query":
        """Janela inclusiva nos dois extremos, como no PostgREST."""
        self._offset = int(start)
        self._limit = int(end) - int(start) + 1
        return self

    def single(self) -> "_Query":
        self._single = True
        self._limit = 1
        return self

    def maybe_single(self) -> "_Query":
        return self.single()

    # ---- montagem --------------------------------------------------------- #

    def _where_sql(self) -> Any:
        if not self._where:
            return _sql.SQL("")
        return _sql.SQL(" WHERE ") + _sql.SQL(" AND ").join(self._where)

    def _order_sql(self) -> Any:
        if not self._order:
            return _sql.SQL("")
        itens = [
            _sql.SQL("{} {}").format(
                _sql.Identifier(coluna), _sql.SQL("DESC" if desc else "ASC")
            )
            for coluna, desc in self._order
        ]
        return _sql.SQL(" ORDER BY ") + _sql.SQL(", ").join(itens)

    def _limit_sql(self) -> Tuple[Any, List[Any]]:
        frag, params = _sql.SQL(""), []
        if self._limit is not None:
            frag = frag + _sql.SQL(" LIMIT %s")
            params.append(self._limit)
        if self._offset:
            frag = frag + _sql.SQL(" OFFSET %s")
            params.append(self._offset)
        return frag, params

    def _columns_sql(self) -> Any:
        colunas = self._columns.strip()
        if colunas in ("", "*"):
            return _sql.SQL("*")
        nomes = [c.strip() for c in colunas.split(",") if c.strip()]
        # Projeção com join/alias do PostgREST (`tabela(col)`) não é suportada:
        # é relacionamento, não coluna, e traduzir por engano devolveria dado
        # de outra tabela.
        for nome in nomes:
            if "(" in nome or ":" in nome:
                raise UnsupportedFilterError(
                    f"projeção com relacionamento não suportada: {nome!r}"
                )
        return _sql.SQL(", ").join(_sql.Identifier(n) for n in nomes)

    def _build(self) -> Tuple[Any, List[Any]]:
        tabela = _sql.Identifier(self._table)
        verbo = self._verb or "select"

        if verbo == "select":
            frag_limit, p_limit = self._limit_sql()
            query = (
                _sql.SQL("SELECT ")
                + self._columns_sql()
                + _sql.SQL(" FROM ")
                + tabela
                + self._where_sql()
                + self._order_sql()
                + frag_limit
            )
            return query, list(self._params) + p_limit

        if verbo in ("insert", "upsert"):
            if not self._rows:
                raise DBError("insert/upsert sem linhas")
            colunas = list(self._rows[0].keys())
            valores = [tuple(linha.get(c) for c in colunas) for linha in self._rows]
            base = (
                _sql.SQL("INSERT INTO ")
                + tabela
                + _sql.SQL(" ({}) VALUES %s").format(
                    _sql.SQL(", ").join(_sql.Identifier(c) for c in colunas)
                )
            )
            if verbo == "upsert":
                if self._on_conflict:
                    alvo = [c.strip() for c in self._on_conflict.split(",")]
                    atualizaveis = [c for c in colunas if c not in alvo]
                    if self._ignore_duplicates:
                        base = base + _sql.SQL(" ON CONFLICT ({}) DO NOTHING").format(
                            _sql.SQL(", ").join(_sql.Identifier(c) for c in alvo)
                        )
                    elif atualizaveis:
                        sets = _sql.SQL(", ").join(
                            _sql.SQL("{} = EXCLUDED.{}").format(
                                _sql.Identifier(c), _sql.Identifier(c)
                            )
                            for c in atualizaveis
                        )
                        base = base + _sql.SQL(
                            " ON CONFLICT ({}) DO UPDATE SET "
                        ).format(
                            _sql.SQL(", ").join(_sql.Identifier(c) for c in alvo)
                        ) + sets
                    else:
                        base = base + _sql.SQL(" ON CONFLICT ({}) DO NOTHING").format(
                            _sql.SQL(", ").join(_sql.Identifier(c) for c in alvo)
                        )
                else:
                    base = base + _sql.SQL(" ON CONFLICT DO NOTHING")
            return base + _sql.SQL(" RETURNING *"), valores

        if verbo == "update":
            if not self._values:
                raise DBError("update sem valores")
            sets = _sql.SQL(", ").join(
                _sql.SQL("{} = %s").format(_sql.Identifier(c)) for c in self._values
            )
            query = (
                _sql.SQL("UPDATE ")
                + tabela
                + _sql.SQL(" SET ")
                + sets
                + self._where_sql()
                + _sql.SQL(" RETURNING *")
            )
            return query, list(self._values.values()) + list(self._params)

        if verbo == "delete":
            query = (
                _sql.SQL("DELETE FROM ")
                + tabela
                + self._where_sql()
                + _sql.SQL(" RETURNING *")
            )
            return query, list(self._params)

        raise DBError(f"verbo não suportado: {verbo}")

    # ---- execução --------------------------------------------------------- #

    def execute(self) -> DBResponse:
        total: Optional[int] = None

        if self._verb == "select" and self._count_mode:
            total = self._client._scalar(
                _sql.SQL("SELECT count(*) FROM ")
                + _sql.Identifier(self._table)
                + self._where_sql(),
                list(self._params),
            )
            if self._head:
                return DBResponse(data=[], count=total)

        query, params = self._build()

        if self._verb in ("insert", "upsert"):
            linhas = self._client._execute_values(query, params)
        else:
            linhas = self._client._fetch(query, params)

        if self._single:
            return DBResponse(data=(linhas[0] if linhas else None), count=total)
        return DBResponse(data=linhas, count=total)


# --------------------------------------------------------------------------- #
# Cliente
# --------------------------------------------------------------------------- #


class PostgresClient:
    """Cliente Postgres com a cara do ``supabase-py``.

    Mantém UMA conexão viva e reconecta se ela cair — o dashboard dispara
    dezenas de consultas por render e abrir conexão por consulta estoura o
    limite de conexões do free tier.
    """

    def __init__(self, dsn: str, connect_timeout: int = 15) -> None:
        if not _HAS_PSYCOPG:
            raise DBError(
                "psycopg2 não instalado. Rode: pip install psycopg2-binary"
            )
        if not dsn:
            raise DBError("DSN vazio — defina RAC_DB_DSN")
        self.dsn = dsn
        self.connect_timeout = connect_timeout
        self._conn = None
        self._lock = threading.RLock()

    # ---- conexão ---------------------------------------------------------- #

    def _connection(self):
        if self._conn is None or self._conn.closed:
            self._conn = psycopg2.connect(
                self.dsn, connect_timeout=self.connect_timeout
            )
            self._conn.autocommit = True
            _registrar_tipos_postgrest(self._conn)
        return self._conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None and not self._conn.closed:
                self._conn.close()
            self._conn = None

    def _run(self, fn):
        """Roda ``fn(cursor)`` reconectando uma vez se a conexão tiver caído."""
        with self._lock:
            for tentativa in (1, 2):
                try:
                    conn = self._connection()
                    with conn.cursor(cursor_factory=RealDictCursor) as cur:
                        return fn(cur)
                except (psycopg2.InterfaceError, psycopg2.OperationalError) as exc:
                    self.close()
                    if tentativa == 2:
                        raise DBError(f"conexão com o Postgres falhou: {exc}") from exc
                    logger.warning(
                        f"[DB] conexão caiu ({exc}); reconectando e repetindo"
                    )

    def _fetch(self, query: Any, params: Sequence[Any]) -> List[Dict[str, Any]]:
        def _go(cur):
            cur.execute(query, params)
            if cur.description is None:
                return []
            return [dict(r) for r in cur.fetchall()]

        return self._run(_go)

    def _execute_values(self, query: Any, valores: Sequence[Any]) -> List[Dict[str, Any]]:
        def _go(cur):
            resultado = execute_values(cur, query, valores, fetch=True)
            return [dict(r) for r in resultado] if resultado else []

        return self._run(_go)

    def _scalar(self, query: Any, params: Sequence[Any]) -> Optional[int]:
        linhas = self._fetch(query, params)
        if not linhas:
            return None
        return int(list(linhas[0].values())[0])

    # ---- API pública ------------------------------------------------------ #

    def table(self, name: str) -> _Query:
        return _Query(self, name)

    def from_(self, name: str) -> _Query:
        return _Query(self, name)

    def rpc(self, fn_name: str, params: Optional[Dict[str, Any]] = None) -> "_RPC":
        return _RPC(self, fn_name, params or {})


class _RPC:
    """``client.rpc("fn", {...}).execute()`` → ``SELECT * FROM fn(...)``."""

    def __init__(
        self, client: PostgresClient, fn_name: str, params: Dict[str, Any]
    ) -> None:
        self._client = client
        self._fn = fn_name
        self._params = params

    def execute(self) -> DBResponse:
        if self._params:
            nomes = list(self._params.keys())
            args = _sql.SQL(", ").join(
                _sql.SQL("{} => %s").format(_sql.Identifier(n)) for n in nomes
            )
            query = _sql.SQL("SELECT * FROM {}({})").format(
                _sql.Identifier(self._fn), args
            )
            valores = [self._params[n] for n in nomes]
        else:
            query = _sql.SQL("SELECT * FROM {}()").format(_sql.Identifier(self._fn))
            valores = []

        linhas = self._client._fetch(query, valores)

        # Função ESCALAR: o Postgres nomeia a única coluna com o nome da
        # função, e o PostgREST entrega o valor cru — inclusive quando o
        # retorno é jsonb. Espelhar isso não é capricho: `get_cobertura_
        # resolucao()` devolve jsonb, e o chamador em app.py faz
        # `{k: int(v) for k, v in data.items()}`. Embrulhado numa lista, esse
        # dict vira {'get_cobertura_resolucao': {...}}, o int() estoura, o
        # `except Exception` engole e o banner do topo some sem erro nenhum.
        #
        # Função TABLE/SETOF (resolver_coletas_pendentes, v_seller_*) tem
        # colunas com nome PRÓPRIO, então não casa aqui e segue como lista —
        # que é o que o PostgREST também faz.
        if (
            len(linhas) == 1
            and len(linhas[0]) == 1
            and next(iter(linhas[0])) == self._fn
        ):
            return DBResponse(data=next(iter(linhas[0].values())))

        return DBResponse(data=linhas)


# --------------------------------------------------------------------------- #
# Fábrica
# --------------------------------------------------------------------------- #


def resolve_backend_name() -> str:
    """Decide o backend a partir do ambiente. Ver docstring do módulo."""
    escolha = os.getenv("RAC_DB_BACKEND", "auto").strip().lower()
    if escolha in ("postgres", "pg", "aiven"):
        return "postgres"
    if escolha == "supabase":
        return "supabase"
    if escolha not in ("", "auto"):
        raise DBError(
            f"RAC_DB_BACKEND={escolha!r} não reconhecido "
            "(use auto, postgres ou supabase)"
        )
    return "postgres" if dsn_from_env() else "supabase"


def dsn_from_env() -> str:
    """DSN do Postgres novo. ``SUPABASE_DSN`` segue aceito por retrocompat."""
    return (
        os.getenv("RAC_DB_DSN", "").strip()
        or os.getenv("SUPABASE_DSN", "").strip()
    )


def get_client(backend: Optional[str] = None):
    """Devolve o cliente do backend ativo.

    Args:
        backend: ``"postgres"`` ou ``"supabase"``. ``None`` = resolve do
            ambiente.

    Returns:
        :class:`PostgresClient` ou o ``Client`` do ``supabase-py`` — ambos
        respondem a ``.table()`` e ``.rpc()``.

    Raises:
        DBError: se faltar credencial para o backend escolhido.
    """
    nome = backend or resolve_backend_name()

    if nome == "postgres":
        dsn = dsn_from_env()
        if not dsn:
            raise DBError(
                "backend 'postgres' pedido mas RAC_DB_DSN está vazio. "
                "Pegue o DSN no painel do provedor e mantenha o ?sslmode=require."
            )
        return PostgresClient(dsn)

    url = os.getenv("SUPABASE_URL", "").strip()
    key = os.getenv("SUPABASE_KEY", "").strip()
    if not (url and key):
        raise DBError("SUPABASE_URL/SUPABASE_KEY ausentes no .env")
    from supabase import create_client

    return create_client(url, key)


__all__ = [
    "DBError",
    "DBResponse",
    "PostgresClient",
    "UnsupportedFilterError",
    "dsn_from_env",
    "get_client",
    "resolve_backend_name",
]
