"""The shared, free status-card deck for El Edificio.

Both players have access to every card in ``CARDS`` — nothing here is
bought (see ``loadout.py`` for purchasable items). Playing a card is a
turn-ending action; using it locks that specific card for a number of
turns, per ``lock_turns_after_use()`` below.
"""

from __future__ import annotations

from dataclasses import dataclass

EFFECT_INSTANT = "instant"  # resolves immediately, no lingering duration
EFFECT_DURATION = "duration"  # lingers for its own duration_turns
EFFECT_KINDS = frozenset({EFFECT_INSTANT, EFFECT_DURATION})


@dataclass(frozen=True)
class Card:
    id: str
    name: str
    description: str
    effect_kind: str  # EFFECT_INSTANT or EFFECT_DURATION
    duration_turns: int  # 0 for instant cards
    potent: bool  # potent cards get +1 extra turn of lock, on top of the above


CARD_DEVOURING_PARASITE = Card(
    id="devouring_parasite",
    name="Parásito Devorador",
    description="Implanta un estado: los ataques del rival hacen -2 de daño durante 3 turnos",
    effect_kind=EFFECT_DURATION,
    duration_turns=3,
    potent=False,
)
CARD_STUNNING_PARASITE = Card(
    id="stunning_parasite",
    name="Parásito Aturdidor",
    description="El próximo ataque del rival hace -5 de daño, un solo uso",
    effect_kind=EFFECT_INSTANT,
    duration_turns=0,
    potent=False,
)
CARD_VAMPIRE = Card(
    id="vampire",
    name="Vampiro",
    description="El próximo ataque del rival hace -4 a -5 de daño, un solo uso",
    effect_kind=EFFECT_INSTANT,
    duration_turns=0,
    potent=False,
)
CARD_SUPERFURY = Card(
    id="superfury",
    name="Superfuria",
    description="+3 de daño a todo lo que lances durante 4 turnos",
    effect_kind=EFFECT_DURATION,
    duration_turns=4,
    potent=False,
)
CARD_BLOODTHIRSTY = Card(
    id="bloodthirsty",
    name="Hambriento de Sangre",
    description=(
        "Durante 3 turnos ganas al azar entre +1 y +3 de ataque cada turno "
        "(acumulable), hasta +9 de daño total"
    ),
    effect_kind=EFFECT_DURATION,
    duration_turns=3,
    potent=False,
)
CARD_VAMPIRIC_WEAR = Card(
    id="vampiric_wear",
    name="Desgaste Vampírico",
    description=(
        "A lo largo de 6 turnos, le va quitando -1 de ataque al rival por "
        "turno, hasta -6 acumulado en el turno 6"
    ),
    effect_kind=EFFECT_DURATION,
    duration_turns=6,
    potent=True,
)
CARD_HELP = Card(
    id="help",
    name="Ayuda (+4)",
    description="Al consumirla, tu ataque final de este turno hace +4 de daño",
    effect_kind=EFFECT_INSTANT,
    duration_turns=0,
    potent=False,
)
CARD_CHAOS = Card(
    id="chaos",
    name="Caos",
    description=(
        "Al azar: +10 de ataque al rival en su próximo turno, o -10 de "
        "ataque al rival en su próximo turno, o -3 de daño a ambos "
        "jugadores por igual"
    ),
    effect_kind=EFFECT_INSTANT,
    duration_turns=0,
    potent=False,
)
CARD_PAIN_BOND = Card(
    id="pain_bond",
    name="Vínculo de Dolor",
    description=(
        "Durante 2 turnos, todo el daño que el rival te haga también le "
        "hace la mitad a él"
    ),
    effect_kind=EFFECT_DURATION,
    duration_turns=2,
    potent=False,
)
CARD_LIFE_STEAL = Card(
    id="life_steal",
    name="Robo Vital",
    description="Tu próximo ataque te cura el 50% del daño que hagas con él",
    effect_kind=EFFECT_INSTANT,
    duration_turns=0,
    potent=False,
)
CARD_FREEZE = Card(
    id="freeze",
    name="Congelación",
    description=(
        "El rival no puede usar arma de fuego en su próximo turno — solo "
        "cuchillo o pasar turno"
    ),
    effect_kind=EFFECT_INSTANT,
    duration_turns=0,
    potent=False,
)
CARD_TOXIC_ECHO = Card(
    id="toxic_echo",
    name="Eco Tóxico",
    description=(
        "-2 de ataque al rival durante 3 turnos, más -2 de daño directo en "
        "cada uno de esos 3 turnos"
    ),
    effect_kind=EFFECT_DURATION,
    duration_turns=3,
    potent=True,
)
CARD_SECOND_CHANCE = Card(
    id="second_chance",
    name="Segunda Oportunidad",
    description=(
        "Un solo uso: si tu vida llegaría a 0 mientras la tienes activa, te "
        "quedas con 1 de vida y se consume"
    ),
    effect_kind=EFFECT_INSTANT,
    duration_turns=0,
    potent=True,
)
CARD_DISARM = Card(
    id="disarm",
    name="Desarme",
    description=(
        "Bloquea el próximo objeto del rival (poción, escudo o carta), lo "
        "obliga a atacar en seco"
    ),
    effect_kind=EFFECT_INSTANT,
    duration_turns=0,
    potent=False,
)

CARDS = (
    CARD_DEVOURING_PARASITE,
    CARD_STUNNING_PARASITE,
    CARD_VAMPIRE,
    CARD_SUPERFURY,
    CARD_BLOODTHIRSTY,
    CARD_VAMPIRIC_WEAR,
    CARD_HELP,
    CARD_CHAOS,
    CARD_PAIN_BOND,
    CARD_LIFE_STEAL,
    CARD_FREEZE,
    CARD_TOXIC_ECHO,
    CARD_SECOND_CHANCE,
    CARD_DISARM,
)
CARDS_BY_ID: dict[str, Card] = {card.id: card for card in CARDS}


def lock_turns_after_use(card: Card) -> int:
    """Return how many turns a card is locked for after being played.

    - Instant-effect cards: locked 1 turn.
    - Duration cards: locked for their own duration.
    - Potent cards (either kind): +1 extra turn on top of the above.
    """
    base = 1 if card.effect_kind == EFFECT_INSTANT else card.duration_turns
    return base + 1 if card.potent else base


def validate() -> None:
    """Raise ValueError on any internal inconsistency in this module's data."""
    ids = [card.id for card in CARDS]
    if len(ids) != len(set(ids)):
        duplicates = {i for i in ids if ids.count(i) > 1}
        raise ValueError(f"Duplicate card id(s): {sorted(duplicates)}")

    for card in CARDS:
        if card.effect_kind not in EFFECT_KINDS:
            raise ValueError(f"Card '{card.id}' has an unknown effect_kind")
        if card.effect_kind == EFFECT_INSTANT and card.duration_turns != 0:
            raise ValueError(
                f"Card '{card.id}' is instant but has a non-zero duration_turns"
            )
        if card.effect_kind == EFFECT_DURATION and card.duration_turns <= 0:
            raise ValueError(
                f"Card '{card.id}' is duration-based but duration_turns <= 0"
            )


validate()
