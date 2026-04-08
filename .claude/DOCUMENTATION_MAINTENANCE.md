# Documentation Maintenance

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
