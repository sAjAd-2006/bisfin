# راهنمای جامع و ساختاریافته APIهای BrsApi

## معرفی کلی

BrsApi سرویسی برای ارائه داده‌های مالی و بورسی به‌صورت لحظه‌ای و تاریخی است. تمام APIها با متد **GET** کار می‌کنند و خروجی را در قالب **JSON** برمی‌گردانند. برای استفاده از هر API، به یک `API Key` معتبر نیاز دارید که از طریق ثبت‌نام در پنل کاربری قابل دریافت است.

**آدرس پایه:** `https://Api.BrsApi.ir/`

پارامترهای مشترک در تمام APIها:
- `key` (ضروری): کلید API اختصاصی شما.

---

## 1. API آمار معاملات فیزیکی بورس کالا (IME Physical)

**Endpoint:** `/IME/Physical.php`
**توضیح:** دریافت اطلاعات معاملات فیزیکی بورس کالا در بازه تاریخی مشخص.

**پارامترها:**

| نام | ضرورت | توضیح | مثال |
|-----|--------|-------|------|
| key | ضروری | کلید API | `YOUR_API_KEY` |
| date_start | اختیاری | تاریخ شروع (فرمت: YYYY-MM-DD شمسی) | `1404-03-18` |
| date_end | اختیاری | تاریخ پایان (فرمت: YYYY-MM-DD شمسی) | `1404-03-18` |

**نمونه درخواست:**
```
https://Api.BrsApi.ir/IME/Physical.php?key=YOUR_API_KEY&date_start=1404-03-18&date_end=1404-03-18
```

**ساختار خروجی:**

| فیلد | توضیح | نوع | مثال |
|------|-------|------|------|
| l18 | نماد | string | `MSC-HSR00000-00` |
| l30 | نام کالا | string | `ورق گرم نوردکاران` |
| id_category | شناسه گروه | string | `1-1-2580` |
| code_offer | کد عرضه | string | `952542` |
| market_hall | تالار | string | `تالار صنعتی` |
| producer | تولیدکننده | string | `فولاد مبارکه اصفهان` |
| supplier | عرضه‌کننده | string | `فولاد مبارکه اصفهان` |
| broker | کارگزار | string | `باهنر` |
| type_contract | نوع قرارداد | string | `سلف` |
| type_settlement | نوع تسویه | string | `نقدی / اعتباری` |
| date_price_settlement | تاریخ قیمت تسویه | string | `2025/02/09` |
| date_delivery | تاریخ تحویل | string | `1404/02/31` |
| location_delivery | مکان تحویل | string | `انبار کارخانه` |
| unit | واحد | string | `تن` |
| type_packaging | نوع بسته‌بندی | string | `سایر` |
| currency | نوع ارز | string | `ریال` |
| method_offer | نحوه عرضه | string | `عمده` |
| method_purchase | روش خرید | string | `عادی` |
| date_trade | تاریخ معامله | string | `1403/12/11` |
| price_base_offer | قیمت پایه عرضه | integer | `362870` |
| volume_contract | حجم قرارداد | integer | `49500` |
| volume_offer | حجم عرضه | integer | `60060` |
| demand | تقاضا | integer | `49500` |
| pmin | پایین‌ترین قیمت | integer | `362870` |
| pmax | بالاترین قیمت | integer | `362870` |
| pl | قیمت پایانی | integer | `362870` |
| pc | قیمت پایانی میانگین موزون | integer | `362870` |
| tval | ارزش معامله (هزارریال) | integer | `17962065000` |

**نکته:** اگر بازه تاریخی تعطیل باشد و معامله‌ای وجود نداشته باشد، خروجی به‌صورت `{"code_http":200,"successful":true,"status":"no_data","message_error":null}` برمی‌گردد.

---

## 2. API بازار آپشن بورس (TSETMC Option)

**Endpoint:** `/Tsetmc/Option.php`
**توضیح:** دریافت اطلاعات لحظه‌ای قراردادهای اختیار معامله (آپشن) از بورس تهران.

**پارامترها:**

| نام | ضرورت | توضیح | مثال |
|-----|--------|-------|------|
| key | ضروری | کلید API | `YOUR_API_KEY` |

**نمونه درخواست:**
```
https://Api.BrsApi.ir/Tsetmc/Option.php?key=YOUR_API_KEY
```

**ساختار خروجی (فیلدهای کلیدی):**

| فیلد | توضیح | نوع | مثال |
|------|-------|------|------|
| time | زمان آخرین اطلاعات | string | `12:30:01` |
| base_l18 | نماد پایه | string | `اهرم` |
| l18 | نماد آپشن | string | `ضهرم9003` |
| l30 | نام | string | `اختیارخ اهرم-16000-1403/09/28` |
| isin | شناسه بین‌المللی | string | `IRO9AHRM2241` |
| type | نوع (call/put) | string | `call` |
| date_begin | تاریخ شروع | string | `1403-04-30` |
| date_end | تاریخ پایان | string | `1403-09-28` |
| day_remain | روزهای باقیمانده تا سررسید | integer | `51` |
| size_contract | اندازه قرارداد | integer | `1000` |
| price_strike | قیمت اعمال | integer | `16000` |
| interest_open | موقعیت‌های باز | integer | `454648` |
| base_py | قیمت پایانی دیروز نماد پایه | integer | `15240` |
| base_pl | آخرین قیمت نماد پایه | integer | `15600` |
| py | قیمت پایانی دیروز آپشن | integer | `1236` |
| pl | آخرین قیمت آپشن | integer | `1066` |
| pc | قیمت پایانی آپشن | integer | `1028` |
| tno | تعداد معاملات | integer | `5574` |
| tvol | حجم معاملات | integer | `437931` |
| tval | ارزش معاملات | integer | `450041749000` |
| Buy_CountI | تعداد خریدار حقیقی | integer | `540` |
| Buy_CountN | تعداد خریدار حقوقی | integer | `6` |
| Sell_CountI | تعداد فروشنده حقیقی | integer | `492` |
| Sell_CountN | تعداد فروشنده حقوقی | integer | `14` |
| Buy_I_Volume | حجم خرید حقیقی | integer | `413270` |
| Buy_N_Volume | حجم خرید حقوقی | integer | `24661` |
| zd1..zd5 | تعداد خریدار در سطرهای ۱ تا ۵ | integer | `8` |
| qd1..qd5 | حجم خرید در سطرهای ۱ تا ۵ | integer | `5971` |
| pd1..pd5 | قیمت خرید در سطرهای ۱ تا ۵ | integer | `1061` |
| zo1..zo5 | تعداد فروشنده در سطرهای ۱ تا ۵ | integer | `1` |
| qo1..qo5 | حجم فروش در سطرهای ۱ تا ۵ | integer | `195` |
| po1..po5 | قیمت فروش در سطرهای ۱ تا ۵ | integer | `1066` |

---

## 3. API بازار آپشن بورس کالا (IME Option)

**Endpoint:** `/IME/Option.php`
**توضیح:** دریافت اطلاعات لحظه‌ای قراردادهای اختیار معامله (آپشن) از بورس کالا.

**پارامترها:**

| نام | ضرورت | توضیح | مثال |
|-----|--------|-------|------|
| key | ضروری | کلید API | `YOUR_API_KEY` |

**نمونه درخواست:**
```
https://Api.BrsApi.ir/IME/Option.php?key=YOUR_API_KEY
```

**ساختار خروجی (فیلدهای کلیدی برای هر دو نوع اختیار خرید و فروش):**

| فیلد | توضیح | نوع | مثال |
|------|-------|------|------|
| date_update | تاریخ آخرین دیتا | string | `1404-07-02` |
| time_update | زمان آخرین دیتا | string | `16:25:28` |
| time | زمان آخرین تغییرات | string | `16:15:31` |
| contract_category | گروه قرارداد | string | `AB04 تاریخ سررسید: 1404/08/18` |
| price_strike | قیمت اعمال | integer | `8000000` |
| call_contract_code | کد قرارداد اختیار خرید | string | `GBAB04C800` |
| call_contract_description | توضیح اختیار خرید | string | `قرارداد اختیار معامله خرید شمش طلا ...` |
| call_date_end | تاریخ سررسید اختیار خرید | string | `1404-08-18` |
| call_day_remain | روزهای باقیمانده تا سررسید اختیار خرید | integer | `46` |
| call_interest_open | موقعیت‌های باز اختیار خرید | integer | `42677` |
| call_py | قیمت آخرین تسویه اختیار خرید | integer | `5143000` |
| call_pl | آخرین قیمت اختیار خرید | integer | `5605000` |
| call_tno | تعداد معاملات اختیار خرید | integer | `1` |
| call_tvol | حجم معاملات اختیار خرید | integer | `1` |
| call_tval | ارزش معاملات اختیار خرید (هزار ریال) | integer | `5605` |
| call_qd1..3 | حجم خرید سطر اول تا سوم اختیار خرید | integer | `18` |
| call_pd1..3 | قیمت خرید سطر اول تا سوم اختیار خرید | integer | `5100001` |
| call_qo1..3 | حجم فروش سطر اول تا سوم اختیار خرید | integer | `1` |
| call_po1..3 | قیمت فروش سطر اول تا سوم اختیار خرید | integer | `5687999` |
| put_contract_code | کد قرارداد اختیار فروش | string | `GBAB04P800` |
| put_contract_description | توضیح اختیار فروش | string | `قرارداد اختیار معامله فروش شمش طلا ...` |
| put_date_end | تاریخ سررسید اختیار فروش | string | `1404-08-18` |
| put_day_remain | روزهای باقیمانده تا سررسید اختیار فروش | integer | `46` |
| put_interest_open | موقعیت‌های باز اختیار فروش | integer | `null` |
| put_py | قیمت آخرین تسویه اختیار فروش | integer | `100` |
| put_pl | آخرین قیمت اختیار فروش | integer | `null` |
| put_tno | تعداد معاملات اختیار فروش | integer | `0` |
| put_qd1..3 | حجم خرید سطر اول تا سوم اختیار فروش | integer | `1` |
| put_qo1..3 | حجم فروش سطر اول تا سوم اختیار فروش | integer | `null` |

---

## 4. API بازار آتی بورس کالا (IME Futures)

**Endpoint:** `/IME/Futures.php`
**توضیح:** دریافت اطلاعات لحظه‌ای قراردادهای آتی از بورس کالا.

**پارامترها:**

| نام | ضرورت | توضیح | مثال |
|-----|--------|-------|------|
| key | ضروری | کلید API | `YOUR_API_KEY` |

**نمونه درخواست:**
```
https://Api.BrsApi.ir/IME/Futures.php?key=YOUR_API_KEY
```

**ساختار خروجی (فیلدهای کلیدی):**

| فیلد | توضیح | نوع | مثال |
|------|-------|------|------|
| date_update | تاریخ آخرین دیتا | string | `1404-07-02` |
| time_update | زمان آخرین دیتا | string | `16:09:27` |
| time | زمان آخرین تغییرات | string | `16:09:24` |
| contract_code | کد قرارداد | string | `ETCME04` |
| contract_description | توضیح قرارداد | string | `قرارداد آتی صندوق طلای لوتوس تحویل مهر ماه 1404` |
| contract_size | اندازه قرارداد | integer | `1000` |
| contract_size_unit | واحد اندازه قرارداد | string | `واحد` |
| contract_currency | ارز قرارداد | string | `ریال` |
| date_end | تاریخ سررسید | string | `1404-07-22` |
| day_remain | روزهای باقیمانده تا سررسید | integer | `20` |
| margin_initial | وجه تضمین اولیه | integer | `184000000` |
| margin_maintenance | حداقل وجه تضمین | integer | `128800000` |
| interest_open | موقعیت‌های باز | integer | `1096` |
| py | قیمت تسویه روزانه | integer | `727979` |
| pf | اولین قیمت | integer | `761500` |
| pmax | بالاترین قیمت | integer | `761500` |
| pmin | پایین‌ترین قیمت | integer | `750600` |
| pl | آخرین قیمت | integer | `750700` |
| pls | قیمت تسویه لحظه‌ای | float | `752261.2717` |
| tno | تعداد معاملات | integer | `173` |
| tvol | حجم معاملات | integer | `288` |
| tval | ارزش معاملات (هزار ریال) | integer | `217197300` |
| Buy_CountI | تعداد خریدار حقیقی | integer | `44` |
| Buy_CountN | تعداد خریدار حقوقی | integer | `0` |
| Sell_CountI | تعداد فروشنده حقیقی | integer | `32` |
| Sell_CountN | تعداد فروشنده حقوقی | integer | `0` |
| qd1..3 | حجم خرید سطر ۱ تا ۳ | integer | `7` |
| pd1..3 | قیمت خرید سطر ۱ تا ۳ | integer | `750700` |
| qo1..3 | حجم فروش سطر ۱ تا ۳ | integer | `1` |
| po1..3 | قیمت فروش سطر ۱ تا ۳ | integer | `753300` |

---

## 5. API دیتای تاریخی بورس (TSETMC History)

این API دو نوع خروجی متفاوت دارد که با پارامتر `type` مشخص می‌شود.

**Endpoint:** `/Tsetmc/History.php`

**پارامترهای مشترک:**

| نام | ضرورت | توضیح | مثال |
|-----|--------|-------|------|
| key | ضروری | کلید API | `YOUR_API_KEY` |
| l18 | ضروری | نماد | `فملی` |

### 5-1. دریافت دیتای معاملات و قیمت (type=0)

**پارامتر اضافی:**

| نام | ضرورت | توضیح | مثال |
|-----|--------|-------|------|
| type | ضروری | `0` برای دیتای قیمت و معاملات | `0` |

**نمونه درخواست:**
```
https://Api.BrsApi.ir/Tsetmc/History.php?key=YOUR_API_KEY&type=0&l18=فملی
```

**ساختار خروجی:**

| فیلد | توضیح | نوع | مثال |
|------|-------|------|------|
| date | تاریخ | string | `1403-08-08` |
| time | زمان | string | `12:30:00` |
| tno | تعداد معاملات | integer | `5953` |
| tvol | حجم معاملات | integer | `174743191` |
| tval | ارزش معاملات | integer | `791481204551` |
| pmin | کمترین قیمت | integer | `4470` |
| pmax | بیشترین قیمت | integer | `4594` |
| py | قیمت پایانی دیروز | integer | `4437` |
| pf | اولین قیمت | integer | `4500` |
| pl | آخرین قیمت | integer | `4499` |
| plc | تغییر آخرین قیمت | integer | `62` |
| plp | درصد تغییر آخرین قیمت | float | `1.4` |
| pc | قیمت پایانی | integer | `4529` |
| pcc | تغییر قیمت پایانی | integer | `92` |
| pcp | درصد تغییر قیمت پایانی | float | `2.07` |

### 5-2. دریافت دیتای حقیقی و حقوقی (type=1)

**پارامتر اضافی:**

| نام | ضرورت | توضیح | مثال |
|-----|--------|-------|------|
| type | ضروری | `1` برای دیتای حقیقی/حقوقی | `1` |

**نمونه درخواست:**
```
https://Api.BrsApi.ir/Tsetmc/History.php?key=YOUR_API_KEY&type=1&l18=فملی
```

**ساختار خروجی:**

| فیلد | توضیح | نوع | مثال |
|------|-------|------|------|
| date | تاریخ | string | `1403-08-08` |
| Buy_CountI | تعداد خریدار حقیقی | integer | `2068` |
| Buy_CountN | تعداد خریدار حقوقی | integer | `19` |
| Sell_CountI | تعداد فروشنده حقیقی | integer | `1473` |
| Sell_CountN | تعداد فروشنده حقوقی | integer | `17` |
| Buy_I_Volume | حجم خرید حقیقی | integer | `79282870` |
| Buy_N_Volume | حجم خرید حقوقی | integer | `13401508` |
| Sell_I_Volume | حجم فروش حقیقی | integer | `86335381` |
| Sell_N_Volume | حجم فروش حقوقی | integer | `6348997` |
| Buy_I_Value | ارزش خرید حقیقی | integer | `1243680035710` |
| Buy_N_Value | ارزش خرید حقوقی | integer | `210225862190` |
| Sell_I_Value | ارزش فروش حقیقی | integer | `1354306054970` |
| Sell_N_Value | ارزش فروش حقوقی | integer | `99599842930` |

---

## 6. API دیتای جامع نماد بورسی (TSETMC Symbol)

**Endpoint:** `/Tsetmc/Symbol.php`
**توضیح:** دریافت اطلاعات جامع و لحظه‌ای یک نماد بورسی شامل قیمت، معاملات، حقیقی/حقوقی، عرضه/تقاضا، و اطلاعات مجامع.

**پارامترها:**

| نام | ضرورت | توضیح | مثال |
|-----|--------|-------|------|
| key | ضروری | کلید API | `YOUR_API_KEY` |
| l18 | ضروری | نماد | `خودرو` |

**نمونه درخواست:**
```
https://Api.BrsApi.ir/Tsetmc/Symbol.php?key=YOUR_API_KEY&l18=خودرو
```

**ساختار خروجی (فیلدهای کلیدی):**

| فیلد | توضیح | نوع | مثال |
|------|-------|------|------|
| date_update | تاریخ آخرین معامله | string | `1403-12-22` |
| time | زمان آخرین اطلاعات | string | `15:53:06` |
| state | وضعیت | string | `مجاز` |
| l18 | نماد | string | `خودرو` |
| l30 | نام شرکت | string | `ایران خودرو` |
| l30_en | نام لاتین | string | `Iran Khodro` |
| isin | شناسه بین‌المللی | string | `IRO1IKCO0001` |
| m | بازار | string | `بورس` |
| m_board | تابلو | string | `بازار دوم (تابلوی فرعی) بورس` |
| cs | گروه صنعت | string | `خودرو و ساخت قطعات` |
| z | تعداد سهام | integer | `301656068000` |
| bvol | حجم مبنا | integer | `30310685` |
| mv | ارزش بازار | integer | `1165599046752000` |
| ff | درصد سهام شناور | integer | `33` |
| eps | EPS | integer | `-784` |
| pe | P/E | float | `-4.93` |
| tmin | آستانه مجاز پایین | integer | `3863` |
| tmax | آستانه مجاز بالا | integer | `4101` |
| pmin | کمترین قیمت | integer | `3863` |
| pmax | بیشترین قیمت | integer | `3910` |
| py | قیمت پایانی دیروز | integer | `3982` |
| pf | اولین قیمت | integer | `3863` |
| pl | آخرین قیمت | integer | `3863` |
| pc | قیمت پایانی | integer | `3864` |
| tno | تعداد معاملات | integer | `22311` |
| tvol | حجم معاملات | integer | `1521006030` |
| tval | ارزش معاملات | integer | `5877854479318` |
| Buy_CountI | تعداد خریدار حقیقی | integer | `7279` |
| Buy_CountN | تعداد خریدار حقوقی | integer | `30` |
| Sell_CountI | تعداد فروشنده حقیقی | integer | `3620` |
| Sell_CountN | تعداد فروشنده حقوقی | integer | `37` |
| Buy_I_Volume | حجم خرید حقیقی | integer | `858040544` |
| Buy_N_Volume | حجم خرید حقوقی | integer | `662965486` |
| Sell_I_Volume | حجم فروش حقیقی | integer | `1274483625` |
| Sell_N_Volume | حجم فروش حقوقی | integer | `246522405` |
| zd1..zd5 | تعداد خریدار سطرهای ۱ تا ۵ | integer | `6` |
| qd1..qd5 | حجم خرید سطرهای ۱ تا ۵ | integer | `444690` |
| pd1..pd5 | قیمت خرید سطرهای ۱ تا ۵ | integer | `4497` |
| zo1..zo5 | تعداد فروشنده سطرهای ۱ تا ۵ | integer | `2` |
| qo1..qo5 | حجم فروش سطرهای ۱ تا ۵ | integer | `8000` |
| po1..po5 | قیمت فروش سطرهای ۱ تا ۵ | integer | `4499` |
| assembly[] | آرایه اطلاعات مجامع | array | - |
| assembly[].title | عنوان اطلاعیه مجمع | string | `آگهی دعوت به مجمع عمومی فوق العاده ...` |
| assembly[].date_title | تاریخ عنوان | string | `1403/12/30` |
| assembly[].date_send | تاریخ ارسال | string | `1403/12/16` |
| assembly[].time_send | زمان ارسال | string | `09:04:12` |
| assembly[].date_publish | تاریخ انتشار | string | `1403/12/16` |
| assembly[].time_publish | زمان انتشار | string | `09:04:12` |
| assembly[].content | محتوای اطلاعیه | string | `زمان برگزاری: 1403/12/28 ساعت 09:00...` |

---

## 7. API رایگان ارز دیجیتال (Cryptocurrency)

**Endpoint:** `/Market/Cryptocurrency.php`
**توضیح:** دریافت قیمت لحظه‌ای بیش از ۳۰۰۰ ارز دیجیتال.

**پارامترها:**

| نام | ضرورت | توضیح | مثال |
|-----|--------|-------|------|
| key | ضروری | کلید API | `YOUR_API_KEY` |

**نمونه درخواست:**
```
https://Api.BrsApi.ir/Market/Cryptocurrency.php?key=YOUR_API_KEY
```

**ساختار خروجی:**

| فیلد | توضیح | نوع | مثال |
|------|-------|------|------|
| date | تاریخ شمسی | string | `1404/01/16` |
| time | زمان | string | `05:41` |
| time_unix | زمان یونیکس | integer | `1743819060` |
| name | نام رمزارز | string | `Bitcoin` |
| price | قیمت به دلار | float | `83937` |
| price_toman | قیمت به تومان | integer | `8651798340` |
| change_percent | درصد تغییر قیمت | float | `1.16` |
| market_cap | ارزش بازار | integer | `1665878484320` |
| link_icon | لینک آیکون | string | `https://s2.coinmarketcap.com/static/img/coins/64x64/1.png` |

---

## 8. API رایگان شاخص بورس (TSETMC Index)

**Endpoint:** `/Tsetmc/Index.php`
**توضیح:** دریافت لحظه‌ای شاخص‌های بورس، فرابورس و شاخص‌های منتخب.

**پارامترها:**

| نام | ضرورت | توضیح | مثال |
|-----|--------|-------|------|
| key | ضروری | کلید API | `YOUR_API_KEY` |
| type | ضروری | `1`=شاخص بورس، `2`=شاخص فرابورس، `3`=شاخص‌های منتخب | `1` |

**نمونه درخواست:**
```
https://Api.BrsApi.ir/Tsetmc/Index.php?key=YOUR_API_KEY&type=1
```

**ساختار خروجی:**

| فیلد | توضیح | نوع | مثال |
|------|-------|------|------|
| date | تاریخ | string | `1403-12-18` |
| time | زمان | string | `20:07:44` |
| name | نام شاخص (در نوع ۳) | string | `شاخص كل` |
| state | وضعیت بازار | string | `بسته` |
| index | مقدار شاخص (برای نوع ۱) | float | `2756970.28` |
| index_change | تغییر شاخص | float | `-33000.22` |
| min | کمترین مقدار (نوع ۳) | float | `2754412.05` |
| max | بیشترین مقدار (نوع ۳) | float | `2791846.39` |
| index_change_percent | درصد تغییر (نوع ۳) | float | `-1.18` |
| index_equalWeight | شاخص هم‌وزن (نوع ۱) | float | `814270.85` |
| index_equalWeight_change | تغییر شاخص هم‌وزن | float | `-9859.51` |
| mv | ارزش بازار | integer | `87748425774158880` |
| mv_main | ارزش بازار اول و دوم (فرابورس) | integer | `14325896686507018` |
| mv_base | ارزش بازار پایه (فرابورس) | integer | `3195854479116770` |
| tno | تعداد معاملات | integer | `493959` |
| tvol | حجم معاملات | integer | `16326463426` |
| tval | ارزش معاملات | integer | `134757329520715` |

---

## 9. API رایگان طلا و ارز (Gold & Currency)

این API دو نسخه دارد: **رایگان** و **حرفه‌ای (Pro)**.

### 9-1. نسخه رایگان

**Endpoint:** `/Market/Gold_Currency.php`

**پارامترها:**

| نام | ضرورت | توضیح | مثال |
|-----|--------|-------|------|
| key | ضروری | کلید API | `YOUR_API_KEY` |

**نمونه درخواست:**
```
https://Api.BrsApi.ir/Market/Gold_Currency.php?key=YOUR_API_KEY
```

**ساختار خروجی:**

| فیلد | توضیح | نوع | مثال |
|------|-------|------|------|
| date | تاریخ | string | `1403/12/28` |
| time | زمان | string | `19:58` |
| time_unix | زمان یونیکس | integer | `1742315280` |
| symbol | نماد | string | `IR_COIN_EMAMI` |
| name_en | نام انگلیسی | string | `Emami Coin` |
| name | نام فارسی | string | `سکه امامی` |
| price | قیمت | integer | `97005000` |
| change_value | مقدار تغییر قیمت | integer | `6964959` |
| change_percent | درصد تغییر قیمت | float | `7.18` |
| unit | واحد قیمت | string | `تومان` |

### 9-2. نسخه حرفه‌ای (Pro)

**Endpoint:** `/Market/Gold_Currency_Pro.php`

**پارامترها:**

| نام | ضرورت | توضیح | مثال |
|-----|--------|-------|------|
| key | ضروری | کلید API | `YOUR_API_KEY` |
| section | اختیاری | `gold`، `currency`، `cryptocurrency` (با کاما جدا می‌شوند) | `gold,currency` |

**نمونه درخواست (قیمت لحظه‌ای):**
```
https://Api.BrsApi.ir/Market/Gold_Currency_Pro.php?key=YOUR_API_KEY&section=gold,currency
```

**ساختار خروجی لحظه‌ای Pro:**

| فیلد | توضیح | نوع | مثال |
|------|-------|------|------|
| successful | وضعیت موفقیت | boolean | `true` |
| code_http | کد HTTP | integer | `200` |
| message_error | پیام خطا | null | `null` |
| url_base_icon | آدرس پایه آیکون | string | `https://s1.BrsApi.ir/.../Icon` |
| date | تاریخ | string | `1404/02/14` |
| time | زمان | string | `19:02` |
| time_unix | زمان یونیکس | integer | `1746372720` |
| symbol | نماد | string | `USD` |
| name_en | نام انگلیسی | string | `US Dollar` |
| name | نام فارسی | string | `دلار آمریکا` |
| sign | نماد گرافیکی | string | `$` |
| price | قیمت | integer | `859000` |
| change_value | مقدار تغییر قیمت | integer | `7000` |
| change_percent | درصد تغییر قیمت | float | `0.82` |
| unit | واحد قیمت | string | `ریال` |
| path_icon | نام فایل آیکون | string | `USD.png` |

**دریافت تاریخچه ۲۴ ساعته:**

پارامترهای اضافی:
- `history=1` (فعال‌سازی حالت ۲۴ ساعته)
- `symbol=USD` (نماد مورد نظر)

```
https://Api.BrsApi.ir/Market/Gold_Currency_Pro.php?key=YourApiKey&history=1&symbol=USD
```

**دریافت تاریخچه روزانه:**

پارامترهای اضافی:
- `history=2`
- `symbol=USD`
- `date_start=1404-01-01` (اختیاری، فرمت شمسی YYYY-MM-DD)
- `date_end=1404-02-02` (اختیاری)

```
https://Api.BrsApi.ir/Market/Gold_Currency_Pro.php?key=YourApiKey&history=2&symbol=USD&date_start=1404-01-01&date_end=1404-02-02
```

**ساختار خروجی تاریخچه روزانه:**

| فیلد | توضیح | نوع | مثال |
|------|-------|------|------|
| symbol | نماد | string | `USD` |
| name | نام | string | `دلار آمریکا` |
| sign | نماد گرافیکی | string | `$` |
| unit | واحد قیمت | string | `ریال` |
| url_base_icon | آدرس پایه آیکون | string | `...` |
| path_icon | نام فایل آیکون | string | `USD.png` |
| date | تاریخ | string | `1404/02/05` |
| open | قیمت اول | integer | `796500` |
| high | بالاترین قیمت | integer | `797000` |
| low | پایین‌ترین قیمت | integer | `795500` |
| close | قیمت آخر | integer | `796000` |

---

## 10. API رایگان کامودیتی (Commodity)

**Endpoint:** `/Market/Commodity.php`
**توضیح:** دریافت قیمت لحظه‌ای فلزات گرانبها، فلزات اساسی و انرژی.

**پارامترها:**

| نام | ضرورت | توضیح | مثال |
|-----|--------|-------|------|
| key | ضروری | کلید API | `YOUR_API_KEY` |

**نمونه درخواست:**
```
https://Api.BrsApi.ir/Market/Commodity.php?key=YOUR_API_KEY
```

**ساختار خروجی:**

| فیلد | توضیح | نوع | مثال |
|------|-------|------|------|
| date | تاریخ | string | `1404/04/31` |
| time | زمان | string | `16:01` |
| time_unix | زمان یونیکس | integer | `1753187460` |
| symbol | نماد | string | `XAGUSD` |
| name | نام | string | `انس نقره` |
| price | قیمت | float | `38.98` |
| change_value | مقدار تغییر قیمت | float | `0.03` |
| change_percent | درصد تغییر قیمت | float | `0.07` |
| unit | واحد قیمت | string | `دلار` |

---

## 11. API ریزمعاملات بورس (TSETMC Transaction)

**Endpoint:** `/Tsetmc/Transaction.php`
**توضیح:** دریافت لیست ریزمعاملات یک نماد در یک تاریخ مشخص.

**پارامترها:**

| نام | ضرورت | توضیح | مثال |
|-----|--------|-------|------|
| key | ضروری | کلید API | `YOUR_API_KEY` |
| l18 | ضروری | نماد | `اهرم` |
| date | اختیاری | تاریخ (فرمت شمسی YYYY-MM-DD)؛ پیش‌فرض: آخرین روز معاملاتی | `1404-02-22` |

**نمونه درخواست:**
```
https://Api.BrsApi.ir/Tsetmc/Transaction.php?key=YOUR_API_KEY&l18=اهرم&date=1404-02-22
```

**ساختار خروجی:**

| فیلد | توضیح | نوع | مثال |
|------|-------|------|------|
| row | ردیف | integer | `1` |
| time | زمان | string | `09:01:03` |
| volume | حجم | integer | `250194` |
| price | قیمت | integer | `17820` |
| canceled | ابطال معامله | integer | `0` |

---

## 12. API سهامداران بورس (TSETMC Shareholder)

**Endpoint:** `/Tsetmc/Shareholder.php`
**توضیح:** دریافت اطلاعات سهامداران عمده یک نماد در یک تاریخ مشخص.

**پارامترها:**

| نام | ضرورت | توضیح | مثال |
|-----|--------|-------|------|
| key | ضروری | کلید API | `YOUR_API_KEY` |
| l18 | ضروری | نماد | `شتران` |
| date | اختیاری | تاریخ (فرمت شمسی YYYY-MM-DD)؛ پیش‌فرض: آخرین وضعیت | `1404-02-22` |

**نمونه درخواست:**
```
https://Api.BrsApi.ir/Tsetmc/Shareholder.php?key=YOUR_API_KEY&l18=شتران&date=1404-02-22
```

**ساختار خروجی:**

| فیلد | توضیح | نوع | مثال |
|------|-------|------|------|
| name | نام سهامدار | string | `ETFکدرزروصندوقهای سرمایه گذاری قابل معامله` |
| volume | تعداد سهام | integer | `789000000` |
| percent | درصد سهام | float | `17.53` |
| change | تغییر تعداد سهام | integer | `-5000000` |

---

## 13. API صندوق‌های کالایی بورس کالا (IME Fund)

**Endpoint:** `/IME/Fund.php`
**توضیح:** دریافت اطلاعات لحظه‌ای صندوق‌های کالایی (طلا، نقره، زعفران و ...) از بورس کالا.

**پارامترها:**

| نام | ضرورت | توضیح | مثال |
|-----|--------|-------|------|
| key | ضروری | کلید API | `YOUR_API_KEY` |

**نمونه درخواست:**
```
https://Api.BrsApi.ir/IME/Fund.php?key=YOUR_API_KEY
```

**ساختار خروجی (مشابه Symbol اما مخصوص صندوق‌های کالایی):**

| فیلد | توضیح | نوع | مثال |
|------|-------|------|------|
| time | زمان آخرین اطلاعات | string | `16:59:59` |
| l18 | نماد | string | `عیار` |
| l30 | نام صندوق | string | `صندوق طلای عیار مفید` |
| isin | شناسه بین‌المللی | string | `IRTKMOFD0001` |
| id | شناسه داخلی | string | `34144395039913458` |
| z | تعداد سهام | integer | `4665000000` |
| bvol | حجم مبنا | integer | `1` |
| mv | ارزش بازار | integer | `2486538300000000` |
| tmin | آستانه مجاز پایین | integer | `472511` |
| tmax | آستانه مجاز بالا | integer | `577513` |
| pmin | کمترین قیمت | integer | `525350` |
| pmax | بیشترین قیمت | integer | `539257` |
| py | قیمت پایانی دیروز | integer | `525012` |
| pf | اولین قیمت | integer | `525500` |
| pl | آخرین قیمت | integer | `535795` |
| pc | قیمت پایانی | integer | `533020` |
| tno | تعداد معاملات | integer | `76166` |
| tvol | حجم معاملات | integer | `98890947` |
| tval | ارزش معاملات | integer | `52710899199669` |
| Buy_CountI | تعداد خریدار حقیقی | integer | `24870` |
| Buy_CountN | تعداد خریدار حقوقی | integer | `71` |
| Sell_CountI | تعداد فروشنده حقیقی | integer | `11456` |
| Sell_CountN | تعداد فروشنده حقوقی | integer | `46` |
| Buy_I_Volume | حجم خرید حقیقی | integer | `88323991` |
| Buy_N_Volume | حجم خرید حقوقی | integer | `10566956` |
| Sell_I_Volume | حجم فروش حقیقی | integer | `68546829` |
| Sell_N_Volume | حجم فروش حقوقی | integer | `30344118` |
| zd1..zd5 | تعداد خریدار سطرهای ۱ تا ۵ | integer | `6` |
| qd1..qd5 | حجم خرید سطرهای ۱ تا ۵ | integer | `13619` |
| pd1..pd5 | قیمت خرید سطرهای ۱ تا ۵ | integer | `535795` |
| zo1..zo5 | تعداد فروشنده سطرهای ۱ تا ۵ | integer | `2` |
| qo1..qo5 | حجم فروش سطرهای ۱ تا ۵ | integer | `100` |
| po1..po5 | قیمت فروش سطرهای ۱ تا ۵ | integer | `535798` |

---

## 14. API کدال بورس (Codal Announcement)

**Endpoint:** `/Codal/Announcement.php`
**توضیح:** دریافت اطلاعیه‌ها و گزارش‌های مالی منتشرشده در سامانه کدال با قابلیت فیلترهای متنوع.

**پارامترها:**

| نام | ضرورت | توضیح | مثال |
|-----|--------|-------|------|
| key | ضروری | کلید API | `YOUR_API_KEY` |
| l18 | اختیاری | نماد | `وبملت` |
| category | اختیاری | شناسه گروه اطلاعیه (۱ تا ۱۱) | `1` |
| period | اختیاری | طول دوره (۱ تا ۱۲ ماه) | `3` |
| audited | اختیاری | حسابرسی شده (true/false) | `true` |
| unaudited | اختیاری | حسابرسی نشده (true/false) | `true` |
| only_main_company | اختیاری | فقط شرکت اصلی (true/false، پیش‌فرض true) | `true` |
| only_subsidiaries | اختیاری | فقط زیرمجموعه‌ها (true/false، پیش‌فرض true) | `false` |
| date_start | اختیاری | تاریخ شروع (فرمت شمسی YYYY-MM-DD) | `1403-01-01` |
| date_end | اختیاری | تاریخ پایان (فرمت شمسی YYYY-MM-DD) | `1403-10-30` |
| page | اختیاری | شماره صفحه (پیش‌فرض ۱) | `1` |

**راهنمای مقادیر category:**
- `1` = اطلاعات و صورت مالی سالانه
- `2` = افشای اطلاعات بااهمیت و شفاف‌سازی
- `3` = گزارش عملکرد ماهانه
- `4` = اساسنامه/امیدنامه
- `5` = اطلاعات هیئت مدیره و کمیته حسابرسی
- `6` = آگهی دعوت به مجامع و تصمیمات
- `7` = افزایش سرمایه
- `8` = شفاف‌سازی مربوط به بورس/فرابورس
- `9` = شفاف‌سازی مربوط به سازمان
- `10` = سایر
- `11` = اوراق بدهی

**نمونه درخواست (دریافت گزارش عملکرد ماهانه دی ۱۴۰۳):**
```
https://Api.BrsApi.ir/Codal/Announcement.php?key=YOUR_API_KEY&category=3&date_start=1403-10-01&date_end=1403-10-30&page=1
```

**ساختار خروجی:**

| فیلد | توضیح | نوع | مثال |
|------|-------|------|------|
| count_announcement | تعداد اطلاعیه‌ها | integer | `45` |
| count_page | تعداد صفحات | integer | `3` |
| l18 | نماد | string | `وبملت` |
| l30 | نام شرکت | string | `بانک ملت` |
| title | عنوان اطلاعیه | string | `اطلاعات و صورت‌های مالی میاندوره‌ای دوره ۹ ماهه ...` |
| code | کد اطلاعیه | string | `ن-۱۰` |
| date_title | تاریخ عنوان | string | `۱۴۰۳/۰۹/۳۰` |
| date_send | تاریخ ارسال | string | `۱۴۰۳/۱۰/۳۰` |
| time_send | زمان ارسال | string | `۱۷:۵۵:۴۱` |
| date_publish | تاریخ انتشار | string | `۱۴۰۳/۱۰/۳۰` |
| time_publish | زمان انتشار | string | `۱۷:۵۵:۴۱` |
| link | لینک اطلاعیه | string | `https://codal.ir/Reports/Decision.aspx?...` |
| link_pdf | لینک PDF | string | `https://codal.ir/DownloadFile.aspx?...` |
| link_excel | لینک Excel | string | `https://excel.codal.ir/...` |
| link_attachment | لینک پیوست‌ها | string | `https://codal.ir/Reports/Attachment.aspx?...` |

---

## 15. API کندل‌استیک بورس (TSETMC Candlestick)

**Endpoint:** `/Tsetmc/Candlestick.php`
**توضیح:** دریافت داده‌های کندل‌استیک (شمعی) به‌صورت لحظه‌ای، تعدیل‌نشده یا تعدیل‌شده.

**پارامترها:**

| نام | ضرورت | توضیح | مثال |
|-----|--------|-------|------|
| key | ضروری | کلید API | `YOUR_API_KEY` |
| type | ضروری | `1`=لحظه‌ای روز جاری، `2`=تعدیل‌نشده روزانه، `3`=تعدیل‌شده روزانه | `3` |
| l18 | ضروری | نماد | `فملی` |

**نمونه درخواست (تعدیل‌شده):**
```
https://Api.BrsApi.ir/Tsetmc/Candlestick.php?key=YOUR_API_KEY&type=3&l18=فملی
```

**ساختار خروجی:**

| فیلد | توضیح | نوع | مثال |
|------|-------|------|------|
| l18 | نماد | string | `فملی` |
| type | نوع دیتا | integer | `1` |
| count | تعداد کندل‌ها | integer | `105` |
| date | تاریخ | string | `1404/02/23` |
| time | زمان | string | `12:28` |
| open | قیمت اول | integer | `7370` |
| high | بالاترین قیمت | integer | `7450` |
| low | پایین‌ترین قیمت | integer | `7360` |
| close | قیمت آخر | integer | `7400` |
| volume | حجم معامله | integer | `21520911` |

---

## 16. API گواهی سپرده کالایی بورس کالا (IME Certificate)

**Endpoint:** `/IME/Certificate.php`
**توضیح:** دریافت اطلاعات لحظه‌ای گواهی‌های سپرده کالایی (شمش طلا، سکه، مس، روی و ...) از بورس کالا.

**پارامترها:**

| نام | ضرورت | توضیح | مثال |
|-----|--------|-------|------|
| key | ضروری | کلید API | `YOUR_API_KEY` |

**نمونه درخواست:**
```
https://Api.BrsApi.ir/IME/Certificate.php?key=YOUR_API_KEY
```

**ساختار خروجی:**

| فیلد | توضیح | نوع | مثال |
|------|-------|------|------|
| date_update | تاریخ آخرین دیتا | string | `1404-07-02` |
| time_update | زمان آخرین دیتا | string | `16:43:56` |
| time | زمان آخرین تغییرات | string | `16:43:51` |
| commodity | نام کالا | string | `شمش طلا` |
| contract_code | کد قرارداد | string | `CD1GOB0001` |
| contract_description | توضیح قرارداد | string | `گواهی سپرده پیوسته شمش طلای +995` |
| contract_size | اندازه قرارداد | integer | `10` |
| contract_size_unit | واحد اندازه | string | `ضریب تبدیل نماد به گروه انبار` |
| contract_currency | ارز قرارداد | string | `ریال` |
| date_y | تاریخ روز معاملاتی قبل | string | `1404-07-01` |
| py | قیمت پایانی روز قبل | integer | `12609320` |
| pf_time | زمان اولین قیمت | string | `12:00:00` |
| pf | اولین قیمت | integer | `13230000` |
| pmax | بالاترین قیمت | integer | `13239780` |
| pmin | پایین‌ترین قیمت | integer | `13130000` |
| pl_time | زمان آخرین قیمت | string | `16:43:27` |
| pl | آخرین قیمت | integer | `13239780` |
| tno | تعداد معاملات | integer | `2714` |
| tvol | حجم معاملات | integer | `1198661` |
| tval | ارزش معاملات (هزار ریال) | integer | `15869015001` |
| tval_unit | واحد ارزش معاملات | string | `هزار ریال` |
| date_order | تاریخ آخرین عرضه/تقاضا | string | `1404-07-02` |
| time_order | زمان آخرین عرضه/تقاضا | string | `16:43:27` |
| qd1..3 | حجم خرید سطر ۱ تا ۳ | integer | `558699` |
| pd1..3 | قیمت خرید سطر ۱ تا ۳ | integer | `13239780` |
| qo1..3 | حجم فروش سطر ۱ تا ۳ | integer | `0` |
| po1..3 | قیمت فروش سطر ۱ تا ۳ | integer | `0` |

---

## 17. Nav API صندوق‌های ETF بورس (TSETMC NAV)

**Endpoint:** `/Tsetmc/Nav.php`
**توضیح:** دریافت NAV صدور و ابطال یک صندوق ETF به‌صورت لحظه‌ای.

**پارامترها:**

| نام | ضرورت | توضیح | مثال |
|-----|--------|-------|------|
| key | ضروری | کلید API | `YOUR_API_KEY` |
| l18 | ضروری | نماد صندوق | `اهرم` |

**نمونه درخواست:**
```
https://Api.BrsApi.ir/Tsetmc/Nav.php?key=YOUR_API_KEY&l18=اهرم
```

**ساختار خروجی:**

| فیلد | توضیح | نوع | مثال |
|------|-------|------|------|
| date | تاریخ | string | `1403-10-19` |
| time | زمان | string | `15:58:04` |
| psubtran | NAV صدور | integer | `30012` |
| predtran | NAV ابطال | integer | `29655` |

---

## نکات تکمیلی

- **تاریخ‌ها:** در تمام APIها، تاریخ‌ها به‌صورت شمسی با فرمت `YYYY-MM-DD` ارسال می‌شوند، مگر در مواردی که تاریخ میلادی ذکر شده باشد.
- **پارامترهای اختیاری:** اگر پارامتر اختیاری ارسال نشود، API از مقدار پیش‌فرض یا آخرین داده‌های موجود استفاده می‌کند.
- **حالت بدون داده:** اگر در بازه تاریخی داده‌ای موجود نباشد، خروجی با وضعیت `"no_data"` برمی‌گردد.
- **محدودیت ریکوئست:** برخی APIها دارای محدودیت ریکوئست روزانه هستند (معمولاً ۱۰۰۰ تا ۱۵۰۰ ریکوئست رایگان).