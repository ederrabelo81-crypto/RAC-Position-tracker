"""Testes da skill compartilhável `coleta-ar-condicionado-br`.

O kit é AUTOCONTIDO (vai para dealers sem o resto do repo), então duplica de
propósito alguns módulos puros. Estes testes são o que impede a cópia de
divergir da origem e o que garante que nada interno vaza junto.
"""

from __future__ import annotations

import csv
import importlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SKILL = REPO / ".claude" / "skills" / "coleta-ar-condicionado-br"
KIT = SKILL / "kit"

sys.path.insert(0, str(KIT))

from rac_kit import bloqueio, texto  # noqa: E402
from rac_kit.coleta import Opcoes, coletar_site  # noqa: E402
from rac_kit.extracao import extrair_cards, ler_buy_box  # noqa: E402
from rac_kit.navegador import argumentos_chrome, navegador_morreu  # noqa: E402
from rac_kit.saida import COLUNAS, gravar_cobertura, gravar_csv, montar_registro  # noqa: E402
from rac_kit.sites import SITES, site_generico  # noqa: E402


# ---------------------------------------------------------------------------
# A cópia não diverge da origem
# ---------------------------------------------------------------------------

class TestSincroniaComORepo:
    @pytest.mark.parametrize("modulo", ["offer_kind.py", "offer_identity.py"])
    def test_modulos_puros_sao_copia_fiel(self, modulo):
        origem = (REPO / "utils" / modulo).read_bytes()
        copia = (KIT / "rac_kit" / modulo).read_bytes()
        assert origem == copia, (
            f"utils/{modulo} mudou: copie de novo para "
            f"{(KIT / 'rac_kit' / modulo).relative_to(REPO)}"
        )

    def test_marcas_iguais_as_do_config(self):
        import config
        assert texto.MARCAS == config.BRANDS

    @pytest.mark.parametrize("bruto", [
        "R$ 1.994,91", "R$1.709,91", "R$\xa02.184,05", "R$ 1829.0", "2339",
        "13% OFFR$ 1.709,91no pix", "1.520,10", "125.0", "", None, "sem preço",
        "R$ 0,00",
    ])
    def test_preco_igual_ao_do_repo(self, bruto):
        from utils.text import parse_price_brazil
        assert texto.parse_price_brazil(bruto) == parse_price_brazil(bruto)

    @pytest.mark.parametrize("titulo,preco", [
        ("Ar Condicionado Split 9000 BTU Inverter", 1994.91),
        ("iPhone 15 Pro 256GB", 5999.0),
        ("Geladeira Midea Frost Free Inverter", 3500.0),
        ("Split Inverter 24000", 3200.0),
        ("Split Hi-Wall 12000 BTUs LG", 150.0),
        ("Clique para ver preço", None),
    ])
    def test_filtro_ac_igual_ao_do_repo(self, titulo, preco):
        from utils.text import is_valid_product
        assert texto.is_valid_product(titulo, preco) == is_valid_product(titulo, preco)

    @pytest.mark.parametrize("titulo", [
        "Ar Condicionado Springer Midea 12.000 BTUs", "Porta-carrier universal",
        "Split LG Dual Inverter 9000btu", "Ventilador de mesa", "", None,
    ])
    def test_marca_e_btu_iguais_aos_do_repo(self, titulo):
        from utils.brands import extract_brand
        from utils.shelf_insights import extract_btu
        assert texto.extract_brand(titulo) == extract_brand(titulo)
        assert texto.extract_btu(titulo) == extract_btu(titulo)

    def test_diferencas_deliberadas_da_origem(self):
        # Defeitos que seguem abertos em utils/: o kit não os copia.
        assert texto.parse_price_brazil("R$ 0.0") is None
        assert texto.extract_btu("Split Cassete 120.000 BTUs") == 120000
        assert texto.extract_btu("Ar 12.000 BTUs") == 12000
        assert texto.extract_btu("Ar 9000btu") == 9000

    @pytest.mark.parametrize("hora", range(24))
    def test_turno_igual_ao_do_repo(self, hora, monkeypatch):
        from utils.text import get_turno
        # Paridade só pelo relógio: RAC_TURNO é override da pipeline deste
        # repo e o kit, de propósito, não o implementa.
        monkeypatch.delenv("RAC_TURNO", raising=False)
        momento = datetime(2026, 11, 10, hora, 30)
        assert texto.get_turno(momento) == get_turno(momento)


# ---------------------------------------------------------------------------
# A skill é compartilhável: autocontida e sem nada interno
# ---------------------------------------------------------------------------

def _arquivos_da_skill():
    return [p for p in SKILL.rglob("*") if p.is_file() and "__pycache__" not in p.parts]


class TestCompartilhavel:
    def test_kit_nao_importa_o_repo(self):
        proibido = re.compile(
            r"^\s*(?:from|import)\s+(?:config|utils|scrapers|bestsellers|main)\b", re.M
        )
        for py in KIT.rglob("*.py"):
            assert not proibido.search(py.read_text(encoding="utf-8")), py

    def test_sem_segredo_nem_dado_interno(self):
        proibido = re.compile(
            r"supabase|service_role|api[_-]?key|telegram|aiven"
            r"|@gmail|password\s*=|token\s*=",
            re.I,
        )
        for arquivo in _arquivos_da_skill():
            achado = proibido.search(arquivo.read_text(encoding="utf-8"))
            assert not achado, f"{arquivo.relative_to(REPO)}: {achado.group(0)!r}"

    def test_sem_depara_de_sellers(self):
        # O de-para de lojistas (qual apelido é de qual loja) é inteligência
        # do projeto e não vai no kit. Nome de marketplace e de marca pode.
        from utils.seller_names import SELLER_GROUPS
        texto_skill = "\n".join(
            a.read_text(encoding="utf-8") for a in _arquivos_da_skill()
        ).lower()
        publico = " ".join(f"{s.nome} {s.home}" for s in SITES.values()).lower()
        vazados = [
            grafia
            for canonico, grafias in SELLER_GROUPS.items()
            for grafia in grafias
            if grafia.lower() != canonico.lower()
            and len(grafia) >= 8
            and grafia.lower() in texto_skill
            and grafia.lower() not in publico
        ]
        assert not vazados, vazados

    def test_skill_md_tem_frontmatter(self):
        cabecalho = (SKILL / "SKILL.md").read_text(encoding="utf-8").split("---")[1]
        assert "name: coleta-ar-condicionado-br" in cabecalho
        assert "description:" in cabecalho

    def test_referencias_citadas_existem(self):
        corpo = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        for caminho in re.findall(r"`((?:references|kit)/[\w./-]+)`", corpo):
            assert (SKILL / caminho).exists(), caminho


# ---------------------------------------------------------------------------
# Navegador
# ---------------------------------------------------------------------------

class TestNavegador:
    def test_chrome_sobe_sem_flag_de_automacao(self):
        args = " ".join(argumentos_chrome(9222, Path("/tmp/perfil")))
        assert "--remote-debugging-port=9222" in args
        assert "--user-data-dir=" in args
        # curinga desligaria a checagem de origem da porta de depuração
        for flag in ("enable-automation", "headless", "restore-last-session",
                     "disable-blink-features", "remote-allow-origins=*"):
            assert flag not in args

    def test_browser_morto_nao_e_bloqueio(self):
        erro = Exception("BrowserContext.new_page: Target page, context or browser has been closed")
        assert navegador_morreu(erro)
        assert not navegador_morreu(Exception("net::ERR_HTTP_RESPONSE_CODE_FAILURE 403"))


# ---------------------------------------------------------------------------
# Sites
# ---------------------------------------------------------------------------

class TestSites:
    @pytest.mark.parametrize("chave,href,esperado", [
        ("mercadolivre", "https://www.mercadolivre.com.br/ar-midea/p/MLB35630510", "MLB35630510"),
        ("mercadolivre", "https://produto.mercadolivre.com.br/MLB-4685950692-ar-_JM", "MLB4685950692"),
        ("mercadolivre", "https://www.mercadolivre.com.br/ofertas", None),
        ("amazon", "/Ar-Condicionado-Split/dp/B0CXYZ1234/ref=sr_1_1", "B0CXYZ1234"),
        ("amazon", "/sspa/click?url=%2FAr-Midea%2Fdp%2FB0CXYZ1234%2Fref%3Dx", "B0CXYZ1234"),
        ("magalu", "/ar-condicionado-split-midea/p/238095600/ar/arsp/", "238095600"),
        ("casasbahia", "https://www.casasbahia.com.br/ar-x/p/55012345", "55012345"),
        ("shopee", "/Ar-Condicionado-Split-i.123456.7890123", "123456.7890123"),
        ("leroy", "/ar-condicionado-split-12000-btus_1571234567", "1571234567"),
        ("leroy", "/ar-condicionado", None),
    ])
    def test_id_de_produto(self, chave, href, esperado):
        assert SITES[chave].id_produto(href) == esperado

    def test_ml_pagina_pelo_que_ja_viu(self):
        ml = SITES["mercadolivre"]
        assert ml.url_busca("ar condicionado", 1, 0).endswith("/ar-condicionado")
        assert ml.url_busca("ar condicionado", 2, 60).endswith("/ar-condicionado_Desde_61")

    def test_site_generico(self):
        loja = site_generico("https://www.loja.com.br/busca?q={q}&page={pagina}")
        assert loja.nome == "loja.com.br"
        assert loja.url_busca("split inverter", 2, 0) == (
            "https://www.loja.com.br/busca?q=split+inverter&page=2"
        )
        assert loja.id_produto("/ar-midea-12000/p") == "/ar-midea-12000/p"
        with pytest.raises(ValueError):
            site_generico("https://www.loja.com.br/busca")
        with pytest.raises(ValueError):
            site_generico("www.loja.com.br/busca?q={q}")  # sem http(s)://

    def test_barra_na_keyword_nao_cria_pasta_na_url_do_ml(self):
        url = SITES["mercadolivre"].url_busca("ar 9000/12000 btus", 1, 0)
        assert url.endswith("/ar-9000%2F12000-btus")


# ---------------------------------------------------------------------------
# Diagnóstico: evidência antes de string
# ---------------------------------------------------------------------------

def _serp_ml(n: int, extra: str = "") -> str:
    cards = "".join(
        f'<li><div class="x{i}"><a href="https://www.mercadolivre.com.br/ar-{i}/p/MLB{1000000 + i}">'
        f'<h3>Ar Condicionado Split Midea {9000 + i} BTUs Inverter</h3></a>'
        f'<s>R$ 3.999,00</s><span>R$</span><span>2.{100 + i}</span><span>,</span><span>90</span>'
        f'<span>10x R$ 219,90 sem juros</span></div></li>'
        for i in range(n)
    )
    return f"<html><body>{extra}<ul>{cards}</ul>{'<p>.</p>' * 200}</body></html>"


class TestDiagnostico:
    ml = SITES["mercadolivre"]

    def test_pagina_com_produto_vence_frase_de_login(self):
        # A SERP normal do ML carrega estas strings; o detector textual antigo
        # zerou uma coleta inteira por causa disso.
        html = _serp_ml(8, '<script src="/gz/webdevice/x.js"></script>'
                           "<div hidden>Para continuar, acesse sua conta</div>"
                           '<div class="g-recaptcha"></div>')
        assert bloqueio.diagnosticar(html, self.ml).causa == bloqueio.OK

    def test_url_de_gate_manda(self):
        veredito = bloqueio.diagnosticar(
            _serp_ml(8), self.ml,
            url="https://www.mercadolivre.com.br/gz/account-verification?go=x",
        )
        assert veredito.causa == bloqueio.LOGIN

    @pytest.mark.parametrize("html,status,causa", [
        ("<html>Access Denied</html>", 200, bloqueio.BLOQUEIO),
        ("<html>" + "x" * 5000 + "</html>", 403, bloqueio.BLOQUEIO),
        ("<html>" + "x" * 5000 + "Reference #18.2f3a</html>", 200, bloqueio.BLOQUEIO),
        ("<html>" + "x" * 5000 + '<div class="g-recaptcha"></div></html>', 200, bloqueio.CAPTCHA),
        ("<html>" + "x" * 5000 + "Para continuar, acesse sua conta</html>", 200, bloqueio.LOGIN),
        ("<html>" + "x" * 5000 + "Não encontramos resultados</html>", 200, bloqueio.VAZIA),
        ("<html>" + "x" * 5000 + "</html>", 200, bloqueio.LAYOUT),
    ])
    def test_causas_nunca_tem_a_mesma_cara(self, html, status, causa):
        assert bloqueio.diagnosticar(html, self.ml, status=status).causa == causa

    def test_url_de_captcha_da_amazon_e_captcha_nao_login(self):
        veredito = bloqueio.diagnosticar(
            "<html>" + "x" * 5000 + "</html>", SITES["amazon"],
            url="https://www.amazon.com.br/errors/validateCaptcha?x=1")
        assert veredito.causa == bloqueio.CAPTCHA

    def test_busca_vazia_com_frase_de_login_no_cabecalho_e_vazia(self):
        html = ("<html><header>Entre na sua conta</header>"
                '<div class="g-recaptcha"></div>Não encontramos resultados</html>')
        assert bloqueio.diagnosticar(html, self.ml).causa == bloqueio.VAZIA

    def test_busca_curta_com_frase_de_login_continua_ok(self):
        html = _serp_ml(2, "<div hidden>Para continuar, acesse sua conta</div>")
        assert bloqueio.diagnosticar(html, self.ml).ok
        assert bloqueio.diagnosticar(html, self.ml, status=403).causa == bloqueio.BLOQUEIO

    def test_busca_curta_nao_e_bloqueio(self):
        assert bloqueio.diagnosticar(_serp_ml(2), self.ml).ok


# ---------------------------------------------------------------------------
# Extração
# ---------------------------------------------------------------------------

class TestExtracao:
    def test_cards_na_ordem_com_preco_certo(self):
        cards = extrair_cards(_serp_ml(5), SITES["mercadolivre"])
        assert [c["posicao_geral"] for c in cards] == [1, 2, 3, 4, 5]
        primeiro = cards[0]
        assert primeiro["id_produto"] == "MLB1000000"
        assert primeiro["titulo"] == "Ar Condicionado Split Midea 9000 BTUs Inverter"
        # nem o riscado (3.999,00) nem a parcela (219,90)
        assert primeiro["preco"] == 2100.90
        assert primeiro["seller"] is None  # a busca não diz quem vende

    def test_patrocinado_separa_as_posicoes(self):
        html = """<html><body>
          <div><a href="/sspa/click?url=%2Fdp%2FB0AAAAAAA1">Ar Condicionado Elgin 12000 BTUs</a>
               <span>Patrocinado</span><span>R$ 2.000,00</span></div>
          <div><a href="/Ar-LG/dp/B0AAAAAAA2">Ar Condicionado LG Dual Inverter 12000 BTUs</a>
               <span>R$ 2.500,00</span></div>
          <div><a href="/Ar-TCL/dp/B0AAAAAAA3"><img alt="Ar Condicionado TCL 9000 BTUs Split"></a>
               <span>R$ 1.800,00</span></div>
        </body></html>"""
        cards = extrair_cards(html, SITES["amazon"], "https://www.amazon.com.br/s?k=ar")
        assert [c["patrocinado"] for c in cards] == [True, False, False]
        assert [c["posicao_organica"] for c in cards] == [None, 1, 2]
        assert cards[0]["posicao_patrocinada"] == 1
        assert cards[1]["url"] == "https://www.amazon.com.br/Ar-LG/dp/B0AAAAAAA2"
        assert cards[2]["titulo"] == "Ar Condicionado TCL 9000 BTUs Split"

    def test_classe_font_bold_nao_e_preco_riscado(self):
        html = """<html><body><div>
          <a href="/Ar-LG/dp/B0AAAAAAA2">Ar Condicionado LG Dual Inverter 12000 BTUs</a>
          <span class="font-bold text-golden">R$ 2.500,00</span></div></body></html>"""
        (card,) = extrair_cards(html, SITES["amazon"])
        assert card["preco"] == 2500.0

    def test_json_ld_vence_o_preco_do_card(self):
        ld = json.dumps({"@type": "ItemList", "itemListElement": [{"item": {
            "@type": "Product", "url": "https://www.loja.com.br/ar-gree-9000/p",
            "offers": {"price": "1899.0", "seller": {"name": "Loja X"}},
        }}]})
        html = f"""<html><body><script type="application/ld+json">{ld}</script>
          <div><a href="/ar-gree-9000/p">Ar Condicionado Gree 9000 BTUs</a>
               <span>12x R$ 199,00</span><span>R$ 2.388,00</span></div></body></html>"""
        loja = site_generico("https://www.loja.com.br/busca?q={q}")
        (card,) = extrair_cards(html, loja)
        assert card["preco"] == 1899.0

    def test_json_ld_com_ofertas_de_precos_diferentes_nao_elege_um(self):
        ld = json.dumps({"@type": "Product", "url": "https://www.loja.com.br/ar-gree-9000/p",
                         "offers": [{"price": "1899.0"}, {"price": "2050.0"}]})
        html = f"""<html><body><script type="application/ld+json">{ld}</script>
          <div><a href="/ar-gree-9000/p">Ar Condicionado Gree 9000 BTUs</a>
               <span>R$ 1.950,00</span></div></body></html>"""
        (card,) = extrair_cards(html, site_generico("https://www.loja.com.br/busca?q={q}"))
        assert card["preco"] == 1950.0  # vale o que o card mostra

    @pytest.mark.parametrize("html,esperado", [
        ("<div>Vendido e entregue por <b>Clima Sul Ar</b></div>", ("Clima Sul Ar", "rotulo")),
        ("<div><span>Vendido por</span>\n<a>Refrigera Norte</a></div>", ("Refrigera Norte", "rotulo")),
        ('<script type="application/ld+json">{"@type":"Product","offers":'
         '{"price":"10","seller":{"name":"Loja Y"}}}</script>', ("Loja Y", "json-ld")),
        ("<div>Produto indisponível</div>", (None, None)),
        ("<div>Vendido por Loja X e enviado pela Amazon</div>", ("Loja X", "rotulo")),
        ('<script type="application/ld+json">{"@type":"https://schema.org/Product",'
         '"offers":{"price":"10","seller":{"name":"Loja W"}}}</script>', ("Loja W", "json-ld")),
        # duas ofertas, dois vendedores: a página não declara vencedor
        ('<script type="application/ld+json">{"@type":["Product","Thing"],"offers":['
         '{"price":"10","seller":{"name":"A"}},{"price":"9","seller":{"name":"B"}}]}</script>',
         (None, None)),
    ])
    def test_buy_box_do_pdp(self, html, esperado):
        assert ler_buy_box(html) == esperado


# ---------------------------------------------------------------------------
# Saída
# ---------------------------------------------------------------------------

class TestSaida:
    def _registro(self, **extra):
        card = {"id_produto": "MLB1", "titulo": "Ar Condicionado Split Midea 12.000 BTUs",
                "url": "https://www.mercadolivre.com.br/ar/p/MLB1", "preco": 2100.9,
                "patrocinado": False, "seller": None, "posicao_geral": 1,
                "posicao_organica": 1, "posicao_patrocinada": None, **extra}
        return montar_registro(card, SITES["mercadolivre"], "ar condicionado",
                               datetime(2026, 11, 10, 8, 5))

    def test_registro(self):
        r = self._registro()
        assert set(r) == set(COLUNAS)
        assert (r["Turno"], r["Marca Monitorada"], r["BTU"]) == ("Abertura", "Midea", 12000)
        assert r["Tipo Anúncio"] == "UNIDADE"
        assert r["Buy Box Seller"] is None and r["Fonte Buy Box"] is None
        assert r["Offer Key"].startswith("v1|")

    def test_kit_de_instalacao_nao_e_unidade(self):
        r = self._registro(titulo="Kit Instalação Ar Condicionado Split 9000 a 12000 BTUs 3m")
        assert r["Tipo Anúncio"] != "UNIDADE"

    def test_controle_pelo_celular_nao_tira_o_ar_do_escopo(self):
        r = self._registro(
            titulo="Ar Condicionado Split 12000 BTUs Wi-Fi Controle pelo Celular")
        assert r["Tipo Anúncio"] == "UNIDADE"

    def test_geladeira_inverter_fica_marcada_fora_de_escopo(self):
        # A busca aceita anúncio de qualquer produto; a linha fica (a posição
        # é fato da página) mas não pode contar como ar condicionado.
        r = self._registro(titulo="Geladeira Midea Frost Free Inverter 411L")
        assert r["Tipo Anúncio"] == "FORA_ESCOPO"
        assert r["Posição Geral"] == 1

    def test_csv_nunca_sobrescreve_outra_coleta(self, tmp_path, monkeypatch):
        from rac_kit import saida
        fixo = datetime(2026, 11, 10, 8, 5, 30)
        monkeypatch.setattr(saida, "now_brt", lambda: fixo)  # mesmo segundo
        primeiro = gravar_csv([self._registro()], tmp_path, "amazon")
        segundo = gravar_csv([], tmp_path, "amazon")
        assert primeiro != segundo and primeiro.exists() and segundo.exists()
        assert primeiro.name == "coleta_amazon_20261110_080530.csv"
        assert len(primeiro.read_text(encoding="utf-8-sig").splitlines()) == 2

    def test_identidade_da_shopee_separa_produto_oferta_e_loja(self):
        card = {"id_produto": "123456.7890123", "titulo": "Ar Condicionado Split 9000 BTUs",
                "url": "https://shopee.com.br/Ar-Split-i.123456.7890123", "posicao_geral": 1}
        r = montar_registro(card, SITES["shopee"], "ar", datetime(2026, 11, 10, 8, 5))
        assert r["ID Produto Marketplace"] == "7890123"
        assert "123456_7890123" in r["Offer Key"]

    def test_buy_box_do_pdp_leva_a_fonte(self):
        r = self._registro(buy_box_seller="Loja Z", fonte_buy_box="rotulo")
        assert (r["Buy Box Seller"], r["Fonte Buy Box"]) == ("Loja Z", "rotulo")

    def test_csv_abre_no_excel_brasileiro(self, tmp_path):
        caminho = gravar_csv([self._registro()], tmp_path, "mercadolivre")
        bruto = caminho.read_bytes()
        assert bruto.startswith(b"\xef\xbb\xbf")
        linhas = list(csv.DictReader(caminho.open(encoding="utf-8-sig"), delimiter=";"))
        assert linhas[0]["Plataforma"] == "Mercado Livre"
        assert linhas[0]["Preço (R$)"] == "2100,90"  # vírgula: o Excel lê número
        cobertura = gravar_cobertura([{"keyword": "x", "causa": "login"}], caminho)
        assert json.loads(cobertura.read_text(encoding="utf-8"))[0]["causa"] == "login"


# ---------------------------------------------------------------------------
# Laço de coleta, com navegador falso
# ---------------------------------------------------------------------------

class _Resposta:
    def __init__(self, status):
        self.status = status


class _Mouse:
    def wheel(self, *a):
        pass

    def move(self, *a):
        pass


class _Aba:
    def __init__(self, chrome):
        self.chrome, self.url, self.mouse, self._html = chrome, "", _Mouse(), ""

    def goto(self, url, **kw):
        self.chrome.visitas.append((url, kw.get("referer")))
        resposta = self.chrome.roteiro(url)
        if isinstance(resposta, Exception):
            raise resposta
        self._html, status = resposta
        self.url = url
        return _Resposta(status)

    def content(self):
        return self._html

    def close(self):
        pass


class _ChromeFalso:
    def __init__(self, roteiro):
        self.roteiro, self.visitas, self.abas = roteiro, [], 0

    def nova_aba(self):
        self.abas += 1
        return _Aba(self)

    def aquecer(self, aba, home, esperar_abck=False):
        return True


def _opcoes(tmp_path, **kw):
    return Opcoes(pasta_logs=tmp_path, dormir=lambda s: None, espera_captcha=0, **kw)


_PDP_OK = "<html><div>Vendido por <b>Loja Teste Ar</b></div>" + "<p>x</p>" * 200 + "</html>"


class TestColeta:
    ml = SITES["mercadolivre"]

    def test_pdp_negado_para_a_leitura_e_vai_para_a_cobertura(self, tmp_path):
        def roteiro(url):
            if "/p/MLB" in url:
                return "<html>Access Denied</html>", 403
            return _serp_ml(6), 200

        chrome = _ChromeFalso(roteiro)
        registros, cobertura = coletar_site(
            chrome, self.ml, ["a", "b"], _opcoes(tmp_path, buy_box=3))
        assert len(registros) == 12 and not any(r["Buy Box Seller"] for r in registros)
        assert "página de produto negada" in cobertura[0]["detalhe"]
        assert list(tmp_path.glob("bloqueio_pdp_*.html"))  # evidência do PDP salva
        pdps = [u for u, _ in chrome.visitas if "/p/MLB" in u]
        assert len(pdps) == 1  # parou no 1º e não tentou de novo na keyword seguinte

    def test_chrome_fechado_no_pdp_nao_desliga_a_buy_box(self, tmp_path):
        pdps = []

        def roteiro(url):
            if "/p/MLB" in url:
                pdps.append(url)
                if len(pdps) == 1:
                    return Exception("Target page, context or browser has been closed")
                return _PDP_OK, 200
            return _serp_ml(6), 200

        registros, cobertura = coletar_site(
            _ChromeFalso(roteiro), self.ml, ["a", "b"], _opcoes(tmp_path, buy_box=1))
        assert "Chrome fechado" in cobertura[0]["detalhe"]
        assert registros[6]["Buy Box Seller"] == "Loja Teste Ar"  # voltou na keyword b

    def test_chrome_fechado_duas_vezes_e_sem_navegador_nao_bloqueio(self, tmp_path):
        morto = Exception("Target page, context or browser has been closed")
        chrome = _ChromeFalso(lambda url: morto)
        _, cobertura = coletar_site(chrome, self.ml, ["a", "b", "c"], _opcoes(tmp_path))
        assert [c["causa"] for c in cobertura] == ["sem_navegador"] * 3

    def test_link_so_em_script_nao_passa_como_busca_ok(self, tmp_path):
        falsos = "".join(
            f'<script>var x = \'<a href="/p/MLB{2000000 + i}">\';</script>' for i in range(8))
        html = f"<html><body>{falsos}{'<p>x</p>' * 300}</body></html>"
        chrome = _ChromeFalso(lambda url: (html, 200))
        registros, cobertura = coletar_site(chrome, self.ml, ["a"], _opcoes(tmp_path))
        assert registros == [] and cobertura[0]["causa"] == "layout"

    def test_coleta_e_pagina(self, tmp_path):
        chrome = _ChromeFalso(lambda url: (_serp_ml(6), 200))
        registros, cobertura = coletar_site(
            chrome, self.ml, ["ar condicionado"], _opcoes(tmp_path, paginas=2)
        )
        assert len(registros) == 12
        assert [c["causa"] for c in cobertura] == ["ok", "ok"]
        assert chrome.visitas[0][1] == self.ml.home          # 1ª busca vem da home
        assert chrome.visitas[1][0].endswith("_Desde_7")     # passo = itens vistos
        assert chrome.visitas[1][1] == chrome.visitas[0][0]  # Referer = SERP anterior

    def test_freio_registra_o_que_nao_foi_observado(self, tmp_path):
        chrome = _ChromeFalso(lambda url: ("<html>Access Denied</html>", 403))
        kws = [f"kw {i}" for i in range(6)]
        registros, cobertura = coletar_site(chrome, self.ml, kws, _opcoes(tmp_path))
        assert registros == []
        assert [c["causa"] for c in cobertura] == ["bloqueio"] * 3 + ["adiada"] * 3
        assert len(chrome.visitas) == 3  # parou de bater no site
        evidencia = Path(cobertura[0]["evidencia"])
        assert re.fullmatch(r"bloqueio_mercadolivre_kw_0_[0-9a-f]{6}_p1\.html", evidencia.name)
        assert evidencia.exists()
        # keywords longas com o mesmo começo não dividem o mesmo arquivo
        from rac_kit.coleta import _slug
        assert _slug("x" * 50 + " a") != _slug("x" * 50 + " b")

    def test_chrome_fechado_reconecta_em_vez_de_perder_a_keyword(self, tmp_path):
        chamadas = []

        def roteiro(url):
            chamadas.append(url)
            if len(chamadas) == 1:
                return Exception("Target page, context or browser has been closed")
            return _serp_ml(6), 200

        chrome = _ChromeFalso(roteiro)
        registros, cobertura = coletar_site(chrome, self.ml, ["ar"], _opcoes(tmp_path))
        assert len(registros) == 6 and cobertura[0]["causa"] == "ok"
        assert chrome.abas == 2

    def test_buy_box_abre_pdp_e_grava_quem_vende(self, tmp_path):
        def roteiro(url):
            if "/p/MLB" in url:
                return _PDP_OK, 200
            return _serp_ml(6), 200

        chrome = _ChromeFalso(roteiro)
        registros, _ = coletar_site(chrome, self.ml, ["ar"], _opcoes(tmp_path, buy_box=2))
        com_bb = [r for r in registros if r["Buy Box Seller"]]
        assert len(com_bb) == 2
        assert com_bb[0]["Fonte Buy Box"] == "rotulo"
        assert registros[2]["Buy Box Seller"] is None  # não observado fica vazio

    def test_pagina_negada_tambem_espera_antes_da_proxima(self, tmp_path):
        pausas = []
        opcoes = Opcoes(pasta_logs=tmp_path, dormir=pausas.append, espera_captcha=0)
        chrome = _ChromeFalso(lambda url: ("<html>Access Denied</html>", 403))
        coletar_site(chrome, self.ml, ["a", "b"], opcoes)
        minimo = self.ml.intervalo[0]
        assert sum(1 for p in pausas if p >= minimo) == 2  # uma por página negada

    def test_interrupcao_preserva_o_que_ja_foi_coletado(self, tmp_path):
        from rac_kit.coleta import Parcial
        chamadas = []

        def roteiro(url):
            chamadas.append(url)
            if len(chamadas) == 2:
                raise KeyboardInterrupt
            return _serp_ml(6), 200

        parcial = Parcial()
        with pytest.raises(KeyboardInterrupt):
            coletar_site(_ChromeFalso(roteiro), self.ml, ["a", "b"],
                         _opcoes(tmp_path), parcial)
        assert len(parcial.registros) == 6
        assert [c["keyword"] for c in parcial.cobertura] == ["a"]

    def test_busca_vazia_nao_conta_como_bloqueio(self, tmp_path):
        vazia = "<html>" + "x" * 5000 + "Não encontramos resultados</html>"
        chrome = _ChromeFalso(lambda url: (vazia, 200))
        _, cobertura = coletar_site(
            chrome, self.ml, [f"kw {i}" for i in range(5)], _opcoes(tmp_path)
        )
        assert [c["causa"] for c in cobertura] == ["vazia"] * 5
