# ورود تقویم معاملاتی (PR-06)

فایل JSON تقویم نسخه‌دار، کامل و صریح است. همهٔ تاریخ‌های بازهٔ بستهٔ `date_from..date_to` باید دقیقاً یک‌بار حضور داشته باشند. تعطیلات از weekday یا state بازار حدس زده نمی‌شوند. فقط `REGULAR` پشتیبانی می‌شود؛ روز باز باید ساعت شروع/پایان داشته باشد و روز بسته هر دو مقدار را `null` نگه دارد.

زمان محلی با timezone IANA نوشته‌شده در manifest و `zoneinfo` به UTC تبدیل می‌شود؛ offset ثابت تهران استفاده نمی‌شود. زمان nonexistent و زمان ambiguous بدون `fold` صریح رد می‌شوند. timezone فایل باید با timezone venue برابر باشد. session موجود فقط هنگامی unchanged است که همهٔ مقادیر برابر باشند؛ conflict overwrite نمی‌شود و ورود canonical اتمیک است.

```bash
uv run --frozen bisfin calendar validate --file tests/fixtures/calendar/tse_regular_success.json
uv run --frozen bisfin calendar import --file tests/fixtures/calendar/tse_regular_success.json
```

هر روز یک raw audit event با کلید `bisfin|calendar|<calendar_id>|<venue>|REGULAR|<date>` می‌گیرد. تقویم آنلاین، محاسبهٔ تعطیلات و اصلاح خودکار ساعت جلسه در محدودهٔ این PR نیست.

## مرز تراکنش durable

Validation فایل پیش از batch است. A) batch `RUNNING` commit می‌شود؛ B) همهٔ raw calendar eventها commit می‌شوند؛ C) sessionهای canonical در یک تراکنش اتمیک نوشته می‌شوند؛ و D) conflict در تراکنش جدا batch را `FAILED` می‌کند. پس conflict هیچ روز جدیدی را باقی نمی‌گذارد، اما raw audit و روز conflict قبلی حفظ می‌شوند.
