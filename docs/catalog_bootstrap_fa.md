# Bootstrap کاتالوگ (PR-06)

کاتالوگ تنها از JSON نسخه‌دار و صریح ساخته می‌شود. `BrsApi Symbol.php` یک API تک‌نماد است، نه ابزار کشف کل بازار؛ بنابراین فقط نمادهای درخواست‌شده در manifest بررسی می‌شوند و هیچ scraping یا فهرست‌سازی خودکار وجود ندارد.

## قرارداد و اختیار داده

`schema_version=1` و `manifest_id` اجباری هستند، فیلد ناشناخته رد می‌شود، JSON با کلید تکراری رد می‌شود و SHA-256 روی بایت‌های دقیق فایل ثبت می‌گردد. manifest اختیار universe، venue، currency، نوع دارایی و زمان‌های مؤثر را دارد. Symbol.php فقط یک مشاهدهٔ audit شده است: `l18`، ISIN و market باید با manifest و mapping صریح `provider_market_mappings` تطابق داشته باشند؛ قیمت و order book هیچ‌گاه canonical نمی‌شوند.

ISIN پس از trim/uppercase با قالب محافظه‌کارانهٔ ISO-6166-like بررسی می‌شود؛ checksum کامل ادعا نمی‌شود. شناسه‌ها نیم‌بازه‌ای هستند: rename صریح، بازهٔ نماد قبلی را در `rename_effective_from` می‌بندد و بازهٔ جدید را از همان زمان باز می‌کند. Instrument با ISIN حل می‌شود؛ تطابق symbol-only یا split identity هرگز merge خودکار نمی‌شود. نسخهٔ specification فقط append-only و مجاور است.

## اجرای محلی

```bash
uv run --frozen bisfin catalog validate --manifest tests/fixtures/catalog/catalog_bootstrap_success.json
uv run --frozen bisfin catalog bootstrap --manifest tests/fixtures/catalog/catalog_bootstrap_success.json --validation-mode manifest-only
uv run --frozen bisfin catalog bootstrap --manifest tests/fixtures/catalog/catalog_bootstrap_success.json --validation-mode fixture-validate --symbol-fixture-dir tests/fixtures/brsapi/symbols
```

`live-validate` فقط با `BRSAPI_API_KEY` اجرا می‌شود و در CI ممنوع است. پاسخ‌های Symbol و هر entry manifest در `ingest.raw_event` با source keyهای `brsapi|symbol|...` و `bisfin|catalog-manifest|...` ثبت می‌شوند. repositoryها commit نمی‌کنند؛ bootstrap زمانی از تراکنش `READ COMMITTED` استفاده می‌کند و قفل advisory per-key به‌همراه triggerهای PostgreSQL مرجع نهایی صحت هستند.

محدودیت‌ها: کشف کل بازار، derivative، corporate action و هرگونه اصلاح خودکار manifest در این PR وجود ندارد.
