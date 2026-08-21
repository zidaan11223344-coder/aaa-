# Giant Chat Bot — Railway + Music

هذه النسخة مخصصة لـ **Giant Chat** وليست Telegram.

## لماذا تم تعديل الموسيقى؟
النسخة السابقة كانت تنزّل الصوت ثم تضعه في Supabase Storage وترسل رابط `media_url`.
على بعض الاستضافات/الإعدادات لا يكون الرابط قابلاً للوصول من تطبيق Giant Chat، أو يكون الصوت بصيغة WebM/M4A غير مناسبة لبعض مشغلات الصوت.

هذه النسخة:
1. تبحث عن الأغنية عبر Piped ثم yt-dlp.
2. تنزّل الصوت.
3. تحوّله إلى **MP3** إذا كان `ffmpeg` متاحاً.
4. تشغّل خادم HTTP داخل نفس خدمة Railway.
5. ترسل إلى Giant Chat رابطاً عاماً من نوع HTTPS.
6. ترسل الرسالة داخل Giant Chat كـ `message_type=voice`.

## Railway
ارفع المشروع إلى GitHub ثم اختره في Railway.

Variables:
- `SUPABASE_URL`
- `SUPABASE_KEY`
- `GIANT_USERNAME`
- `GIANT_PASSWORD`
- `OWNER_USERNAME` (اختياري)
- `PUBLIC_BASE_URL` (اختياري؛ إذا لم تضعه، يحاول البوت استخدام `RAILWAY_PUBLIC_DOMAIN` تلقائياً)

إذا لم يتوفر `RAILWAY_PUBLIC_DOMAIN`، بعد إنشاء Public Domain للخدمة ضع:
`PUBLIC_BASE_URL=https://your-domain.up.railway.app`

لا تحتاج إلى Telegram ولا Bot Token.

## أمر التشغيل
Dockerfile يشغل:
`python bot.py`

## أمر الأغنية داخل Giant Chat
`تشغيل اسم الأغنية`

أو:
`play اسم الأغنية`

## مهم
لا تضع كلمة مرور الحساب أو مفتاح Supabase داخل GitHub. استخدم Railway Variables.


## الإصدار Railway V2
- `PUBLIC_BASE_URL` يستخدم للصور والأغاني والهدايا.
- `YOUTUBE_COOKIES` متغير سري اختياري يحتوي محتوى ملف Netscape cookies للحساب الأول.
- `YOUTUBE_COOKIES_1` إلى `YOUTUBE_COOKIES_10` متغيرات سرية اختيارية لحسابات إضافية؛ كل متغير يمثل ملف Cookies مستقلًا لحساب تملكه.
- عند فشل جلسة، يجرب البوت الجلسة التالية تلقائيًا. لا يتم دمج ملفات Cookies بين الحسابات ولا يتم تسجيل محتواها في السجل.
- `TIKTOK_COOKIES` متغير سري اختياري لـ yt-dlp.
- `SPOTIFY_COOKIES` محفوظ كمتغير اختياري، لكن Spotify لا يوفّر بث الأغاني عبر cookies؛ يمكن استخدامه للوصول إلى البيانات فقط.
- أوامر فلتر الكلمات: `mf@on`, `mf@off`, `+mf@كلمة`, `-mf@كلمة`, `l@mf`, `clear@mf`.
- أوامر الترحيب: `+wc رسالة`, `wc@on`, `wc@off`, `l@wc`, `clear@wc`.
- `نشر@` ثم إرسال صورة ينشر الصورة في الغرف التي يستطيع البوت الكتابة فيها.

\n## Music V5
- `تشغيل اسم الأغنية` يبحث في YouTube بعدة صيغ.
- `.تشغيل اسم الأغنية` يستخدم Spotify للبيانات ثم يبحث عن نسخة صوتية قابلة للتشغيل.
- `تيك اسم الأغنية` لا يحتاج TikTok Cookies.
- إذا تعذر تنزيل الصوت، يرسل البوت رابط التشغيل المباشر داخل الغرفة بدلاً من فقدان النتيجة.
- أخطاء البحث والتنزيل الحقيقية تستمر بالوصول إلى الماستر للتشخيص.
- `SPOTIFY_COOKIES` لا تُستخدم لتنزيل صوت Spotify؛ Spotify لا يوفّر ملف الصوت الخام عبر cookies.

## Music V12 — تشخيص YouTube
- `YOUTUBE_COOKIES` و`YOUTUBE_COOKIES_1..10` يتم التحقق من صيغتها عند بدء البوت دون تسجيل محتواها.
- `YOUTUBE_PLAYER_CLIENTS` اختياري؛ الافتراضي `web_safari,tv,web` لتفادي فرض `mweb` افتراضياً.
- `YOUTUBE_PO_TOKEN` اختياري إذا كان إعداد YouTube/yt-dlp عندك يحتاج PO Token.
- رسائل فشل الموسيقى تفرق بين: عدم وجود نتائج، فشل الاتصال بيوتيوب، رفض YouTube بسبب Cookies/تحقق/PO Token، فشل التنزيل، فشل FFmpeg، فشل الرابط العام، وفشل إرسال رسالة الصوت.
- التفاصيل الخام للخطأ تصل للماستر بدون إرسال قيمة Cookies أو الأسرار.
- جميع صور الألعاب أصبحت محلية داخل `assets/`، ولعبة الحرب تستخدم `war_game.png` وبطاقة نتيجة ديناميكية للسفينة المدمرة.
