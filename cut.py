# /// script
# requires-python = ">=3.10,<3.13"
# dependencies = ["faster-whisper", "imageio-ffmpeg", "librosa"]
# ///
"""Talking-Head-Cutter: Pausen raus, inhaltlich kürzen, Zooms, 9:16, Wort-Untertitel.

    uv run cut.py video.mp4                       -> video_cut.mp4 + video_cut.words.tsv
    uv run cut.py video.mp4 --drop "12.3-15.8,40-44"   Stellen rausschneiden (Sekunden im Original)
    uv run cut.py video.mp4 --words video_cut.words.tsv   korrigierte Wörter nutzen statt neu zu transkribieren
    uv run cut.py video.mp4 --title "0-3=5 Tage Zeit" --flash "12.3,40" --music beat.mp3
    Abschalten: --no-subs  --no-zoom  --no-vertical
"""
import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ZOOM = 1.12


def keep_segments(removed, duration):
    """Dreht die zu entfernenden Abschnitte (dürfen sich überlappen) in die Abschnitte um, die bleiben."""
    keep, pos = [], 0.0
    for start, end in sorted(removed):
        b = min(start, duration)
        if b - pos > 0.05:
            keep.append((pos, b))
        pos = max(pos, end)
    if duration - pos > 0.05:
        keep.append((pos, duration))
    return keep


def remap(t, keep):
    """Zeit im Original -> Zeit im geschnittenen Video (Zeiten in Lücken rutschen an den Schnitt)."""
    out = 0.0
    for a, b in keep:
        if t < b:
            return out + max(t - a, 0)
        out += b - a
    return out


def zoom_spans(boundaries, total, min_len=2.5):
    """Jede zweite Einstellung wird gezoomt; eine Einstellung endet an Schnitt oder Satzende, frühestens nach min_len."""
    spans, start, zoomed = [], 0.0, False
    for t in sorted(boundaries) + [total]:
        if t - start >= min_len or t == total:
            if zoomed and t > start:
                spans.append((start, t))
            start, zoomed = t, not zoomed
    return spans


assert keep_segments([(1, 2)], 5) == [(0.0, 1), (2, 5)]
assert keep_segments([(3, 4), (1, 3.5), (2, 3)], 5) == [(0.0, 1), (4, 5)]
assert keep_segments([(0, 5)], 5) == [] and keep_segments([], 5) == [(0.0, 5)]
assert [remap(t, [(0, 1), (2, 5)]) for t in (0.5, 1.5, 3, 9)] == [0.5, 1.0, 2.0, 4.0]
assert zoom_spans([3, 4, 8], 10) == [(3, 8)]


def run(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", **kw)
    if r.returncode:
        sys.exit(f"ffmpeg-Fehler:\n{r.stderr[-1500:]}")
    return r.stderr


def detect_silence(ff, src, noise, min_silence):
    err = run([ff, "-hide_banner", "-i", src, "-vn", "-af",
               f"silencedetect=noise={noise}dB:d={min_silence}", "-f", "null", "-"])
    h, m, s = re.search(r"Duration: (\d+):(\d+):([\d.]+)", err).groups()
    duration = int(h) * 3600 + int(m) * 60 + float(s)
    starts = [max(float(x), 0) for x in re.findall(r"silence_start: (-?[\d.]+)", err)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", err)]
    ends += [duration] * (len(starts) - len(ends))  # Stille bis zum Dateiende hat kein silence_end
    return list(zip(starts, ends)), duration


def transcribe(ff, src, model):
    import numpy as np
    from faster_whisper import WhisperModel

    # Ton selbst per ffmpeg dekodieren: faster-whisper's PyAV-Aufruf bricht mit neuen PyAV-Versionen
    pcm = subprocess.run([ff, "-v", "error", "-i", src, "-f", "f32le", "-ac", "1", "-ar", "16000", "-"],
                         capture_output=True, check=True).stdout
    segments, _ = WhisperModel(model, device="cpu", compute_type="int8").transcribe(
        np.frombuffer(pcm, np.float32), word_timestamps=True)
    return [(w.start, w.end, w.word.strip()) for s in segments for w in s.words]


def read_words(path):
    rows = [line.split("\t") for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
    return [(float(a), float(b), w.strip()) for a, b, w in rows]


def snap(t, beats, tol=0.35):
    """Zeit auf den nächsten Beat ziehen, wenn einer nah genug liegt."""
    near = min(beats, key=lambda b: abs(b - t), default=t)
    return near if abs(near - t) <= tol else t


assert snap(1.2, [1.0, 2.0]) == 1.0 and snap(1.5, [1.0, 2.0]) == 1.5 and snap(1, []) == 1


def detect_beats(ff, music, total):
    """Tempo und Beat-Zeiten der Musik über die Länge des fertigen Videos (Musik läuft in Schleife)."""
    import librosa
    import numpy as np

    pcm = subprocess.run([ff, "-v", "error", "-stream_loop", "-1", "-i", music, "-t", f"{total:.2f}",
                          "-f", "f32le", "-ac", "1", "-ar", "22050", "-"], capture_output=True, check=True).stdout
    tempo, frames = librosa.beat.beat_track(y=np.frombuffer(pcm, np.float32), sr=22050)
    return float(np.atleast_1d(tempo)[0]), [float(t) for t in librosa.frames_to_time(frames, sr=22050)]


def write_ass(words, titles, path, vertical):
    """Untertitel: je 3 Wörter (oder bis Satzende), das gesprochene Wort gelb. Titel: gelbe Karte oben, poppt auf."""
    # shortcut: ohne --no-vertical wird 1080x1920 angenommen, sonst 1920x1080;
    # bei anderen Formaten skaliert libass, die Schrift wirkt dann etwas größer/kleiner.
    w, h, size, margin = (1080, 1920, 84, 400) if vertical else (1920, 1080, 60, 80)
    t_size, t_margin = (92, 260) if vertical else (70, 60)

    def ts(t):
        cs = round(t * 100)
        return f"{cs // 360000}:{cs // 6000 % 60:02}:{cs // 100 % 60:02}.{cs % 100:02}"

    chunks, cur = [], []
    for word in words:
        cur.append(word)
        if len(cur) == 3 or word[2][-1:] in ".!?":
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)

    lines = ["[Script Info]", "ScriptType: v4.00+", f"PlayResX: {w}", f"PlayResY: {h}", "",
             "[V4+ Styles]",
             "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, BorderStyle, "
             "Outline, Shadow, Alignment, MarginL, MarginR, MarginV",
             f"Style: Default,Arial,{size},&H00FFFFFF,&H00000000,&H00000000,1,1,6,0,2,60,60,{margin}",
             # BorderStyle 3 = Kasten in der Outline-Farbe (gelb), Schrift schwarz, oben mittig
             f"Style: Title,Arial,{t_size},&H00000000,&H0000E5FF,&H00000000,1,3,14,0,8,60,60,{t_margin}", "",
             "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    for chunk in chunks:
        for i, (start, end, _) in enumerate(chunk):
            stop = chunk[i + 1][0] if i + 1 < len(chunk) else end
            if stop <= start:
                continue
            text = " ".join(f"{{\\c&H00FFFF&}}{t}{{\\c&HFFFFFF&}}" if j == i else t
                            for j, (_, _, t) in enumerate(chunk))
            lines.append(f"Dialogue: 0,{ts(start)},{ts(stop)},Default,,0,0,0,,{text}")
    for start, end, text in titles:
        pop = "{\\fad(100,200)\\fscx70\\fscy70\\t(0,160,\\fscx100\\fscy100)}"
        lines.append(f"Dialogue: 1,{ts(start)},{ts(end)},Title,,0,0,0,,{pop}{text}")
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def spans_expr(spans):
    return "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in spans)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("video")
    p.add_argument("-o", "--out")
    p.add_argument("--drop", default="", help='Stellen rausschneiden, Sekunden im Original: "12.3-15.8,40-44"')
    p.add_argument("--words", help="korrigierte .words.tsv nutzen statt neu zu transkribieren")
    p.add_argument("--title", action="append", default=[],
                   help='Einblendung oben, Sekunden im Original: "0-3=Text" (mehrfach möglich)')
    p.add_argument("--flash", default="", help='Blitz-Übergang an Themenwechseln, Sekunden im Original: "12.3,40"')
    p.add_argument("--music", help="Musikdatei: läuft leise unter der Stimme, Zooms und Blitze rasten auf den Beat ein")
    p.add_argument("--music-volume", type=float, default=0.2, help="Lautstärke der Musik (Standard 0.2)")
    p.add_argument("--no-subs", action="store_true")
    p.add_argument("--no-zoom", action="store_true")
    p.add_argument("--no-vertical", action="store_true")
    p.add_argument("--noise", type=float, default=-30, help="Stille-Schwelle in dB (Standard -30)")
    p.add_argument("--min-silence", type=float, default=0.5, help="kürzeste Pause in s (Standard 0.5)")
    p.add_argument("--model", default="small", help="Whisper-Modell (tiny/base/small/medium)")
    a = p.parse_args()

    src = Path(a.video).resolve()
    if not src.is_file():
        sys.exit(f"Datei nicht gefunden: {src}")
    out = Path(a.out).resolve() if a.out else src.with_name(src.stem + "_cut.mp4")
    if out == src:
        sys.exit("Ausgabe würde das Original überschreiben, bitte -o angeben.")
    try:
        drops = [tuple(float(x) for x in part.split("-")) for part in a.drop.split(",") if part.strip()]
        assert all(len(d) == 2 and d[0] < d[1] for d in drops)
    except (ValueError, AssertionError):
        sys.exit('--drop braucht die Form "12.3-15.8,40-44" (Start kleiner als Ende).')
    try:
        titles = []
        for item in a.title:
            times, text = item.split("=", 1)
            start, end = (float(x) for x in times.split("-"))
            titles.append((start, end, text.strip()))
        flashes = [float(x) for x in a.flash.split(",") if x.strip()]
    except ValueError:
        sys.exit('--title braucht die Form "0-3=Text", --flash die Form "12.3,40".')
    music = Path(a.music).resolve() if a.music else None
    if music and not music.is_file():
        sys.exit(f"Musikdatei nicht gefunden: {music}")

    import imageio_ffmpeg
    ff = imageio_ffmpeg.get_ffmpeg_exe()

    # Wörter stehen in Original-Zeit: so bleiben sie gültig, egal wie danach geschnitten wird.
    if a.words:
        words = read_words(a.words)
    else:
        words = transcribe(ff, str(src), a.model)
        tsv = out.with_suffix(".words.tsv")
        tsv.write_text("".join(f"{s:.2f}\t{e:.2f}\t{w}\n" for s, e, w in words), encoding="utf-8")
        print(f"{len(words)} Wörter transkribiert: {tsv}")

    pad = 0.1  # etwas Luft an jedem Pausen-Schnitt, damit keine Wortenden fehlen
    silences, duration = detect_silence(ff, str(src), a.noise, a.min_silence)
    keep = keep_segments([(s + pad, e - pad) for s, e in silences if e - s > 2 * pad] + drops, duration)
    if not keep:
        sys.exit("Es bleibt nichts übrig. --noise niedriger setzen (z. B. -40) oder --drop prüfen.")
    total = sum(b - x for x, b in keep)
    print(f"{len(silences)} Pausen, {len(drops)} Stellen entfernt: {duration:.1f}s -> {total:.1f}s")

    # Nur Wörter behalten, deren Mitte im fertigen Video liegt; Zeiten auf das fertige Video umrechnen.
    kept_words = [(remap(s, keep), remap(e, keep), w) for s, e, w in words
                  if any(x <= (s + e) / 2 < b for x, b in keep)]

    beats = []
    if music:
        bpm, beats = detect_beats(ff, str(music), total)
        print(f"Musik: {bpm:.0f} BPM, {len(beats)} Beats")
    kept_titles = [(s, e, t) for s, e, t in ((remap(s, keep), remap(e, keep), t) for s, e, t in titles) if e > s]
    flash_times = [snap(remap(t, keep), beats) for t in flashes]

    select = spans_expr(keep)
    graph = f"[0:v]select='{select}',setpts=N/FRAME_RATE/TB"
    if not a.no_vertical:
        graph += ",crop='min(iw,ih*9/16)':'min(ih,iw*16/9)',scale=1080:1920"
    if not a.no_zoom:
        cuts = [remap(b, keep) for _, b in keep[:-1]]
        # Schnitte bleiben exakt; nur die Zoom-Wechsel an Satzenden rasten auf den Beat ein
        sentence_ends = [snap(e, beats) for _, e, w in kept_words if w[-1:] in ".!?"]
        zooms = zoom_spans(cuts + sentence_ends, total)
        if zooms:
            print("Zooms: " + ", ".join(f"{x:.1f}-{b:.1f}" for x, b in zooms))
            # größere Kopie mittig (leicht nach oben, zum Gesicht) über das Bild legen, nur in den Zoom-Spannen
            graph += (f",split[base][big];[big]scale=iw*{ZOOM}:ih*{ZOOM}[zoomed];"
                      f"[base][zoomed]overlay=(W-w)/2:(H-h)*0.4:enable='{spans_expr(zooms)}'")

    if flash_times:
        print("Blitze: " + ", ".join(f"{t:.1f}" for t in flash_times))
        # kurzer heller Blitz (0,24 s) als Übergang: Helligkeit steigt zum Zeitpunkt und fällt wieder
        bright = "+".join(f"0.5*max(0,1-abs(t-{t:.3f})/0.12)" for t in flash_times)
        graph += f",eq=brightness='min(1,{bright})':eval=frame"

    fade = f"fade=t=out:st={max(total - 0.4, 0):.3f}:d=0.4"  # weiches Ende statt hartem Abbruch
    with tempfile.TemporaryDirectory() as tmp:
        sub_words = [] if a.no_subs else kept_words
        if sub_words or kept_titles:
            write_ass(sub_words, kept_titles, Path(tmp) / "subs.ass", not a.no_vertical)
            graph += ",subtitles=subs.ass"  # cwd=tmp: so braucht der Filter keinen escapten Windows-Pfad
        graph += f",{fade}[v];[0:a]aselect='{select}',asetpts=N/SR/TB"
        inputs = ["-i", str(src)]
        if music:
            inputs += ["-stream_loop", "-1", "-i", str(music)]
            # Musik wird leiser, sobald gesprochen wird (Stimme steuert den Kompressor)
            graph += (f",asplit[voice][key];[1:a]volume={a.music_volume}[m];"
                      "[m][key]sidechaincompress=threshold=0.05:ratio=3:attack=20:release=400[duck];"
                      "[voice][duck]amix=inputs=2:duration=first:normalize=0")
        graph += f",a{fade}[au]"
        # shortcut: alle Schnitte stehen in einem Filterausdruck (Windows-Limit ~1000 Schnitte),
        # für stundenlange Videos auf eine Filter-Datei umstellen.
        run([ff, "-y", "-hide_banner", *inputs, "-filter_complex", graph, "-map", "[v]", "-map", "[au]",
             "-t", f"{total:.3f}", "-c:v", "libx264", "-crf", "20", "-preset", "fast", "-c:a", "aac", str(out)],
            cwd=tmp)
    print(f"Fertig: {out}")


if __name__ == "__main__":
    main()
