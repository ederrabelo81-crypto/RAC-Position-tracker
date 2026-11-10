#!/usr/bin/env python3
"""Coleta ofertas de ar condicionado num marketplace, pelo Chrome local.

    python coletar.py --site mercadolivre --keyword "ar condicionado 12000 btus"
    python coletar.py --site amazon --keyword "ar condicionado split" --buy-box 10
    python coletar.py --site magalu --keywords-arquivo keywords.txt --paginas 2
    python coletar.py --url-busca "https://www.loja.com.br/busca?q={q}" \\
                      --keyword "split inverter"

Sai com código 0 se tudo foi observado e 2 se alguma keyword ficou sem dado
(bloqueio, login, CAPTCHA, layout novo, Chrome que não conectou) ou se a coleta
foi interrompida — útil para o agendador avisar. Mesmo interrompida, grava o
que já tinha coletado.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import List

from rac_kit import bloqueio
from rac_kit.coleta import Opcoes, Parcial, coletar_site
from rac_kit.navegador import ChromeLocal
from rac_kit.saida import gravar_cobertura, gravar_csv
from rac_kit.sites import SITES, site_generico

_REMEDIO = {
    bloqueio.LOGIN: "logue o perfil: python abrir_perfil.py --site <site>",
    bloqueio.CAPTCHA: "resolva o CAPTCHA na janela do Chrome e rode de novo",
    bloqueio.BLOQUEIO: "pare, espere 30+ min e reduza keywords/páginas",
    bloqueio.LAYOUT: "o site mudou: abra o HTML salvo e ajuste re_produto em sites.py",
    "adiada": "rode de novo mais tarde só com estas keywords",
    "sem_navegador": "reabra o Chrome: python abrir_perfil.py",
    "interrompida": "a coleta foi interrompida: rode de novo só com estas keywords",
}


def _keywords(args: argparse.Namespace) -> List[str]:
    lista = list(args.keyword or [])
    if args.keywords_arquivo:
        # utf-8-sig: o Bloco de Notas grava BOM, que colaria na 1ª keyword.
        linhas = Path(args.keywords_arquivo).read_text(encoding="utf-8-sig").splitlines()
        lista += [l.strip() for l in linhas if l.strip() and not l.strip().startswith("#")]
    return list(dict.fromkeys(lista))


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    alvo = parser.add_mutually_exclusive_group(required=True)
    alvo.add_argument("--site", choices=sorted(SITES))
    alvo.add_argument("--url-busca", help="loja própria: URL com {q} e {pagina}")
    parser.add_argument("--nome", help="nome da plataforma (com --url-busca)")
    parser.add_argument("--re-produto", help="regex de link de produto (com --url-busca)")
    parser.add_argument("--keyword", action="append", help="pode repetir")
    parser.add_argument("--keywords-arquivo", help="uma keyword por linha")
    parser.add_argument("--paginas", type=int, default=1)
    parser.add_argument("--buy-box", type=int, default=0, metavar="N",
                        help="abre o PDP dos N primeiros por página para ler quem vende")
    parser.add_argument("--saida", default="output")
    parser.add_argument("--logs", default="logs")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S",
    )
    log = logging.getLogger("coletar")

    if args.paginas < 1:
        parser.error("--paginas precisa ser 1 ou mais")
    if args.buy_box < 0:
        parser.error("--buy-box não pode ser negativo")
    keywords = _keywords(args)
    if not keywords:
        parser.error("informe --keyword ou --keywords-arquivo")
    if args.url_busca and args.paginas > 1 and "{pagina}" not in args.url_busca:
        parser.error("--paginas maior que 1 exige {pagina} em --url-busca "
                     "(sem isso a página 1 seria coletada de novo)")
    try:
        site = (SITES[args.site] if args.site
                else site_generico(args.url_busca, args.nome, args.re_produto))
    except ValueError as exc:
        parser.error(str(exc))
    if site.requer_login:
        log.info("%s exige conta logada no perfil (abrir_perfil.py --site %s).",
                 site.nome, site.chave)

    opcoes = Opcoes(paginas=args.paginas, buy_box=args.buy_box,
                    pasta_logs=Path(args.logs))
    parcial = Parcial()
    interrompida = False
    causa_resto = "adiada"  # causa das keywords que nem chegaram a ser tentadas
    chrome = ChromeLocal()
    try:
        if not chrome.conectar():
            interrompida, causa_resto = True, "sem_navegador"
            log.error("Não foi possível conectar ao Chrome local.")
        else:
            coletar_site(chrome, site, keywords, opcoes, parcial)
    except KeyboardInterrupt:
        interrompida, causa_resto = True, "interrompida"
        log.warning("Coleta interrompida — gravando o que já foi coletado.")
    except Exception as exc:  # noqa: BLE001 — o que já foi coletado não se perde
        interrompida, causa_resto = True, "interrompida"
        log.exception("Coleta abortada: %s", exc)
    finally:
        chrome.fechar()
    registros, cobertura = parcial.registros, parcial.cobertura

    # Keyword que nem chegou a ser tentada também é "não observada".
    tentadas = {c["keyword"] for c in cobertura}
    if interrompida and cobertura and cobertura[-1]["causa"] == bloqueio.OK:
        # A keyword em andamento parou no meio da paginação: as páginas que
        # faltaram também não foram observadas.
        ultima = cobertura[-1]
        if ultima["pagina"] < args.paginas:
            cobertura.append({
                "plataforma": site.nome, "keyword": ultima["keyword"],
                "pagina": ultima["pagina"] + 1, "causa": causa_resto,
                "detalhe": f"a coleta parou antes das páginas "
                           f"{ultima['pagina'] + 1} a {args.paginas}",
                "produtos": 0, "evidencia": None,
            })
    for keyword in keywords:
        if keyword not in tentadas:
            cobertura.append({
                "plataforma": site.nome, "keyword": keyword, "pagina": 1,
                "causa": causa_resto,
                "detalhe": "a coleta parou antes desta keyword",
                "produtos": 0, "evidencia": None,
            })

    csv_path = gravar_csv(registros, Path(args.saida), site.chave)
    gravar_cobertura(cobertura, csv_path)
    log.info("%s linhas em %s", len(registros), csv_path)

    falhas = [c for c in cobertura if c["causa"] not in (bloqueio.OK, bloqueio.VAZIA)]
    for falha in falhas:
        log.warning("NÃO OBSERVADA: %s p%s — %s. Conserto: %s",
                    falha["keyword"], falha["pagina"], falha["detalhe"],
                    _REMEDIO.get(falha["causa"], "veja o HTML salvo em logs/"))
    return 2 if falhas or interrompida else 0


if __name__ == "__main__":
    sys.exit(main())
