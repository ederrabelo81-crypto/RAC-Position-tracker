# UX dos painéis — ganhos rápidos + Fase 1 (Nov/2026)

Revisão de design/UX do `app.py` (painel interno). O `seller_app/` segue com o
roteiro próprio (ver "Próximas fases").

## O que mudou

### Navegação por pergunta, no topo
- `st.navigation(position="top")`: as 21 páginas saíram da barra lateral e
  viraram 8 seções no cabeçalho (**Hoje · Prateleira · Buy box e sellers ·
  Preço · Demanda · Produto · Operação · Admin**). A barra lateral ficou só com
  os filtros globais, que antes ficavam abaixo de 23 botões. No celular o
  Streamlit recolhe as seções no menu lateral sozinho.
- Antes: 23 páginas em 3 grupos (17 em INSIGHTS), feitas de `st.button` +
  `st.rerun()`. Cada clique rodava o script **duas vezes** e nenhuma página
  tinha URL própria.
- Cada página tem `url_path` estável (`/buy-box`, `/preco-do-dia`, …), então
  dá para mandar o link.
- Páginas que respondiam a mesma pergunta viraram **uma página com visões**
  (`_SUBVIEWS`, seletor `st.segmented_control`, que roda só a visão escolhida):
  - 🏆 **Buy Box** = BuyBox Position + Share of Buy Box
  - 🚨 **Alertas** = Top Movers + Price Anomalies
- Todos os nomes de página em português. Nomes antigos continuam valendo em
  `_nav_page` / `_current_page` (`_LEGACY_PAGES`).
- Requer `streamlit>=1.46` (`position="top"`).

### Selo de frescor no topo de toda página
`utils/freshness.py` (puro) cruza o livro-razão (`pipeline_heartbeat`) com o
contrato (`utils/pipeline_registry.jobs_do_dia`), só para jobs que gravam em
`coletas`. O resultado é uma linha: 🟢 em dia / 🟠 parcial / 🔴 falha (com o
turno e o executor) / ⚪ não verificado. Fica 5 min em cache. Se o livro-razão
estiver indisponível, o selo mostra **"Frescor não verificado"**, nunca "em
dia".

### Uma cor por marca, e a interface fora do azul da Midea
- `utils/ui_tokens.py` é a fonte única de cor de marca e plataforma. Antes havia
  duas paletas que se contradiziam: a Midea era `#1a56db` nos gráficos e
  `#4338ca` na Ficha do Produto, e a Philco era vermelha num lugar e laranja no
  outro.
- O acento da interface (botões, abas, foco) virou `#0f766e`, em
  `.streamlit/config.toml` e no `:root` de `assets/style.css`. O azul fica
  reservado ao dado da Midea.
- `focus_color_map`: Midea + 2 rivais em cor, o resto em cinza. Aplicado na
  evolução do Cockpit.
- Barras de ranking de seller numa cor só: pintar cada seller de uma cor não
  codificava nada.

### Leitura: a resposta antes da metodologia
- `_como_ler(...)` é um popover "ℹ️ Como ler" com a metodologia e as ressalvas.
  No Cockpit ele substituiu a caption de base e o expander de turnos, que
  ficavam entre os KPIs e os alertas.
- O Cockpit abre com uma **frase-resposta** (`_cockpit_headline`), por exemplo:
  "A Midea Carrier ocupa 31% da prateleira genérica em 08/11, ▼ 2.4 p.p. vs o
  dia anterior — rival líder: Philco com 18%".
- `_empty_state(tipo, ...)` tem três variantes, que nunca podem ter a mesma
  cara: `sem_dado` (o mercado não teve), `sem_coleta` (nós não olhamos) e
  `fora_cobertura` (a pergunta não se aplica ao recorte).
- `_label_line_ends(fig)` põe o rótulo direto no fim de cada linha (até 8
  séries) no lugar da legenda embaixo, que custava cerca de 140 px por gráfico.
  Aplicado na evolução do Cockpit e no 📈 Evolução de Preço.

### Limpeza
`design_system.py` (986 linhas, sem nenhum import), `MELHORIAS_DESIGN.md` e
`GUIA_DESIGN_SYSTEM.md` foram para `docs/archive/`.

## Regras para página nova
1. Registrar em `PAGES` **e** numa seção de `_NAV_GROUPS`. O
   `tests/test_ui_components.py` reprova página fora do menu ou em duas seções.
2. Nome em português, com emoji na frente (vira o ícone do menu).
3. Cor de marca e plataforma só via `utils/ui_tokens.py`, nunca hex solto.
4. Metodologia em `_como_ler`, estado vazio em `_empty_state`.
5. Pergunta que já tem página vira uma **visão** dela (`_SUBVIEWS`), não uma
   página nova.

## Próximas fases (não feitas aqui)
- **Fase 2:** feita — ver a seção "Fase 2" no fim.
- **Popover "Como ler" nas demais páginas.** Mais Vendidos (20 captions e 18
  avisos) e Preço do Dia são as próximas.
- **Seller App:** feito — ver a seção abaixo.

## Seller App — roteiro (Nov/2026)

`seller_app/app.py` + contas puras em `seller_app/seller_insights.py`
(`tests/test_seller_insights.py`). O app segue publicado sozinho: o módulo é
irmão do `app.py` e as cores de plataforma são uma cópia de
`utils/ui_tokens.PLATFORM_COLORS` (o teste reprova se divergirem).

- **Selo de cobertura no topo**, antes de qualquer número: 🟢 leitura
  completa / 🟠 parcial (qual plataforma e qual turno faltou) / ⚪ sem
  registro. A aba 🩺 Cobertura continua com o detalhe.
- **KPIs com comparação**, em cartões 2×2 (2×2 também no celular):
  - **Share de buy box** com delta contra 7 dias antes. O delta usa só as
    plataformas observadas nos dois momentos.
  - **Posição no ranking** ("#2 de 41"), com o líder e a distância em
    p.p.
  - **Saldo de viradas**: ganhos − perdas, num cartão próprio. Antes o saldo
    vinha como delta dentro do card "Perdidos" e se lia como "perdi mais 3".
  - **Perdas abertas**.
- **🎯 Onde agir** (1ª aba): cada produto perdido com quem levou, o preço
  dele no turno da perda, o seu último preço antes dela, a diferença em % e se
  você já recuperou. Ordem: perdas abertas primeiro, as de menor diferença no
  topo (as mais baratas de reverter). Sem o seu preço anterior a perda
  aparece, mas sem diferença inventada. Turno é ordenado pelo horário
  (Abertura < Tarde < Fechamento), nunca pelo alfabeto (o defeito da
  migração 016).
- **🥊 Ranking** abre com "Sua posição em cada plataforma" (posição, nº de
  sellers, líder e distância ao líder).
- **Formatação:** share em barra, preço em R$, datas DD/MM, cores fixas por
  marketplace nos gráficos de share e de posição.
- **Exportação** em CSV para Excel PT-BR (`;`, vírgula decimal, BOM): a lista
  "Onde agir" e todas as ofertas da janela.
- **Glossário** "❔ O que é isto?" (buy box, share, ganho/perda, turno) e
  **tela de senha** com identidade do produto.
- Requer `streamlit>=1.40` (`color=`/rótulos de eixo nos gráficos nativos).

Ficaram de fora de propósito: Plotly no Seller App (os gráficos nativos com
cor fixa resolvem a consistência sem somar dependência ao deploy) e a troca do
padrão "Web Continental" no seletor da demo.

## Fase 2 (Nov/2026)

### Recorte na URL
- `utils/url_state.py` (puro, `tests/test_url_state.py`). O recorte (período,
  canal, plataformas, marcas, fontes, comparar) vai na query string, por
  exemplo `/buy-box?de=2026-11-01&ate=2026-11-05&canal=Marketplaces&plat=Magalu`.
- Ao abrir uma sessão, a URL manda no recorte (`_aplicar_url_uma_vez`). Depois
  disso a URL espelha os filtros a cada execução (`_sincronizar_url`) e
  acompanha a troca de página. Copiar a barra de endereço é compartilhar o
  recorte.
- Período padrão (últimos 7 dias) **não** vai para a URL: link sem `de`/`ate`
  é rolante. Valor que deixou de ser opção (plataforma renomeada, data
  inválida) é ignorado em silêncio: um link antigo nunca derruba a página.

### Links nos alertas do Telegram
- `linha_telegram(pagina, filtros)` monta a linha; ela só aparece com a
  variável **`RAC_DASHBOARD_URL`** (URL pública do painel). Sem ela, o alerta
  sai idêntico ao de antes.
- Onde já há link:
  - `pipeline_watch.py` → 🩺 Saúde do Dado (sem datas: a página usa a
    própria janela, não o período global);
  - resumo de coleta (`utils/n8n_notify.py`) → Cockpit;
  - brief de Mais Vendidos → 🥇 Mais Vendidos.
- No Actions, cadastre `RAC_DASHBOARD_URL` em *Settings → Secrets and
  variables → Actions → **Variables*** (não é segredo). O `pipeline_guard.yml`
  já lê `vars.RAC_DASHBOARD_URL`. No PC coletor, vai no `.env` (modelo em
  `.env.example`).
- `tests/test_url_state.py` confere que os destinos dos alertas
  (`saude-do-dado`, `mais-vendidos`) existem no menu; o Cockpit é a raiz.

### `@st.fragment` nos controles locais
- `_fragment = getattr(st, "fragment", ...)`: o controle reroda só o bloco
  dele.
- 🏆 Buy Box → Posição e vencedor → Timeline (`_bb_timeline`). **Corrige um
  bug:** a página só carrega dado com o botão "Load BuyBox", e mexer no
  "Agrupar por" rerodava a página inteira, que voltava ao "clique em Load".
- 🥇 Mais Vendidos → Ranking do dia (`_bs_ranking_do_dia`): trocar a data ou a
  plataforma não refaz mais a leitura e as validações da página.

### KPIs 2×2 no celular
- Abaixo de 640 px o Streamlit empilha toda linha de colunas. A linha que só
  tem métricas agora quebra em pares (`assets/style.css`). O Seller App tem a
  mesma regra embutida, porque é publicado sozinho.

### Recortes salvos
- Sai do "⚙️ Avançado" para **⭐ Recortes**, logo abaixo dos filtros:
  - atalhos prontos, definidos em código e que nunca somem: 7 dias,
    marketplaces, Midea 30 dias, ontem e hoje;
  - os recortes salvos pelo usuário;
  - o **link do recorte atual**.
- Os salvos ainda moram num arquivo do servidor, que some a cada reinício do
  Streamlit Cloud. Por isso o link é a forma durável: o recorte inteiro está
  na URL, e favoritar o link é salvar.
- Aplicar um recorte usa um callback (`on_change`): ele grava os filtros antes
  de os widgets renascerem. Antes era uma sequência de comparação e
  `st.rerun()`.
