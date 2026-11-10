import os, re, time, uuid, random, threading, mimetypes
from pathlib import Path
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import quote

try:
    import yt_dlp
except Exception:
    yt_dlp = None

try:
    from PIL import Image, ImageDraw, ImageFont, ImageOps
except Exception:
    Image = ImageDraw = ImageFont = None

try:
    import arabic_reshaper
    from bidi.algorithm import get_display
except Exception:
    arabic_reshaper = get_display = None

BASE_DIR = Path(__file__).resolve().parent
MEDIA_DIR = BASE_DIR / 'media'
MUSIC_DIR = MEDIA_DIR / 'music'
GIFT_DIR = BASE_DIR / 'assets'
MUSIC_DIR.mkdir(parents=True, exist_ok=True)
MEDIA_DIR.mkdir(parents=True, exist_ok=True)

def _rtl_text(value):
    """Return Arabic in visual order only for Pillow's legacy renderer."""
    text = str(value or "")
    if not any("\u0600" <= ch <= "\u06ff" for ch in text):
        return text
    try:
        if Image is not None and getattr(Image, "features", None):
            from PIL import features
            if features.check("raqm"):
                return text
    except Exception:
        pass
    if arabic_reshaper is not None and get_display is not None:
        try:
            return get_display(arabic_reshaper.reshape(text), base_dir="R")
        except Exception:
            pass
    return text

def _draw_rtl(draw, xy, value, font, **kwargs):
    text = _rtl_text(value)
    try:
        return draw.text(xy, text, font=font, direction="rtl", language="ar", **kwargs)
    except (TypeError, ValueError):
        return draw.text(xy, text, font=font, **kwargs)

GIFTS = {
 '1':('🌹','وردة'), '2':('❤️','قلب'), '3':('💋','قبلة'), '4':('🧸','دب'),
 '5':('🎂','كعكة'), '6':('🎆','ألعاب نارية'), '7':('⚡','برق'), '8':('👑','تاج'),
 '9':('👸','أميرة'), '10':('🏎️','سيارة'), '11':('✈️','طائرة'), '12':('🐉','تنين'),
 '13':('🚀','سفينة فضاء'), '14':('🏰','قصر')
}

class _Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        # Always serve from the project root so /media/... resolves even if the
        # bot changes its working directory later.
        kw['directory'] = str(BASE_DIR)
        super().__init__(*a, **kw)

    def log_message(self, fmt, *args):
        pass

    def end_headers(self):
        # Chat clients fetch media from another origin.
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Cache-Control', 'public, max-age=3600')
        super().end_headers()

def public_base_url():
    # One public base URL serves every media type; retain old variable names
    # as fallbacks for deployments that have not migrated their .env yet.
    for key in ('PUBLIC_MEDIA_URL', 'MEDIA_PUBLIC_URL', 'PUBLIC_BASE_URL',
                'RAVEN_PUBLIC_URL', 'GIFT_PUBLIC_BASE_URL'):
        manual = os.getenv(key, '').strip().rstrip('/')
        if manual: return manual
    for key in ('RAVEN_PUBLIC_DOMAIN', 'RAILWAY_PUBLIC_DOMAIN',
                'RENDER_EXTERNAL_URL', 'RENDER_EXTERNAL_HOSTNAME', 'RAILWAY_STATIC_URL'):
        domain = os.getenv(key, '').strip().rstrip('/')
        if domain:
            if domain.startswith(('http://', 'https://')):
                return domain
            return 'https://' + domain
    return ''

def start_media_server(port=None):
    port = int(port or os.getenv('PORT') or os.getenv('ASSET_HTTP_PORT') or '8080')
    server = ThreadingHTTPServer(('0.0.0.0', port), _Handler)
    threading.Thread(target=server.serve_forever, name='media-server', daemon=True).start()
    print(f'[MEDIA] server listening on 0.0.0.0:{port}', flush=True)
    return server

def _cleanup():
    cutoff=time.time()-3600
    for p in MUSIC_DIR.glob('*'):
        try:
            if p.is_file() and p.stat().st_mtime < cutoff: p.unlink()
        except OSError: pass

def _yt_options(cookie_file=None, player_clients=None):
    o={
      'quiet':True, 'no_warnings':True, 'noplaylist':True,
      'socket_timeout':35, 'retries':6, 'fragment_retries':6,
      'file_access_retries':3, 'extractor_retries':3,
      'retry_sleep_functions': {'http': lambda n: min(2 ** n, 8), 'fragment': lambda n: min(2 ** n, 8)},
      'cachedir':False, 'overwrites':True,
      'format':'bestaudio/best',
      'http_headers': {'User-Agent':'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131 Safari/537.36'},
      'outtmpl':str(MUSIC_DIR/'%(id)s.%(ext)s'),
      'postprocessors':[{'key':'FFmpegExtractAudio','preferredcodec':'mp3','preferredquality':'128'}],
    }
    if cookie_file and Path(cookie_file).is_file(): o['cookiefile']=cookie_file
    clients = player_clients or [x.strip() for x in os.getenv('YOUTUBE_PLAYER_CLIENTS','tv,web_safari,mweb,web_embedded,default').split(',') if x.strip()]
    o['extractor_args'] = {'youtube': {'player_client': clients or ['default']}}
    return o

def _cookie_candidates():
    out=[]
    file_env=os.getenv('YOUTUBE_COOKIES_FILE','').strip()
    if file_env and Path(file_env).is_file(): out.append(file_env)
    for candidate in (BASE_DIR/'youtube_cookies.txt', BASE_DIR/'cookies.txt'):
        if candidate.is_file() and str(candidate) not in out: out.append(str(candidate))
    raw=os.getenv('YOUTUBE_COOKIES','').strip()
    if raw:
        p=Path('/tmp/youtube_cookies.txt'); p.write_text(raw, encoding='utf-8'); out.append(str(p))
    for i in range(1,11):
        raw=os.getenv(f'YOUTUBE_COOKIES_{i}','').strip()
        if raw:
            p=Path(f'/tmp/youtube_cookies_{i}.txt'); p.write_text(raw, encoding='utf-8'); out.append(str(p))
    return out

def youtube_cookie_status():
    files=_cookie_candidates()
    if not files: return False, 'لا يوجد ملف Cookies مضبوط.'
    for f in files:
        try:
            rows=0
            for line in Path(f).read_text(encoding='utf-8', errors='ignore').splitlines():
                if line and not line.startswith('#') and len(line.split('\t')) >= 7: rows += 1
            if rows: return True, f'Cookies موجودة: {rows} سجل.'
        except Exception: pass
    return False, 'ملف Cookies موجود لكنه ليس بصيغة Netscape الصحيحة.'

def _youtube_client_profiles():
    """Return ordered profiles; YouTube occasionally invalidates one client temporarily."""
    configured = [x.strip() for x in os.getenv('YOUTUBE_PLAYER_CLIENTS','tv,web_safari,mweb,web_embedded,default').split(',') if x.strip()]
    profiles = [configured or ['default'], ['tv'], ['mweb'], ['web_safari'], ['web_embedded'], ['android_vr'], ['ios'], ['android'], ['default']]
    unique=[]
    for profile in profiles:
        if profile not in unique: unique.append(profile)
    return unique

def search_download_youtube(query):
    if yt_dlp is None: raise RuntimeError('yt-dlp غير مثبت')
    q=str(query or '').strip()
    if not q: raise RuntimeError('اكتب اسم الأغنية')
    _cleanup()
    url=q if re.match(r'https?://(?:www\.)?(?:youtube\.com|youtu\.be)/', q, re.I) else 'ytsearch1:' + q
    errors=[]
    cookies=_cookie_candidates() or [None]
    # A transient "page needs to be reloaded" must not fail the command immediately.
    attempts=int(os.getenv('YOUTUBE_ATTEMPTS','3'))
    for cookie in cookies:
        for clients in _youtube_client_profiles():
            for attempt in range(max(1, attempts)):
                try:
                    opts=_yt_options(cookie, clients); opts['skip_download']=True
                    with yt_dlp.YoutubeDL(opts) as ydl: info=ydl.extract_info(url, download=False)
                    if info and info.get('entries'): info=next((e for e in info['entries'] if e), None)
                    if not info: raise RuntimeError('لم يتم العثور على نتيجة')
                    duration=float(info.get('duration') or 0)
                    if duration > 900: raise RuntimeError('الأغنية أطول من 15 دقيقة')
                    vid=str(info.get('id') or uuid.uuid4().hex)
                    title=str(info.get('title') or q)
                    artist=str(info.get('uploader') or info.get('channel') or 'YouTube')
                    direct=str(info.get('webpage_url') or url)
                    opts=_yt_options(cookie, clients); opts['outtmpl']=str(MUSIC_DIR/f'{vid}.%(ext)s')
                    with yt_dlp.YoutubeDL(opts) as ydl: ydl.download([direct])
                    mp3=MUSIC_DIR/f'{vid}.mp3'
                    candidates=list(MUSIC_DIR.glob(f'{vid}.*'))
                    if not mp3.exists():
                        candidates=[p for p in candidates if p.suffix.lower() in ('.mp3','.m4a','.webm','.opus')]
                        if not candidates: raise RuntimeError('فشل تنزيل الصوت')
                        mp3=candidates[0]
                    return {'title':title,'artist':artist,'duration_ms':int(duration*1000),'path':mp3,'source_url':direct}
                except Exception as e:
                    message=str(e).strip() or type(e).__name__
                    errors.append(f'{clients[0]}#{attempt+1}: {message}')
                    if attempt + 1 < max(1, attempts): time.sleep(min(2 + attempt * 2, 6))
    raise RuntimeError(' | '.join(errors[-3:]) or 'تعذر تنزيل الأغنية')

def music_url(path):
    base=public_base_url()
    if not base: raise RuntimeError('رابط الوسائط العام غير مضبوط. فعّل Public Domain للخدمة في Railway أو ضع PUBLIC_BASE_URL.')
    return base + '/media/music/' + quote(Path(path).name)

def gift_image(gid):
    patterns=[f'gift_{int(gid):02d}_*.png',f'gift_{int(gid):02d}_*.jpg',f'gift_{int(gid):02d}_*.jpeg']
    files=[]
    for pat in patterns: files += list(GIFT_DIR.glob(pat))
    if not files: raise RuntimeError(f'لا توجد صورة للهدية رقم {gid}')
    return random.choice(files)

def gift_url(path):
    base=public_base_url()
    if not base: raise RuntimeError('رابط الوسائط العام غير مضبوط. فعّل Public Domain للخدمة في Railway أو ضع PUBLIC_BASE_URL.')
    target=MEDIA_DIR/'gifts'; target.mkdir(exist_ok=True)
    dst=target/(f'{Path(path).stem}_{uuid.uuid4().hex[:8]}{Path(path).suffix}')
    dst.write_bytes(Path(path).read_bytes())
    return base + '/media/gifts/' + quote(dst.name)

def gift_card_url(gid, sender, receiver):
    """Create a personalized card from the supplied elegant template."""
    base=public_base_url()
    if not base: raise RuntimeError('رابط الوسائط العام غير مضبوط. فعّل Public Domain للخدمة في Railway أو ضع PUBLIC_BASE_URL.')
    if Image is None:
        return gift_url(gift_image(gid))
    template=GIFT_DIR/'gift_template_elegant.png'
    if not template.is_file():
        try:
            template=gift_image(gid)
        except Exception:
            return gift_url(gift_image(gid))
    target=MEDIA_DIR/'gifts'; target.mkdir(exist_ok=True)
    dst=target/(f'gift_{int(gid):02d}_{uuid.uuid4().hex[:8]}.png')
    # Use one of the three supplied gift variants as the artwork layer. The
    # elegant template is a transparent frame/overlay, not the gift itself.
    # This keeps repeated sends visually different while preserving the
    # repository assets as the source of truth.
    artwork=Image.open(gift_image(gid)).convert('RGBA')
    image=ImageOps.fit(artwork, (1400, 1435), method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))
    template_image=Image.open(template).convert('RGBA').resize(image.size, Image.Resampling.LANCZOS)
    image.alpha_composite(template_image)
    draw=ImageDraw.Draw(image)
    font_path=GIFT_DIR/'NotoSansArabic-SemiBold.ttf'
    font_small=GIFT_DIR/'DejaVuSans.ttf'
    try:
        ar_font=ImageFont.truetype(str(font_path), 47)
        en_font=ImageFont.truetype(str(font_small), 35)
        title_font=ImageFont.truetype(str(font_path), 62)
    except Exception:
        ar_font=en_font=title_font=ImageFont.load_default()
    cx=image.width//2
    _emoji,name=GIFTS.get(str(gid), ('🎁','هدية'))
    # Keep Arabic labels and account names on separate lines so a font that
    # lacks Latin glyphs cannot turn usernames into replacement boxes.
    _draw_rtl(draw, (cx, 235), name, title_font, fill='#4b241d', anchor='mm', stroke_width=1, stroke_fill='#f0c27b')
    _draw_rtl(draw, (cx, 420), 'المرسل', ar_font, fill='#3f211b', anchor='mm', stroke_width=1, stroke_fill='#eabd7d')
    draw.text((cx, 475), f'@{sender}', font=en_font, fill='#3f211b', anchor='mm')
    _draw_rtl(draw, (cx, 545), 'المستقبل', ar_font, fill='#3f211b', anchor='mm', stroke_width=1, stroke_fill='#eabd7d')
    draw.text((cx, 600), f'@{receiver}', font=en_font, fill='#3f211b', anchor='mm')
    draw.text((cx, 650), 'A special gift for you', font=en_font, fill='#6a3428', anchor='mm')
    image.save(dst, format='PNG', optimize=True)
    return base + '/media/gifts/' + quote(dst.name)

def url_is_reachable(url, timeout=6):
    """Verify the chat server will actually be able to fetch the media URL."""
    try:
        import urllib.request
        req=urllib.request.Request(url, method='GET', headers={'Range':'bytes=0-64','User-Agent':'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= int(resp.status) < 400
    except Exception:
        return False


def gift_card(gid, sender, receiver):
    """Always return a usable public image URL for a gift.

    Order: personalized card -> plain gift picture -> raise.
    Any failure in the fancy card must never remove the picture.
    """
    errors=[]
    for maker in (lambda: gift_card_url(gid, sender, receiver), lambda: gift_url(gift_image(gid))):
        try:
            url=maker()
            if url: return url
        except Exception as e:
            errors.append(str(e))
    raise RuntimeError(' | '.join(errors) or 'تعذر تجهيز صورة الهدية')


def gifts_catalog():
    return '\n'.join(['🎁 الهدايا','━━━━━━━━━━━━',' '.join([f'{k}:{v[0]} {v[1]}' for k,v in GIFTS.items()]),'━━━━━━━━━━━━','الإرسال: gv@رقم_الهدية@اسم_المستخدم'])
