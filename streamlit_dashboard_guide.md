# 🎨 Guia de Transformação de Dashboards Streamlit

## Superando as Limitações de Design do Streamlit

### 1. **Bibliotecas de Visualização Avançadas**

#### Plotly (Recomendação Principal)
```python
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# Gráfico interativo sofisticado
fig = make_subplots(
    rows=2, cols=2,
    subplot_titles=('Vendas por Região', 'Tendência Temporal', 
                   'Distribuição', 'Correlação'),
    specs=[[{"type": "choropleth"}, {"type": "scatter"}],
           [{"type": "histogram"}, {"type": "heatmap"}]]
)

# Adicionar traces com customização avançada
fig.add_trace(
    go.Choropleth(
        locations=['US', 'BR', 'DE'],
        z=[50, 30, 40],
        locationmode='ISO-3',
        colorscale='Viridis',
        colorbar_title="Vendas (M)"
    ),
    row=1, col=1
)

fig.update_layout(
    height=800,
    showlegend=False,
    template="plotly_dark",
    font=dict(family="Inter", size=14),
    plot_bgcolor='rgba(0,0,0,0)',
    paper_bgcolor='rgba(0,0,0,0)'
)

st.plotly_chart(fig, use_container_width=True)
```

#### Altair (Para visualizações estatísticas elegantes)
```python
import altair as alt

# Configurar limite de dados
alt.data_transformers.disable_max_rows()

# Gráfico com brush e link entre views
brush = alt.selection_interval()

base = alt.Chart(df).properties(
    width=400,
    height=300
).add_params(brush)

points = base.mark_point().encode(
    x='revenue:Q',
    y='profit:Q',
    color=alt.condition(brush, 'category:N', alt.value('lightgray'))
).interactive()

areas = base.mark_bar().encode(
    x='category:N',
    y='sum(revenue):Q',
    opacity=alt.condition(brush, alt.value(1), alt.value(0.3))
)

chart = alt.vconcat(points, areas)
st.altair_chart(chart, use_container_width=True)
```

#### Bokeh (Para dashboards altamente interativos)
```python
from bokeh.plotting import figure
from bokeh.models import HoverTool, ColumnDataSource
from bokeh.layouts import gridplot
from bokeh.palettes import Viridis256

source = ColumnDataSource(data=df)

p1 = figure(width=600, height=400, tools="pan,wheel_zoom,box_zoom,reset")
p1.circle('x', 'y', source=source, size=8, color='navy', alpha=0.5)

hover = HoverTool(tooltips=[
    ("Índice", "@index"),
    ("Valor", "@y{0.00}"),
    ("Categoria", "@category")
])
p1.add_tools(hover)

st.bokeh_chart(p1, use_container_width=True)
```

### 2. **Paletas de Cores Impactantes**

```python
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap
import seaborn as sns

# Paletas modernas e acessíveis
palettes = {
    'modern_gradient': ['#667eea', '#764ba2', '#f093fb'],
    'professional': ['#2c3e50', '#3498db', '#e74c3c', '#2ecc71'],
    'warm_sunset': ['#ff6b6b', '#feca57', '#ff9ff3', '#54a0ff'],
    'ocean_depths': ['#0077b6', '#00b4d8', '#90e0ef', '#caf0f8'],
    'forest_mist': ['#2d6a4f', '#40916c', '#74c69d', '#d8f3dc'],
    'monochrome_elegant': ['#1a1a2e', '#16213e', '#0f3460', '#e94560']
}

# Criar colormap customizada
def create_custom_cmap(colors, name='custom'):
    return LinearSegmentedColormap.from_list(name, colors)

# Aplicar no Plotly
import plotly.colors as pc

# Usar paletas do ColorBrewer (otimizadas para daltonismo)
pc.sequential.Aggrnyl  # Azul-verde moderno
pc.diverging.RdBu_r    # Divergente para diferenças
pc.qualitative.Set2    # Categórica distinta

# Exemplo de aplicação
fig = px.bar(df, x='category', y='value',
             color='value',
             color_continuous_scale=pc.sequential.Plasma)
```

### 3. **Layout e Componentes Customizados**

```python
import streamlit as st
from streamlit.components.v1 import html

# CSS customizado para styling avançado
st.markdown("""
<style>
/* Importar fontes modernas */
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;700&display=swap');

/* Reset e base */
.stApp {
    font-family: 'Inter', sans-serif;
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
}

/* Cards com glassmorphism */
.metric-card {
    background: rgba(255, 255, 255, 0.1);
    backdrop-filter: blur(10px);
    border-radius: 16px;
    padding: 24px;
    border: 1px solid rgba(255, 255, 255, 0.2);
    box-shadow: 0 8px 32px rgba(31, 38, 135, 0.37);
    transition: transform 0.3s ease;
}

.metric-card:hover {
    transform: translateY(-5px);
}

/* Métricas destacadas */
.big-metric {
    font-size: 3rem;
    font-weight: 700;
    background: linear-gradient(45deg, #FF6B6B, #4ECDC4);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
}

/* Containers flutuantes */
.stDataFrame {
    border-radius: 12px;
    box-shadow: 0 4px 20px rgba(0,0,0,0.1);
    overflow: hidden;
}

/* Botões customizados */
.stButton > button {
    background: linear-gradient(45deg, #667eea, #764ba2);
    color: white;
    border: none;
    border-radius: 8px;
    padding: 12px 24px;
    font-weight: 600;
    transition: all 0.3s ease;
}

.stButton > button:hover {
    transform: scale(1.05);
    box-shadow: 0 8px 20px rgba(102, 126, 234, 0.4);
}

/* Sidebar elegante */
[data-testid="stSidebar"] {
    background: rgba(255, 255, 255, 0.05);
    backdrop-filter: blur(10px);
}

/* Headers modernos */
h1, h2, h3 {
    font-weight: 700;
    letter-spacing: -0.02em;
}

/* Progress bars animadas */
.stProgress > div > div {
    background: linear-gradient(90deg, #667eea, #764ba2);
    transition: width 0.5s ease;
}
</style>
""", unsafe_allow_html=True)

# Layout em colunas com cards
col1, col2, col3, col4 = st.columns(4)

with col1:
    st.markdown("""
    <div class="metric-card">
        <div style="font-size: 0.9rem; color: #fff; opacity: 0.8;">Receita Total</div>
        <div class="big-metric">R$ 2.4M</div>
        <div style="color: #4ECDC4; font-size: 0.9rem;">↑ 23% vs mês anterior</div>
    </div>
    """, unsafe_allow_html=True)

# Componente HTML/JS customizado
html("""
<div style="
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    border-radius: 16px;
    padding: 30px;
    color: white;
    text-align: center;
    box-shadow: 0 10px 40px rgba(0,0,0,0.2);
">
    <h2 style="margin: 0; font-size: 2rem;">Dashboard Interativo</h2>
    <p style="opacity: 0.9; margin-top: 10px;">Explore os dados com ferramentas avançadas</p>
</div>
""", height=150)
```

### 4. **Interatividade Avançada**

```python
from streamlit_javascript import st_javascript
import streamlit_analytics as analytics

# Cache estratégico para performance
from functools import lru_cache
import hashlib

@st.cache_data(show_spinner=False)
def load_processed_data(file_hash):
    """Processamento pesado cacheado"""
    return df_processed

@st.cache_resource
def get_model():
    """Modelos ML cacheados como recurso"""
    return trained_model

# Widgets interativos sofisticados
from streamlit_extras import keyup

# Slider com feedback em tempo real
value = st.slider(
    "Selecione o intervalo",
    min_value=0,
    max_value=100,
    value=(25, 75),
    step=1,
    help="Ajuste para filtrar os dados"
)

# Selectbox com busca
options = df['category'].unique().tolist()
selected = st.selectbox(
    "Categoria",
    options,
    index=0,
    help="Busque por categoria específica"
)

# Multiselect com todos/selecionar nenhum
all_options = df['region'].unique().tolist()
selected_regions = st.multiselect(
    "Regiões",
    all_options,
    default=all_options[:3]
)

# Toggle switches
enable_feature = st.toggle(
    "Ativar modo avançado",
    value=False,
    help="Habilita funcionalidades extras"
)

# Callbacks para atualização dinâmica
def update_charts():
    st.session_state['charts_updated'] = True

st.button(
    "Atualizar Análise",
    on_click=update_charts,
    type="primary"
)
```

### 5. **Análise Insightful com Estatística e ML**

```python
import pandas as pd
import numpy as np
from scipy import stats
from sklearn.preprocessing import StandardScaler
from sklearn.cluster import KMeans
import shap

# Análise estatística automática
def generate_insights(df):
    insights = []
    
    # Correlações significativas
    corr_matrix = df.corr(numeric_only=True)
    high_corr = corr_matrix[abs(corr_matrix) > 0.7].stack().reset_index()
    
    for _, row in high_corr.iterrows():
        if row['level_0'] != row['level_1']:
            insights.append({
                'type': 'correlation',
                'message': f"{row['level_0']} e {row['level_1']} têm correlação forte ({row[0]:.2f})",
                'strength': abs(row[0])
            })
    
    # Outliers detection
    for col in df.select_dtypes(include=[np.number]).columns:
        z_scores = np.abs(stats.zscore(df[col].dropna()))
        outliers = (z_scores > 3).sum()
        if outliers > 0:
            insights.append({
                'type': 'outlier',
                'message': f"{col} possui {outliers} outliers significativos",
                'severity': 'high' if outliers > 10 else 'medium'
            })
    
    # Tendências temporais
    if 'date' in df.columns:
        df_sorted = df.sort_values('date')
        slope, intercept, r_value, p_value, std_err = stats.linregress(
            range(len(df_sorted)), 
            df_sorted[df_sorted.select_dtypes(include=[np.number]).columns[0]]
        )
        trend = "crescente" if slope > 0 else "decrescente"
        insights.append({
            'type': 'trend',
            'message': f"Tendência {trend} detectada (p-value: {p_value:.3f})",
            'direction': slope
        })
    
    return insights

# Clusterização para segmentação
def perform_clustering(df, n_clusters=4):
    scaler = StandardScaler()
    scaled_data = scaler.fit_transform(df.select_dtypes(include=[np.number]))
    
    kmeans = KMeans(n_clusters=n_clusters, random_state=42)
    clusters = kmeans.fit_predict(scaled_data)
    
    # Visualização dos clusters
    fig = px.scatter(
        df, 
        x=df.columns[0], 
        y=df.columns[1],
        color=clusters.astype(str),
        title=f"Segmentação em {n_clusters} clusters",
        color_discrete_sequence=px.colors.qualitative.Vivid
    )
    
    return clusters, fig

# Explicabilidade de modelos com SHAP
def explain_model_predictions(model, X_sample):
    explainer = shap.TreeExplainer(model)
    shap_values = explainer.shap_values(X_sample)
    
    # Gráfico de importância
    shap.summary_plot(shap_values, X_sample, show=False)
    plt.savefig('shap_summary.png', bbox_inches='tight', dpi=300)
    
    return shap_values

# Exibir insights automaticamente
insights = generate_insights(df)
if insights:
    with st.expander("🔍 Insights Automáticos", expanded=True):
        for insight in sorted(insights, key=lambda x: x.get('strength', 0), reverse=True):
            icon = "📈" if insight['type'] == 'correlation' else "⚠️" if insight['type'] == 'outlier' else "📊"
            st.warning(f"{icon} {insight['message']}") if insight['type'] == 'outlier' else st.info(f"{icon} {insight['message']}")
```

### 6. **Dashboard Template Completo**

```python
import streamlit as st
import plotly.express as px
import pandas as pd
import numpy as np

# Configuração da página
st.set_page_config(
    page_title="Dashboard Analítico Avançado",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded"
)

# CSS personalizado
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;700&display=swap');

.stApp {
    font-family: 'Inter', sans-serif;
}

.metric-container {
    background: linear-gradient(135deg, rgba(255,255,255,0.1), rgba(255,255,255,0.05));
    backdrop-filter: blur(10px);
    border-radius: 16px;
    padding: 24px;
    border: 1px solid rgba(255,255,255,0.1);
    box-shadow: 0 8px 32px rgba(0,0,0,0.1);
}

.main-title {
    font-size: 2.5rem;
    font-weight: 700;
    background: linear-gradient(45deg, #667eea, #764ba2);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
}
</style>
""", unsafe_allow_html=True)

# Header
st.markdown('<p class="main-title">📊 Dashboard Analítico Avançado</p>', unsafe_allow_html=True)
st.markdown("Análise inteligente de dados com visualizações interativas")
st.divider()

# Sidebar com controles
with st.sidebar:
    st.header("🎛️ Controles")
    
    uploaded_file = st.file_uploader("Carregar dados", type=['csv', 'xlsx'])
    
    date_range = st.date_input("Período", value=[])
    
    categories = st.multiselect(
        "Categorias",
        options=['A', 'B', 'C'],
        default=['A']
    )
    
    st.divider()
    
    show_advanced = st.toggle("Modo avançado")
    
    if show_advanced:
        st.info("Funcionalidades extras ativadas")

# Carregar dados de exemplo
@st.cache_data
def load_sample_data():
    dates = pd.date_range('2024-01-01', periods=100, freq='D')
    df = pd.DataFrame({
        'date': dates,
        'revenue': np.random.randint(1000, 10000, 100),
        'costs': np.random.randint(500, 5000, 100),
        'category': np.random.choice(['A', 'B', 'C'], 100),
        'region': np.random.choice(['Norte', 'Sul', 'Leste', 'Oeste'], 100)
    })
    df['profit'] = df['revenue'] - df['costs']
    return df

df = load_sample_data()

# KPIs principais
col1, col2, col3, col4 = st.columns(4)

with col1:
    st.metric(
        label="Receita Total",
        value=f"R$ {df['revenue'].sum():,.0f}",
        delta=f"{np.random.randint(5, 25)}%"
    )

with col2:
    st.metric(
        label="Lucro Médio",
        value=f"R$ {df['profit'].mean():,.0f}",
        delta=f"{np.random.randint(-5, 15)}%"
    )

with col3:
    st.metric(
        label="Transações",
        value=len(df),
        delta=f"{np.random.randint(10, 30)}%"
    )

with col4:
    st.metric(
        label="Ticket Médio",
        value=f"R$ {df['revenue'].mean():,.0f}",
        delta=f"{np.random.randint(-3, 12)}%"
    )

st.divider()

# Abas para organização
tab1, tab2, tab3, tab4 = st.tabs([
    "📈 Visão Geral", 
    "🌍 Geográfico", 
    "🔬 Análise Estatística",
    "🤖 Machine Learning"
])

with tab1:
    col1, col2 = st.columns([2, 1])
    
    with col1:
        fig_trend = px.line(
            df.groupby('date')['revenue'].sum().reset_index(),
            x='date',
            y='revenue',
            title='Tendência de Receita',
            markers=True,
            line_shape='spline'
        )
        fig_trend.update_traces(line=dict(width=3))
        fig_trend.update_layout(template="plotly_white", height=400)
        st.plotly_chart(fig_trend, use_container_width=True)
    
    with col2:
        fig_pie = px.pie(
            df,
            names='category',
            values='revenue',
            title='Distribuição por Categoria',
            color_discrete_sequence=px.colors.qualitative.Set2
        )
        st.plotly_chart(fig_pie, use_container_width=True)

with tab2:
    fig_map = px.choropleth(
        df.groupby('region')['revenue'].sum().reset_index(),
        locations='region',
        locationmode='country names',
        color='revenue',
        color_continuous_scale='Viridis',
        title='Receita por Região'
    )
    st.plotly_chart(fig_map, use_container_width=True)

with tab3:
    col1, col2 = st.columns(2)
    
    with col1:
        fig_hist = px.histogram(
            df,
            x='profit',
            nbins=30,
            title='Distribuição de Lucros',
            color_discrete_sequence=['#667eea']
        )
        st.plotly_chart(fig_hist, use_container_width=True)
    
    with col2:
        fig_box = px.box(
            df,
            x='category',
            y='revenue',
            title='Boxplot por Categoria',
            color='category'
        )
        st.plotly_chart(fig_box, use_container_width=True)
    
    # Matriz de correlação
    fig_corr = px.imshow(
        df.corr(numeric_only=True),
        text_auto='.2f',
        aspect='auto',
        color_continuous_scale='RdBu_r',
        title='Matriz de Correlação'
    )
    st.plotly_chart(fig_corr, use_container_width=True)

with tab4:
    if show_advanced:
        st.subheader("🎯 Segmentação de Clientes")
        
        from sklearn.cluster import KMeans
        
        features = df[['revenue', 'profit']].dropna()
        kmeans = KMeans(n_clusters=3, random_state=42)
        df['cluster'] = kmeans.fit_predict(features)
        
        fig_scatter = px.scatter(
            df,
            x='revenue',
            y='profit',
            color='cluster',
            size='revenue',
            hover_data=['category', 'region'],
            title='Clusters Identificados',
            color_discrete_sequence=px.colors.qualitative.Vivid
        )
        st.plotly_chart(fig_scatter, use_container_width=True)
        
        st.success("✅ 3 segmentos distintos identificados!")

# Footer
st.divider()
st.caption("Dashboard desenvolvido com Streamlit + Plotly | Atualizado automaticamente")
```

### 7. **Bibliotecas Recomendadas para Instalar**

```bash
# Visualização avançada
pip install plotly>=5.18.0
pip install altair>=5.2.0
pip install bokeh>=3.3.0
pip install plotly-express>=0.4.1

# Componentes extras do Streamlit
pip install streamlit-extras
pip install streamlit-javascript
pip install streamlit-analytics

# Análise estatística e ML
pip install scikit-learn>=1.4.0
pip install scipy>=1.12.0
pip install shap>=0.44.0
pip install statsmodels>=0.14.0

# Utilitários
pip install pillow>=10.0.0
pip install python-dateutil>=2.8.2
```

### 8. **Dicas de Performance**

```python
# 1. Cache estratégico
@st.cache_data(ttl=3600)  # Cache por 1 hora
def fetch_external_data():
    return data

@st.cache_resource
def initialize_model():
    return model

# 2. Lazy loading de componentes pesados
if st.checkbox("Carregar visualização detalhada"):
    # Carrega apenas quando necessário
    complex_chart()

# 3. Pagination para grandes datasets
def paginate_dataframe(df, page_size=100):
    total_pages = (len(df) - 1) // page_size + 1
    page = st.number_input("Página", 1, total_pages)
    start = (page - 1) * page_size
    end = start + page_size
    return df.iloc[start:end]

# 4. Async para operações I/O
import asyncio

async def fetch_multiple_sources():
    tasks = [fetch_source1(), fetch_source2(), fetch_source3()]
    return await asyncio.gather(*tasks)
```

### 9. **Checklist de Melhores Práticas**

- ✅ Use `st.cache_data` e `st.cache_resource` apropriadamente
- ✅ Implemente tratamento de erros com `st.error()` e `st.exception()`
- ✅ Adicione tooltips e ajuda contextual com `help=`
- ✅ Use layouts responsivos com `use_container_width=True`
- ✅ Implemente filtros na sidebar para economizar espaço
- ✅ Use abas (`st.tabs`) para organizar conteúdo complexo
- ✅ Adicione estados de loading com `st.spinner()`
- ✅ Valide inputs do usuário antes de processar
- ✅ Use cores acessíveis (teste para daltonismo)
- ✅ Otimize para mobile com `layout="wide"`

---

## 🚀 Próximos Passos

1. **Comece pequeno**: Implemente uma melhoria de cada vez
2. **Teste com usuários**: Colete feedback sobre usabilidade
3. **Monitore performance**: Use `streamlit-analytics` para tracking
4. **Itere rapidamente**: Streamlit permite prototipagem ágil
5. **Documente**: Mantenha comentários sobre decisões de design

**Recursos Adicionais:**
- [Streamlit Gallery](https://streamlit.io/gallery) - Inspiração de dashboards
- [Plotly Python Graphing Library](https://plotly.com/python/) - Documentação completa
- [ColorBrewer](https://colorbrewer2.org/) - Paletas cientificamente validadas
- [Streamlit Components](https://streamlit.io/components) - Componentes da comunidade

Boa construção! 🎨📊
