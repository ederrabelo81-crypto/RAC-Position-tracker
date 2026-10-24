# Documentation Maintenance

## README.md vs CLAUDE.md

- **`README.md`** retrata o **estado atual** para quem chega ao projeto: o que
  é, onde roda, como instalar, como usar. Ao mudar executor, agenda,
  plataforma, comando ou variável de ambiente, atualize o README no mesmo PR.
  Não acumule changelog nem relatórios de validação nele — isso vai para
  `docs/archive/` (o README antigo está em
  `docs/archive/README_HISTORICO_ATE_SET2026.md`).
- **`CLAUDE.md`** guarda as regras duras, os incidentes que as motivaram e o
  rodapé "Latest changes".
- Números citados nos dois (testes, keywords, dealers, páginas do dashboard)
  devem ser conferidos contra o código antes de publicar.

## When to Update COMMON_MISTAKES.md

- **New bug found in production** that cost >1 hour to debug
- **Recurring mistake** made 2+ times across sessions
- **Framework/site migration** changes fundamental patterns (e.g., Magalu nm-* migration)
- Keep to max 10 items; archive older entries to `docs/archive/`

## When to Create Completion Docs

After completing any task that:
- Took more than 1 session
- Involved non-obvious decisions
- Changed architecture or patterns

Save to `.claude/completions/YYYY-MM-DD_<task-name>.md` using template.

## When to Archive

Move to `docs/archive/` when:
- Planning docs after implementation is done
- POC summaries after decision is made
- Superseded documentation
- Session notes older than 30 days

## When to Update Learnings

- New scraping pattern discovered (new site layout, new anti-bot)
- New dealer platform type encountered (not VTEX/WooCommerce)
- Performance optimization validated in production
- New best practice established

## Decision Tree

```
Bug found → Is it a common pattern?
  Yes → Update COMMON_MISTAKES.md
  No  → Log in the relevant learnings file

Task completed → Did it take >1 session?
  Yes → Create completion doc
  No  → No action needed

Doc >1000 lines → Split into topic files under docs/learnings/
Doc outdated → Move to docs/archive/
```
