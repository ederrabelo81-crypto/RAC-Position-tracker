#!/usr/bin/env python3
"""Abre o Chrome do perfil dedicado para você logar nos sites, uma vez.

Nenhum programa fica conectado ao Chrome durante o login: o site vê um
navegador comum. Logue com a SUA conta, navegue um pouco e feche esta janela
do terminal com Enter. O login fica salvo na pasta do perfil.

    python abrir_perfil.py --site shopee
    python abrir_perfil.py --site mercadolivre
    python abrir_perfil.py --url https://www.minhaloja.com.br/
"""

from __future__ import annotations

import argparse
import sys
import time

from rac_kit.navegador import abrir_chrome, endpoint_cdp, pasta_do_perfil, porta_cdp
from rac_kit.sites import SITES


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--site", choices=sorted(SITES), help="marketplace a logar")
    parser.add_argument("--url", help="URL avulsa (loja própria)")
    args = parser.parse_args()

    url = args.url or (SITES[args.site].home if args.site else "about:blank")
    porta, perfil = porta_cdp(), pasta_do_perfil()

    if endpoint_cdp(porta):
        print(f"Já existe um Chrome deste perfil aberto (porta {porta}).")
        print(f"Abra nele: {url}")
    else:
        processo = abrir_chrome(porta, perfil, url)
        if processo is None:
            return 1
        for _ in range(40):
            if endpoint_cdp(porta):
                break
            time.sleep(0.5)
        else:
            print(f"O Chrome não abriu a porta {porta} em 20 s. Feche as outras "
                  "janelas do Chrome que usam este perfil e tente de novo.")
            # Não deixa um Chrome sem porta de depuração segurando o perfil.
            try:
                processo.terminate()
            except OSError:
                pass
            return 1
        print(f"Chrome aberto no perfil {perfil}")

    print("\n1. Faça login com a sua conta, na janela que abriu.")
    print("2. Faça uma busca qualquer e abra um produto.")
    print("3. Volte aqui e aperte Enter. Pode deixar o Chrome aberto.\n")
    try:
        input("Enter para terminar... ")
    except (EOFError, KeyboardInterrupt):
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
