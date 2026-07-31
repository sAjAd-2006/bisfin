# فرهنگ داده (Data Dictionary)

این سند مکمل زنجیرهٔ Migrationهای اجرایی `0001` تا `0004` در `db/postgresql/migrations/` است. تعریف SQL مرجع نهایی نوع، Default، `CHECK`، `PRIMARY KEY` و `FOREIGN KEY` است. توسعه بخش ML/DL در فاز فعلی متوقف است؛ Migration `0003` جامعیت زمانی Catalog و دسترسی امن Point-in-Time به Bar را سخت‌گیرانه می‌کند و Migration `0004` فقط Helper هم‌زمانی امن برای Partition ماهانهٔ Raw Event را می‌افزاید.

نشانه‌ها: `NN` = `NOT NULL`، `NULL` = Nullable، `PK` = کلید اصلی، `FK` = کلید خارجی، `UQ` = یکتا، `ID` = `GENERATED ALWAYS AS IDENTITY`. تمام `TIMESTAMPTZ`ها دقت ۶ و قرارداد UTC دارند؛ همه بازه‌ها `[from,to)` هستند. تمام `NUMERIC`های مالی، مگر آن‌جا که صریحاً ذکر شده، `NUMERIC(38,18)` هستند.

## Catalog و Instrument Master

### `catalog.currency` — ارز/واحد ارزش Canonical

- `currency_code VARCHAR(12) NN PK`: کد ارز یا Crypto؛ مثل IRR/USD/BTC.
- `display_name TEXT NN`: نام نمایشی.
- `minor_unit SMALLINT NN DEFAULT 2`: تعداد رقم جزء؛ بازه ۰ تا ۱۸.
- `is_fiat BOOLEAN NN DEFAULT true`: ارز رسمی یا دارایی دیجیتال.
- `metadata JSONB NN DEFAULT {}`: ویژگی‌های کم‌تکرار.

### `catalog.asset_type` — رده دارایی

- `asset_type_code VARCHAR(32) NN PK`: کد پایدار مانند EQUITY/FOREX/OPTION.
- `display_name TEXT NN`: نام نمایشی.
- `description TEXT NULL`: شرح رده.

### `catalog.data_provider` — عرضه‌کننده داده

- `provider_id SMALLINT NN PK ID`: شناسه داخلی.
- `provider_code VARCHAR(64) NN UQ`: کد پایدار Provider.
- `display_name TEXT NN`: نام.
- `provider_kind VARCHAR(24) NN`: یکی از Exchange/Broker/Vendor/Internal/Public.
- `base_url TEXT NULL`: نشانی مستندات/API؛ Secret نیست.
- `default_timezone VARCHAR(64) NULL`: نام IANA منطقه زمانی.
- `metadata JSONB NN`: Metadata غیرهسته‌ای.
- `created_at TIMESTAMPTZ NN`: زمان ثبت.

### `catalog.data_feed` — قرارداد/Feed داده

- `feed_id BIGINT NN PK ID`: شناسه Feed.
- `provider_id SMALLINT NN FK -> data_provider`: مالک Feed.
- `feed_code VARCHAR(96) NN`: کد درون Provider؛ همراه `provider_id` یکتا.
- `display_name TEXT NN`: نام.
- `data_kind VARCHAR(32) NN`: Instrument/Bar/Tick/OrderBook/Fundamental/Document و غیره.
- `native_timezone VARCHAR(64) NULL`: Zone زمان خام.
- `parser_version TEXT NULL`: نسخه Parser فعلی.
- `active_from`, `active_to TIMESTAMPTZ NULL`: اعتبار Feed؛ انتها بزرگ‌تر از آغاز.
- `metadata JSONB NN`: تنظیمات غیرمحرمانه و قرارداد منبع.

### `catalog.venue` — بورس/محل معامله

- `venue_id SMALLINT NN PK ID`: شناسه داخلی.
- `venue_code VARCHAR(32) NN UQ`: کد پایدار.
- `display_name TEXT NN`: نام بازار.
- `mic_code VARCHAR(8) NULL`: Market Identifier Code در صورت وجود.
- `country_code CHAR(2) NULL`: کشور ISO.
- `timezone_name VARCHAR(64) NN`: Zone IANA برای تبدیل زمان محلی.
- `base_currency_code VARCHAR(12) NULL FK -> currency`: ارز پایه بازار.
- `metadata JSONB NN`: ویژگی‌های بازار/تابلو.

### `catalog.timeframe` — دانه‌بندی زمانی

- `timeframe_id SMALLINT NN PK ID`: شناسه.
- `timeframe_code VARCHAR(16) NN UQ`: مانند 1m/2m/5m/15m/1h/1d.
- `display_name TEXT NN`: نام.
- `duration_seconds INTEGER NULL`: فقط برای Fixed Interval و بزرگ‌تر از صفر.
- `calendar_unit VARCHAR(16) NN`: Fixed/Session/Day/Week/Month.
- `session_aligned BOOLEAN NN`: هم‌ترازی با Session بازار.

### `catalog.trading_session` — تقویم معاملاتی

- `venue_id SMALLINT NN PK/FK -> venue`: بازار.
- `trading_date DATE NN PK`: تاریخ میلادی Canonical.
- `session_code VARCHAR(24) NN PK`: Regular/Pre-open و غیره.
- `is_trading_day BOOLEAN NN`: روز باز یا تعطیل.
- `session_open_ts`, `session_close_ts TIMESTAMPTZ NULL`: برای روز باز اجباری و Close بعد از Open.
- `settlement_date DATE NULL`: تاریخ تسویه.
- `metadata JSONB NN`: Half-day، Holiday reason و جزئیات.

### `catalog.instrument` — ابزار مالی Canonical

- `instrument_id BIGINT NN PK ID`: شناسه داخلی پایدار.
- `asset_type_code VARCHAR(32) NN FK -> asset_type`: نوع دارایی.
- `venue_id SMALLINT NULL FK -> venue`: محل Listing؛ برای Global/OTC می‌تواند NULL باشد.
- `quote_currency_code VARCHAR(12) NN FK -> currency`: ارز مظنه.
- `canonical_symbol VARCHAR(128) NN`: نماد فعلی؛ همراه Venue با `NULLS NOT DISTINCT` یکتا.
- `display_name TEXT NN`: نام فعلی.
- `status VARCHAR(16) NN`: Pending/Active/Halted/Delisted/Expired/Inactive.
- `active_from`, `active_to TIMESTAMPTZ NULL`: بازه حیات؛ انتها Exclusive.
- `metadata JSONB NN`: ویژگی‌های Long-tail.
- `created_at TIMESTAMPTZ NN`: زمان ایجاد Master Record.

### `catalog.instrument_identifier` — شناسه و نماد Provider به‌صورت SCD2

- `provider_id SMALLINT NN PK/FK -> data_provider`: Provider.
- `identifier_type VARCHAR(32) NN PK`: مانند ISIN/TSETMC_ID/L18/CONTRACT_CODE.
- `identifier_value TEXT NN PK`: مقدار خام؛ Text برای حفظ صفر و دقت.
- `valid_from TIMESTAMPTZ NN PK`: آغاز اعتبار.
- `valid_to TIMESTAMPTZ NULL`: انتهای Exclusive.
- `instrument_id BIGINT NN FK -> instrument`: ابزار Canonical.
- `is_primary BOOLEAN NN`: شناسه اصلی در آن دوره.
- `metadata JSONB NN`: نام خام و مشخصات اضافی.
- Migration `0003` هم‌پوشانی `[valid_from,valid_to)` را بر کلید منطقی `(provider_id, identifier_type, identifier_value)` با SQLSTATE `23P01` منع می‌کند؛ `valid_to IS NULL` یعنی `infinity` و مرزهای مساوی مجاور مجازند.

### `catalog.instrument_spec_version` — مشخصات معامله‌پذیری تاریخ‌مند

- `instrument_id BIGINT NN PK/FK -> instrument`: ابزار.
- `effective_from TIMESTAMPTZ NN PK`, `effective_to TIMESTAMPTZ NULL`: بازه نسخه.
- `price_tick`, `quantity_step`, `lot_size NUMERIC NULL`: Tick/Step/Lot مثبت.
- `contract_multiplier NUMERIC NN DEFAULT 1`: ضریب قرارداد مثبت.
- `price_scale`, `quantity_scale SMALLINT NULL`: Scale Feed/Integer optimization.
- `lower_price_limit`, `upper_price_limit NUMERIC NULL`: دامنه مجاز همان نسخه.
- `shares_outstanding NUMERIC(38,6) NULL`: سهام/واحد منتشرشده.
- `metadata JSONB NN`: حجم مبنا، Margin rule و مشخصات خاص.
- Migration `0003` هم‌پوشانی `[effective_from,effective_to)` را برای هر `instrument_id` منع می‌کند.

### `catalog.derivative_contract` — Subtype مشتقه

- `instrument_id BIGINT NN PK/FK -> instrument`: خود قرارداد.
- `underlying_instrument_id BIGINT NULL FK -> instrument`: دارایی پایه.
- `contract_type VARCHAR(16) NN`: Future/Option/Swap/Forward/CFD.
- `option_side VARCHAR(4) NULL`: Call/Put؛ برای Option اجباری.
- `strike_price NUMERIC NULL`: برای Option اجباری.
- `contract_size NUMERIC NN`: اندازه مثبت.
- `expiry_ts TIMESTAMPTZ NULL`: سررسید.
- `settlement_type VARCHAR(16) NULL`: Cash/Physical.
- `initial_margin`, `maintenance_margin NUMERIC NULL`: وجه تضمین.
- `metadata JSONB NN`: واحد کالا، Settlement calendar و جزئیات.

### `catalog.corporate_action` — رویداد شرکتی Versioned

- `corporate_action_id BIGINT NN PK ID`: شناسه.
- `instrument_id BIGINT NN FK -> instrument`: ابزار اثرپذیر.
- `feed_id BIGINT NULL FK -> data_feed`, `external_id TEXT NULL`: Provenance و کلید منبع.
- `action_type VARCHAR(24) NN`: Split/Dividend/Rights/Merger و غیره.
- `announced_at TIMESTAMPTZ NULL`: زمان اعلان.
- `ex_ts TIMESTAMPTZ NN`: زمان اثر اقتصادی.
- `record_date`, `payable_date DATE NULL`: تاریخ ثبت/پرداخت.
- `ratio_numerator`, `ratio_denominator NUMERIC NULL`: نسبت؛ مخرج صفر نیست.
- `cash_amount NUMERIC NULL`, `currency_code VARCHAR(12) NULL FK`: مبلغ نقدی.
- `available_at`, `system_available_at TIMESTAMPTZ NN`: زمان عمومی/سامانه؛ دومی دیرتر یا مساوی.
- `revision_no INTEGER NN`: نسخه مثبت.
- `metadata JSONB NN`: جزئیات منبع.

### `catalog.adjustment_set` و `catalog.adjustment_factor` — سیاست تعدیل

- `adjustment_set`: `adjustment_set_id BIGINT PK ID`، `instrument_id BIGINT NN FK`، `method_code VARCHAR(32) NN`، `version_no INTEGER NN`، `knowledge_cutoff_ts TIMESTAMPTZ NN`، `code_sha256 CHAR(64) NULL`، `created_at TIMESTAMPTZ NN`، `metadata JSONB NN`. ترکیب ابزار/روش/نسخه یکتا است.
- `adjustment_factor`: `adjustment_set_id BIGINT NN PK/FK`، `effective_ts TIMESTAMPTZ NN PK`، `price_multiplier NUMERIC NN >0`، `price_addend NUMERIC NN`، `volume_multiplier NUMERIC NN >0`، `source_action_id BIGINT NULL FK -> corporate_action`.

### `catalog.universe` و `catalog.universe_member` — Universe بدون Survivorship Bias

- `universe`: `universe_id BIGINT PK ID`، `universe_code VARCHAR(96) NN UQ`، `display_name TEXT NN`، `selection_rule JSONB NN`، `created_at TIMESTAMPTZ NN`.
- `universe_member`: `universe_id BIGINT NN PK/FK`، `instrument_id BIGINT NN PK/FK`، `valid_from TIMESTAMPTZ NN PK`، `valid_to TIMESTAMPTZ NULL`، `weight DOUBLE NULL`، `source_reason TEXT NULL`.
- Migration `0003` هم‌پوشانی `[valid_from,valid_to)` را برای هر `(universe_id, instrument_id)` منع می‌کند.

هر سه Guard پیش از Query هم‌پوشانی یک `pg_advisory_xact_lock` مشتق از نام جدول و کلید منطقی می‌گیرند؛ بنابراین رقابت فقط برای همان Entity سریال می‌شود. نوشتن این سه جدول باید در `READ COMMITTED` باشد؛ Isolation قوی‌تر با `0A000` رد می‌شود تا Snapshot قدیمی پس از انتظار قفل، جامعیت را تضعیف نکند.

### `catalog.data_snapshot` و `catalog.data_snapshot_component` — مرز تکرارپذیری

- `data_snapshot`: `data_snapshot_id BIGINT PK ID`، `snapshot_code VARCHAR(128) NN UQ`، `knowledge_cutoff_ts TIMESTAMPTZ NN`، `availability_mode VARCHAR(24) NN`، `manifest_sha256 CHAR(64) NULL در Building و اجباری در Frozen`، `status VARCHAR(16) NN`، `created_at TIMESTAMPTZ NN`، `frozen_at TIMESTAMPTZ NULL`، `metadata JSONB NN`.
- `data_snapshot_component`: `data_snapshot_id BIGINT NN PK/FK`، `component_key VARCHAR(160) NN PK`، `feed_id BIGINT NULL FK`، `event_from/event_to TIMESTAMPTZ NULL`، `max_available_at/max_system_available_at TIMESTAMPTZ NULL`، `row_count BIGINT NULL`، `component_sha256 CHAR(64) NN`، `storage_uri TEXT NULL`.

## Ingestion، Market و External Data

### `ingest.ingestion_batch` — واحد Audit دریافت

- `ingestion_batch_id BIGINT NN PK ID`: شناسه Batch.
- `feed_id BIGINT NN FK -> data_feed`: Feed.
- `request_id TEXT NULL`: Idempotency key عرضه‌کننده؛ همراه Feed یکتا در صورت وجود.
- `requested_event_from`, `requested_event_to TIMESTAMPTZ NULL`: بازه درخواست با انتهای Exclusive.
- `started_at TIMESTAMPTZ NN`, `finished_at TIMESTAMPTZ NULL`: زمان اجرا.
- `status VARCHAR(16) NN`: Running/Succeeded/Partial/Failed/Quarantined.
- `received_row_count`, `accepted_row_count`, `rejected_row_count BIGINT NN`: شمارش‌های نامنفی و قابل تطبیق.
- `payload_sha256 CHAR(64) NULL`: Hash کل Payload/Batch.
- `parser_version TEXT NN`: نسخه Parser/Mapping.
- `source_watermark TEXT NULL`: Cursor/ETag/Sequence منبع.
- `error_summary TEXT NULL`, `metadata JSONB NN`: خطا و Metadata.

### `ingest.raw_event` — Raw Zone پارتیشن‌شده

- `ingested_at TIMESTAMPTZ NN PK/PARTITION KEY`: زمان ورود.
- `raw_event_id BIGINT NN PK ID`: شناسه درون Partition tree.
- `ingestion_batch_id BIGINT NN FK -> ingestion_batch`, `feed_id BIGINT NN FK`: Lineage.
- `source_record_key TEXT NULL`, `source_sequence BIGINT NULL`: Natural key/Sequence منبع.
- `source_event_time_text`, `source_date_text TEXT NULL`: نمایش خام، از جمله تاریخ جلالی.
- `observed_at TIMESTAMPTZ NULL`: زمان مشاهده شبکه/Collector.
- `payload_sha256 CHAR(64) NN`, `raw_payload JSONB NN`: Hash و Payload تغییرنکرده.
- `validation_status VARCHAR(16) NN`, `validation_errors JSONB NN`: نتیجه Contract/Data Quality.
- `ingest.create_raw_event_month_partition(month)`: Partition ماهانهٔ UTC با نام `raw_event_yYYYYmMM` می‌سازد؛ قفل Advisory تراکنشی، اجرای تکراری و هم‌زمان را در `READ COMMITTED` امن می‌کند و هیچ Default Partition نمی‌سازد.

### `market.bar_series` — هویت یک سری کندل

- `bar_series_id BIGINT NN PK ID`: شناسه سری.
- `feed_id BIGINT NN FK`, `instrument_id BIGINT NN FK`, `timeframe_id SMALLINT NN FK`: منبع/ابزار/تایم‌فریم.
- `price_basis VARCHAR(24) NN`: Raw/Split-adjusted/Total-return/Provider-adjusted/Custom.
- `adjustment_set_id BIGINT NULL FK`: برای Basis غیر Raw اجباری و متعلق به همان ابزار.
- `close_semantics VARCHAR(24) NN`: Last-trade/Official-close/Settlement/Mid/NAV.
- `session_code VARCHAR(24) NN`: Session مربوط.
- `metadata JSONB NN`, `created_at TIMESTAMPTZ NN`: جزئیات و زمان ایجاد.

### `market.bar_revision` — OHLCV نسخه‌دار، پارتیشن Range روی `bar_open_ts`

- کلید: `bar_open_ts TIMESTAMPTZ NN PARTITION/PK`، `bar_series_id BIGINT NN PK/FK`، `revision_no INTEGER NN PK`، `available_at TIMESTAMPTZ NN PK`.
- زمان: `system_available_at TIMESTAMPTZ NN`، `bar_close_ts TIMESTAMPTZ NN`، `trading_date DATE NN`؛ Bar نهایی قبل از Close قابل‌استفاده نیست.
- قیمت: `open_price`, `high_price`, `low_price`, `close_price NUMERIC NN`؛ High/Low و دامنه Open/Close با `CHECK`.
- قیمت‌های متمایز: `official_close_price`, `settlement_price`, `previous_close_price NUMERIC NULL` تا `pc/pl/settlement/py` یکی نشوند.
- فعالیت: `volume`, `quote_volume NUMERIC NULL`، `trade_count BIGINT NULL`، `vwap`, `open_interest NUMERIC NULL`؛ مقادیر حجمی نامنفی.
- کیفیت/Lineage: `is_final BOOLEAN NN`، `quality_flags INTEGER NN`، `ingestion_batch_id BIGINT NN FK`، `recorded_at TIMESTAMPTZ NN`.

### `market.quote_snapshot` — Snapshot نسخه‌دار وضعیت و آمار جاری بازار

- کلید/زمان: `event_ts/feed_id/instrument_id/revision_no/available_at`، به‌همراه `system_available_at` و `trading_date`؛ پارتیشن Range ماهانه روی `event_ts`.
- وضعیت: `source_state TEXT NULL` و `normalized_state` از Preopen/Open/Auction/Halted/Suspended/Closed/Unknown. وضعیت چرخه‌عمر در `catalog.instrument.status` باقی می‌ماند.
- قیمت: دامنه روزانه، قیمت دیروز، اولین/کمینه/بیشینه/آخرین/پایانی/تسویه؛ نگاشت BrsApi به‌ترتیب `tmin/tmax`, `py`, `pf/pmin/pmax/pl/pc` است.
- فعالیت: `base_volume`, `trade_count`, `volume`, `turnover_value`؛ سپس `is_final`, `ingestion_batch_id`, `quality_flags`, `recorded_at`.
- دو ایندکس B-tree برای Public/System PIT و یک BRIN زمانی دارد.

### `market.participant_flow_snapshot` — جریان حقیقی/حقوقی

- همان هویت زمانی و Revision جدول Quote، همراه `window_start_ts` و `aggregation_kind` از Bar/Session-to-date/Session-final.
- ۱۲ مقدار Typed برای تعداد، حجم و ارزش خرید/فروش حقیقی (`individual`) و حقوقی (`legal`)؛ مقدارهای Snapshot زنده می‌توانند NULL باشند.
- Wide Table بودن عمدی است تا محاسبه سرانه، قدرت خریدار و خالص جریان در بک‌تست نیازمند Pivot روی EAV نباشد.

### `market.trade_tick` — Tape نسخه/ابطال‌پذیر، پارتیشن Range روی `event_ts`

- کلید: `event_ts TIMESTAMPTZ NN PK/PARTITION`، `feed_id BIGINT NN PK/FK`، `instrument_id BIGINT NN PK/FK`، `source_sequence BIGINT NN PK`، `event_no SMALLINT NN PK`، `revision_no INTEGER NN PK`.
- `available_at`, `system_available_at TIMESTAMPTZ NN`: دسترسی عمومی/واقعی.
- `event_ts_ns BIGINT NULL`: ترتیب Nanosecond اختیاری.
- `source_trade_id TEXT NULL`: شناسه معامله Vendor.
- `trade_state VARCHAR(12) NN`: Active/Canceled/Corrected.
- `price`, `quantity NUMERIC NULL`: در Cancel می‌توانند خالی باشند؛ Quantity نامنفی.
- `aggressor_side CHAR(1) NULL`: Buy/Sell/Unknown.
- `ingestion_batch_id BIGINT NN FK`, `quality_flags INTEGER NN`, `recorded_at TIMESTAMPTZ NN`: Lineage/کیفیت.

### Order Book: `market.order_book_snapshot`, `order_book_level`, `order_book_delta`

- Snapshot header: `event_ts TIMESTAMPTZ NN PK/PARTITION`، `snapshot_id BIGINT NN PK ID`، `feed_id BIGINT NN FK`، `instrument_id BIGINT NN FK`، `source_sequence BIGINT NN`، `revision_no INTEGER NN`، `available_at/system_available_at TIMESTAMPTZ NN`، `is_complete BOOLEAN NN`، `depth SMALLINT NULL`، `ingestion_batch_id BIGINT NN FK`، `quality_flags INTEGER NN`.
- Level: `event_ts TIMESTAMPTZ NN PK/FK/PARTITION`، `snapshot_id BIGINT NN PK/FK`، `side CHAR(1) NN PK`، `level_no SMALLINT NN PK`، `price NUMERIC NN`، `quantity NUMERIC NN`، `order_count INTEGER NULL`. حذف Snapshot به Levelها Cascade می‌شود.
- Delta: کلید `event_ts/feed_id/instrument_id/source_sequence/event_no/revision_no`؛ سپس `available_at/system_available_at TIMESTAMPTZ NN`، `side CHAR(1) NULL`، `action_code VARCHAR(8) NN`، `price/quantity NUMERIC NULL`، `order_count INTEGER NULL`، `ingestion_batch_id BIGINT NN FK`، `quality_flags INTEGER NN`. Clear به Price/Side نیاز ندارد؛ Upsert/Delete دارد.

### `external.data_series` — رجیستری داده بنیادی/جایگزین

- `series_id BIGINT NN PK ID`, `feed_id BIGINT NN FK`: هویت و منبع.
- `series_code VARCHAR(160) NN`: همراه Feed یکتا؛ `display_name TEXT NN`.
- `entity_type VARCHAR(24) NN`: Instrument/Issuer/Document/Blockchain/Macro/Global/Other.
- `value_type VARCHAR(16) NN`: Numeric/Float/Integer/Boolean/Text/JSON.
- `unit_code VARCHAR(32) NULL`, `currency_code VARCHAR(12) NULL FK`: واحد.
- `event_time_semantics TEXT NN`, `availability_rule JSONB NN`: معنی زمان و قاعده دسترسی.
- `metadata JSONB NN`: جزئیات منبع.

### `external.observation` — Observation نسخه‌دار، پارتیشن Range روی `event_ts`

- کلید: `event_ts TIMESTAMPTZ NN PK/PARTITION`، `series_id BIGINT NN PK/FK`، `entity_key TEXT NN PK`، `available_at TIMESTAMPTZ NN PK`، `revision_no INTEGER NN PK`.
- `instrument_id BIGINT NULL FK`: لینک اختیاری ابزار.
- `system_available_at TIMESTAMPTZ NN`: دسترسی واقعی سامانه.
- دقیقاً یکی از `value_numeric NUMERIC`, `value_float DOUBLE`, `value_integer BIGINT`, `value_boolean BOOLEAN`, `value_text TEXT`, `value_json JSONB` مقدار دارد؛ یا همه با `is_missing=true` خالی‌اند.
- `is_missing BOOLEAN NN`, `quality_flags INTEGER NN`, `ingestion_batch_id BIGINT NN FK`, `recorded_at TIMESTAMPTZ NN`: کیفیت و Lineage.

### `external.document` — کدال/خبر/افشا

- `document_id BIGINT NN PK ID`, `feed_id BIGINT NN FK`, `external_id TEXT NN`: هویت؛ Feed/External/Revision یکتا.
- `instrument_id BIGINT NULL FK`, `document_type VARCHAR(32) NN`: Entity و نوع.
- `event_ts TIMESTAMPTZ NN`, `published_at TIMESTAMPTZ NULL`, `available_at/system_available_at TIMESTAMPTZ NN`: زمان موضوع/انتشار/دسترسی.
- `revision_no INTEGER NN`, `title TEXT NULL`, `source_uri/content_uri TEXT NULL`, `content_sha256 CHAR(64) NULL`: Revision و محتوای Content-addressed.
- `metadata JSONB NN`, `ingestion_batch_id BIGINT NN FK`: Metadata و Lineage.

## Backtesting و Execution Ledger

### تعریف Strategy و Run

- `backtest.strategy`: `strategy_id BIGINT PK ID`، `strategy_code VARCHAR(128) NN UQ`، `display_name TEXT NN`، `description/owner_name TEXT NULL`، `created_at TIMESTAMPTZ NN`.
- `backtest.strategy_version`: `strategy_version_id BIGINT PK ID`، `strategy_id BIGINT NN FK`، `version_no INTEGER NN`، `class_path TEXT NN`، `code_sha256 CHAR(64) NN`، `code_uri TEXT NULL`، `parameter_schema/default_parameters JSONB NN`، `created_at TIMESTAMPTZ NN`، `deprecated_at TIMESTAMPTZ NULL`.
- `backtest.run`: `run_id BIGINT PK ID`، `run_code VARCHAR(128) NN UQ`، FKهای `strategy_version_id/data_snapshot_id/universe_id/timeframe_id`، `base_currency_code VARCHAR(12) NN FK`، `event_from/event_to/knowledge_cutoff_ts TIMESTAMPTZ NN`، `availability_mode VARCHAR(24) NN`، `initial_capital NUMERIC NN`، `parameters JSONB NN`، `parameter_sha256 CHAR(64) NN`، `execution_model/transaction_cost_model JSONB NN`، `engine_version TEXT NN`، `random_seed BIGINT NN`، `status VARCHAR(16) NN`، `created_at/started_at/finished_at TIMESTAMPTZ`، `error_summary TEXT NULL`، `metadata JSONB NN`.
- `backtest.run_instrument`: `run_id BIGINT NN PK/FK`، `instrument_id BIGINT NN PK/FK`، `membership_valid_from/to TIMESTAMPTZ NULL`، `initial_weight DOUBLE NULL`؛ Universe دقیق Run را Freeze می‌کند.
- `backtest.run_market_series`: کلید `run_id/bar_series_id/series_role`؛ نقش Signal/Execution/Valuation/Benchmark، Primary اختیاری و `execution_lag` را ثبت می‌کند. Trigger ایجاد Run فقط Snapshot واقعاً Frozen و Cutoff/Mode منطبق را می‌پذیرد.
- `backtest.decision_context`: یک ارزیابی قطعی Strategy با `decision_seq`, `decision_ts`، Hash وضعیت و Manifest ورودی.
- `backtest.decision_bar_input`: Revision دقیق هر Bar مصرف‌شده؛ Trigger مقدار مؤثر Public/System را محاسبه و هر `available_at > decision_ts` یا فراتر از Snapshot Cutoff را Reject می‌کند.

### `backtest.signal`, `bt_order`, `fill`

- Signal: `signal_id BIGINT PK ID`، `run_id/instrument_id BIGINT NN FK`، `decision_context_id BIGINT NULL FK`، `signal_ts TIMESTAMPTZ NN`، `signal_type VARCHAR(16) NN`، `direction VARCHAR(8) NN`، `target_quantity/intended_entry_price/stop_loss_price/take_profit_price NUMERIC NULL`، `score DOUBLE NULL`، `reason_code TEXT NULL`، `payload JSONB NN`، `created_at TIMESTAMPTZ NN`.
- Order: `order_id BIGINT PK ID`، `run_id BIGINT NN FK`، `signal_id BIGINT NULL FK`، `instrument_id BIGINT NN FK`، `client_order_key TEXT NN`، `submitted_at TIMESTAMPTZ NN`، `valid_until TIMESTAMPTZ NULL`، `side/order_type/time_in_force/status VARCHAR NN`، `quantity NUMERIC NN >0`، `limit_price/stop_price NUMERIC NULL`، `reject_reason TEXT NULL`، `metadata JSONB NN`.
- Fill: `fill_id BIGINT PK ID`، `run_id/order_id/instrument_id BIGINT NN FK` با FK مرکب ضد اتصال بین Run/نماد، `fill_ts TIMESTAMPTZ NN`، `price/quantity NUMERIC NN`، هزینه‌ها، `execution_key TEXT NULL` با Unique جزئی برای Idempotency، `execution_reference JSONB NN` و `created_at`.
- `backtest.order_event`: رویدادهای Immutable چرخه سفارش از Submitted تا Filled/Cancelled/Rejected، دارای `event_seq`, `event_key`, `status_after` و لینک Typed به Fill. Constraint Deferred از Overfill و ناسازگاری Status/Fill جلوگیری می‌کند.
- `backtest.fill_market_reference`: منبع دقیق BAR/TICK/BOOK/QUOTE یا MODEL هر Fill؛ FKهای Typed و Trigger PIT تضمین می‌کنند Fill از داده آینده یا Revision متعلق به نماد دیگر ساخته نشود.

### معامله، Cash، Position و Equity

- `round_trip_trade`: `trade_id BIGINT PK ID`، `run_id/instrument_id BIGINT NN FK`، `entry_signal_id/exit_signal_id BIGINT NULL FK`، `direction VARCHAR(8) NN`، `entry_ts TIMESTAMPTZ NN`، `exit_ts TIMESTAMPTZ NULL`، `quantity/average_entry_price NUMERIC NN`، `average_exit_price/gross_pnl/net_pnl NUMERIC NULL`، چهار ستون هزینه `commission/slippage/tax/other_cost NUMERIC NN`، `return_fraction/MFE/MAE DOUBLE NULL`، `status VARCHAR(8) NN`.
- `trade_fill_allocation`: `trade_id BIGINT NN PK/FK`، `fill_id BIGINT NN PK/FK`، `leg_type VARCHAR(8) NN PK`، `allocated_quantity NUMERIC NN`؛ Partial Fill را به Leg ورود/خروج وصل می‌کند.
- `cash_ledger`: `cash_entry_id BIGINT PK ID`، `run_id BIGINT NN FK`، `entry_ts TIMESTAMPTZ NN`، `currency_code VARCHAR(12) NN FK`، `entry_type VARCHAR(24) NN`، `amount NUMERIC NN`، `fill_id/trade_id/corporate_action_id BIGINT NULL FK`، `fx_rate_to_base NUMERIC NULL`، `source_key TEXT NULL` با Unique جزئی، `metadata JSONB NN`.
- `position_ledger`: دفتر Signed تغییر تعداد با Source Key یکتا؛ Fill، Split، سود سهمی، حق‌تقدم، Expiry و Adjustment را ثبت می‌کند و بازسازی Position را مستقل از Snapshot ممکن می‌سازد.
- `position_snapshot`: PK مرکب `run_id/instrument_id/snapshot_ts`؛ سپس `quantity NUMERIC NN`، `average_cost/market_price/market_value_base NUMERIC NULL`، `realized_pnl_base/unrealized_pnl_base/margin_used_base NUMERIC NN`.
- `position_valuation_bar_reference`: FK یک‌به‌یک از Position Snapshot به Revision دقیق Bar و فیلد قیمت؛ Trigger، Instrument و Point-in-Time را کنترل می‌کند.
- `equity_point`: PK `run_id/event_ts`؛ `cash_base/equity_base NUMERIC NN`، `gross_exposure_base/net_exposure_base NUMERIC NN`، `drawdown_fraction DOUBLE NULL`.

### نتیجه و Metric

- `run_summary`: `run_id BIGINT PK/FK`؛ Ratioهای `total_return`, `annualized_return`, `annualized_volatility`, `sharpe_ratio`, `sortino_ratio`, `max_drawdown`, `win_rate`, `profit_factor`, `exposure_fraction`, `turnover_fraction DOUBLE NULL`؛ شمارنده‌های `trade_count/winning_trade_count/losing_trade_count BIGINT NN`؛ `gross_pnl_base/net_pnl_base/total_cost_base NUMERIC NULL`؛ `calculation_version TEXT NN`، `annualization_basis JSONB NN`، `calculated_at TIMESTAMPTZ NN`.
- `run_metric`: `run_metric_id BIGINT PK ID`، `run_id BIGINT NN FK`، `metric_name VARCHAR(96) NN`، `metric_version VARCHAR(32) NN`، `scope_key VARCHAR(160) NN`، `metric_value DOUBLE NN`، `unit_code VARCHAR(32) NULL`، `annualization_basis/dimensions JSONB NN`، `measured_at TIMESTAMPTZ NN`؛ Run/Name/Version/Scope یکتا.

## Feature Store، Label، Dataset و Experiment

### تعریف Feature و Feature Set

- `ml.feature_definition`: `feature_definition_id BIGINT PK ID`، `feature_key VARCHAR(160) NN`، `version_no INTEGER NN`، `display_name TEXT NN`، `description TEXT NULL`، `value_type VARCHAR(12) NN`، `entity_type VARCHAR(16) NN`، `event_time_semantics TEXT NN`، `availability_rule JSONB NN`، `lookback_bars INTEGER NULL`، `lookback_interval INTERVAL NULL`، `parameters JSONB NN`، `adjustment_policy VARCHAR(32) NN`، `code_uri TEXT NULL`، `code_sha256 CHAR(64) NN`، `status VARCHAR(12) NN`، `created_at TIMESTAMPTZ NN`، `frozen_at TIMESTAMPTZ NULL`. Key/Version یکتا و نسخه Frozen دارای `frozen_at` است.
- `ml.feature_set`: `feature_set_id BIGINT PK ID`، `feature_set_key VARCHAR(160) NN UQ`، `display_name TEXT NN`، `description TEXT NULL`، `created_at TIMESTAMPTZ NN`.
- `ml.feature_set_version`: `feature_set_version_id BIGINT PK ID`، `feature_set_id BIGINT NN FK`، `version_no INTEGER NN`، `timeframe_id SMALLINT NULL FK`، `schema_sha256 CHAR(64) NN`، `status VARCHAR(12) NN`، `created_at TIMESTAMPTZ NN`، `frozen_at TIMESTAMPTZ NULL`.
- `ml.feature_set_member`: PK `feature_set_version_id/feature_definition_id`؛ `ordinal INTEGER NN UQ per set`، `output_name VARCHAR(160) NN UQ per set`، `is_required BOOLEAN NN`، `deterministic_transform JSONB NN`. Transformهای Fit‌شونده مثل Scaler این‌جا نیستند و Artifact آموزش‌اند.

### Materialization و Feature Value/Vector

- `ml.feature_materialization_run`: `materialization_run_id BIGINT PK ID`، `feature_set_version_id/data_snapshot_id BIGINT NN FK`، `availability_mode VARCHAR(24) NN`، `event_from/event_to TIMESTAMPTZ NN`، `code_sha256/parameter_sha256 CHAR(64) NN`، `parameters JSONB NN`، `status VARCHAR(16) NN`، `started_at/finished_at TIMESTAMPTZ NULL`، `error_summary TEXT NULL`.
- `ml.feature_value` کلید/Partition: `event_ts TIMESTAMPTZ NN`، `feature_definition_id/instrument_id BIGINT NN`، `timeframe_id SMALLINT NN`، `available_at TIMESTAMPTZ NN`، `revision_no INTEGER NN`. زمان/Lineage: `system_available_at`, `window_end_ts`, `source_max_available_at`, `computed_at TIMESTAMPTZ NN`، `window_start_ts TIMESTAMPTZ NULL`، `materialization_run_id/data_snapshot_id BIGINT NN FK`، `row_sha256 CHAR(64) NN`. مقدار: دقیقاً یکی از `value_float DOUBLE`, `value_integer BIGINT`, `value_boolean BOOLEAN`, `value_text TEXT` یا همه خالی با `is_missing=true`؛ `missing_reason TEXT NULL`, `quality_flags INTEGER NN`.
- `ml.feature_vector` کلید/Partition: `event_ts`, `feature_set_version_id`, `instrument_id`, `timeframe_id`, `available_at`, `revision_no`. زمان‌ها: `system_available_at`, `window_start_ts`, `window_end_ts`, `source_max_available_at`, `computed_at TIMESTAMPTZ NN`. بردار: `feature_count SMALLINT NN`, `missing_count SMALLINT NN`, `values DOUBLE PRECISION[] NN`, `quality_flags INTEGER NN`; Lineage: `materialization_run_id/data_snapshot_id BIGINT NN FK`, `row_sha256 CHAR(64) NN`. `cardinality(values)=feature_count`.

در هر دو جدول Feature، `available_at >= max(window_end_ts, source_max_available_at)` و `system_available_at >= max(available_at, computed_at)` است. این قیود، Feature مشتق‌شده از اطلاعات آینده را Reject می‌کنند.

### تعریف و مقدار Label

- `ml.label_definition`: `label_definition_id BIGINT PK ID`، `label_key VARCHAR(160) NN`، `version_no INTEGER NN`، `display_name TEXT NN`، `description TEXT NULL`، `task_type VARCHAR(24) NN`، `value_type VARCHAR(12) NN`، دقیقاً یکی از `horizon_bars INTEGER NULL` یا `horizon_interval INTERVAL NULL`، `formula_parameters JSONB NN`، `adjustment_policy VARCHAR(32) NN`، `code_uri TEXT NULL`، `code_sha256 CHAR(64) NN`، `status VARCHAR(12) NN`، `created_at TIMESTAMPTZ NN`، `frozen_at TIMESTAMPTZ NULL`.
- `ml.label_materialization_run`: `label_run_id BIGINT PK ID`، `label_definition_id/data_snapshot_id BIGINT NN FK`، `event_from/event_to TIMESTAMPTZ NN`، `code_sha256/parameter_sha256 CHAR(64) NN`، `status VARCHAR(16) NN`، `started_at/finished_at TIMESTAMPTZ NULL`، `error_summary TEXT NULL`.
- `ml.label_value` کلید/Partition: `anchor_ts`, `label_definition_id`, `instrument_id`, `timeframe_id`, `available_at`, `revision_no`. زمان: `system_available_at`, `outcome_start_ts`, `outcome_end_ts`, `computed_at TIMESTAMPTZ NN`; `available_at >= outcome_end_ts`. مقدار: یکی از `value_float DOUBLE`, `value_integer BIGINT`, `value_text TEXT`, `value_json JSONB` یا همه خالی با `is_censored=true`; سپس `censor_reason TEXT NULL`, `quality_flags INTEGER NN`, `label_run_id/data_snapshot_id BIGINT NN FK`, `row_sha256 CHAR(64) NN`.

### Dataset و Freeze دقیق Window

- `ml.dataset`: `dataset_id BIGINT PK ID`، `dataset_key VARCHAR(160) NN UQ`، `display_name TEXT NN`، `description TEXT NULL`، `created_at TIMESTAMPTZ NN`.
- `ml.dataset_version`: `dataset_version_id BIGINT PK ID`، `dataset_id BIGINT NN FK`، `version_no INTEGER NN`، `task_mode VARCHAR(16) NN`، `feature_set_version_id BIGINT NN FK`، `label_definition_id/label_run_id BIGINT NULL FK`، `universe_id BIGINT NN FK`، `timeframe_id SMALLINT NN FK`، `data_snapshot_id BIGINT NN FK`، `event_from/event_to/knowledge_cutoff_ts TIMESTAMPTZ NN`، `availability_mode VARCHAR(24) NN`، `sequence_length/stride_bars INTEGER NN`، `decision_lag INTERVAL NN`، `sampling_policy/missing_value_policy/split_policy JSONB NN`، `manifest_uri TEXT NULL`، `manifest_sha256 CHAR(64) NULL`، `data_fingerprint CHAR(64) NN`، `status VARCHAR(16) NN`، `created_at TIMESTAMPTZ NN`، `frozen_at TIMESTAMPTZ NULL`. نسخه Supervised Label لازم دارد؛ نسخه Frozen Manifest Hash دارد.
- `ml.dataset_sample`: PK `dataset_version_id/sample_id`؛ `instrument_id BIGINT NN FK`, `timeframe_id SMALLINT NN FK`, `bar_series_id BIGINT NN FK`، `anchor_ts/prediction_ts/window_start_ts/window_end_ts TIMESTAMPTZ NN`، `expected_steps INTEGER NN`، مختصات Revision دقیق Label شامل `label_definition_id BIGINT`, `label_anchor_ts/label_available_at TIMESTAMPTZ`, `label_revision_no INTEGER` همگی NULL یا همگی مقدار، `sample_weight DOUBLE NN`, `sample_sha256 CHAR(64) NN`.
- `ml.dataset_sample_step`: PK `dataset_version_id/sample_id/step_no`؛ مختصات Bar دقیق `bar_series_id BIGINT`, `bar_open_ts/bar_available_at TIMESTAMPTZ`, `bar_revision_no INTEGER`؛ مختصات Feature دقیق `feature_event_ts TIMESTAMPTZ`, `feature_set_version_id/instrument_id BIGINT`, `timeframe_id SMALLINT`, `feature_available_at TIMESTAMPTZ`, `feature_revision_no INTEGER`؛ همه NN و دارای FK مرکب به Sample، Bar Revision و Feature Vector.
- `ml.dataset_split`: `split_id BIGINT PK ID`، `dataset_version_id BIGINT NN FK`، `fold_no/segment_no INTEGER NN`، `split_role VARCHAR(12) NN`، `event_from/event_to TIMESTAMPTZ NN`، `purge_bars/embargo_bars INTEGER NN`.
- `ml.dataset_sample_assignment`: PK/FK مرکب `dataset_version_id/sample_id/split_id`؛ عضویت دقیق هر Sample در Fold/Role را Freeze می‌کند.

### Experiment Tracking و Model Registry

- `ml.experiment`: `experiment_id BIGINT PK ID`، `experiment_key VARCHAR(160) NN UQ`، `objective TEXT NN`، `owner_name TEXT NULL`، `tags JSONB NN`، `created_at TIMESTAMPTZ NN`.
- `ml.training_run`: `training_run_id BIGINT PK ID`، `experiment_id/dataset_version_id BIGINT NN FK`، `fold_no INTEGER NULL`، `model_family TEXT NN`، `hyperparameters JSONB NN`، `random_seed BIGINT NN`، `code_sha256 CHAR(64) NN`، `container_digest TEXT NULL`، `dependency_lock_sha256 CHAR(64) NULL`، `hardware_info JSONB NN`، `deterministic_training BOOLEAN NN`، `status VARCHAR(16) NN`، `created_at TIMESTAMPTZ NN`، `started_at/finished_at TIMESTAMPTZ NULL`.
- `ml.training_metric`: `training_metric_id BIGINT PK ID`، `training_run_id BIGINT NN FK`، `split_role VARCHAR(12) NN`، `metric_name VARCHAR(96) NN`، `metric_value DOUBLE NN`، `epoch_no/step_no INTEGER NULL`، `dimensions JSONB NN`، `measured_at TIMESTAMPTZ NN`.
- `ml.model_artifact`: `artifact_id BIGINT PK ID`، `training_run_id BIGINT NN FK`، `artifact_type VARCHAR(24) NN`، `artifact_uri TEXT NN`، `artifact_sha256 CHAR(64) NN`، `metadata JSONB NN`، `created_at TIMESTAMPTZ NN`; Run/Type/Hash یکتا.

## View و Helper

- `market.bars_as_of(BIGINT,TIMESTAMPTZ,TIMESTAMPTZ,TIMESTAMPTZ,VARCHAR)`: API رسمی تاریخی برای یک `bar_series_id` و بازه `[from,to)`؛ در `PUBLIC_REPLAY` از `available_at` و در `ACTUAL_SYSTEM_REPLAY` از `system_available_at` استفاده می‌کند، فقط Bar نهاییِ تکمیل‌شده تا cutoff را می‌پذیرد و یک Revision قطعی را به‌ترتیب زمانی برمی‌گرداند. ستون `effective_available_at` زمان Availability مؤثر Mode انتخابی است.
- `market.current_bar`: آخرین Revision عمومی هر `(bar_series_id, bar_open_ts)` بدون cutoff و بدون الزام `is_final`؛ فقط برای Current-state عملیاتی است و برای Backtest تاریخی، تولید Feature به‌صورت PIT یا Dataset تاریخی ML ناامن است. جست‌وجوی Repository پیش از `0003` مصرف‌کننده اجرایی برای این View پیدا نکرد.
- `market.create_bar_month_partition(month, hash_buckets)`: Partition ماهانه UTC می‌سازد؛ مقدار ۰ یعنی فقط Range و ۲ تا ۶۴ یعنی Hash Subpartition بر اساس `bar_series_id`.
- `market.create_technical_month_partitions(month, hash_buckets)`: Partition همان ماه را برای Bar، Tape، Snapshot/Level/Delta دفتر سفارش، Quote و حقیقی/حقوقی یکجا می‌سازد.

ورودی NULL تابع `bars_as_of` با `22004`، Range/Mode/Cutoff نامعتبر یا Adjustment آینده‌دان با `22023` و Series ناشناخته با `P0002` رد می‌شود. برای Replay ترتیبی، cutoff هر فراخوانی باید `LEAST(decision_ts, snapshot.knowledge_cutoff_ts)` باشد؛ یک cutoff انتهای Run ممکن است Correctionهای دیرهنگام را برای تصمیم‌های قدیمی آشکار کند.
