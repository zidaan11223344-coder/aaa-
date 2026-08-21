from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

base = Path(__file__).resolve().parent
template = base / 'assets' / 'gift_template_rose.webp'
out = base / 'generated_gifts' / 'test_from_to.png'
out.parent.mkdir(parents=True, exist_ok=True)
image = Image.open(template).convert('RGBA')
draw = ImageDraw.Draw(image)
font_path = base / 'assets' / 'Amiri-Bold.ttf'
font = ImageFont.truetype(str(font_path), 28)
for label, name, y in [('FROM:', 'alsfer', int(image.height*0.78)), ('TO:', 'Crocodile', int(image.height*0.88))]:
    text = f'{label} @{name}'
    box = draw.textbbox((0, 0), text, font=font)
    x = (image.width - (box[2] - box[0])) // 2
    draw.text((x + 2, y + 2), text, font=font, fill=(0, 0, 0, 180), stroke_width=2, stroke_fill=(0, 0, 0, 180))
    draw.text((x, y), text, font=font, fill=(255, 255, 255, 255), stroke_width=1, stroke_fill=(20, 20, 20, 255))
image.save(out, 'PNG', optimize=True)
print(out)
print(Image.open(out).size)
