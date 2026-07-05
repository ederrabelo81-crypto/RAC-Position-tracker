# Automação das Coletas Autenticadas — Shopee, Magalu e Casas Bahia

> ⚠️ **Superado (Jul/2026).** Esta página descreve o caminho **antigo** (CDP +
> perfil COPIADO), que falhava porque o Chrome 136+ ignora
> `--remote-debugging-port` no perfil padrão e a cópia do perfil DESLOGA as
> contas (Shopee → 403). Para rodar no notebook com o seu Chrome logado, use
> **`docs/COLETA_LOCAL_AUTENTICADA.md`** (Chrome real + perfil dedicado, sem
> CDP). Mantido aqui como referência histórica / caminho da VM.


> **Problema (Jun/2026):** os 3 marketplaces protegidos por antibot só coletavam
> esporadicamente porque dependiam de o analista estar logado em conta pessoal,
> num perfil autenticado do Chrome (coleta manual/extensão). No banco: Magalu
> parou em 05/06, Shopee em 27/05, Casas Bahia em 26/05.
>
> **Solução implementada:** estender a infraestrutura CDP do Magalu (que já
> funciona) para servir os 3 marketplaces, com **renovação automática de
> sessões** — zero intervenção humana no dia a dia.

---

## Por que esses 3 sites exigem "estar logado no Chrome pessoal"

| Site | Proteção | O que o antibot avalia | O que quebra automação comum |
|------|----------|------------------------|------------------------------|
| Magalu | Akamai Bot Manager (sensor.js) | TLS, fingerprint JS, histórico do perfil, reputação do IP | Headless detectado; IP datacenter flagado; `Runtime.enable` do CDP vaza |
| Casas Bahia | Akamai (WAF) | Cookies `_abck`/`bm_sz` válidos + IP | IP datacenter bloqueado antes do fingerprint |
| Shopee | Anti-fraude próprio | Cookies `SPC_*` de conta logada + header `af-ac-enc-dat` gerado por JS | Sessão expira em horas; exige login real |

O denominador comum: **um Chrome genuíno, com perfil que tem histórico/login,
saindo de IP residencial** passa em tudo. A automação portanto não tenta
"enganar" o antibot — ela **reutiliza esse Chrome de verdade** de forma
programática.

---

## Arquitetura da solução (Opção 1 — implementada ✅)

```
Windows Task Scheduler (PC pessoal, IP residencial)
│
├─ RAC_Chrome_CDP_Startup (no logon)
│    └─ start_chrome_cdp.bat → Chrome real, perfil copiado do usuário,
│       porta de debug 9222 (logins Shopee/ML preservados no perfil)
│
├─ RAC_Autenticada_Manha (10:05)  ┐
└─ RAC_Autenticada_Noite (21:05)  ┘
     └─ collect_authenticated_cdp.bat
          1. Valida CDP em :9222
          2. python scripts/refresh_sessions_cdp.py --sites shopee casasbahia
             • abre abas no Chrome REAL (rebrowser-playwright, sem Runtime.enable)
             • navega home → busca de cada site (renova SPC_*/_abck/bm_sz)
             • salva utils/sessions/{site}.json (formato do session_grabber)
             • fecha só as abas que abriu
          3. python main.py --platforms magalu shopee casasbahia
             • Magalu: connect_over_cdp (fluxo já existente)
             • Shopee: curl_cffi + cookies recém-renovados (load_session)
             • Casas Bahia: warm-up Akamai + cookies frescos como semente
          4. upload_csv.py → Supabase → Telegram
```

### Por que isso resolve cada site

- **Shopee** — a causa da coleta esporádica era a sessão (`SPC_*`, `csrftoken`)
  expirar em horas e exigir re-captura manual via `session_grabber.py`. O
  `refresh_sessions_cdp.py` faz exatamente o que o grabber pedia ao humano
  (navegar logado pela busca), mas dentro do Chrome CDP onde a conta **já está
  logada no perfil**. Login manual é necessário **uma única vez** (fica salvo).
- **Casas Bahia** — o warm-up curl_cffi falhava porque o IP de datacenter era
  bloqueado antes do fingerprint. No PC pessoal o IP é residencial, e os
  cookies Akamai (`_abck`/`bm_sz`/`AKA_A2`) colhidos do Chrome real alimentam a
  cadeia existente do scraper (sessão manual → warm-up → browser fallback).
- **Magalu** — já funcionava via CDP; a coleta autenticada apenas o integra ao
  mesmo agendamento (e remove as tarefas antigas `RAC_Magalu_*` para não
  duplicar dados).

### Setup (uma vez)

```powershell
cd "C:\Users\Eder Rabelo\Downloads\rac-position-tracker"

# 0. Dependência anti-detecção (se ainda não tiver)
.venv\Scripts\activate
pip install rebrowser-playwright

# 1. Perfil CDP (se ainda não existe — copia o perfil "Eder" p/ C:\chrome-rac-cdp)
scripts\setup_cdp_profile.bat

# 2. Abrir o Chrome CDP e logar 1x na Shopee (login fica salvo no perfil)
scripts\start_chrome_cdp.bat

# 3. Registrar as tarefas agendadas (substitui as RAC_Magalu_*)
PowerShell -ExecutionPolicy Bypass -File scripts\setup_authenticated_scheduler.ps1
```

### Teste manual

```powershell
# Só renovar sessões (ver status dos cookies críticos por site):
python scripts\refresh_sessions_cdp.py --sites shopee casasbahia

# Ciclo completo (sessões + coleta + upload):
scripts\collect_authenticated_cdp.bat 1
```

### Validação diária

- `python scripts/daily_status_check.py` — PASS/FAIL por plataforma no Telegram.
- Dashboard → **🩺 Data Health** — Magalu/Shopee/Casas Bahia devem voltar a
  aparecer com registros diários e campos de buy box preenchidos.

---

## Opção 2 — Oracle VM + proxy residencial BR (caminho p/ tirar o PC da rota)

O PC pessoal continua sendo um ponto único de falha (desligado = sem coleta).
A alternativa estrutural, já apontada no `docs/DIAGNOSTICO_COLETA_JUN2026.md`,
é dar **IP residencial** à VM:

1. **Proxy residencial/móvel BR** (Soax, Bright Data, IPRoyal, Smartproxy…):
   - Custo típico: US$ 4–8/GB (coleta 2×/dia dos 3 sites ≈ 1–3 GB/mês).
   - Integração: `curl_cffi` aceita `proxies={"https": "..."}` — adicionar
     suporte via env `RAC_PROXY_URL` em `shopee.py`/`casas_bahia.py`/`magalu.py`.
   - Shopee continuaria precisando dos cookies de conta logada — manter o
     `refresh_sessions_cdp.py` no PC (ou capturar sessão semanalmente) e
     **comitar o JSON no canal seguro** (NUNCA no git) ou sincronizar via
     `scripts/sync_linux.sh`.
2. **Tailscale exit-node no PC residencial** (`scripts/setup_tailscale_linux.sh`
   já existe): a VM roteia o tráfego pelo IP de casa sem custo de proxy.
   Limitação: o PC precisa estar ligado — não elimina a dependência, só move o
   ponto de execução.

**Recomendação:** manter a Opção 1 como canal primário e evoluir para proxy
residencial na VM quando houver orçamento — aí o PC vira backup.

---

## Opção 3 — Vias oficiais (menor atrito, menor cobertura)

- **Shopee Open Platform** (https://open.shopee.com/): API oficial de afiliados/
  sellers. Estável, mas o catálogo de busca pública é limitado e exige
  aprovação de app — útil como complemento, não substitui a SERP.
- **PriceTrack como fallback de preço/seller**: os 3 marketplaces já têm preço
  diário por seller via `pricetrack_daily` (importado 06:00 BRT). O que o
  PriceTrack NÃO cobre: posição na busca, patrocinado, avaliações, buy box da
  SERP — por isso a coleta própria continua necessária para os insights.

---

## Resumo executivo

| Camada | Ferramenta | Status |
|--------|-----------|--------|
| Chrome real + perfil logado | `start_chrome_cdp.bat` + `setup_cdp_profile.bat` | ✅ já existia (Magalu) |
| Renovação automática de sessões | `scripts/refresh_sessions_cdp.py` | ✅ novo (Jun/2026) |
| Coleta unificada dos 3 sites | `scripts/collect_authenticated_cdp.bat` | ✅ novo (Jun/2026) |
| Agendamento 10:05/21:05 | `scripts/setup_authenticated_scheduler.ps1` | ✅ novo (Jun/2026) |
| Watchdog | `daily_status_check.py` + 🩺 Data Health | ✅ já existia |
| Proxy residencial na VM | env `RAC_PROXY_URL` (a implementar) | 🔜 evolução |

*Criado em Jun/2026 — sessão de correção e melhorias.*
