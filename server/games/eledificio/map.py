"""Room graph generation and lookups for El Edificio.

The map is a connected, branching graph of rooms (not a grid or a single
corridor), so it stays unpredictable even on the small room counts used for
1v1. See the design document, section "Mapa y exploración", for the rules
this module implements.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

#: Room counts by match size (see design doc, "Tamaño del mapa según jugadores").
ROOM_COUNT_SMALL = 9  # 1v1 / 2v2
ROOM_COUNT_MEDIUM = 13  # 3v3
ROOM_COUNT_LARGE = 17  # 4v4 or up to 8 free-for-all
ROOM_COUNTS = frozenset({ROOM_COUNT_SMALL, ROOM_COUNT_MEDIUM, ROOM_COUNT_LARGE})

#: Extra edges added on top of the spanning tree, as a fraction of room
#: count, so the layout branches instead of reading like one corridor.
_EXTRA_EDGE_RATIO = 0.35

#: Display names drawn from when building a map. Must have at least
#: ROOM_COUNT_LARGE entries; build_map() raises if the pool runs short.
#: Themed as a hotel/building, per the game's setting.
ROOM_NAMES_POOL = (
    "estacionamiento",
    "cochera",
    "recepción",
    "vestíbulo",
    "sala de estar",
    "comedor",
    "cocina",
    "cuarto de planta baja",
    "cuarto de segundo piso",
    "cuarto de tercer piso",
    "habitación 101",
    "habitación 202",
    "pasillo principal",
    "escalera",
    "elevador",
    "azotea",
    "sótano",
    "lavandería",
)


def room_count_for_players(player_count: int) -> int:
    """Return the room count to use for a given number of active players."""
    if player_count <= 2:
        return ROOM_COUNT_SMALL
    if player_count <= 6:
        return ROOM_COUNT_MEDIUM
    return ROOM_COUNT_LARGE


@dataclass
class RoomMap:
    """A connected graph of rooms, plus the two spawn points furthest apart.

    ``connections`` stores each room's neighbor ids as a list rather than a
    set, so this dataclass serializes cleanly with the JSON-based save
    format the rest of the games in this codebase use (see
    ``server/games/breachpoint/ground.py`` and ``effects.py`` for the same
    list-over-set convention on serialized fields).
    """

    room_ids: list[str] = field(default_factory=list)
    room_names: dict[str, str] = field(default_factory=dict)
    connections: dict[str, list[str]] = field(default_factory=dict)
    spawn_a: str = ""
    spawn_b: str = ""

    def neighbors(self, room_id: str) -> list[str]:
        """Return the ids of rooms directly connected to ``room_id``."""
        return self.connections.get(room_id, [])

    def rooms_within(self, room_id: str, max_distance: int) -> set[str]:
        """Return rooms reachable from ``room_id`` in at most ``max_distance``
        hops, excluding ``room_id`` itself.

        Used to enforce the "hasta 2 cuartos de distancia" inspection limit
        (or up to 4 while Sobrecarga de Movimiento is active).
        """
        visited = {room_id}
        frontier = {room_id}
        for _ in range(max_distance):
            next_frontier: set[str] = set()
            for current in frontier:
                for neighbor in self.connections.get(current, []):
                    if neighbor not in visited:
                        visited.add(neighbor)
                        next_frontier.add(neighbor)
            frontier = next_frontier
            if not frontier:
                break
        visited.discard(room_id)
        return visited

    def distance(self, start: str, goal: str) -> int:
        """Return the hop count on the shortest path between two rooms.

        Returns -1 if ``goal`` is unreachable from ``start``. This should
        never happen on a map produced by :func:`build_map`, which always
        yields a connected graph.
        """
        if start == goal:
            return 0
        visited = {start}
        frontier = [start]
        distance = 0
        while frontier:
            distance += 1
            next_frontier: list[str] = []
            for current in frontier:
                for neighbor in self.connections.get(current, []):
                    if neighbor == goal:
                        return distance
                    if neighbor not in visited:
                        visited.add(neighbor)
                        next_frontier.append(neighbor)
            frontier = next_frontier
        return -1


def build_map(player_count: int, rng: random.Random | None = None) -> RoomMap:
    """Build a connected, branching room graph sized for ``player_count``.

    Algorithm:

    1. Build a random spanning tree (attach each new room to a random room
       already placed) — this guarantees every room is reachable from every
       other room, with no isolated pockets.
    2. Add a handful of extra random edges on top of the tree, so the layout
       branches into loops and shortcuts instead of reading as one
       predictable corridor.
    3. Find the two rooms with the largest shortest-path distance between
       them (ties broken at random) and use them as the two spawn points,
       so players start as far apart as the map allows.

    Raises:
        ValueError: if ``ROOM_NAMES_POOL`` doesn't have enough entries for
            the room count this player count requires.
    """
    rng = rng or random.Random()
    room_count = room_count_for_players(player_count)

    if room_count > len(ROOM_NAMES_POOL):
        raise ValueError(
            f"Not enough room names in ROOM_NAMES_POOL for {room_count} "
            f"rooms (have {len(ROOM_NAMES_POOL)})"
        )

    room_ids = [f"room_{index}" for index in range(room_count)]
    shuffled_names = list(ROOM_NAMES_POOL)
    rng.shuffle(shuffled_names)
    room_names = {
        room_id: shuffled_names[index] for index, room_id in enumerate(room_ids)
    }

    connection_sets: dict[str, set[str]] = {room_id: set() for room_id in room_ids}

    def connect(a: str, b: str) -> None:
        connection_sets[a].add(b)
        connection_sets[b].add(a)

    # Step 1: random spanning tree.
    placed = [room_ids[0]]
    for room_id in room_ids[1:]:
        anchor = rng.choice(placed)
        connect(room_id, anchor)
        placed.append(room_id)

    # Step 2: extra edges for branching.
    extra_edges = max(1, round(room_count * _EXTRA_EDGE_RATIO))
    added = 0
    attempts = 0
    max_attempts = extra_edges * 10
    while added < extra_edges and attempts < max_attempts:
        attempts += 1
        a, b = rng.sample(room_ids, 2)
        if b not in connection_sets[a]:
            connect(a, b)
            added += 1

    room_map = RoomMap(
        room_ids=room_ids,
        room_names=room_names,
        connections={
            room_id: sorted(neighbors)
            for room_id, neighbors in connection_sets.items()
        },
    )

    # Step 3: farthest-apart spawn pair.
    room_map.spawn_a, room_map.spawn_b = _pick_farthest_pair(room_map, rng)
    return room_map


def _pick_farthest_pair(room_map: RoomMap, rng: random.Random) -> tuple[str, str]:
    """Return two room ids with the largest shortest-path distance between
    them. Ties are broken at random so spawn points don't always land on the
    same pair of rooms for a given room count.
    """
    best_distance = -1
    best_pairs: list[tuple[str, str]] = []
    room_ids = room_map.room_ids
    for i, start in enumerate(room_ids):
        for goal in room_ids[i + 1 :]:
            distance = room_map.distance(start, goal)
            if distance > best_distance:
                best_distance = distance
                best_pairs = [(start, goal)]
            elif distance == best_distance:
                best_pairs.append((start, goal))
    return rng.choice(best_pairs)
