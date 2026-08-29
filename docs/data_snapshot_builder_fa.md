# سازندهٔ Snapshot دادهٔ Point-in-Time

`catalog.data_snapshot` مرز تکرارپذیری دادهٔ بازار است. چرخهٔ آن `BUILDING → FROZEN` است؛ هر شکست پس از ایجاد، آن را `FAILED` می‌کند. Snapshot منجمد شامل manifest و componentهای hash‌شده است و فقط componentهای `BAR_REVISION` را پوشش می‌دهد.

## قاعدهٔ حیاتی

**Frozen snapshot != decision-time visibility.** cutoff سراسری فقط سقف داده‌ای است که می‌تواند داخل artifact باشد. مصرف‌کنندهٔ آینده همچنان باید برای هر `decision_ts` قاعدهٔ `effective_available_at <= decision_ts` را اعمال کند. `market.current_bar` برای این کار ممنوع است.

در `PUBLIC_REPLAY` شرط eligibility برابر `available_at <= knowledge_cutoff_ts` و در `ACTUAL_SYSTEM_REPLAY` برابر `system_available_at <= knowledge_cutoff_ts` است. در هر دو حالت فقط revision نهایی، با `bar_close_ts <= cutoff` و در بازهٔ نیمه‌باز `[event_from,event_to)` وارد می‌شود. تمام revisionهای eligible منجمد می‌شوند، نه فقط آخرین correction.

## Artifact و hash

مسیر محلی به‌شکل `<root>/<snapshot-code>/manifest.json` و `components/0001-<sha256(component_key)>.jsonl` است. JSONL UTF-8، کلیدهای مرتب، LF، timestamp UTC و Decimal رشتهٔ دقیق دارد؛ componentها بر اساس `(bar_open_ts, bar_series_id, revision_no)` مرتب‌اند. SHA-256 دقیق bytes component و manifest در DB ثبت می‌شود.

publication ابتدا در staging و سپس با rename اتمیک انجام می‌شود. URI محلی `file://` فقط برای development/CI تک‌نود مناسب است؛ برای بازتولید میان ماشین‌ها artifact باید همراه DB کپی شود. migration به object storage در آینده می‌تواند پشت همین artifact boundary انجام شود.

## CLI

```bash
uv run --frozen bisfin snapshot validate --manifest snapshot.json
uv run --frozen bisfin snapshot build --manifest snapshot.json --output-dir ./var/snapshots
uv run --frozen bisfin snapshot show --code CODE
uv run --frozen bisfin snapshot verify --code CODE
uv run --frozen bisfin snapshot verify --code CODE --against-db
```

`verify` همیشه artifact را کنترل می‌کند؛ `--against-db` candidate set فعلی را با hash frozen مقایسه و drift را بدون mutation گزارش می‌کند. Snapshot بازار به‌تنهایی survivorship bias/universe تاریخی را حل نمی‌کند و PR-08 تنها می‌تواند از snapshot FROZEN مصرف کند؛ این PR هیچ backtest engine یا ML/feature work ایجاد نمی‌کند.
