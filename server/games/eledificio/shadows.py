"""Shadow decoys for El Edificio.

A shadow is planted in a room (consuming one of the owner's inspections —
that cost is enforced by the room-exploration code, not here). This module
models what happens once one exists: being found alone, being found
alongside its owner, and its three bonus uses (camuflaje de rastro, trampa
viva, bono por sigilo).
"""

from __future__ import annotations

from dataclasses import dataclass

#: The automatic counterattack a shadow lands when found alone — matches
#: the Cuchillo Fantasma's base hit, since it's meant to sting, not decide
#: the match.
SHADOW_WEAK_HIT_DAMAGE = 4

#: Awarded if a shadow reaches the owner's final combat alive and never found.
STEALTH_BONUS_COINS = 250

SHADOW_OUTCOME_COUNTERATTACK = "shadow_destroyed_counterattack"
SHADOW_OUTCOME_OWNER_HIT = "owner_hit"


@dataclass
class Shadow:
    owner_player_id: str
    room_id: str
    carried_trap_id: str | None = None  # "trampa viva" — a trap id from traps.py
    alive: bool = True
    ever_found: bool = False

    def destroy(self) -> None:
        self.alive = False


def downgrades_trace(shadow: Shadow | None) -> bool:
    """Whether a live shadow currently downgrades its owner's trace by one
    level ("presencia reciente" reads as "rastro frío" to the rival)."""
    return shadow is not None and shadow.alive


def resolve_found_alone(shadow: Shadow) -> int:
    """The rival found the shadow while the owner was elsewhere.

    The shadow returns one weak automatic hit, then is destroyed. The
    owner's real location is never revealed. Returns the damage dealt to
    the rival.
    """
    shadow.ever_found = True
    shadow.destroy()
    return SHADOW_WEAK_HIT_DAMAGE


def resolve_blind_pick(shadow: Shadow, rival_picked_shadow: bool) -> str:
    """Owner and shadow share a room; the rival picks blind between them.

    Returns SHADOW_OUTCOME_COUNTERATTACK if the rival guessed wrong (the
    shadow is destroyed and the owner gets the counterattack turn), or
    SHADOW_OUTCOME_OWNER_HIT if the rival guessed right (normal combat
    starts; the shadow disappears either way).
    """
    shadow.ever_found = True
    shadow.destroy()
    return SHADOW_OUTCOME_COUNTERATTACK if rival_picked_shadow else SHADOW_OUTCOME_OWNER_HIT


def stealth_bonus(shadow: Shadow | None) -> int:
    """Coins earned for this shadow reaching the owner's final combat intact."""
    if shadow is None:
        return 0
    return STEALTH_BONUS_COINS if shadow.alive and not shadow.ever_found else 0
