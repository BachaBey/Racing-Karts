"""Ghost ("saved player") recordings.

While a race runs the server samples every human kart 10x per second.  When a
human finishes, the run is saved; the best runs per (track, laps) are kept and
can be replayed as ghost opponents, so a single player can race against
previously saved players.  If no human run exists yet, CPU runs are generated
by simulating the AI driver so solo play always has opponents.
"""
import json
import os
import threading

from common import config as C
from common.physics import AIDriver, KartPhysics
from common.track import Track

SAMPLE_DT = 0.1
KEEP_PER_TRACK = 6


class GhostStore:
    def __init__(self, folder):
        self.folder = folder
        self.lock = threading.Lock()
        os.makedirs(folder, exist_ok=True)

    def _path(self, track, laps):
        return os.path.join(self.folder, f"{track}_{laps}laps.json")

    def load(self, track, laps):
        p = self._path(track, laps)
        with self.lock:
            if os.path.exists(p):
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        return json.load(f)
                except (OSError, ValueError):
                    pass
        return []

    def save_run(self, track, laps, user, finish_time, samples):
        if len(samples) < 10:
            return
        runs = self.load(track, laps)
        runs.append({"user": user, "time": round(finish_time, 3), "samples": samples})
        runs.sort(key=lambda r: r["time"])
        # keep the best runs, but at most 2 per user so ghosts stay varied
        kept, per_user = [], {}
        for r in runs:
            if per_user.get(r["user"], 0) >= 2:
                continue
            per_user[r["user"]] = per_user.get(r["user"], 0) + 1
            kept.append(r)
            if len(kept) >= KEEP_PER_TRACK:
                break
        with self.lock:
            tmp = self._path(track, laps) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(kept, f, separators=(",", ":"))
            os.replace(tmp, self._path(track, laps))

    def pick(self, track, laps, n):
        runs = self.load(track, laps)
        if len(runs) < n:
            runs = runs + self.cpu_runs(track, laps, n - len(runs))
        return runs[:n]

    _cpu_cache = {}

    def cpu_runs(self, track_key, laps, n):
        skills = [(0.97, -0.12, "CPU_Hard"), (0.90, 0.15, "CPU_Medium"), (0.82, 0.0, "CPU_Easy")]
        out = []
        for skill, lane, name in skills[:n]:
            key = (track_key, laps, name)
            if key not in self._cpu_cache:
                samples = simulate_run(Track(track_key), laps, skill, lane)
                self._cpu_cache[key] = {"user": name, "time": samples[-1][0], "cpu": True, "samples": samples}
            out.append(self._cpu_cache[key])
        return out


def simulate_run(track, laps, skill, lane, slot=5):
    x, y, a = track.grid_slot(slot)
    kart = KartPhysics(x, y, a)
    ai = AIDriver(track, skill, lane)
    dt = 1.0 / 30
    t = 0.0
    prog, hint, _ = track.progress(x, y)
    total = prog - 1.0 if prog > 0.5 else prog
    samples = [[0.0, round(x, 1), round(y, 1), round(a, 3)]]
    next_sample = SAMPLE_DT
    while total < laps and t < C.RACE_HARD_LIMIT:
        th, br, st = ai.control(kart)
        kart.step(dt, th, br, st, track, speed_factor=skill)
        t += dt
        p, hint, _ = track.progress(kart.x, kart.y, hint)
        d = p - prog
        if d < -0.5:
            d += 1
        elif d > 0.5:
            d -= 1
        total += d
        prog = p
        if t >= next_sample:
            samples.append([round(t, 2), round(kart.x, 1), round(kart.y, 1), round(kart.angle, 3)])
            next_sample += SAMPLE_DT
    samples.append([round(t, 2), round(kart.x, 1), round(kart.y, 1), round(kart.angle, 3)])
    return samples
