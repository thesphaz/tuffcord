from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from flask_socketio import SocketIO, join_room, leave_room, emit
import secrets
import string
import re
import time
import unicodedata
import shlex
import sqlite3
import os
import json
import urllib.parse
import urllib.request
import urllib.error
import uuid
from pathlib import Path

app = Flask(__name__)
SECRET_PATH = Path(__file__).with_name(".tuffcord_secret")
try:
    secret_key = SECRET_PATH.read_text(encoding="utf-8").strip()
    if len(secret_key) < 32:
        raise ValueError("Invalid saved secret")
except (OSError, ValueError):
    secret_key = secrets.token_hex(32)
    SECRET_PATH.write_text(secret_key, encoding="utf-8")
app.config["SECRET_KEY"] = secret_key
socketio = SocketIO(app)

MAX_PLAYERS = 8
DATABASE_PATH = Path(__file__).with_name("DATAOFSHTI.db")
OWNER_CODE = "Zachary1432S"
MOD_CODE = "MrTinyToon"
AVAILABLE_TAGS = ["OWNER", "EMPEROR", "VIP", "MODERATOR", "ADMIN", "DEVELOPER", "LEGEND", "TOTEM OF NULL"]
TAG_COLORS = {
    "OWNER": "#ffca66", "EMPEROR": "#ff6b35", "VIP": "#d9a7ff",
    "MODERATOR": "#80d9ff", "ADMIN": "#ff8585", "DEVELOPER": "#8ff0bd",
    "LEGEND": "#ff9fce", "TOTEM OF NULL": "#c8c5ff"
}

# room fields: players, host_player_id, banned player IDs, manually-muted IDs
rooms = {}
player_rooms = {}
automod_muted_until = {}  # player_id -> timestamp
manual_tags = {}          # player_id -> custom tag objects / staff-applied tags
disabled_tags = {}        # player_id -> tag names disabled by the owner

SWEAR_WORDS = {
    "fuck", "fucking", "fucked", "fucker", "shit", "shitty", "bullshit",
    "bitch", "bitchy", "asshole", "arsehole", "bastard", "dick", "dickhead",
    "piss", "pissed", "motherfucker", "wtf"
}
SLUR_WORDS = {
    "faggot", "fag", "dyke", "tranny", "retard", "retarded", "kike", "spic",
    "chink", "wetback", "gook", "cracker"
}
NSFW_WORDS = {
    "nsfw", "porn", "porno", "pornography", "xxx", "onlyfans", "nude", "nudes",
    "nudity", "naked", "sex", "sexual", "sexy", "erotic", "hentai", "orgasm",
    "masturbate", "masturbation", "blowjob", "handjob", "dildo", "vibrator",
    "fetish", "kink", "bdsm", "rape", "molest", "seduce", "hookup", "hooking up"
}
# Common evasions are mapped to their intended word before comparison.
ALIASES = {"fuxk": "fuck", "fuk": "fuck", "fck": "fuck", "phuck": "fuck",
           "sh1t": "shit", "5hit": "shit", "b1tch": "bitch", "a55hole": "asshole"}
ALL_BAD_WORDS = SWEAR_WORDS | SLUR_WORDS | NSFW_WORDS
BAD_NAME_WORDS = ALL_BAD_WORDS | {"dih"}
BAD_CANONICAL = ALL_BAD_WORDS | set(ALIASES.values()) | {"dih"}


def normalize_token(value):
    value = unicodedata.normalize("NFKC", value).casefold()
    value = value.translate(str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s", "!": "i"}))
    return re.sub(r"[^a-z]", "", value)


def token_is_bad(token):
    normalized = normalize_token(token)
    normalized = ALIASES.get(normalized, normalized)
    return normalized in BAD_CANONICAL or normalized[::-1] in BAD_CANONICAL


def term_pattern(words):
    alternatives = "|".join(re.escape(term) for term in sorted(words, key=len, reverse=True))
    return re.compile(r"(?<!\w)(?:" + alternatives + r")(?!\w)", re.IGNORECASE)


ALL_FILTER_PATTERN = term_pattern(ALL_BAD_WORDS)
MODERATION_WORDS = SWEAR_WORDS | SLUR_WORDS
MODERATION_PATTERN = term_pattern(MODERATION_WORDS)
FLEXIBLE_WORDS = BAD_CANONICAL | {word[::-1] for word in BAD_CANONICAL if " " not in word}
FLEXIBLE_MODERATION_WORDS = MODERATION_WORDS | set(ALIASES.values()) | {word[::-1] for word in MODERATION_WORDS if " " not in word}
FLEXIBLE_PATTERNS = [
    re.compile(r"(?<!\w)" + r"[\W_]*".join(re.escape(ch) for ch in word) + r"(?!\w)", re.IGNORECASE)
    for word in sorted(FLEXIBLE_WORDS, key=len, reverse=True) if len(word) >= 4
]
FLEXIBLE_MODERATION_PATTERNS = [
    re.compile(r"(?<!\w)" + r"[\W_]*".join(re.escape(ch) for ch in word) + r"(?!\w)", re.IGNORECASE)
    for word in sorted(FLEXIBLE_MODERATION_WORDS, key=len, reverse=True) if len(word) >= 4
]
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9@$!._-]+")


def contains_bad_term(text):
    if ALL_FILTER_PATTERN.search(text) or any(pattern.search(text) for pattern in FLEXIBLE_PATTERNS):
        return True
    for token in TOKEN_PATTERN.findall(text):
        if token_is_bad(token):
            return True
    compact = normalize_token(text)
    return any(normalize_token(word) in compact or normalize_token(word)[::-1] in compact
               for word in ALL_BAD_WORDS if len(word) >= 5)


def contains_moderation_term(text):
    if MODERATION_PATTERN.search(text) or any(pattern.search(text) for pattern in FLEXIBLE_MODERATION_PATTERNS):
        return True
    for token in TOKEN_PATTERN.findall(text):
        normalized = ALIASES.get(normalize_token(token), normalize_token(token))
        if normalized in MODERATION_WORDS or normalized[::-1] in MODERATION_WORDS:
            return True
    compact = normalize_token(text)
    return any(normalize_token(word) in compact or normalize_token(word)[::-1] in compact
               for word in MODERATION_WORDS if len(word) >= 5)


def filter_message(message):
    # First mask normal spellings and punctuation-separated spellings.
    message = ALL_FILTER_PATTERN.sub("#######", message)
    for pattern in FLEXIBLE_PATTERNS:
        message = pattern.sub("#######", message)
    # Then mask common leetspeak and reversed forms one token at a time.
    def replace_token(match):
        return "#######" if token_is_bad(match.group(0)) else match.group(0)
    message = TOKEN_PATTERN.sub(replace_token, message)
    return message


def db_connect():
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_database():
    with db_connect() as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS accounts (
                username_key TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                player_id TEXT NOT NULL UNIQUE,
                created_at REAL NOT NULL
            )
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS global_bans (
                username_key TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                reason TEXT NOT NULL,
                banned_by TEXT NOT NULL,
                created_at REAL NOT NULL,
                expires_at REAL
            )
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS chat_mutes (
                username_key TEXT NOT NULL,
                room_code TEXT NOT NULL DEFAULT '',
                username TEXT NOT NULL,
                reason TEXT NOT NULL,
                muted_by TEXT NOT NULL,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                PRIMARY KEY (username_key, room_code)
            )
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS player_progress (
                player_id TEXT PRIMARY KEY, total_xp INTEGER NOT NULL DEFAULT 0
            )
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS player_custom_tags (
                player_id TEXT NOT NULL, tag_name TEXT NOT NULL, color TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY (player_id, tag_name)
            )
        """)


def get_total_xp(pid):
    with db_connect() as connection:
        row = connection.execute("SELECT total_xp FROM player_progress WHERE player_id = ?", (pid,)).fetchone()
    return int(row["total_xp"]) if row else 0


def level_info(total_xp):
    level, remaining = 0, max(0, int(total_xp))
    needed = 20
    while remaining >= needed:
        remaining -= needed
        level += 1
        needed = max(1, round(20 * (1.08 ** level)))
    return {"xp": int(total_xp), "level": level, "progress": remaining, "needed": needed}


def award_message_xp(pid):
    with db_connect() as connection:
        connection.execute("INSERT INTO player_progress (player_id, total_xp) VALUES (?, 1) ON CONFLICT(player_id) DO UPDATE SET total_xp = total_xp + 1", (pid,))
    info = level_info(get_total_xp(pid))
    # Each ten-level milestone grants a persistent, toggleable reward tag.
    if info["level"] >= 10:
        for milestone in range(10, info["level"] + 1, 10):
            tag_name = f"LEVEL {milestone}"
            color = "#ffca66" if milestone % 20 == 0 else "#c8c5ff"
            with db_connect() as connection:
                connection.execute("INSERT OR IGNORE INTO player_custom_tags (player_id, tag_name, color, enabled) VALUES (?, ?, ?, 1)", (pid, tag_name, color))
    return info


def load_custom_tags(pid):
    with db_connect() as connection:
        rows = connection.execute("SELECT tag_name, color, enabled FROM player_custom_tags WHERE player_id = ?", (pid,)).fetchall()
    manual_tags[pid] = [{"name": row["tag_name"], "color": row["color"]} for row in rows]
    disabled_tags[pid] = {row["tag_name"].casefold() for row in rows if not row["enabled"]}
    return manual_tags[pid]


def persist_tag_enabled(pid, name, enabled):
    with db_connect() as connection:
        connection.execute("UPDATE player_custom_tags SET enabled = ? WHERE player_id = ? AND lower(tag_name) = lower(?)", (1 if enabled else 0, pid, name))


def get_global_ban(username):
    key = username.strip().casefold()
    now = time.time()
    with db_connect() as connection:
        row = connection.execute("SELECT * FROM global_bans WHERE username_key = ?", (key,)).fetchone()
        if row and row["expires_at"] is not None and row["expires_at"] <= now:
            connection.execute("DELETE FROM global_bans WHERE username_key = ?", (key,))
            return None
        return dict(row) if row else None


def get_active_mute(username, room_code):
    now = time.time()
    username_key = username.strip().casefold()
    room_key = room_code.casefold() if room_code else ""
    with db_connect() as connection:
        connection.execute("DELETE FROM chat_mutes WHERE expires_at <= ?", (now,))
        rows = connection.execute(
            "SELECT * FROM chat_mutes WHERE username_key = ? AND room_code IN ('', ?)",
            (username_key, room_key)
        ).fetchall()
    if not rows:
        return None
    return max((dict(row) for row in rows), key=lambda row: row["expires_at"])


def save_mute(username, room_code, reason, muted_by, expires_at):
    username_key = username.strip().casefold()
    room_key = room_code.casefold() if room_code else ""
    with db_connect() as connection:
        connection.execute("""
            INSERT INTO chat_mutes
                (username_key, room_code, username, reason, muted_by, created_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(username_key, room_code) DO UPDATE SET
                username=excluded.username, reason=excluded.reason,
                muted_by=excluded.muted_by, created_at=excluded.created_at,
                expires_at=excluded.expires_at
        """, (username_key, room_key, username.strip(), reason, muted_by, time.time(), expires_at))


def remove_mute(username, room_code):
    username_key = username.strip().casefold()
    room_key = room_code.casefold() if room_code else ""
    with db_connect() as connection:
        connection.execute(
            "DELETE FROM chat_mutes WHERE username_key = ? AND room_code = ?",
            (username_key, room_key)
        )


def is_valid_username(name):
    # One word only: ASCII letters, digits, and underscores. No spaces or punctuation.
    if not re.fullmatch(r"[A-Za-z0-9_]{1,20}", name):
        return False
    normalized = normalize_token(name)
    bad_names = {normalize_token(word) for word in BAD_NAME_WORDS}
    bad_names.update(normalize_token(word) for word in ALIASES)
    bad_names.update({"dih", "kys"})
    # Check embedded and reversed terms too, so adding characters around a blocked
    # word doesn't trivially bypass the username check.
    if any(word and (word in normalized or word[::-1] in normalized) for word in bad_names):
        return False
    if contains_bad_term(name):
        return False
    return True


def parse_ban_duration(value):
    value = value.strip().casefold()
    if value in {"perm", "permanent", "forever", "indefinite"}:
        return None
    match = re.fullmatch(r"(\d+)(s|m|h|d|w)?", value)
    if not match:
        return False
    amount = int(match.group(1))
    unit = match.group(2) or "s"
    multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
    seconds = amount * multipliers[unit]
    if seconds < 1 or seconds > 315360000:  # cap at ten years; use permanent for no expiry
        return False
    return seconds


initialize_database()


def player_id():
    value = session.get("player_id")
    if not value:
        value = secrets.token_hex(16)
        session["player_id"] = value
    return value


def default_tag_objects(names):
    result = []
    for name in names:
        if name in AVAILABLE_TAGS:
            result.append({"name": name, "color": TAG_COLORS[name]})
    return result


def current_tags():
    pid = player_id()
    names = session.get("tags", [])
    tags = default_tag_objects(names)
    load_custom_tags(pid)
    tags.extend(manual_tags.get(pid, []))
    disabled = {name.casefold() for name in disabled_tags.get(pid, set())}
    seen = set()
    unique = []
    for tag in tags:
        key = tag["name"].casefold()
        if key not in seen and key not in disabled:
            seen.add(key)
            unique.append(tag)
    return unique


def get_player_by_name(room, target_name):
    target_name = target_name.strip().strip('"').strip("'").casefold()
    for sid, player in room["players"].items():
        if player["name"].casefold() == target_name:
            return sid, player
    return None, None


def player_list(room):
    return [{"name": p["name"], "tags": p["tags"], "is_host": p["player_id"] == room["host_player_id"]}
            for p in room["players"].values()]


def announce(code, message):
    emit("chat_message", {"name": "SERVER", "tags": [], "message": message}, to=code)


def sync_room(code):
    room = rooms.get(code)
    if room:
        emit("player_list", player_list(room), to=code)


def leave_current_room(sid, announce_leave=True):
    code = player_rooms.pop(sid, None)
    if not code:
        return
    room = rooms.get(code)
    if not room:
        return
    player = room["players"].pop(sid, None)
    try:
        leave_room(code, sid=sid)
    except (RuntimeError, TypeError):
        pass
    if player:
        if announce_leave:
            announce(code, f"{player['name']} left the room.")
        if player["player_id"] == room["host_player_id"] and room["players"]:
            room["host_player_id"] = next(iter(room["players"].values()))["player_id"]
            announce(code, "The room host has changed.")
        sync_room(code)
    if not room["players"]:
        rooms.pop(code, None)


def is_owner(player):
    return bool(player.get("is_owner"))


def is_staff(player):
    return bool(player.get("is_owner") or player.get("is_mod"))


def has_mute_permission(player, room):
    return is_staff(player) or player["player_id"] == room["host_player_id"]


def add_tag_to_player(player, name, color):
    name = re.sub(r"\s+", " ", name.strip())[:24]
    if not name:
        return False
    named_colors = {"red": "#ff5555", "blue": "#55aaff", "purple": "#b58aff", "green": "#65df91",
                    "orange": "#ff9b45", "pink": "#ff85c2", "yellow": "#ffda63", "cyan": "#5ce8e8",
                    "white": "#ffffff", "black": "#55505a", "gold": "#ffca66", "silver": "#c5cbd3"}
    color = named_colors.get(color.casefold(), color)
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
        color = "#f2a65a"
    existing = player["tags"]
    matched = False
    for tag in existing:
        if tag["name"].casefold() == name.casefold():
            tag["color"] = color
            matched = True
    if not matched:
        existing.append({"name": name, "color": color})
    stored = manual_tags.setdefault(player["player_id"], [])
    for tag in stored:
        if tag["name"].casefold() == name.casefold():
            tag["color"] = color
            break
    else:
        stored.append({"name": name, "color": color})
    disabled_tags.setdefault(player["player_id"], set()).discard(name.casefold())
    with db_connect() as connection:
        connection.execute("INSERT INTO player_custom_tags (player_id, tag_name, color, enabled) VALUES (?, ?, ?, 1) ON CONFLICT(player_id, tag_name) DO UPDATE SET color=excluded.color, enabled=1", (player["player_id"], name, color))
    return True


def handle_command(code, sid, message):
    room = rooms[code]
    actor = room["players"][sid]
    try:
        args = shlex.split(message)
    except ValueError:
        emit("error_message", {"message": "Command syntax error. Check your quotation marks."}, to=sid)
        return True
    if not args:
        return True
    command = args[0].casefold()

    if command == "/help":
        help_text = "/clear (staff); /give tag USER TAGNAME COLOUR (staff); /tagoff USER TAG (owner); /ban USER REASON TIME (staff); /unban USER (staff); /mute USER [seconds] and /unmute USER (host/staff); /addxp USER AMOUNT and /removexp USER AMOUNT (owner); /roll [SIDES]; /coin; /8ball QUESTION; /choose OPTION1 | OPTION2; /rate USER; /ship USER1 USER2; /whoami; /afk [MESSAGE]; /back. Send a message to earn 1 XP. Usernames are one word."
        emit("chat_message", {"name": "SERVER", "tags": [], "message": help_text}, to=sid)
        return True

    if command in {"/addxp", "/removexp"}:
        if not is_owner(actor):
            emit("error_message", {"message": "Only the owner can change players' XP."}, to=sid)
            return True
        if len(args) != 3 or not args[2].isdigit():
            emit("error_message", {"message": f"Usage: {command} USER AMOUNT (amount must be a positive whole number)."}, to=sid)
            return True
        amount = int(args[2])
        if amount < 1 or amount > 1000000:
            emit("error_message", {"message": "Amount must be between 1 and 1,000,000 XP."}, to=sid)
            return True
        username_key = args[1].casefold()
        with db_connect() as connection:
            account = connection.execute("SELECT username, player_id FROM accounts WHERE username_key = ?", (username_key,)).fetchone()
            if not account:
                emit("error_message", {"message": f"Account '{args[1]}' was not found."}, to=sid)
                return True
            old_xp_row = connection.execute("SELECT total_xp FROM player_progress WHERE player_id = ?", (account["player_id"],)).fetchone()
            old_xp = int(old_xp_row["total_xp"]) if old_xp_row else 0
            if command == "/addxp":
                new_xp = old_xp + amount
            else:
                new_xp = max(0, old_xp - amount)
            connection.execute("INSERT INTO player_progress (player_id, total_xp) VALUES (?, ?) ON CONFLICT(player_id) DO UPDATE SET total_xp = excluded.total_xp", (account["player_id"], new_xp))
        old_level = level_info(old_xp)["level"]
        new_level = level_info(new_xp)["level"]
        action_text = "added to" if command == "/addxp" else "removed from"
        announce(code, f"{amount} XP {action_text} {account['username']}. Total: {new_xp} XP (level {new_level}).")
        if new_level > old_level:
            announce(code, f"{account['username']} reached level {new_level}!")
        elif new_level < old_level:
            announce(code, f"{account['username']} is now level {new_level}.")
        return True

    if command == "/roll":
        if len(args) > 2 or (len(args) == 2 and not args[1].isdigit()):
            emit("error_message", {"message": "Usage: /roll [SIDES]"}, to=sid)
            return True
        sides = max(2, min(1000000, int(args[1]))) if len(args) == 2 else 6
        import random
        result = random.randint(1, sides)
        announce(code, f"{actor['name']} rolled {result} (1-{sides}).")
        return True

    if command == "/coin":
        import random
        announce(code, f"{actor['name']} flipped a coin: {random.choice(['Heads', 'Tails'])}.")
        return True

    if command == "/8ball":
        if len(args) < 2:
            emit("error_message", {"message": "Usage: /8ball QUESTION"}, to=sid)
            return True
        import random
        answer = random.choice(["It is certain.", "Ask again later.", "Signs point to yes.", "Don't count on it.", "Absolutely.", "Very doubtful.", "The outlook is good.", "Concentrate and ask again."])
        announce(code, f"Magic 8-ball for {actor['name']}: {answer}")
        return True

    if command == "/whoami":
        announce(code, f"You are {actor['name']} in room {code}.")
        return True

    if command == "/afk":
        reason = " ".join(args[1:]).strip() or "away"
        announce(code, f"{actor['name']} is now AFK: {reason}")
        return True

    if command == "/back":
        announce(code, f"{actor['name']} is back.")
        return True

    if command == "/choose":
        raw_options = message[len(args[0]):].strip()
        options = [part.strip() for part in raw_options.split("|") if part.strip()]
        if len(options) < 2:
            emit("error_message", {"message": "Usage: /choose OPTION 1 | OPTION 2 | [MORE OPTIONS]"}, to=sid)
            return True
        import random
        announce(code, f"{actor['name']} asked the universe to choose: {random.choice(options)}")
        return True

    if command == "/rate":
        if len(args) != 2:
            emit("error_message", {"message": "Usage: /rate USER"}, to=sid)
            return True
        target_name = args[1]
        import random
        score = random.randint(0, 100)
        announce(code, f"SERVER rates {target_name}: {score}/100. This highly scientific result is final.")
        return True

    if command == "/ship":
        if len(args) != 3:
            emit("error_message", {"message": "Usage: /ship USER1 USER2"}, to=sid)
            return True
        import random
        score = random.randint(0, 100)
        announce(code, f"{args[1]} + {args[2]} compatibility: {score}%. Completely random, not actual relationship advice.")
        return True

    if command == "/clear":
        if not is_staff(actor):
            emit("error_message", {"message": "Only staff can clear the chat."}, to=sid)
            return True
        room.setdefault("messages", {}).clear()
        emit("clear_chat", {"by": actor["name"]}, to=code)
        announce(code, f"Chat cleared by {actor['name']}.")
        return True

    if command == "/give" and len(args) >= 5 and args[1].casefold() == "tag":
        if not is_staff(actor):
            emit("error_message", {"message": "Only admins/moderators and the owner can give tags."}, to=sid)
            return True
        target_sid, target = get_player_by_name(room, args[2])
        if not target:
            emit("error_message", {"message": "That player isn't in this room. Use their exact name."}, to=sid)
            return True
        tag_name, color = " ".join(args[3:-1]), args[-1]
        if not add_tag_to_player(target, tag_name, color):
            emit("error_message", {"message": "Enter a valid tag name."}, to=sid)
            return True
        announce(code, f"{target['name']} received the {tag_name} tag.")
        sync_room(code)
        return True

    if command == "/tagoff" and len(args) >= 3:
        if not is_owner(actor):
            emit("error_message", {"message": "Only the owner can disable other players' tags."}, to=sid)
            return True
        target_sid, target = get_player_by_name(room, args[1])
        if not target:
            emit("error_message", {"message": "That player isn't in this room."}, to=sid)
            return True
        wanted = " ".join(args[2:]).casefold()
        before = len(target["tags"])
        target["tags"] = [tag for tag in target["tags"] if tag["name"].casefold() != wanted]
        manual_tags[target["player_id"]] = [tag for tag in manual_tags.get(target["player_id"], []) if tag["name"].casefold() != wanted]
        disabled_tags.setdefault(target["player_id"], set()).add(wanted)
        persist_tag_enabled(target["player_id"], wanted, False)
        if target["player_id"] == session.get("player_id"):
            session["tags"] = [tag for tag in session.get("tags", []) if tag.casefold() != wanted]
        if len(target["tags"]) == before:
            emit("error_message", {"message": "That player doesn't have that tag."}, to=sid)
        else:
            announce(code, f"{target['name']}'s {wanted} tag was disabled by the owner.")
            sync_room(code)
        return True

    if command in {"/ban", "/unban"}:
        if not is_staff(actor):
            emit("error_message", {"message": "Only admins/moderators and the owner can ban players."}, to=sid)
            return True
        if command == "/unban":
            if len(args) != 2:
                emit("error_message", {"message": "Usage: /unban USER"}, to=sid)
                return True
            target_name = args[1]
            with db_connect() as connection:
                cursor = connection.execute("DELETE FROM global_bans WHERE username_key = ?", (target_name.casefold(),))
            if cursor.rowcount:
                announce(code, f"Global ban removed for {target_name}.")
            else:
                emit("error_message", {"message": f"{target_name} does not have an active global ban."}, to=sid)
            return True

        if len(args) < 4:
            emit("error_message", {"message": "Usage: /ban USER REASON TIME (example: /ban bob spam 1d; use permanent for no expiry)."}, to=sid)
            return True
        target_name = args[1]
        reason = " ".join(args[2:-1]).strip()
        duration = parse_ban_duration(args[-1])
        if not reason or duration is False:
            emit("error_message", {"message": "Include a reason and a valid time: 30s, 10m, 2h, 7d, 1w, or permanent."}, to=sid)
            return True
        target_sid, target = get_player_by_name(room, target_name)
        if target and target["player_id"] == actor["player_id"]:
            emit("error_message", {"message": "You can't ban yourself."}, to=sid)
            return True
        if target and target.get("is_owner") and not is_owner(actor):
            emit("error_message", {"message": "You can't ban the owner."}, to=sid)
            return True
        # Staff cannot globally ban the owner, even if the owner is in another room.
        if not is_owner(actor):
            for active_room in rooms.values():
                for active_player in active_room["players"].values():
                    if active_player["name"].casefold() == target_name.casefold() and active_player.get("is_owner"):
                        emit("error_message", {"message": "You can't ban the owner."}, to=sid)
                        return True
        expiry = None if duration is None else time.time() + duration
        with db_connect() as connection:
            connection.execute("""
                INSERT INTO global_bans (username_key, username, reason, banned_by, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(username_key) DO UPDATE SET username=excluded.username,
                    reason=excluded.reason, banned_by=excluded.banned_by,
                    created_at=excluded.created_at, expires_at=excluded.expires_at
            """, (target_name.casefold(), target_name, reason, actor["name"], time.time(), expiry))
        duration_text = "permanently" if duration is None else f"for {args[-1]}"
        announce(code, f"{target_name} was globally banned {duration_text}. Reason: {reason}")
        # A global ban applies across every room in this server process.
        matching_sids = []
        for active_code, active_room in list(rooms.items()):
            for active_sid, active_player in list(active_room["players"].items()):
                if active_player["name"].casefold() == target_name.casefold():
                    if active_player.get("is_owner") and not is_owner(actor):
                        continue
                    emit("error_message", {"message": f"You are globally banned. Reason: {reason}. Duration: {duration_text}."}, to=active_sid)
                    matching_sids.append(active_sid)
        for active_sid in matching_sids:
            try:
                socketio.server.disconnect(active_sid, namespace="/")
            except Exception:
                pass
            leave_current_room(active_sid, announce_leave=False)
        return True

    if command in {"/mute", "/unmute"}:
        if not has_mute_permission(actor, room):
            emit("error_message", {"message": "Only the room host, staff, or owner can mute players."}, to=sid)
            return True
        if len(args) < 2:
            emit("error_message", {"message": f"Usage: {command} USER"}, to=sid)
            return True
        target_name = " ".join(args[1:]) if command == "/unmute" else args[1]
        target_sid, target = get_player_by_name(room, target_name)
        if not target:
            emit("error_message", {"message": "That player isn't in this room. Quote multi-word names."}, to=sid)
            return True
        if target["player_id"] == actor["player_id"]:
            emit("error_message", {"message": "You can't use this command on yourself."}, to=sid)
            return True
        if command == "/unmute":
            room["manual_muted"].pop(target["player_id"], None)
            remove_mute(target["name"], code)
            emit("mute_status", {"seconds": 0, "reason": "Your mute has been removed."}, to=target_sid)
            announce(code, f"{target['name']} can chat again.")
        else:
            seconds = 300
            if len(args) >= 3 and args[2].isdigit():
                seconds = max(1, min(3600, int(args[2])))
            expiry = time.time() + seconds
            room["manual_muted"][target["player_id"]] = expiry
            save_mute(target["name"], code, "Muted by the room host or staff", actor["name"], expiry)
            emit("mute_status", {"seconds": seconds, "reason": "You were muted by the room host or staff."}, to=target_sid)
            announce(code, f"{target['name']} was muted for {seconds} seconds.")
        return True

    emit("error_message", {"message": "Unknown command. Type /help for commands."}, to=sid)
    return True


@app.route("/", methods=["GET", "POST"])
def home():
    if "name" in session:
        return redirect(url_for("menu"))
    if request.method == "POST":
        action = request.form.get("action", "login")
        if action not in {"login", "register"}:
            action = "login"
        name = request.form.get("name", "").strip()
        password = request.form.get("password", "")
        if not name or not password:
            return render_template("index.html", error="Enter both your username and password.", mode=action)
        if not is_valid_username(name):
            return render_template("index.html", error="Usernames must be 1–20 characters using letters, numbers, or underscores, and cannot contain inappropriate terms.", mode=action)
        key = name.casefold()
        with db_connect() as connection:
            account = connection.execute("SELECT * FROM accounts WHERE username_key = ?", (key,)).fetchone()
            if action == "register":
                if len(password) < 8:
                    return render_template("index.html", error="Your password must be at least 8 characters.", mode="register")
                if account:
                    return render_template("index.html", error="That username already has an account. Log in instead.", mode="register")
                pid = secrets.token_hex(16)
                connection.execute(
                    "INSERT INTO accounts (username_key, username, password_hash, player_id, created_at) VALUES (?, ?, ?, ?, ?)",
                    (key, name, generate_password_hash(password), pid, time.time())
                )
                account_name = name
            else:
                if not account or not check_password_hash(account["password_hash"], password):
                    return render_template("index.html", error="Incorrect username or password.", mode="login")
                account_name = account["username"]
                pid = account["player_id"]
        global_ban = get_global_ban(account_name)
        if global_ban:
            if global_ban["expires_at"] is None:
                time_text = "permanently"
            else:
                time_text = f"until {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(global_ban['expires_at']))}"
            return render_template("index.html", error=f"This username is globally banned {time_text}. Reason: {global_ban['reason']}", mode=action)
        session.clear()
        session["name"] = account_name
        session["player_id"] = pid
        session["tags"] = []
        load_custom_tags(pid)
        return redirect(url_for("menu"))
    return render_template("index.html", error=None, mode="login")


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("home"))


@app.route("/menu")
def menu():
    if "name" not in session:
        return redirect(url_for("home"))
    global_ban = get_global_ban(session["name"])
    if global_ban:
        return f"You are globally banned. Reason: {global_ban['reason']}", 403
    names = session.get("tags", [])
    pid = session.get("player_id", "")
    custom = load_custom_tags(pid)
    progress = level_info(get_total_xp(pid))
    return render_template("menu.html", name=session["name"], progress=progress, owner_unlocked=bool(session.get("owner_unlocked")),
        mod_unlocked=bool(session.get("mod_unlocked")), tags=names, available_tags=AVAILABLE_TAGS,
        tag_colors=TAG_COLORS, custom_tags=custom, disabled_custom_tags=disabled_tags.get(session.get("player_id", ""), set()), owner_error=session.pop("owner_error", None),
        tags_message=session.pop("tags_message", None))


@app.route("/role-access", methods=["POST"])
def role_access():
    if "name" not in session:
        return redirect(url_for("home"))
    entered = request.form.get("access_code", "")
    names = set(session.get("tags", []))
    if secrets.compare_digest(entered, OWNER_CODE):
        session["owner_unlocked"] = True
        session["mod_unlocked"] = True
        names.update(["OWNER", "EMPEROR"])
        names.update(AVAILABLE_TAGS)
        session["tags"] = [tag for tag in AVAILABLE_TAGS if tag in names]
        session["owner_error"] = None
    elif secrets.compare_digest(entered, MOD_CODE):
        session["mod_unlocked"] = True
        names.update(tag for tag in AVAILABLE_TAGS if tag != "OWNER")
        session["tags"] = [tag for tag in AVAILABLE_TAGS if tag in names]
        session["owner_error"] = None
    else:
        session["owner_error"] = "Incorrect code. Tag and staff controls remain locked."
    return redirect(url_for("menu"))


@app.route("/set-tags", methods=["POST"])
def set_tags():
    if "name" not in session:
        return redirect(url_for("home"))
    if not (session.get("owner_unlocked") or session.get("mod_unlocked")):
        return "Forbidden: enter the correct staff code first.", 403
    selected = set(request.form.getlist("tags")) & set(AVAILABLE_TAGS)
    if session.get("mod_unlocked") and not session.get("owner_unlocked"):
        selected.discard("OWNER")
    session["tags"] = [tag for tag in AVAILABLE_TAGS if tag in selected]
    disabled_tags.setdefault(player_id(), set()).difference_update(selected)
    session["tags_message"] = "Tag settings saved. Your role permissions remain separate from visible tags."
    return redirect(url_for("menu"))


@app.route("/set-custom-tags", methods=["POST"])
def set_custom_tags():
    if "name" not in session:
        return redirect(url_for("home"))
    pid = player_id()
    assigned = manual_tags.get(pid, [])
    assigned_names = {tag["name"].casefold() for tag in assigned}
    selected = {name.casefold() for name in request.form.getlist("custom_tags")} & assigned_names
    hidden = disabled_tags.setdefault(pid, set())
    hidden.difference_update(assigned_names)
    hidden.update(assigned_names - selected)
    for tag in assigned:
        persist_tag_enabled(pid, tag["name"], tag["name"].casefold() in selected)
    session["tags_message"] = "Your visible custom tags have been updated. Your staff permissions are unchanged."
    return redirect(url_for("menu"))


@app.route("/api/giphy")
def giphy_search():
    api_key = os.environ.get("GIPHY_API_KEY", "").strip()
    if not api_key:
        return jsonify({"error": "Set GIPHY_API_KEY in your environment to enable GIF search."}), 503
    query = request.args.get("q", "trending").strip()[:80] or "trending"
    endpoint = "https://api.giphy.com/v1/gifs/search?" + urllib.parse.urlencode({"api_key": api_key, "q": query, "limit": 12, "rating": "pg-13", "lang": "en"})
    try:
        with urllib.request.urlopen(endpoint, timeout=8) as response:
            payload = json.loads(response.read().decode("utf-8"))
        gifs = []
        for item in payload.get("data", []):
            images = item.get("images", {})
            preview = images.get("fixed_width_small", {}).get("url") or images.get("preview_gif", {}).get("url")
            original = images.get("original", {}).get("url")
            if preview and original:
                gifs.append({"preview": preview, "url": original, "title": item.get("title", "GIF")})
        return jsonify({"gifs": gifs})
    except (urllib.error.URLError, TimeoutError, ValueError):
        return jsonify({"error": "GIPHY could not be reached. Try again."}), 502


@app.route("/api/rooms")
def room_list():
    return jsonify([{"code": code, "players": len(room["players"]), "limit": MAX_PLAYERS}
                    for code, room in rooms.items()])


@app.route("/host", methods=["POST"])
def host():
    if "name" not in session:
        return redirect(url_for("home"))
    global_ban = get_global_ban(session["name"])
    if global_ban:
        return f"You are globally banned. Reason: {global_ban['reason']}", 403
    code = "".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(4))
    while code in rooms:
        code = "".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(4))
    rooms[code] = {"players": {}, "host_player_id": player_id(), "banned": set(), "banned_names": set(), "banned_name_ids": {}, "manual_muted": {}, "messages": {}}
    return redirect(url_for("room_page", code=code))


@app.route("/join/<code>", methods=["POST"])
def join_page(code):
    if "name" not in session:
        return redirect(url_for("home"))
    global_ban = get_global_ban(session["name"])
    if global_ban:
        return f"You are globally banned. Reason: {global_ban['reason']}", 403
    room = rooms.get(code)
    if room is None:
        return "Room not found. It may have closed.", 404
    if player_id() in room["banned"] or session["name"].casefold() in room["banned_names"]:
        return "You are banned from this room.", 403
    if len(room["players"]) >= MAX_PLAYERS:
        return "Room is full.", 403
    return redirect(url_for("room_page", code=code))


@app.route("/room/<code>")
def room_page(code):
    if "name" not in session:
        return redirect(url_for("home"))
    global_ban = get_global_ban(session["name"])
    if global_ban:
        return f"You are globally banned. Reason: {global_ban['reason']}", 403
    if code not in rooms:
        return "Room not found. Go back to the room list and try another room.", 404
    room = rooms[code]
    if player_id() in room["banned"] or session["name"].casefold() in room["banned_names"]:
        return "You are banned from this room.", 403
    return render_template("room.html", code=code, name=session["name"], is_staff=bool(session.get("owner_unlocked") or session.get("mod_unlocked")))


@socketio.on("join")
def on_join(data):
    code = str((data or {}).get("code", "")).lower()
    name = session.get("name")
    pid = player_id()
    if not name or code not in rooms:
        emit("error_message", {"message": "That room no longer exists."})
        return
    global_ban = get_global_ban(name)
    if global_ban:
        emit("error_message", {"message": f"You are globally banned. Reason: {global_ban['reason']}"})
        return
    room = rooms[code]
    if pid in room["banned"] or name.casefold() in room["banned_names"]:
        emit("error_message", {"message": "You are banned from this room."})
        return
    if player_rooms.get(request.sid) == code:
        remaining = max(0, int(automod_muted_until.get(pid, 0) - time.time()))
        if remaining:
            emit("mute_status", {"seconds": remaining, "reason": "Automatic moderation"})
        return
    leave_current_room(request.sid)
    room = rooms.get(code)
    if room is None:
        emit("error_message", {"message": "That room no longer exists."})
        return
    if len(room["players"]) >= MAX_PLAYERS:
        emit("error_message", {"message": "This room is full."})
        return
    join_room(code)
    role_tags = current_tags()
    is_owner_role = bool(session.get("owner_unlocked"))
    is_mod_role = bool(session.get("mod_unlocked"))
    # Include any custom tags issued earlier by the owner.
    player = {"name": name, "tags": role_tags, "player_id": pid, "is_owner": is_owner_role, "is_mod": is_mod_role}
    room["players"][request.sid] = player
    player_rooms[request.sid] = code
    announce(code, f"{name} joined the room.")
    sync_room(code)
    emit("xp_update", level_info(get_total_xp(pid)))
    remaining = max(0, int(automod_muted_until.get(pid, 0) - time.time()))
    manual_remaining = max(0, int(room["manual_muted"].get(pid, 0) - time.time()))
    stored_mute = get_active_mute(name, code)
    stored_remaining = max(0, int(stored_mute["expires_at"] - time.time())) if stored_mute else 0
    if remaining or manual_remaining or stored_remaining:
        reason = stored_mute["reason"] if stored_mute else "You are currently muted."
        emit("mute_status", {"seconds": max(remaining, manual_remaining, stored_remaining), "reason": reason})


@socketio.on("message")
def on_message(data):
    data = data or {}
    code = str(data.get("code", "")).lower()
    message = str(data.get("message", "")).strip()
    if not message or len(message) > 500 or code not in rooms or player_rooms.get(request.sid) != code:
        return
    room = rooms[code]
    player = room["players"].get(request.sid)
    if not player:
        return
    pid = player["player_id"]
    auto_remaining = max(0, int(automod_muted_until.get(pid, 0) - time.time()))
    manual_remaining = max(0, int(room["manual_muted"].get(pid, 0) - time.time()))
    stored_mute = get_active_mute(player["name"], code)
    stored_remaining = max(0, int(stored_mute["expires_at"] - time.time())) if stored_mute else 0
    remaining = max(auto_remaining, manual_remaining, stored_remaining)
    if remaining:
        emit("mute_status", {"seconds": remaining, "reason": "You are currently muted."})
        return
    if message.startswith("/"):
        handle_command(code, request.sid, message)
        return
    triggered = contains_moderation_term(message)
    safe_message = filter_message(message)
    message_id = uuid.uuid4().hex
    room.setdefault("messages", {})[message_id] = {"name": player["name"], "player_id": pid}
    emit("chat_message", {"id": message_id, "name": player["name"], "tags": player["tags"], "message": safe_message, "staff": bool(player.get("is_owner") or player.get("is_mod"))}, to=code)
    previous_level = level_info(get_total_xp(pid))["level"]
    xp_info = award_message_xp(pid)
    emit("xp_update", xp_info, to=request.sid)
    if xp_info["level"] != previous_level:
        player["tags"] = current_tags()
        sync_room(code)
        announce(code, f"{player['name']} reached level {xp_info['level']}!")
    if triggered:
        automod_muted_until[pid] = time.time() + 60
        save_mute(player["name"], None, "Automatic moderation", "Roomspace", time.time() + 60)
        emit("mute_status", {"seconds": 60, "reason": "Swearing or a detected slur"})


@socketio.on("delete_message")
def on_delete_message(data):
    data = data or {}
    code = str(data.get("code", "")).lower()
    message_id = str(data.get("id", ""))
    if code not in rooms or player_rooms.get(request.sid) != code:
        return
    room = rooms[code]
    actor = room["players"].get(request.sid)
    if not actor or not is_staff(actor):
        emit("error_message", {"message": "Only staff can delete messages."}, to=request.sid)
        return
    if message_id not in room.setdefault("messages", {}):
        return
    room["messages"].pop(message_id, None)
    emit("remove_message", {"id": message_id}, to=code)


@socketio.on("leave_room")
def on_leave_room(data):
    leave_current_room(request.sid)
    emit("room_left", {}, to=request.sid)


@socketio.on("disconnect")
def on_disconnect():
    leave_current_room(request.sid)


if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5000, debug=False)
