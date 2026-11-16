"""Shared constants used by both the server and the client."""

GAME_NAME = "Turbo Kart Arena"
PROTOCOL_VERSION = 1

# ---- Network ----------------------------------------------------------------
TCP_PORT = 5555          # reliable control channel (login, lobby, rooms, items, results)
UDP_PORT = 5556          # real-time channel (kart states / world snapshots)
DISCOVERY_PORT = 5557    # UDP broadcast LAN server discovery

CLIENT_STATE_HZ = 30     # client -> server kart state rate (UDP)
SERVER_TICK_HZ = 30      # server simulation tick (items, projectiles, laps)
SNAPSHOT_HZ = 20         # server -> clients world snapshot rate (UDP)

HEARTBEAT_INTERVAL = 1.0  # client sends TCP PING every second (also used to measure RTT)
TIMEOUT_SECONDS = 6.0     # server drops a client after this long without any packet
SERVER_LOST_SECONDS = 6.0 # client gives up on the server after this long without TCP data

INTERP_DELAY = 0.10       # base interpolation delay: remote karts rendered 100 ms in the past
INTERP_DELAY_MAX = 0.30   # grows up to this on jittery (internet) links
UDP_KEEPALIVE = 5.0       # client re-sends UDP HELLO this often so NAT mappings never expire

# ---- Public-server protection (server reachable from the whole internet) ---
MAX_SESSIONS = 64
MAX_CONNECTIONS_PER_IP = 8
LOGIN_DEADLINE = 30.0     # unauthenticated TCP connections are closed after this

# ---- Game rules -------------------------------------------------------------
MAX_PLAYERS_PER_ROOM = 6  # e.g. 3 PCs x 2 split-screen players
MAX_GHOSTS = 3
DEFAULT_LAPS = 3
COUNTDOWN_SECONDS = 3.0
FINISH_GRACE_SECONDS = 25.0   # after the first human finishes, others get this long
RACE_HARD_LIMIT = 600.0       # a race never lasts more than 10 minutes
POINTS_TABLE = [10, 8, 6, 4, 2, 1]

# ---- Kart physics (client simulates its own kart, server validates) ---------
KART_MAX_SPEED = 260.0
KART_BOOST_SPEED = 400.0
KART_GRASS_SPEED = 110.0
KART_ACCEL = 220.0
KART_BRAKE = 420.0
KART_REVERSE_MAX = 80.0
KART_FRICTION = 120.0
KART_TURN_RATE = 2.6          # rad/s at full grip
KART_RADIUS = 14.0
SPIN_TIME = 1.3
BOOST_TIME = 1.6
SHIELD_TIME = 6.0
MAX_PLAUSIBLE_SPEED = KART_BOOST_SPEED * 1.6   # server anti-cheat bound

# ---- Items -----------------------------------------------------------------
ITEM_NONE, ITEM_BOOST, ITEM_BANANA, ITEM_SHELL, ITEM_HOMING, ITEM_SHIELD, ITEM_TRIPLE = range(7)
ITEM_NAMES = {
    ITEM_NONE: "",
    ITEM_BOOST: "Turbo",
    ITEM_BANANA: "Banana",
    ITEM_SHELL: "Shell",
    ITEM_HOMING: "Seeker",
    ITEM_SHIELD: "Shield",
    ITEM_TRIPLE: "3x Turbo",
}
ITEM_BOX_RESPAWN = 4.0
ITEM_BOX_RADIUS = 22.0
BANANA_RADIUS = 14.0
SHELL_SPEED = 520.0
SHELL_LIFETIME = 4.0
HOMING_SPEED = 470.0
HOMING_LIFETIME = 7.0
PROJECTILE_RADIUS = 12.0
