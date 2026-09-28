"""Room traps for El Edificio: the four kinds, their prices and effects,
per-player placement limits, and the Duelo trap's rock-paper-scissors.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from . import state

POISON_DAMAGE = 10
STUN_INSPECTIONS_LOST = 1
REDIRECT_ROOMS_BACK = 3


@dataclass(frozen=True)
class TrapDefinition:
    id: str
    name: str
    price: int
    description: str


TRAP_DEF_STUN = TrapDefinition(
    id=state.TRAP_STUN,
    name="Aturdidora",
    price=90,
    description="La víctima pierde una de sus inspecciones ese turno (de 2 pasa a 1)",
)
TRAP_DEF_POISON = TrapDefinition(
    id=state.TRAP_POISON,
    name="Bomba de veneno",
    price=130,
    description="10 de daño fijo, instantáneo, sin efectos encadenados",
)
TRAP_DEF_REDIRECT = TrapDefinition(
    id=state.TRAP_REDIRECT,
    name="Desvío",
    price=100,
    description="Manda a la víctima 3 cuartos atrás por el grafo",
)
TRAP_DEF_DUEL = TrapDefinition(
    id=state.TRAP_DUEL,
    name="Duelo",
    price=80,
    description=(
        "Piedra-papel-o-tijera; quien pierde suelta un objeto, el otro lo recoge"
    ),
)

TRAPS = (TRAP_DEF_STUN, TRAP_DEF_POISON, TRAP_DEF_REDIRECT, TRAP_DEF_DUEL)
TRAPS_BY_ID: dict[str, TrapDefinition] = {trap.id: trap for trap in TRAPS}


@dataclass
class PlacedTrap:
    trap_id: str
    room_id: str
    owner_player_id: str


@dataclass
class TrapInventory:
    """Tracks one player's placed traps and enforces the per-match limits."""

    placed: list[PlacedTrap] = field(default_factory=list)
    poison_used: bool = False

    def can_place(self, trap_id: str) -> bool:
        if trap_id not in state.TRAP_KINDS:
            raise ValueError(f"Unknown trap kind: {trap_id}")
        if len(self.placed) >= state.MAX_TRAPS_PER_PLAYER:
            return False
        if trap_id in state.SINGLE_USE_TRAP_KINDS and self.poison_used:
            return False
        return True

    def place(self, trap_id: str, room_id: str, owner_player_id: str) -> PlacedTrap:
        if not self.can_place(trap_id):
            raise ValueError(f"Cannot place trap '{trap_id}' — a limit was reached")
        trap = PlacedTrap(
            trap_id=trap_id, room_id=room_id, owner_player_id=owner_player_id
        )
        self.placed.append(trap)
        if trap_id in state.SINGLE_USE_TRAP_KINDS:
            self.poison_used = True
        return trap

    def trap_in_room(self, room_id: str) -> PlacedTrap | None:
        for trap in self.placed:
            if trap.room_id == room_id:
                return trap
        return None

    def remove(self, trap: PlacedTrap) -> None:
        self.placed.remove(trap)


# ---------------------------------------------------------------------------
# Duelo trap: rock-paper-scissors
# ---------------------------------------------------------------------------

ROCK = 0
PAPER = 1
SCISSORS = 2
DUEL_CHOICES = (ROCK, PAPER, SCISSORS)
_BEATS = {ROCK: SCISSORS, PAPER: ROCK, SCISSORS: PAPER}


def duel_winner(choice_a: int, choice_b: int) -> int:
    """Return 0 if ``choice_a`` wins, 1 if ``choice_b`` wins, -1 on a tie."""
    if choice_a not in DUEL_CHOICES or choice_b not in DUEL_CHOICES:
        raise ValueError("choices must be 0 (piedra), 1 (papel) or 2 (tijera)")
    if choice_a == choice_b:
        return -1
    return 0 if _BEATS[choice_a] == choice_b else 1


def random_duel_choice(rng: random.Random) -> int:
    """A uniformly random rock/paper/scissors pick, for bot opponents."""
    return rng.choice(DUEL_CHOICES)


def validate() -> None:
    """Raise ValueError on any internal inconsistency in this module's data."""
    ids = {trap.id for trap in TRAPS}
    if ids != state.TRAP_KINDS:
        raise ValueError(
            f"TRAPS doesn't match state.TRAP_KINDS: {ids} vs {state.TRAP_KINDS}"
        )
    if len(TRAPS) != len(TRAPS_BY_ID):
        raise ValueError("Duplicate trap id in TRAPS")
    for trap in TRAPS:
        if trap.price < 0:
            raise ValueError(f"Trap '{trap.id}' has a negative price")


validate()
