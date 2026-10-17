"""Profile picture: a cream index card seal. 1080x1080, reads at 150px."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "grade"))
from PIL import Image, ImageDraw  # noqa: E402
from atlas_grade import _font, CREAM, INK, OXBLOOD  # noqa: E402

S = 1080
img = Image.new("RGB", (S, S), CREAM)
d = ImageDraw.Draw(img)
# double ring: Instagram shows the picture as a circle, so the seal is drawn as one
d.ellipse((60, 60, S - 60, S - 60), outline=INK, width=8)
d.ellipse((88, 88, S - 88, S - 88), outline=INK, width=2)
f1 = _font(True, 132)
f2 = _font(False, 52)
for i, word in enumerate(["BUREAU", "OF", "LEISURE"]):
    w = f1.getlength(word)
    d.text(((S - w) / 2, 300 + i * 150), word, font=f1, fill=INK)
tag = "EST. 2026"
w = f2.getlength(tag)
d.text(((S - w) / 2, 770), tag, font=f2, fill=OXBLOOD)
out = os.path.join(os.path.dirname(__file__), "avatar.jpg")
img.save(out, quality=95)
# the small version Instagram actually shows
img.resize((320, 320), Image.LANCZOS).save(os.path.join(os.path.dirname(__file__), "avatar_preview.jpg"), quality=92)
print(out)
