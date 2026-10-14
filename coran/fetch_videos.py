#!/usr/bin/env python3
"""Cherche des vidéos de paysages libres de droits (CC0 / domaine public) sur
Wikimedia Commons, les télécharge (doucement, pour respecter leurs limites) et
fabrique une planche contact (une image tirée du milieu de chaque vidéo).

Sortie : out_vfonds/v<n>.webm, out_vfonds/planche.jpg, coran/vfonds_candidats.json
"""
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import urllib.error

from PIL import Image, ImageDraw, ImageFont

API = "https://commons.wikimedia.org/w/api.php"
UA = {"User-Agent": "quiz-ou-pas-video-bot/1.0 (github.com/yanismesbah77100-tech/quiz-; contact via GitHub)"}
QUERIES = ["waterfall", "forest", "river", "stream forest", "lake mountains", "sunset", "sunrise",
           "mountains", "sea", "beach", "desert dunes", "snow mountains", "aurora", "meadow flowers", "rain forest"]
PREFIX = os.environ.get("VF_PREFIX", "n")  # nouveaux noms : ne jamais écraser les vidéos déjà utilisées (v01…)
OK_LIC = ("cc0", "public domain", "pd")
OUT = "out_vfonds"
LOG = open("coran/vfonds_log.txt", "w")


def log(*a):
    print(*a, file=LOG, flush=True)
    print(*a, file=sys.stderr)


def get(params):
    url = API + "?" + urllib.parse.urlencode(params)
    for k in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
                return json.load(r)
        except Exception as e:
            log("API erreur, pause", repr(e))
            time.sleep(20 * (k + 1))
    return {}


def main():
    os.makedirs(OUT, exist_ok=True)
    old = json.load(open("coran/vfonds_candidats.json")) if os.path.exists("coran/vfonds_candidats.json") else []
    seen, cands = {c["title"] for c in old}, []
    for q in QUERIES:
        for cat in ("CC-Zero", "PD-self"):
            time.sleep(3)
            d = get({"action": "query", "format": "json", "generator": "search", "gsrnamespace": 6,
                     "gsrsearch": f'{q} incategory:"{cat}" filetype:video', "gsrlimit": 10,
                     "prop": "imageinfo|videoinfo", "iiprop": "url|size|extmetadata|mime",
                     "viprop": "derivatives"})
            pages = (d.get("query", {}) or {}).get("pages", {}) or {}
            log("requête", q, cat, "->", len(pages))
            for p in pages.values():
                ii = (p.get("imageinfo") or [{}])[0]
                if p["title"] in seen:
                    continue
                meta = ii.get("extmetadata", {})
                lic = meta.get("LicenseShortName", {}).get("value", "").lower()
                if not any(k in lic for k in OK_LIC):
                    continue
                if ii.get("height", 0) < 700 or ii.get("duration", 99) < 8:
                    continue
                # préférer une version transcodée 1080p ou 720p (plus légère)
                url = ii.get("url")
                for dv in (p.get("videoinfo") or [{}])[0].get("derivatives", []) or []:
                    if dv.get("transcodekey", "") in ("1080p.vp9.webm", "720p.vp9.webm", "1080p.webm", "720p.webm"):
                        url = dv["src"]
                        break
                seen.add(p["title"])
                cands.append(dict(title=p["title"], query=q, license=meta.get("LicenseShortName", {}).get("value"),
                                  page=ii.get("descriptionurl"), url=url, w=ii.get("width"), h=ii.get("height")))
    log("candidats :", len(cands))
    cands = cands[:25]
    thumbs = []
    for i, c in enumerate(cands, 1):
        path = os.path.join(OUT, f"{PREFIX}{i:02d}.webm")
        time.sleep(15)
        try:
            for k in range(5):
                try:
                    req = urllib.request.Request(c["url"], headers=UA)
                    with urllib.request.urlopen(req, timeout=300) as r, open(path, "wb") as f:
                        f.write(r.read())
                    break
                except urllib.error.HTTPError as e:
                    if e.code != 429 or k == 4:
                        raise
                    log("429, pause", 60 * (k + 1), "s")
                    time.sleep(60 * (k + 1))
            dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                                       capture_output=True, text=True).stdout.strip() or 0)
            jpg = os.path.join(OUT, f"{PREFIX}{i:02d}.jpg")
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(dur / 2), "-i", path, "-frames:v", "1", jpg], check=True)
            th = Image.open(jpg).convert("RGB")
            th.thumbnail((360, 240))
            c.update(file=f"{PREFIX}{i:02d}.webm", duration=round(dur, 1))
            thumbs.append((f"{PREFIX}{i:02d} ({dur:.0f}s)", th))
        except Exception as e:
            log("échec", c["title"], repr(e))
    if not thumbs:
        log("aucune vidéo")
        sys.exit(1)
    cols = 5
    rows = (len(thumbs) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * 370, rows * 275), (20, 20, 20))
    dr = ImageDraw.Draw(sheet)
    for k, (name, th) in enumerate(thumbs):
        x, y = (k % cols) * 370 + 5, (k // cols) * 275 + 5
        sheet.paste(th, (x, y))
        dr.text((x + 4, y + 244), name, fill=(255, 255, 0), font=ImageFont.load_default(22))
    sheet.save(os.path.join(OUT, f"planche_{PREFIX}.jpg"), quality=85)
    json.dump(old + [c for c in cands if "file" in c], open("coran/vfonds_candidats.json", "w"), indent=1, ensure_ascii=False)


if __name__ == "__main__":
    main()
