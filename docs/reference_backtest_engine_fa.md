# موتور مرجع بک‌تست قطعی (PR-08)

این موتور یک شبیه‌ساز مرجع (reference engine) برای صحت‌سنجی بازپخش
نقطه‌درزمان (Point-in-Time) است، نه شبیه‌ساز کامل بازار یا توصیهٔ سرمایه‌گذاری.

## مرز داده و تکرارپذیری

پیش از ایجاد Run، Snapshot باید `FROZEN` باشد و hash فایل `manifest.json`، تمام
componentها و تعداد سطرها با `bisfin snapshot verify` تأیید می‌شود. بعد از آن
تمام قیمت‌ها و انتخاب Revisionها فقط از JSONL همان Artifact خوانده می‌شوند؛
موتور برای انتخاب قیمت، `market.current_bar` و `market.bars_as_of(...)` را
فراخوانی نمی‌کند. PostgreSQL فقط برای ثبت Run، کلیدهای خارجی و Triggerهای
lineage استفاده می‌شود.

هر Manifest مجموعهٔ ابزارها را صریحاً در `instruments` تعیین می‌کند. فیلد
`universe_code` صرفاً provenance آزمایش است و `backtest.run_instrument` حقیقت
عملیاتی PR-08 محسوب می‌شود؛ بازسازی تاریخی عضویت Universe و حذف survivorship
bias هنوز در این نسخه حل نشده است.

## قرارداد Run

```bash
uv run --frozen bisfin backtest validate --manifest run.json
uv run --frozen bisfin backtest run --manifest run.json --output-format json
uv run --frozen bisfin backtest show --code RUN_CODE --output-format json
```

Manifest strict JSON است: فیلد ناشناخته، کلید تکراری، float غیرمتناهی، زمان بدون
timezone، component نامعتبر، قیمت adjusted و currency ناسازگار رد می‌شوند.
`run_spec_sha256` از JSON canonical (بدون `run_code`) و `parameter_sha256` از
پارامترهای Strategy ساخته می‌شوند. بنابراین دو اجرای معادل با `run_code` متفاوت
هش نتیجهٔ یکسان دارند.

## مدل مرجع v1

- فقط BAR و `RAW`؛ یک ارز پایه؛ بدون FX، margin، leverage، short یا corporate action.
- `SMA_CROSS_LONG_FLAT_V1` برای هر ابزار مستقل است: پیش از warmup صفر، و پس از
  آن وقتی SMA سریع بزرگ‌تر از SMA کندل بود `target_quantity`، وگرنه صفر.
- سفارش فقط `MARKET/DAY` و full-fill است. Fill روی اولین کندل منطقی بعد از کندل
  Signal انجام می‌شود که `effective_available_at >= submitted_at + lag` دارد؛
  حتی با lag صفر، همان کندل Signal هرگز قابل اجرا نیست.
- قیمت مرجع Close همان Revision فریز‌شده است. Slippage، commission و sell tax
  با `Decimal` و basis point محاسبه می‌شوند. Slippage در قیمت اجرا لحاظ می‌شود
  و دوباره از cash کسر نمی‌شود.
- ترتیب رویدادهای هم‌زمان قطعی است: fillهای معلق، سپس تصمیم‌ها، سپس valuation؛
  در هر گروه instrument/series/bar به‌ترتیب صعودی است. در خریدهای هم‌زمان، این
  یک convention مرجع برای رقابت cash است، نه مدل واقعی بازار.

## Lineage و lifecycle

چرخهٔ اجرا به‌ترتیب validate artifact، ایجاد `QUEUED`، تغییر کوتاه‌مدت به
`RUNNING`، شبیه‌سازی memory-only و ثبت اتمیک journal است. خطا پس از `RUNNING`
ledger نیمه‌کاره باقی نمی‌گذارد و Run را `FAILED` می‌کند.

ثبت نهایی شامل `decision_context`, `decision_bar_input`, signal/order/events,
fill و `fill_market_reference`, cash/position ledger, valuation reference,
equity point و summary است. Triggerهای موجود PostgreSQL همچنان guard نهایی PIT
هستند. `result_sha256` IDهای surrogate و timestampهای ایجاد DB را حذف و فقط
journal معنایی را hash می‌کند.

## آزمون‌ها

```bash
make backtest-test
make backtest-test-integration
```

تست واحد selector، اصلاح Revisionها، عدم look-ahead، حسابداری دقیق Decimal و
hash را پوشش می‌دهد. تست integration یک Snapshot واقعی می‌سازد و artifact-only
run، Triggerهای lineage و idempotency همان `run_code` را بررسی می‌کند.
