import random, numpy as np
from pdf2image import convert_from_path
from PIL import Image, ImageFilter, ImageDraw, ImageFont, ImageEnhance
random.seed(7); np.random.seed(7)
src = "invoices/08_scanned_orbit_no_po.pdf"
img = convert_from_path(src, dpi=150)[0].convert("RGB")
# rubber stamp "RECEIVED" in blue, slightly rotated
st = Image.new("RGBA", (420, 170), (0, 0, 0, 0)); d = ImageDraw.Draw(st)
f1 = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 44)
f2 = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 26)
d.rounded_rectangle((6, 6, 414, 164), 14, outline=(40, 60, 170, 200), width=6)
d.text((40, 22), "RECEIVED", font=f1, fill=(40, 60, 170, 200))
d.text((40, 90), "26 SEP 2026 - KCPL", font=f2, fill=(40, 60, 170, 190))
st = st.rotate(9, expand=True, resample=Image.BICUBIC)
img.paste(st, (int(img.width*0.52), int(img.height*0.50)), st)
# handwritten-ish note
d2 = ImageDraw.Draw(img)
fh = ImageFont.truetype("/usr/share/fonts/truetype/freefont/FreeSerifItalic.ttf", 30)
d2.text((int(img.width*0.08), int(img.height*0.70)), "fwd to Sandeep - IT", font=fh, fill=(30, 30, 30))
g = img.convert("L")
g = g.rotate(-1.3, resample=Image.BICUBIC, expand=False, fillcolor=245)
arr = np.asarray(g).astype(np.float32)
# uneven lighting + noise
yy, xx = np.mgrid[0:arr.shape[0], 0:arr.shape[1]]
arr = arr * (0.93 + 0.07 * (xx / arr.shape[1])) - 8 * (yy / arr.shape[0])
arr += np.random.normal(0, 9, arr.shape)
arr = np.clip(arr, 0, 255).astype(np.uint8)
g = Image.fromarray(arr).filter(ImageFilter.GaussianBlur(0.7))
g = ImageEnhance.Contrast(g).enhance(0.85)
g.save("invoices/08_scanned_orbit_no_po.jpg", quality=55)
Image.open("invoices/08_scanned_orbit_no_po.jpg").save("invoices/08_scanned_orbit_no_po.pdf", "PDF", resolution=150)
print(g.size)
