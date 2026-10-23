#!/usr/bin/env python3
"""Ajoute N vidéos Coran à coran/queue.json en prenant les passages de coran/passages.json
pas encore utilisés (ni dans la file, ni dans coran/published.json).

Usage : python3 coran/plan.py 1        -> affiche le(s) nom(s) ajouté(s)
        python3 coran/plan.py --next   -> prochaine vidéo prête (en ligne, jamais publiée)
        python3 coran/plan.py --stock  -> nombre de passages encore jamais utilisés
"""
import json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
P = lambda f: os.path.join(HERE, f)


def load(f, default):
    return json.load(open(P(f), encoding="utf-8")) if os.path.exists(P(f)) else default


def main():
    queue = load("queue.json", {"videos": []})
    pub = load("published.json", [])
    index = load("index.json", {})
    passages = load("passages.json", [])
    key = lambda x: (x["surah"], x["from"], x["to"])
    used = {key(v) for v in queue["videos"]} | {key(p) for p in pub if "surah" in p}
    published_names = {p["video"] for p in pub}
    free = [p for p in passages if key(p) not in used]
    arg = sys.argv[1] if len(sys.argv) > 1 else "--next"
    if arg == "--stock":
        ready = [v["name"] for v in queue["videos"] if v["name"] in index and v["name"] not in published_names]
        print(json.dumps({"pretes_non_publiees": ready, "passages_libres": len(free)}))
        return
    if arg == "--next":
        for v in queue["videos"]:
            if v["name"] in index and v["name"] not in published_names:
                print(json.dumps(v)); return
        print("aucune"); return
    n = int(arg)
    num = max([int(v["name"][1:]) for v in queue["videos"]] + [int(p["video"][1:]) for p in pub] + [int(k[1:]) for k in index] + [0])
    added = []
    for p in free[:n]:
        num += 1
        name = f"c{num:04d}"
        queue["videos"].append({"name": name, "surah": p["surah"], "from": p["from"], "to": p["to"], "mode": "recit", **({"reciter": p["reciter"]} if p.get("reciter") else {})})
        added.append(name)
    if len(added) < n:
        sys.exit("Plus assez de passages dans coran/passages.json")
    json.dump(queue, open(P("queue.json"), "w"), indent=1); open(P("queue.json"), "a").write("\n")
    print(" ".join(added))


if __name__ == "__main__":
    main()
