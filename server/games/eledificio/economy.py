"""Economy for El Edificio: starting coins, room coin drops, the one chest
per match, combat rewards, and the wallet players spend from.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from . import loadout

STARTING_COINS = 2000

#: Price to buy one shadow charge in the pre-match loadout shop. Not part
#: of the original design doc — proposed here at the same tier as Poción
#: de Vida Menor, since shadows never had a price before.
SHADOW_PRICE = 150

#: Loose coin piles found exploring pick one of these amounts at random.
ROOM_COIN_AMOUNTS = (100, 200, 500, 1000)

#: Exactly one chest spawns per match, always for this fixed amount.
CHEST_AMOUNT = 2500

REWARD_TRAP_DUEL_WIN = 180  # exclusive to the Duelo trap — see traps.py
REWARD_SHADOW_ELIMINATED = 100
REWARD_SURVIVAL_BONUS = 40
SURVIVAL_BONUS_INTERVAL_TURNS = 3
REWARD_TRAP_TRIGGERED = 120  # every other trap kind

#: No purchasable or found item may cost more than this — rare finds
#: (Almacenador de Vida Raro) are the only things that can reach it.
MAX_ITEM_VALUE = 5500


@dataclass
class RoomCoinDrop:
    room_id: str
    amount: int


def place_coin_drops(
    room_ids: list[str],
    excluded_room_ids: set[str],
    rng: random.Random,
    drop_count: int | None = None,
) -> list[RoomCoinDrop]:
    """Scatter loose coin piles across a subset of rooms.

    ``excluded_room_ids`` should include the two spawn rooms, so nobody
    starts standing on free money. ``drop_count`` defaults to roughly a
    third of the eligible rooms — a starting point for how much loose money
    is on the map, meant to be tuned with playtesting rather than treated
    as final.
    """
    eligible = [room_id for room_id in room_ids if room_id not in excluded_room_ids]
    if drop_count is None:
        drop_count = max(1, len(eligible) // 3)
    drop_count = min(drop_count, len(eligible))
    chosen_rooms = rng.sample(eligible, drop_count)
    return [
        RoomCoinDrop(room_id=room_id, amount=rng.choice(ROOM_COIN_AMOUNTS))
        for room_id in chosen_rooms
    ]


def place_chest(
    room_ids: list[str], excluded_room_ids: set[str], rng: random.Random
) -> str:
    """Return the room id where the single coin chest spawns."""
    eligible = [room_id for room_id in room_ids if room_id not in excluded_room_ids]
    if not eligible:
        raise ValueError("No eligible rooms to place the chest")
    return rng.choice(eligible)


@dataclass
class Wallet:
    balance: int = STARTING_COINS

    def add(self, amount: int) -> None:
        if amount < 0:
            raise ValueError("negative amount — use spend() instead")
        self.balance += amount

    def can_afford(self, amount: int) -> bool:
        return self.balance >= amount

    def spend(self, amount: int) -> None:
        if amount < 0:
            raise ValueError("amount must be non-negative")
        if not self.can_afford(amount):
            raise ValueError("insufficient balance")
        self.balance -= amount


def validate() -> None:
    """Raise ValueError if any priced item in loadout.py exceeds the value
    ceiling, or if the reward/drop constants are inconsistent."""
    priced_collections = (
        loadout.WEAPONS,
        loadout.EXPLOSIVES,
        loadout.LIFE_ITEMS,
        loadout.AMULETS,
        loadout.POTIONS,
        loadout.SHIELDS,
        loadout.ARMORS,
    )
    for items in priced_collections:
        for item in items:
            if item.price > MAX_ITEM_VALUE:
                raise ValueError(
                    f"'{item.id}' costs {item.price}, over the "
                    f"MAX_ITEM_VALUE ceiling of {MAX_ITEM_VALUE}"
                )
    if CHEST_AMOUNT > MAX_ITEM_VALUE:
        raise ValueError("CHEST_AMOUNT exceeds MAX_ITEM_VALUE")
    if SHADOW_PRICE <= 0 or SHADOW_PRICE > MAX_ITEM_VALUE:
        raise ValueError("SHADOW_PRICE must be positive and under MAX_ITEM_VALUE")
    if any(amount <= 0 for amount in ROOM_COIN_AMOUNTS):
        raise ValueError("ROOM_COIN_AMOUNTS must all be positive")
    for reward in (
        REWARD_TRAP_DUEL_WIN,
        REWARD_SHADOW_ELIMINATED,
        REWARD_SURVIVAL_BONUS,
        REWARD_TRAP_TRIGGERED,
    ):
        if reward <= 0:
            raise ValueError("combat rewards must be positive")


validate()
