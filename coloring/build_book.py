"""Build the KDP interior PDF for a coloring book.
Usage: python build_book.py <pages_dir> <out.pdf> [--sample N,N]
8.5x11 no-bleed, 300 DPI. Each coloring page on a right-hand page, backed by a blank.
"""
import sys, pathlib, re
from PIL import Image, ImageDraw, ImageFont, ImageFilter

W, H = 2550, 3300            # 8.5 x 11 in at 300 DPI
M = 188                      # 0.625 in outer margin (KDP min 0.25 in; kids' books like room)
LABEL_H = 520                # space at the bottom for the word label
FONT = "/usr/share/fonts/truetype/google-fonts/Poppins-Bold.ttf"

LABELS = {
 "dump-truck":"DUMP TRUCK","excavator":"EXCAVATOR","bulldozer":"BULLDOZER","monster-truck":"MONSTER TRUCK",
 "fire-truck":"FIRE TRUCK","cement-mixer":"CEMENT MIXER","crane":"CRANE","tractor":"TRACTOR",
 "garbage-truck":"GARBAGE TRUCK","tow-truck":"TOW TRUCK","front-loader":"LOADER","race-car":"RACE CAR",
 "digger":"DIGGER","road-roller":"ROAD ROLLER","police-car":"POLICE CAR","school-bus":"SCHOOL BUS",
 "ice-cream-truck":"ICE CREAM TRUCK","combine-harvester":"COMBINE","forklift":"FORKLIFT",
 "semi-truck":"SEMI TRUCK","snowplow":"SNOWPLOW","skid-steer":"SKID STEER","pickup-truck":"PICKUP TRUCK",
 "ambulance":"AMBULANCE","train-engine":"TRAIN","helicopter":"HELICOPTER","street-sweeper":"STREET SWEEPER",
 "cherry-picker":"BUCKET TRUCK","fuel-truck":"TANKER TRUCK","mail-truck":"MAIL TRUCK","big-rig":"BIG RIG",
 "digger-and-dump-truck":"TEAMWORK!","parade-of-trucks":"TRUCK PARADE!",
}

def label_for(slug):
    for k in sorted(LABELS, key=len, reverse=True):
        if slug.startswith(k):
            return LABELS[k]
    return slug.split("-")[0].upper()

def clean_art(path, box_w, box_h):
    im = Image.open(path).convert("L")
    s = min(box_w / im.width, box_h / im.height)
    im = im.resize((round(im.width * s), round(im.height * s)), Image.LANCZOS)
    im = im.filter(ImageFilter.GaussianBlur(1.2))
    return im.point(lambda v: 0 if v < 150 else 255).convert("1")   # pure black/white

def outlined_word(draw, text, cx, top, max_w, max_h):
    size = 400
    while size > 60:
        f = ImageFont.truetype(FONT, size)
        l, t, r, b = draw.textbbox((0, 0), text, font=f, stroke_width=14)
        if r - l <= max_w and b - t <= max_h:
            break
        size -= 10
    x = cx - (r - l) / 2 - l
    y = top + (max_h - (b - t)) / 2 - t
    # hollow bubble letters: white fill, thick black stroke -> kids can color the word
    draw.text((x, y), text, font=f, fill=255, stroke_width=14, stroke_fill=0)

def coloring_page(path, slug):
    page = Image.new("L", (W, H), 255)
    art = clean_art(path, W - 2 * M, H - 2 * M - LABEL_H)
    page.paste(art.convert("L"), ((W - art.width) // 2, M))
    d = ImageDraw.Draw(page)
    outlined_word(d, label_for(slug), W / 2, H - M - LABEL_H + 60, W - 2 * M, LABEL_H - 80)
    return page.convert("1")

def text_page(lines):
    page = Image.new("L", (W, H), 255); d = ImageDraw.Draw(page)
    y = 1000
    for txt, size in lines:
        f = ImageFont.truetype(FONT, size)
        l, t, r, b = d.textbbox((0, 0), txt, font=f, stroke_width=10)
        d.text(((W - (r - l)) / 2 - l, y - t), txt, font=f, fill=255, stroke_width=10, stroke_fill=0)
        y += (b - t) + 120
    return page.convert("1")

def belongs_page():
    page = text_page([("THIS BOOK", 230), ("BELONGS TO", 230)])
    d = ImageDraw.Draw(page)
    d.rounded_rectangle((M + 100, 1900, W - M - 100, 2500), radius=80, outline=0, width=16)
    return page

def blank():
    return Image.new("1", (W, H), 1)

def main():
    src = pathlib.Path(sys.argv[1]); out = sys.argv[2]
    files = sorted(src.glob("*.png"))
    pages = [text_page([("BIG TRUCKS", 300), ("& ANIMAL", 230), ("DRIVERS", 230)]), blank(),
             belongs_page(), blank()]
    for f in files:
        slug = re.sub(r"^[a-z]+_\d+_", "", f.stem)
        pages += [coloring_page(f, slug), blank()]
    pages[0].save(out, save_all=True, append_images=pages[1:], resolution=300)
    print(f"{len(files)} coloring pages -> {len(pages)} interior pages -> {out}")

if __name__ == "__main__":
    main()
