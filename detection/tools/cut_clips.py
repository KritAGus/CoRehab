"""Cut long rehab recordings into labelled clips, from the labeler's segments.csv.

Usage (Colab or any machine with ffmpeg):
    python cut_clips.py segments.csv --videos /path/to/videos --out /path/to/clips

Writes:
    <out>/correct/<clip>.mp4     takes marked correct
    <out>/wrong/<clip>.mp4       takes marked wrong
    <out>/labels.csv             one row per clip: the slate plus the clip's path
Clips are re-encoded (H.264, CRF 18) so the cut lands on the exact frame.
"""
import argparse, csv, os, shutil, subprocess, sys

ap = argparse.ArgumentParser()
ap.add_argument("segments")
ap.add_argument("--videos", default=".", help="folder holding the original videos")
ap.add_argument("--out", default="clips")
ap.add_argument("--pad", type=float, default=0.0, help="extra seconds kept before and after each take")
ap.add_argument("--keep-audio", action="store_true", help="keep sound (off by default: voices stay out of the dataset)")
a = ap.parse_args()

if not shutil.which("ffmpeg"):
    sys.exit("ffmpeg not found. In Colab it is already installed; elsewhere install it first.")

rows = list(csv.DictReader(open(a.segments, newline="", encoding="utf-8")))
os.makedirs(a.out, exist_ok=True)
done, missing, failed, out_rows = 0, set(), [], []
for r in rows:
    src = os.path.join(a.videos, r["video"])
    if not os.path.exists(src):
        missing.add(r["video"]); continue
    start = max(0.0, float(r["start_s"]) - a.pad)
    end = float(r["end_s"]) + a.pad
    folder = os.path.join(a.out, "correct" if r["form"] == "correct" else "wrong")
    os.makedirs(folder, exist_ok=True)
    dst = os.path.join(folder, r["clip"] + ".mp4")
    tmp = dst[:-4] + ".part.mp4"
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{start:.3f}", "-i", src, "-t", f"{end - start:.3f}",
           "-c:v", "libx264", "-crf", "18", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
    cmd += ["-c:a", "aac"] if a.keep_audio else ["-an"]
    if subprocess.run(cmd + [tmp]).returncode == 0:
        os.replace(tmp, dst); done += 1
        out_rows.append({**r, "start_s": f"{start:.2f}", "end_s": f"{end:.2f}", "path": os.path.relpath(dst, a.out)})
    else:
        failed.append(r["clip"])
        if os.path.exists(tmp): os.remove(tmp)

if out_rows:
    with open(os.path.join(a.out, "labels.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(out_rows[0].keys()))
        w.writeheader(); w.writerows(out_rows)
print(f"Cut {done} of {len(rows)} clips into {a.out}/")
if missing: print("Videos not found (check --videos):", ", ".join(sorted(missing)))
if failed: print("ffmpeg failed on:", ", ".join(failed))
