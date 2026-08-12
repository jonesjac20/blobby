"""Will hold per-tick step() and try_split().

Filled in during Phase 1. step(world, dt) performs: apply input -> move ->
collide -> decay split velocity -> remerge -> respawn food, in that order.
"""
