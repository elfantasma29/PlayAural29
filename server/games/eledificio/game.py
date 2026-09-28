"""El Edificio game implementation.

Phases:

- ``PHASE_LOADOUT``: right after the match starts, both players privately
  choose which trap kinds to carry and how many shadows to buy (0-2),
  simultaneously — nobody's "turn" yet, same shape as Battle's
  PHASE_SELECTION. Once both have locked in, exploration begins.
- ``PHASE_EXPLORE``: movement/inspection, trace detection, coin drops, the
  chest, the Sobrecarga de Movimiento pickup, planting the traps and
  shadows bought in the loadout shop, health tracking with voice-narrated
  states, and the encounter that hands off to the buy phase.
- ``PHASE_BUY``: both combatants shop simultaneously for weapons,
  explosives, life items, amulets, potions, shields and armor. Weapons,
  armor and amulets are equipped the instant they're bought; shields and
  consumables go to inventory and are activated/used during combat.
- ``PHASE_COMBAT``: turn-based duel. Each turn allows up to
  ``state.MAX_FREE_ACTIONS_PER_TURN`` free actions (reload a weapon, drink
  a life potion, activate a shield) before a definitive action (attack
  with a weapon, throw an explosive) ends the turn. Status cards and
  long-duration potions are not wired in yet — see the module's trailing
  note for exactly what's deferred to the next slice.

No sounds are wired in yet — there's no confirmed asset pack for this game.
Every event here is announced through localized text only
(``broadcast_l``/``speak_l``), matching what other games in this codebase
do before their audio pack exists.
"""

from dataclasses import dataclass, field
from datetime import datetime
import random

from ..base import Game, GameOptions, Player
from ..registry import register_game
from ...game_utils.actions import Action, ActionSet, MenuInput, Visibility
from ...game_utils.bot_helper import BotHelper
from ...game_utils.game_result import GameResult, PlayerResult
from ...messages.localization import Localization
from ...users.base import MenuItem
from . import cards
from . import cards
from . import combat
from . import economy
from . import loadout
from . import map as room_map
from . import shadows
from . import state
from . import traps

STARTING_HEALTH = 100


@dataclass
class EdificioPlayer(Player):
    """Per-player El Edificio state."""

    room_id: str = ""
    wallet: economy.Wallet = field(default_factory=economy.Wallet)
    #: This player's own visit history, read by the *other* player's trace
    #: check — a player never consults their own history.
    room_last_visited_turn: dict[str, int] = field(default_factory=dict)
    trap_inventory: traps.TrapInventory = field(default_factory=traps.TrapInventory)
    #: Trap kinds bought in the pre-match loadout shop. Planting one during
    #: exploration is free from here on — it was already paid for here.
    purchased_trap_types: list[str] = field(default_factory=list)
    #: How many more shadows this player may plant this match (bought in
    #: the loadout shop, max state.MAX_SHADOW_CHARGES).
    shadow_charges: int = 0
    loadout_locked: bool = False
    shadow: shadows.Shadow | None = None
    movement_boost_turns_left: int = 0
    stunned_next_turn: bool = False
    health: int = STARTING_HEALTH
    max_health: int = STARTING_HEALTH
    last_health_state: str = state.HEALTH_STATE_FINE

    # ---- Buy phase (PHASE_BUY): what each combatant walks into the duel
    # with. Runtime combat state (reload lockouts, armor wear, active
    # potions, card cooldowns) is initialized separately once combat starts.
    owned_weapon_ids: list[str] = field(default_factory=lambda: [loadout.WEAPON_KNIFE.id])
    owned_explosives: dict[str, int] = field(default_factory=dict)
    owned_life_items: dict[str, int] = field(default_factory=dict)
    equipped_amulet_ids: list[str] = field(default_factory=list)
    owned_potions: list[str] = field(default_factory=list)
    owned_shields: dict[str, int] = field(default_factory=dict)
    owned_armor_id: str = ""  # "" means no armor
    buy_locked: bool = False

    # ---- Combat phase (PHASE_COMBAT) runtime state ----
    #: True while a weapon has ammo loaded. Firing empties it; reloading
    #: (a paid free action) refills it. Separate from the lockout below —
    #: a weapon can be both unlocked *and* still needing a reload.
    weapon_ammo_loaded: dict[str, bool] = field(default_factory=dict)
    #: Turns remaining before a just-fired weapon can be used again at all,
    #: regardless of ammo. Ticks down at the start of this player's turns.
    weapon_lockouts: dict[str, int] = field(default_factory=dict)
    #: Mirrors combat.EquippedArmor's mutable fields, flattened here so the
    #: whole player stays one simple serializable dataclass — reconstructed
    #: into a real EquippedArmor on the fly wherever the tested wear/
    #: mitigation logic in combat.py is needed.
    armor_turns_equipped: int = 0
    armor_accelerated_wear_events: int = 0
    #: The one shield currently soaking hits, if any ("" = none active).
    active_shield_id: str = ""
    active_shield_hits_remaining: int = 0
    #: Per-shield-kind cooldown before a spent copy can be activated again.
    shield_cooldowns: dict[str, int] = field(default_factory=dict)
    free_actions_used_this_turn: int = 0
    #: Status-card cooldowns (shared free deck — see cards.py).
    card_lockouts: dict[str, int] = field(default_factory=dict)
    #: Every status-card or long-duration-potion effect currently affecting
    #: this player, whether self-cast or cast on them by the rival.
    active_card_effects: list[combat.ActiveCardEffect] = field(default_factory=list)


@dataclass
class EdificioOptions(GameOptions):
    """No configurable options yet — the design is fixed for 1v1."""


@dataclass
@register_game
class EdificioGame(Game):
    """El Edificio: exploration-and-combat arcade game for PlayAural."""

    players: list[EdificioPlayer] = field(default_factory=list)
    options: EdificioOptions = field(default_factory=EdificioOptions)
    phase: str = state.PHASE_LOADOUT
    game_map: room_map.RoomMap = field(default_factory=room_map.RoomMap)
    coin_drops: list[economy.RoomCoinDrop] = field(default_factory=list)
    chest_room_id: str = ""
    chest_claimed: bool = False
    #: The one Sobrecarga de Movimiento pickup on the map this match.
    movement_boost_room_id: str = ""
    movement_boost_claimed: bool = False
    #: Set once the two players meet. No actions are offered past this point
    #: yet — the buy phase and combat come in a later slice.
    encounter_room_id: str = ""
    #: Pending Duelo trap: both sides must submit a real rock-paper-scissors
    #: choice before the explore action that triggered it can finish landing.
    pending_duel_victim_id: str = ""
    pending_duel_owner_id: str = ""
    pending_duel_room_id: str = ""
    pending_duel_victim_choice: int = -1
    pending_duel_owner_choice: int = -1
    #: Pending blind pick: the finder must choose which of two identical
    #: entries to attack before their explore action finishes landing.
    pending_blind_pick_finder_id: str = ""
    pending_blind_pick_target_id: str = ""
    pending_blind_pick_room_id: str = ""
    pending_blind_pick_order: list[str] = field(default_factory=list)
    #: Set once combat ends in a knockout.
    combat_winner_id: str = ""
    combat_end_reason: str = ""

    @classmethod
    def get_name(cls) -> str:
        return "El Edificio"

    @classmethod
    def get_type(cls) -> str:
        return "eledificio"

    @classmethod
    def get_category(cls) -> str:
        return "arcade"

    @classmethod
    def get_min_players(cls) -> int:
        return 2

    @classmethod
    def get_max_players(cls) -> int:
        return 2

    def create_player(
        self, player_id: str, name: str, is_bot: bool = False
    ) -> EdificioPlayer:
        return EdificioPlayer(id=player_id, name=name, is_bot=is_bot)

    def _player_locale(self, player: Player) -> str:
        user = self.get_user(player)
        return user.locale if user else "en"

    def _active_edificio_players(self) -> list[EdificioPlayer]:
        return [player for player in self.players if isinstance(player, EdificioPlayer)]

    def _opponent_of(self, player: EdificioPlayer) -> EdificioPlayer | None:
        for other in self._active_edificio_players():
            if other.id != player.id:
                return other
        return None

    def _movement_range_for(self, player: EdificioPlayer) -> int:
        if player.stunned_next_turn:
            return 1
        if player.movement_boost_turns_left > 0:
            return state.INSPECTIONS_PER_TURN_WITH_BOOST
        return state.INSPECTIONS_PER_TURN

    def _trap_in_room(
        self, room_id: str
    ) -> tuple[traps.PlacedTrap, EdificioPlayer] | None:
        """Return the trap placed in a room and its owner, from either player."""
        for player in self._active_edificio_players():
            found = player.trap_inventory.trap_in_room(room_id)
            if found is not None:
                return found, player
        return None

    # ==================================================================
    # Health
    # ==================================================================

    def _apply_damage(self, player: EdificioPlayer, amount: int) -> None:
        if amount <= 0:
            return
        new_health = player.health - amount
        if new_health <= 0:
            ward = next(
                (
                    effect
                    for effect in player.active_card_effects
                    if effect.card_id == cards.CARD_SECOND_CHANCE.id
                ),
                None,
            )
            if ward is not None:
                player.active_card_effects.remove(ward)
                new_health = 1
                self.broadcast_l(
                    "eledificio-second-chance-triggered",
                    buffer="game",
                    player=player.name,
                )
        player.health = max(0, new_health)
        new_state = combat.health_state_for(player.health, player.max_health)
        if new_state != player.last_health_state:
            player.last_health_state = new_state
            line = combat.narrate_health_state(new_state, player.name)
            if line:
                self.broadcast_l("eledificio-health-state", buffer="game", line=line)

    # ==================================================================
    # Game flow
    # ==================================================================

    def on_start(self) -> None:
        self.status = "playing"
        self.phase = state.PHASE_LOADOUT
        self.round = 0
        self.encounter_room_id = ""
        self.chest_claimed = False
        self.movement_boost_claimed = False
        self.pending_duel_victim_id = ""
        self.pending_duel_owner_id = ""
        self.pending_duel_room_id = ""
        self.pending_duel_victim_choice = -1
        self.pending_duel_owner_choice = -1
        self.pending_blind_pick_finder_id = ""
        self.pending_blind_pick_target_id = ""
        self.pending_blind_pick_room_id = ""
        self.pending_blind_pick_order = []

        for player in self._active_edificio_players():
            player.wallet = economy.Wallet()
            player.room_id = ""
            player.room_last_visited_turn = {}
            player.trap_inventory = traps.TrapInventory()
            player.purchased_trap_types = []
            player.shadow_charges = 0
            player.loadout_locked = False
            player.shadow = None
            player.movement_boost_turns_left = 0
            player.stunned_next_turn = False
            player.health = STARTING_HEALTH
            player.max_health = STARTING_HEALTH
            player.last_health_state = state.HEALTH_STATE_FINE

        self.broadcast_l("eledificio-loadout-start", buffer="game")
        self._auto_loadout_for_bots()
        self.refresh_menus()

    def _all_loadouts_locked(self) -> bool:
        players = self._active_edificio_players()
        return bool(players) and all(player.loadout_locked for player in players)

    def _begin_exploration(self) -> None:
        self.phase = state.PHASE_EXPLORE
        active_players = self._active_edificio_players()
        rng_seed_source = "".join(sorted(player.id for player in active_players))
        rng = random.Random(rng_seed_source or None)
        self.game_map = room_map.build_map(len(active_players), rng=rng)

        spawn_a, spawn_b = self.game_map.spawn_a, self.game_map.spawn_b
        excluded = {spawn_a, spawn_b}
        self.coin_drops = economy.place_coin_drops(
            self.game_map.room_ids, excluded, rng
        )
        self.chest_room_id = economy.place_chest(
            self.game_map.room_ids, excluded, rng
        )
        boost_eligible = [
            room_id for room_id in self.game_map.room_ids if room_id not in excluded
        ]
        self.movement_boost_room_id = rng.choice(boost_eligible)

        for index, player in enumerate(active_players):
            player.room_id = spawn_a if index == 0 else spawn_b
            player.room_last_visited_turn = {player.room_id: 0}

        self.set_turn_players(active_players)
        self.broadcast_l(
            "eledificio-intro",
            buffer="game",
            rooms=len(self.game_map.room_ids),
        )
        self._start_turn()

    def _start_turn(self) -> None:
        self.round += 1
        player = self.current_player
        if not isinstance(player, EdificioPlayer):
            return
        if player.movement_boost_turns_left > 0:
            player.movement_boost_turns_left -= 1
        self.broadcast_l(
            "eledificio-turn-start",
            buffer="game",
            player=player.name,
        )
        if player.is_bot:
            BotHelper.jolt_bot(player, ticks=random.randint(8, 20))
        self.refresh_menus()

    def on_tick(self) -> None:
        super().on_tick()
        if self.status != "playing":
            return

        if self.phase == state.PHASE_LOADOUT:
            self._auto_loadout_for_bots()
            if self._all_loadouts_locked():
                self._begin_exploration()
            return

        if self.phase == state.PHASE_BUY:
            self._auto_buy_for_bots()
            if self._all_buys_locked():
                self._begin_combat()
            return

        if self.phase == state.PHASE_COMBAT:
            player = self.current_player
            if not isinstance(player, EdificioPlayer) or not player.is_bot:
                return
            if player.bot_think_ticks > 0:
                player.bot_think_ticks -= 1
                return
            self._perform_bot_combat_turn(player)
            return

        if self._turn_frozen():
            return
        player = self.current_player
        if not isinstance(player, EdificioPlayer) or not player.is_bot:
            return
        if player.bot_think_ticks > 0:
            player.bot_think_ticks -= 1
            return
        self._perform_bot_turn(player)

    def before_menu_build(self, player: Player) -> None:
        self._sync_turn_actions(player)

    def _sync_turn_actions(self, player: Player) -> None:
        turn_set = self.get_action_set(player, "turn")
        if not isinstance(player, EdificioPlayer) or not turn_set:
            return
        turn_set._actions.clear()
        turn_set._order.clear()
        if self.status != "playing" or player.is_spectator:
            return
        locale = self._player_locale(player)
        if self.phase == state.PHASE_LOADOUT:
            self._populate_loadout_actions(turn_set, player, locale)
        elif self.phase == state.PHASE_BUY:
            self._populate_buy_actions(turn_set, player, locale)
        elif self.phase == state.PHASE_COMBAT:
            self._populate_combat_actions(turn_set, player, locale)
        else:
            self._populate_exploration_actions(turn_set, player, locale)

    # ==================================================================
    # Loadout shop (PHASE_LOADOUT) — not turn-based, both players act at
    # the same time. Mirrors Battle's simultaneous PHASE_SELECTION.
    # ==================================================================

    #: Bots keep at least this much in reserve when shopping their loadout,
    #: so they still arrive at the eventual combat buy phase with money.
    BOT_LOADOUT_RESERVE = 1000

    def _is_loadout_action_hidden(self, player: Player) -> Visibility:
        if self.status != "playing" or player.is_spectator:
            return Visibility.HIDDEN
        if self.phase != state.PHASE_LOADOUT:
            return Visibility.HIDDEN
        if not isinstance(player, EdificioPlayer) or player.loadout_locked:
            return Visibility.HIDDEN
        return Visibility.VISIBLE

    def _is_loadout_action_enabled(self, player: Player) -> str | None:
        if self.status != "playing":
            return "action-not-playing"
        if self.phase != state.PHASE_LOADOUT:
            return "action-not-available"
        if not isinstance(player, EdificioPlayer) or player.loadout_locked:
            return "action-not-available"
        return None

    def _is_toggle_trap_enabled(
        self, player: Player, *, action_id: str | None = None
    ) -> str | None:
        base = self._is_loadout_action_enabled(player)
        if base:
            return base
        assert isinstance(player, EdificioPlayer) and action_id is not None
        trap_id = action_id.removeprefix("toggle_trap_")
        trap_def = traps.TRAPS_BY_ID[trap_id]
        if trap_id not in player.purchased_trap_types and not player.wallet.can_afford(
            trap_def.price
        ):
            return "eledificio-not-enough-coins"
        return None

    def _get_toggle_trap_label(self, player: Player, action_id: str) -> str:
        trap_id = action_id.removeprefix("toggle_trap_")
        trap_def = traps.TRAPS_BY_ID[trap_id]
        locale = self._player_locale(player)
        owned = (
            isinstance(player, EdificioPlayer)
            and trap_id in player.purchased_trap_types
        )
        key = "eledificio-trap-owned" if owned else "eledificio-trap-not-owned"
        return Localization.get(locale, key, name=trap_def.name, price=trap_def.price)

    def _action_toggle_trap(self, player: Player, action_id: str) -> None:
        if not isinstance(player, EdificioPlayer) or player.loadout_locked:
            return
        trap_id = action_id.removeprefix("toggle_trap_")
        trap_def = traps.TRAPS_BY_ID.get(trap_id)
        if trap_def is None:
            return
        if trap_id in player.purchased_trap_types:
            player.purchased_trap_types.remove(trap_id)
            player.wallet.add(trap_def.price)
        else:
            if not player.wallet.can_afford(trap_def.price):
                return
            player.wallet.spend(trap_def.price)
            player.purchased_trap_types.append(trap_id)
        self.refresh_menus()

    def _is_buy_shadow_enabled(self, player: Player) -> str | None:
        base = self._is_loadout_action_enabled(player)
        if base:
            return base
        assert isinstance(player, EdificioPlayer)
        if player.shadow_charges >= state.MAX_SHADOW_CHARGES:
            return "eledificio-shadow-limit-reached"
        if not player.wallet.can_afford(economy.SHADOW_PRICE):
            return "eledificio-not-enough-coins"
        return None

    def _action_buy_shadow(self, player: Player, action_id: str) -> None:
        if not isinstance(player, EdificioPlayer):
            return
        if self._is_buy_shadow_enabled(player) is not None:
            return
        player.wallet.spend(economy.SHADOW_PRICE)
        player.shadow_charges += 1
        self.refresh_menus()

    def _is_sell_shadow_enabled(self, player: Player) -> str | None:
        base = self._is_loadout_action_enabled(player)
        if base:
            return base
        assert isinstance(player, EdificioPlayer)
        if player.shadow_charges <= 0:
            return "eledificio-nothing-to-sell"
        return None

    def _action_sell_shadow(self, player: Player, action_id: str) -> None:
        if not isinstance(player, EdificioPlayer):
            return
        if self._is_sell_shadow_enabled(player) is not None:
            return
        player.shadow_charges -= 1
        player.wallet.add(economy.SHADOW_PRICE)
        self.refresh_menus()

    def _get_shadow_count_label(self, player: Player, action_id: str) -> str:
        charges = player.shadow_charges if isinstance(player, EdificioPlayer) else 0
        return Localization.get(
            self._player_locale(player),
            "eledificio-shadow-count",
            count=charges,
            max=state.MAX_SHADOW_CHARGES,
            price=economy.SHADOW_PRICE,
        )

    def _is_submit_loadout_enabled(self, player: Player) -> str | None:
        return self._is_loadout_action_enabled(player)

    def _action_submit_loadout(self, player: Player, action_id: str) -> None:
        if not isinstance(player, EdificioPlayer) or player.loadout_locked:
            return
        player.loadout_locked = True
        self.broadcast_l(
            "eledificio-loadout-locked", buffer="game", player=player.name
        )
        self.refresh_menus()
        if self._all_loadouts_locked():
            self._begin_exploration()

    def _populate_loadout_actions(
        self, turn_set: ActionSet, player: EdificioPlayer, locale: str
    ) -> None:
        for trap_def in traps.TRAPS:
            turn_set.add(
                Action(
                    id=f"toggle_trap_{trap_def.id}",
                    label=Localization.get(
                        locale,
                        "eledificio-trap-not-owned",
                        name=trap_def.name,
                        price=trap_def.price,
                    ),
                    handler="_action_toggle_trap",
                    is_enabled="_is_toggle_trap_enabled",
                    is_hidden="_is_loadout_action_hidden",
                    get_label="_get_toggle_trap_label",
                    show_in_actions_menu=False,
                )
            )
        turn_set.add(
            Action(
                id="buy_shadow",
                label=Localization.get(
                    locale,
                    "eledificio-shadow-count",
                    count=player.shadow_charges,
                    max=state.MAX_SHADOW_CHARGES,
                    price=economy.SHADOW_PRICE,
                ),
                handler="_action_buy_shadow",
                is_enabled="_is_buy_shadow_enabled",
                is_hidden="_is_loadout_action_hidden",
                get_label="_get_shadow_count_label",
                show_in_actions_menu=False,
            )
        )
        turn_set.add(
            Action(
                id="sell_shadow",
                label=Localization.get(locale, "eledificio-sell-shadow"),
                handler="_action_sell_shadow",
                is_enabled="_is_sell_shadow_enabled",
                is_hidden="_is_loadout_action_hidden",
                show_in_actions_menu=False,
            )
        )
        turn_set.add(
            Action(
                id="submit_loadout",
                label=Localization.get(locale, "eledificio-submit-loadout"),
                handler="_action_submit_loadout",
                is_enabled="_is_submit_loadout_enabled",
                is_hidden="_is_loadout_action_hidden",
                show_in_actions_menu=False,
            )
        )

    def _auto_loadout_for_bots(self) -> None:
        for player in self._active_edificio_players():
            if player.is_bot and not player.loadout_locked:
                self._auto_fill_loadout(player)

    def _auto_fill_loadout(self, player: EdificioPlayer) -> None:
        trap_ids = [trap_def.id for trap_def in traps.TRAPS]
        random.shuffle(trap_ids)
        for trap_id in trap_ids:
            trap_def = traps.TRAPS_BY_ID[trap_id]
            if (
                player.wallet.balance - trap_def.price >= self.BOT_LOADOUT_RESERVE
                and random.random() < 0.5
            ):
                player.wallet.spend(trap_def.price)
                player.purchased_trap_types.append(trap_id)
        for _ in range(state.MAX_SHADOW_CHARGES):
            if (
                player.wallet.balance - economy.SHADOW_PRICE >= self.BOT_LOADOUT_RESERVE
                and random.random() < 0.7
            ):
                player.wallet.spend(economy.SHADOW_PRICE)
                player.shadow_charges += 1
        player.loadout_locked = True
        self.broadcast_l(
            "eledificio-loadout-locked", buffer="game", player=player.name
        )

    # ==================================================================
    # Buy phase (PHASE_BUY) — triggered by an encounter. Not turn-based,
    # both combatants shop at the same time, same shape as the loadout shop.
    # ==================================================================

    #: Bots keep at least this much in reserve so they don't spend every
    #: last coin and have literally nothing to fall back on turn one.
    BOT_BUY_RESERVE = 100

    def _all_buys_locked(self) -> bool:
        players = self._active_edificio_players()
        return bool(players) and all(player.buy_locked for player in players)

    def _begin_combat(self) -> None:
        self.phase = state.PHASE_COMBAT
        self.combat_winner_id = ""
        self.combat_end_reason = ""
        active_players = self._active_edificio_players()
        for player in active_players:
            player.weapon_ammo_loaded = {
                weapon_id: True for weapon_id in player.owned_weapon_ids
            }
            player.weapon_lockouts = {}
            player.armor_turns_equipped = 0
            player.armor_accelerated_wear_events = 0
            player.active_shield_id = ""
            player.active_shield_hits_remaining = 0
            player.shield_cooldowns = {}
            player.free_actions_used_this_turn = 0
            player.card_lockouts = {}
            player.active_card_effects = []
        random.shuffle(active_players)
        self.set_turn_players(active_players)
        self.broadcast_l("eledificio-combat-start", buffer="game")
        self._start_combat_turn()

    def _is_buy_action_hidden(self, player: Player) -> Visibility:
        if self.status != "playing" or player.is_spectator:
            return Visibility.HIDDEN
        if self.phase != state.PHASE_BUY:
            return Visibility.HIDDEN
        if not isinstance(player, EdificioPlayer) or player.buy_locked:
            return Visibility.HIDDEN
        return Visibility.VISIBLE

    def _is_buy_action_enabled(self, player: Player) -> str | None:
        if self.status != "playing":
            return "action-not-playing"
        if self.phase != state.PHASE_BUY:
            return "action-not-available"
        if not isinstance(player, EdificioPlayer) or player.buy_locked:
            return "action-not-available"
        return None

    # ---- Weapons (own or don't; Cuchillo Fantasma is free and automatic) --

    _PURCHASABLE_WEAPON_IDS = [
        weapon.id for weapon in loadout.WEAPONS if weapon.id != loadout.WEAPON_KNIFE.id
    ]

    def _is_toggle_weapon_enabled(
        self, player: Player, *, action_id: str | None = None
    ) -> str | None:
        base = self._is_buy_action_enabled(player)
        if base:
            return base
        assert isinstance(player, EdificioPlayer) and action_id is not None
        weapon_id = action_id.removeprefix("toggle_weapon_")
        weapon = loadout.WEAPONS_BY_ID[weapon_id]
        if weapon_id not in player.owned_weapon_ids and not player.wallet.can_afford(
            weapon.price
        ):
            return "eledificio-not-enough-coins"
        return None

    def _get_toggle_weapon_label(self, player: Player, action_id: str) -> str:
        weapon_id = action_id.removeprefix("toggle_weapon_")
        weapon = loadout.WEAPONS_BY_ID[weapon_id]
        owned = (
            isinstance(player, EdificioPlayer) and weapon_id in player.owned_weapon_ids
        )
        key = "eledificio-item-owned" if owned else "eledificio-item-not-owned"
        return Localization.get(
            self._player_locale(player), key, name=weapon.name, price=weapon.price
        )

    def _action_toggle_weapon(self, player: Player, action_id: str) -> None:
        if not isinstance(player, EdificioPlayer) or player.buy_locked:
            return
        weapon_id = action_id.removeprefix("toggle_weapon_")
        weapon = loadout.WEAPONS_BY_ID.get(weapon_id)
        if weapon is None or weapon_id == loadout.WEAPON_KNIFE.id:
            return
        if weapon_id in player.owned_weapon_ids:
            player.owned_weapon_ids.remove(weapon_id)
            player.wallet.add(weapon.price)
        else:
            if not player.wallet.can_afford(weapon.price):
                return
            player.wallet.spend(weapon.price)
            player.owned_weapon_ids.append(weapon_id)
        self.refresh_menus()

    # ---- Amulets (own or don't, max state loadout.MAX_EQUIPPED_AMULETS) --

    def _is_toggle_amulet_enabled(
        self, player: Player, *, action_id: str | None = None
    ) -> str | None:
        base = self._is_buy_action_enabled(player)
        if base:
            return base
        assert isinstance(player, EdificioPlayer) and action_id is not None
        amulet_id = action_id.removeprefix("toggle_amulet_")
        amulet = loadout.AMULETS_BY_ID[amulet_id]
        if amulet_id in player.equipped_amulet_ids:
            return None
        if len(player.equipped_amulet_ids) >= loadout.MAX_EQUIPPED_AMULETS:
            return "eledificio-amulet-limit-reached"
        if not player.wallet.can_afford(amulet.price):
            return "eledificio-not-enough-coins"
        return None

    def _get_toggle_amulet_label(self, player: Player, action_id: str) -> str:
        amulet_id = action_id.removeprefix("toggle_amulet_")
        amulet = loadout.AMULETS_BY_ID[amulet_id]
        owned = (
            isinstance(player, EdificioPlayer)
            and amulet_id in player.equipped_amulet_ids
        )
        key = "eledificio-item-owned" if owned else "eledificio-item-not-owned"
        return Localization.get(
            self._player_locale(player), key, name=amulet.name, price=amulet.price
        )

    def _action_toggle_amulet(self, player: Player, action_id: str) -> None:
        if not isinstance(player, EdificioPlayer) or player.buy_locked:
            return
        amulet_id = action_id.removeprefix("toggle_amulet_")
        amulet = loadout.AMULETS_BY_ID.get(amulet_id)
        if amulet is None:
            return
        if amulet_id in player.equipped_amulet_ids:
            player.equipped_amulet_ids.remove(amulet_id)
            player.wallet.add(amulet.price)
        else:
            if len(player.equipped_amulet_ids) >= loadout.MAX_EQUIPPED_AMULETS:
                return
            if not player.wallet.can_afford(amulet.price):
                return
            player.wallet.spend(amulet.price)
            player.equipped_amulet_ids.append(amulet_id)
        self.refresh_menus()

    # ---- Long-duration potions (own or don't, one of each at most) -------

    def _is_toggle_potion_enabled(
        self, player: Player, *, action_id: str | None = None
    ) -> str | None:
        base = self._is_buy_action_enabled(player)
        if base:
            return base
        assert isinstance(player, EdificioPlayer) and action_id is not None
        potion_id = action_id.removeprefix("toggle_potion_")
        potion = loadout.POTIONS_BY_ID[potion_id]
        if potion_id not in player.owned_potions and not player.wallet.can_afford(
            potion.price
        ):
            return "eledificio-not-enough-coins"
        return None

    def _get_toggle_potion_label(self, player: Player, action_id: str) -> str:
        potion_id = action_id.removeprefix("toggle_potion_")
        potion = loadout.POTIONS_BY_ID[potion_id]
        owned = (
            isinstance(player, EdificioPlayer) and potion_id in player.owned_potions
        )
        key = "eledificio-item-owned" if owned else "eledificio-item-not-owned"
        return Localization.get(
            self._player_locale(player), key, name=potion.name, price=potion.price
        )

    def _action_toggle_potion(self, player: Player, action_id: str) -> None:
        if not isinstance(player, EdificioPlayer) or player.buy_locked:
            return
        potion_id = action_id.removeprefix("toggle_potion_")
        potion = loadout.POTIONS_BY_ID.get(potion_id)
        if potion is None:
            return
        if potion_id in player.owned_potions:
            player.owned_potions.remove(potion_id)
            player.wallet.add(potion.price)
        else:
            if not player.wallet.can_afford(potion.price):
                return
            player.wallet.spend(potion.price)
            player.owned_potions.append(potion_id)
        self.refresh_menus()

    # ---- Armor (mutually exclusive: none, or exactly one tier) -----------

    def _armor_options(self, player: Player) -> list[str]:
        return ["none"] + [armor.id for armor in loadout.ARMORS]

    def _armor_option_label(self, player: Player, option_id: str) -> str:
        locale = self._player_locale(player)
        if option_id == "none":
            return Localization.get(locale, "eledificio-armor-none")
        armor = loadout.ARMORS_BY_ID[option_id]
        return Localization.get(
            locale, "eledificio-armor-option", name=armor.name, price=armor.price
        )

    def _is_select_armor_enabled(self, player: Player) -> str | None:
        return self._is_buy_action_enabled(player)

    def _get_select_armor_label(self, player: Player, action_id: str) -> str:
        locale = self._player_locale(player)
        if not isinstance(player, EdificioPlayer) or not player.owned_armor_id:
            current_name = Localization.get(locale, "eledificio-armor-none")
        else:
            current_name = loadout.ARMORS_BY_ID[player.owned_armor_id].name
        return Localization.get(
            locale, "eledificio-select-armor", current=current_name
        )

    def _action_select_armor(self, player: Player, option_id: str, action_id: str) -> None:
        if not isinstance(player, EdificioPlayer) or player.buy_locked:
            return
        if option_id != "none" and option_id not in loadout.ARMORS_BY_ID:
            return
        new_price = 0 if option_id == "none" else loadout.ARMORS_BY_ID[option_id].price
        old_price = (
            0
            if not player.owned_armor_id
            else loadout.ARMORS_BY_ID[player.owned_armor_id].price
        )
        if new_price > old_price and not player.wallet.can_afford(
            new_price - old_price
        ):
            return
        player.wallet.add(old_price)
        player.wallet.spend(new_price)
        player.owned_armor_id = "" if option_id == "none" else option_id
        self.refresh_menus()

    def _armor_bot_select(self, player: Player, options: list[str]) -> str | None:
        return options[0] if options else None

    # ---- Explosives (quantity, no per-type cap) ---------------------------

    def _buyable_explosive_options(self, player: Player) -> list[str]:
        if not isinstance(player, EdificioPlayer):
            return []
        return [
            explosive.id
            for explosive in loadout.EXPLOSIVES
            if player.wallet.can_afford(explosive.price)
        ]

    def _sellable_explosive_options(self, player: Player) -> list[str]:
        if not isinstance(player, EdificioPlayer):
            return []
        return [
            explosive_id
            for explosive_id, count in player.owned_explosives.items()
            if count > 0
        ]

    def _explosive_option_label(self, player: Player, explosive_id: str) -> str:
        explosive = loadout.EXPLOSIVES_BY_ID[explosive_id]
        owned = (
            player.owned_explosives.get(explosive_id, 0)
            if isinstance(player, EdificioPlayer)
            else 0
        )
        return Localization.get(
            self._player_locale(player),
            "eledificio-quantity-item",
            name=explosive.name,
            price=explosive.price,
            owned=owned,
        )

    def _is_buy_explosive_enabled(self, player: Player) -> str | None:
        base = self._is_buy_action_enabled(player)
        if base:
            return base
        if not self._buyable_explosive_options(player):
            return "eledificio-not-enough-coins"
        return None

    def _is_sell_explosive_enabled(self, player: Player) -> str | None:
        base = self._is_buy_action_enabled(player)
        if base:
            return base
        if not self._sellable_explosive_options(player):
            return "eledificio-nothing-to-sell"
        return None

    def _action_buy_explosive(self, player: Player, explosive_id: str, action_id: str) -> None:
        if not isinstance(player, EdificioPlayer) or player.buy_locked:
            return
        explosive = loadout.EXPLOSIVES_BY_ID.get(explosive_id)
        if explosive is None or not player.wallet.can_afford(explosive.price):
            return
        player.wallet.spend(explosive.price)
        player.owned_explosives[explosive_id] = (
            player.owned_explosives.get(explosive_id, 0) + 1
        )
        self.refresh_menus()

    def _action_sell_explosive(self, player: Player, explosive_id: str, action_id: str) -> None:
        if not isinstance(player, EdificioPlayer) or player.buy_locked:
            return
        owned = player.owned_explosives.get(explosive_id, 0)
        if owned <= 0:
            return
        explosive = loadout.EXPLOSIVES_BY_ID[explosive_id]
        player.owned_explosives[explosive_id] = owned - 1
        if player.owned_explosives[explosive_id] <= 0:
            del player.owned_explosives[explosive_id]
        player.wallet.add(explosive.price)
        self.refresh_menus()

    def _buy_explosive_bot_select(self, player: Player, options: list[str]) -> str | None:
        return random.choice(options) if options else None

    # ---- Life items (quantity, capped per type at loadout's max_purchases) --

    def _buyable_life_item_options(self, player: Player) -> list[str]:
        if not isinstance(player, EdificioPlayer):
            return []
        return [
            item.id
            for item in loadout.LIFE_ITEMS
            if player.owned_life_items.get(item.id, 0) < item.max_purchases
            and player.wallet.can_afford(item.price)
        ]

    def _sellable_life_item_options(self, player: Player) -> list[str]:
        if not isinstance(player, EdificioPlayer):
            return []
        return [
            item_id for item_id, count in player.owned_life_items.items() if count > 0
        ]

    def _life_item_option_label(self, player: Player, item_id: str) -> str:
        item = loadout.LIFE_ITEMS_BY_ID[item_id]
        owned = (
            player.owned_life_items.get(item_id, 0)
            if isinstance(player, EdificioPlayer)
            else 0
        )
        return Localization.get(
            self._player_locale(player),
            "eledificio-quantity-item",
            name=item.name,
            price=item.price,
            owned=owned,
        )

    def _is_buy_life_item_enabled(self, player: Player) -> str | None:
        base = self._is_buy_action_enabled(player)
        if base:
            return base
        if not self._buyable_life_item_options(player):
            return "eledificio-not-enough-coins"
        return None

    def _is_sell_life_item_enabled(self, player: Player) -> str | None:
        base = self._is_buy_action_enabled(player)
        if base:
            return base
        if not self._sellable_life_item_options(player):
            return "eledificio-nothing-to-sell"
        return None

    def _action_buy_life_item(self, player: Player, item_id: str, action_id: str) -> None:
        if not isinstance(player, EdificioPlayer) or player.buy_locked:
            return
        item = loadout.LIFE_ITEMS_BY_ID.get(item_id)
        if item is None:
            return
        owned = player.owned_life_items.get(item_id, 0)
        if owned >= item.max_purchases or not player.wallet.can_afford(item.price):
            return
        player.wallet.spend(item.price)
        player.owned_life_items[item_id] = owned + 1
        self.refresh_menus()

    def _action_sell_life_item(self, player: Player, item_id: str, action_id: str) -> None:
        if not isinstance(player, EdificioPlayer) or player.buy_locked:
            return
        owned = player.owned_life_items.get(item_id, 0)
        if owned <= 0:
            return
        item = loadout.LIFE_ITEMS_BY_ID[item_id]
        player.owned_life_items[item_id] = owned - 1
        if player.owned_life_items[item_id] <= 0:
            del player.owned_life_items[item_id]
        player.wallet.add(item.price)
        self.refresh_menus()

    def _buy_life_item_bot_select(self, player: Player, options: list[str]) -> str | None:
        return random.choice(options) if options else None

    # ---- Shields (quantity, capped per type at loadout's max_purchases) --

    def _buyable_shield_options(self, player: Player) -> list[str]:
        if not isinstance(player, EdificioPlayer):
            return []
        return [
            shield.id
            for shield in loadout.SHIELDS
            if player.owned_shields.get(shield.id, 0) < shield.max_purchases
            and player.wallet.can_afford(shield.price)
        ]

    def _sellable_shield_options(self, player: Player) -> list[str]:
        if not isinstance(player, EdificioPlayer):
            return []
        return [
            shield_id for shield_id, count in player.owned_shields.items() if count > 0
        ]

    def _shield_option_label(self, player: Player, shield_id: str) -> str:
        shield = loadout.SHIELDS_BY_ID[shield_id]
        owned = (
            player.owned_shields.get(shield_id, 0)
            if isinstance(player, EdificioPlayer)
            else 0
        )
        return Localization.get(
            self._player_locale(player),
            "eledificio-quantity-item",
            name=shield.name,
            price=shield.price,
            owned=owned,
        )

    def _is_buy_shield_enabled(self, player: Player) -> str | None:
        base = self._is_buy_action_enabled(player)
        if base:
            return base
        if not self._buyable_shield_options(player):
            return "eledificio-not-enough-coins"
        return None

    def _is_sell_shield_enabled(self, player: Player) -> str | None:
        base = self._is_buy_action_enabled(player)
        if base:
            return base
        if not self._sellable_shield_options(player):
            return "eledificio-nothing-to-sell"
        return None

    def _action_buy_shield(self, player: Player, shield_id: str, action_id: str) -> None:
        if not isinstance(player, EdificioPlayer) or player.buy_locked:
            return
        shield = loadout.SHIELDS_BY_ID.get(shield_id)
        if shield is None:
            return
        owned = player.owned_shields.get(shield_id, 0)
        if owned >= shield.max_purchases or not player.wallet.can_afford(shield.price):
            return
        player.wallet.spend(shield.price)
        player.owned_shields[shield_id] = owned + 1
        self.refresh_menus()

    def _action_sell_shield(self, player: Player, shield_id: str, action_id: str) -> None:
        if not isinstance(player, EdificioPlayer) or player.buy_locked:
            return
        owned = player.owned_shields.get(shield_id, 0)
        if owned <= 0:
            return
        shield = loadout.SHIELDS_BY_ID[shield_id]
        player.owned_shields[shield_id] = owned - 1
        if player.owned_shields[shield_id] <= 0:
            del player.owned_shields[shield_id]
        player.wallet.add(shield.price)
        self.refresh_menus()

    def _buy_shield_bot_select(self, player: Player, options: list[str]) -> str | None:
        return random.choice(options) if options else None

    # ---- Submit ------------------------------------------------------

    def _is_submit_buy_enabled(self, player: Player) -> str | None:
        return self._is_buy_action_enabled(player)

    def _action_submit_buy(self, player: Player, action_id: str) -> None:
        if not isinstance(player, EdificioPlayer) or player.buy_locked:
            return
        player.buy_locked = True
        self.broadcast_l("eledificio-buy-locked", buffer="game", player=player.name)
        self.refresh_menus()
        if self._all_buys_locked():
            self._begin_combat()

    def _populate_buy_actions(
        self, turn_set: ActionSet, player: EdificioPlayer, locale: str
    ) -> None:
        for weapon_id in self._PURCHASABLE_WEAPON_IDS:
            weapon = loadout.WEAPONS_BY_ID[weapon_id]
            turn_set.add(
                Action(
                    id=f"toggle_weapon_{weapon_id}",
                    label=Localization.get(
                        locale,
                        "eledificio-item-not-owned",
                        name=weapon.name,
                        price=weapon.price,
                    ),
                    handler="_action_toggle_weapon",
                    is_enabled="_is_toggle_weapon_enabled",
                    is_hidden="_is_buy_action_hidden",
                    get_label="_get_toggle_weapon_label",
                    show_in_actions_menu=False,
                )
            )
        for amulet in loadout.AMULETS:
            turn_set.add(
                Action(
                    id=f"toggle_amulet_{amulet.id}",
                    label=Localization.get(
                        locale,
                        "eledificio-item-not-owned",
                        name=amulet.name,
                        price=amulet.price,
                    ),
                    handler="_action_toggle_amulet",
                    is_enabled="_is_toggle_amulet_enabled",
                    is_hidden="_is_buy_action_hidden",
                    get_label="_get_toggle_amulet_label",
                    show_in_actions_menu=False,
                )
            )
        for potion in loadout.POTIONS:
            turn_set.add(
                Action(
                    id=f"toggle_potion_{potion.id}",
                    label=Localization.get(
                        locale,
                        "eledificio-item-not-owned",
                        name=potion.name,
                        price=potion.price,
                    ),
                    handler="_action_toggle_potion",
                    is_enabled="_is_toggle_potion_enabled",
                    is_hidden="_is_buy_action_hidden",
                    get_label="_get_toggle_potion_label",
                    show_in_actions_menu=False,
                )
            )
        turn_set.add(
            Action(
                id="select_armor",
                label=Localization.get(
                    locale, "eledificio-select-armor", current=""
                ),
                handler="_action_select_armor",
                is_enabled="_is_select_armor_enabled",
                is_hidden="_is_buy_action_hidden",
                get_label="_get_select_armor_label",
                input_request=MenuInput(
                    prompt="eledificio-select-armor-prompt",
                    options="_armor_options",
                    option_label="_armor_option_label",
                    bot_select="_armor_bot_select",
                ),
                show_in_actions_menu=False,
            )
        )
        turn_set.add(
            Action(
                id="buy_explosive",
                label=Localization.get(locale, "eledificio-buy-explosive"),
                handler="_action_buy_explosive",
                is_enabled="_is_buy_explosive_enabled",
                is_hidden="_is_buy_action_hidden",
                input_request=MenuInput(
                    prompt="eledificio-buy-explosive-prompt",
                    options="_buyable_explosive_options",
                    option_label="_explosive_option_label",
                    bot_select="_buy_explosive_bot_select",
                ),
                show_in_actions_menu=False,
            )
        )
        turn_set.add(
            Action(
                id="sell_explosive",
                label=Localization.get(locale, "eledificio-sell-explosive"),
                handler="_action_sell_explosive",
                is_enabled="_is_sell_explosive_enabled",
                is_hidden="_is_buy_action_hidden",
                input_request=MenuInput(
                    prompt="eledificio-sell-explosive-prompt",
                    options="_sellable_explosive_options",
                    option_label="_explosive_option_label",
                ),
                show_in_actions_menu=False,
            )
        )
        turn_set.add(
            Action(
                id="buy_life_item",
                label=Localization.get(locale, "eledificio-buy-life-item"),
                handler="_action_buy_life_item",
                is_enabled="_is_buy_life_item_enabled",
                is_hidden="_is_buy_action_hidden",
                input_request=MenuInput(
                    prompt="eledificio-buy-life-item-prompt",
                    options="_buyable_life_item_options",
                    option_label="_life_item_option_label",
                    bot_select="_buy_life_item_bot_select",
                ),
                show_in_actions_menu=False,
            )
        )
        turn_set.add(
            Action(
                id="sell_life_item",
                label=Localization.get(locale, "eledificio-sell-life-item"),
                handler="_action_sell_life_item",
                is_enabled="_is_sell_life_item_enabled",
                is_hidden="_is_buy_action_hidden",
                input_request=MenuInput(
                    prompt="eledificio-sell-life-item-prompt",
                    options="_sellable_life_item_options",
                    option_label="_life_item_option_label",
                ),
                show_in_actions_menu=False,
            )
        )
        turn_set.add(
            Action(
                id="buy_shield",
                label=Localization.get(locale, "eledificio-buy-shield"),
                handler="_action_buy_shield",
                is_enabled="_is_buy_shield_enabled",
                is_hidden="_is_buy_action_hidden",
                input_request=MenuInput(
                    prompt="eledificio-buy-shield-prompt",
                    options="_buyable_shield_options",
                    option_label="_shield_option_label",
                    bot_select="_buy_shield_bot_select",
                ),
                show_in_actions_menu=False,
            )
        )
        turn_set.add(
            Action(
                id="sell_shield",
                label=Localization.get(locale, "eledificio-sell-shield"),
                handler="_action_sell_shield",
                is_enabled="_is_sell_shield_enabled",
                is_hidden="_is_buy_action_hidden",
                input_request=MenuInput(
                    prompt="eledificio-sell-shield-prompt",
                    options="_sellable_shield_options",
                    option_label="_shield_option_label",
                ),
                show_in_actions_menu=False,
            )
        )
        turn_set.add(
            Action(
                id="submit_buy",
                label=Localization.get(locale, "eledificio-submit-buy"),
                handler="_action_submit_buy",
                is_enabled="_is_submit_buy_enabled",
                is_hidden="_is_buy_action_hidden",
                show_in_actions_menu=False,
            )
        )

    def _auto_buy_for_bots(self) -> None:
        for player in self._active_edificio_players():
            if player.is_bot and not player.buy_locked:
                self._auto_fill_buy(player)

    def _auto_fill_buy(self, player: EdificioPlayer) -> None:
        reserve = self.BOT_BUY_RESERVE

        # A weapon beyond the free knife, roughly half the time if affordable.
        weapon_ids = list(self._PURCHASABLE_WEAPON_IDS)
        random.shuffle(weapon_ids)
        for weapon_id in weapon_ids:
            weapon = loadout.WEAPONS_BY_ID[weapon_id]
            if (
                player.wallet.balance - weapon.price >= reserve
                and random.random() < 0.5
            ):
                player.wallet.spend(weapon.price)
                player.owned_weapon_ids.append(weapon_id)
                break  # one ranged weapon is plenty for a bot to manage

        # Armor: buy the best one it can comfortably afford, if any.
        affordable_armors = [
            armor
            for armor in sorted(loadout.ARMORS, key=lambda item: -item.price)
            if player.wallet.balance - armor.price >= reserve
        ]
        if affordable_armors and random.random() < 0.6:
            armor = affordable_armors[0]
            player.wallet.spend(armor.price)
            player.owned_armor_id = armor.id

        # A shield, sometimes.
        for shield in loadout.SHIELDS:
            if (
                player.wallet.balance - shield.price >= reserve
                and random.random() < 0.3
            ):
                player.wallet.spend(shield.price)
                player.owned_shields[shield.id] = (
                    player.owned_shields.get(shield.id, 0) + 1
                )

        # A life item, sometimes.
        for item in loadout.LIFE_ITEMS:
            if (
                player.wallet.balance - item.price >= reserve
                and random.random() < 0.3
            ):
                player.wallet.spend(item.price)
                player.owned_life_items[item.id] = (
                    player.owned_life_items.get(item.id, 0) + 1
                )

        # A couple of explosives, sometimes.
        for explosive in loadout.EXPLOSIVES:
            if (
                player.wallet.balance - explosive.price >= reserve
                and random.random() < 0.25
            ):
                player.wallet.spend(explosive.price)
                player.owned_explosives[explosive.id] = (
                    player.owned_explosives.get(explosive.id, 0) + 1
                )

        # An amulet, sometimes (respecting the equip cap).
        amulet_ids = [amulet.id for amulet in loadout.AMULETS]
        random.shuffle(amulet_ids)
        for amulet_id in amulet_ids:
            if len(player.equipped_amulet_ids) >= loadout.MAX_EQUIPPED_AMULETS:
                break
            amulet = loadout.AMULETS_BY_ID[amulet_id]
            if (
                player.wallet.balance - amulet.price >= reserve
                and random.random() < 0.2
            ):
                player.wallet.spend(amulet.price)
                player.equipped_amulet_ids.append(amulet_id)

        # A long-duration potion, sometimes.
        potion_ids = [potion.id for potion in loadout.POTIONS]
        random.shuffle(potion_ids)
        for potion_id in potion_ids:
            potion = loadout.POTIONS_BY_ID[potion_id]
            if (
                player.wallet.balance - potion.price >= reserve
                and random.random() < 0.2
            ):
                player.wallet.spend(potion.price)
                player.owned_potions.append(potion_id)

        player.buy_locked = True
        self.broadcast_l("eledificio-buy-locked", buffer="game", player=player.name)

    # ==================================================================
    # Combat phase (PHASE_COMBAT) — turn-based duel. Weapons, explosives,
    # life items, shields, armor, status cards and long-duration potions
    # are all live here.
    # ==================================================================

    def _start_combat_turn(self) -> None:
        self.round += 1
        player = self.current_player
        if not isinstance(player, EdificioPlayer):
            return
        player.free_actions_used_this_turn = 0
        for weapon_id in list(player.weapon_lockouts):
            if player.weapon_lockouts[weapon_id] > 0:
                player.weapon_lockouts[weapon_id] -= 1
        for shield_id in list(player.shield_cooldowns):
            if player.shield_cooldowns[shield_id] > 0:
                player.shield_cooldowns[shield_id] -= 1
        if player.owned_armor_id:
            player.armor_turns_equipped += 1
        combat.tick_card_locks(player.card_lockouts)
        self._tick_card_effects(player)
        if self._check_combat_winner():
            return
        self._trigger_turn_start_amulets(player)
        self.broadcast_l(
            "eledificio-combat-turn-start", buffer="game", player=player.name
        )
        if player.is_bot:
            BotHelper.jolt_bot(player, ticks=random.randint(8, 20))
        self.refresh_menus()

    def _end_combat_turn(self) -> None:
        self.advance_turn(announce=False)
        self._start_combat_turn()

    def _combat_action_disabled_reason(self, player: Player) -> str | None:
        if self.status != "playing":
            return "action-not-playing"
        if player.is_spectator:
            return "action-spectator"
        if self.phase != state.PHASE_COMBAT:
            return "action-not-available"
        if self.current_player != player:
            return "action-not-your-turn"
        return None

    def _is_combat_action_hidden(self, player: Player) -> Visibility:
        if self.status != "playing" or player.is_spectator:
            return Visibility.HIDDEN
        if self.phase != state.PHASE_COMBAT:
            return Visibility.HIDDEN
        return Visibility.VISIBLE

    def _can_use_free_action(self, player: EdificioPlayer) -> bool:
        return player.free_actions_used_this_turn < state.MAX_FREE_ACTIONS_PER_TURN

    def _consume_free_action(self, player: EdificioPlayer) -> None:
        player.free_actions_used_this_turn += 1

    def _apply_healing(self, player: EdificioPlayer, amount: int) -> None:
        if amount <= 0:
            return
        player.health = min(player.max_health, player.health + amount)
        # Keep the health-state machine in sync (silently) so a later drop
        # narrates correctly relative to where health actually is now.
        player.last_health_state = combat.health_state_for(
            player.health, player.max_health
        )

    # ------------------------------------------------------------------
    # Status-card and long-duration-potion effects: generic bookkeeping.
    # Stored on whoever's stats/actions they modify, whether self-cast or
    # cast on them by the rival — "what's affecting me right now" is
    # always a plain scan of one list.
    # ------------------------------------------------------------------

    #: Synthetic sub-effect ids for Caos's two non-damage outcomes and
    #: Furia Final's opening burst — not real cards, just markers reused by
    #: the one-shot attack-modifier queue below.
    CHAOS_BUFF_EFFECT_ID = "eledificio_chaos_buff_rival"
    CHAOS_DEBUFF_EFFECT_ID = "eledificio_chaos_debuff_rival"
    FINAL_FURY_BUFF_ID = "eledificio_final_fury_buff"

    #: Effects consumed by an event (an attack, a death check) rather than
    #: by the passage of turns — never touched by _tick_card_effects.
    _ONE_SHOT_EFFECT_IDS = frozenset(
        {
            cards.CARD_STUNNING_PARASITE.id,
            cards.CARD_VAMPIRE.id,
            cards.CARD_HELP.id,
            cards.CARD_LIFE_STEAL.id,
            cards.CARD_SECOND_CHANCE.id,
            CHAOS_BUFF_EFFECT_ID,
            CHAOS_DEBUFF_EFFECT_ID,
            FINAL_FURY_BUFF_ID,
        }
    )
    #: Effects that stop changing once fully ramped up but never expire —
    #: Furia Final's penalty is permanent for the rest of the duel.
    _PERMANENT_AFTER_RAMP_IDS = frozenset({loadout.POTION_FINAL_FURY.id})

    def _tick_card_effects(self, player: EdificioPlayer) -> None:
        """Advance every recurring effect on ``player`` by one of their own
        turns; apply any per-tick logic; drop what's fully expired.

        An effect created with ``turns_remaining=N`` stays present (and its
        per-tick logic keeps firing) for exactly N of this player's own
        turns, only disappearing starting their turn N+1 — the presence
        check below happens *before* decrementing, precisely so the turn
        that brings the counter to 0 still counts as one of the N.
        """
        remaining: list[combat.ActiveCardEffect] = []
        for effect in player.active_card_effects:
            if effect.card_id in self._ONE_SHOT_EFFECT_IDS:
                remaining.append(effect)
                continue
            if effect.card_id in self._PERMANENT_AFTER_RAMP_IDS:
                if effect.stacks < 3:
                    effect.stacks += 1
                remaining.append(effect)
                continue
            if effect.turns_remaining <= 0:
                continue  # already exhausted from a previous tick
            if effect.card_id == cards.CARD_BLOODTHIRSTY.id:
                effect.stacks += random.randint(1, 3)
            elif effect.card_id == cards.CARD_VAMPIRIC_WEAR.id:
                effect.stacks += 1
            elif effect.card_id == cards.CARD_TOXIC_ECHO.id:
                self._apply_damage(player, 2)
            remaining.append(effect)  # present for this turn's action
            effect.turns_remaining -= 1
        player.active_card_effects = remaining

    def _attack_modifier_for(self, player: EdificioPlayer) -> int:
        """Sum of every recurring effect currently modifying this player's
        outgoing attack damage (self-buffs and rival-cast debuffs alike).
        """
        total = 0
        for effect in player.active_card_effects:
            if effect.card_id == cards.CARD_SUPERFURY.id:
                total += 3
            elif effect.card_id == cards.CARD_BLOODTHIRSTY.id:
                total += effect.stacks
            elif effect.card_id == cards.CARD_DEVOURING_PARASITE.id:
                total -= 2
            elif effect.card_id == cards.CARD_VAMPIRIC_WEAR.id:
                total -= effect.stacks
            elif effect.card_id == cards.CARD_TOXIC_ECHO.id:
                total -= 2
            elif effect.card_id == loadout.POTION_ADRENALINE.id:
                total += 3
            elif effect.card_id == loadout.POTION_FINAL_FURY.id:
                total -= effect.stacks
        return total

    def _consume_one_shot_attack_modifiers(self, player: EdificioPlayer) -> int:
        """Resolve and remove any one-shot next-attack bonus/penalty
        currently sitting on ``player`` (Vampiro, Parásito Aturdidor, Ayuda,
        Caos's buff/debuff, Furia Final's opening +10).
        """
        delta = 0
        to_remove = []
        for effect in player.active_card_effects:
            if effect.card_id == cards.CARD_VAMPIRE.id:
                delta -= random.randint(4, 5)
                to_remove.append(effect)
            elif effect.card_id == cards.CARD_STUNNING_PARASITE.id:
                delta -= 5
                to_remove.append(effect)
            elif effect.card_id == cards.CARD_HELP.id:
                delta += 4
                to_remove.append(effect)
            elif effect.card_id == self.CHAOS_BUFF_EFFECT_ID:
                delta += 10
                to_remove.append(effect)
            elif effect.card_id == self.CHAOS_DEBUFF_EFFECT_ID:
                delta -= 10
                to_remove.append(effect)
            elif effect.card_id == self.FINAL_FURY_BUFF_ID:
                delta += 10
                to_remove.append(effect)
        for effect in to_remove:
            player.active_card_effects.remove(effect)
        return delta

    def _consume_life_steal(self, player: EdificioPlayer) -> bool:
        found = next(
            (
                effect
                for effect in player.active_card_effects
                if effect.card_id == cards.CARD_LIFE_STEAL.id
            ),
            None,
        )
        if found is None:
            return False
        player.active_card_effects.remove(found)
        return True

    def _deal_combat_damage(
        self, attacker: EdificioPlayer, target: EdificioPlayer, amount: int
    ) -> None:
        """Apply damage from one combatant to the other, honoring Vínculo
        de Dolor's reflection if the target has it active.
        """
        self._apply_damage(target, amount)
        if any(
            effect.card_id == cards.CARD_PAIN_BOND.id
            for effect in target.active_card_effects
        ):
            reflected = amount // 2
            if reflected > 0:
                self.broadcast_l(
                    "eledificio-pain-bond-reflected",
                    buffer="game",
                    attacker=attacker.name,
                    amount=reflected,
                )
                self._apply_damage(attacker, reflected)

    def _is_disarmed(self, player: EdificioPlayer) -> bool:
        return any(
            effect.card_id == cards.CARD_DISARM.id
            for effect in player.active_card_effects
        )

    def _is_frozen(self, player: EdificioPlayer) -> bool:
        return any(
            effect.card_id == cards.CARD_FREEZE.id
            for effect in player.active_card_effects
        )

    def _fortune_bonus_chance(self, player: EdificioPlayer) -> float:
        return (
            0.05
            if any(
                effect.card_id == loadout.POTION_FORTUNE.id
                for effect in player.active_card_effects
            )
            else 0.0
        )

    def _consume_attack_amulet_bonus(
        self, player: EdificioPlayer, rng: random.Random
    ) -> int:
        """Roll the attacker's Puño Cargado, if equipped."""
        if loadout.AMULET_CHARGED_FIST.id not in player.equipped_amulet_ids:
            return 0
        amulet = loadout.AMULET_CHARGED_FIST
        chance = min(
            1.0, amulet.activation_chance + self._fortune_bonus_chance(player)
        )
        if rng.random() < chance:
            bonus = rng.randint(10, 15)
            self.broadcast_l(
                "eledificio-amulet-triggered",
                buffer="game",
                player=player.name,
                amulet=amulet.name,
            )
            return bonus
        return 0

    def _apply_liquid_armor_bonus(
        self, defender_player: EdificioPlayer, result: combat.MitigationResult
    ) -> int:
        """Return final_damage reduced further by Blindaje Líquido's +5%
        armor absorption, if the defender has it active and owns armor.
        """
        if not defender_player.owned_armor_id:
            return result.final_damage
        if not any(
            effect.card_id == loadout.POTION_LIQUID_ARMOR.id
            for effect in defender_player.active_card_effects
        ):
            return result.final_damage
        pre_armor_value = result.final_damage + result.armor_absorbed
        extra_absorbed = round(pre_armor_value * 0.05)
        return max(0, result.final_damage - extra_absorbed)

    def _trigger_turn_start_amulets(self, player: EdificioPlayer) -> None:
        """Núcleo Vital rolls once at the start of its owner's own turn."""
        if loadout.AMULET_VITAL_CORE.id not in player.equipped_amulet_ids:
            return
        amulet = loadout.AMULET_VITAL_CORE
        chance = min(
            1.0, amulet.activation_chance + self._fortune_bonus_chance(player)
        )
        if random.random() < chance:
            self._apply_healing(player, 15)
            self.broadcast_l(
                "eledificio-amulet-triggered",
                buffer="game",
                player=player.name,
                amulet=amulet.name,
            )

    # ------------------------------------------------------------------
    # Bridges to the already-tested combat.py mitigation pipeline: build a
    # Defender snapshot from a player's flattened fields, then write any
    # mutations (shield hits spent, armor wear) back afterward.
    # ------------------------------------------------------------------

    def _defender_for(self, player: EdificioPlayer) -> combat.Defender:
        shield = None
        if player.active_shield_id and player.active_shield_hits_remaining > 0:
            shield = combat.ActiveShield(
                shield_id=player.active_shield_id,
                hits_remaining=player.active_shield_hits_remaining,
            )
        armor = None
        if player.owned_armor_id:
            armor = combat.EquippedArmor(
                armor_id=player.owned_armor_id,
                turns_equipped=player.armor_turns_equipped,
                accelerated_wear_events=player.armor_accelerated_wear_events,
            )
        return combat.Defender(
            shield=shield,
            equipped_amulet_ids=tuple(player.equipped_amulet_ids),
            equipped_armor=armor,
        )

    def _write_back_defender(
        self,
        defender_player: EdificioPlayer,
        defender: combat.Defender,
        result: combat.MitigationResult,
    ) -> None:
        if defender.shield is not None:
            defender_player.active_shield_hits_remaining = (
                defender.shield.hits_remaining
            )
            if defender.shield.hits_remaining <= 0:
                spent_id = defender.shield.shield_id
                defender_player.active_shield_id = ""
                defender_player.active_shield_hits_remaining = 0
                shield_def = loadout.SHIELDS_BY_ID.get(spent_id)
                if shield_def:
                    defender_player.shield_cooldowns[spent_id] = (
                        shield_def.cooldown_turns
                    )
        if defender.equipped_armor is not None:
            if result.crit_reduced_by_armor:
                defender.equipped_armor.register_blocked_attack()
            defender_player.armor_accelerated_wear_events = (
                defender.equipped_armor.accelerated_wear_events
            )

    # ---- Reload (free action) --------------------------------------

    def _reloadable_weapon_options(self, player: Player) -> list[str]:
        if not isinstance(player, EdificioPlayer) or not self._can_use_free_action(
            player
        ):
            return []
        options = []
        for weapon_id in player.owned_weapon_ids:
            weapon = loadout.WEAPONS_BY_ID[weapon_id]
            if weapon.reload_cost <= 0:
                continue  # the knife never needs reloading
            if player.weapon_ammo_loaded.get(weapon_id, True):
                continue  # already loaded
            if player.weapon_lockouts.get(weapon_id, 0) > 0:
                continue  # still locked out from its last burst
            if not player.wallet.can_afford(weapon.reload_cost):
                continue
            options.append(weapon_id)
        return options

    def _reload_option_label(self, player: Player, weapon_id: str) -> str:
        weapon = loadout.WEAPONS_BY_ID[weapon_id]
        return Localization.get(
            self._player_locale(player),
            "eledificio-reload-option",
            name=weapon.name,
            cost=weapon.reload_cost,
        )

    def _is_reload_enabled(self, player: Player) -> str | None:
        base = self._combat_action_disabled_reason(player)
        if base:
            return base
        if not self._reloadable_weapon_options(player):
            return "eledificio-no-reload-available"
        return None

    def _action_reload(self, player: Player, weapon_id: str, action_id: str) -> None:
        if not isinstance(player, EdificioPlayer):
            return
        if weapon_id not in self._reloadable_weapon_options(player):
            return
        weapon = loadout.WEAPONS_BY_ID[weapon_id]
        player.wallet.spend(weapon.reload_cost)
        player.weapon_ammo_loaded[weapon_id] = True
        self._consume_free_action(player)
        self.broadcast_l(
            "eledificio-reloaded", buffer="game", player=player.name, weapon=weapon.name
        )
        self.refresh_menus()

    def _reload_bot_select(self, player: Player, options: list[str]) -> str | None:
        return random.choice(options) if options else None

    # ---- Drink a life item (free action) ---------------------------

    def _drinkable_life_item_options(self, player: Player) -> list[str]:
        if not isinstance(player, EdificioPlayer) or not self._can_use_free_action(
            player
        ):
            return []
        if self._is_disarmed(player):
            return []
        return [
            item_id for item_id, count in player.owned_life_items.items() if count > 0
        ]

    def _life_item_drink_label(self, player: Player, item_id: str) -> str:
        item = loadout.LIFE_ITEMS_BY_ID[item_id]
        return Localization.get(
            self._player_locale(player),
            "eledificio-drink-life-item-option",
            name=item.name,
            heal=item.heal_amount,
        )

    def _is_drink_life_item_enabled(self, player: Player) -> str | None:
        base = self._combat_action_disabled_reason(player)
        if base:
            return base
        if not self._drinkable_life_item_options(player):
            return "eledificio-no-life-items"
        return None

    def _action_drink_life_item(
        self, player: Player, item_id: str, action_id: str
    ) -> None:
        if not isinstance(player, EdificioPlayer):
            return
        if item_id not in self._drinkable_life_item_options(player):
            return
        item = loadout.LIFE_ITEMS_BY_ID[item_id]
        owned = player.owned_life_items.get(item_id, 0)
        player.owned_life_items[item_id] = owned - 1
        if player.owned_life_items[item_id] <= 0:
            del player.owned_life_items[item_id]
        self._apply_healing(player, item.heal_amount)
        self._consume_free_action(player)
        self.broadcast_l(
            "eledificio-drank-life-item",
            buffer="game",
            player=player.name,
            item=item.name,
            heal=item.heal_amount,
        )
        self.refresh_menus()

    def _drink_life_item_bot_select(
        self, player: Player, options: list[str]
    ) -> str | None:
        return random.choice(options) if options else None

    # ---- Activate a shield (free action) ---------------------------

    def _activatable_shield_options(self, player: Player) -> list[str]:
        if not isinstance(player, EdificioPlayer) or not self._can_use_free_action(
            player
        ):
            return []
        if self._is_disarmed(player):
            return []
        if player.active_shield_id:
            return []  # only one active shield at a time
        return [
            shield_id
            for shield_id, count in player.owned_shields.items()
            if count > 0 and player.shield_cooldowns.get(shield_id, 0) <= 0
        ]

    def _shield_activate_label(self, player: Player, shield_id: str) -> str:
        shield = loadout.SHIELDS_BY_ID[shield_id]
        return Localization.get(
            self._player_locale(player),
            "eledificio-activate-shield-option",
            name=shield.name,
        )

    def _is_activate_shield_enabled(self, player: Player) -> str | None:
        base = self._combat_action_disabled_reason(player)
        if base:
            return base
        if not self._activatable_shield_options(player):
            return "eledificio-no-shields-available"
        return None

    def _action_activate_shield(
        self, player: Player, shield_id: str, action_id: str
    ) -> None:
        if not isinstance(player, EdificioPlayer):
            return
        if shield_id not in self._activatable_shield_options(player):
            return
        shield = loadout.SHIELDS_BY_ID[shield_id]
        owned = player.owned_shields.get(shield_id, 0)
        player.owned_shields[shield_id] = owned - 1
        if player.owned_shields[shield_id] <= 0:
            del player.owned_shields[shield_id]
        player.active_shield_id = shield_id
        player.active_shield_hits_remaining = shield.hits_absorbed
        self._consume_free_action(player)
        self.broadcast_l(
            "eledificio-shield-activated",
            buffer="game",
            player=player.name,
            shield=shield.name,
        )
        self.refresh_menus()

    def _activate_shield_bot_select(
        self, player: Player, options: list[str]
    ) -> str | None:
        return random.choice(options) if options else None

    # ---- Attack with a weapon (definitive action) ------------------

    def _attackable_weapon_options(self, player: Player) -> list[str]:
        if not isinstance(player, EdificioPlayer):
            return []
        frozen = self._is_frozen(player)
        options = []
        for weapon_id in player.owned_weapon_ids:
            weapon = loadout.WEAPONS_BY_ID[weapon_id]
            if frozen and weapon.reload_cost > 0:
                continue  # Congelación: only the knife works this turn
            if player.weapon_lockouts.get(weapon_id, 0) > 0:
                continue
            if not player.weapon_ammo_loaded.get(weapon_id, True):
                continue
            options.append(weapon_id)
        return options

    def _attack_option_label(self, player: Player, weapon_id: str) -> str:
        weapon = loadout.WEAPONS_BY_ID[weapon_id]
        return Localization.get(
            self._player_locale(player),
            "eledificio-attack-option",
            name=weapon.name,
            damage=weapon.total_damage,
        )

    def _is_attack_enabled(self, player: Player) -> str | None:
        base = self._combat_action_disabled_reason(player)
        if base:
            return base
        if not self._attackable_weapon_options(player):
            return "eledificio-no-weapons-available"
        return None

    def _action_attack(self, player: Player, weapon_id: str, action_id: str) -> None:
        if not isinstance(player, EdificioPlayer):
            return
        if player is not self.current_player:
            return
        if weapon_id not in self._attackable_weapon_options(player):
            return
        opponent = self._opponent_of(player)
        if opponent is None:
            return
        weapon = loadout.WEAPONS_BY_ID[weapon_id]

        rng = random.Random()
        burst = combat.resolve_weapon_burst(
            weapon, rng, crit_chance_bonus=self._fortune_bonus_chance(player)
        )
        is_crit = burst.crit_bullets > 0

        modifier = self._attack_modifier_for(player)
        modifier += self._consume_one_shot_attack_modifiers(player)
        modifier += self._consume_attack_amulet_bonus(player, rng)
        raw_damage = max(0, burst.total_damage + modifier)

        defender = self._defender_for(opponent)
        result = combat.apply_mitigation(
            raw_damage,
            is_crit=is_crit,
            defender=defender,
            rng=rng,
            amulet_chance_bonus=self._fortune_bonus_chance(opponent),
        )
        self._write_back_defender(opponent, defender, result)
        final_damage = self._apply_liquid_armor_bonus(opponent, result)

        player.weapon_ammo_loaded[weapon_id] = False
        player.weapon_lockouts[weapon_id] = weapon.reload_lockout_turns
        if weapon.reload_cost <= 0:
            # Unlimited-use weapons (the Cuchillo Fantasma) never run dry and
            # never lock out — there is nothing to reload, ever.
            player.weapon_ammo_loaded[weapon_id] = True
            player.weapon_lockouts[weapon_id] = 0

        life_steal_active = self._consume_life_steal(player)

        self.broadcast_l(
            "eledificio-attack-result",
            buffer="game",
            attacker=player.name,
            weapon=weapon.name,
            target=opponent.name,
            damage=final_damage,
        )
        if is_crit:
            self.broadcast_l("eledificio-critical-hit", buffer="game")
        self._deal_combat_damage(player, opponent, final_damage)
        if life_steal_active and final_damage > 0:
            heal = final_damage // 2
            if heal > 0:
                self._apply_healing(player, heal)
                self.broadcast_l(
                    "eledificio-life-steal",
                    buffer="game",
                    player=player.name,
                    heal=heal,
                )
        if self._check_combat_winner():
            return
        self._end_combat_turn()

    def _attack_bot_select(self, player: Player, options: list[str]) -> str | None:
        return random.choice(options) if options else None

    # ---- Throw an explosive (definitive action) --------------------

    def _throwable_explosive_options(self, player: Player) -> list[str]:
        if not isinstance(player, EdificioPlayer):
            return []
        return [
            explosive_id
            for explosive_id, count in player.owned_explosives.items()
            if count > 0
        ]

    def _explosive_throw_label(self, player: Player, explosive_id: str) -> str:
        explosive = loadout.EXPLOSIVES_BY_ID[explosive_id]
        return Localization.get(
            self._player_locale(player),
            "eledificio-throw-option",
            name=explosive.name,
        )

    def _is_throw_explosive_enabled(self, player: Player) -> str | None:
        base = self._combat_action_disabled_reason(player)
        if base:
            return base
        if not self._throwable_explosive_options(player):
            return "eledificio-no-explosives"
        return None

    def _action_throw_explosive(
        self, player: Player, explosive_id: str, action_id: str
    ) -> None:
        if not isinstance(player, EdificioPlayer):
            return
        if player is not self.current_player:
            return
        if explosive_id not in self._throwable_explosive_options(player):
            return
        opponent = self._opponent_of(player)
        if opponent is None:
            return
        explosive = loadout.EXPLOSIVES_BY_ID[explosive_id]

        owned = player.owned_explosives.get(explosive_id, 0)
        player.owned_explosives[explosive_id] = owned - 1
        if player.owned_explosives[explosive_id] <= 0:
            del player.owned_explosives[explosive_id]

        rng = random.Random()
        raw_damage = rng.randint(explosive.min_damage, explosive.max_damage)

        if explosive.hits_both_players:
            # Design: "hace 5 a ambos jugadores por igual" — flat and
            # unmitigated for both sides, including the thrower.
            self.broadcast_l(
                "eledificio-explosive-result-both",
                buffer="game",
                thrower=player.name,
                name=explosive.name,
                damage=raw_damage,
            )
            self._apply_damage(opponent, raw_damage)
            self._apply_damage(player, raw_damage)
        else:
            defender = self._defender_for(opponent)
            result = combat.apply_mitigation(
                raw_damage,
                is_crit=False,
                defender=defender,
                rng=rng,
                amulet_chance_bonus=self._fortune_bonus_chance(opponent),
            )
            self._write_back_defender(opponent, defender, result)
            final_damage = self._apply_liquid_armor_bonus(opponent, result)
            self.broadcast_l(
                "eledificio-explosive-result",
                buffer="game",
                thrower=player.name,
                name=explosive.name,
                target=opponent.name,
                damage=final_damage,
            )
            self._deal_combat_damage(player, opponent, final_damage)

        if self._check_combat_winner():
            return
        self._end_combat_turn()

    def _throw_explosive_bot_select(
        self, player: Player, options: list[str]
    ) -> str | None:
        return random.choice(options) if options else None

    # ---- Play a status card (definitive action) ---------------------

    def _playable_card_options(self, player: Player) -> list[str]:
        if not isinstance(player, EdificioPlayer):
            return []
        if self._is_disarmed(player):
            return []
        return [
            card.id
            for card in cards.CARDS
            if not combat.is_card_locked(card.id, player.card_lockouts)
        ]

    def _card_option_label(self, player: Player, card_id: str) -> str:
        card = cards.CARDS_BY_ID[card_id]
        return Localization.get(
            self._player_locale(player),
            "eledificio-card-option",
            name=card.name,
            description=card.description,
        )

    def _is_play_card_enabled(self, player: Player) -> str | None:
        base = self._combat_action_disabled_reason(player)
        if base:
            return base
        if not self._playable_card_options(player):
            return "eledificio-no-cards-available"
        return None

    def _play_card_bot_select(self, player: Player, options: list[str]) -> str | None:
        return random.choice(options) if options else None

    def _action_play_card(self, player: Player, card_id: str, action_id: str) -> None:
        if not isinstance(player, EdificioPlayer):
            return
        if player is not self.current_player:
            return
        if card_id not in self._playable_card_options(player):
            return
        opponent = self._opponent_of(player)
        if opponent is None:
            return
        card = cards.CARDS_BY_ID[card_id]
        combat.start_card_lock(card_id, player.card_lockouts)

        if card_id == cards.CARD_DEVOURING_PARASITE.id:
            opponent.active_card_effects.append(
                combat.ActiveCardEffect(card_id=card_id, turns_remaining=3)
            )
        elif card_id == cards.CARD_STUNNING_PARASITE.id:
            opponent.active_card_effects.append(
                combat.ActiveCardEffect(card_id=card_id, turns_remaining=1)
            )
        elif card_id == cards.CARD_VAMPIRE.id:
            opponent.active_card_effects.append(
                combat.ActiveCardEffect(card_id=card_id, turns_remaining=1)
            )
        elif card_id == cards.CARD_SUPERFURY.id:
            player.active_card_effects.append(
                combat.ActiveCardEffect(card_id=card_id, turns_remaining=4)
            )
        elif card_id == cards.CARD_BLOODTHIRSTY.id:
            player.active_card_effects.append(
                combat.ActiveCardEffect(card_id=card_id, turns_remaining=3, stacks=0)
            )
        elif card_id == cards.CARD_VAMPIRIC_WEAR.id:
            opponent.active_card_effects.append(
                combat.ActiveCardEffect(card_id=card_id, turns_remaining=6, stacks=0)
            )
        elif card_id == cards.CARD_HELP.id:
            player.active_card_effects.append(
                combat.ActiveCardEffect(card_id=card_id, turns_remaining=1)
            )
        elif card_id == cards.CARD_CHAOS.id:
            outcome = random.choice(("buff", "debuff", "mutual"))
            if outcome == "mutual":
                self.broadcast_l("eledificio-chaos-mutual", buffer="game")
                self._apply_damage(player, 3)
                self._apply_damage(opponent, 3)
            elif outcome == "buff":
                self.broadcast_l(
                    "eledificio-chaos-buff-rival",
                    buffer="game",
                    player=player.name,
                    opponent=opponent.name,
                )
                opponent.active_card_effects.append(
                    combat.ActiveCardEffect(
                        card_id=self.CHAOS_BUFF_EFFECT_ID, turns_remaining=1
                    )
                )
            else:
                self.broadcast_l(
                    "eledificio-chaos-debuff-rival",
                    buffer="game",
                    player=player.name,
                    opponent=opponent.name,
                )
                opponent.active_card_effects.append(
                    combat.ActiveCardEffect(
                        card_id=self.CHAOS_DEBUFF_EFFECT_ID, turns_remaining=1
                    )
                )
        elif card_id == cards.CARD_PAIN_BOND.id:
            player.active_card_effects.append(
                combat.ActiveCardEffect(card_id=card_id, turns_remaining=2)
            )
        elif card_id == cards.CARD_LIFE_STEAL.id:
            player.active_card_effects.append(
                combat.ActiveCardEffect(card_id=card_id, turns_remaining=1)
            )
        elif card_id == cards.CARD_FREEZE.id:
            opponent.active_card_effects.append(
                combat.ActiveCardEffect(card_id=card_id, turns_remaining=1)
            )
        elif card_id == cards.CARD_TOXIC_ECHO.id:
            opponent.active_card_effects.append(
                combat.ActiveCardEffect(card_id=card_id, turns_remaining=3)
            )
        elif card_id == cards.CARD_SECOND_CHANCE.id:
            player.active_card_effects.append(
                combat.ActiveCardEffect(card_id=card_id, turns_remaining=1)
            )
        elif card_id == cards.CARD_DISARM.id:
            opponent.active_card_effects.append(
                combat.ActiveCardEffect(card_id=card_id, turns_remaining=1)
            )

        self.broadcast_l(
            "eledificio-card-played", buffer="game", player=player.name, card=card.name
        )
        if self._check_combat_winner():
            return
        self._end_combat_turn()

    # ---- Drink a long-duration potion (free action) ------------------

    def _drinkable_potion_options(self, player: Player) -> list[str]:
        if not isinstance(player, EdificioPlayer) or not self._can_use_free_action(
            player
        ):
            return []
        if self._is_disarmed(player):
            return []
        return list(player.owned_potions)

    def _potion_drink_label(self, player: Player, potion_id: str) -> str:
        potion = loadout.POTIONS_BY_ID[potion_id]
        return Localization.get(
            self._player_locale(player),
            "eledificio-drink-potion-option",
            name=potion.name,
            description=potion.description,
        )

    def _is_drink_potion_enabled(self, player: Player) -> str | None:
        base = self._combat_action_disabled_reason(player)
        if base:
            return base
        if not self._drinkable_potion_options(player):
            return "eledificio-no-potions"
        return None

    def _drink_potion_bot_select(
        self, player: Player, options: list[str]
    ) -> str | None:
        return random.choice(options) if options else None

    def _action_drink_potion(
        self, player: Player, potion_id: str, action_id: str
    ) -> None:
        if not isinstance(player, EdificioPlayer):
            return
        if potion_id not in self._drinkable_potion_options(player):
            return
        player.owned_potions.remove(potion_id)
        self._consume_free_action(player)

        if potion_id == loadout.POTION_ADRENALINE.id:
            player.active_card_effects.append(
                combat.ActiveCardEffect(card_id=potion_id, turns_remaining=18)
            )
        elif potion_id == loadout.POTION_LIQUID_ARMOR.id:
            player.active_card_effects.append(
                combat.ActiveCardEffect(card_id=potion_id, turns_remaining=15)
            )
        elif potion_id == loadout.POTION_FORTUNE.id:
            player.active_card_effects.append(
                combat.ActiveCardEffect(card_id=potion_id, turns_remaining=20)
            )
        elif potion_id == loadout.POTION_FINAL_FURY.id:
            player.active_card_effects.append(
                combat.ActiveCardEffect(
                    card_id=self.FINAL_FURY_BUFF_ID, turns_remaining=1
                )
            )
            player.active_card_effects.append(
                combat.ActiveCardEffect(card_id=potion_id, turns_remaining=3, stacks=0)
            )

        self.broadcast_l(
            "eledificio-drank-potion",
            buffer="game",
            player=player.name,
            potion=loadout.POTIONS_BY_ID[potion_id].name,
        )
        self.refresh_menus()

    # ---- Win condition and match end --------------------------------

    def _check_combat_winner(self) -> bool:
        fallen = [
            player
            for player in self._active_edificio_players()
            if player.health <= 0
        ]
        if not fallen:
            return False
        loser = fallen[0]
        winner = self._opponent_of(loser)
        if winner is None:
            return False
        self._end_combat(winner, loser)
        return True

    def _end_combat(self, winner: EdificioPlayer, loser: EdificioPlayer) -> None:
        self.combat_winner_id = winner.id
        self.combat_end_reason = state.WIN_KNOCKOUT
        self.phase = state.PHASE_ENDED
        self.status = "finished"
        self._sync_table_status()
        self.broadcast_l(
            "eledificio-combat-winner",
            buffer="game",
            winner=winner.name,
            loser=loser.name,
        )
        self.refresh_menus()
        self.finish_game()

    def build_game_result(self) -> GameResult:
        players = self._active_edificio_players()
        winner = self.get_player_by_id(self.combat_winner_id)
        return GameResult(
            game_type=self.get_type(),
            timestamp=datetime.now().isoformat(),
            duration_ticks=self.sound_scheduler_tick,
            player_results=[
                PlayerResult(
                    player_id=player.id,
                    player_name=player.name,
                    is_bot=player.is_bot and not player.replaced_human,
                )
                for player in players
            ],
            custom_data={
                "winner_id": self.combat_winner_id,
                "winner_name": winner.name if winner else None,
                "end_reason": self.combat_end_reason,
                "final_health": {player.name: player.health for player in players},
                "final_money": {
                    player.name: player.wallet.balance for player in players
                },
            },
        )

    def format_end_screen(self, result: GameResult, locale: str) -> list[str]:
        lines = [Localization.get(locale, "game-final-scores")]
        winner_name = result.custom_data.get("winner_name")
        if winner_name:
            lines.append(
                Localization.get(locale, "eledificio-end-winner", player=winner_name)
            )
        else:
            lines.append(Localization.get(locale, "eledificio-end-draw"))
        return lines

    # ---- Populating the combat action set ---------------------------

    def _populate_combat_actions(
        self, turn_set: ActionSet, player: EdificioPlayer, locale: str
    ) -> None:
        turn_set.add(
            Action(
                id="reload_weapon",
                label=Localization.get(locale, "eledificio-reload"),
                handler="_action_reload",
                is_enabled="_is_reload_enabled",
                is_hidden="_is_combat_action_hidden",
                input_request=MenuInput(
                    prompt="eledificio-reload-prompt",
                    options="_reloadable_weapon_options",
                    option_label="_reload_option_label",
                    bot_select="_reload_bot_select",
                ),
                show_in_actions_menu=False,
            )
        )
        turn_set.add(
            Action(
                id="drink_life_item",
                label=Localization.get(locale, "eledificio-drink-life-item"),
                handler="_action_drink_life_item",
                is_enabled="_is_drink_life_item_enabled",
                is_hidden="_is_combat_action_hidden",
                input_request=MenuInput(
                    prompt="eledificio-drink-life-item-prompt",
                    options="_drinkable_life_item_options",
                    option_label="_life_item_drink_label",
                    bot_select="_drink_life_item_bot_select",
                ),
                show_in_actions_menu=False,
            )
        )
        turn_set.add(
            Action(
                id="activate_shield",
                label=Localization.get(locale, "eledificio-activate-shield"),
                handler="_action_activate_shield",
                is_enabled="_is_activate_shield_enabled",
                is_hidden="_is_combat_action_hidden",
                input_request=MenuInput(
                    prompt="eledificio-activate-shield-prompt",
                    options="_activatable_shield_options",
                    option_label="_shield_activate_label",
                    bot_select="_activate_shield_bot_select",
                ),
                show_in_actions_menu=False,
            )
        )
        turn_set.add(
            Action(
                id="attack",
                label=Localization.get(locale, "eledificio-attack"),
                handler="_action_attack",
                is_enabled="_is_attack_enabled",
                is_hidden="_is_combat_action_hidden",
                input_request=MenuInput(
                    prompt="eledificio-attack-prompt",
                    options="_attackable_weapon_options",
                    option_label="_attack_option_label",
                    bot_select="_attack_bot_select",
                ),
                show_in_actions_menu=False,
            )
        )
        turn_set.add(
            Action(
                id="throw_explosive",
                label=Localization.get(locale, "eledificio-throw-explosive"),
                handler="_action_throw_explosive",
                is_enabled="_is_throw_explosive_enabled",
                is_hidden="_is_combat_action_hidden",
                input_request=MenuInput(
                    prompt="eledificio-throw-explosive-prompt",
                    options="_throwable_explosive_options",
                    option_label="_explosive_throw_label",
                    bot_select="_throw_explosive_bot_select",
                ),
                show_in_actions_menu=False,
            )
        )
        turn_set.add(
            Action(
                id="play_card",
                label=Localization.get(locale, "eledificio-play-card"),
                handler="_action_play_card",
                is_enabled="_is_play_card_enabled",
                is_hidden="_is_combat_action_hidden",
                input_request=MenuInput(
                    prompt="eledificio-play-card-prompt",
                    options="_playable_card_options",
                    option_label="_card_option_label",
                    bot_select="_play_card_bot_select",
                ),
                show_in_actions_menu=False,
            )
        )
        turn_set.add(
            Action(
                id="drink_potion",
                label=Localization.get(locale, "eledificio-drink-potion"),
                handler="_action_drink_potion",
                is_enabled="_is_drink_potion_enabled",
                is_hidden="_is_combat_action_hidden",
                input_request=MenuInput(
                    prompt="eledificio-drink-potion-prompt",
                    options="_drinkable_potion_options",
                    option_label="_potion_drink_label",
                    bot_select="_drink_potion_bot_select",
                ),
                show_in_actions_menu=False,
            )
        )

    # ---- Bot AI for combat -------------------------------------------

    BOT_LOW_HEALTH_FRACTION = 0.4
    BOT_SHIELD_ACTIVATE_CHANCE = 0.5
    BOT_POTION_DRINK_CHANCE = 0.3
    BOT_CARD_PLAY_CHANCE = 0.2

    def _perform_bot_combat_turn(self, player: EdificioPlayer) -> None:
        # 1. Heal first if hurt and able — the strongest available potion.
        if player.health / max(1, player.max_health) <= self.BOT_LOW_HEALTH_FRACTION:
            life_options = self._drinkable_life_item_options(player)
            if life_options:
                item_id = max(
                    life_options,
                    key=lambda option: loadout.LIFE_ITEMS_BY_ID[option].heal_amount,
                )
                self._action_drink_life_item(player, item_id, "drink_life_item")

        # 2. Maybe drink a long-duration potion, if a free action remains.
        potion_options = self._drinkable_potion_options(player)
        if potion_options and random.random() < self.BOT_POTION_DRINK_CHANCE:
            self._action_drink_potion(
                player, random.choice(potion_options), "drink_potion"
            )

        # 3. Put up a shield sometimes if none is active.
        shield_options = self._activatable_shield_options(player)
        if shield_options and random.random() < self.BOT_SHIELD_ACTIVATE_CHANCE:
            self._action_activate_shield(
                player, random.choice(shield_options), "activate_shield"
            )

        # 4. Reload anything that needs it, if a free action remains.
        reload_options = self._reloadable_weapon_options(player)
        if reload_options:
            self._action_reload(player, reload_options[0], "reload_weapon")

        # 5. Definitive action: sometimes a status card instead of attacking.
        card_options = self._playable_card_options(player)
        if card_options and random.random() < self.BOT_CARD_PLAY_CHANCE:
            self._action_play_card(player, random.choice(card_options), "play_card")
            return

        attack_options = self._attackable_weapon_options(player)
        if attack_options:
            best_weapon_id = max(
                attack_options,
                key=lambda option: loadout.WEAPONS_BY_ID[option].total_damage,
            )
            self._action_attack(player, best_weapon_id, "attack")
            return

        explosive_options = self._throwable_explosive_options(player)
        if explosive_options:
            self._action_throw_explosive(
                player, random.choice(explosive_options), "throw_explosive"
            )
            return

        if card_options:
            self._action_play_card(player, random.choice(card_options), "play_card")
            return

        # Nothing usable at all this turn — still must end it.
        self._end_combat_turn()

    # ---- Detailed status (available from PHASE_LOADOUT onward) ------

    def create_standard_action_set(self, player: Player) -> ActionSet:
        action_set = super().create_standard_action_set(player)
        locale = self._player_locale(player)
        action_set.add(
            Action(
                id="check_status",
                label=Localization.get(locale, "eledificio-check-status"),
                handler="_action_check_status",
                is_enabled="_is_check_status_enabled",
                is_hidden="_is_check_status_hidden",
                include_spectators=True,
            )
        )
        return action_set

    def _is_check_status_enabled(self, player: Player) -> str | None:
        return None if self.status == "playing" else "action-not-playing"

    def _is_check_status_hidden(self, player: Player) -> Visibility:
        return Visibility.VISIBLE if self.status == "playing" else Visibility.HIDDEN

    def _status_lines(self, locale: str) -> list[MenuItem]:
        items = [
            MenuItem(
                text=Localization.get(
                    locale, "eledificio-status-phase", phase=self.phase, round=self.round
                ),
                id="phase",
            )
        ]
        for player in self._active_edificio_players():
            if player.health <= 0:
                health_text = Localization.get(
                    locale, "eledificio-status-defeated", player=player.name
                )
            else:
                health_text = Localization.get(
                    locale,
                    "eledificio-status-health",
                    player=player.name,
                    health=player.health,
                    max=player.max_health,
                )
            items.append(MenuItem(text=health_text, id=f"health:{player.id}"))
            items.append(
                MenuItem(
                    text=Localization.get(
                        locale,
                        "eledificio-status-money",
                        player=player.name,
                        amount=player.wallet.balance,
                    ),
                    id=f"money:{player.id}",
                )
            )
        return items

    def _action_check_status(self, player: Player, action_id: str) -> None:
        user = self.get_user(player)
        if not user:
            return
        self.live_status_box(
            player,
            "eledificio_status",
            lambda _player, live_user: self._status_lines(live_user.locale),
        )

    # ==================================================================
    # Shared turn-action gating (exploration phase)
    # ==================================================================

    def _turn_frozen(self) -> bool:
        """True while the current player's turn can't proceed: an encounter
        started, or a Duelo/blind-pick choice is still awaited from someone.
        """
        return bool(
            self.encounter_room_id
            or self.pending_duel_victim_id
            or self.pending_blind_pick_finder_id
        )

    def _turn_action_disabled_reason(self, player: Player) -> str | None:
        if self.status != "playing":
            return "action-not-playing"
        if player.is_spectator:
            return "action-spectator"
        if self._turn_frozen():
            return "eledificio-combat-pending"
        if self.current_player != player:
            return "action-not-your-turn"
        return None

    def _is_turn_action_hidden(self, player: Player) -> Visibility:
        if self.status != "playing" or player.is_spectator:
            return Visibility.HIDDEN
        if self.phase != state.PHASE_EXPLORE:
            return Visibility.HIDDEN
        if self._turn_frozen():
            return Visibility.HIDDEN
        return Visibility.VISIBLE

    def _end_turn(self) -> None:
        self.advance_turn(announce=False)
        self._start_turn()

    # ==================================================================
    # Turn action: explore (move + inspect)
    # ==================================================================

    def _is_explore_enabled(self, player: Player) -> str | None:
        return self._turn_action_disabled_reason(player)

    def _explore_room_options(self, player: Player) -> list[str]:
        if not isinstance(player, EdificioPlayer):
            return []
        max_distance = self._movement_range_for(player)
        return sorted(self.game_map.rooms_within(player.room_id, max_distance))

    def _explore_room_label(self, player: Player, room_id: str) -> str:
        name = self.game_map.room_names.get(room_id, room_id)
        return Localization.get(
            self._player_locale(player), "eledificio-room-name", room=name
        )

    def _explore_bot_select(self, player: Player) -> str | None:
        """Prefer a room this bot hasn't visited yet; fall back to any option."""
        if not isinstance(player, EdificioPlayer):
            return None
        options = self._explore_room_options(player)
        if not options:
            return None
        unvisited = [
            room_id
            for room_id in options
            if room_id not in player.room_last_visited_turn
        ]
        return random.choice(unvisited or options)

    def _action_explore(self, player: Player, room_id: str, action_id: str) -> None:
        if not isinstance(player, EdificioPlayer):
            return
        if player is not self.current_player or self._turn_frozen():
            return
        options = self._explore_room_options(player)
        if room_id not in options:
            return

        trap_hit = self._trap_in_room(room_id)
        if trap_hit is not None and trap_hit[1].id != player.id:
            placed_trap, owner = trap_hit
            if placed_trap.trap_id == state.TRAP_DUEL:
                self._start_duel(player, owner, placed_trap)
                return
            arrival_room_id = self._resolve_trap_trigger(player, trap_hit)
        else:
            arrival_room_id = room_id

        player.stunned_next_turn = False  # this turn's movement consumed any stun
        self._finish_landing(player, arrival_room_id)

    def _finish_landing(self, player: EdificioPlayer, arrival_room_id: str) -> None:
        """Resolve what happens once a player's movement into a room is final
        (after any trap has already been triggered and resolved).
        """
        opponent = self._opponent_of(player)

        if opponent is not None:
            opponent_here = opponent.room_id == arrival_room_id
            shadow_here = (
                opponent.shadow is not None
                and opponent.shadow.alive
                and opponent.shadow.room_id == arrival_room_id
            )

            if opponent_here and shadow_here:
                self._start_blind_pick(player, opponent, arrival_room_id)
                return
            if opponent_here:
                self._trigger_encounter(player, opponent, arrival_room_id)
                return
            if shadow_here:
                self._resolve_shadow_found_alone(player, opponent, arrival_room_id)
                # falls through — the turn still ends normally below

        trace = None
        if opponent is not None:
            trace = self._trace_for_room(opponent, arrival_room_id)

        player.room_id = arrival_room_id
        player.room_last_visited_turn[arrival_room_id] = self.round

        if trace == "recent":
            self.broadcast_l(
                "eledificio-trace-recent", buffer="game", player=player.name
            )
        elif trace == "cold":
            self.broadcast_l(
                "eledificio-trace-cold", buffer="game", player=player.name
            )

        self._claim_room_rewards(player, arrival_room_id)

        self._end_turn()

    def _trace_for_room(self, opponent: EdificioPlayer, room_id: str) -> str | None:
        """Return 'recent', 'cold', or None for how stale opponent's trace is."""
        last_turn = opponent.room_last_visited_turn.get(room_id)
        if last_turn is None:
            return None
        turns_ago = self.round - last_turn
        if turns_ago == state.TRACE_RECENT_TURNS_AGO:
            return "recent"
        if turns_ago == state.TRACE_COLD_TURNS_AGO:
            return "cold"
        return None

    def _claim_room_rewards(self, player: EdificioPlayer, room_id: str) -> None:
        for drop in list(self.coin_drops):
            if drop.room_id == room_id:
                player.wallet.add(drop.amount)
                self.broadcast_l(
                    "eledificio-found-coins",
                    buffer="game",
                    player=player.name,
                    amount=drop.amount,
                )
                self.coin_drops.remove(drop)
        if not self.chest_claimed and room_id == self.chest_room_id:
            player.wallet.add(economy.CHEST_AMOUNT)
            self.chest_claimed = True
            self.broadcast_l(
                "eledificio-found-chest",
                buffer="game",
                player=player.name,
                amount=economy.CHEST_AMOUNT,
            )
        if not self.movement_boost_claimed and room_id == self.movement_boost_room_id:
            player.movement_boost_turns_left = state.MOVEMENT_BOOST_DURATION_TURNS
            self.movement_boost_claimed = True
            self.broadcast_l(
                "eledificio-found-movement-boost",
                buffer="game",
                player=player.name,
                turns=state.MOVEMENT_BOOST_DURATION_TURNS,
            )

    # ------------------------------------------------------------------
    # Traps: resolving a trigger. Returns the room the victim actually
    # ends up in (normally room_id, unless Desvío redirected them).
    # ------------------------------------------------------------------

    def _resolve_trap_trigger(
        self,
        victim: EdificioPlayer,
        trap_hit: tuple[traps.PlacedTrap, EdificioPlayer],
    ) -> str:
        placed_trap, owner = trap_hit
        trap_def = traps.TRAPS_BY_ID[placed_trap.trap_id]
        room_id = placed_trap.room_id
        owner.trap_inventory.remove(placed_trap)

        if placed_trap.trap_id == state.TRAP_STUN:
            victim.stunned_next_turn = True
            self.broadcast_l(
                "eledificio-trap-stun",
                buffer="game",
                victim=victim.name,
                trap=trap_def.name,
            )
            owner.wallet.add(economy.REWARD_TRAP_TRIGGERED)
            return room_id

        if placed_trap.trap_id == state.TRAP_POISON:
            self._apply_damage(victim, traps.POISON_DAMAGE)
            self.broadcast_l(
                "eledificio-trap-poison",
                buffer="game",
                victim=victim.name,
                damage=traps.POISON_DAMAGE,
            )
            owner.wallet.add(economy.REWARD_TRAP_TRIGGERED)
            return room_id

        if placed_trap.trap_id == state.TRAP_REDIRECT:
            destination = self._redirect_target(victim, room_id)
            self.broadcast_l(
                "eledificio-trap-redirect",
                buffer="game",
                victim=victim.name,
            )
            owner.wallet.add(economy.REWARD_TRAP_TRIGGERED)
            return destination

        return room_id

    def _redirect_target(self, victim: EdificioPlayer, current_room: str) -> str:
        """Pick a room roughly REDIRECT_ROOMS_BACK hops closer to the
        victim's own spawn, to approximate "3 cuartos atrás por el grafo".
        """
        own_spawn = (
            self.game_map.spawn_a
            if victim.id == self._active_edificio_players()[0].id
            else self.game_map.spawn_b
        )
        candidates = self.game_map.rooms_within(
            current_room, traps.REDIRECT_ROOMS_BACK
        )
        candidates.discard(current_room)
        if not candidates:
            return current_room

        def backward_score(room_id: str) -> tuple[int, int]:
            hop_distance = self.game_map.distance(current_room, room_id)
            distance_to_spawn = self.game_map.distance(room_id, own_spawn)
            # Prefer landing as close to a full 3-hop jump as the map
            # allows; among equally-far rooms, prefer the one closer to the
            # victim's own spawn.
            return (-hop_distance, distance_to_spawn)

        return min(candidates, key=backward_score)

    # ------------------------------------------------------------------
    # Duelo trap: real rock-paper-scissors from both players. Pauses the
    # explore action that triggered it until both choices are in.
    # ------------------------------------------------------------------

    def _start_duel(
        self, victim: EdificioPlayer, owner: EdificioPlayer, placed_trap: traps.PlacedTrap
    ) -> None:
        owner.trap_inventory.remove(placed_trap)
        self.pending_duel_victim_id = victim.id
        self.pending_duel_owner_id = owner.id
        self.pending_duel_room_id = placed_trap.room_id
        self.pending_duel_victim_choice = -1
        self.pending_duel_owner_choice = -1
        self.broadcast_l(
            "eledificio-trap-duel-start",
            buffer="game",
            victim=victim.name,
            owner=owner.name,
        )
        # Bots never leave a pending duel hanging — they answer immediately.
        if victim.is_bot:
            self._action_duel_choice(
                victim, self._duel_choice_bot_select(victim), "duel_choice"
            )
        if owner.is_bot and self.pending_duel_owner_id:
            self._action_duel_choice(
                owner, self._duel_choice_bot_select(owner), "duel_choice"
            )
        self.refresh_menus()

    def _is_duel_pending_for(self, player: Player) -> bool:
        if not self.pending_duel_victim_id:
            return False
        if player.id == self.pending_duel_victim_id:
            return self.pending_duel_victim_choice == -1
        if player.id == self.pending_duel_owner_id:
            return self.pending_duel_owner_choice == -1
        return False

    def _is_duel_choice_hidden(self, player: Player) -> Visibility:
        if self.status != "playing" or player.is_spectator:
            return Visibility.HIDDEN
        return (
            Visibility.VISIBLE
            if self._is_duel_pending_for(player)
            else Visibility.HIDDEN
        )

    def _is_duel_choice_enabled(self, player: Player) -> str | None:
        if self.status != "playing":
            return "action-not-playing"
        if not self._is_duel_pending_for(player):
            return "eledificio-no-pending-duel"
        return None

    _DUEL_CHOICE_IDS = {"rock": traps.ROCK, "paper": traps.PAPER, "scissors": traps.SCISSORS}
    _DUEL_CHOICE_LABEL_KEYS = {
        "rock": "eledificio-duel-rock",
        "paper": "eledificio-duel-paper",
        "scissors": "eledificio-duel-scissors",
    }

    def _duel_choice_options(self, player: Player) -> list[str]:
        return ["rock", "paper", "scissors"]

    def _duel_choice_option_label(self, player: Player, option_id: str) -> str:
        return Localization.get(
            self._player_locale(player), self._DUEL_CHOICE_LABEL_KEYS[option_id]
        )

    def _duel_choice_bot_select(self, player: Player) -> str:
        return random.choice(["rock", "paper", "scissors"])

    def _action_duel_choice(self, player: Player, option_id: str, action_id: str) -> None:
        if not isinstance(player, EdificioPlayer):
            return
        if not self._is_duel_pending_for(player):
            return
        choice = self._DUEL_CHOICE_IDS.get(option_id)
        if choice is None:
            return
        if player.id == self.pending_duel_victim_id:
            self.pending_duel_victim_choice = choice
        else:
            self.pending_duel_owner_choice = choice
        self.broadcast_l(
            "eledificio-duel-choice-made", buffer="game", player=player.name
        )
        if self.pending_duel_victim_choice != -1 and self.pending_duel_owner_choice != -1:
            self._resolve_duel()
        else:
            self.refresh_menus()

    def _resolve_duel(self) -> None:
        victim = self.get_player_by_id(self.pending_duel_victim_id)
        owner = self.get_player_by_id(self.pending_duel_owner_id)
        room_id = self.pending_duel_room_id
        assert isinstance(victim, EdificioPlayer) and isinstance(owner, EdificioPlayer)

        outcome = traps.duel_winner(
            self.pending_duel_victim_choice, self.pending_duel_owner_choice
        )
        if outcome == 0:
            victim.wallet.add(economy.REWARD_TRAP_DUEL_WIN)
            winner_name = victim.name
        elif outcome == 1:
            owner.wallet.add(economy.REWARD_TRAP_DUEL_WIN)
            winner_name = owner.name
        else:
            winner_name = None
        self.broadcast_l(
            "eledificio-trap-duel-result",
            buffer="game",
            victim=victim.name,
            owner=owner.name,
            winner=winner_name or "nadie",
        )

        self.pending_duel_victim_id = ""
        self.pending_duel_owner_id = ""
        self.pending_duel_room_id = ""
        self.pending_duel_victim_choice = -1
        self.pending_duel_owner_choice = -1

        self._finish_landing(victim, room_id)

    # ------------------------------------------------------------------
    # Shadows
    # ------------------------------------------------------------------

    def _is_plant_shadow_enabled(self, player: Player) -> str | None:
        reason = self._turn_action_disabled_reason(player)
        if reason:
            return reason
        assert isinstance(player, EdificioPlayer)
        if player.shadow is not None and player.shadow.alive:
            return "eledificio-shadow-already-planted"
        if player.shadow_charges <= 0:
            return "eledificio-no-shadow-charges"
        return None

    def _action_plant_shadow(self, player: Player, action_id: str) -> None:
        if not isinstance(player, EdificioPlayer):
            return
        if self._is_plant_shadow_enabled(player) is not None:
            return
        player.shadow_charges -= 1
        player.shadow = shadows.Shadow(
            owner_player_id=player.id, room_id=player.room_id
        )
        self.broadcast_l(
            "eledificio-shadow-planted", buffer="game", player=player.name
        )
        self._end_turn()

    def _resolve_shadow_found_alone(
        self, finder: EdificioPlayer, shadow_owner: EdificioPlayer, room_id: str
    ) -> None:
        assert shadow_owner.shadow is not None
        damage = shadows.resolve_found_alone(shadow_owner.shadow)
        self._apply_damage(finder, damage)
        self.broadcast_l(
            "eledificio-shadow-found-alone",
            buffer="game",
            finder=finder.name,
            owner=shadow_owner.name,
            damage=damage,
        )

    def _start_blind_pick(
        self, finder: EdificioPlayer, target: EdificioPlayer, room_id: str
    ) -> None:
        self.pending_blind_pick_finder_id = finder.id
        self.pending_blind_pick_target_id = target.id
        self.pending_blind_pick_room_id = room_id
        order = ["real", "shadow"]
        random.shuffle(order)
        self.pending_blind_pick_order = order
        self.broadcast_l(
            "eledificio-blind-pick-start",
            buffer="game",
            finder=finder.name,
            target=target.name,
        )
        if finder.is_bot:
            self._action_resolve_blind_pick(
                finder, self._blind_pick_bot_select(finder), "resolve_blind_pick"
            )
        else:
            self.refresh_menus()

    def _is_blind_pick_hidden(self, player: Player) -> Visibility:
        if self.status != "playing" or player.is_spectator:
            return Visibility.HIDDEN
        return (
            Visibility.VISIBLE
            if player.id == self.pending_blind_pick_finder_id
            else Visibility.HIDDEN
        )

    def _is_blind_pick_enabled(self, player: Player) -> str | None:
        if self.status != "playing":
            return "action-not-playing"
        if player.id != self.pending_blind_pick_finder_id:
            return "eledificio-no-pending-blind-pick"
        return None

    def _blind_pick_options(self, player: Player) -> list[str]:
        if player.id != self.pending_blind_pick_finder_id:
            return []
        return list(self.pending_blind_pick_order)

    def _blind_pick_option_label(self, player: Player, option_id: str) -> str:
        # Deliberately identical no matter which of the two this is — telling
        # them apart would defeat the point of a *blind* pick.
        target = self.get_player_by_id(self.pending_blind_pick_target_id)
        name = target.name if target else "?"
        return Localization.get(
            self._player_locale(player), "eledificio-blind-pick-option", target=name
        )

    def _blind_pick_bot_select(self, player: Player) -> str:
        return random.choice(self.pending_blind_pick_order or ["real", "shadow"])

    def _action_resolve_blind_pick(
        self, player: Player, option_id: str, action_id: str
    ) -> None:
        if player.id != self.pending_blind_pick_finder_id:
            return
        if option_id not in ("real", "shadow"):
            return
        finder = player
        target = self.get_player_by_id(self.pending_blind_pick_target_id)
        room_id = self.pending_blind_pick_room_id
        assert (
            isinstance(finder, EdificioPlayer)
            and isinstance(target, EdificioPlayer)
            and target.shadow is not None
        )

        picked_shadow = option_id == "shadow"
        outcome = shadows.resolve_blind_pick(target.shadow, picked_shadow)

        self.pending_blind_pick_finder_id = ""
        self.pending_blind_pick_target_id = ""
        self.pending_blind_pick_room_id = ""
        self.pending_blind_pick_order = []

        if outcome == shadows.SHADOW_OUTCOME_COUNTERATTACK:
            self.broadcast_l(
                "eledificio-blind-pick-shadow",
                buffer="game",
                finder=finder.name,
                target=target.name,
            )
            finder.room_id = room_id
            finder.room_last_visited_turn[room_id] = self.round
            self._end_turn()
        else:
            self._trigger_encounter(finder, target, room_id)

    # ------------------------------------------------------------------
    # Traps: planting (already bought in the loadout shop — free from here)
    # ------------------------------------------------------------------

    def _plantable_trap_options(self, player: Player) -> list[str]:
        if not isinstance(player, EdificioPlayer):
            return []
        if self._trap_in_room(player.room_id) is not None:
            return []
        options = []
        for trap_def in traps.TRAPS:
            if trap_def.id not in player.purchased_trap_types:
                continue
            if not player.trap_inventory.can_place(trap_def.id):
                continue
            options.append(trap_def.id)
        return options

    def _is_plant_trap_enabled(self, player: Player) -> str | None:
        reason = self._turn_action_disabled_reason(player)
        if reason:
            return reason
        if not self._plantable_trap_options(player):
            return "eledificio-no-traps-available"
        return None

    def _trap_option_label(self, player: Player, trap_id: str) -> str:
        trap_def = traps.TRAPS_BY_ID[trap_id]
        return Localization.get(
            self._player_locale(player),
            "eledificio-trap-option-owned",
            name=trap_def.name,
        )

    def _action_plant_trap(self, player: Player, trap_id: str, action_id: str) -> None:
        if not isinstance(player, EdificioPlayer):
            return
        if trap_id not in self._plantable_trap_options(player):
            return
        trap_def = traps.TRAPS_BY_ID[trap_id]
        player.trap_inventory.place(trap_id, player.room_id, player.id)
        self.broadcast_l(
            "eledificio-trap-planted",
            buffer="game",
            player=player.name,
            trap=trap_def.name,
        )
        self._end_turn()

    def _trap_bot_select(self, player: Player, options: list[str]) -> str | None:
        """Fallback for the plant_trap menu's declarative bot_select path —
        used only if a human gets replaced by a bot while this menu is
        already open. Normal bot turns never go through this.
        """
        if not isinstance(player, EdificioPlayer) or not options:
            return options[0] if options else None
        trap_id = self._bot_choose_trap(player)
        if trap_id is None:
            return options[0]
        label = self._trap_option_label(player, trap_id)
        return label if label in options else options[0]

    # ------------------------------------------------------------------
    # Encounter
    # ------------------------------------------------------------------

    def _settle_stealth_bonus(self, player: EdificioPlayer) -> None:
        bonus = shadows.stealth_bonus(player.shadow)
        if bonus:
            player.wallet.add(bonus)
            self.broadcast_l(
                "eledificio-stealth-bonus",
                buffer="game",
                player=player.name,
                amount=bonus,
            )

    def _trigger_encounter(
        self, finder: EdificioPlayer, found: EdificioPlayer, room_id: str
    ) -> None:
        finder.room_id = room_id
        finder.room_last_visited_turn[room_id] = self.round
        self.encounter_room_id = room_id
        for player in (finder, found):
            self._settle_stealth_bonus(player)
        self.broadcast_l(
            "eledificio-encounter",
            buffer="game",
            finder=finder.name,
            found=found.name,
        )
        self._start_buy_phase()
        self.refresh_menus()

    def _start_buy_phase(self) -> None:
        self.phase = state.PHASE_BUY
        for player in self._active_edificio_players():
            player.owned_weapon_ids = [loadout.WEAPON_KNIFE.id]
            player.owned_explosives = {}
            player.owned_life_items = {}
            player.equipped_amulet_ids = []
            player.owned_potions = []
            player.owned_shields = {}
            player.owned_armor_id = ""
            player.buy_locked = False
        self.broadcast_l("eledificio-buy-start", buffer="game")
        self._auto_buy_for_bots()

    # ==================================================================
    # Turn action set (exploration phase)
    # ==================================================================

    def create_turn_action_set(self, player: Player) -> ActionSet:
        return ActionSet(name="turn")

    def _populate_exploration_actions(
        self, turn_set: ActionSet, player: EdificioPlayer, locale: str
    ) -> None:
        turn_set.add(
            Action(
                id="explore",
                label=Localization.get(locale, "eledificio-explore"),
                handler="_action_explore",
                is_enabled="_is_explore_enabled",
                is_hidden="_is_turn_action_hidden",
                input_request=MenuInput(
                    prompt="eledificio-explore-prompt",
                    options="_explore_room_options",
                    option_label="_explore_room_label",
                    bot_select="_explore_bot_select",
                ),
                show_in_actions_menu=False,
            )
        )
        turn_set.add(
            Action(
                id="plant_shadow",
                label=Localization.get(locale, "eledificio-plant-shadow"),
                handler="_action_plant_shadow",
                is_enabled="_is_plant_shadow_enabled",
                is_hidden="_is_turn_action_hidden",
                show_in_actions_menu=False,
            )
        )
        turn_set.add(
            Action(
                id="plant_trap",
                label=Localization.get(locale, "eledificio-plant-trap"),
                handler="_action_plant_trap",
                is_enabled="_is_plant_trap_enabled",
                is_hidden="_is_turn_action_hidden",
                input_request=MenuInput(
                    prompt="eledificio-plant-trap-prompt",
                    options="_plantable_trap_options",
                    option_label="_trap_option_label",
                    bot_select="_trap_bot_select",
                ),
                show_in_actions_menu=False,
            )
        )
        turn_set.add(
            Action(
                id="resolve_blind_pick",
                label=Localization.get(locale, "eledificio-attack"),
                handler="_action_resolve_blind_pick",
                is_enabled="_is_blind_pick_enabled",
                is_hidden="_is_blind_pick_hidden",
                input_request=MenuInput(
                    prompt="eledificio-blind-pick-prompt",
                    options="_blind_pick_options",
                    option_label="_blind_pick_option_label",
                    bot_select="_blind_pick_bot_select",
                ),
                show_in_actions_menu=False,
            )
        )
        turn_set.add(
            Action(
                id="duel_choice",
                label=Localization.get(locale, "eledificio-duel-choose"),
                handler="_action_duel_choice",
                is_enabled="_is_duel_choice_enabled",
                is_hidden="_is_duel_choice_hidden",
                input_request=MenuInput(
                    prompt="eledificio-duel-prompt",
                    options="_duel_choice_options",
                    option_label="_duel_choice_option_label",
                    bot_select="_duel_choice_bot_select",
                ),
                show_in_actions_menu=False,
            )
        )

    # ==================================================================
    # Bot AI
    #
    # Mirrors how Battle's bots work in this codebase: bots never go
    # through the human menu/MenuInput pipeline for their main decision —
    # they call the same internal action methods a human's choice would
    # resolve to, directly, with a precomputed value. See
    # BattleGame._perform_bot_turn / _choose_bot_plan for the pattern this
    # follows.
    # ==================================================================

    BOT_TRAP_CHANCE = 0.25
    BOT_SHADOW_CHANCE = 0.35

    def _bot_choose_trap(self, player: EdificioPlayer) -> str | None:
        """Return a random trap this bot currently owns and can plant here,
        or None if it has nothing plantable right now.

        Picking at random (not always the same one) means a bot's trap
        choices vary over a match instead of only ever placing one kind.
        """
        options = self._plantable_trap_options(player)
        if not options:
            return None
        return random.choice(options)

    def _perform_bot_turn(self, player: EdificioPlayer) -> None:
        trap_id = self._bot_choose_trap(player)
        if trap_id is not None and random.random() < self.BOT_TRAP_CHANCE:
            self._action_plant_trap(player, trap_id, "plant_trap")
            return

        no_live_shadow = player.shadow is None or not player.shadow.alive
        if (
            no_live_shadow
            and player.shadow_charges > 0
            and random.random() < self.BOT_SHADOW_CHANCE
        ):
            self._action_plant_shadow(player, "plant_shadow")
            return

        destination = self._explore_bot_select(player)
        if destination is not None:
            self._action_explore(player, destination, "explore")
        else:
            # Should not normally happen (every room has at least one
            # neighbor), but never leave the match stalled if it does.
            self._end_turn()
