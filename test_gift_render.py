from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
try:
    import arabic_reshaper
    from bidi.algorithm import get_display
except ImportError:
    arabic_reshaper = None
    get_display = None

base = Path(__file__).resolve().parent
template = base / "assets" / "gift_template_elegant.png"
source = Path("/mnt/data/a_romantic_gift_card_banner_design_overall_scen.png")
out = base / "generated_gifts" / "test_elegant_arabic.png"

def shape(text):
    if arabic_reshaper and get_display:
        return get_display(arabic_reshaper.reshape(text))
    return text

def center(d, xy, text, font, fill):
    text = shape(text)
    b = d.textbbox((0,0), text, font=font, stroke_width=2)
    x = int(xy[0]-(b[2]-b[0])/2-b[0])
    y = int(xy[1]-(b[3]-b[1])/2-b[1])
    d.text((x,y), text, font=font, fill=fill, stroke_width=2, stroke_fill=(0,0,0,210))

bg = Image.open(source).convert("RGB").resize(Image.open(template).size)
im = bg.convert("RGBA")
im.alpha_composite(Image.open(template).convert("RGBA"))
d = ImageDraw.Draw(im)
font_path = base / "assets" / "Amiri-Bold.ttf"
font = ImageFont.truetype(str(font_path), 44)
small = ImageFont.truetype(str(font_path), 30)

gold=(244,196,92,255)
panel=(10,14,28,238)
w,h=im.size
header=(int(w*.27),65,int(w*.73),205)
d.rounded_rectangle(header,radius=48,fill=panel,outline=gold,width=4)
center(d,((header[0]+header[2])/2,135),"هدية بوسة",font,(255,222,155,255))

left=(55,int(h*.70),int(w*.455),int(h*.91))
right=(int(w*.545),int(h*.70),w-55,int(h*.91))
for box in (left,right):
    d.rounded_rectangle(box,radius=34,fill=panel,outline=gold,width=4)
center(d,((left[0]+left[2])/2,left[1]+35),"إلى",small,(255,224,165,255))
center(d,((right[0]+right[2])/2,right[1]+35),"من",small,(255,224,165,255))
center(d,((left[0]+left[2])/2,left[1]+125),"الأميرة جنات",font,(255,130,165,255))
center(d,((right[0]+right[2])/2,right[1]+125),"السفير",font,(100,220,255,255))
im.save(out,"PNG")
print(out)
print(im.size)


# Regression cases:
# 1) Arabic must remain Arabic and unchanged.
# 2) Latin usernames must render as real Latin glyphs, not square boxes.
# The production renderer selects DejaVuSans.ttf automatically for Latin-only names.
