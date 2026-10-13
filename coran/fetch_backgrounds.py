#!/usr/bin/env python3
"""Cherche des photos de paysages libres de droits (CC0 / domaine public) sur
Wikimedia Commons, les télécharge et fabrique une planche contact pour choisir.

Sortie : out_fonds/<n>.jpg, out_fonds/planche.jpg, coran/fonds_candidats.json
"""
import json
import os
import sys
import urllib.parse
import urllib.request

from PIL import Image, ImageDraw, ImageFont

API = "https://commons.wikimedia.org/w/api.php"
UA = {"User-Agent": "quiz-ou-pas-video-bot/1.0 (github.com/yanismesbah77100-tech/quiz-)"}
QUERIES = ["beach sunset", "clouds sky", "ocean waves", "sand dunes desert", "mountain lake",
           "sunrise sea", "sky clouds sunset", "palm beach", "night sky stars landscape", "waterfall forest"]
OK_LIC = ("cc0", "public domain", "pd")
OUT = "out_fonds"


def get(params):
    url = API + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
        return json.load(r)


def main():
    os.makedirs(OUT, exist_ok=True)
    seen, cands = set(), []
    for q in QUERIES:
        for cat in ("CC-Zero", "PD-self", "PD-user"):
            d = get({"action": "query", "format": "json", "generator": "search", "gsrnamespace": 6,
                     "gsrsearch": f'{q} incategory:"{cat}" filetype:bitmap', "gsrlimit": 12,
                     "prop": "imageinfo", "iiprop": "url|size|extmetadata|mime",
                     "iiurlwidth": 2400})
            for p in (d.get("query", {}).get("pages", {}) or {}).values():
                ii = p.get("imageinfo", [{}])[0]
                if p["title"] in seen or ii.get("mime") != "image/jpeg":
                    continue
                meta = ii.get("extmetadata", {})
                lic = meta.get("LicenseShortName", {}).get("value", "").lower()
                if not any(k in lic for k in OK_LIC):
                    continue
                if ii.get("width", 0) < 2000 or ii.get("height", 0) < 1300:
                    continue
                seen.add(p["title"])
                cands.append(dict(title=p["title"], query=q, license=meta.get("LicenseShortName", {}).get("value"),
                                  page=ii.get("descriptionurl"), url=ii.get("thumburl") or ii["url"],
                                  w=ii["width"], h=ii["height"]))
        print(q, "->", len(cands), file=sys.stderr)
    cands = cands[:60]
    thumbs = []
    for i, c in enumerate(cands, 1):
        path = os.path.join(OUT, f"f{i:02d}.jpg")
        try:
            req = urllib.request.Request(c["url"], headers=UA)
            with urllib.request.urlopen(req, timeout=120) as r, open(path, "wb") as f:
                f.write(r.read())
            im = Image.open(path).convert("RGB")
            c["file"] = f"f{i:02d}.jpg"
            th = im.copy()
            th.thumbnail((360, 240))
            thumbs.append((c["file"], th))
        except Exception as e:
            print("échec", c["title"], e, file=sys.stderr)
    cols = 6
    rows = (len(thumbs) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * 370, rows * 275), (20, 20, 20))
    dr = ImageDraw.Draw(sheet)
    for k, (name, th) in enumerate(thumbs):
        x, y = (k % cols) * 370 + 5, (k // cols) * 275 + 5
        sheet.paste(th, (x, y))
        dr.text((x + 4, y + 244), name, fill=(255, 255, 0), font=ImageFont.load_default(22))
    sheet.save(os.path.join(OUT, "planche.jpg"), quality=85)
    json.dump([c for c in cands if "file" in c], open("coran/fonds_candidats.json", "w"), indent=1, ensure_ascii=False)
    print("candidats :", len(thumbs))


if __name__ == "__main__":
    main()
