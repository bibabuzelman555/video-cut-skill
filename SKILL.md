---
name: video-cut
description: Schneidet Talking-Head-Videos zu fertigen Reels/Shorts (Pausen raus, inhaltlich kürzen, Zooms, 9:16, Wort-Untertitel, Titelkarten, Blitz-Übergänge, Musik auf den Beat) und prüft das Ergebnis selbst. Nutzen bei "schneide dieses Video", "mach ein Reel/Short/TikTok daraus", "Untertitel einbrennen", "Video cutten".
---

# Video-Cut

Du lieferst ein fertiges, selbst geprüftes Video ab. Nicht "Befehl lief durch", sondern "ich habe das Ergebnis angesehen".

Werkzeug: `cut.py` im Ordner dieses Skills (normalerweise `~/.claude/skills/video-cut/cut.py`; unten steht dafür `<cut.py>`). Aufruf immer mit `uv run`, Abhängigkeiten lädt uv selbst. Voraussetzung: uv ist installiert.
CapCut wird nicht ferngesteuert: Es hat keine Schnittstelle. Das Ergebnis ist eine mp4, die der Nutzer dort weiterbearbeiten kann.

## Ablauf

1. **Klären** (nur was fehlt, als anklickbare Optionen): Welche Datei? Hochkant oder Originalformat? Untertitel und Zooms ja/nein? Zielänge?
2. **Erster Lauf:**
   `uv run "<cut.py>" "<video>" -o "<ausgabe>.mp4"`
   Nie das Original überschreiben. Bei Videos über 2 Minuten im Hintergrund starten.
   Die Ausgabe nennt Pausen, Länge vorher/nachher und die Zoom-Spannen. Daneben entsteht `<ausgabe>.words.tsv`.
3. **Wortliste lesen** (ganz): Jede Zeile ist `Start<TAB>Ende<TAB>Wort`, Zeiten in Sekunden **im Original**.
   - **Wörter korrigieren:** falsch gehörte Namen, Fachwörter, Zahlen, Rechtschreibung. Zerrissene Wörter zusammenführen (z. B. `5` und `.2` zu einer Zeile `5.2` mit Start der ersten und Ende der zweiten). Zeiten sonst nicht ändern.
   - **Inhaltlich kürzen:** Suche Versprecher, doppelt gesagte Sätze, Füllsätze und Neuansätze. Eine Stelle reicht vom Start des ersten überflüssigen Worts bis zum Start des ersten Worts, das bleiben soll. Schneide nur ganze Sinneinheiten, nie mitten im Satz, und lass den ersten Satz (Hook) stehen, außer er ist doppelt.
4. **Zweiter Lauf** mit der korrigierten Liste und den Stellen:
   `uv run ... "<video>" -o "<ausgabe>.mp4" --words "<ausgabe>.words.tsv" --drop "12.3-15.8,40-44"`
   `--words` spart die erneute Transkription. Die Zeiten bleiben gültig, auch wenn du `--drop`, `--noise` oder `--min-silence` änderst.
   **Gestaltung** (im selben Lauf, alle Zeiten in Sekunden im Original):
   - `--title "0-3=TEXT"`: gelbe Titelkarte oben, mehrfach möglich. Setze immer eine über den Hook (erste 3 Sekunden, höchstens 4 Wörter, Großbuchstaben). Weitere nur bei echten Abschnitten ("TIPP 1"), nicht öfter als alle 8 Sekunden.
   - `--flash "12.3,40"`: kurzer heller Blitz als Übergang. Nur an Themenwechseln, höchstens 3 bis 4 pro Minute.
   - `--music "<datei>"`: Musik läuft unter der Stimme und wird beim Sprechen leiser. Das Werkzeug misst Tempo und Beats; Zoom-Wechsel und Blitze rasten auf den nächsten Beat ein (bis 0,35 s Abstand). Lautstärke mit `--music-volume` (Standard 0.2). Die Musikdatei muss der Nutzer liefern; frag nach, ob er sie verwenden darf, wenn er das Video veröffentlichen will.
   - Das Ende wird immer 0,4 s ausgeblendet.
5. **Zahlen prüfen:**
   - Mit Musik: Die Ausgabe nennt BPM und Beat-Zahl. Liegt das Tempo unter 60 oder über 200, ist die Erkennung vermutlich falsch; sag es.
   - Fast nichts geschnitten, obwohl Pausen da sind: `--min-silence 0.3`, bei Hintergrundgeräusch `--noise -25`.
   - Wörter fehlen am Schnitt: `--min-silence 0.7` oder `--noise -35`.
6. **Bilder ansehen:** Ziehe 4 Einzelbilder, davon mindestens eines innerhalb und eines außerhalb einer Zoom-Spanne, und sieh sie dir mit Read an:
   `uv run --with imageio-ffmpeg python -c "import imageio_ffmpeg,subprocess,sys;[subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-v','error','-y','-ss',t,'-i',sys.argv[1],'-frames:v','1',f'{sys.argv[2]}_{t}.jpg']) for t in sys.argv[3:]]" "<ausgabe>.mp4" "<scratchpad>\check" 2 15 30 50`
   Nimm dazu ein Bild aus jeder Titelkarte und eines genau auf einem Blitz-Zeitpunkt (die Ausgabe nennt sie).
   Prüfe: Titelkarte vollständig und nicht über dem Gesicht, Gesicht auch im Zoom nicht angeschnitten, Untertitel lesbar und nicht über Mund oder vorhandenem Text, das gelbe Wort passt zum Moment, Format stimmt.
7. **Abgeben:** Pfad der Datei, Länge vorher/nachher, welche Stellen du entfernt und welche Wörter du korrigiert hast, was du nicht prüfen konntest.

## Regeln

- Sag dem Nutzer vor dem zweiten Lauf in einem Satz, welche Stellen du entfernst, wenn zusammen mehr als 10 % der Länge wegfallen.
- Bei Hochkant-Export aus Querformat wird mittig beschnitten. Steht die Person nicht mittig, sag es und nimm `--no-vertical`.
- Hat das Video schon eingebrannte Untertitel, nimm `--no-subs`. Wirkt der Zoom unruhig oder schneidet er das Gesicht an, nimm `--no-zoom`.
- Zooms setzt das Werkzeug selbst (jede zweite Einstellung, Wechsel an Schnitten und Satzenden). Einzelne Zooms lassen sich nicht von Hand setzen.
- Was das Werkzeug nicht kann (B-Roll, Bilder und Logos einblenden, Soundeffekte, weiche Überblendungen zwischen Szenen): nicht behaupten. Sag es und biete an, es einzeln zu bauen, erst nach Zustimmung.
- Ton kannst du nicht hören. Das Werkzeug misst Beats, aber ob Musik und Stimme gut zusammen klingen, kann nur der Nutzer beurteilen. Sag das bei jedem Video mit Musik und nenne `--music-volume` als Stellschraube.
