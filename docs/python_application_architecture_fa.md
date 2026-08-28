# معماری برنامهٔ Python و دسترسی Typed به PostgreSQL

این سند Foundation همگام PR-04 و Vertical Slice محدود PR-05 را توصیف می‌کند.
PR-05 فقط کندل روزانهٔ تعدیل‌نشده BrsApi (`Candlestick type=2`) را از Fixture یا
یک درخواست Live صریح به Raw audit و `market.bar_revision` می‌رساند. Worker،
Scheduler، API Server، Candle تعدیل‌شده/Intraday و محاسبهٔ Feature وجود ندارد.

## ساختار Package و جهت وابستگی

```text
src/bisfin/
├── schema_contract.py # Head مشترک Runtime و Migration Registry
├── config/          # Settings و ساخت URL بدون افشای Secret
├── logging/         # Logging استاندارد console/json و ContextVar
├── domain/          # DTOهای frozen و مستقل از Persistence
├── db/              # Engine، Transaction، UoW، Health، Error و Core metadata
├── integrations/    # قرارداد BrsApi، HTTP/Fixture client و Parser خالص
├── ingestion/       # orchestration روزانه و Resultهای secret-free
├── repositories/    # Protocolها و پیاده‌سازی SQLAlchemy Core
└── cli.py           # Composition root و فرمان‌های عملیاتی
```

جهت مجاز وابستگی چنین است:

```text
CLI composition root
├──> settings/logging
└──> ingestion service
     ├──> integrations/brsapi (client, contracts, parser)
     └──> repositories/UoW ──> db primitives
                       └─────> domain DTOs
```

ماژول‌های `domain` هیچ Importی از SQLAlchemy، Psycopg، Repository، CLI یا تنظیم
Logging ندارند. Repositoryها Connection را دریافت می‌کنند و Transaction مستقل
نمی‌سازند. یک تست AST سبک این مرزها، نبود `metadata.create_all()` و عدم استفادهٔ
تاریخی از `market.current_bar` را کنترل می‌کند.

## Settings و Secretها

`bisfin.config.Settings` بر پایهٔ Pydantic 2 و `pydantic-settings` است. Environment
بر `.env` اولویت دارد و `DATABASE_URL` نیز در صورت وجود بر فیلدهای
`POSTGRES_HOST/PORT/DB/USER/PASSWORD` مقدم است. Schemeهای `postgres://` و
`postgresql://` به `postgresql+psycopg://` تبدیل می‌شوند. Helper دوم URL بومی
Psycopg با Scheme `postgresql://` را ارائه می‌کند.

`DATABASE_URL` و Password از نوع `SecretStr` هستند. `repr`، خطای CLI و
`safe_summary()` آن‌ها را نمایش نمی‌دهند. Username، Password و Database هنگام
ساخت fallback URL به‌درستی Percent-encode می‌شوند. Environmentهای مجاز عبارت‌اند
از `local`, `development`, `test`, `ci`, `staging`, `production` و Format لاگ فقط
`console` یا `json` است. Port باید در بازهٔ معتبر، Pool مثبت، Overflow نامنفی و
Timeoutها مثبت باشند.

`.env.example` فقط Default امن توسعه دارد. Secret واقعی نباید در Git، CLI، Error،
Log یا Metadata خطای ingestion ثبت شود.

Settings BrsApi شامل `BRSAPI_BASE_URL`, `BRSAPI_API_KEY`,
`BRSAPI_CONNECT_TIMEOUT_SECONDS`, `BRSAPI_READ_TIMEOUT_SECONDS`,
`BRSAPI_USER_AGENT`, `BRSAPI_PROVIDER_CODE`, `BRSAPI_DAILY_RAW_FEED_CODE`,
`BRSAPI_IDENTIFIER_TYPE`, `BRSAPI_DEFAULT_TIMEZONE` است. Base URL فقط HTTPS و
بدون credential/query/fragment، Timeoutها مثبت و finite، Timezone معتبر IANA و
API key از نوع Optional `SecretStr` است. Fixture key نمی‌خواهد؛ Live mode بدون
آن Fail می‌شود. Summary فقط configured بودن Key را اعلام می‌کند.

## Integration همگام BrsApi

`HttpxBrsApiClient` با `httpx 0.28.1` فقط این قرارداد را ارائه می‌کند:

```text
GET https://Api.BrsApi.ir/Tsetmc/Candlestick.php
key=<secret>&type=2&l18=<normalized-symbol>
```

Client synchronous، بدون Redirect و Retry است. Connect/Pool و Read/Write timeout
صریح‌اند و Body bytes دقیق، Headerهای allow-list و زمان شروع/دریافت ثبت می‌شوند.
URL کلیددار Log نمی‌شود؛ query و Key خام/encoded از Exception زنجیره‌شده پاک
می‌شوند. `FixtureBrsApiClient` همان DTO را با Clock تزریق‌شده و بدون Network می‌سازد.

مستند committed فیلدهای Candlestick را دارد، اما Envelope موفق را مشخص نکرده
است. Parser بنابراین Fail-closed فقط آرایهٔ غیرخالی Top-level از Objectها را
Success می‌داند و Shape دیگر را حدس نمی‌زند. no-data فقط Envelope صریح قرارداد
پروژه است و آرایهٔ خالی نیز مبهم و مردود است.
اعداد با `Decimal`، تاریخ جلالی با `jdatetime 6.0.1` و Symbol با
NFKC/Yeh/Kaf/Digit/Whitespace normalization پردازش می‌شوند؛ صفر ابتدایی و متن
اصلی Raw حفظ می‌شود. شرح کامل در `docs/brsapi_daily_bar_ingestion_fa.md` است.

## Logging ساختاریافته

`bisfin.logging.configure_logging` فقط از کتابخانهٔ استاندارد Python استفاده
می‌کند. حالت `console` برای انسان و حالت `json` یک JSON object در هر خط می‌سازد.
فیلدهای ثابت شامل Timestamp، Level، Logger، Message، Environment و Application
هستند. Contextهای `request_id`, `correlation_id`, `ingestion_batch_id` و
`backtest_run_id` با `ContextVar` اضافه می‌شوند.

برای هر عملیات مستقل از `log_context(...)` یا زوج `bind_log_context` و
`reset_log_context` استفاده شود. `clear_log_context()` مرز صریح عملیات جدید است.
Exceptionها ثبت می‌شوند، اما URL اتصال، Bearer Token و Password پیش از خروجی
Redact می‌شوند.

## Engine و چرخهٔ اتصال

`bisfin.db.engine.create_engine(settings)` یک Engine همگام SQLAlchemy 2 با Driver
Psycopg 3 می‌سازد، اما تا نخستین استفاده به PostgreSQL وصل نمی‌شود. Engine در
Import ماژول ساخته نمی‌شود و باید به Dependencyها تزریق شود. تنظیمات زیر اعمال
می‌شوند:

- `pool_pre_ping=True`؛
- `pool_size`, `max_overflow`, `pool_timeout` از Settings؛
- `application_name` در connect arguments؛
- `statement_timeout` در Option اتصال.

Isolation سراسری تنظیم نمی‌شود؛ Default PostgreSQL یعنی `READ COMMITTED` حفظ
می‌شود. مالک Engine باید در پایان Process یا Test، `dispose_engine(engine)` را
فراخوانی کند.

## Transaction Manager

مرز معمول Transaction به‌شکل زیر است:

```python
with transaction_manager.begin() as connection:
    ...
```

ورود Transaction را آغاز می‌کند، خروج موفق Commit، و هر Exception Rollback می‌کند
و همان Exception اصلی را دوباره بالا می‌فرستد. Connection در همهٔ مسیرها به Pool
برمی‌گردد. `read_only=True` برای Read مستقل قابل استفاده است و Isolation صریح نیز
پشتیبانی می‌شود.

Callerهایی که سه جدول زمانی Catalog را می‌نویسند باید `temporal_write=True` را
اعلام کنند. برای این Writeها `REPEATABLE READ` و `SERIALIZABLE` پیش از اتصال با
SQLSTATE قراردادی `0A000` رد می‌شوند، چون Triggerهای migration `0003` پس از
Advisory Lock به Snapshot تازهٔ `READ COMMITTED` نیاز دارند. این محدودیت برای
Read-onlyهای نامرتبط اعمال نمی‌شود. Retry خودکار هنوز وجود ندارد.

## Unit of Work

`SqlAlchemyUnitOfWorkFactory` شش Factory تایپ‌شده برای `data_feeds`,
`instruments`, `ingestion_batches`, `raw_events`, `bars`, `bar_writer` می‌گیرد.
هر UoW:

- دقیقاً یک Connection و یک Transaction دارد؛
- هر شش Repository را روی همان Connection می‌سازد؛
- فقط با `commit()` صریح پایدار می‌شود؛
- بدون Commit یا هنگام Exception Rollback می‌شود؛
- پس از Commit/Rollback/خروج قابل استفادهٔ دوباره نیست؛
- Re-entry و استفادهٔ تو‌در‌تو را با خطای Lifecycle روشن رد می‌کند.

Repositoryها اجازهٔ Commit/Rollback یا بازکردن Connection مستقل ندارند.

## SQLAlchemy Core Metadata

Core metadata فقط برای ساخت Query است و هیچ فراخوانی `create_all()` ندارد. جدول‌های
زیر با نام، Schema، ستون، Type، Nullability، Primary Key و Foreign Key واقعی map
شده‌اند:

```text
catalog.data_provider
catalog.data_feed
catalog.timeframe
catalog.trading_session
catalog.instrument
catalog.instrument_identifier
catalog.instrument_spec_version
ingest.ingestion_batch
ingest.raw_event
market.bar_series
market.bar_revision
```

دو تطبیق با Schema واقعی مهم است: جدول Provider واقعاً
`catalog.data_provider` است، نه `catalog.provider`. همچنین
`instrument_spec_version` برای `get_active_spec` ضروری است. Mappingهای Feed،
Timeframe و Trading Session برای ingestion افزوده شده‌اند. Test integration این
subset یازده‌جدولی را با Catalog زنده مقایسه می‌کند؛
کل ۷۲ جدول در Python بازتاب داده نشده‌اند.

## Repositoryها

### InstrumentRepository

- `get_by_id(instrument_id)`؛
- `find_by_identifier(provider_id, identifier_type, identifier_value, as_of)`؛
- `get_active_spec(instrument_id, as_of)`.

Lookup تاریخی دقیقاً از `[valid_from, valid_to)` استفاده می‌کند، صفر ابتدایی
Identifier را حفظ می‌کند و هرگز به Latest فعلی fallback نمی‌کند. بیش از یک Match
فعال، حتی اگر به‌علت نقص غیرمنتظرهٔ داده باشد، `IntegrityViolationError` می‌دهد.
PostgreSQL `-infinity` در DTO به `None` projection می‌شود؛ فیلتر زمانی همچنان روی
مقدار اصلی دیتابیس انجام می‌شود.

### IngestionBatchRepository

- `create_batch`, `get_by_id`, `mark_running`, `mark_succeeded`, `mark_failed`.

PR-05 همچنین `create_batch_if_absent`, `get_by_request_id`,
`record_acquisition`, `finalize_batch` را برای Idempotency و مرزهای A/B/C اضافه
می‌کند. `(feed_id, request_id)` Authority دیتابیس است و Finalization فقط از
`RUNNING` ممکن است.

Schema وضعیت `QUEUED` ندارد؛ Batch هنگام ایجاد مستقیماً `RUNNING` است. بنابراین
`mark_running` یک Assertion idempotent روی همان وضعیت است و Batch نهایی را باز
نمی‌کند. Finalization فقط با `UPDATE ... WHERE status='RUNNING'` انجام می‌شود و
تکرار آن `InvalidStateTransitionError` می‌دهد. `finished_at` از زمان دیتابیس گرفته
می‌شود. Failure شامل Code/Message/Details محدود، JSON-compatible و Redacted است؛
Authorization header یا Secret کامل ذخیره نمی‌شود.

### BarRepository

`get_series_by_id` Metadata Series را می‌خواند. متد تاریخی فقط SQL زیر را اجرا
می‌کند:

```sql
SELECT
    bar_open_ts, bar_series_id, revision_no, available_at,
    system_available_at, bar_close_ts, trading_date,
    open_price, high_price, low_price, close_price,
    official_close_price, settlement_price, volume, quote_volume,
    trade_count, vwap, open_interest, is_final, quality_flags,
    ingestion_batch_id, recorded_at, previous_close_price,
    effective_available_at
FROM market.bars_as_of(
    CAST(:bar_series_id AS BIGINT),
    CAST(:from_ts AS TIMESTAMPTZ),
    CAST(:to_ts AS TIMESTAMPTZ),
    CAST(:knowledge_cutoff_ts AS TIMESTAMPTZ),
    CAST(:replay_mode AS VARCHAR)
)
ORDER BY bar_open_ts, bar_series_id;
```

هیچ انتخاب Revision در Python تکرار نشده است. Repository تاریخی نه
`market.current_bar` و نه `market.bar_revision` را مستقیماً Query می‌کند. قیمت‌ها
و حجم‌ها `Decimal`، زمان‌ها timezone-aware و Mode دقیقاً یکی از
`PUBLIC_REPLAY`/`ACTUAL_SYSTEM_REPLAY` است.

### Repositoryهای Ingestion روزانه

`DataFeedRepository` فقط Provider/Feed/Timeframe `1d` از پیش provision‌شده را
Resolve می‌کند و چیزی نمی‌سازد. `InstrumentRepository` شناسهٔ تاریخی را در Open
همان Regular Session Resolve می‌کند. `RawEventRepository` Partition UTC ماه
دریافت را تضمین، هر Object کامل را immutable درج، فقط Validation result را Update
و Audit Batch را Query می‌کند. Hash Row از JSON قطعی UTF-8 و Hash Batch از Byteهای
دقیق پاسخ است.

`BarWriterRepository` هویت دقیق `RAW/1d/LAST_TRADE/REGULAR` را conflict-safe
resolve/create می‌کند، ماه‌های Bar را در ترتیب ثابت زیر Advisory Lock می‌سازد و
برای هر `(bar_series_id, bar_open_ts)` Revision را append می‌کند. فیلدهای مالی
یکسان `UNCHANGED`، نخستین Bar `INSERTED` و Correction واقعی `CORRECTED` است؛ هیچ
Revision قبلی Update/Delete نمی‌شود.

## Orchestration و Availability

Service پایگاه داده را هنگام HTTP I/O باز نگه نمی‌دارد: Transaction A Batch
`RUNNING` را Commit می‌کند، Fetch بیرون Transaction است، B Hash/Raw acquisition
را Commit و C Catalog/Session/Series/Revision/Validation/Finalization را Atomic
می‌کند. Failure با Transaction کوتاه جدا ثبت می‌شود؛ بنابراین Failure مرحله C
Rawهای Commit‌شدهٔ B را حذف نمی‌کند.

تاریخ و `time` Provider زمان Availability نیستند. سیاست محافظه‌کار:

```text
available_at        = response_received_at
system_available_at = clock immediately before canonical persistence
```

Session open/close زمان Canonical Bar را تعیین می‌کند. `system_available_at` نباید
از `available_at` عقب‌تر باشد و Correction دیرهنگام به گذشته Backdate نمی‌شود.

## Error Model

Hierarchy کوچک خطاها شامل Configuration، Database unavailable، Repository،
Integrity، Temporal overlap، PIT invalid، Entity not found، State transition و UoW
Lifecycle است. Mappingهای مهم `23P01`, `22004`, `22023`, `P0002`, `0A000` حفظ
می‌شوند و Caller می‌تواند `sqlstate` و Exception اصلی را بررسی کند. Exception اصلی
با chaining نگه داشته می‌شود و URL/Password در Message برنامه ظاهر نمی‌شود.
Sanitizer محل کامل URLهای PostgreSQL و مقدار Authorization/Token را حذف می‌کند؛
Failure metadata نیز بر اساس نام Key و محتوای String پاک‌سازی و محدود می‌شود.

## Health Check و CLI

`DatabaseHealthChecker` فقط Queryهای ارزان Catalog را اجرا می‌کند: `SELECT 1`،
نسخهٔ major برابر ۱۶، Alembic برابر Head Registry، وجود Schemaهای واقعی
`catalog`, `ingest`, `market`, `backtest`, `ml`، امضای `market.bars_as_of`، صفر
Index نامعتبر و صفر Constraint تأییدنشده. درخواست اولیه نام `feature` را ذکر می‌کرد،
اما Inspection نشان داد Schema واقعی Feature Store برابر `ml` است. خروجی یک
`DatabaseHealthReport` شامل Checkهای مستقل و Summary است.
مقدار `ALEMBIC_HEAD_REVISION` داخل Package قرار دارد تا Console Script نصب‌شده به
فایل خارج از Wheel وابسته نباشد؛ خود `migration_registry.py` نیز Revision نهایی را
از همین قرارداد می‌گیرد و Test عدم Drift را بررسی می‌کند.

Head فعلی `0004` است. این Migration فقط تابع
`ingest.create_raw_event_month_partition(DATE)` را با Lock تراکنشی ماهانه، مرز UTC
و الزام `READ COMMITTED` اضافه می‌کند؛ Checksum آن
`188080740e805ed9d58de2f4c72a3007b6c46a45e3b253e7f5226d8538a417b7` است.

فرمان‌ها:

```bash
uv run --frozen bisfin config-check
uv run --frozen bisfin db-health
uv run --frozen bisfin db-current
```

PR-05 فرمان Fixture/Live زیر را نیز اضافه می‌کند:

```bash
uv run --frozen bisfin ingest brsapi-daily-bars \
  --symbol فملی \
  --fixture tests/fixtures/brsapi/candlestick_type2_success.json \
  --output-format human
```

`--request-id` اختیاری و `--output-format json` قابل استفاده است. Fixture مسیر
Network و نیاز API key را حذف می‌کند؛ بدون `--fixture` Key الزامی است. Engine و
Logging context در تمام مسیرها پاک می‌شوند و Summary هیچ Raw body/Secret/URL
کلیدداری ندارد.

فرمان ingestion برای `SUCCEEDED` مقدار خروج `0`، برای `PARTIAL` مقدار `3` و برای
`FAILED`، `QUARANTINED` یا خطای عملیاتی مقدار `4` دارد؛ خطای Settings پیش از
اجرای فرمان مقدار `2` است. Make target زنده علاوه بر Key فقط با
`BISFIN_RUN_BRSAPI_LIVE_TEST=1` فعال می‌شود، در حالی که اجرای مستقیم CLI با حذف
`--fixture` مسئولیت صریح اپراتور است.

`config-check` فقط Summary غیرمحرمانه چاپ می‌کند. `db-health` فقط در حالت سلامت
کامل Exit 0 دارد. `db-current` Revision جاری و Head مورد انتظار را چاپ می‌کند و در
Mismatch یا عدم اتصال non-zero است.

## تست و توسعهٔ محلی

Unit testها Docker را آغاز نمی‌کنند:

```bash
uv run --frozen pytest
make python-lint
make python-format-check
```

Integration testها به PostgreSQL 16 آماده و migrate‌شده نیاز دارند:

```bash
make db-up
make db-wait
make db-migrate
make python-test-integration
```

Fixtureهای integration دادهٔ یکتا می‌سازند، به ترتیب معکوس پاک می‌کنند، تاریخچهٔ
Alembic را تغییر نمی‌دهند و کل Database را reset نمی‌کنند. تست‌های SQL و رقابت
Triggerها مستقل باقی مانده‌اند: `make db-test` و `make db-test-pit`.

تست‌های متمرکز PR-05:

```bash
make brsapi-test
make brsapi-test-integration
make brsapi-ingest-fixture
```

Target زندهٔ Make فقط با
`BISFIN_RUN_BRSAPI_LIVE_TEST=1 make brsapi-ingest-live` فعال می‌شود و هیچ‌گاه عضو
CI یا Targetهای عادی نیست.

در Windows استفاده از Git Bash یا WSL پیشنهاد می‌شود. Docker Desktop باید فعال
باشد و `POSTGRES_HOST=127.0.0.1` از تأخیر fallback IPv6 روی بعضی میزبان‌ها جلوگیری
می‌کند. PowerShell برای Copy فایل محیط می‌تواند `Copy-Item .env.example .env` را
به‌کار ببرد.

پیش از Commit:

```bash
uv sync --locked --dev
uv lock --check
make python-lint
make python-format-check
make python-test
make db-up db-wait db-migrate
make migration-check db-test db-test-pit
make python-test-integration
make app-config-check app-db-health app-db-current
```

## محدودیت‌ها و PR بعدی

- Foundation فقط synchronous است؛ Async DB در workload فعلی توجیه ندارد.
- Retry، COPY bulk، Instrument/Calendar auto-create و Scheduling وجود ندارند.
- تغییر Migration head باید هم‌زمان از قرارداد مشترک `schema_contract.py` عبور کند؛
  Migration Registry و Test خودکار از Drift جلوگیری می‌کنند.
- `mark_running` Transition واقعی نیست، زیرا Schema وضعیت پیش از RUNNING ندارد.
- `market.current_bar` صرفاً برای Current-state عملیاتی است.
- فقط `type=2` پشتیبانی می‌شود؛ `type=1`, `type=3` و endpointهای دیگر خارج‌اند.
- Availability تاریخی عمداً به زمان Acquisition محدود می‌شود.

PR بعدی می‌تواند Calendar/Symbol-master provisioning یا Worker زمان‌بندی‌شده با
Retry/Rate-limit صریح را هدف بگیرد. Candle تعدیل‌شده فقط پس از قرارداد مستقل
Corporate Action/Adjustment قابل افزودن است.

## PR-06: Bootstrap و Calendar صریح

PR-06 کاتالوگ و تقویم را با فایل‌های JSON نسخه‌دار و Repositoryهای جدید
`catalog_writer` و `trading_calendar` تأمین می‌کند. Parsing و Symbol fixture/live
خارج از تراکنش هستند؛ نوشتن temporal کاتالوگ با `READ COMMITTED` انجام می‌شود.
جزئیات عملیاتی در `catalog_bootstrap_fa.md` و `trading_calendar_import_fa.md` است.
