# Testing & Debugging — RAC Position Tracker

## Debug Workflow: Dealer Returns 0 Products

1. **Check the log**: `logs/bot_YYYYMMDD_HHMMSS.log`
   - Look for `[DealerName]` lines
   - `0 itens no DOM (seletor: nenhum)` → selectors don't match
   - `Bloqueio detectado: recaptcha` → anti-bot block
   - `Timeout aguardando precos` → normal (not a failure), prices found via fallback

2. **Check debug HTML**: `logs/dealer_debug_<name>_p<N>.html`
   - Open in browser
   - Inspect product card structure
   - Find the correct CSS class for product cards
   - Check if `<script type="application/ld+json">` has product data

3. **Run with visible browser**: `--no-headless`
   ```bash
   python main.py --platforms dealers --pages 1 --no-headless
   ```

## Debug Workflow: Price is 0% for a Dealer

1. **Check log for JSON-LD**: `JSON-LD: N precos carregados`
   - If N > 0 but prices still empty → matching is failing
   - Add debug log: compare JSON-LD keys vs DOM titles

2. **Check price selectors**:
   - Open debug HTML → find where price appears
   - Is it in `meta[itemprop="price"]`? In `[data-price]`? In a custom class?
   - Is price loaded via JavaScript (empty in initial HTML)?

3. **Check VTEX split price**: Some VTEX sites split "2.499,00" into:
   - `<span class="currencyInteger">2.499</span>`
   - `<span class="currencyDecimalSeparator">,</span>`
   - `<span class="currencyDecimalDigits">00</span>`

## Debug Workflow: Too Many Items (>120)

The `_detect_items()` sanity check will skip selectors returning >120 items.
If a dealer still shows too many → the items are being captured at too low a level.

Fix: Add specific `item_selector` or `item_selector_candidates` to DEALER_CONFIGS.

## CSV Quality Checks

After a run, quick validation:

```python
import pandas as pd
df = pd.read_csv("output/rac_monitoramento_*.csv", sep=";", encoding="utf-8-sig")

# Per-platform stats
for plat in df["Plataforma"].unique():
    sub = df[df["Plataforma"] == plat]
    preco_ok = sub["Preço (R$)"].notna().sum()
    print(f"{plat}: {len(sub)} rows, {preco_ok} with price ({100*preco_ok/len(sub):.0f}%)")
```

## Log Interpretation

| Log Pattern | Meaning |
|-------------|---------|
| `JSON-LD: N precos carregados` | Found prices in structured data |
| `Fallback por indice: N precos atribuidos` | Matched prices by position |
| `Bloqueio detectado: recaptcha/cloudflare` | Anti-bot triggered |
| `Rotacao proativa` | Magalu browser restart (every 15 keywords) |
| `Radware detectado` | Magalu CAPTCHA — rotating browser |
| `item_selector override ... retornou 0` | Config selector doesn't match current layout |
| `Sem preco CSS/JSON-LD` | All price extraction methods failed for this item |
| `Validacao: N registros | sem preco: M` | Quality summary per dealer |

## _validate_results() Output

Called at end of each dealer scrape. Logs:
- Total records and price coverage percentage
- Empty product names (should be 0)
- Remaining duplicates after dedup
- Brand concatenation bugs (ArCerto pattern)
