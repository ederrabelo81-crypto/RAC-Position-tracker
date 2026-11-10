# Kit de coleta — ar condicionado nos marketplaces do Brasil

Coleta posição na busca, preço, patrocinado e quem vende (buy box), usando o
seu Chrome, a sua conta e a sua internet.

## Requisitos

- Windows, macOS ou Linux com Google Chrome instalado
- Python 3.10 ou mais novo
- Internet residencial (em servidor de nuvem os sites tendem a negar)

## Passo a passo

```
pip install -r requirements.txt
python abrir_perfil.py --site mercadolivre     # logue uma vez, com a sua conta
python coletar.py --site mercadolivre --keyword "ar condicionado 12000 btus"
```

O resultado sai em `output/` (CSV que abre no Excel; em pandas, `sep=";", decimal=","`) com um arquivo
`.cobertura.json` ao lado, dizendo o que foi e o que não foi observado.

## Mais usos

```
python coletar.py --site amazon --keyword "ar condicionado split" --buy-box 10
python coletar.py --site magalu --keywords-arquivo keywords.txt --paginas 2
python coletar.py --url-busca "https://www.sualoja.com.br/busca?q={q}" --keyword "split"
```

Sites prontos: `mercadolivre`, `amazon`, `magalu`, `casasbahia`, `shopee`,
`leroy`.

## Se voltar sem dado

O programa diz a causa e o conserto, e salva a página em `logs/`. Não aumente
o volume nem repita em seguida: bloqueio se resolve esperando.

## Licença

MIT — veja o arquivo `LICENSE` na pasta da skill. Pode usar, copiar e adaptar,
mantendo o aviso de copyright.

## Uso responsável

Use a sua própria conta, colete só dado público de oferta, mantenha o volume
baixo. Respeitar os termos de uso de cada site é responsabilidade de quem roda.
