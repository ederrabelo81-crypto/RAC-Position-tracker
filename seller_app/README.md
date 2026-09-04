# Track Position Seller — publicar o painel

App **separado** do `app.py` da raiz. O da raiz é interno (visão da indústria,
chave que lê tudo); este atende gente de fora e **não carrega chave de
escrita**. A fronteira é a chave e a policy de RLS, não um `WHERE` no código.

## Pré-requisito: a migração 016

O painel lê `seller_offer_daily`, `seller_coverage_daily` e
`v_seller_buybox_share`. Elas não existem até a migração rodar:

```bash
psql "$SUPABASE_DB_URL" -f docs/migrations/016_seller_offer_daily.sql
python scripts/build_seller_offer_daily.py --desde 2026-08-28   # popula
```

O `build` exige `SUPABASE_KEY` **service_role**: a escrita é negada por
ausência de policy, então a chave `anon` grava nada em silêncio — o mesmo modo
de falha da migração 012.

## Rodar local

```bash
pip install -r seller_app/requirements.txt
mkdir -p .streamlit && cat > .streamlit/secrets.toml <<'EOF'
SUPABASE_URL = "https://<projeto>.supabase.co"
SUPABASE_ANON_KEY = "<chave anon>"
SELLER = "Web Continental"
SENHA = "<senha da demo>"
EOF
streamlit run seller_app/app.py
```

## Pôr no ar — Streamlit Community Cloud

O caminho mais curto para um link apresentável, e gratuito.

1. **`share.streamlit.io` → New app**, apontando para este repositório, branch
   `main`, arquivo `seller_app/app.py`.
2. **Advanced settings → Secrets**: cole o mesmo conteúdo do
   `secrets.toml` acima. Nunca comite esse arquivo — o `.gitignore` já cobre
   `.streamlit/secrets.toml`.
3. Deploy. Sai uma URL `https://<nome>.streamlit.app`.

**Três coisas para conferir antes de mandar o link:**

- **A chave é a `anon`, nunca a `service_role`.** O segredo do Streamlit Cloud
  é visível a quem administra o app, e a `service_role` ignora RLS: vazá-la dá
  escrita no banco inteiro.
- **RLS ligada com policy de leitura.** A migração 016 cria as policies só
  quando o papel `anon` existe. Confira que a leitura funciona pela `anon` —
  RLS ligada **sem** policy responde `[]` com HTTP 200, sem erro e sem log, e o
  painel fica indistinguível de "não coletou".
- **App público é público.** O Community Cloud gratuito não restringe quem
  abre o link; o `SENHA` do secrets é um cadeado de demo, não autenticação. Para
  um piloto pago, veja abaixo.

## Quando sair da demo

O Community Cloud serve para apresentar. Para tenant pagando, três diferenças:

| | Demo (agora) | Piloto pago |
|---|---|---|
| Acesso | senha única no secrets | login por tenant (Supabase Auth) |
| Isolamento | `SELLER` fixo no secrets | claim de `tenant_id` no JWT + RLS |
| Hospedagem | Community Cloud | app privado (Streamlit for Teams, Render, Fly) |
| Domínio | `*.streamlit.app` | domínio do produto |

O passo que muda a arquitetura é o do meio: enquanto o seller vem de um segredo
de configuração, um app por tenant é a única forma de isolar. Com o claim no
JWT, um app serve todos e a policy faz o corte — que é o desenho do §2.4 do
documento.

## Onde o painel se recusa a responder

Por desenho, e vale explicar ao seller na apresentação:

- **Loja própria não entra em KPI.** Lá o lojista joga sozinho e detém 100% da
  própria vitrine; somar isso ao share inflaria o número.
- **Oferta com identidade ambígua fica de fora.** Quando a chave de oferta
  colapsa na origem, ela some dos números — visível no rodapé, nunca somada em
  silêncio.
- **Turno não coletado não vira zero.** A aba Cobertura existe para separar
  "não houve oferta" de "não olhamos".
