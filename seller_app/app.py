"""
seller_app/app.py — Track Position Seller, painel do lojista.

APLICAÇÃO SEPARADA do `app.py` da raiz, de propósito (§2 do documento):

  * o `app.py` é interno, visão da indústria, e segura uma chave que lê tudo;
  * este aqui atende gente de fora e **não tem chave de escrita**: lê só
    `seller_offer_daily`, `seller_coverage_daily` e a view de share, com a
    chave `anon` e RLS ligada (migração 016).

Pôr uma página de tenant dentro do `app.py` transformaria o isolamento num
`WHERE` em Python: um bug de filtro e o Dufrio vê o plano do Frigelar. Aqui a
fronteira é a chave e a policy, não o código.

Nada aqui é dado privado — tudo é observado-público (§2.3 do documento): a
vitrine mostra o mesmo preço e o mesmo vencedor de buy box para qualquer
visitante do marketplace. É por isso que o painel pode nomear e comparar
concorrentes livremente (aba Ranking) sem violar isolamento nenhum; o dia em
que houver custo, margem ou estoque do próprio tenant, esse dado nasce em
outro lugar (o TPS da Fase 2) e nunca entra nesta base.

Rodar local:
    streamlit run seller_app/app.py

Segredos (`.streamlit/secrets.toml` local, ou o painel do Streamlit Cloud):
    SUPABASE_URL = "https://<projeto>.supabase.co"
    SUPABASE_ANON_KEY = "<chave anon — NUNCA a service_role>"
    SELLER = "Web Continental"       # opcional: trava o painel num seller só.
                                      # Ausente = seletor livre entre todos os
                                      # sellers com dado na janela (uso: demo
                                      # compartilhada). Presente = pensado para
                                      # uma instância dedicada por tenant.
    SENHA = "<senha da demo>"        # opcional; sem ela o painel fica aberto
"""
from __future__ import annotations

import os
from datetime import date, timedelta

import pandas as pd
import streamlit as st
from supabase import create_client

try:  # Streamlit Cloud: o diretório do script (seller_app/) está no sys.path
    import seller_insights as ins
except ImportError:  # testes (AppTest.from_string) rodam a partir da raiz
    from seller_app import seller_insights as ins

st.set_page_config(page_title="Track Position Seller", page_icon="📦", layout="wide")

# KPIs 2×2 no celular: abaixo de 640 px o Streamlit empilha as colunas uma a
# uma, e os 4 cartões viravam 4 telas de rolagem. Só a linha de colunas que
# contém métrica quebra em pares (mesma regra de `assets/style.css` do painel
# interno — este app é publicado sozinho e não lê aquele arquivo).
st.markdown("""<style>
@media (max-width: 640px) {
  [data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"] [data-testid="stMetric"]):not(:has(> [data-testid="stColumn"] :is([data-testid="stPlotlyChart"], [data-testid="stDataFrame"], [data-testid="stVegaLiteChart"], [data-testid="stTable"], [data-testid="stSelectbox"], [data-testid="stMultiSelect"], [data-testid="stDateInput"], [data-testid="stTextInput"], [data-testid="stNumberInput"], [data-testid="stSlider"], [data-testid="stRadio"], [data-testid="stCheckbox"], [data-testid="stButton"], [data-testid="stDownloadButton"]))) {
    flex-direction: row !important; flex-wrap: wrap !important; gap: .5rem !important; }
  [data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"] [data-testid="stMetric"]):not(:has(> [data-testid="stColumn"] :is([data-testid="stPlotlyChart"], [data-testid="stDataFrame"], [data-testid="stVegaLiteChart"], [data-testid="stTable"], [data-testid="stSelectbox"], [data-testid="stMultiSelect"], [data-testid="stDateInput"], [data-testid="stTextInput"], [data-testid="stNumberInput"], [data-testid="stSlider"], [data-testid="stRadio"], [data-testid="stCheckbox"], [data-testid="stButton"], [data-testid="stDownloadButton"]))) > [data-testid="stColumn"] {
    flex: 1 1 calc(50% - .5rem) !important; width: calc(50% - .5rem) !important;
    min-width: calc(50% - .5rem) !important; }
  [data-testid="stMetricValue"] { font-size: 1.35rem !important; }
}
</style>""", unsafe_allow_html=True)

# ── Cores: verde-petróleo do produto, âmbar para "não observado" ────────────
ACENTO, ALERTA, NEUTRO = "#1B6E6A", "#9C5D11", "#56635F"

_GLOSSARIO = """
**Buy box** — o botão "Comprar" da página do produto. Quando vários lojistas
vendem o mesmo item, o marketplace escolhe UM para levar a venda por padrão.
Quem está com a buy box fica com a maior parte das vendas daquele produto.

**Share de buy box** — dos produtos que observamos na plataforma, em quantos
você está com a buy box. O denominador é o universo observado, não só os seus.

**Ganho / perda** — o momento em que a buy box troca de dono entre dois turnos.
A vitrine só mostra quem está com ela agora; por isso ganho e perda só existem
como evento.

**Turno** — a coleta roda 3× ao dia: 08h (Abertura), 14h (Tarde) e 20h
(Fechamento). Turno não observado fica de fora de todo número — nunca vira zero.
"""


@st.cache_resource
def _client():
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_ANON_KEY"])


PAGINA = 1000  # teto por chamada do PostgREST; ler além disso exige paginar

# `numeric`/`decimal` do Postgres chega pelo PostgREST como STRING JSON, não
# número — é assim que a API evita perder precisão de ponto flutuante em
# coluna de dinheiro. Sem esta conversão, `share.pivot_table(..., aggfunc=
# "mean")` quebra com "dtype 'str' does not support operation 'mean'" — bug
# real que chegou a ir para produção porque nenhum teste anterior tocava
# dado de verdade (o sandbox de desenvolvimento não alcança o Supabase).
_COLUNAS_NUMERICAS = {"share_buybox_pct", "preco", "posicao_mediana"}


def _tipar(df: pd.DataFrame) -> pd.DataFrame:
    """Converte para número as colunas `numeric` do Postgres, uma vez na
    borda de entrada — para que nenhum código adiante precise lembrar disso."""
    for col in _COLUNAS_NUMERICAS & set(df.columns):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


# As colunas pedidas ao PostgREST, uma vez só: o mesmo texto vai no `select()`
# e no esqueleto do dataframe vazio, para os dois não divergirem em silêncio.
_SELECT_FATO = (
    "data,turno,plataforma,superficie,offer_key,marketplace_product_id,produto,marca,"
    "preco,posicao_melhor,posicao_mediana,keywords_presente,detentor_buybox,"
    "detentor_anterior,virou_no_turno,qtd_sellers,tipo_seller,identidade_suspeita"
)
_SELECT_PERDIDOS = (
    "data,turno,plataforma,seller_canonical,marketplace_product_id,produto,marca,preco"
)


def _quadro(linhas: list[dict], select: str) -> pd.DataFrame:
    """DataFrame que carrega o ESQUEMA mesmo quando não veio nenhuma linha.

    `pd.DataFrame([])` não tem zero linhas: tem zero linhas **e zero
    colunas**. Todo `df["coluna"]` adiante vira `KeyError`, e no Streamlit
    isso não é uma tabela vazia na tela — é a página inteira morrendo antes
    de renderizar. Foi o que aconteceu quando o `SELLER` do secrets passou a
    nomear uma grafia que a canonização aposentou: a consulta voltava vazia,
    o aviso "sem oferta registrada" era escrito, e a linha seguinte derrubava
    o app com `KeyError: 'virou_no_turno'` — as abas que o aviso prometia
    ("a de Cobertura diz qual dos dois") nunca chegavam a existir.

    Declarar as colunas resolve na borda de entrada, como o `_tipar`: com a
    lista vazia o pandas materializa o esqueleto, e com linhas ele fixa
    ordem e conjunto — a resposta do PostgREST nunca contradiz o `select`.
    """
    return pd.DataFrame(linhas, columns=select.split(","))


def _todas_as_linhas(consulta, ordenar_por: list[str]) -> list[dict]:
    """Lê a consulta inteira, página a página.

    O PostgREST devolve no máximo 1.000 linhas e **não avisa** que truncou:
    um seller com 2.865 linhas na janela apareceria com um terço do histórico,
    subestimando ofertas, viradas e cobertura sem nenhum sinal na tela. A
    ordenação estável é o que garante que as páginas não se sobreponham nem
    pulem linhas entre chamadas.
    """
    for coluna in ordenar_por:
        consulta = consulta.order(coluna)
    linhas: list[dict] = []
    inicio = 0
    while True:
        lote = consulta.range(inicio, inicio + PAGINA - 1).execute().data or []
        linhas.extend(lote)
        if len(lote) < PAGINA:
            return linhas
        inicio += PAGINA


@st.cache_data(ttl=900)
def carregar_mercado(desde: date) -> pd.DataFrame:
    """Share de TODOS os sellers na janela, sem filtro por seller.

    Uma única consulta alimenta duas coisas: a lista do seletor (§ picker) e o
    ranking por plataforma (aba Ranking). É dado público — a mesma vitrine que
    qualquer visitante do marketplace vê — então nomear e ordenar concorrentes
    aqui não fura fronteira nenhuma de tenant.
    """
    cli = _client()
    linhas = _todas_as_linhas(
        cli.table("v_seller_buybox_share").select("*").gte("data", desde.isoformat()),
        # `seller_canonical` como 3º critério não é enfeite: é o que torna a
        # ordenação ÚNICA. `(data, plataforma)` se repete em toda linha do
        # mesmo dia/plataforma — sem desempate, paginação por OFFSET/LIMIT
        # não garante ordem estável entre chamadas sucessivas, e linha pode
        # sumir ou duplicar na fronteira de página (aqui, 1204 > PAGINA=1000,
        # cruza página de verdade). `(data, plataforma, seller_canonical)` é
        # a chave de agrupamento da própria view — de fato única.
        ["data", "plataforma", "seller_canonical"])
    return _tipar(pd.DataFrame(linhas))


@st.cache_data(ttl=900)
def carregar(seller: str, desde: date) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Fato do seller, share e cobertura. TTL de 15 min — a coleta é 3x/dia."""
    cli = _client()
    d = desde.isoformat()

    fato = _tipar(_quadro(_todas_as_linhas(
        cli.table("seller_offer_daily").select(_SELECT_FATO)
        .eq("seller_canonical", seller).gte("data", d),
        ["data", "turno", "plataforma", "offer_key"]), _SELECT_FATO))

    share = _tipar(pd.DataFrame(_todas_as_linhas(
        cli.table("v_seller_buybox_share").select("*")
        .eq("seller_canonical", seller).gte("data", d),
        ["data", "plataforma"])))

    cobertura = pd.DataFrame(_todas_as_linhas(
        cli.table("seller_coverage_daily").select("*").gte("data", d),
        ["data", "turno", "plataforma"]))

    return fato, share, cobertura


@st.cache_data(ttl=900)
def intervalo_dados() -> tuple[date | None, date | None]:
    """Menor e maior `data` já materializada no fato — o histórico REAL.

    A janela do painel era ancorada em `date.today()`, mas a coleta atrasa
    (roda em 3 turnos e o fato é reprocessado depois): o último dia com dado
    fica um ou dois dias atrás de hoje. Sem saber onde o histórico começa e
    termina, o slider de dias vira um controle cego — aumentar de 14 para 60
    dias não muda nada na tela quando só há 8 dias de dado, e o operador
    conclui (com razão) que "mudar os dias não muda o histórico".

    Duas leituras mínimas (`limit(1)` em cada ponta), não um agregado: a chave
    `anon` lê o fato pela policy da migração 016, mas não tem função de
    agregação exposta pelo PostgREST sem uma RPC dedicada — e a borda ordenada
    é barata (usa o índice de `data`).
    """
    cli = _client()

    def _borda(desc: bool) -> date | None:
        linha = (cli.table("seller_offer_daily").select("data")
                 .order("data", desc=desc).limit(1).execute().data or [])
        return date.fromisoformat(linha[0]["data"]) if linha else None

    return _borda(False), _borda(True)


def _teto_retencao() -> int:
    """Maior janela que a retenção do banco sustenta: retenção − 2 (padrão 28).

    Lê `RAC_SELLER_RETENTION_DAYS` do ambiente — o mesmo nome que a poda usa
    (`utils/history/store.py`); o app não importa `utils` porque é publicado
    sozinho. Ausente ou inválida → 30 (o padrão da poda). Com retenção
    maior, o slider alcança o histórico que o banco de fato guarda.
    """
    try:
        retencao = int(os.environ.get("RAC_SELLER_RETENTION_DAYS", "") or 30)
    except ValueError:
        retencao = 30
    return max(4, retencao - 2)


def _dias_max(intervalo: tuple[date | None, date | None]) -> int:
    """Teto do slider = dias de histórico que EXISTEM (limitado a [4, retenção − 2]).

    Fixar o teto em 60 fazia a metade de cima do slider ser inerte quando o
    histórico é curto — a origem do sintoma relatado. Amarrar o teto ao span
    real deixa todo o curso do slider mexer no dado, e o teto cresce sozinho
    conforme a coleta acumula dias. O piso 4 garante `min < max` para o
    `st.slider` (o mínimo é 3) mesmo com um ou dois dias só de dado.

    Teto padrão 28 (era 60): o banco guarda 30 dias do fato do seller desde
    07/11/2026 (`RAC_SELLER_RETENTION_DAYS`), e a janela cheia ancorada no
    último dia com dado pede hoje − (teto + 1). Ver `_teto_retencao`.
    """
    teto = _teto_retencao()
    min_data, max_data = intervalo
    if not min_data or not max_data:
        return teto
    span = (max_data - min_data).days + 1
    return max(4, min(teto, span))


def _janela(dias: int, intervalo: tuple[date | None, date | None]) -> date:
    """`desde` da janela, ancorado no último dia COM dado, não em hoje.

    Ancorar em `max_data` remove a zona morta que o atraso da coleta abria no
    fim baixo do slider: "3 dias" passa a valer 3 dias de dado real, não 3
    dias contados a partir de um hoje que ainda não coletou. Sem histórico
    nenhum, cai no comportamento antigo (âncora em hoje) para não quebrar.
    """
    _, max_data = intervalo
    ancora = max_data or date.today()
    return ancora - timedelta(days=dias)


def _legenda_janela(dias: int, intervalo: tuple[date | None, date | None]) -> None:
    """Diz na sidebar quanto histórico existe — o sinal que faltava.

    O sintoma relatado ("mudar os dias não muda o histórico") não tinha, antes,
    nenhuma explicação na tela: o painel não dizia quantos dias de dado
    existem, nem que a janela já os cobria por inteiro. Estas três legendas
    tornam o comportamento legível — cobertura, saturação da janela e atraso
    da coleta.
    """
    min_data, max_data = intervalo
    if not min_data or not max_data:
        st.sidebar.warning(
            "Sem dado materializado ainda — rode `build_seller_offer_daily.py`.")
        return

    span = (max_data - min_data).days + 1
    st.sidebar.caption(
        f"Dados disponíveis: {min_data:%d/%m} a {max_data:%d/%m} "
        f"({span} dia{'s' if span != 1 else ''}).")
    if dias >= span:
        st.sidebar.info(
            "A janela já cobre **todo** o histórico disponível — aumentar os "
            "dias não traz nada novo até a coleta acumular mais dias.")

    atraso = (date.today() - max_data).days
    if atraso >= 1:
        st.sidebar.caption(
            f"⚠️ Última coleta há {atraso} dia{'s' if atraso != 1 else ''} "
            f"(em {max_data:%d/%m}).")


@st.cache_data(ttl=900)
def carregar_perdidos(seller: str, desde: date) -> pd.DataFrame:
    """Produtos em que ESTE seller detinha a caixa e outro tomou.

    `seller_offer_daily` só tem linha do DETENTOR (a SERP não mostra
    perdedor) — por isso "ganhei" já sai de graça filtrando pelas próprias
    linhas (`detentor_anterior` preenchido nelas). "Perdi" é o espelho: a
    linha pertence a OUTRO seller, e é ele quem carrega `detentor_anterior`
    apontando para este. Consulta separada porque o filtro é por uma coluna
    diferente da que trava o resto da página.
    """
    cli = _client()
    linhas = _todas_as_linhas(
        cli.table("seller_offer_daily").select(_SELECT_PERDIDOS)
        .eq("detentor_anterior", seller).eq("virou_no_turno", True)
          .eq("identidade_suspeita", False).gte("data", desde.isoformat()),
        ["data", "turno", "plataforma"])
    return _tipar(_quadro(linhas, _SELECT_PERDIDOS))


def _porta() -> bool:
    """Senha simples da demo. Não é autenticação de tenant — é um cadeado."""
    senha = st.secrets.get("SENHA")
    if not senha:
        return True
    if st.session_state.get("liberado"):
        return True
    _, meio, _ = st.columns([1, 2, 1])
    with meio:
        st.markdown(
            f"<div style='margin-top:3rem;padding:1.5rem 1.75rem;border-radius:12px;"
            f"border:1px solid #d6e2e0;border-top:4px solid {ACENTO}'>"
            f"<div style='font-size:1.6rem;font-weight:700;color:{ACENTO}'>📦 Track Position Seller</div>"
            "<div style='color:#334155;margin:.35rem 0 0'>Sua buy box, sua posição e quem "
            "está levando suas vendas nos marketplaces — 3 leituras por dia.</div></div>",
            unsafe_allow_html=True)
        digitada = st.text_input("Senha de acesso", type="password")
        if digitada and digitada == senha:
            st.session_state["liberado"] = True
            st.rerun()
        elif digitada:
            st.error("Senha incorreta.")
        st.caption("Dado observado-público: a mesma vitrine que qualquer visitante vê.")
    return False


def _escolher_seller(desde: date) -> tuple[str | None, pd.DataFrame]:
    """Decide o seller da sessão: travado por secret, ou escolhido na sidebar.

    Retorna também o mercado (todos os sellers) já carregado, para não buscar
    duas vezes — o ranking da aba própria reusa o mesmo dataframe.
    """
    mercado = carregar_mercado(desde)
    travado = st.secrets.get("SELLER")

    if travado:
        st.sidebar.caption(f"Instância travada em **{travado}**.")
        return travado, mercado

    if mercado.empty:
        st.sidebar.error("Sem sellers com dado na janela selecionada.")
        return None, mercado

    ranking = (mercado.groupby("seller_canonical")["produtos_detidos"]
               .sum().sort_values(ascending=False))
    lista = ranking.index.tolist()
    padrao = lista.index("Web Continental") if "Web Continental" in lista else 0
    escolhido = st.sidebar.selectbox(
        "Seller", lista, index=padrao,
        help="Todo dado aqui é observado-público — a mesma vitrine que "
             "qualquer visitante do marketplace vê.")
    return escolhido, mercado


def _selo_cobertura(cobertura: pd.DataFrame) -> None:
    """Uma linha ANTES dos números: o último dia foi observado por inteiro?"""
    res = ins.resumo_cobertura(cobertura)
    if res.nivel == "sem_registro":
        cor, txt = ALERTA, "⚪ <b>Cobertura não registrada</b> no período — os números abaixo podem estar incompletos."
    elif res.nivel == "ok":
        cor, txt = ACENTO, (f"🟢 <b>Leitura completa</b> em {res.dia:%d/%m}: 3 turnos em "
                            f"{res.plataformas} plataforma(s).")
    else:
        falt = "; ".join(res.faltando[:4]) + (f" (+{len(res.faltando) - 4})" if len(res.faltando) > 4 else "")
        cor, txt = ALERTA, (f"🟠 <b>Leitura parcial</b> em {res.dia:%d/%m}: {res.completas}/"
                            f"{res.plataformas} plataformas com os 3 turnos · sem leitura: {falt}. "
                            "Turno sem leitura fica fora dos números — não é queda sua.")
    st.markdown(
        f"<div style='border-left:4px solid {cor};padding:.4rem .8rem;margin:.25rem 0 1rem;"
        f"background:rgba(148,163,184,.10);border-radius:6px;font-size:.88rem'>{txt}</div>",
        unsafe_allow_html=True)


def _metric(alvo, label: str, value, *, sem_seta: bool = False, **kw) -> None:
    """`st.metric` em cartão; ``sem_seta`` tira a seta de delta INFORMATIVO.

    "você lidera" ou "10 ganhos · 7 perdas" não são variação — a seta ↑ ali
    sugeria uma melhora que o número não diz. `delta_arrow`/`border` só
    existem em Streamlit recente; em versão antiga o cartão sai sem eles.
    """
    import inspect

    params = inspect.signature(st.metric).parameters
    if "border" in params:
        kw.setdefault("border", True)
    if sem_seta and "delta_arrow" in params:
        kw["delta_arrow"] = "off"
    alvo.metric(label, value, **kw)


def _fmt_brl(v) -> str:
    return "—" if v is None or pd.isna(v) else f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _csv(df: pd.DataFrame) -> bytes:
    """CSV para Excel PT-BR: `;` e BOM UTF-8."""
    return df.to_csv(index=False, sep=";", decimal=",").encode("utf-8-sig")


def main() -> None:
    if not _porta():
        return

    intervalo = intervalo_dados()
    dias_max = _dias_max(intervalo)
    dias = st.sidebar.slider("Janela (dias)", 3, dias_max, min(14, dias_max))
    desde = _janela(dias, intervalo)
    _legenda_janela(dias, intervalo)

    seller, mercado = _escolher_seller(desde)
    if not seller:
        return

    cab, glos = st.columns([5, 1])
    cab.title(f"📦 {seller}")
    cab.caption(
        "Buy box, posição e concorrência nos marketplaces · Track Position Seller · "
        "coleta em 3 turnos (08h · 14h · 20h)."
    )
    with glos:
        with st.popover("❔ O que é isto?", use_container_width=True):
            st.markdown(_GLOSSARIO)

    fato, share, cobertura = carregar(seller, desde)
    perdidos = carregar_perdidos(seller, desde)
    _selo_cobertura(cobertura)

    vazio = fato.empty
    if vazio:
        # Sem KPI, mas as abas CONTINUAM: a de Cobertura é justamente o que
        # separa "não houve oferta" de "a coleta não rodou", e esconder ela
        # aqui tiraria a resposta exatamente no caso em que a pergunta importa.
        st.warning(
            f"Sem oferta registrada para **{seller}** desde {desde:%d/%m}. "
            "Isso pode ser ausência real **ou** coleta que não rodou — a aba "
            "**Cobertura** abaixo diz qual dos dois."
        )
        if st.secrets.get("SELLER"):
            # TERCEIRA causa possível, que a aba Cobertura NÃO consegue
            # descartar: instância travada num nome que a canonização
            # aposentou. É HIPÓTESE, não diagnóstico (seller novo, dia parado e
            # coleta que não rodou dão o mesmo estado) — por isso `st.info`
            # condicionado a "se a coleta rodou", nunca um `st.error`.
            st.info(
                f"**Se a coleta rodou** (veja a aba Cobertura), sobra conferir o "
                f"nome. Esta instância está travada em **{seller}** pelo secret "
                "`SELLER`, comparado **literalmente** com `seller_canonical` — e "
                "a canonização aposenta grafias: quando uma vira variante de "
                "outra, o canônico muda e o secret velho passa a apontar para um "
                "seller que não existe mais, sem erro nenhum na resposta."
            )
            if not mercado.empty:
                nomes = (mercado.groupby("seller_canonical")["produtos_detidos"]
                         .sum().sort_values(ascending=False).index.tolist())
                st.caption(
                    "Sellers com buy box observada na janela: "
                    + ", ".join(f"`{n}`" for n in nomes[:15])
                    + (f" … e mais {len(nomes) - 15}." if len(nomes) > 15 else "."))

    # ── Só marketplace entra em KPI. Loja própria o lojista joga sozinho, e
    #    identidade suspeita é chave colapsada: nem numerador, nem denominador.
    limpo = (fato[(fato["superficie"] == "marketplace") & (~fato["identidade_suspeita"])]
             if not vazio else fato)
    ganhos = limpo[limpo["virou_no_turno"].fillna(False)] if not vazio else limpo
    ranking = ins.ranking_relativo(mercado, seller)
    agir = ins.onde_agir(perdidos, limpo)

    if not vazio:
        # 4 cartões (2×2 no celular): cada um com comparação, nunca número solto.
        linha1 = st.columns(2)
        linha2 = st.columns(2)
        fotos = ins.share_atual_e_anterior(share, dias=7)
        atual, comp, ant = fotos["atual"], fotos["atual_comp"], fotos["anterior"]
        delta = None
        if comp and ant and comp.pct is not None and ant.pct is not None:
            delta = f"{comp.pct - ant.pct:+.1f} p.p. em 7 dias"
        _metric(
            linha1[0], "Share de buy box",
            f"{atual.pct:.1f}%" if atual.pct is not None else "—",
            delta=delta,
            help=(f"{atual.detidos} de {atual.universo} produtos com buy box observada, "
                  "no último dia de cada plataforma (soma/soma). O delta compara só as "
                  "plataformas observadas nos dois momentos."))
        if not ranking.empty:
            melhor = ranking.iloc[0]
            _metric(
                linha1[1], "Posição no ranking",
                f"#{int(melhor['posicao'])} de {int(melhor['total'])}",
                sem_seta=True,
                delta=(f"líder: {melhor['lider']} (+{melhor['gap_lider_pp']:.1f} p.p.)"
                       if melhor["posicao"] > 1 else "você lidera"),
                delta_color="off",
                help=f"Ranking de share de buy box em {melhor['plataforma']} "
                     f"({melhor['data']:%d/%m}). Todas as plataformas na aba 🥊 Ranking.")
        else:
            _metric(linha1[1], "Posição no ranking", "—")
        saldo = len(ganhos) - len(perdidos)
        _metric(
            linha2[0], "Saldo de viradas", f"{saldo:+d}", sem_seta=True,
            delta=f"{len(ganhos)} ganhos · {len(perdidos)} perdas", delta_color="off",
            help="Ganho = você tomou a buy box de outro seller. Perda = outro tomou a sua.")
        abertas = int((~agir["recuperado"]).sum()) if not agir.empty else 0
        _metric(
            linha2[1], "Perdas abertas", abertas,
            help="Produtos que você perdeu e não recuperou até o último turno observado. "
                 "Lista completa na aba 🎯 Onde agir.")

    aba0, aba1, aba2, aba3, aba4, aba5 = st.tabs([
        "🎯 Onde agir", "📈 Share", "🏆 Ganhos e perdas", "🏷️ Marcas e posição",
        "🥊 Ranking", "🩺 Cobertura",
    ])

    with aba0:
        st.markdown("##### Perdas de buy box, das mais fáceis de reverter para as mais difíceis")
        if agir.empty:
            st.info("Nenhuma perda de buy box observada na janela. 🎉")
        else:
            st.caption(
                "Para cada produto que você perdeu: quem levou, o preço dele no turno da "
                "perda e o seu último preço antes dela. Diferença negativa = ele estava "
                "mais barato. Preço não é o único critério do marketplace (reputação, "
                "frete e estoque também pesam) — use como ponto de partida.")
            vis = agir.copy()
            vis["Situação"] = vis["recuperado"].map({True: "✅ recuperado", False: "⏳ aberta"})
            st.dataframe(
                vis[["Situação", "plataforma", "produto", "levou", "preco_dele",
                     "seu_preco", "diferenca_pct", "data", "turno"]],
                use_container_width=True, hide_index=True,
                column_config={
                    "plataforma": "Plataforma", "produto": "Produto", "levou": "Quem levou",
                    "preco_dele": st.column_config.NumberColumn("Preço dele", format="R$ %.2f"),
                    "seu_preco": st.column_config.NumberColumn("Seu último preço", format="R$ %.2f"),
                    "diferenca_pct": st.column_config.NumberColumn("Diferença", format="%+.1f%%"),
                    "data": st.column_config.DateColumn("Perdeu em", format="DD/MM"),
                    "turno": "Turno",
                })
            st.download_button("⬇️ Baixar lista (CSV)", _csv(agir),
                               f"onde_agir_{seller}_{desde:%Y%m%d}.csv", "text/csv",
                               key="dl_agir")

    with aba1:
        if share.empty:
            st.info("Sem share no período — nenhum produto seu detinha a BB.")
        else:
            pivo = share.pivot_table(index="data", columns="plataforma",
                                     values="share_buybox_pct", aggfunc="mean")
            st.line_chart(pivo, height=320, color=ins.cores_plataformas(pivo.columns),
                          y_label="Share de buy box (%)", x_label="")
            st.dataframe(
                share.sort_values(["data", "share_buybox_pct"], ascending=[False, False])[
                    ["data", "plataforma", "produtos_detidos", "produtos_universo",
                     "share_buybox_pct"]
                ],
                use_container_width=True, hide_index=True,
                column_config={
                    "data": st.column_config.DateColumn("Data", format="DD/MM/YYYY"),
                    "plataforma": "Plataforma",
                    "produtos_detidos": "Com a BB",
                    "produtos_universo": "Universo observado",
                    "share_buybox_pct": st.column_config.ProgressColumn(
                        "Share", format="%.1f%%", min_value=0, max_value=100),
                })
            st.caption(
                "O denominador é o universo de produtos **observados** na "
                "plataforma, não as suas linhas. Sobre as suas linhas o número "
                "daria sempre 100%: a vitrine só mostra quem detém a BB."
            )

    with aba2:
        col_g, col_p = st.columns(2)
        col_g.metric("Ganhos na janela", len(ganhos))
        col_p.metric("Perdidos na janela", len(perdidos))
        st.caption(
            "Ganhei = tomei a BB de outro seller. Perdi = outro seller tomou "
            "a minha. Os dois só existem como EVENTO — a foto de um turno nunca "
            "mostra \"perdedor\", só quem está com a BB agora."
        )
        _cfg_evento = {
            "data": st.column_config.DateColumn("Data", format="DD/MM/YYYY"),
            "turno": "Turno", "plataforma": "Plataforma", "produto": "Produto",
            "marca": "Marca", "preco": st.column_config.NumberColumn("Preço", format="R$ %.2f"),
        }

        st.markdown("##### ✅ Produtos que você ganhou")
        if ganhos.empty:
            st.info("Nenhum ganho de buy box observado na janela.")
        else:
            st.dataframe(
                ganhos[["data", "turno", "plataforma", "produto", "marca",
                        "detentor_anterior", "preco"]].sort_values("data", ascending=False),
                use_container_width=True, hide_index=True,
                column_config={**_cfg_evento, "detentor_anterior": "Tomou de"})

        st.markdown("##### ❌ Produtos que você perdeu")
        if perdidos.empty:
            st.info("Nenhuma perda de buy box observada na janela.")
        else:
            st.dataframe(
                perdidos[["data", "turno", "plataforma", "produto", "marca",
                          "seller_canonical", "preco"]].sort_values("data", ascending=False),
                use_container_width=True, hide_index=True,
                column_config={**_cfg_evento, "seller_canonical": "Levou",
                               "preco": st.column_config.NumberColumn("Preço de quem levou",
                                                                      format="R$ %.2f")})

    with aba3:
        detidos_marca = limpo[limpo["detentor_buybox"] == True]  # noqa: E712
        st.markdown("##### Portfólio — produtos detidos por marca")
        if detidos_marca.empty:
            st.info("Sem produto com buy box detida na janela.")
        else:
            # Conta PRODUTO (marketplace_product_id), não oferta: a mesma
            # ligação produto+marca pode render mais de uma offer_key ao
            # longo da janela (a URL canônica muda, ou o produto cai no
            # degrau de hash em vez do de id) — contar offer_key infla a
            # marca. Fallback pro offer_key só nas linhas sem id de produto.
            chave_produto = detidos_marca["marketplace_product_id"].fillna(
                detidos_marca["offer_key"])
            por_marca = (detidos_marca.assign(_produto=chave_produto)
                         .groupby("marca")["_produto"]
                         .nunique().sort_values(ascending=False))
            # `st.bar_chart` roda em cima do Vega-Lite, que ordena eixo
            # nominal/ordinal ALFABETICAMENTE por padrão — ignora a ordem do
            # `sort_values` acima. Índice como categórico ORDENADO é o único
            # jeito de fixar no gráfico a ordem decrescente que o pandas já
            # calculou (testado contra a versão exata do deploy: sem isto o
            # spec sai com `"sort": None` e as barras voltam para A→Z).
            ordem = pd.CategoricalDtype(categories=por_marca.index, ordered=True)
            por_marca.index = por_marca.index.astype(ordem)
            st.bar_chart(por_marca, height=280)
            sem_buybox_exposta = int(limpo["detentor_buybox"].isna().sum())
            if sem_buybox_exposta:
                st.caption(
                    f"⚠️ {sem_buybox_exposta} ofertas ficaram fora deste gráfico: "
                    "estão em plataformas que não expõem vencedor de buy box na "
                    "vitrine (ex.: Casas Bahia). A Amazon passou a expor via PDP "
                    "(coletor Amazon-only no GitHub Actions, Set/2026), então já "
                    "entra aqui."
                )

        st.markdown("##### Posição mediana por plataforma")
        if limpo.empty or limpo["posicao_mediana"].isna().all():
            st.info("Sem dado de posição na janela.")
        else:
            # median(), não mean(): a coluna já é a mediana POR OFERTA
            # (entre keywords, dentro de um turno); agregar várias ofertas
            # com mean() vira "média das medianas", que não é o que o
            # título promete. median() é o mais próximo que dá pra honrar
            # o rótulo sem ter a posição bruta por keyword nesta camada.
            pos = (limpo.groupby(["data", "plataforma"])["posicao_mediana"]
                  .median().reset_index()
                  .pivot(index="data", columns="plataforma", values="posicao_mediana"))
            st.line_chart(pos, height=280, color=ins.cores_plataformas(pos.columns),
                          y_label="Posição mediana (menor = melhor)", x_label="")
            st.caption(
                "Quanto menor, melhor — é a posição mediana entre as keywords em "
                "que a oferta apareceu no turno. Não soma entre plataformas nem "
                "vira ranking absoluto: é relativa a cada busca."
            )

        if not limpo.empty and limpo["tipo_seller"].notna().any():
            st.markdown("##### Como você aparece na vitrine")
            tipos = limpo["tipo_seller"].value_counts()
            st.dataframe(
                tipos.rename_axis("Tipo de seller").reset_index(name="Ofertas"),
                use_container_width=True, hide_index=True)

    with aba4:
        plataformas_do_seller = sorted(share["plataforma"].unique()) if not share.empty else []
        if not plataformas_do_seller or mercado.empty:
            st.info("Sem ranking disponível — este seller não detém buy box em nenhuma plataforma na janela.")
        else:
            # Cada plataforma no seu próprio último dia observado (ver KPI): a
            # Amazon materializa com atraso e o max() global de `data` a apagava
            # deste ranking. Assim ela aparece mesmo um dia defasada.
            ultimo_por_plat = mercado.groupby("plataforma")["data"].transform("max")
            foto = mercado[mercado["data"] == ultimo_por_plat].copy()
            st.caption("Fotografia do último dia observado de cada plataforma.")
            if not ranking.empty:
                st.markdown("##### Sua posição em cada plataforma")
                st.dataframe(
                    ranking, use_container_width=True, hide_index=True,
                    column_config={
                        "plataforma": "Plataforma",
                        "data": st.column_config.DateColumn("Observado em", format="DD/MM"),
                        "posicao": st.column_config.NumberColumn("Posição", format="#%d"),
                        "total": "Sellers",
                        "share_pct": st.column_config.ProgressColumn(
                            "Seu share", format="%.1f%%", min_value=0, max_value=100),
                        "lider": "Líder",
                        "share_lider": st.column_config.NumberColumn("Share do líder", format="%.1f%%"),
                        "gap_lider_pp": st.column_config.NumberColumn(
                            "Distância ao líder", format="%.1f p.p."),
                    })
            foto["posicao"] = foto.groupby("plataforma")["share_buybox_pct"] \
                                   .rank(ascending=False, method="min").astype(int)
            for plataforma in plataformas_do_seller:
                bloco = foto[foto["plataforma"] == plataforma].sort_values("posicao")
                total = len(bloco)
                linha_seller = bloco[bloco["seller_canonical"] == seller]
                if linha_seller.empty:
                    continue
                minha_posicao = int(linha_seller["posicao"].iloc[0])
                dia_plat = pd.to_datetime(bloco["data"].iloc[0]).strftime("%d/%m")
                st.markdown(
                    f"##### {plataforma} — você é **#{minha_posicao} de {total}** "
                    f"· observado em {dia_plat}")
                topo = bloco.head(8).copy()
                if minha_posicao > 8:
                    topo = pd.concat([topo, linha_seller])
                topo["Você"] = topo["seller_canonical"].eq(seller).map({True: "✅", False: ""})
                st.dataframe(
                    topo[["posicao", "seller_canonical", "Você", "produtos_detidos",
                          "produtos_universo", "share_buybox_pct"]],
                    use_container_width=True, hide_index=True,
                    column_config={
                        "posicao": "#", "seller_canonical": "Seller",
                        "produtos_detidos": "Com a BB", "produtos_universo": "Universo",
                        "share_buybox_pct": st.column_config.ProgressColumn(
                            "Share", format="%.1f%%", min_value=0,
                            max_value=float(max(100.0, bloco["share_buybox_pct"].max()))),
                    })

    with aba5:
        st.markdown(
            "**Coleta ausente não é mercado vazio.** Turno sem leitura aparece "
            "aqui como não observado, e as células dele ficam fora de todo "
            "número acima — nunca viram zero."
        )
        if cobertura.empty:
            st.warning("Sem registro de cobertura no período.")
        else:
            # Conta turno OBSERVADO. Contar linhas presentes daria cobertura
            # completa mesmo quando as três células estão marcadas como não
            # observadas — que é exatamente o caso que esta aba existe para expor.
            resumo = (cobertura.assign(observado=cobertura["observado"].astype(bool))
                      .groupby(["data", "plataforma"])
                      .agg(turnos_observados=("observado", "sum"),
                           linhas=("linhas", "sum"))
                      .reset_index())
            faltantes = resumo[resumo["turnos_observados"] < 3]
            if faltantes.empty:
                st.success("Os 3 turnos foram observados em todas as plataformas.")
            else:
                st.warning(f"{len(faltantes)} combinações data×plataforma com menos de 3 turnos.")
                st.dataframe(faltantes, use_container_width=True, hide_index=True)

    if not vazio:
        st.download_button(
            "⬇️ Baixar todas as suas ofertas da janela (CSV)", _csv(limpo),
            f"ofertas_{seller}_{desde:%Y%m%d}.csv", "text/csv", key="dl_fato")

    suspeitas = int(fato["identidade_suspeita"].sum()) if not vazio else 0
    if suspeitas:
        st.divider()
        st.caption(
            f"⚠️ {suspeitas} ofertas ficaram fora dos números por identidade "
            "ambígua (chave de oferta colapsada na origem). Estão excluídas de "
            "propósito: entrariam somando produtos diferentes como se fossem um."
        )


if __name__ == "__main__":
    main()
