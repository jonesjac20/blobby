"""Simulation constants from build plan section 5, plus world/tuning values."""

TICK_RATE = 30
MIN_SPLIT_MASS = 35
MAX_PIECES = 8
EAT_RATIO = 1.25
REMERGE_SECONDS = 12
SPLIT_KICK_DECAY_SECONDS = 0.5

WORLD_WIDTH = 1200
WORLD_HEIGHT = 1200

FOOD_COUNT = 600
FOOD_MASS = 1

# Above MIN_SPLIT_MASS so a freshly spawned player can split without eating first.
INITIAL_PLAYER_MASS = 40

# World units per second for a piece at INITIAL_PLAYER_MASS. Radius is
# sqrt(mass/pi), so a spawn-sized blob is only ~3.6 units across; keep this low
# enough that one tick of travel never exceeds that radius, or fast blobs jump
# straight over the food they are chasing instead of eating it.
BASE_SPEED = 100
# Initial magnitude of the velocity kick given to a freshly split piece. Total
# kick displacement is SPLIT_KICK_SPEED * SPLIT_KICK_DECAY_SECONDS / 2; keeping
# that near the blob radius means halves drift apart but stay in contact, so the
# remerge timer is what decides when they recombine.
SPLIT_KICK_SPEED = 40

# Exponent of the agar.io-style speed falloff. Larger means heavier blobs slow
# down more sharply.
SPEED_FALLOFF = 0.4


def speed_for_mass(mass: float) -> float:
    """Movement speed in world units per second for a piece of `mass`."""
    if mass <= 0:
        return BASE_SPEED
    return BASE_SPEED * (INITIAL_PLAYER_MASS / mass) ** SPEED_FALLOFF
