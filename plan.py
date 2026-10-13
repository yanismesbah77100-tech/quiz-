#!/usr/bin/env python3
"""Ajoute N nouvelles vidéos à queue.json en piochant des questions jamais utilisées.

Chaque vidéo : 2 questions faciles, 2 moyennes, 1 difficile (dans cet ordre).
Usage : python3 plan.py 3      -> affiche les noms des vidéos ajoutées
        python3 plan.py --stock -> affiche le nombre de questions restantes par niveau
"""
import json
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PATTERN = [1, 1, 2, 2, 3]


def load():
    bank = json.load(open(os.path.join(HERE, "questions.json"), encoding="utf-8"))
    qpath = os.path.join(HERE, "queue.json")
    queue = json.load(open(qpath)) if os.path.exists(qpath) else {"videos": []}
    used = {i for v in queue["videos"] for i in v["ids"]}
    return bank, queue, used, qpath


def stock():
    bank, _, used, _ = load()
    return {lvl: sum(1 for q in bank if q["lvl"] == lvl and q["id"] not in used) for lvl in (1, 2, 3)}


def main():
    if sys.argv[1:] == ["--stock"]:
        print(json.dumps(stock()))
        return
    n = int(sys.argv[1])
    bank, queue, used, qpath = load()
    rnd = random.Random(len(used))
    pools = {lvl: [q["id"] for q in bank if q["lvl"] == lvl and q["id"] not in used] for lvl in (1, 2, 3)}
    for p in pools.values():
        rnd.shuffle(p)
    num = max([int(v["name"][1:]) for v in queue["videos"]] or [0])
    added = []
    for _ in range(n):
        need = {lvl: PATTERN.count(lvl) for lvl in (1, 2, 3)}
        if any(len(pools[l]) < c for l, c in need.items()):
            sys.exit("Plus assez de questions : ajoute-en dans questions.json (stock : %s)" % json.dumps(stock()))
        ids = [pools[l].pop() for l in PATTERN]
        num += 1
        name = f"v{num:04d}"
        queue["videos"].append({"name": name, "ids": ids})
        added.append(name)
    with open(qpath, "w") as f:
        json.dump(queue, f, indent=1)
        f.write("\n")
    print(" ".join(added))


if __name__ == "__main__":
    main()
