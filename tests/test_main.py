"""Covers the Phase 1 console harness in server/main.py.

The summary line format is quoted verbatim in the guidebook, so it is pinned
here. Phase 2 replaces this module's tick loop, but `centroid` and the
nearest-food steering graduate into the state broadcast and the bots rather than
being deleted with it.
"""

import math

import pytest
from conftest import add_piece, add_player

from server.main import _summary_line, centroid, input_toward_nearest_food
from server.models import Food

# --- centroid -------------------------------------------------------------


def test_centroid_of_a_single_piece_is_its_position(world):
    player = add_player(world, "solo", 100.0, 200.0)

    assert centroid(player) == (100.0, 200.0)


def test_centroid_is_mass_weighted(world):
    player = add_player(world, "two", 0.0, 0.0, mass=300)
    add_piece(world, player, 100.0, 0.0, mass=100)

    # Three times the mass on the left, so the centroid sits a quarter of the way over.
    assert centroid(player) == (25.0, 0.0)


def test_centroid_of_a_player_with_no_pieces_is_the_origin(world):
    """An eaten player reads as (0, 0), which is why the summary shows `pieces=[]`."""
    player = add_player(world, "gone", 500.0, 500.0)
    player.pieces.clear()

    assert centroid(player) == (0.0, 0.0)


# --- nearest-food steering ------------------------------------------------


def test_input_toward_nearest_food_is_a_unit_vector_at_the_closest_pellet(world):
    player = add_player(world, "seeker", 500.0, 500.0)
    world.food["near"] = Food(id="near", x=530.0, y=540.0)
    world.food["far"] = Food(id="far", x=1000.0, y=500.0)

    dx, dy = input_toward_nearest_food(world, player)

    assert math.hypot(dx, dy) == pytest.approx(1.0)
    assert (dx, dy) == pytest.approx((0.6, 0.8))


def test_input_toward_nearest_food_is_zero_when_there_is_no_food(world):
    player = add_player(world, "seeker", 500.0, 500.0)

    assert input_toward_nearest_food(world, player) == (0.0, 0.0)


def test_input_toward_nearest_food_is_zero_when_standing_on_it(world):
    player = add_player(world, "seeker", 500.0, 500.0)
    world.food["under"] = Food(id="under", x=500.0, y=500.0)

    assert input_toward_nearest_food(world, player) == (0.0, 0.0)


def test_input_toward_nearest_food_is_zero_for_a_player_with_no_pieces(world):
    player = add_player(world, "gone", 500.0, 500.0)
    player.pieces.clear()
    world.food["near"] = Food(id="near", x=530.0, y=540.0)

    assert input_toward_nearest_food(world, player) == (0.0, 0.0)


# --- summary line ---------------------------------------------------------


def test_summary_line_matches_the_documented_format(world):
    a = add_player(world, "A", 300.0, 600.0, mass=200)
    b = add_player(world, "B", 900.0, 600.0, mass=200)
    world.food["pellet"] = Food(id="pellet", x=1.0, y=1.0)

    assert _summary_line(world, 30, a, b) == (
        "tick 30 | A pieces=[200] pos=(300,600) | B pieces=[200] pos=(900,600) | food=1"
    )


def test_summary_line_spells_out_the_halves_of_a_split_player(world):
    """A centroid alone cannot show a split, so multi-piece players also get `at=`."""
    a = add_player(world, "A", 300.0, 600.0, mass=100)
    add_piece(world, a, 340.0, 600.0, mass=100)
    b = add_player(world, "B", 900.0, 600.0, mass=200)

    assert _summary_line(world, 90, a, b) == (
        "tick 90 | A pieces=[100,100] pos=(320,600) at=(300,600) (340,600) "
        "| B pieces=[200] pos=(900,600) | food=0"
    )
