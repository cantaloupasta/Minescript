"""
Unified Fishing Bot

Modes: Default (fish only) | Treasure (+ apnea breathing) | Trophy (+ golden
fish detect/aim/use) | Magma (+ clear blazes/piglins) | Worm (+ clear
silverfish) | Strider (+ clear striders, melee).

Totem placement (toggle, Default/Magma/Worm/Strider): every 302s, places on
a block within TOTEM_PLACE_RANGE, >TOTEM_NPC_EXCLUSION from a "CLICK" armor
stand, and to the SIDE of the saved orientation (not front/back, so it can't
block rod casts or worm's straight-down attack). Returns to saved orientation
after.

"Saved orientation" = yaw/pitch captured once at fishing_loop start; used as
the return point after interrupts and as the front/back reference for totem
placement (relative to saved yaw, not live yaw).
"""

import time
import random
import math
import queue
import re
import tkinter as tk
import minescript as m
import base64
import json

from system.lib.java import JavaClass
from threading import Thread, Lock
from system.lib.minescript import EventType, EventQueue
from eventlib import *  # must be imported before any EventQueue() is instantiated

events = EventQueue()
_Minecraft = JavaClass("net.minecraft.client.Minecraft")


def safe_entities(**kwargs):
    """m.entities() wrapper that swallows the known NaN-velocity Gson crash
    (e.g. a bobber the instant it's cast/reeled) which otherwise kills the
    calling thread; skipping that one poll costs nothing (~0.1s later)."""
    try:
        return m.entities(**kwargs)
    except Exception as e:
        m.log(f"[!] entities() call failed (likely NaN velocity glitch), skipping this poll: {e}")
        return []

# ==============================================================================
# GOLDEN FISH DETECTOR
# Mirrors SkyHanni's GoldenFishTimer.kt: confirms via (1) the spawn chat line
# and (2) a nearby armor stand wearing a player_head skin.
#
# Chat is handled via eventlib's CHAT listener (eventlib=True), which
# populates a `.json` field on top of the normal chat text. Unlike the old
# INCOMING_CHAT_INTERCEPT approach, this listener is purely observational --
# it doesn't cancel/hide incoming chat lines, so normal chat still displays
# for the player the whole time the bot runs. On the spawn line, Python does
# a direct entities(nbt=True) scan for speed, falling back to the Pyjinn
# sticky-lock scanner (runs continuously on "render") if the entity hasn't
# loaded client-side yet. chat_listener_worker() does the confirmation/echo
# and feeds the resolved UUID to the aim/use step.
#
# NOTE: NBT-scan reliability unverified; Pyjinn scanner logs (not chats) each
# new sighting so you can tail latest.log to confirm it's firing independently.
# ==============================================================================
BITE_ENTITY_NAME = "!!!"  # bite-indicator armor stand name; never treat this as the golden fish
GOLDEN_FISH_SPAWN_TEXT = "surface from beneath the lava"
GOLDEN_FISH_DESPAWN_TEXT = "swims back beneath the lava"
GOLDEN_FISH_CATCH_TEXT = "you caught a golden fish"

GOLDEN_FISH_PROFILE_ID = "b7fdbe67cd004683b9fa9e3e17738254"
GOLDEN_FISH_SCAN_INTERVAL = 0.5


golden_fish_confirmed = False
golden_fish_uuid = None

def extract_profile_id(nbt_str):
    if not nbt_str or "minecraft:player_head" not in nbt_str:
        return None
    match = re.search(r'value:"([^"]+)"', nbt_str)
    if not match:
        return None
    try:
        decoded = base64.b64decode(match.group(1)).decode("utf-8")
        return json.loads(decoded).get("profileId")
    except Exception:
        return None

def find_golden_fish_entity(max_distance=None):
    try:
        kwargs = {"nbt": True}
        if max_distance is not None:
            kwargs["max_distance"] = max_distance
        for e in safe_entities(**kwargs):
            if "armor_stand" not in str(getattr(e, "type", "")).lower():
                continue
            if extract_profile_id(getattr(e, "nbt", None)) == GOLDEN_FISH_PROFILE_ID:
                return e.uuid, tuple(e.position)
    except Exception as ex:
        m.echo(f"[!] Golden fish entity scan failed: {ex}")
    return None, None

def golden_fish_scanner_worker():
    global golden_fish_uuid
    while True:
        if golden_fish_confirmed and golden_fish_enabled():
            uuid, _ = find_golden_fish_entity()
            if uuid:
                golden_fish_uuid = uuid
        time.sleep(GOLDEN_FISH_SCAN_INTERVAL)
        
def golden_fish_enabled():
    """True only while running AND Trophy mode selected -- gate so MB4-stop
    actually halts the golden-fish background threads, not just `running`."""
    return running and var_mode.get() == "Trophy"


def disable_golden_fish_tracking():
    """Clears golden-fish state (no camera change) so tracking/aiming stops
    immediately on stop/mode-switch instead of waiting for the next chat line."""
    global golden_fish_confirmed, golden_fish_uuid
    golden_fish_confirmed = False
    golden_fish_uuid = None


def strip_mc_colors(s):
    """Removes Minecraft §-color/format codes for plain-text pattern matching."""
    return re.sub("\u00a7.", "", s)


def debug_print_targeted_entity():
    """Debug: echoes crosshair entity's position/name/uuid/player_head NBT --
    point at an armor stand to read its real coords instead of guessing."""
    try:
        target = m.player_get_targeted_entity(max_distance=20, nbt=True)
    except Exception as ex:
        m.echo(f"[Debug] Failed to get targeted entity: {ex}")
        return

    if not target:
        m.echo("[Debug] No entity in crosshair (within 20 blocks).")
        return

    x, y, z = target.position
    name = getattr(target, "name", "") or "(unnamed)"
    nbt = getattr(target, "nbt", None) or ""

    m.echo(f"[Debug] type={target.type}  name={name}  uuid={target.uuid}")
    m.echo(f"[Debug] exact pos=({x:.3f}, {y:.3f}, {z:.3f})")
    m.echo(f"[Debug] rounded block pos=({round(x)}, {round(y)}, {round(z)})")

    has_player_head = "player_head" in str(nbt).lower()
    m.echo(f"[Debug] has player_head NBT: {has_player_head}")


def get_entity_position_by_uuid(uuid):
    """Live position lookup by UUID (no NBT needed once we have it).
    Returns None if the entity no longer exists (e.g. caught)."""
    try:
        matches = safe_entities(uuid=uuid)
        if matches:
            return matches[0].position
    except Exception as ex:
        m.echo(f"[!] Golden fish position lookup failed: {ex}")
    return None


def resolve_golden_fish_uuid_and_pos():
    """(Re)acquire the golden fish: direct scan first, Pyjinn sticky-lock
    fallback. Returns (uuid, pos); pos may be None on the fallback path."""
    global golden_fish_uuid
    uuid, pos = find_golden_fish_entity()
    if uuid:
        golden_fish_uuid = uuid
        return uuid, pos

    uuid = golden_fish_uuid
    if uuid:
        golden_fish_uuid = uuid
        pos = get_entity_position_by_uuid(uuid)
    return uuid, pos


def track_step_look_at(tx, ty, tz, turn_speed=0.35):
    """Small nudge toward target instead of a full eased curve each tick --
    replaying smooth_look_at() every 0.1s is what made tracking look jittery."""
    try:
        px, py, pz = m.player_position()
        dx, dy, dz = tx - px, ty - (py + 1.62), tz - pz
        dist = math.sqrt(dx ** 2 + dz ** 2)
        target_yaw = -math.atan2(dx, dz) * 180.0 / math.pi
        target_pitch = -math.atan2(dy, dist) * 180.0 / math.pi

        current_yaw, current_pitch = m.player_orientation()
        diff_yaw = (target_yaw - current_yaw + 180) % 360 - 180
        diff_pitch = target_pitch - current_pitch

        m.player_set_orientation(
            current_yaw + diff_yaw * turn_speed,
            current_pitch + diff_pitch * turn_speed,
        )
    except Exception:
        pass


GOLDEN_FISH_MELEE_RANGE = 3.0  # below this distance the rod's "use" is disabled by the game
GOLDEN_FISH_JUMP_APEX_DELAY = 0.25  # seconds after jumping to wait before aiming+using (timed, not velocity-based)

# True while an aim-then-use sequence runs, so the tracker's re-aim doesn't fight it.
golden_fish_busy = False


def snap_look_at(tx, ty, tz):
    """Instant (no interpolation) orientation snap -- for the jump-peak
    trick where the aim window is too short for a smoothed look."""
    try:
        px, py, pz = m.player_position()
        dx, dy, dz = tx - px, ty - (py + 1.62), tz - pz
        dist = math.sqrt(dx ** 2 + dz ** 2)
        yaw = -math.atan2(dx, dz) * 180.0 / math.pi
        pitch = -math.atan2(dy, dist) * 180.0 / math.pi
        m.player_set_orientation(yaw, pitch)
    except Exception:
        m.player_look_at(tx, ty, tz)


def jump_and_use_at_peak(tx, ty, tz):
    """Rod 'use' is disabled within GOLDEN_FISH_MELEE_RANGE, so jump, wait
    for the apex (timed, not velocity-polled), snap-aim, and fire."""
    m.player_press_jump(True)
    time.sleep(0.05)
    m.player_press_jump(False)

    time.sleep(GOLDEN_FISH_JUMP_APEX_DELAY)

    snap_look_at(tx, ty, tz)
    use_rod()


def aim_and_use_rod_on_golden_fish(uuid, pos=None):
    """Aim must fully settle before the rod fires or the cast misses; uses
    the jump-peak trick when within melee range. `pos` skips a redundant
    lookup on the first shot if already known."""
    global golden_fish_busy
    golden_fish_busy = True  # pause the tracker's own aiming for this sequence
    try:
        if pos is None:
            pos = get_entity_position_by_uuid(uuid)
        if pos is None:
            # No confirmed position yet -- best effort, still try the rod.
            use_rod()
            return
        x, y, z = pos
        ty = y + 2  # aim at the head/top of the stand, not its feet
        try:
            px, py, pz = m.player_position()
            dist = math.sqrt((x - px) ** 2 + (y - py) ** 2 + (z - pz) ** 2)
        except Exception:
            dist = None

        if dist is not None and dist <= GOLDEN_FISH_MELEE_RANGE:
            jump_and_use_at_peak(x, ty, z)
        else:
            # Aim first and let it fully settle, THEN use the rod.
            smooth_look_at(x, ty, z, base_steps=6)
            time.sleep(random.uniform(0.03, 0.06))
            use_rod()
    finally:
        golden_fish_busy = False


GOLDEN_FISH_MISSING_LIMIT = 5  # consecutive failed lookups (~0.5s) before treating fish as gone (safety-net fallback only)


def resolve_golden_fish_encounter():
    """Clears golden-fish state and hands aim back to fishing_loop. Called on
    catch, on despawn, or as a fallback from the tracker (GOLDEN_FISH_MISSING_LIMIT)
    so the bot never gets stuck with camera/control still on a gone fish."""
    global golden_fish_confirmed, golden_fish_uuid
    golden_fish_confirmed = False
    golden_fish_uuid = None
    return_to_saved_orientation()


def golden_fish_tracker_worker():
    """Re-aims at the confirmed fish via small continuous nudges (not a
    replayed ease curve, which caused jitter). Pauses during an active
    aim_and_use sequence, resolves the encounter as a safety net if the
    entity stops resolving for GOLDEN_FISH_MISSING_LIMIT checks, and stops
    immediately (no chat-line wait) on stop/mode-switch."""
    missing_count = 0
    while True:
        if golden_fish_confirmed and golden_fish_enabled() and not golden_fish_busy:
            uuid = golden_fish_uuid

            pos = get_entity_position_by_uuid(uuid) if uuid else None
            if pos:
                missing_count = 0
                x, y, z = pos
                track_step_look_at(x, y + 2, z)
            else:
                missing_count += 1
                if missing_count >= GOLDEN_FISH_MISSING_LIMIT:
                    missing_count = 0
                    m.echo("[*] Golden fish encounter ended -- resuming normal fishing.")
                    resolve_golden_fish_encounter()
        else:
            missing_count = 0
            if golden_fish_confirmed and not golden_fish_enabled():
                disable_golden_fish_tracking()
        time.sleep(0.1)


# ==============================================================================
# FIXED CONSTANTS (not user-configurable)
# ==============================================================================
TOTEM_PLACE_RANGE = 5        # totem candidate block must be within this many blocks of the player
TOTEM_NPC_EXCLUSION = 5      # totem candidate must be MORE than this many blocks from a "CLICK" NPC
TOTEM_INTERVAL_SECONDS = 302
TOTEM_SIDE_MIN_ANGLE = 45    # candidate must be at least this far (deg) from the saved front
TOTEM_SIDE_MAX_ANGLE = 135   # and at least this far (deg) from the saved back
STRIDER_ATTACK_RANGE = 5     # striders must be within this many blocks to be engaged
MOUSE_BUTTON_STOP = 3        # mouse button 4

BITE_TIMEOUT_SECONDS_DEFAULT = 8  # fallback if var_bite_timeout is unset/invalid
MAX_CONSECUTIVE_TIMEOUTS_DEFAULT = 5  # fallback if var_max_timeouts is unset/invalid
AUTO_LOBBY_HOURS_DEFAULT = 4  # fallback if var_auto_lobby_hours is unset/invalid; <=0 disables the feature

MODES_ALLOWING_TOTEM = ("Default", "Magma", "Worm", "Strider", "Trophy")
MODES_WITH_CLEARING = ("Magma", "Worm", "Strider")

# ==============================================================================
# RUNTIME STATE
# ==============================================================================
running = False
bot_thread = None
totem_thread_handle = None
bot_start_time = None
space_full = False
current_slot = None

saved_yaw = 0.0
saved_pitch = 0.0
_orientation_lock = Lock()


# ==============================================================================
# ORIENTATION HELPERS
# ==============================================================================
def get_saved_orientation():
    with _orientation_lock:
        return saved_yaw, saved_pitch


def set_saved_orientation(yaw, pitch):
    global saved_yaw, saved_pitch
    with _orientation_lock:
        saved_yaw = yaw
        saved_pitch = pitch


def smooth_look_to(target_yaw, target_pitch, steps=10):
    """Smoothly interpolates camera to explicit yaw/pitch angles."""
    try:
        current_yaw, current_pitch = m.player_orientation()
    except (AttributeError, TypeError, ValueError):
        m.player_set_orientation(target_yaw, target_pitch)
        return

    diff_yaw = (target_yaw - current_yaw + 180) % 360 - 180
    diff_pitch = target_pitch - current_pitch

    for i in range(1, steps + 1):
        pct = i / steps
        ease = math.sin((pct * math.pi) / 2)
        jitter = random.uniform(-0.01, 0.01)
        m.player_set_orientation(
            current_yaw + diff_yaw * ease + jitter,
            current_pitch + diff_pitch * ease + jitter,
        )
        time.sleep(random.uniform(0.006, 0.012))


def smooth_look_at(tx, ty, tz, base_steps=8):
    """Smoothly interpolates camera to a world position."""
    try:
        px, py, pz = m.player_position()
        dx, dy, dz = tx - px, ty - (py + 1.62), tz - pz
        dist = math.sqrt(dx ** 2 + dz ** 2)
        target_yaw = -math.atan2(dx, dz) * 180.0 / math.pi
        target_pitch = -math.atan2(dy, dist) * 180.0 / math.pi
        smooth_look_to(target_yaw, target_pitch, steps=base_steps)
    except Exception:
        m.player_look_at(tx, ty, tz)


def return_to_saved_orientation():
    yaw, pitch = get_saved_orientation()
    smooth_look_to(yaw, pitch, steps=10)
# ==============================================================================
# FISHING BOBBER TRACKER
# ==============================================================================
def get_my_fishing_hook():
    """Local player's own FishingHook, or None -- same ref the client uses
    to render the rod as cast, so it can't pick up someone else's bobber."""
    try:
        client = _Minecraft.getInstance()
        player = client.player
        if not player:
            return None
        # Field name is mapping/version-dependent (commonly "fishing"); if this
        # raises, run \install_mappings and check java_field_names(Player).
        hook = player.fishing
        return hook if hook else None
    except Exception as ex:
        m.echo(f"[!] Fishing hook lookup failed: {ex}")
        return None
# ==============================================================================
# SLOT / INPUT HELPERS
# ==============================================================================
def select_slot(slot):
    global current_slot
    if current_slot != slot:
        m.player_inventory_select_slot(slot)
        current_slot = slot
        time.sleep(random.uniform(0.05, 0.08))


def real_slot(var):
    """1-indexed UI hotbar slot (1-9) -> 0-indexed slot for the API."""
    return int(var.get()) - 1


def press_use():
    m.player_press_use(True)
    time.sleep(random.uniform(0.05, 0.09))
    m.player_press_use(False)


def press_attack():
    m.player_press_attack(True)
    time.sleep(random.uniform(0.05, 0.09))
    m.player_press_attack(False)


def use_rod():
    select_slot(real_slot(var_slot_rod))
    press_use()


def breathe():
    m.echo("[*] *GASPS* for air!")
    m.player_press_jump(True)
    time.sleep(random.uniform(0.2, 0.3))
    m.player_press_jump(False)
    time.sleep(random.uniform(0.1, 0.15))
    m.player_press_sneak(True)
    time.sleep(random.uniform(0.2, 0.3))
    m.player_press_sneak(False)


# ==============================================================================
# CLEARING / ATTACK MECHANICS (Magma / Worm / Strider)
# ==============================================================================
def is_magma_target(e):
    t = str(getattr(e, "type", "")).lower()
    return any(k in t for k in ("blaze", "zombified_piglin", "zombie_pigman", "pigman"))


def is_strider_target(e):
    return "strider" in str(getattr(e, "type", "")).lower()


def is_worm_target(e):
    return "silverfish" in str(getattr(e, "type", "")).lower()


def is_worm_chimney():
    """Chimney = plain look-down fishing (no clearing/totem), same as Default.
    Funnel is the original Worm behavior; left untouched."""
    return var_mode.get() == "Worm" and var_worm_submode.get() == "Chimney"


# ==============================================================================
# CHAT LISTENER ACTIVATION (mode-gated)
# ==============================================================================
# Uses eventlib's plain CHAT listener (register_chat_listener(eventlib=True))
# rather than the old INCOMING_CHAT_INTERCEPT approach. This listener is
# purely observational -- it does NOT cancel/hide incoming chat lines, so
# normal chat stays visible to the player the whole time window.py runs.
# It's still mode-gated (on/off) simply to avoid running the extra listener
# and its per-line processing for modes that don't need it: Trophy (golden
# fish text), Magma/Strider ("not enough space"), Worm/Funnel ("not enough
# space"). Not needed for Default, Treasure, or Worm/Chimney.
chat_listener_registered = False        # is the eventlib chat listener currently ON?
chat_listener_ever_registered = False   # has register_chat_listener(eventlib=True) ever been called?
_chat_listener_lock = Lock()


def mode_needs_chat_listener():
    mode = var_mode.get()
    if mode in ("Trophy", "Magma", "Strider"):
        return True
    if mode == "Worm" and not is_worm_chimney():
        return True
    return False


def _set_chat_listener_state(enabled):
    """Flips the eventlib chat listener's Java-side flag directly (mirrors
    register/unregister_all) -- used after first registration so the queue
    isn't re-appended and events don't get delivered twice."""
    execute(
        fr"""\eval '0' '__script__.vars["game"]["eventlib"]["{identifier}"]["chat_listener"] = {enabled}'"""
    )


def update_chat_listener_state():
    """Enables/disables the eventlib chat listener to match whether the
    current mode needs it. Safe to call any time the mode selection changes."""
    global chat_listener_registered, chat_listener_ever_registered
    with _chat_listener_lock:
        needed = mode_needs_chat_listener()
        if needed and not chat_listener_registered:
            if not chat_listener_ever_registered:
                events.register_chat_listener(eventlib=True)
                chat_listener_ever_registered = True
            else:
                _set_chat_listener_state(True)
            chat_listener_registered = True
        elif not needed and chat_listener_registered:
            _set_chat_listener_state(False)
            chat_listener_registered = False


def attack_magma(detect_distance):
    """Aim at blazes/zombified piglins and use the ranged weapon until none remain."""
    m.echo("[*] Space full! Clearing Blazes/Zombified Piglins...")
    select_slot(real_slot(var_slot_ranged))
    while running:
        target = next(
            (e for e in safe_entities(max_distance=detect_distance, sort="nearest") if is_magma_target(e)),
            None,
        )
        if not target:
            break
        smooth_look_at(target.position[0], target.position[1] + 0.4, target.position[2], base_steps=4)
        time.sleep(random.uniform(0.03, 0.08))
        press_use()
        time.sleep(random.uniform(0.03, 0.08))


def attack_strider():
    """Aim at striders within 5 blocks and melee them until none remain."""
    m.echo("[*] Space full! Clearing Striders...")
    select_slot(real_slot(var_slot_melee))
    while running:
        target = next(
            (e for e in safe_entities(max_distance=STRIDER_ATTACK_RANGE, sort="nearest") if is_strider_target(e)),
            None,
        )
        if not target:
            break
        smooth_look_at(target.position[0], target.position[1] + 0.4, target.position[2], base_steps=4)
        time.sleep(random.uniform(0.03, 0.08))
        press_attack()
        time.sleep(random.uniform(0.03, 0.08))


def attack_worm(detect_distance):
    """Look straight down and use the ranged weapon until all silverfish are dead."""
    m.echo("[*] Space full! Clearing Silverfish...")
    select_slot(real_slot(var_slot_ranged))
    yaw, _ = get_saved_orientation()
    smooth_look_to(yaw, 90.0, steps=6)  # straight down
    press_use()


def handle_clearing(mode, detect_distance):
    global space_full
    if mode == "Magma":
        attack_magma(detect_distance)
    elif mode == "Strider":
        attack_strider()
    elif mode == "Worm":
        attack_worm(detect_distance)
    space_full = False
    return_to_saved_orientation()


# ==============================================================================
# TOTEM PLACEMENT
# ==============================================================================
def find_totem_target():
    """Finds a placeable block that's within reach, clear of CLICK NPCs, and
    to the side (not front/back) of the saved fishing orientation."""
    px, py, pz = m.player_position()
    ix, iy, iz = int(px), int(py), int(pz)
    r = TOTEM_PLACE_RANGE

    coords = [
        [x, y, z]
        for x in range(ix - r, ix + r + 1)
        for y in range(iy - r, iy + r + 1)
        for z in range(iz - r, iz + r + 1)
    ]
    blocks = m.getblocklist(coords)
    block_dict = {tuple(c): b for c, b in zip(coords, blocks)}

    click_coords = []
    try:
        for e in safe_entities():
            if getattr(e, "name", "") == "CLICK":
                click_coords.append(tuple(e.position))
    except Exception as ex:
        m.echo(f"[!] Warning: Could not fetch entities for CLICK check ({ex})")

    yaw, _ = get_saved_orientation()
    valid = []

    for (x, y, z), b in block_dict.items():
        if not b or "air" in b or "lava" in b or "water" in b:
            continue

        above = block_dict.get((x, y + 1, z))
        if not above or "air" not in above:
            continue

        tx, ty, tz = x + 0.5, y + 1.0, z + 0.5
        dist = math.sqrt((tx - px) ** 2 + (ty - py) ** 2 + (tz - pz) ** 2)
        if not (0.5 <= dist <= TOTEM_PLACE_RANGE):
            continue

        too_close_to_npc = False
        for (cx, cy, cz) in click_coords:
            if math.sqrt((tx - cx) ** 2 + (ty - cy) ** 2 + (tz - cz) ** 2) <= TOTEM_NPC_EXCLUSION:
                too_close_to_npc = True
                break
        if too_close_to_npc:
            continue

        # Reject anything in the saved-orientation's front/back cone; only the sides are valid.
        block_yaw = -math.atan2(tx - px, tz - pz) * 180.0 / math.pi
        angle = (block_yaw - yaw + 180) % 360 - 180
        if not (TOTEM_SIDE_MIN_ANGLE <= abs(angle) <= TOTEM_SIDE_MAX_ANGLE):
            continue

        valid.append((dist, tx, ty, tz))

    if not valid:
        return None
    valid.sort(key=lambda v: v[0])
    return valid[0][1:]

def place_totem_cycle():
    target = find_totem_target()
    if not target:
        m.echo("[!] No valid totem spot found (side-only, in-reach, NPC-clear).")
        return
    tx, ty, tz = target
    smooth_look_at(tx, ty, tz, base_steps=10)
    
    # Select totem and place it
    select_slot(real_slot(var_slot_totem))
    time.sleep(0.05)
    m.player_press_use(True)
    time.sleep(0.08)
    m.player_press_use(False)
    
    return_to_saved_orientation()
    m.echo("[*] Totem placed.")

    try:
        select_slot(real_slot(var_slot_rod))
    except NameError:
        pass

# ==============================================================================
# MAIN FISHING LOOP
# ==============================================================================
def fishing_loop():
    global running, space_full

    mode = var_mode.get()
    try:
        detect_distance = int(var_detect_distance.get())
    except ValueError:
        detect_distance = 7
    try:
        apnea_time = int(var_apnea.get())
    except ValueError:
        apnea_time = 90
    try:
        bite_timeout = float(var_bite_timeout.get())
    except ValueError:
        bite_timeout = BITE_TIMEOUT_SECONDS_DEFAULT
    try:
        max_timeouts = int(var_max_timeouts.get())
    except ValueError:
        max_timeouts = MAX_CONSECUTIVE_TIMEOUTS_DEFAULT
    try:
        auto_lobby_hours = float(var_auto_lobby_hours.get())
    except ValueError:
        auto_lobby_hours = AUTO_LOBBY_HOURS_DEFAULT

    try:
        set_saved_orientation(*m.player_orientation())
    except Exception:
        set_saved_orientation(0.0, 0.0)

    last_action = time.time()
    last_breath = time.time()
    last_totem = time.time()
    timeout_count = 0

    use_rod()  # initial cast

    while running:
        # --- auto-lobby check ---
        if auto_lobby_hours > 0 and bot_start_time and (time.time() - bot_start_time) >= auto_lobby_hours * 3600:
            m.echo(f"[!] Auto-lobby time ({auto_lobby_hours}h) reached. Sending player to lobby.")
            m.execute("/lobby")
            running = False
            break
        # --- totem placement (Worm/Chimney never places, even if checkbox left on) ---
        if var_totem.get() and mode in MODES_ALLOWING_TOTEM and not is_worm_chimney():
            if time.time() - last_totem >= TOTEM_INTERVAL_SECONDS:
                use_rod()  # reel in the bobber safely before looking away
                time.sleep(0.05)
                place_totem_cycle()
                time.sleep(0.05)
                use_rod()  # cast the bobber back out
                
                last_action = time.time()
                last_totem = time.time()
                continue
        # --- clearing interrupt (magma / worm-funnel / strider; Worm/Chimney skips, behaves like Default) ---
        if mode in MODES_WITH_CLEARING and space_full and not is_worm_chimney():
            use_rod()  # reel in before switching to weapon
            handle_clearing(mode, detect_distance)
            use_rod()  # recast
            last_action = time.time()
            continue

        # --- treasure: periodic breathing ---
        if mode == "Treasure" and time.time() - last_breath > apnea_time:
            breathe()
            last_breath = time.time()

        # Golden fish handling runs in the background threads (Trophy-gated); nothing to poll here.

        # --- bite scan ---
        entities = safe_entities(max_distance=detect_distance)
        bite = any(
            "armor_stand" in str(getattr(e, "type", "")).lower() and getattr(e, "name", "") == BITE_ENTITY_NAME
            for e in entities
        )
        if bite:
            timeout_count = 0  # reset consecutive-timeout counter on a real catch
            time.sleep(random.uniform(0.03, 0.06))
            use_rod()  # reel in
            last_action = time.time()
            time.sleep(random.uniform(0.01, 0.03))
            use_rod()  # recast
            time.sleep(1.0)

        # --- timeout / bobber recovery ---
        if not running:
            break
        if time.time() - last_action > bite_timeout:
            timeout_count += 1
            return_to_saved_orientation()
            bobber_present = get_my_fishing_hook() is not None
            if bobber_present:
                m.echo(f"Bobber lagging. Timeouts: {timeout_count}/{max_timeouts}")
                use_rod()
                time.sleep(random.uniform(0.03, 0.06))
                use_rod()
            else:
                m.echo(f"Bobber missing. Timeouts: {timeout_count}/{max_timeouts}")
                use_rod()
            last_action = time.time()

            if timeout_count >= max_timeouts:
                m.echo(f"[!] {max_timeouts} consecutive timeouts reached. Stopping bot.")
                m.execute("/lobby")
                running = False
                break

        time.sleep(0.1)
    root.after(0, update_button_ui)


# ==============================================================================
# BACKGROUND LISTENERS
# ==============================================================================
def chat_listener_worker():
    global space_full, golden_fish_confirmed, golden_fish_uuid
    update_chat_listener_state()  # only registers/enables if the starting mode needs it
    while True:
        event = events.get()

        if event.type != EventType.CHAT:
            continue

        if "There is not enough space" in event.message:
            space_full = True

        msg = strip_mc_colors(event.message)

        # Golden Fish tracking/handling only runs while the bot is started
        # AND Trophy mode is selected -- neither the mode alone nor pressing
        # stop (mouse button 4) used to be enough to fully disable it.
        if not golden_fish_enabled():
            disable_golden_fish_tracking()
            continue

        if GOLDEN_FISH_SPAWN_TEXT in msg:
            if not golden_fish_confirmed:
                golden_fish_confirmed = True

                # Directly scan for the closest unnamed player_head armor
                # stand right now instead of waiting on the Pyjinn scanner's
                # sticky UUID -- faster, and doesn't depend on a render-frame
                # sample lining up with this exact tick.
                uuid, pos = find_golden_fish_entity()

                if uuid is None:
                    # The entity may not have finished loading client-side
                    # in the same tick the chat line arrived -- a couple of
                    # quick retries before falling back to the Pyjinn
                    # sticky-lock scanner.
                    for _ in range(5):
                        time.sleep(0.1)
                        uuid, pos = find_golden_fish_entity()
                        if uuid:
                            break

                if uuid is None:
                    uuid = golden_fish_uuid
                    pos = get_entity_position_by_uuid(uuid) if uuid else None

                golden_fish_uuid = uuid
                if uuid:
                    if pos:
                        px, py, pz = pos
                        m.echo(f"[!] Golden fish is here! uuid={uuid}  pos=({px:.2f}, {py:.2f}, {pz:.2f})")
                    else:
                        m.echo(f"[!] Golden fish is here! uuid={uuid}  (position unknown)")
                    time.sleep(random.uniform(0.03, 0.06))
                    aim_and_use_rod_on_golden_fish(uuid, pos)
                else:
                    m.echo("[!] Golden fish is here! (no armor stand found -- using rod blind)")
                    use_rod()
        elif GOLDEN_FISH_DESPAWN_TEXT in msg:
            resolve_golden_fish_encounter()
        elif GOLDEN_FISH_CATCH_TEXT in msg.lower():
            m.echo("[*] Golden fish caught!")
            resolve_golden_fish_encounter()

        if "escapes your hook" in msg:
            time.sleep(random.uniform(0.03, 0.06))
            uuid, pos = resolve_golden_fish_uuid_and_pos()
            if uuid:
                aim_and_use_rod_on_golden_fish(uuid, pos)
            else:
                use_rod()

        if "is weak!" in msg:
            # This is the final reel of the encounter -- the fish gives up
            # and gets caught right after going weak. The actual catch is
            # confirmed separately by GOLDEN_FISH_CATCH_TEXT above/below
            # (the real "you caught a Golden Fish" chat line), so this just
            # does the reel and doesn't resolve the encounter itself.
            time.sleep(random.uniform(0.03, 0.06))
            uuid, pos = resolve_golden_fish_uuid_and_pos()
            if uuid:
                aim_and_use_rod_on_golden_fish(uuid, pos)
            else:
                use_rod()

def mouse_listener_worker():
    with m.EventQueue() as eq:
        eq.register_mouse_listener()
        while True:
            ev = eq.get()
            if ev and ev.type == m.EventType.MOUSE and ev.button == MOUSE_BUTTON_STOP and ev.action == 1:
                root.after(0, toggle_bot)


# ==============================================================================
# TKINTER UI
# ==============================================================================
def on_mode_change():
    mode = var_mode.get()

    if mode in ("Treasure", "Trophy") or is_worm_chimney():
        var_totem.set(False)
        chk_totem.config(state="disabled")
    else:
        chk_totem.config(state="normal")

    update_chat_listener_state()


def validate_slots():
    try:
        rod = int(var_slot_rod.get())
        melee = int(var_slot_melee.get())
        ranged = int(var_slot_ranged.get())
        totem = int(var_slot_totem.get())
    except ValueError:
        return False, "Slots must be whole numbers."
    if len({rod, melee, ranged, totem}) != 4:
        return False, "Rod, melee, ranged, and totem slots must all be different."
    return True, ""


def toggle_bot():
    global running, bot_thread, bot_start_time
    if running:
        running = False
        m.echo("[*] Bot stopped.")
        btn.config(text="Start Bot", bg="green")
        return

    ok, err = validate_slots()
    if not ok:
        lbl_error.config(text=err)
        return
    lbl_error.config(text="")

    running = True
    m.echo("[*] Bot started.")
    bot_start_time = time.time()
    btn.config(text="Stop Bot", bg="red")
    bot_thread = Thread(target=fishing_loop, daemon=True)
    bot_thread.start()

def update_button_ui():
    btn.config(text="Start Bot", bg="green")


root = tk.Tk()
root.title("Unified Fishing Bot")
root.geometry("330x650")
root.attributes("-topmost", True)

var_mode = tk.StringVar(value="Default")
var_worm_submode = tk.StringVar(value="Funnel")
var_totem = tk.BooleanVar(value=False)
var_apnea = tk.StringVar(value="90")
var_detect_distance = tk.StringVar(value="7")
var_bite_timeout = tk.StringVar(value=str(BITE_TIMEOUT_SECONDS_DEFAULT))
var_max_timeouts = tk.StringVar(value=str(MAX_CONSECUTIVE_TIMEOUTS_DEFAULT))
var_auto_lobby_hours = tk.StringVar(value=str(AUTO_LOBBY_HOURS_DEFAULT))
var_slot_rod = tk.StringVar(value="1")
var_slot_ranged = tk.StringVar(value="2")
var_slot_totem = tk.StringVar(value="3")
var_slot_melee = tk.StringVar(value="4")

tk.Label(root, text="Fishing Mode", font=("Arial", 10, "bold")).pack(pady=(10, 2))
frame_mode = tk.LabelFrame(root, text="Mode (select one)")
frame_mode.pack(fill="x", padx=10, pady=5)
MODE_DISPLAY_NAMES = {
    "Default": "Default",
    "Treasure": "Treasure",
    "Trophy": "Trophy",
    "Magma": "Magma Core",
    "Worm": "Worm",
    "Strider": "Strider",
}
# Worm's Funnel/Chimney choice nests under the "Worm" radiobutton. Funnel =
# original silverfish-clearing behavior; Chimney = plain Default-like fishing.
for mode_name in ("Default", "Treasure", "Trophy", "Magma", "Worm", "Strider"):
    tk.Radiobutton(
        frame_mode, text=MODE_DISPLAY_NAMES[mode_name], variable=var_mode, value=mode_name, command=on_mode_change
    ).pack(anchor="w")

    if mode_name == "Worm":
        frame_worm_submode = tk.Frame(frame_mode)
        frame_worm_submode.pack(anchor="w", padx=(22, 0))
        tk.Radiobutton(
            frame_worm_submode, text="Funnel",
            variable=var_worm_submode, value="Funnel", command=on_mode_change,
        ).pack(side="left")
        tk.Radiobutton(
            frame_worm_submode, text="Chimney",
            variable=var_worm_submode, value="Chimney", command=on_mode_change,
        ).pack(side="left")

frame_totem = tk.LabelFrame(root, text="Deployable Placement")
frame_totem.pack(fill="x", padx=10, pady=5)
chk_totem = tk.Checkbutton(frame_totem, text="Enable deployable placement", variable=var_totem)
chk_totem.pack(anchor="w")

frame_const = tk.LabelFrame(root, text="Configurable Constants")
frame_const.pack(fill="x", padx=10, pady=5)


def labeled_entry(parent, label, var):
    row = tk.Frame(parent)
    row.pack(fill="x", pady=2)
    tk.Entry(row, textvariable=var, width=5).pack(side="left")
    tk.Label(row, text=label, anchor="w", justify="left", wraplength=200).pack(side="left", padx=(6, 0))


labeled_entry(frame_const, "Apnea time (s)", var_apnea)
labeled_entry(frame_const, "Entity/Bobber Detect distance (m)", var_detect_distance)
labeled_entry(frame_const, "Bite timeout (s)", var_bite_timeout)
labeled_entry(frame_const, "Number of timeouts before stopping", var_max_timeouts)
labeled_entry(frame_const, "Return to lobby after (hours, 0=off)", var_auto_lobby_hours)
labeled_entry(frame_const, "Rod slot", var_slot_rod)
labeled_entry(frame_const, "Ranged slot", var_slot_ranged)
labeled_entry(frame_const, "Deployable slot", var_slot_totem)
labeled_entry(frame_const, "Melee slot", var_slot_melee)

tk.Label(
    root,
    text="(Deployable reach range and NPC exclusion are both fixed at 5 blocks)",
    font=("Arial", 8, "italic"),
    wraplength=290,
).pack(pady=(2, 0))

lbl_error = tk.Label(root, text="", fg="red", wraplength=290)
lbl_error.pack(pady=(4, 0))

btn = tk.Button(
    root, text="Start Bot", command=toggle_bot, width=15, height=2,
    bg="green", fg="white", font=("Arial", 10, "bold"),
)
btn.pack(pady=12)

Thread(target=chat_listener_worker, daemon=True).start()
Thread(target=mouse_listener_worker, daemon=True).start()
Thread(target=golden_fish_tracker_worker, daemon=True).start()
Thread(target=golden_fish_scanner_worker, daemon=True).start()

root.mainloop()
