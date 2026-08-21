# تعليمات نشر Giant Chat Bot

## PythonAnywhere

ارفع مجلد `ready_bot` كاملًا إلى المسار `/home/Ahmd444/ready_bot/`، بما في ذلك مجلدا `assets/` و`generated_gifts/`. من صفحة **Web > Static files** أضف السطر التالي:

| URL | Directory |
|---|---|
| `/gifts/` | `/home/Ahmd444/ready_bot/generated_gifts/` |
| `/games/` | `/home/Ahmd444/ready_bot/assets/` |

إذا كان اسم مستخدم PythonAnywhere مختلفًا، عدّل المسار والرابط في `config.json`. القيمة الحالية هي:

```json
"gift_public_base_url": "https://Ahmd444.pythonanywhere.com/gifts"
```

من تبويب **Consoles** شغّل:

```bash
cd /home/Ahmd444/ready_bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python3 -m py_compile bot.py
python3 bot.py
```

للتشغيل المستمر استخدم **Always-on task** إن كان متاحًا في حسابك، وضع الأمر:

```bash
cd /home/Ahmd444/ready_bot && /home/Ahmd444/ready_bot/venv/bin/python bot.py
```

لا تشغّل نسختين من البوت في الوقت نفسه. أوقف العملية القديمة من وحدة التحكم، أو اعرض رقمها بالأمر `pgrep -af 'python.*bot.py'` ثم أرسل لها `kill -TERM رقم_العملية`.

## اختبار الهدايا

بعد ضبط Static Files جرّب أمرًا مثل:

```text
gv@1@اسم_الحساب
```

رقم `1` هو الوردة، ورقم `2` القلب، وبقية الأرقام موجودة في كتالوج البوت. يجب أن تظهر الصورة قبل رسالة الإهداء، وبداخلها اسما المرسل والمستقبل. الصور تحفظ محليًا داخل `generated_gifts/` وتُحذف تلقائيًا بعد نحو 30 دقيقة.

## الموسيقى

استخراج YouTube يعتمد على `yt-dlp` ويعمل في مهمة خلفية حتى لا يتوقف استقبال أوامر الغرفة. الرسالة الصوتية تُرسل بنوع `voice`، ولذلك يفترض أن يظهر لها زر التشغيل داخل Giant Chat. خطأ `Sign in to confirm you're not a bot` مصدره حظر YouTube لعنوان IP الخاص بالاستضافة، وليس خطأً في حلقة البوت. جرّب تحديث `yt-dlp` أولًا؛ وإذا استمر الخطأ فالأكثر استقرارًا استخدام مزود موسيقى أو استضافة أخرى. لا ترفع Cookies لحساب شخص آخر.

## Pydroid 3

ثبت الحزم من الملف المناسب:

```bash
pip install -r requirements_pydroid.txt
python bot.py
```

يُفضّل استخدام PythonAnywhere للتشغيل الدائم، وPydroid للاختبار أو التشغيل المؤقت من الهاتف.

## الصور والموسيقى والأوامر الجديدة
في `config.json` اضبط `game_public_base_url` على:
`https://USERNAME.pythonanywhere.com/games`

الأوامر:
- `حرب` ثم لاعب ثانٍ يكتب `حرب`، وبعد بدء الجولة اكتب رقمًا 1..6 بالتناوب، 3 محاولات لكل لاعب.
- `تيك اسم الأغنية` للصوت من TikTok.
- `نشر النص` للماستر.
- `نشرصورة رابط_الصورة` للماستر.
- الفاصل بين ألعاب المستخدمين 30 ثانية، وبين طلبات الصوت دقيقتان.

مهم: TikTok وYouTube قد يغيران صفحات البحث أو يمنعان عناوين IP الخاصة بالخوادم؛ لذلك وجود الأمر لا يضمن نجاح الاستخراج في كل وقت.


## صور الألعاب الجديدة
بعد رفع النسخة، اجعل Static Files يشير إلى مجلد `assets` على المسار `/games/`. الصور المستخدمة للألعاب هي:
- `slap_action.jpg`
- `defense_action.jpg`
- `fight_action.jpg`

ويجب أن يكون `game_public_base_url` في `config.json` رابط `/games` الخاص بحساب PythonAnywhere.
