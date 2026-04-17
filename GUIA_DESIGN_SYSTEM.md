# 🎨 Guia de Design System para Dashboard RAC Price Monitor

## Visão Geral

Este guia apresenta o **design system** criado especificamente para dashboards de monitoramento de preços e disponibilidade de produtos e-commerce. O sistema inclui componentes visuais modernos, paleta de cores profissional e padrões de UI/UX best-practice.

---

## 📁 Arquivos Criados

### 1. `design_system.py`
Módulo principal com todos os componentes visuais:
- Paleta de cores profissional
- CSS avançado com glassmorphism
- Componentes de UI reutilizáveis
- Funções de estilização para Plotly

### 2. `dashboard_exemplo.py`
Dashboard completo demonstrando todas as funcionalidades do design system.

---

## 🚀 Como Integrar ao Seu Dashboard Atual

### Passo 1: Importar o Design System

No início do seu `app.py`, adicione:

```python
from design_system import (
    apply_custom_theme,
    render_header,
    metric_card,
    kpi_card,
    status_badge,
    price_card,
    info_box,
    style_plotly_chart,
    create_availability_gauge,
    format_currency,
    format_percentage,
    COLOR_PALETTE,
)
```

### Passo 2: Aplicar o Tema

Substitua sua configuração de página atual por:

```python
# No início do app, após imports
apply_custom_theme()
```

Isso automaticamente:
- Configura a página com layout wide
- Aplica todo o CSS customizado
- Carrega a fonte Inter (moderna e legível)

### Passo 3: Usar o Header Customizado

Substitua seu título atual por:

```python
render_header(
    title="RAC Price Monitor",
    subtitle="Monitoramento de preços e disponibilidade em tempo real",
    icon="❄️"
)
```

### Passo 4: Substituir Métricas Nativas pelos Cards Avançados

**Antes:**
```python
col1, col2, col3 = st.columns(3)
with col1:
    st.metric("Disponibilidade", "87.5%", "+2.3%")
```

**Depois:**
```python
col1, col2, col3 = st.columns(3)
with col1:
    metric_card(
        label="Disponibilidade Geral",
        value="87.5%",
        delta="+2.3%",
        delta_positive=True,
        gradient="success"
    )
```

### Passo 5: Estilizar Gráficos Plotly

**Antes:**
```python
fig = px.line(df, x="data", y="preco")
st.plotly_chart(fig)
```

**Depois:**
```python
fig = px.line(df, x="data", y="preco")
fig = style_plotly_chart(
    fig,
    height=450,
    title="Evolução de Preços",
    legend_position="top-center"
)
st.plotly_chart(fig, use_container_width=True)
```

---

## 🎨 Componentes Disponíveis

### 1. KPI Cards (Com Ícones e Gradientes)

```python
kpi_card(
    title="Produtos Monitorados",
    value="1,234",
    icon="📦",
    gradient="primary",  # primary, success, warning, danger, ocean, sunset
    subtitle="+12 vs ontem"
)
```

**Use para:** Métricas principais no topo do dashboard

### 2. Metric Cards (Avançados com Delta)

```python
metric_card(
    label="Taxa de Disponibilidade",
    value="87.5%",
    delta="+2.3%",
    delta_positive=True,
    gradient="success"
)
```

**Use para:** Seção de métricas detalhadas

### 3. Status Badges (Disponibilidade)

```python
# Retorna HTML do badge
st.markdown(status_badge("available"), unsafe_allow_html=True)
st.markdown(status_badge("unavailable"), unsafe_allow_html=True)
st.markdown(status_badge("limited"), unsafe_allow_html=True)
```

**Use para:** Colunas de status em tabelas, indicadores visuais

### 4. Price Cards (Destaque para Preços)

```python
price_card(
    current_price="R$ 2.459,00",
    old_price="R$ 2.899,00",  # opcional
    label="Preço Atual"
)
```

**Use para:** Cards de produto, comparações de preço

### 5. Info Boxes (Alertas e Mensagens)

```python
info_box("Dados atualizados há 5 minutos.", "info")
info_box("Disponibilidade aumentou 2.3%.", "success")
info_box("Atenção: 3 produtos com variação > 10%.", "warning")
info_box("Erro na coleta de 5 produtos.", "danger")
```

**Use para:** Alertas, notificações, insights automáticos

### 6. Gauge Chart (Disponibilidade)

```python
gauge_fig = create_availability_gauge(availability_pct=87.5)
st.plotly_chart(gauge_fig, use_container_width=True)
```

**Use para:** Visualização rápida de taxa de disponibilidade

---

## 🎯 Paleta de Cores

### Cores Principais

| Nome | Hex | Uso |
|------|-----|-----|
| `primary` | #2563EB | Ações principais, links |
| `success` | #10B981 | Disponível, positivo |
| `warning` | #F59E0B | Atenção, estoque limitado |
| `danger` | #EF4444 | Indisponível, erro, alerta |
| `info` | #06B6D4 | Informações neutras |

### Gradientes Prontos

```python
gradient="primary"   # Azul → Roxo
gradient="success"   # Verde escuro → Verde claro
gradient="warning"   # Laranja → Amarelo
gradient="danger"    # Vermelho → Vermelho escuro
gradient="ocean"     # Azul → Ciano
gradient="sunset"    # Laranja → Vermelho
```

---

## 📊 Exemplo de Estrutura de Dashboard

```python
from design_system import *
import streamlit as st
import pandas as pd
import plotly.express as px

# 1. Aplicar tema
apply_custom_theme()

# 2. Header
render_header(
    title="RAC Price Monitor",
    subtitle="Dashboard de preços e disponibilidade",
    icon="❄️"
)

# 3. KPIs Principais
col1, col2, col3, col4 = st.columns(4)
with col1:
    kpi_card("Produtos", "1,234", "📦", "primary")
with col2:
    kpi_card("Disponibilidade", "87.5%", "✅", "success")
with col3:
    kpi_card("Preço Médio", "R$ 2.459", "💰", "ocean")
with col4:
    kpi_card("Alertas", "23", "⚠️", "warning")

st.markdown("<br>", unsafe_allow_html=True)

# 4. Métricas Detalhadas
col1, col2, col3 = st.columns(3)
with col1:
    metric_card("Disponibilidade", "87.5%", "+2.3%", True, "success")
with col2:
    metric_card("Variação Preço", "-5.2%", "-5.2%", True, "ocean")
with col3:
    metric_card("Indisponíveis", "156", "+12", False, "danger")

st.markdown("<br>")

# 5. Gráficos
tab1, tab2, tab3 = st.tabs(["Preços", "Disponibilidade", "Detalhes"])

with tab1:
    fig = px.line(df, x="data", y="preco", color="marca")
    fig = style_plotly_chart(fig, title="Evolução de Preços")
    st.plotly_chart(fig, use_container_width=True)

with tab2:
    gauge_fig = create_availability_gauge(87.5)
    st.plotly_chart(gauge_fig, use_container_width=True)
    
with tab3:
    info_box("Insights automáticos aqui", "info")

# 6. Tabela com Status Badges
st.markdown("### Produtos Recentes")
df["status"] = df["disponibilidade"].apply(lambda x: status_badge(x))
st.dataframe(df, use_container_width=True)
```

---

## 🔧 Customizações Adicionais

### Alterar Cores da Paleta

Edite `design_system.py`:

```python
COLOR_PALETTE = {
    "primary": "#SUA_COR_AQUI",
    # ... outras cores
}
```

### Adicionar Novos Gradientes

```python
COLOR_PALETTE["gradient_custom"] = "linear-gradient(135deg, #COR1 0%, #COR2 100%)"
```

### Criar Novos Componentes

Siga o padrão dos componentes existentes:

```python
def novo_componente(param1, param2):
    html = f"""
    <div class="novo-componente">
        {param1}
        {param2}
    </div>
    """
    st.markdown(html, unsafe_allow_html=True)
```

E adicione o CSS correspondente em `CUSTOM_CSS`.

---

## 💡 Dicas de Best Practices

### 1. Hierarquia Visual
- Use KPI cards apenas para 3-5 métricas principais
- Métricas secundárias use `metric_card`
- Detalhes use tabelas ou texto

### 2. Cores Semânticas
- ✅ Verde (`success`) = disponível, positivo, vantagem
- ⚠️ Amarelo (`warning`) = atenção, estoque limitado
- 🚨 Vermelho (`danger`) = indisponível, erro, crítico
- 🔵 Azul (`primary`) = informações neutras, ações

### 3. Espaçamento
- Use `st.markdown("<br>", unsafe_allow_html=True)` entre seções
- Mantenha consistência: 1 `<br>` entre elementos relacionados, 2 entre seções

### 4. Performance
- Cache dados com `@st.cache_data`
- Use `use_container_width=True` em gráficos
- Evite muitos badges HTML em tabelas grandes (>100 linhas)

### 5. Responsividade
- Teste em diferentes tamanhos de tela
- Use `st.columns()` com cuidado em mobile (máximo 2-3 colunas)

---

## 📈 Insights Automáticos Sugeridos

Para tornar o dashboard mais insightful, adicione:

```python
# Detecção de variação significativa de preço
def detectar_alertas_preco(df, threshold=10):
    alertas = []
    # Lógica de detecção
    return alertas

# Uso
alertas = detectar_alertas_preco(latest_df)
if alertas:
    info_box(f"🔔 {len(alertas)} produtos com variação > {threshold}%", "warning")
```

---

## 🧪 Testando Localmente

```bash
# Rodar exemplo completo
streamlit run dashboard_exemplo.py

# Rodar seu dashboard atualizado
streamlit run app.py
```

---

## 📚 Recursos Adicionais

### Bibliotecas Recomendadas

```bash
pip install streamlit-extras  # Componentes extras
pip install plotly            # Gráficos interativos
pip install altair            # Visualizações estatísticas
pip install streamlit-aggrid  # Tabelas avançadas
```

### Inspiração de Design

- [Tailwind UI](https://tailwindui.com/) - Componentes modernos
- [Material Design](https://material.io/) - Google Design System
- [Stripe Dashboard](https://stripe.com/) - Referência em fintech

---

## ✅ Checklist de Implementação

- [ ] Importar `design_system.py` no `app.py`
- [ ] Substituir `st.set_page_config` por `apply_custom_theme()`
- [ ] Adicionar `render_header()` no topo
- [ ] Substituir `st.metric()` por `kpi_card()` e `metric_card()`
- [ ] Aplicar `style_plotly_chart()` em todos os gráficos
- [ ] Adicionar `status_badge()` em colunas de disponibilidade
- [ ] Inserir `info_box()` para alertas automáticos
- [ ] Testar em diferentes resoluções
- [ ] Validar performance com dados reais

---

## 🎉 Resultado Esperado

Após aplicar o design system, seu dashboard terá:

✨ **Visual moderno e profissional**  
🎨 **Cores semânticas e acessíveis**  
📊 **Gráficos consistentes e elegantes**  
🏷️ **Badges animados para status**  
📱 **Responsivo e mobile-friendly**  
⚡ **Performance otimizada**  

---

**Dúvidas?** Execute `dashboard_exemplo.py` para ver todos os componentes em ação!
