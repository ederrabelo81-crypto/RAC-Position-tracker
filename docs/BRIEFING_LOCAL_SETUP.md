# Briefing diário local — setup único

O briefing diário roda no PC coletor (Claude Code CLI local, headless),
não no Cowork/claude.ai, porque hoje não existe conector MCP Postgres
genérico no diretório de conectores do Cowork — só serviços com conector
OAuth publicado (Supabase, Neon, etc.). Ver CLAUDE.md, seção
"Briefing/resumo diário — nunca consultar o banco de memória", para o
incidente que motivou essa migração.

> **Atualização (23/09/2026):** o banco da janela quente voltou a ser o
> **Supabase** (`docs/RETORNO_SUPABASE.md`, decisão de 22/09) — a Aiven só
> existe como rollback e não recebe coleta nova desde a virada. A seção 1
> abaixo foi reescrita para apontar o conector `postgres` local pro
> Supabase; se o `claude mcp list` desta máquina ainda mostrar o conector
> com um DSN de Aiven (host `*.aivencloud.com`), ele está desatualizado —
> refaça o passo 1 antes de confiar em qualquer briefing gerado.

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

## 1. Conector MCP Postgres/Supabase (se ainda não existir)

Confirme se já existe:

```powershell
claude mcp list
```

Se `postgres` aparecer na lista mas com um DSN de Aiven (host
`*.aivencloud.com`), **remova e recrie** — apontar pra Aiven faz o briefing
ler um banco congelado desde a virada de 22/09/2026, que não recebe coleta
nova (é o rollback, não a fonte viva):

```powershell
claude mcp remove postgres
```

Adicione apontando pro **Supabase**, usando a conexão direta via **Session
Pooler** (Supabase → Project Settings → Database → Connection string →
Session pooler) — essa conexão fala Postgres puro na porta 5432, então
continua de pé mesmo quando a API REST (PostgREST) está restrita por cota
(`docs/RETORNO_SUPABASE.md`, §5.1):

```powershell
claude mcp add-json postgres '{"command":"npx","args":["-y","@modelcontextprotocol/server-postgres","<DSN Session Pooler do Supabase>"]}'
```

**Use uma credencial READ-ONLY do Supabase para esse DSN, diferente da
`SUPABASE_KEY` (service_role) de escrita usada pela coleta.** O briefing só
lê; mesmo com o allowlist de permissões da seção 2b restrito às ferramentas
certas, uma credencial read-only é a segunda camada de defesa contra um
comando mal formado escrever no banco de produção. Crie um usuário Postgres
dedicado no Supabase (SQL Editor, com a mesma conexão do Session Pooler):
`CREATE ROLE briefing_ro LOGIN PASSWORD '...'; GRANT CONNECT ON DATABASE postgres TO briefing_ro; GRANT USAGE ON SCHEMA public TO briefing_ro; GRANT SELECT ON ALL TABLES IN SCHEMA public TO briefing_ro;`
e monte o DSN com esse usuário (senha sem símbolos, ou URL-encode —
`@`→`%40`, `#`→`%23`, `/`→`%2F`, `+`→`%2B`, espaço→`%20`).

Sobre TLS: o Supabase aceita `sslmode=require` na connection string do
Session Pooler sem certificado adicional (diferente da Aiven, que exigia
`certs/aiven-ca.pem` para `sslmode=verify-full` — não é preciso replicar
esse certificado aqui).

## 2. Conector MCP Notion

Feito uma vez, com login OAuth no navegador:

```powershell
claude mcp add --transport http notion https://mcp.notion.com/mcp
```

Isso abre o navegador para autenticar; depois disso o token fica salvo e
renova sozinho — a tarefa agendada não precisa de nenhum login futuro.

## 2b. Permitir execução sem prompt de aprovação

Uma sessão agendada não tem ninguém no teclado para aprovar cada chamada de
ferramenta. Isso é configurado no `settings.json` do Claude Code — **local
seu** (`.claude/settings.local.json` na raiz do repo, ou o `settings.json`
do usuário em `~/.claude/settings.json`), nunca num flag dentro do `.bat`
versionado no repo: mantém a política de permissão como configuração sua,
revisável e reversível, em vez de hardcoded num script que todo mundo com
acesso ao repo lê. `.claude/settings.local.json` já está no `.gitignore`
deste repositório — mesmo criando dentro da pasta do projeto, ele nunca vira
configuração compartilhada por acidente.

Adicione um allowlist restrito às ferramentas que o PASSO 0-8 do prompt
realmente usa — nunca um bypass geral. Exemplo de `.claude/settings.local.json`:

```json
{
  "permissions": {
    "allow": [
      "Bash(git pull --ff-only origin main)",
      "Bash(git add docs/painel/index.html)",
      "Bash(git commit -m *)",
      "Bash(git push origin main)",
      "Bash(python scripts/render_painel_diario.py *)",
      "mcp__postgres__*",
      "mcp__notion__*"
    ]
  }
}
```

`claude mcp list` só confirma que os conectores `postgres`/`notion` estão
registrados — ele **não** lista os nomes das ferramentas de cada um, então
não dá pra tirar `mcp__postgres__*`/`mcp__notion__*` exatos dali. Ajuste os
nomes reais rodando uma vez de forma interativa
(`type docs\briefing_diario_prompt.md | claude`, sem `-p`) e observando o
nome exato de cada ferramenta que o CLI pede aprovação na tela — copie
esses nomes para o `allow`. Se você tiver a skill `update-config` instalada
nesta conta, pode usá-la para gerar/editar esse arquivo em vez de escrevê-lo
à mão — mas ela é opcional, o `settings.json` acima funciona sozinho.

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

Fora da janela 7h-10h, ou se já rodou hoje com sucesso, o `.bat` pula sem
chamar o `claude` (guarda de janela + marcador `logs\briefing_<data>.done`,
mesmo padrão de `local_scheduled_collect.bat` para a coleta — evita
republicar o Notion/painel várias vezes no mesmo dia). Para forçar mesmo
assim (teste manual, fora da janela ou já rodou hoje):

```powershell
$env:RAC_FORCE_BRIEFING = "1"
scripts\run_briefing_diario.bat
```

## Diagnóstico

| Sintoma | Causa provável |
|---|---|
| Log mostra "conector Postgres não disponível" | `claude mcp list` não lista `postgres`, ou o processo `npx` não sobe (rede/proxy). Rode o passo 1 de novo. |
| Log mostra erro do Notion (401/403) | Token OAuth expirou ou foi revogado — refaça `claude mcp add --transport http notion ...`. |
| Painel não atualiza no GitHub Pages | Cheque se o push do PASSO 8 realmente aconteceu (`git log -1 -- docs/painel/index.html`) e se o Pages está apontando pra `/docs` do `main` (passo 3). Pages pode levar 1-2 min pra propagar. |
| Tarefa não dispara às 7h | Notebook desligado/deslogado fora da janela 7h-10h — o catch-up de logon cobre isso só dentro da janela. Rode manual (seção "Testar" acima) para o dia. |
| Log mostra "ja publicado hoje - nada a fazer" | `logs\briefing_<data>.done` já existe (rodou com sucesso hoje). Esperado, não é erro — apague o arquivo ou use `RAC_FORCE_BRIEFING=1` se precisar rodar de novo no mesmo dia. |
| `claude -p` para pedindo aprovação | O allowlist de permissões local (seção 2b) não cobre uma das ferramentas que o prompt usou naquele dia — confira `settings.json` e amplie o allowlist para a ferramenta específica que travou. |

## Remover

```powershell
PowerShell -ExecutionPolicy Bypass -File scripts\setup_briefing_scheduler.ps1 -Remove
```
