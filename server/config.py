"""Simulation constants from build plan section 5, plus world/tuning values.

These names are bound by value at import into `server.world` and
`server.simulation`. Patching `server.config.FOOD_COUNT` afterwards has no
effect; patch `server.world.FOOD_COUNT`, or pass `World(food_target=...)`.
"""

import os
import time

TICK_RATE = 30
MIN_SPLIT_MASS = 35
MAX_PIECES = 8
EAT_RATIO = 1.25
REMERGE_SECONDS = 12
SPLIT_KICK_DECAY_SECONDS = 0.5

# --- simulation clock ------------------------------------------------------
#
# The server owns time. Every timer in the simulation - the remerge wait, the
# kick decay - is measured against World.now, which nothing but simulation.step
# advances and only ever by an interval the server itself measured from the
# source below. No message on the wire carries or influences a timestamp, so a
# client cannot run its clock fast to reach a remerge or shed a split kick early,
# and two clients cannot disagree about when anything happened.
#
# Named here rather than called inline so the whole server reads one clock: a
# monotonic one, which cannot jump backwards when the host's wall clock is
# corrected mid-game.
SIMULATION_CLOCK_SOURCE = time.monotonic
# Ceiling on how much sim time a single tick may advance. A hitch - a debugger
# pause, a laptop waking up - would otherwise teleport every blob across the map
# on the tick after it. Sim time falls behind real time instead, which is the
# safe direction.
MAX_TICK_SECONDS = 0.25

# Bind address for the aiohttp process. 0.0.0.0, not 127.0.0.1: binding
# localhost would make Phase 7's external test fail in a way
# indistinguishable from a bad port forward.
HOST = os.environ.get("BLOBBY_HOST", "0.0.0.0")
PORT = int(os.environ.get("BLOBBY_PORT", "8000"))

NAME_MAX_LEN = 16
DEFAULT_COLOR = "#4fc3f7"

WORLD_WIDTH = 1200
WORLD_HEIGHT = 1200

FOOD_COUNT = 600
FOOD_MASS = 1

# Above MIN_SPLIT_MASS so a freshly spawned player can split without eating first.
INITIAL_PLAYER_MASS = 40

# How long a freshly joined player cannot be eaten. Spawn points come from the
# world RNG and are only clamped into the rectangle, never away from other
# bodies, so a join can land inside a blob big enough to eat it on the next
# tick. This is the window to eat some food or run. A feel parameter like the
# cluster values - judge it on a screen in Phase 4, not here.
SPAWN_INVULN_SECONDS = 3.0

# World units per second for a piece at INITIAL_PLAYER_MASS. Lighter pieces
# move faster (`speed_for_mass`), so a split fragment can travel more than
# its own radius in one tick. Food collection is a swept test along that
# path, so pellets on the trajectory are still eaten.
BASE_SPEED = 100
# Initial magnitude of the velocity kick given to a freshly split piece. Total
# kick displacement is SPLIT_KICK_SPEED * SPLIT_KICK_DECAY_SECONDS / 2, so the
# halves of a mass-100 blob pop about 30 units apart - several blob widths, and
# unmistakable. The kick does not have to stay small to keep the halves in
# contact; COHESION_SPEED below is what pulls them back.
SPLIT_KICK_SPEED = 120

# Exponent of the agar.io-style speed falloff. Larger means heavier blobs slow
# down more sharply.
SPEED_FALLOFF = 0.4

# --- soft-body cluster and collision ---------------------------------------
#
# Each of the three overlap constants is a threshold on simulation.engulfment,
# which reads 0.0 when two circles just touch and 1.0 when the smaller sits
# fully inside the larger. Their ordering is the whole design: pieces rest in
# contact, and eating or merging takes real penetration past that resting depth.

# Engulfment a player's own pieces settle at. Deep enough to read as one fused
# body, shallow enough that the merge pull has somewhere to travel.
OWN_PIECE_OVERLAP = 0.15
# The prey's center has to reach the predator's rim before it is eaten, so a
# graze reads as a shove rather than a kill.
EAT_OVERLAP = 0.5
# Just past EAT_OVERLAP, so a pair resting in contact has to actively sink in
# before it merges.
MERGE_OVERLAP = 0.6

# World units per second each of a player's pieces drifts toward its neighbours.
COHESION_SPEED = 12.0
# World units per second once a pair's remerge timer clears. Both pieces move,
# so a resting pair closes the gap in roughly 0.4s: visible, not a snap.
MERGE_PULL_SPEED = 6.0
# Position-projection rounds per tick. Projection is dt-independent, so this is
# the only knob deciding how firmly a crowded cluster is pushed apart.
SEPARATION_PASSES = 4


def speed_for_mass(mass: float) -> float:
    """Movement speed in world units per second for a piece of `mass`."""
    if mass <= 0:
        return BASE_SPEED
    return BASE_SPEED * (INITIAL_PLAYER_MASS / mass) ** SPEED_FALLOFF
