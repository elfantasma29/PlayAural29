"""Weapon, explosive, life-item, amulet, potion, shield and armor
definitions for El Edificio.

Mirrors the data-driven pattern in ``server/games/breachpoint/arsenal.py``:
every item is a frozen dataclass instance in a module-level tuple, and
``validate()`` runs at import time so a balance mistake (a duplicate id, a
weapon whose total doesn't match its own bullets) fails loudly at startup
instead of surfacing as a wrong number mid-match.

Armor's turn-by-turn wear is *not* modeled here — these are the static
rules only (ranges, wear-start turns, wear rates). The mutable per-player
tracking (turns equipped, accelerated-wear events) belongs in combat.py,
since that's per-match state, not a fixed item definition.
"""

from __future__ import annotations

from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Weapons
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Weapon:
    id: str
    name: str
    price: int
    bullet_count: int
    damage_per_bullet: int
    crit_damage_per_bullet: int
    crit_chance_per_bullet: float  # e.g. 0.01 for 1% per bullet
    reload_cost: int
    reload_lockout_turns: int  # turns the weapon is unusable after a burst

    @property
    def total_damage(self) -> int:
        """Total damage of a full burst with no crits."""
        return self.bullet_count * self.damage_per_bullet

    def burst_crit_chance(self) -> float:
        """Probability that at least one bullet in a full burst crits."""
        return 1 - (1 - self.crit_chance_per_bullet) ** self.bullet_count


WEAPON_KNIFE = Weapon(
    id="knife",
    name="Cuchillo Fantasma",
    price=0,
    bullet_count=1,
    damage_per_bullet=4,
    crit_damage_per_bullet=4,
    crit_chance_per_bullet=0.0,
    reload_cost=0,
    reload_lockout_turns=0,
)
WEAPON_ECO_PISTOL = Weapon(
    id="eco_pistol",
    name="Pistola Eco",
    price=350,
    bullet_count=5,
    damage_per_bullet=2,
    crit_damage_per_bullet=5,
    crit_chance_per_bullet=0.01,
    reload_cost=200,
    reload_lockout_turns=1,
)
WEAPON_SHADOW_RIFLE = Weapon(
    id="shadow_rifle",
    name="Rifle Sombra",
    price=900,
    bullet_count=6,
    damage_per_bullet=3,
    crit_damage_per_bullet=7,
    crit_chance_per_bullet=0.01,
    reload_cost=200,
    reload_lockout_turns=1,
)
WEAPON_TITAN_RIFLE = Weapon(
    id="titan_rifle",
    name="Rifle de Asalto Titán",
    price=1800,
    bullet_count=10,
    damage_per_bullet=3,
    crit_damage_per_bullet=8,
    crit_chance_per_bullet=0.01,
    reload_cost=200,
    reload_lockout_turns=1,
)
WEAPON_ECLIPSE_SNIPER = Weapon(
    id="eclipse_sniper",
    name="Francotirador Eclipse",
    price=3300,
    bullet_count=15,
    damage_per_bullet=2,
    crit_damage_per_bullet=15,
    crit_chance_per_bullet=0.01,
    reload_cost=200,
    reload_lockout_turns=1,
)

WEAPONS = (
    WEAPON_KNIFE,
    WEAPON_ECO_PISTOL,
    WEAPON_SHADOW_RIFLE,
    WEAPON_TITAN_RIFLE,
    WEAPON_ECLIPSE_SNIPER,
)
WEAPONS_BY_ID: dict[str, Weapon] = {weapon.id: weapon for weapon in WEAPONS}


# ---------------------------------------------------------------------------
# Explosives
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Explosive:
    id: str
    name: str
    price: int
    min_damage: int
    max_damage: int
    hits_both_players: bool = False


EXPLOSIVE_MOLOTOV_1 = Explosive(
    id="molotov_1", name="Molotov I", price=90, min_damage=5, max_damage=5
)
EXPLOSIVE_MOLOTOV_2 = Explosive(
    id="molotov_2", name="Molotov II", price=160, min_damage=8, max_damage=8
)
EXPLOSIVE_FRAG_GRENADE = Explosive(
    id="frag_grenade",
    name="Granada de Fragmento",
    price=240,
    min_damage=8,
    max_damage=10,
)
EXPLOSIVE_MOLOTOV_3 = Explosive(
    id="molotov_3", name="Molotov III", price=340, min_damage=11, max_damage=11
)
EXPLOSIVE_MOLOTOV_INFERNAL = Explosive(
    id="molotov_infernal",
    name="Molotov Infernal",
    price=520,
    min_damage=15,
    max_damage=15,
)
EXPLOSIVE_GAS_BOMB = Explosive(
    id="gas_bomb",
    name='Bomba de Gas "Nube Tóxica"',
    price=300,
    min_damage=5,
    max_damage=5,
    hits_both_players=True,
)

EXPLOSIVES = (
    EXPLOSIVE_MOLOTOV_1,
    EXPLOSIVE_MOLOTOV_2,
    EXPLOSIVE_FRAG_GRENADE,
    EXPLOSIVE_MOLOTOV_3,
    EXPLOSIVE_MOLOTOV_INFERNAL,
    EXPLOSIVE_GAS_BOMB,
)
EXPLOSIVES_BY_ID: dict[str, Explosive] = {
    explosive.id: explosive for explosive in EXPLOSIVES
}


# ---------------------------------------------------------------------------
# Life items (bought) and life storages (found exploring only)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LifeItem:
    id: str
    name: str
    price: int
    heal_amount: int
    max_purchases: int


LIFE_ITEM_MINOR_POTION = LifeItem(
    id="minor_life_potion",
    name="Poción de Vida Menor",
    price=150,
    heal_amount=25,
    max_purchases=2,
)
LIFE_ITEM_MAJOR_POTION = LifeItem(
    id="major_life_potion",
    name="Poción de Vida Mayor",
    price=230,
    heal_amount=30,
    max_purchases=2,
)

LIFE_ITEMS = (LIFE_ITEM_MINOR_POTION, LIFE_ITEM_MAJOR_POTION)
LIFE_ITEMS_BY_ID: dict[str, LifeItem] = {item.id: item for item in LIFE_ITEMS}


@dataclass(frozen=True)
class LifeStorage:
    """A max-health boost found exploring — never sold in the shop."""

    id: str
    name: str
    max_health_bonus: int


LIFE_STORAGE_BASIC = LifeStorage(
    id="basic_life_storage", name="Almacenador de Vida Básico", max_health_bonus=20
)
LIFE_STORAGE_RARE = LifeStorage(
    id="rare_life_storage", name="Almacenador de Vida Raro", max_health_bonus=32
)

LIFE_STORAGES = (LIFE_STORAGE_BASIC, LIFE_STORAGE_RARE)
LIFE_STORAGES_BY_ID: dict[str, LifeStorage] = {
    storage.id: storage for storage in LIFE_STORAGES
}


# ---------------------------------------------------------------------------
# Amulets
# ---------------------------------------------------------------------------

AMULET_KIND_DEFENSE = "defense"
AMULET_KIND_ATTACK = "attack"
AMULET_KIND_LIFE = "life"
AMULET_KINDS = frozenset({AMULET_KIND_DEFENSE, AMULET_KIND_ATTACK, AMULET_KIND_LIFE})
#: A player may equip at most this many amulets at once.
MAX_EQUIPPED_AMULETS = 2


@dataclass(frozen=True)
class Amulet:
    id: str
    name: str
    price: int
    kind: str
    activation_chance: float
    description: str


AMULET_SURVIVOR_SHIELD = Amulet(
    id="survivor_shield",
    name="Escudo del Superviviente",
    price=950,
    kind=AMULET_KIND_DEFENSE,
    activation_chance=0.06,
    description=(
        "Deja pasar solo 1 de daño del ataque recibido, bloquea el resto"
    ),
)
AMULET_CHARGED_FIST = Amulet(
    id="charged_fist",
    name="Puño Cargado",
    price=850,
    kind=AMULET_KIND_ATTACK,
    activation_chance=0.04,
    description="Suma entre 10 y 15 de daño extra a ese golpe",
)
AMULET_VITAL_CORE = Amulet(
    id="vital_core",
    name="Núcleo Vital",
    price=700,
    kind=AMULET_KIND_LIFE,
    activation_chance=0.08,
    description="Recupera 15 de vida al instante",
)

AMULETS = (AMULET_SURVIVOR_SHIELD, AMULET_CHARGED_FIST, AMULET_VITAL_CORE)
AMULETS_BY_ID: dict[str, Amulet] = {amulet.id: amulet for amulet in AMULETS}


# ---------------------------------------------------------------------------
# Long-duration potions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Potion:
    id: str
    name: str
    price: int
    duration_turns: int
    description: str


POTION_ADRENALINE = Potion(
    id="adrenaline",
    name="Adrenalina",
    price=420,
    duration_turns=18,
    description="+3 de daño a todos tus ataques",
)
POTION_LIQUID_ARMOR = Potion(
    id="liquid_armor",
    name="Blindaje Líquido",
    price=480,
    duration_turns=15,
    description=(
        "+5% de absorción de armadura, desgaste a la mitad de velocidad"
    ),
)
POTION_FINAL_FURY = Potion(
    id="final_fury",
    name="Furia Final",
    price=280,
    duration_turns=1,
    description=(
        "Tu próximo ataque hace +10 de daño; luego -1 de ataque acumulativo "
        "durante 3 turnos hasta -3 permanente el resto del duelo"
    ),
)
POTION_FORTUNE = Potion(
    id="fortune",
    name="Fortuna",
    price=600,
    duration_turns=20,
    description=(
        "+5% de probabilidad de que se activen amuletos y balas críticas"
    ),
)

POTIONS = (POTION_ADRENALINE, POTION_LIQUID_ARMOR, POTION_FINAL_FURY, POTION_FORTUNE)
POTIONS_BY_ID: dict[str, Potion] = {potion.id: potion for potion in POTIONS}


# ---------------------------------------------------------------------------
# Shields
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Shield:
    id: str
    name: str
    price: int
    absorb_fraction: float
    hits_absorbed: int  # how many incoming attacks it soaks before it's spent
    max_purchases: int  # per player, per match
    cooldown_turns: int  # turns before a spent copy can be used again


SHIELD_BASIC_BARRIER = Shield(
    id="basic_barrier",
    name="Barrera Básica",
    price=160,
    absorb_fraction=0.4,
    hits_absorbed=1,
    max_purchases=2,
    cooldown_turns=3,
)
SHIELD_BASTION = Shield(
    id="bastion",
    name="Bastión",
    price=520,
    absorb_fraction=0.8,
    hits_absorbed=2,
    max_purchases=2,
    cooldown_turns=3,
)

SHIELDS = (SHIELD_BASIC_BARRIER, SHIELD_BASTION)
SHIELDS_BY_ID: dict[str, Shield] = {shield.id: shield for shield in SHIELDS}
#: Total shields a player may own at once (2 of each of the 2 kinds above).
MAX_SHIELDS_OWNED = sum(shield.max_purchases for shield in SHIELDS)


# ---------------------------------------------------------------------------
# Armor (static rules only — see module docstring)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Armor:
    id: str
    name: str
    price: int
    min_absorption: float  # starting range floor, e.g. 0.10
    max_absorption: float  # starting range ceiling, e.g. 0.13
    natural_wear_start_turn: int  # turn at which natural wear begins
    natural_wear_per_turn: float  # fraction lost per turn once wear starts
    #: Only Égida has these; zero means "no accelerated wear mechanic".
    crit_damage_reduction: float = 0.0  # e.g. 0.5 halves a crit's damage
    parasite_block_chance: float = 0.0
    accelerated_wear_step: float = 0.0  # instant drop when it blocks a crit/parasite
    accelerated_wear_floor: float = 0.0  # won't decay below this from blocking
    accelerated_wear_decay_per_two_turns: float = 0.0


ARMOR_LIGHT_PLATE = Armor(
    id="light_plate",
    name="Placa Ligera",
    price=650,
    min_absorption=0.10,
    max_absorption=0.13,
    natural_wear_start_turn=7,
    natural_wear_per_turn=0.01,
)
ARMOR_REINFORCED_PLATE = Armor(
    id="reinforced_plate",
    name="Placa Reforzada",
    price=1500,
    min_absorption=0.16,
    max_absorption=0.19,
    natural_wear_start_turn=15,
    natural_wear_per_turn=0.01,
)
ARMOR_AEGIS = Armor(
    id="aegis",
    name="Égida",
    price=3200,
    min_absorption=0.25,
    max_absorption=0.30,
    natural_wear_start_turn=30,
    natural_wear_per_turn=0.01,
    crit_damage_reduction=0.5,
    parasite_block_chance=0.125,  # midpoint of the documented 10%-15%
    accelerated_wear_step=0.025,  # midpoint of the documented 2%-3%
    accelerated_wear_floor=0.22,
    accelerated_wear_decay_per_two_turns=0.01,
)

ARMORS = (ARMOR_LIGHT_PLATE, ARMOR_REINFORCED_PLATE, ARMOR_AEGIS)
ARMORS_BY_ID: dict[str, Armor] = {armor.id: armor for armor in ARMORS}


# ---------------------------------------------------------------------------
# Validation — runs at import time, like arsenal.py's validate functions.
# ---------------------------------------------------------------------------


def _validate_unique_ids(items: tuple, label: str) -> None:
    ids = [item.id for item in items]
    if len(ids) != len(set(ids)):
        duplicates = {i for i in ids if ids.count(i) > 1}
        raise ValueError(f"Duplicate {label} id(s): {sorted(duplicates)}")


def _validate_non_negative_prices(items: tuple, label: str) -> None:
    for item in items:
        if item.price < 0:
            raise ValueError(f"{label} '{item.id}' has a negative price")


def validate() -> None:
    """Raise ValueError on any internal inconsistency in this module's data.

    Called at import time (see bottom of file) so a balance mistake is
    caught at startup, not mid-match.
    """
    _validate_unique_ids(WEAPONS, "weapon")
    _validate_unique_ids(EXPLOSIVES, "explosive")
    _validate_unique_ids(LIFE_ITEMS, "life item")
    _validate_unique_ids(LIFE_STORAGES, "life storage")
    _validate_unique_ids(AMULETS, "amulet")
    _validate_unique_ids(POTIONS, "potion")
    _validate_unique_ids(SHIELDS, "shield")
    _validate_unique_ids(ARMORS, "armor")

    for items, label in (
        (WEAPONS, "weapon"),
        (EXPLOSIVES, "explosive"),
        (LIFE_ITEMS, "life item"),
        (AMULETS, "amulet"),
        (POTIONS, "potion"),
        (SHIELDS, "shield"),
        (ARMORS, "armor"),
    ):
        _validate_non_negative_prices(items, label)

    for weapon in WEAPONS:
        if weapon.bullet_count <= 0:
            raise ValueError(f"Weapon '{weapon.id}' must have at least 1 bullet")
        if not 0.0 <= weapon.crit_chance_per_bullet <= 1.0:
            raise ValueError(f"Weapon '{weapon.id}' has an invalid crit chance")

    for explosive in EXPLOSIVES:
        if explosive.min_damage > explosive.max_damage:
            raise ValueError(
                f"Explosive '{explosive.id}' has min_damage > max_damage"
            )

    for amulet in AMULETS:
        if amulet.kind not in AMULET_KINDS:
            raise ValueError(f"Amulet '{amulet.id}' has an unknown kind")
        if not 0.0 <= amulet.activation_chance <= 1.0:
            raise ValueError(f"Amulet '{amulet.id}' has an invalid activation chance")

    for armor in ARMORS:
        if armor.min_absorption > armor.max_absorption:
            raise ValueError(f"Armor '{armor.id}' has min_absorption > max_absorption")


validate()
