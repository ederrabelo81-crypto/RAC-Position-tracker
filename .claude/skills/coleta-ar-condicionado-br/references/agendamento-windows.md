# Agendar no Windows

Agende só depois de alguns dias rodando à mão sem bloqueio.

## Desenho recomendado

- Três turnos: 08:00, 14:00 e 20:00. Buy box e posição mudam ao longo do dia.
- Um site por vez, em sequência. Nunca dois processos no mesmo site.
- A máquina precisa estar ligada e com o usuário logado no Windows: a coleta usa
  a janela do Chrome.

## O arquivo .bat

Modelo em `kit/coletar_turno.bat`. Ele entra na pasta do kit, roda cada site e
escreve o log **de dentro do .bat**.

O modelo é parcial de propósito — edite-o:

- roda só Mercado Livre, Amazon, Magalu e Casas Bahia. Shopee ficou fora porque
  a sessão dela vale horas e exige relogar; Leroy, porque preço e vendedor só
  são confiáveis na página do produto. Acrescente na lista se quiser;
- vem com `BUYBOX=0`: coleta posição e dados do card, **sem** ler buy box. Para
  observar a buy box mudar de dono ao longo do dia, ajuste `BUYBOX` (cada
  unidade é uma página de produto a mais por keyword e por site).

## Registrar a tarefa

```
schtasks /Create /TN "Coleta_AC_Manha" /SC DAILY /ST 08:00 /TR "\"C:\coleta\kit\coletar_turno.bat\""
schtasks /Create /TN "Coleta_AC_Tarde" /SC DAILY /ST 14:00 /TR "\"C:\coleta\kit\coletar_turno.bat\""
schtasks /Create /TN "Coleta_AC_Noite" /SC DAILY /ST 20:00 /TR "\"C:\coleta\kit\coletar_turno.bat\""
```

Marque "Executar somente quando o usuário estiver conectado".

## Armadilhas

- **Redirecionar log na ação da tarefa.** Uma ação do tipo
  `cmd /c "C:\pasta com espaco\x.bat" >> "log"` perde a primeira e a última
  aspas, o caminho quebra no espaço e a tarefa falha sem escrever log nenhum. A
  ação deve ser só o `.bat`; o log fica dentro dele.
- **Scripts 100% ASCII.** O PowerShell 5.1 lê `.ps1` sem BOM como ANSI: um
  travessão ou acento dentro de uma string fecha a string no meio e o erro
  aponta para um bloco correto. Sem acento em `.bat` e `.ps1`.
- **`.bat` que se atualiza.** Se o `.bat` fizer `git pull` em si mesmo, o `cmd`
  continua lendo o arquivo antigo por posição e quebra. Deixe a atualização num
  `.bat` separado, que chama o da coleta depois.
- **Código de saída.** `coletar.py` devolve 2 quando alguma keyword não foi
  observada. O `coletar_turno.bat` resume o turno: sai com 1 se qualquer site
  terminou diferente de zero e grava `FALHOU site=<site>` no log. Use isso para
  avisar alguém; tarefa "verde" com dado faltando é o pior caso.
- **Quem cobra a ausência?** Uma tarefa que deixa de rodar não gera erro. Tenha
  uma verificação separada ("o CSV de hoje existe?") rodando em outro horário.
