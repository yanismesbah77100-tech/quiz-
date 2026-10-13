# quiz.ou.pas — vidéos quiz automatiques

Ce dépôt fabrique les vidéos quiz du compte TikTok **@quiz.ou.pas**.

- `questions.json` : la banque de questions (culture générale).
- `generate.py` : fabrique une vidéo verticale (question lue par une voix, 3 choix, compte à rebours de 5 s, révélation de la réponse).
- `queue.json` : les questions à transformer en vidéo. Quand ce fichier change, GitHub Actions génère les vidéos manquantes dans `videos/`.
- `published.json` : l'historique des vidéos programmées sur TikTok (via Metricool), pour ne jamais republier la même question.

Test local sans voix : `python3 generate.py 1 --no-voice`
