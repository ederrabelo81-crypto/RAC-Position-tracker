@echo off
:: -----------------------------------------------------------------------------
:: collect_bestsellers.bat - Coleta "Mais Vendidos" RAC no notebook.
::
:: Roda scripts\collect_bestsellers.py (6 listas ordenadas por VENDAS: Amazon,
:: Mercado Livre, Magalu, Shopee, Leroy Merlin e Casas Bahia), grava o CSV do
:: dia, a serie historica, o brief e o Supabase, e manda o resumo no Telegram.
::
:: Usa o mesmo Chrome real e logado da coleta principal (RAC_LOCAL_CHROME=1,
:: perfil data\chrome_profile). Amazon e Mercado Livre entram pelas listas via
:: browser e a Shopee so devolve dados com a sessao logada - sem isso a lista
:: dela sai vazia e o dia perde a unica base de VELOCIDADE de venda.
::
:: A variavel de RESULTADO: a coleta principal (main.py) mede oferta, preco,
:: posicao e buy box; nenhuma delas contem volume de venda. Estas listas sao a
:: unica leitura de resultado por SKU - por isso rodam em tarefa propria e nao
:: penduradas na coleta de abertura.
::
:: Uso:
::   scripts\collect_bestsellers.bat                 -> 6 listas, 1 pagina
::   scripts\collect_bestsellers.bat --paginas 2     -> repassa argumentos
::   scripts\collect_bestsellers.bat --plataformas amazon --no-headless
:: -----------------------------------------------------------------------------

setlocal

:: Forca UTF-8 no Python: sem isto o log do Loguru (setas/acentos) estoura com
:: UnicodeEncodeError quando o stdout vai para arquivo em cp1252.
set "PYTHONUTF8=1"

:: Raiz do projeto = pasta pai deste script
for %%I in ("%~dp0..") do set "BASE_DIR=%%~fI"
cd /d "%BASE_DIR%"
if not exist logs mkdir logs

:: Chrome real e logado (perfil dedicado), igual a coleta principal.
set "RAC_LOCAL_CHROME=1"

:: Ativa a venv (aceita .venv ou venv)
if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
) else if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
) else (
    echo [AVISO] Nenhuma venv encontrada [.venv/venv] - usando Python do sistema.
)

echo.
echo === Mais Vendidos RAC: 6 listas ordenadas por vendas ===
echo.

:: Ruido do driver Node do rebrowser (stderr) vai para arquivo - mesmo padrao
:: da coleta principal, deixando o console com os logs da coleta (stdout).
python scripts\collect_bestsellers.py --notificar %* 2>>"logs\driver_stderr.log"
set "RC=%ERRORLEVEL%"

echo.
echo === Mais Vendidos concluido (exit=%RC%) ===
echo     Brief:     output\bestsellers\brief_bestsellers_^<data^>.md
echo     Serie:     data\bestsellers\master_bestsellers.csv

endlocal & exit /b %RC%
