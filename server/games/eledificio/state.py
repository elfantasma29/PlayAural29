"""Stable state identifiers shared across El Edificio's modules.

Follows the same convention as ``server/games/breachpoint/state.py``:
plain string constants plus frozensets for validation, no logic here.
"""

PHASE_LOADOUT = "loadout"
PHASE_EXPLORE = "explore"
PHASE_BUY = "buy"
PHASE_COMBAT = "combat"
PHASE_ENDED = "ended"
PHASES = frozenset(
    {PHASE_LOADOUT, PHASE_EXPLORE, PHASE_BUY, PHASE_COMBAT, PHASE_ENDED}
)

#: How many shadows a player may buy in the pre-match loadout shop.
MAX_SHADOW_CHARGES = 2

TRAP_STUN = "stun"
TRAP_POISON = "poison"
TRAP_REDIRECT = "redirect"
TRAP_DUEL = "duel"
TRAP_KINDS = frozenset({TRAP_STUN, TRAP_POISON, TRAP_REDIRECT, TRAP_DUEL})
#: Poison can only ever be placed once per player per match (design doc,
#: "Trampas de cuarto" — it deals fixed damage no armor mitigates).
SINGLE_USE_TRAP_KINDS = frozenset({TRAP_POISON})
#: A player may have at most this many traps placed at once, across all kinds.
MAX_TRAPS_PER_PLAYER = 4

HEALTH_STATE_FINE = "fine"
HEALTH_STATE_HURT = "hurt"
HEALTH_STATE_SEVERE = "severe"
HEALTH_STATE_DAZED = "dazed"
HEALTH_STATE_UNCONSCIOUS = "unconscious"
HEALTH_STATES = frozenset(
    {
        HEALTH_STATE_FINE,
        HEALTH_STATE_HURT,
        HEALTH_STATE_SEVERE,
        HEALTH_STATE_DAZED,
        HEALTH_STATE_UNCONSCIOUS,
    }
)

FREE_ACTION_RELOAD = "reload"
FREE_ACTION_POTION = "potion"
FREE_ACTION_SHIELD = "shield"
FREE_ACTIONS = frozenset({FREE_ACTION_RELOAD, FREE_ACTION_POTION, FREE_ACTION_SHIELD})
#: A turn may include at most this many free actions before a definitive
#: action (attack, throw an explosive, play a status card) must close it.
MAX_FREE_ACTIONS_PER_TURN = 2

#: How many rooms away (by graph hops) a player may move-and-inspect in one
#: turn, normally and while Sobrecarga de Movimiento is active.
INSPECTIONS_PER_TURN = 2
INSPECTIONS_PER_TURN_WITH_BOOST = 4
MOVEMENT_BOOST_DURATION_TURNS = 3

#: A trace left behind stops being announced after this many turns.
TRACE_RECENT_TURNS_AGO = 1
TRACE_COLD_TURNS_AGO = 2

#: Damage mitigation is applied in this order when several defenses are
#: active at once: the shield absorbs first, then the defense amulet may
#: trigger, and the armor's percentage reduction applies last.
MITIGATION_STEP_SHIELD = "shield"
MITIGATION_STEP_AMULET = "amulet"
MITIGATION_STEP_ARMOR = "armor"
MITIGATION_ORDER = (
    MITIGATION_STEP_SHIELD,
    MITIGATION_STEP_AMULET,
    MITIGATION_STEP_ARMOR,
)

WIN_KNOCKOUT = "knockout"
WIN_REASONS = frozenset({WIN_KNOCKOUT})
