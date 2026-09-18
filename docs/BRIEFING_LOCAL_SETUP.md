# Briefing diário local — setup único

O briefing diário roda no PC coletor (Claude Code CLI local, headless),
não no Cowork/claude.ai, porque hoje não existe conector MCP Postgres
genérico no diretório de conectores do Cowork — só serviços com conector
OAuth publicado (Supabase, Neon, etc.), e o Aiven não é um deles. Ver
CLAUDE.md, seção "Briefing/resumo diário — nunca consultar o banco de
memória", para o incidente que motivou essa migração.

Arquivos envolvidos:
- `docs/briefing_diario_prompt.md` — o prompt completo, versionado no repo.
- `scripts/run_briefing_diario.bat` — wrapper agendado (git pull + `claude -p`).
- `scripts/setup_briefing_scheduler.ps1` — registra a tarefa `RAC_Briefing_0700`
  no Task Scheduler (07:00 BRT + catch-up no logon).
- `docs/painel/index.html` — o painel, sobrescrito pelo próprio briefing
  (PASSO 8) e publicado via GitHub Pages.

Os passos abaixo são feitos **uma única vez** no PC coletor. Depois disso a
tarefa agendada roda sozinha, sem comando manual nenhum.

---

## 1. Conector MCP Postgres/Aiven (se ainda não existir)

Já deve existir, é o mesmo usado nas consultas ad-hoc do dia a dia (ver
`docs/MIGRACAO_AIVEN.md`, seção 6). Confirme com:

```powershell
claude mcp list
```

Se `postgres` não aparecer na lista, adicione:

```powershell
claude mcp add-json postgres '{"command":"npx","args":["-y","@modelcontextprotocol/server-postgres","<DSN Aiven>"]}'
```

**Use uma credencial READ-ONLY do Aiven para esse DSN, diferente da
`RAC_DB_DSN` de escrita usada pela coleta.** O briefing só lê; uma sessão
Claude rodando com `--permission-mode bypassPermissions` (obrigatório em
modo agendado, ver seção 4) não deve ter, por acidente ou por um comando mal
formado, como escrever no banco de produção. Crie um usuário Postgres
`readonly` na Aiven (Console → Users → Add user, ou
`CREATE ROLE briefing_ro LOGIN PASSWORD '...'; GRANT CONNECT ON DATABASE defaultdb TO briefing_ro; GRANT USAGE ON SCHEMA public TO briefing_ro; GRANT SELECT ON ALL TABLES IN SCHEMA public TO briefing_ro;`)
e monte o DSN com esse usuário.

Sobre TLS: o driver Node por trás desse MCP server precisa do certificado da
Aiven para `sslmode=verify-full` (ver `docs/MIGRACAO_AIVEN.md` seção 6 —
`certs/aiven-ca.pem`, já versionado no repo). `sslmode=no-verify` funciona
sem o certificado, mas valida menos a cadeia.

## 2. Conector MCP Notion

Feito uma vez, com login OAuth no navegador:

```powershell
claude mcp add --transport http notion https://mcp.notion.com/mcp
```

Isso abre o navegador para autenticar; depois disso o token fica salvo e
renova sozinho — a tarefa agendada não precisa de nenhum login futuro.

## 2b. Permitir execução sem prompt de aprovação

Uma sessão agendada não tem ninguém no teclado para aprovar cada chamada de
ferramenta. Configure isso no `settings.json` do Claude Code (não num flag
dentro do `.bat` versionado no repo — mantém a política de permissão como
configuração local sua, revisável e reversível, em vez de hardcoded num
script que todo mundo com acesso ao repo lê). Use a skill `update-config`
deste projeto (`/update-config` ou peça "configurar permissões para rodar o
briefing sem prompt") para adicionar as ferramentas específicas que essa
rotina usa (o conector `postgres`, o conector `notion`, `git add/commit/push`
neste repo) a um allowlist — evite um bypass geral de todas as permissões;
restrinja ao que o PASSO 0-8 do prompt realmente precisa.

## 3. GitHub Pages para o painel

No GitHub: **Settings → Pages → Build and deployment → Source: Deploy from a
branch → Branch: `main`, pasta `/docs` → Save.**

A partir daí, todo `git push` no `main` que muda `docs/painel/index.html`
republica o painel sozinho, sem passo manual. A URL fica em
`https://<owner>.github.io/RAC-Position-tracker/painel/`.

⚠️ **Nota de exposição:** habilitar Pages em `/docs` publica TODO o
conteúdo dessa pasta (inclusive os `.md` de documentação técnica — nenhum
tem segredo, mas confira antes se algum arquivo novo que for adicionado a
`docs/` no futuro deveria mesmo ficar público). Se preferir isolar só o
painel, uma alternativa é publicar `docs/painel/` numa branch órfã dedicada
(`gh-pages`) em vez de `/docs` do `main` — mais setup, mas nada em `docs/`
fica exposto.

## 4. Registrar a tarefa agendada

```powershell
PowerShell -ExecutionPolicy Bypass -File scripts\setup_briefing_scheduler.ps1
```

Isso cria `RAC_Briefing_0700` (07:00 diário + catch-up no logon, janela
7h–10h), rodando como o seu usuário Windows (precisa estar logado ou o
notebook precisa acordar — mesmo padrão de `RAC_Local_Manha`/`Tarde`/`Noite`
em `scripts/setup_local_scheduler.ps1`).

## Testar sem esperar o horário

```powershell
Start-ScheduledTask -TaskName "RAC_Briefing_0700"
Get-Content logs\briefing_scheduler.log -Tail 80 -Wait
```

## Diagnóstico

| Sintoma | Causa provável |
|---|---|
| Log mostra "conector Postgres não disponível" | `claude mcp list` não lista `postgres`, ou o processo `npx` não sobe (rede/proxy). Rode o passo 1 de novo. |
| Log mostra erro do Notion (401/403) | Token OAuth expirou ou foi revogado — refaça `claude mcp add --transport http notion ...`. |
| Painel não atualiza no GitHub Pages | Cheque se o push do PASSO 8 realmente aconteceu (`git log -1 -- docs/painel/index.html`) e se o Pages está apontando pra `/docs` do `main` (passo 3). Pages pode levar 1-2 min pra propagar. |
| Tarefa não dispara às 7h | Notebook desligado/deslogado fora da janela 7h-10h — o catch-up de logon cobre isso só dentro da janela. Rode manual (seção "Testar" acima) para o dia. |
| `claude -p` para pedindo aprovação | O allowlist de permissões local (seção 2b) não cobre uma das ferramentas que o prompt usou naquele dia — confira `settings.json` e amplie o allowlist para a ferramenta específica que travou. |

## Remover

```powershell
PowerShell -ExecutionPolicy Bypass -File scripts\setup_briefing_scheduler.ps1 -Remove
```
