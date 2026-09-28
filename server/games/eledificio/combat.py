"""Combat resolution for El Edificio.

Covers: health-state narration, per-bullet weapon bursts, the
shield -> amulet -> armor damage mitigation pipeline (including armor's
turn-by-turn wear), free-action turn limits, and status-card cooldown
tracking. Pure game logic — no dependency yet on the PlayAural framework's
Game base class or its mixins.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from . import cards as cards_module
from . import loadout
from . import state


# ---------------------------------------------------------------------------
# Health-state narration
# ---------------------------------------------------------------------------


def health_state_for(current_health: int, max_health: int) -> str:
    """Return the health-state id for a health value, per the design doc's
    percentage thresholds of max_health."""
    if max_health <= 0:
        raise ValueError("max_health must be positive")
    if current_health <= 0:
        return state.HEALTH_STATE_UNCONSCIOUS
    fraction = current_health / max_health
    if fraction <= 0.20:
        return state.HEALTH_STATE_DAZED
    if fraction <= 0.40:
        return state.HEALTH_STATE_SEVERE
    if fraction <= 0.60:
        return state.HEALTH_STATE_HURT
    return state.HEALTH_STATE_FINE


_HEALTH_NARRATION: dict[str, str | None] = {
    state.HEALTH_STATE_FINE: None,
    state.HEALTH_STATE_HURT: "El dolor se apodera de {name}. Está herido.",
    state.HEALTH_STATE_SEVERE: "Está gravemente herido.",
    state.HEALTH_STATE_DAZED: "Está aturdido.",
    state.HEALTH_STATE_UNCONSCIOUS: "Está inconsciente.",
}


def narrate_health_state(health_state: str, name: str) -> str | None:
    """Return the voice line for a health state, or None for the "fine"
    state (61%-100% health), which gets no announcement."""
    if health_state not in state.HEALTH_STATES:
        raise ValueError(f"Unknown health state: {health_state}")
    template = _HEALTH_NARRATION[health_state]
    return None if template is None else template.format(name=name)


# ---------------------------------------------------------------------------
# Weapons: burst resolution and per-player reload lockout tracking
# ---------------------------------------------------------------------------


@dataclass
class BurstResult:
    total_damage: int
    crit_bullets: int
    normal_bullets: int


def resolve_weapon_burst(
    weapon: loadout.Weapon, rng: random.Random, *, crit_chance_bonus: float = 0.0
) -> BurstResult:
    """Roll each bullet's crit chance independently and sum the burst.

    ``crit_chance_bonus`` adds to every bullet's own chance (e.g. Fortuna's
    +5%); it defaults to 0.0, so existing callers are unaffected.
    """
    crit_bullets = 0
    total = 0
    effective_chance = min(1.0, weapon.crit_chance_per_bullet + crit_chance_bonus)
    for _ in range(weapon.bullet_count):
        if rng.random() < effective_chance:
            total += weapon.crit_damage_per_bullet
            crit_bullets += 1
        else:
            total += weapon.damage_per_bullet
    return BurstResult(
        total_damage=total,
        crit_bullets=crit_bullets,
        normal_bullets=weapon.bullet_count - crit_bullets,
    )


@dataclass
class WeaponState:
    """Per-player, per-weapon reload lockout tracking."""

    weapon_id: str
    lockout_turns_remaining: int = 0

    def is_usable(self) -> bool:
        return self.lockout_turns_remaining <= 0

    def fire(self) -> None:
        """Mark the weapon as just having emptied its magazine."""
        weapon = loadout.WEAPONS_BY_ID[self.weapon_id]
        self.lockout_turns_remaining = weapon.reload_lockout_turns

    def tick(self) -> None:
        """Advance one turn; call once per turn boundary for every weapon a
        player owns."""
        if self.lockout_turns_remaining > 0:
            self.lockout_turns_remaining -= 1


# ---------------------------------------------------------------------------
# Armor: mutable per-match wear tracking (loadout.Armor stays static rules)
# ---------------------------------------------------------------------------


@dataclass
class EquippedArmor:
    armor_id: str
    turns_equipped: int = 0
    accelerated_wear_events: int = 0  # crits/parasites this armor has blocked

    def current_absorption(self) -> float:
        armor = loadout.ARMORS_BY_ID[self.armor_id]
        value = armor.max_absorption

        if armor.accelerated_wear_step > 0:
            # Égida: only drops when it actually blocks a crit or parasite,
            # then keeps decaying slowly, floored until natural wear starts.
            if self.accelerated_wear_events > 0:
                value -= armor.accelerated_wear_step * self.accelerated_wear_events
                extra_two_turn_steps = self.turns_equipped // 2
                value -= (
                    armor.accelerated_wear_decay_per_two_turns * extra_two_turn_steps
                )
                value = max(value, armor.accelerated_wear_floor)

        if self.turns_equipped >= armor.natural_wear_start_turn:
            turns_past = self.turns_equipped - armor.natural_wear_start_turn + 1
            value -= armor.natural_wear_per_turn * turns_past

        return max(value, 0.0)

    def register_blocked_attack(self) -> None:
        """Call when this armor blocks a crit or a parasite (Égida only)."""
        self.accelerated_wear_events += 1

    def tick(self) -> None:
        self.turns_equipped += 1


def armor_blocks_parasite(armor_id: str, rng: random.Random) -> bool:
    """Roll whether the equipped armor blocks an incoming status card.

    Only Égida has a non-zero ``parasite_block_chance``; every other armor
    always returns False.
    """
    armor = loadout.ARMORS_BY_ID[armor_id]
    return rng.random() < armor.parasite_block_chance


# ---------------------------------------------------------------------------
# Damage mitigation: shield -> amulet -> armor (state.MITIGATION_ORDER)
# ---------------------------------------------------------------------------


@dataclass
class ActiveShield:
    shield_id: str
    hits_remaining: int


@dataclass
class Defender:
    """Snapshot of a player's active defenses, passed into apply_mitigation."""

    shield: ActiveShield | None = None
    equipped_amulet_ids: tuple[str, ...] = ()
    equipped_armor: EquippedArmor | None = None


@dataclass
class MitigationResult:
    final_damage: int
    crit_reduced_by_armor: bool = False
    shield_absorbed: int = 0
    shield_spent: bool = False
    amulet_triggered: str | None = None
    armor_absorbed: int = 0


def apply_mitigation(
    raw_damage: int,
    *,
    is_crit: bool,
    defender: Defender,
    rng: random.Random,
    amulet_chance_bonus: float = 0.0,
) -> MitigationResult:
    """Reduce an incoming hit through the defender's active defenses.

    Order: armor's crit-damage reduction is a property of the hit itself and
    is applied first; then the pipeline proper runs shield, then defense
    amulet, then armor's flat percentage — matching the design doc's
    "escudo, amuleto, armadura" order. ``amulet_chance_bonus`` adds to the
    defense amulet's own activation chance (e.g. Fortuna's +5%); it defaults
    to 0.0, so existing callers are unaffected.
    """
    remaining = raw_damage
    result = MitigationResult(final_damage=raw_damage)

    if is_crit and defender.equipped_armor is not None:
        armor = loadout.ARMORS_BY_ID[defender.equipped_armor.armor_id]
        if armor.crit_damage_reduction > 0:
            remaining = round(remaining * (1 - armor.crit_damage_reduction))
            result.crit_reduced_by_armor = True

    if defender.shield is not None and defender.shield.hits_remaining > 0:
        shield = loadout.SHIELDS_BY_ID[defender.shield.shield_id]
        absorbed = round(remaining * shield.absorb_fraction)
        remaining -= absorbed
        result.shield_absorbed = absorbed
        defender.shield.hits_remaining -= 1
        if defender.shield.hits_remaining <= 0:
            result.shield_spent = True

    for amulet_id in defender.equipped_amulet_ids:
        amulet = loadout.AMULETS_BY_ID[amulet_id]
        if amulet.kind != loadout.AMULET_KIND_DEFENSE:
            continue
        effective_chance = min(1.0, amulet.activation_chance + amulet_chance_bonus)
        if rng.random() < effective_chance:
            remaining = min(remaining, 1)
            result.amulet_triggered = amulet_id
        break  # only one defense amulet applies per hit

    if defender.equipped_armor is not None:
        absorption = defender.equipped_armor.current_absorption()
        absorbed = round(remaining * absorption)
        remaining -= absorbed
        result.armor_absorbed = absorbed

    result.final_damage = max(remaining, 0)
    return result


# ---------------------------------------------------------------------------
# Free actions: max 2 per turn (recargar / poción / escudo), then a
# definitive action is required to close the turn.
# ---------------------------------------------------------------------------


@dataclass
class TurnAllowance:
    free_actions_used: int = 0

    def can_use_free_action(self) -> bool:
        return self.free_actions_used < state.MAX_FREE_ACTIONS_PER_TURN

    def use_free_action(self, kind: str) -> None:
        if kind not in state.FREE_ACTIONS:
            raise ValueError(f"Unknown free action kind: {kind}")
        if not self.can_use_free_action():
            raise ValueError("No free actions left this turn")
        self.free_actions_used += 1

    def reset(self) -> None:
        self.free_actions_used = 0





# ---------------------------------------------------------------------------
# Status-card cooldowns
# ---------------------------------------------------------------------------


def start_card_lock(card_id: str, lockouts: dict[str, int]) -> None:
    card = cards_module.CARDS_BY_ID[card_id]
    lockouts[card_id] = cards_module.lock_turns_after_use(card)


def tick_card_locks(lockouts: dict[str, int]) -> None:
    """Advance every active lockout by one turn, dropping expired ones."""
    for card_id in list(lockouts):
        lockouts[card_id] -= 1
        if lockouts[card_id] <= 0:
            del lockouts[card_id]


def is_card_locked(card_id: str, lockouts: dict[str, int]) -> bool:
    return lockouts.get(card_id, 0) > 0


# ---------------------------------------------------------------------------
# Active card/potion effects — runtime state for whichever status cards and
# long-duration potions currently affect a player, regardless of who cast
# them. Stored on whoever's stats/actions they modify, so "what's affecting
# me right now" is always a simple scan of one list.
# ---------------------------------------------------------------------------


@dataclass
class ActiveCardEffect:
    card_id: str
    turns_remaining: int
    #: Running accumulated magnitude for effects that build up over time
    #: (Hambriento de Sangre, Desgaste Vampírico, Furia Final's decay).
    stacks: int = 0
