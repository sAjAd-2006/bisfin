# راهنمای Ingestion کندل روزانهٔ تعدیل‌نشده BrsApi

این سند قرارداد اجرایی PR-05 را تعریف می‌کند. دامنه فقط یک Vertical Slice است:

```text
BrsApi TSETMC Candlestick type=2
→ ingestion batch
→ immutable raw events
→ validation/catalog resolution
→ RAW daily bar revisions
→ point-in-time verification
```

`type=1` روز جاری/Intraday و `type=3` تعدیل‌شده، endpointهای دیگر BrsApi و ساخت
خودکار Instrument یا Calendar خارج از این مرحله‌اند.

## قرارداد Provider و فرض Fail-closed

درخواست زنده دقیقاً یک GET همگام است:

```text
GET https://Api.BrsApi.ir/Tsetmc/Candlestick.php
query: key=<secret>&type=2&l18=<normalized-symbol>
```

پارامتر درخواست `type=2` تنها مرجع Price Basis است. مقدار احتمالی و ناسازگار
`type` داخل Row فقط Warning می‌سازد و هرگز داده را Adjusted طبقه‌بندی نمی‌کند.
Interface عمومی برای `type=1` یا `type=3` وجود ندارد.

فیلدهای مستند Row عبارت‌اند از `l18`, `type`, `count`, `date`, `time`, `open`,
`high`, `low`, `close`, `volume`. تمام فیلدهای ناشناخته نیز در JSON خام باقی
می‌مانند و silently discard نمی‌شوند.

یک محدودیت مهم قرارداد: فایل committed یعنی `docs/BrsApiDoc.md` فیلدهای Row را
نشان می‌دهد، اما Envelope موفق را مشخص نمی‌کند. پروژه شکل نامستند را حدس نمی‌زند؛
Fixtureهای committed به‌صورت Fail-closed فرض می‌کنند پاسخ موفق یک آرایهٔ غیرخالی
Top-level از JSON objectهاست. هر Success shape دیگر `BrsApiContractError` است.
آرایهٔ خالی مبهم نیز پذیرفته نمی‌شود. تنها no-data صریح این Object است:

```json
{"code_http":200,"successful":true,"status":"no_data","message_error":null}
```

این فرض باید با Contract Test و Fixture نسخه‌بندی شود؛ تغییر واقعی Provider نباید
با Adapter حدسی پنهان شود.

## Settings، HTTP و Secret

متغیرهای دقیق این Integration:

| متغیر | Default | قرارداد |
| --- | --- | --- |
| `BRSAPI_BASE_URL` | `https://Api.BrsApi.ir/` | URL مطلق HTTPS، بدون credential/query/fragment |
| `BRSAPI_API_KEY` | ندارد | `SecretStr`؛ فقط Live mode الزامی |
| `BRSAPI_CONNECT_TIMEOUT_SECONDS` | `5` | مثبت و finite؛ برای Connect و Pool |
| `BRSAPI_READ_TIMEOUT_SECONDS` | `30` | مثبت و finite؛ برای Read و Write |
| `BRSAPI_USER_AGENT` | `bisfin/0.1 brsapi-daily-bars` | غیرخالی و بدون line break |
| `BRSAPI_PROVIDER_CODE` | `BRSAPI` | Provider از پیش provision‌شده |
| `BRSAPI_DAILY_RAW_FEED_CODE` | `TSETMC_CANDLE_DAILY_RAW` | Feed روزانهٔ خام |
| `BRSAPI_IDENTIFIER_TYPE` | `BRSAPI_L18` | نوع شناسهٔ تاریخی |
| `BRSAPI_DEFAULT_TIMEZONE` | `Asia/Tehran` | نام معتبر IANA |

Client زنده از `httpx 0.28.1` به‌صورت synchronous استفاده می‌کند، Redirect را
دنبال نمی‌کند و Retry خودکار ندارد. Connect/Pool timeout از متغیر Connect و
Read/Write timeout از متغیر Read می‌آید. Status، Headerهای diagnostic allow-list،
Body bytes دقیق، زمان شروع/دریافت و elapsed ثبت می‌شوند.

API key فقط در query ارسال می‌شود و نباید در `repr`، Log، CLI، URL کامل، Exception،
Batch metadata یا Test failure ظاهر شود. Settings آن را `SecretStr` نگه می‌دارد؛
Client query را از Exception زنجیره‌شده حذف و مقدار `key` و شکل URL-encoded آن را
Redact می‌کند. `config-check` فقط `brsapi_api_key_configured=yes/no` را نشان
می‌دهد. Secret واقعی فقط در `.env` ignored یا Secret Store قرار می‌گیرد.

## Fixture mode و Live mode

Fixture client همان DTO پاسخ را از Byteهای UTF-8 محلی و Clock تزریق‌شده می‌سازد،
API key نمی‌خواهد و هیچ Network code path ندارد. Fixtureهای sanitized عبارت‌اند از:

```text
tests/fixtures/brsapi/candlestick_type2_success.json
tests/fixtures/brsapi/candlestick_type2_no_data.json
tests/fixtures/brsapi/candlestick_type2_provider_error.json
tests/fixtures/brsapi/candlestick_type2_partial_invalid.json
tests/fixtures/brsapi/candlestick_type2_duplicate_date.json
tests/fixtures/brsapi/candlestick_type2_corrected.json
tests/fixtures/brsapi/candlestick_malformed_json.txt
```

Hosted CI فقط Fixture mode را اجرا می‌کند و هیچ API key یا تماس BrsApi ندارد. Live
mode opt-in است، هزینه/Rate limit Provider را مصرف می‌کند، فقط یک Symbol می‌گیرد،
Retry ندارد و در صورت اجرای صریح، همان Transactionهای A/B/C را در دیتابیس Commit
می‌کند. خود CLI پرچم `--live` ندارد: نبود `--fixture` به‌معنای Live است؛ Target
مربوط در Make علاوه بر Key، متغیر Opt-in جداگانه را نیز اجباری می‌کند.

## Unicode، جلالی و اعتبارسنجی Row

Normalizer شناسه به‌ترتیب NFKC، تبدیل `ي` و `ى` به `ی`، تبدیل `ك` به `ک`، تبدیل
Digitهای فارسی/عربی به ASCII، Trim و Collapse کردن whitespace تکراری را اعمال
می‌کند. Transliteration، حذف کاراکتر معنادار، تبدیل شناسه به عدد یا حذف صفرهای
ابتدایی انجام نمی‌شود. مقدار اصلی `l18` در Raw JSON می‌ماند و مقدار Normalize‌شده
فقط برای Lookup و Key قطعی استفاده می‌شود.

برای تقویم جلالی `jdatetime 6.0.1` انتخاب شده است: با Python 3.12 سازگار است و به
جای الگوریتم دست‌نویس و بررسی‌نشده، تبدیل نگهداری‌شده ارائه می‌دهد. تاریخ‌های
`YYYY-MM-DD` و `YYYY/MM/DD` با Digit فارسی/عربی پذیرفته و تاریخ نامعتبر رد می‌شود.
متن اصلی در `source_date_text` حفظ می‌شود. `time` اختیاری فقط در قالب `HH:MM` یا
`HH:MM:SS` parse می‌شود و Metadata منبع است، نه زمان Session یا Availability.

Integerهای JSON به `int` و عددهای اعشاری مستقیم به `Decimal` parse می‌شوند؛ هیچ
قیمت یا حجم از binary float عبور نمی‌کند. Boolean، Float ورودی Python،
NaN/Infinity، مقدار نامتناهی، قیمت منفی، Volume منفی، `high < low` و Open/Close
خارج از Low/High رد می‌شوند. نماد پاسخ باید پس از Normalization با نماد درخواست
برابر باشد. Errorها Code پایدار و diagnostic محدود دارند.

برای Duplicate یک Symbol/Date:

- Rowهای معادل بر پایهٔ Hash همان JSON کانونی همگی Raw می‌مانند؛ فقط نخستین
  Candidate canonical می‌شود و `DUPLICATE_IDENTICAL` ثبت می‌گردد. این قاعده به
  برابری Byteهای کل HTTP response وابسته نیست.
- Duplicateهای متعارض همگی `DUPLICATE_CONFLICT` می‌گیرند و هیچ Revision مبهمی
  تولید نمی‌شود.

## Hash، Raw Event و کلید منبع

دو Hash مستقل وجود دارد:

```text
batch payload_sha256 = SHA-256(exact response body bytes)
row payload_sha256   = SHA-256(canonical JSON bytes for one row)
```

Hash پاسخ پیش از Decode روی Byteهای دقیق HTTP/Fixture است؛ تغییر whitespace پاسخ
Hash را عوض می‌کند. JSON هر Row با UTF-8، کلیدهای مرتب، separator فشرده،
`ensure_ascii=false` و Decimal بدون Float serialize می‌شود؛ Objectهای معادل در
سیستم‌عامل‌های مختلف Hash یکسان دارند.

کلید منطقی Row:

```text
brsapi|candlestick|type=2|<normalized_symbol>|<source_date_text.strip() یا unknown-date>
```

جزء تاریخ عمداً متن تاریخ Provider پس از Trim است، نه تاریخ Gregorian تبدیل‌شده؛
برای Row فاقد تاریخ String مقدار ثابت `unknown-date` به‌کار می‌رود. جزء Symbol،
`l18` همان Row پس از Normalization است و اگر String خالی/ناموجود باشد از Symbol
Normalize‌شدهٔ درخواست استفاده می‌شود.
برای هر Row یک `ingest.raw_event` درج می‌شود و `raw_payload` همان Object کامل
Provider، شامل فیلدهای ناشناخته، است. Acquisition تکراری برای Audit حفظ می‌شود؛
Idempotency Canonical با حذف Raw history پیاده نمی‌شود. فقط
`validation_status/validation_errors` بعداً تغییر می‌کند و Payload خام immutable
است. Row ردشده همچنان با diagnostic JSON قابل Query است.

JSONB نمی‌تواند Byteهای arbitrary و malformed را نگه دارد. برای پاسخ non-JSON،
Batch Hash دقیق Body را نگه می‌دارد، diagnostic bounded/redacted ثبت می‌شود و هیچ
Raw Row جعلی ساخته نمی‌شود؛ Batch به `QUARANTINED` می‌رود.

## پیش‌نیازهای Catalog و Session

Pipeline هیچ Provider، Feed، Venue، Timeframe، Instrument، Identifier یا Calendar
Row را خودکار نمی‌سازد. پیش از اجرا باید موارد زیر وجود داشته باشند:

1. `catalog.data_provider.provider_code='BRSAPI'`؛
2. Feed همان Provider با `feed_code='TSETMC_CANDLE_DAILY_RAW'` و `data_kind='BAR'`؛
3. `catalog.timeframe.timeframe_code='1d'` با `calendar_unit='SESSION'` و
   `session_aligned=true`؛
4. Instrument موجود با `venue_id` معتبر؛
5. Identifier تاریخی `(provider_id, 'BRSAPI_L18', normalized_l18)` که در زمان
   Open Session معتبر باشد؛
6. `catalog.trading_session` برای Venue/تاریخ Gregorian با
   `session_code='REGULAR'`, `is_trading_day=true`، Open/Close غیرNULL و
   `close > open`.

Lookup Identifier همان قرارداد نیمه‌باز `[valid_from, valid_to)` را دارد، از
Session open به‌عنوان `as_of` استفاده می‌کند و به Symbol فعلی fallback نمی‌کند.
Instrument بدون Venue، Identifier یا Session معتبر Row را رد می‌کند؛ Timestamp
ساختگی تولید نمی‌شود. پاسخ دریافت‌شده پیش از `session_close_ts` نیز نمی‌تواند یک
Daily Bar نهایی باشد و با `RESPONSE_BEFORE_SESSION_CLOSE` رد می‌شود.

Canonical timeها دقیقاً از Session می‌آیند:

```text
bar_open_ts  = session_open_ts
bar_close_ts = session_close_ts
trading_date = converted Gregorian date
```

## مرزهای Transaction و وضعیت Batch

HTTP I/O هرگز داخل Transaction دیتابیس نگه داشته نمی‌شود:

1. **Transaction A — Start:** Provider/Feed resolve، درخواست Idempotency بررسی،
   Batch `RUNNING` با `feed_id`, `request_id`, parser version و metadata بدون Secret
   ایجاد و Commit می‌شود.
2. **External operation:** Fixture یا GET زنده خارج از Transaction اجرا می‌شود.
3. **Transaction B — Acquisition:** Hash پاسخ، HTTP/status/timing metadata، Count
   و تمام Raw Rowها پس از ساخت Partition ماه دریافت ذخیره و Commit می‌شوند.
4. **Transaction C — Canonicalization:** Session/Instrument/Timeframe/Series resolve،
   Partitionهای Bar ساخته، Revisionها append، وضعیت Rawها و Batch به‌صورت Atomic
   نهایی و Commit می‌شوند.
5. **Failure transaction:** خطای Transport/HTTP/Provider/Parse/Canonicalization در
   Transaction کوتاه جدید Finalize می‌شود؛ Exception chaining حفظ و Batch نهایی
   دوباره باز نمی‌شود. Commit مرحله B باعث می‌شود خطای مرحله C Raw acquisition را
   حذف نکند.

اگر Transport پیش از دریافت پاسخ قابل‌استفاده Fail شود، Body/Hashی برای مرحله B
وجود ندارد. پاسخ HTTP ناموفق، Provider error یا Payload malformed دارای پاسخ،
ابتدا Hash و transport metadata را به‌عنوان Acquisition بدون Raw Row ثبت می‌کند و
سپس در Failure transaction نهایی می‌شود.

قاعده وضعیت:

| وضعیت | معنا |
| --- | --- |
| `SUCCEEDED` | همه Candidateها پذیرفته، یا no-data معتبر با Count صفر |
| `PARTIAL` | حداقل یک Row پذیرفته و حداقل یک Row رد شده |
| `FAILED` | خطای fatal در Transport، HTTP، Provider یا Failure غیرمنتظرهٔ Canonicalization |
| `QUARANTINED` | Payload malformed/مبهم یا هیچ Row امن برای Canonicalization |

`(feed_id, request_id)` مرجع یکتایی است. Request ID نهایی، نتیجهٔ Idempotent روشن
می‌دهد و Fetch دوم انجام نمی‌دهد؛ Batch `RUNNING` تعارض است و استفادهٔ همان
Request ID برای Symbol دیگری نیز Fail می‌شود. Request IDهای متفاوت می‌توانند همان
Payload را به‌عنوان Audit مستقل ذخیره کنند، اما Revision تکراری نمی‌سازند.

## Availability، Series و Revision

BrsApi زمان انتشار تاریخی قابل‌اعتماد برای هر Revision نمی‌دهد. سیاست leakage-safe:

```text
available_at        = provider response_received_at
system_available_at = clock immediately before canonical persistence
system_available_at >= available_at
```

تاریخ/زمان Row هرگز Availability نیست و Correction تاریخی به Session Close
backdate نمی‌شود. هر دریافت دیرتر `available_at` دیرتر دارد؛ این سیاست محافظه‌کار
است و ممکن است Availability واقعی قدیمی را دیرتر از واقع نشان دهد، اما آینده را
به گذشته نشت نمی‌دهد.

هویت Series دقیقاً این است:

```text
feed=<configured daily raw feed>
instrument=<historically resolved instrument>
timeframe=1d
price_basis=RAW
adjustment_set_id=NULL
close_semantics=LAST_TRADE
session_code=REGULAR
```

ساخت Series با conflict-safe PostgreSQL انجام می‌شود. پیش از Bar write، ماه‌های
Gregorian یکتا مرتب می‌شوند؛ برای helper قدیمی Bar یک Advisory Lock ماهانه گرفته
و `market.create_bar_month_partition(month, 0)` بدون Hash/List subpartition صدا
زده می‌شود.

برای هر `(bar_series_id, bar_open_ts)` یک Advisory Lock تراکنشی گرفته می‌شود.
آخرین Revision با این فیلدهای Canonical مقایسه می‌شود:

```text
bar_close_ts, trading_date,
open_price, high_price, low_price, close_price, volume,
official_close_price, settlement_price, quote_volume, trade_count,
vwap, open_interest, is_final, quality_flags, previous_close_price
```

`available_at`, `system_available_at`, `recorded_at`, `ingestion_batch_id` فقط
Audit هستند و در Equality نیستند. نبود سابقه `INSERTED`/Revision 1، داده یکسان
`UNCHANGED` بدون Insert، و تغییر واقعی `CORRECTED` با Revision بعدی می‌سازد. Row
قدیمی هرگز Update/Delete نمی‌شود و `market.current_bar` مقصد Write یا منبع History
نیست.

تأیید PIT فقط از `market.bars_as_of(...)` انجام می‌شود: Cutoff پیش از Correction
Revision قبلی و Cutoff پس از آن Revision جدید را برمی‌گرداند. `PUBLIC_REPLAY` از
`available_at` و `ACTUAL_SYSTEM_REPLAY` از `system_available_at` استفاده می‌کند.

## Partition خام و Migration `0004`

`ingest.raw_event` بر `ingested_at` Range-partitioned است. Revision `0004` تابع
زیر را بدون تغییر تعریف جدول اضافه می‌کند:

```sql
ingest.create_raw_event_month_partition(p_month DATE)
```

نام Child برابر `ingest.raw_event_yYYYYmMM` و Range ماه UTC با انتهای Exclusive
است. تابع قبل از Catalog check یک `pg_advisory_xact_lock` ماهانه می‌گیرد، اجرای
دوم No-op است، Relation هم‌نام نامعتبر را Fail می‌کند، Default Partition یا
Extension نمی‌سازد و `READ COMMITTED` می‌خواهد؛ Isolation stale با `0A000` رد
می‌شود. Checksum ثبت‌شده Migration:

```text
188080740e805ed9d58de2f4c72a3007b6c46a45e3b253e7f5226d8538a417b7
```

زنجیره نهایی `0001 -> 0002 -> 0003 -> 0004 (head)` است. Smoke SQL مرز ماه،
Idempotency، Index/Constraint را کنترل و تست دو Connection انتظار واقعی Advisory
Lock را اثبات می‌کند.

## CLI و فرمان‌های توسعه

Fixture mode بدون Network و API key:

```bash
uv run --frozen bisfin ingest brsapi-daily-bars \
  --symbol فملی \
  --fixture tests/fixtures/brsapi/candlestick_type2_success.json \
  --output-format human
```

JSON summary با `--output-format json` و Request ID صریح با
`--request-id <id>` فعال می‌شود. خروجی Human فقط Batch/Symbol، Status و Countها را
نشان می‌دهد؛ خروجی JSON علاوه بر آن، Hash، Watermark، Timeها و پرچم Replay را از
DTO محدود برمی‌گرداند. هیچ‌کدام Raw body، keyed URL یا Secret چاپ نمی‌کنند.

Exit code فرمان ingestion قطعی است: `0` برای `SUCCEEDED`، مقدار `3` برای
`PARTIAL` و مقدار `4` برای `FAILED`، `QUARANTINED` یا خطای عملیاتی ingestion.
خطای اعتبارسنجی Settings پیش از dispatch مقدار `2` می‌دهد.

Live mode مستقیم با حذف `--fixture` و تنظیم `BRSAPI_API_KEY` اجرا می‌شود. مسیر
توصیه‌شدهٔ Make، Opt-in صریح جداگانه را نیز الزام می‌کند:

```bash
BISFIN_RUN_BRSAPI_LIVE_TEST=1 make brsapi-ingest-live BRSAPI_SYMBOL=فملی
```

فرمان‌های تکرارپذیر:

```bash
make brsapi-test
make brsapi-test-integration
make brsapi-ingest-fixture \
  BRSAPI_SYMBOL=فملی \
  BRSAPI_FIXTURE=tests/fixtures/brsapi/candlestick_type2_success.json

make db-test
make db-test-pit
make python-test-integration
uv run --frozen pytest -m integration \
  tests/integration/test_brsapi_ingestion.py::test_cli_fixture_mode_end_to_end
```

Unit testها Docker/DB/Network/Clock واقعی نمی‌خواهند. Integrationها PostgreSQL 16
واقعی، Fixtureهای Catalog/Session/Partition و Timeout محدود برای Raceها دارند.
در Windows Targetهای Make از Git Bash یا WSL اجرا شوند. Live target عضو `check`،
`python-test` یا CI نیست.

## محدودیت‌ها و مرحله بعد

- Instrument، Provider، Feed، Venue و Calendar خودکار ایجاد یا دانلود نمی‌شوند.
- فقط Candlestick `type=2` پشتیبانی می‌شود؛ Candle تعدیل‌شده و Intraday وجود ندارد.
- زمان Availability تاریخی محافظه‌کارانه برابر Acquisition time است.
- Retry framework، Scheduler، Worker، Multi-symbol job و Bulk `COPY` وجود ندارد.
- Endpointهای History/Symbol/Transaction/Order Book و Corporate Action خارج‌اند.
- API server، FastAPI، Redis، Kafka، Celery، Feature generation، Backtest execution
  و ML/DL خارج‌اند.

PR بعدی باید فقط پس از مشاهدهٔ قرارداد واقعی Provider و Benchmark این Slice انتخاب
شود؛ گزینه‌های منطقی، مدیریت Calendar/Symbol master و Worker زمان‌بندی‌شده با Retry
صریح و Rate-limit-aware هستند. افزودن `type=3` نیازمند قرارداد Adjustment و
Corporate Action مستقل است و نباید با RAW series ادغام شود.

## PR-06 و Symbol.php

`Symbol.php` فقط برای validate/enrich یک symbol از manifest به‌کار می‌رود؛ endpoint
bulk یا discovery نیست. Fixture mode بدون کلید است و CI هیچ درخواست live ندارد.
جزئیات authority و raw audit در `catalog_bootstrap_fa.md` ثبت شده است.
