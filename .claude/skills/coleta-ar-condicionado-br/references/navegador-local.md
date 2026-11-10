# Navegador local: Chrome comum + perfil dedicado + CDP

## O desenho

1. Abre-se o Chrome instalado, **sem nenhuma flag de automação**, apontando para
   uma pasta de perfil própria (`~/.coleta-ac/perfil`), com
   `--remote-debugging-port`.
2. O usuário loga nos sites nesse Chrome. Nenhum programa está conectado nessa
   hora, então o site vê um navegador comum.
3. Na coleta, o kit conecta nesse mesmo Chrome (`connect_over_cdp`) usando o
   `rebrowser-playwright`, que esconde o sinal de depuração que o Akamai procura.
4. O Chrome fica aberto entre coletas. Perfil "quente" é mais bem aceito.

## Os três caminhos que não funcionam

| Tentativa | O que acontece |
|---|---|
| Playwright abrindo o browser (`launch`, `launch_persistent_context`) | Sobe com marcas de automação. Magalu e Casas Bahia respondem 403; o Google recusa o login ("navegador pode não ser seguro"). |
| Porta de depuração no perfil **padrão** do Chrome | O Chrome 136+ ignora a flag nesse perfil. |
| **Copiar** o perfil do usuário para outra pasta | O Chrome trata como perfil realocado e desloga as contas. |

A pasta do perfil precisa nascer vazia e nunca mudar de lugar.

## Variáveis de ambiente

| Variável | Padrão | Para quê |
|---|---|---|
| `COLETA_CHROME_EXE` | detecção automática | caminho do `chrome.exe` |
| `COLETA_PERFIL_DIR` | `~/.coleta-ac/perfil` | pasta do perfil dedicado |
| `COLETA_CDP_PORTA` | `9222` | porta de depuração |
| `COLETA_FECHAR_CHROME` | desligado | `1` pede ao Chrome para encerrar ao fim (inclusive um que já estava aberto) |

## Limite de confiança da porta de depuração

Quem alcança a porta controla o Chrome e as contas logadas nele. Por isso:

- a porta só escuta em `127.0.0.1` — nunca a exponha na rede nem por túnel;
- qualquer programa rodando na mesma máquina, com o mesmo usuário, alcança a
  porta: use o kit numa máquina em que você confia;
- o kit restringe as origens aceitas à própria porta local (sem `*`);
- o kit usa o Chrome que responder na porta configurada e **não confere de
  qual perfil ele é**. Mantenha essa porta só para o perfil dedicado;
- use o perfil dedicado só para a coleta, não para navegar em outros sites.

## Armadilhas

- **Aquecer a home antes da busca.** Ir direto à URL de busca chega ao antibot
  antes de a sessão ser validada. Em sites com Akamai o kit espera o cookie
  `_abck` conter `~0~`. Se não validar, o aquecimento não é guardado e a próxima
  página tenta de novo.
- **Navegar com Referer.** A primeira busca sai da home; a página 2 sai da
  página 1. Navegação sem origem é um dos sinais que disparam tela de login.
- **Chrome fechado no meio da coleta.** É a janela do usuário: fechar, atualizar
  ou travar derruba a conexão. O erro típico é `Target page, context or browser
  has been closed`. Isso não é bloqueio. O kit reconecta até 3 vezes por coleta.
  Depois de reconectar a sessão está fria: aqueça de novo.
- **Dois Playwright na mesma thread.** Um segundo `sync_playwright()` falha com
  `Sync API inside the asyncio loop`. O `ChromeLocal` é o único dono.
- **Extensões.** O kit sobe com `--disable-extensions`. Perfil sincronizado com a
  conta Google herda extensões que abrem abas sozinhas por cima do login.
- **`--restore-last-session`.** Não adicione, nem com `=false`: é um switch por
  presença e reabriria as abas antigas.
- **User-Agent.** Não troque. O Chrome real já anuncia o que é; um User-Agent de
  outro navegador sobre um motor Chromium é incoerência que os sites detectam.
- **Cookies entre ferramentas.** Cookie emitido para o Chrome não vale num
  cliente HTTP: o antibot amarra o cookie à impressão digital de quem o recebeu.
- **Sessão que parece logada.** Cookie de login presente não prova sessão
  aceita. O teste é a busca devolver produto.

## Diagnóstico rápido

```
python kit/abrir_perfil.py --site magalu     # o Chrome abre? o login persiste?
curl http://127.0.0.1:9222/json/version      # a porta responde? (troque 9222 se usar COLETA_CDP_PORTA)
python kit/coletar.py --site magalu --keyword "ar condicionado" -v
```

Se a porta não sobe em 20 s, há outro Chrome usando a mesma pasta de perfil sem
a flag. Feche-o e abra de novo pelo `abrir_perfil.py`.
