"""Game rooms and the authoritative race simulation.

All methods are called with GameServer.lock held (one lock = no data races
between the per-client TCP threads, the UDP thread and the tick thread).
"""
import math
import random
import time

from common import config as C
from common import protocol as P
from common.track import TRACKS, Track, wrap_angle

WAITING, COUNTDOWN, RACING, RESULTS = "WAITING", "COUNTDOWN", "RACING", "RESULTS"


def _unwrap(delta):
    if delta < -0.5:
        return delta + 1.0
    if delta > 0.5:
        return delta - 1.0
    return delta


class RaceKart:
    def __init__(self, session, color, grid, track):
        self.pid = session.pid
        self.name = session.user
        self.session = session
        self.color = color
        self.ghost = False
        self.x, self.y, self.a = grid
        self.speed = 0.0
        self.cflags = 0
        self.item = C.ITEM_NONE
        self.item_count = 0
        self.shield_t = 0.0
        self.invuln_t = 0.0
        self.prog, self.hint, _ = track.progress(self.x, self.y)
        self.total = self.prog - 1.0 if self.prog > 0.5 else self.prog
        self.last_seq = -1
        self.last_state_time = time.time()
        self.finished = False
        self.finish_time = None
        self.lap_start = 0.0
        self.best_lap = None
        self.laps_done = 0
        self.connected = True
        self.leave_reason = ""
        self.samples = []
        self.next_sample = 0.0
        self.rank = 0


class GhostKart:
    def __init__(self, pid, run, color, track):
        self.pid = pid
        self.name = "Ghost " + run["user"] if not run.get("cpu") else run["user"]
        self.color = color
        self.ghost = True
        self.cpu = bool(run.get("cpu"))
        self.samples = run["samples"]
        self.time = run["time"]
        self.idx = 0
        self.x, self.y, self.a = self.samples[0][1], self.samples[0][2], self.samples[0][3]
        self.speed = 0.0
        self.prog, self.hint, _ = track.progress(self.x, self.y)
        self.total = self.prog - 1.0 if self.prog > 0.5 else self.prog
        self.finished = False
        self.finish_time = None
        self.rank = 0
        self.connected = True
        self.shield_t = 0.0

    def update(self, t, track):
        s = self.samples
        while self.idx < len(s) - 2 and s[self.idx + 1][0] <= t:
            self.idx += 1
        a, b = s[self.idx], s[min(self.idx + 1, len(s) - 1)]
        span = (b[0] - a[0]) or 1e-6
        f = max(0.0, min(1.0, (t - a[0]) / span))
        nx = a[1] + (b[1] - a[1]) * f
        ny = a[2] + (b[2] - a[2]) * f
        self.speed = math.hypot(b[1] - a[1], b[2] - a[2]) / span
        self.a = a[3] + wrap_angle(b[3] - a[3]) * f
        self.x, self.y = nx, ny
        p, self.hint, _ = track.progress(nx, ny, self.hint)
        self.total += _unwrap(p - self.prog)
        self.prog = p
        if not self.finished and t >= self.time:
            self.finished = True
            self.finish_time = self.time


class Room:
    def __init__(self, server, rid, name, track, laps, ghosts):
        self.server = server
        self.id = rid
        self.name = (name or f"Room {rid}")[:24]
        self.track_key = track if track in TRACKS else "sunny"
        self.laps = max(1, min(5, int(laps or C.DEFAULT_LAPS)))
        self.ghosts = max(0, min(C.MAX_GHOSTS, int(ghosts or 0)))
        self.members = {}     # pid -> session  (insertion order = join order)
        self.ready = {}       # pid -> bool
        self.colors = {}      # pid -> color index
        self.host = None
        self.status = WAITING
        self.race = None

    # ------------------------------------------------------------- membership
    def summary(self):
        return {"id": self.id, "name": self.name, "track": self.track_key, "laps": self.laps,
                "ghosts": self.ghosts, "players": len(self.members),
                "max": C.MAX_PLAYERS_PER_ROOM, "status": self.status,
                "host": self.members[self.host].user if self.host in self.members else ""}

    def state(self):
        s = self.summary()
        s["host_pid"] = self.host
        s["members"] = [{"pid": pid, "name": ses.user, "ready": self.ready.get(pid, False),
                         "color": self.colors.get(pid, 0), "addr": ses.addr[0]}
                        for pid, ses in self.members.items()]
        return s

    def broadcast(self, typ, **fields):
        for ses in list(self.members.values()):
            self.server.send(ses, typ, **fields)

    def push_state(self):
        self.broadcast(P.S_ROOM_STATE, room=self.state())

    def add(self, session):
        if self.status != WAITING:
            return "A race is in progress in this room"
        if len(self.members) >= C.MAX_PLAYERS_PER_ROOM:
            return "Room is full"
        self.members[session.pid] = session
        self.ready[session.pid] = False
        used = set(self.colors.values())
        self.colors[session.pid] = next(i for i in range(8) if i not in used)
        if self.host is None:
            self.host = session.pid
        session.room = self
        self.push_state()
        return None

    def remove(self, session, reason="left"):
        pid = session.pid
        if pid not in self.members:
            return
        del self.members[pid]
        self.ready.pop(pid, None)
        self.colors.pop(pid, None)
        session.room = None
        if self.race is not None:
            k = self.race["karts"].get(pid)
            if k is not None and k.connected:
                k.connected = False
                k.leave_reason = reason
                self.broadcast(P.S_PLAYER_LEFT, pid=pid, name=session.user, reason=reason)
                self.broadcast(P.S_EVENT, text=f"{session.user} {'disconnected' if reason != 'left' else 'left the race'}")
        if self.host == pid:
            self.host = next(iter(self.members), None)
            if self.host is not None:
                self.broadcast(P.S_EVENT, text=f"{self.members[self.host].user} is now the host")
        if self.members:
            self.push_state()

    def set_ready(self, session, ready):
        if self.status == WAITING and session.pid in self.members:
            self.ready[session.pid] = bool(ready)
            self.push_state()

    def settings(self, session, track=None, laps=None, ghosts=None):
        if session.pid != self.host:
            return "Only the host can change settings"
        if self.status != WAITING:
            return "Cannot change settings during a race"
        if track in TRACKS:
            self.track_key = track
        if laps is not None:
            self.laps = max(1, min(5, int(laps)))
        if ghosts is not None:
            self.ghosts = max(0, min(C.MAX_GHOSTS, int(ghosts)))
        self.push_state()
        return None

    # ------------------------------------------------------------- race setup
    def start(self, session):
        if session.pid != self.host:
            return "Only the host can start the race"
        if self.status != WAITING:
            return "Race already running"
        if not all(self.ready.get(pid) for pid in self.members):
            return "Everyone must be ready"
        n_ghosts = min(self.ghosts, C.MAX_PLAYERS_PER_ROOM - len(self.members))
        if len(self.members) + n_ghosts < 2:
            return "Need at least 2 racers - invite players or add ghosts"
        track = Track(self.track_key)
        karts, grid = {}, []
        for slot, (pid, ses) in enumerate(self.members.items()):
            g = track.grid_slot(slot)
            k = RaceKart(ses, self.colors[pid], g, track)
            karts[pid] = k
            grid.append({"pid": pid, "name": ses.user, "color": k.color, "x": g[0], "y": g[1], "a": g[2],
                         "ghost": False})
        ghosts = []
        for i, run in enumerate(self.server.ghosts.pick(self.track_key, self.laps, n_ghosts)):
            gk = GhostKart(200 + i, run, 6 + i, track)
            ghosts.append(gk)
            grid.append({"pid": gk.pid, "name": gk.name, "color": gk.color, "x": gk.x, "y": gk.y, "a": gk.a,
                         "ghost": True})
        self.race = {
            "track": track, "karts": karts, "ghosts": ghosts,
            "boxes": [0.0] * len(track.box_positions),  # respawn timers (0 = active)
            "objs": {}, "next_obj": 1,
            "start": time.time(), "go_time": time.time() + C.COUNTDOWN_SECONDS,
            "first_finish": None, "snap_seq": 0, "finish_order": 0,
        }
        self.status = COUNTDOWN
        self.broadcast(P.S_RACE_START, track=self.track_key, laps=self.laps, countdown=C.COUNTDOWN_SECONDS,
                       grid=grid, room=self.name)
        self.push_state()
        self.server.log(f"Room {self.id}: race started on {track.title} ({len(karts)} players, {len(ghosts)} ghosts)")
        self.server.lobby_changed()
        return None

    def race_time(self):
        return max(0.0, time.time() - self.race["go_time"])

    # ------------------------------------------------------------- inputs
    def on_state(self, session, d):
        if self.race is None or self.status != RACING:
            return
        k = self.race["karts"].get(session.pid)
        if k is None or not k.connected:
            return
        seq = d["seq"]
        if seq <= k.last_seq and k.last_seq - seq < 1_000_000:
            return  # stale or duplicated UDP datagram -> drop
        k.last_seq = seq
        now = time.time()
        dt = max(0.033, now - k.last_state_time)
        k.last_state_time = now
        jump = math.hypot(d["x"] - k.x, d["y"] - k.y)
        if jump > C.MAX_PLAUSIBLE_SPEED * dt + 40:
            return  # implausible teleport -> ignore (basic anti-cheat)
        k.x, k.y, k.a, k.speed, k.cflags = d["x"], d["y"], d["a"], d["speed"], d["flags"]
        if k.finished:
            return
        track = self.race["track"]
        p, k.hint, _ = track.progress(k.x, k.y, k.hint)
        k.total += _unwrap(p - k.prog)
        k.prog = p
        t = self.race_time()
        laps_now = int(math.floor(k.total))
        if laps_now > k.laps_done and laps_now >= 1:
            lap_time = t - k.lap_start
            k.lap_start = t
            k.laps_done = laps_now
            if k.best_lap is None or lap_time < k.best_lap:
                k.best_lap = lap_time
            if laps_now >= self.laps:
                self._finish(k, t)
            elif laps_now == self.laps - 1:
                self.server.send(k.session, P.S_EVENT, text="FINAL LAP!")

    def _finish(self, k, t):
        k.finished = True
        k.finish_time = t
        self._rank()
        if self.race["first_finish"] is None:
            self.race["first_finish"] = time.time()
        self.broadcast(P.S_FINISHED, pid=k.pid, name=k.name, rank=k.rank, time=round(t, 3))
        self.broadcast(P.S_EVENT, text=f"{k.name} finished #{k.rank} ({t:.2f}s)")
        self.server.log(f"Room {self.id}: {k.name} finished #{k.rank} in {t:.2f}s")

    def use_item(self, session):
        if self.race is None or self.status != RACING:
            return
        k = self.race["karts"].get(session.pid)
        if k is None or k.item == C.ITEM_NONE or k.finished:
            return
        item = k.item
        k.item_count -= 1
        if k.item_count <= 0:
            k.item, k.item_count = C.ITEM_NONE, 0
        fx, fy = math.cos(k.a), math.sin(k.a)
        if item in (C.ITEM_BOOST, C.ITEM_TRIPLE):
            self.server.send(session, P.S_EFFECT, kind="boost")
        elif item == C.ITEM_SHIELD:
            k.shield_t = C.SHIELD_TIME
            self.server.send(session, P.S_EFFECT, kind="shield")
        elif item == C.ITEM_BANANA:
            self._spawn(P.OBJ_BANANA, k.x - fx * 34, k.y - fy * 34, 0, 0, k.pid)
        elif item == C.ITEM_SHELL:
            sp = C.SHELL_SPEED + max(0.0, k.speed)
            self._spawn(P.OBJ_SHELL, k.x + fx * 36, k.y + fy * 36, fx * sp, fy * sp, k.pid)
        elif item == C.ITEM_HOMING:
            target = self._target_ahead(k)
            o = self._spawn(P.OBJ_HOMING, k.x + fx * 36, k.y + fy * 36, fx * C.HOMING_SPEED,
                            fy * C.HOMING_SPEED, k.pid)
            o["target"] = target.pid if target else None
            if target:
                self.server.send(target.session, P.S_EVENT, text=f"Seeker incoming from {k.name}!")
        self.server.send(session, P.S_ITEM_GOT, item=k.item, count=k.item_count)

    def _spawn(self, kind, x, y, vx, vy, owner):
        objs = self.race["objs"]
        if len(objs) >= 40:
            del objs[next(iter(objs))]
        oid = self.race["next_obj"]
        self.race["next_obj"] = oid % 65000 + 1
        o = {"id": oid, "kind": kind, "x": x, "y": y, "vx": vx, "vy": vy, "owner": owner, "t": 0.0, "target": None}
        objs[oid] = o
        return o

    def _target_ahead(self, k):
        humans = sorted((x for x in self.race["karts"].values() if x.connected and not x.finished),
                        key=lambda x: x.rank)
        ahead = [x for x in humans if x.rank < k.rank and x.pid != k.pid]
        if ahead:
            return ahead[-1]
        others = [x for x in humans if x.pid != k.pid]
        return others[0] if others else None

    def _roll_item(self, k):
        n = max(2, len(self.race["karts"]) + len(self.race["ghosts"]))
        f = (k.rank - 1) / (n - 1)  # 0 = leader, 1 = last place
        lead = {C.ITEM_BANANA: 35, C.ITEM_SHELL: 30, C.ITEM_SHIELD: 20, C.ITEM_BOOST: 15}
        last = {C.ITEM_BOOST: 20, C.ITEM_TRIPLE: 25, C.ITEM_HOMING: 30, C.ITEM_SHELL: 15, C.ITEM_SHIELD: 10}
        keys = set(lead) | set(last)
        weights = {i: lead.get(i, 0) * (1 - f) + last.get(i, 0) * f for i in keys}
        r = random.uniform(0, sum(weights.values()))
        for item, w in weights.items():
            r -= w
            if r <= 0:
                return item
        return C.ITEM_BOOST

    def _hit(self, victim, attacker, what):
        if victim.invuln_t > 0 or victim.finished:
            return False
        if victim.shield_t > 0:
            victim.shield_t = 0.0
            self.server.send(victim.session, P.S_EFFECT, kind="shield_break")
            self.broadcast(P.S_EVENT, text=f"{victim.name}'s shield blocked a {what}")
            return True
        victim.invuln_t = C.SPIN_TIME + 0.6
        self.server.send(victim.session, P.S_EFFECT, kind="spin")
        who = attacker.name if attacker else "someone"
        if attacker is victim:
            self.broadcast(P.S_EVENT, text=f"{victim.name} slipped on their own banana")
        else:
            self.broadcast(P.S_EVENT, text=f"{who} hit {victim.name} with a {what}")
        return True

    # ------------------------------------------------------------- tick
    def tick(self, dt):
        if self.race is None:
            return
        now = time.time()
        r = self.race
        if self.status == COUNTDOWN:
            if now >= r["go_time"]:
                self.status = RACING
                for k in r["karts"].values():
                    k.last_state_time = now
                self.broadcast(P.S_GO)
            return
        if self.status == RESULTS:
            if now >= r["results_until"]:
                self.race = None
                self.status = WAITING
                for pid in self.ready:
                    self.ready[pid] = False
                self.push_state()
                self.server.lobby_changed()
            return
        if self.status != RACING:
            return
        t = self.race_time()
        track = r["track"]
        karts = [k for k in r["karts"].values() if k.connected]
        for g in r["ghosts"]:
            g.update(t, track)
        for k in karts:
            k.shield_t = max(0.0, k.shield_t - dt)
            k.invuln_t = max(0.0, k.invuln_t - dt)
            if not k.finished and t >= k.next_sample:
                k.samples.append([round(t, 2), round(k.x, 1), round(k.y, 1), round(k.a, 3)])
                k.next_sample = t + 0.1
        # item boxes
        boxes = r["boxes"]
        for i, (bx, by) in enumerate(track.box_positions):
            if boxes[i] > 0:
                boxes[i] = max(0.0, boxes[i] - dt)
                continue
            for k in karts:
                if not k.finished and (k.x - bx) ** 2 + (k.y - by) ** 2 < (C.ITEM_BOX_RADIUS + C.KART_RADIUS) ** 2:
                    boxes[i] = C.ITEM_BOX_RESPAWN
                    if k.item == C.ITEM_NONE:
                        k.item = self._roll_item(k)
                        k.item_count = 3 if k.item == C.ITEM_TRIPLE else 1
                        self.server.send(k.session, P.S_ITEM_GOT, item=k.item, count=k.item_count)
                    break
        # projectiles & traps
        by_pid = {k.pid: k for k in karts}
        dead = []
        for oid, o in r["objs"].items():
            o["t"] += dt
            if o["kind"] == P.OBJ_SHELL:
                o["x"] += o["vx"] * dt
                o["y"] += o["vy"] * dt
                if o["t"] > C.SHELL_LIFETIME or track.nearest(o["x"], o["y"])[2] > track.half + 25:
                    dead.append(oid)
                    continue
            elif o["kind"] == P.OBJ_HOMING:
                tgt = by_pid.get(o["target"])
                if tgt is not None and not tgt.finished:
                    desired = math.atan2(tgt.y - o["y"], tgt.x - o["x"])
                    cur = math.atan2(o["vy"], o["vx"])
                    cur += max(-4.0 * dt, min(4.0 * dt, wrap_angle(desired - cur)))
                    o["vx"], o["vy"] = math.cos(cur) * C.HOMING_SPEED, math.sin(cur) * C.HOMING_SPEED
                o["x"] += o["vx"] * dt
                o["y"] += o["vy"] * dt
                if o["t"] > C.HOMING_LIFETIME:
                    dead.append(oid)
                    continue
            rad = C.BANANA_RADIUS if o["kind"] == P.OBJ_BANANA else C.PROJECTILE_RADIUS
            for k in karts:
                if k.pid == o["owner"] and o["kind"] != P.OBJ_BANANA and o["t"] < 0.4:
                    continue
                if k.pid == o["owner"] and o["kind"] == P.OBJ_BANANA and o["t"] < 1.0:
                    continue
                if (k.x - o["x"]) ** 2 + (k.y - o["y"]) ** 2 < (rad + C.KART_RADIUS) ** 2:
                    what = {P.OBJ_BANANA: "banana", P.OBJ_SHELL: "shell", P.OBJ_HOMING: "seeker"}[o["kind"]]
                    if self._hit(k, by_pid.get(o["owner"]) or r["karts"].get(o["owner"]), what):
                        dead.append(oid)
                    break
        for oid in dead:
            r["objs"].pop(oid, None)
        self._rank()
        # end conditions
        humans_left = [k for k in r["karts"].values() if k.connected]
        if not humans_left:
            self.server.log(f"Room {self.id}: all racers gone, race aborted")
            self.race = None
            self.status = WAITING
            self.server.lobby_changed()
            return
        if all(k.finished for k in humans_left):
            self._end_race()
        elif r["first_finish"] and now - r["first_finish"] > C.FINISH_GRACE_SECONDS:
            self._end_race()
        elif t > C.RACE_HARD_LIMIT:
            self._end_race()

    def _rank(self):
        r = self.race
        everyone = [k for k in r["karts"].values() if k.connected] + r["ghosts"]
        everyone.sort(key=lambda k: (0, k.finish_time, 0) if k.finished else (1, 0, -k.total))
        for i, k in enumerate(everyone):
            k.rank = i + 1

    def _end_race(self):
        r = self.race
        track = r["track"]
        finished = []
        for k in r["karts"].values():
            if k.finished:
                finished.append((k.finish_time, k))
        for g in r["ghosts"]:
            finished.append((g.time, g))  # ghosts' recorded time always counts
        finished.sort(key=lambda e: e[0])
        dnf = sorted((k for k in r["karts"].values() if not k.finished), key=lambda k: (not k.connected, -k.total))
        results = []
        rank = 0
        for t, k in finished:
            rank += 1
            pts = C.POINTS_TABLE[rank - 1] if rank <= len(C.POINTS_TABLE) and not k.ghost else 0
            results.append({"rank": rank, "pid": k.pid, "name": k.name, "time": round(t, 3), "ghost": k.ghost,
                            "best_lap": round(k.best_lap, 3) if not k.ghost and k.best_lap else None,
                            "points": pts, "status": "FINISHED"})
        for k in dnf:
            rank += 1
            results.append({"rank": rank, "pid": k.pid, "name": k.name, "time": None, "ghost": False,
                            "best_lap": round(k.best_lap, 3) if k.best_lap else None, "points": 0,
                            "status": "DNF" if k.connected else ("LEFT" if k.leave_reason == "left" else "DISCONNECTED")})
        # persist stats + ghost recordings (registered users only)
        for res in results:
            k = r["karts"].get(res["pid"])
            if k is None or k.session.guest:
                continue
            self.server.accounts.record_result(k.name, self.track_key, res["rank"], res["points"], res["time"])
            if k.finished:
                k.samples.append([round(k.finish_time, 2), round(k.x, 1), round(k.y, 1), round(k.a, 3)])
                self.server.ghosts.save_run(self.track_key, self.laps, k.name, k.finish_time, k.samples)
        winner = results[0]["name"] if results else "-"
        self.broadcast(P.S_RACE_RESULTS, results=results, winner=winner, track=self.track_key)
        self.server.log(f"Room {self.id}: race over, winner {winner}")
        self.status = RESULTS
        r["results_until"] = time.time() + 8.0
        self.push_state()

    # ------------------------------------------------------------- snapshot
    def snapshot(self):
        r = self.race
        if r is None or self.status not in (COUNTDOWN, RACING):
            return None
        r["snap_seq"] += 1
        karts = []
        for k in r["karts"].values():
            if not k.connected:
                continue
            flags = (k.cflags & (P.F_BOOST | P.F_SPIN))
            if k.shield_t > 0:
                flags |= P.F_SHIELD
            if k.finished:
                flags |= P.F_FINISHED
            lap = min(self.laps, max(1, int(math.floor(k.total)) + 1))
            karts.append((k.pid, k.x, k.y, k.a, k.speed, lap, k.rank, flags))
        for g in r["ghosts"]:
            flags = P.F_GHOST | (P.F_FINISHED if g.finished else 0)
            lap = min(self.laps, max(1, int(math.floor(g.total)) + 1))
            karts.append((g.pid, g.x, g.y, g.a, g.speed, lap, g.rank, flags))
        boxes = 0
        for i, tleft in enumerate(r["boxes"]):
            if tleft <= 0:
                boxes |= 1 << i
        objs = [(o["kind"], o["id"], o["x"], o["y"]) for o in r["objs"].values()]
        return P.pack_snapshot(r["snap_seq"], self.race_time(), boxes, karts, objs)
