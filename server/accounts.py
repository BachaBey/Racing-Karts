"""Player registration / login with salted PBKDF2 password hashes, persisted to JSON."""
import hashlib
import json
import os
import re
import secrets
import threading
import time

USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,16}$")


class Accounts:
    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.users = {}
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    self.users = json.load(f)
            except (OSError, ValueError):
                self.users = {}

    def _save(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.users, f, indent=1)
        os.replace(tmp, self.path)

    @staticmethod
    def _hash(pw, salt):
        return hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), bytes.fromhex(salt), 60000).hex()

    def register(self, user, pw):
        if not USERNAME_RE.match(user or ""):
            return False, "Username must be 3-16 letters, digits or _"
        if not pw or len(pw) < 3:
            return False, "Password must be at least 3 characters"
        salt = secrets.token_hex(16)
        h = self._hash(pw, salt)
        with self.lock:
            if user.lower() in (u.lower() for u in self.users):
                return False, "Username already taken"
            self.users[user] = {"salt": salt, "hash": h, "created": time.time(),
                                "races": 0, "wins": 0, "points": 0, "best": {}}
            self._save()
        return True, "Registered"

    def check(self, user, pw):
        with self.lock:
            rec = self.users.get(user)
        if rec is None:
            return False, "Unknown username (register first)"
        if not secrets.compare_digest(self._hash(pw or "", rec["salt"]), rec["hash"]):
            return False, "Wrong password"
        return True, "OK"

    def stats(self, user):
        with self.lock:
            rec = self.users.get(user)
            if not rec:
                return {"races": 0, "wins": 0, "points": 0}
            return {"races": rec["races"], "wins": rec["wins"], "points": rec["points"]}

    def record_result(self, user, track, rank, points, time_s):
        with self.lock:
            rec = self.users.get(user)
            if not rec:
                return
            rec["races"] += 1
            rec["points"] += points
            if rank == 1:
                rec["wins"] += 1
            if time_s is not None:
                best = rec["best"].get(track)
                if best is None or time_s < best:
                    rec["best"][track] = round(time_s, 3)
            self._save()

    def leaderboard(self, n=10):
        with self.lock:
            rows = [{"user": u, "points": r["points"], "wins": r["wins"], "races": r["races"]}
                    for u, r in self.users.items()]
        rows.sort(key=lambda r: (-r["points"], -r["wins"], r["user"]))
        return rows[:n]
