# معماری برنامهٔ Python و دسترسی Typed به PostgreSQL

این سند قرارداد اجرایی PR-04 را توصیف می‌کند. دامنهٔ این مرحله Foundation همگام
Python 3.12 برای Worker و CLI است؛ هیچ تماس BrsApi، درج Bar، Worker، API Server یا
محاسبهٔ Feature در آن وجود ندارد.

## ساختار Package و جهت وابستگی

```text
src/bisfin/
├── schema_contract.py # Head مشترک Runtime و Migration Registry
├── config/          # Settings و ساخت URL بدون افشای Secret
├── logging/         # Logging استاندارد console/json و ContextVar
├── domain/          # DTOهای frozen و مستقل از Persistence
├── db/              # Engine، Transaction، UoW، Health، Error و Core metadata
├── repositories/    # Protocolها و پیاده‌سازی SQLAlchemy Core
└── cli.py           # Composition root و فرمان‌های عملیاتی
```

جهت مجاز وابستگی چنین است:

```text
config ──> db primitives ──> repositories ──> application entry points
              ^                    |
              └──── domain DTOs ───┘
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

`SqlAlchemyUnitOfWorkFactory` سه Factory Repository را می‌گیرد. هر UoW:

- دقیقاً یک Connection و یک Transaction دارد؛
- Repositoryهای `instruments`, `ingestion_batches`, `bars` را روی همان Connection
  می‌سازد؛
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
`instrument_spec_version` هشتمین Mapping ضروری است، چون `get_active_spec` بدون آن
قابل پیاده‌سازی نیست. Test integration این subset را با Catalog زنده مقایسه می‌کند؛
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

فرمان‌ها:

```bash
uv run --frozen bisfin config-check
uv run --frozen bisfin db-health
uv run --frozen bisfin db-current
```

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
- Retry، COPY bulk، Instrument auto-create، Raw parser و Bar write عمداً وجود ندارند.
- تغییر Migration head باید هم‌زمان از قرارداد مشترک `schema_contract.py` عبور کند؛
  Migration Registry و Test خودکار از Drift جلوگیری می‌کنند.
- `mark_running` Transition واقعی نیست، زیرا Schema وضعیت پیش از RUNNING ندارد.
- `market.current_bar` صرفاً برای Current-state عملیاتی است.

PR بعدی یک Vertical Slice کوچک ingestion روزانهٔ BrsApi را روی همین Settings،
Transaction، UoW، Repository و DTOها می‌سازد؛ HTTP client یا رفتار ingestion در
PR-04 پیاده‌سازی نشده است.
