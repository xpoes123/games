"""Effect dispatcher.

Every card's effect is dispatched here, keyed by CardDef.effect_key. Effects
mutate room/player state directly and return a list of event dicts that the
WS layer will broadcast.

Targets have already been validated by Room._validate_targets before resolve()
is called. Effects may emit secondary prompts via room.pending_prompts /
PendingPlay if needed (none of the current cards require that — every
sequential decision was pre-declared in CardDef.targets and answered eagerly
on play_card).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from src.chess.board import Piece
from src.chess.cards import PIECE_COSTS

if TYPE_CHECKING:
    from src.chess.prompts import PendingPlay
    from src.chess.rooms import Player, Room


PIECE_PLACE_KINDS = {
    "place_pawn": "pawn",
    "place_knight": "knight",
    "place_bishop": "bishop",
    "place_rook": "rook",
    "place_queen": "queen",
}

# Material point values used for the 7/8-material spells. Per CARDS.md:
# pawn=1, knight=2, bishop=3, rook=5, queen=8 (card costs as points).
MATERIAL_POINTS = {"pawn": 1, "knight": 2, "bishop": 3, "rook": 5, "queen": 8}


def resolve(room: "Room", player: "Player", pending: "PendingPlay") -> list[dict]:
    key = pending.card.defn.effect_key

    if key in PIECE_PLACE_KINDS:
        return _place_piece(room, player, pending, PIECE_PLACE_KINDS[key])
    if key == "place_any":
        kind = (pending.modal_choice or "pawn").lower()
        return _place_piece(room, player, pending, kind)
    if key == "spell_draw_2":
        room.draw_cards(player, 2)
        return [{"kind": "card_drawn", "by": player.seat, "count": 2}]
    if key == "spell_gain_2_gold":
        player.gold += 2
        return []
    if key == "spell_extra_pawn_move":
        sq = pending.targets_collected[0]["square"]
        player.extra_pawn_squares[sq] = player.extra_pawn_squares.get(sq, 0) + 1
        # Grant a free pawn move so the bonus is actually usable, even if the
        # player already exhausted their normal move this turn.
        from src.chess.rooms import MAX_MOVES_PER_TURN
        player.moves_remaining = min(player.moves_remaining + 1, MAX_MOVES_PER_TURN)
        return []
    if key == "spell_pawn_back_or_two_moves":
        return _pawn_back_or_two(room, player, pending)
    if key == "spell_play_2_pawns":
        return _place_many(room, player, pending, ["pawn", "pawn"])
    if key == "spell_combine_pawns":
        events = _combine_pawns(room, player, pending)
        drew = room.draw_cards(player, 1)
        events.append({"kind": "card_drawn", "by": player.seat, "count": drew})
        return events
    if key == "spell_adjacent_en_passant":
        return _adjacent_ep(room, player, pending)
    if key == "spell_remove_pawn_draw_3":
        sq = pending.targets_collected[0]["square"]
        room.board.remove(sq)
        room.draw_cards(player, 3)
        return [
            {"kind": "piece_captured", "sq": sq, "victim_kind": "pawn", "victim_color": player.seat},
            {"kind": "card_drawn", "by": player.seat, "count": 3},
        ]
    if key == "spell_modular_board":
        player.modular_board_this_turn = True
        player.cannot_capture_king_this_turn = True
        return []
    if key == "spell_tax_opponent":
        opp = room.opponent_of(player.seat)
        if opp is not None:
            opp.spell_tax_next_turn += 3
        return []
    if key == "spell_pawn_to_rook":
        pawn_sq = pending.targets_collected[0]["square"]
        room.board.remove(pawn_sq)
        place_sq = pending.targets_collected[1]["square"]
        room.board.place(place_sq, Piece(player.seat, "rook", placed_this_turn=True))
        return [
            {"kind": "piece_captured", "sq": pawn_sq, "victim_kind": "pawn", "victim_color": player.seat},
            {"kind": "piece_placed", "color": player.seat, "piece_kind": "rook", "sq": place_sq},
        ]
    if key == "spell_remove_minor":
        sq = pending.targets_collected[0]["square"]
        victim = room.board.remove(sq)
        if victim is None:
            return []
        drew = room.draw_cards(player, 1)
        return [
            {"kind": "piece_captured", "sq": sq,
             "victim_kind": victim.kind, "victim_color": victim.color},
            {"kind": "card_drawn", "by": player.seat, "count": drew},
        ]
    if key == "spell_forced_promotion":
        pawn_sq = pending.targets_collected[0]["square"]
        kind = (pending.modal_choice or "Knight").lower()
        pc = room.board.at(pawn_sq)
        if pc is None or pc.kind != "pawn" or pc.color != player.seat:
            return []
        room.board.place(pawn_sq, Piece(player.seat, kind, placed_this_turn=True))  # type: ignore[arg-type]
        return [
            {"kind": "piece_captured", "sq": pawn_sq, "victim_kind": "pawn", "victim_color": player.seat},
            {"kind": "piece_placed", "color": player.seat, "piece_kind": kind, "sq": pawn_sq},
        ]
    if key == "spell_discard_draw":
        return _discard_draw(room, player, pending)
    if key == "spell_teleport":
        return _teleport(room, player, pending)
    if key == "spell_king_to_center":
        events = _king_to_center(room, player, pending)
        drew = room.draw_cards(player, 1)
        events.append({"kind": "card_drawn", "by": player.seat, "count": drew})
        return events
    if key == "spell_draw_double":
        # Hand-size at moment of resolution. The played card has already been
        # removed from the hand by play_card.
        n = len(player.hand)
        drew = room.draw_cards(player, n)
        return [{"kind": "card_drawn", "by": player.seat, "count": drew}]
    if key == "spell_pawns_to_queen":
        return _pawns_to_queen(room, player, pending)
    if key == "spell_material_to_queen":
        return _material_to_queen(room, player, pending)
    if key == "spell_convert_piece":
        sq = pending.targets_collected[0]["square"]
        pc = room.board.at(sq)
        if pc is None:
            return []
        pc.color = player.seat  # type: ignore[assignment]
        return [{"kind": "piece_converted", "sq": sq, "to_color": player.seat, "kind": pc.kind}]
    if key == "spell_triple_move":
        player.moves_remaining = 3
        player.cannot_capture_king_this_turn = True
        # Distinct-pieces constraint: each of the 3 moves must be a different
        # piece. Tracked via Player.triple_move_used (squares pieces moved from
        # / arrived at this turn).
        player.triple_move_active = True
        player.triple_move_used.clear()
        return []
    if key == "spell_rook_and_minor":
        return _rook_and_minor(room, player, pending)
    if key == "spell_deploy_enemy_rank":
        kind = (pending.modal_choice or "Pawn").lower()
        sq = pending.targets_collected[0]["square"]
        room.board.place(sq, Piece(player.seat, kind, placed_this_turn=True))  # type: ignore[arg-type]
        return [{"kind": "piece_placed", "color": player.seat, "piece_kind": kind, "sq": sq}]
    if key == "spell_eight_or_eight":
        return _eight_or_eight(room, player, pending)
    if key == "spell_wipe_type":
        return _wipe_type(room, player, pending)
    if key == "spell_draw_full_no_move":
        from src.chess.rooms import HAND_CAP
        n = HAND_CAP - len(player.hand)
        drew = room.draw_cards(player, n) if n > 0 else 0
        player.no_chess_move_this_turn = True
        player.moves_remaining = 0
        return [{"kind": "card_drawn", "by": player.seat, "count": drew}]
    if key == "spell_choose_opp_move":
        # Immediate mode: caster picks one of opponent's currently-legal
        # moves and it resolves NOW (during the caster's own turn). The
        # picking is surfaced via a choose_opp_move prompt to the caster;
        # apply_opp_chosen_move handles the actual move application.
        opp = room.opponent_of(player.seat)
        if opp is None:
            return []
        moves = room._legal_moves_for_opp_chooser(opp, exclude_caster_king=True)
        if not moves:
            # Opp has nothing to move — spell fizzles silently.
            return []
        from src.chess.prompts import build_choose_opp_move_prompt
        prompt = build_choose_opp_move_prompt(player.seat, moves)
        room.pending_prompts[prompt.prompt_id] = prompt
        return []
    if key == "spell_draw_rook_extra":
        room.draw_cards(player, 4)
        sq = pending.targets_collected[0]["square"]
        room.board.place(sq, Piece(player.seat, "rook", placed_this_turn=True))
        from src.chess.rooms import MAX_MOVES_PER_TURN
        player.moves_remaining = min(player.moves_remaining + 1, MAX_MOVES_PER_TURN)
        player.cannot_capture_king_this_turn = True
        return [
            {"kind": "card_drawn", "by": player.seat, "count": 4},
            {"kind": "piece_placed", "color": player.seat, "piece_kind": "rook", "sq": sq},
        ]
    if key == "spell_quad_deploy":
        return _place_many(room, player, pending, ["knight", "bishop", "rook", "pawn"])
    if key == "spell_extra_turn":
        player.extra_turn_queued = True
        player.extra_turn_no_capture_king = True
        return []
    if key == "spell_queen_and_strip":
        return _queen_and_strip(room, player, pending)
    if key == "spell_echo":
        # Resolved via a prompt — see rooms.play_card / resolve_echo_pick.
        # By the time we get here as a stub the pending.targets_collected has
        # the picked opp_hand_slot. Move that card from opp hand to caster.
        opp = room.opponent_of(player.seat)
        if opp is None or not pending.targets_collected:
            return []
        slot = pending.targets_collected[0].get("opp_slot")
        if slot is None or slot < 0 or slot >= len(opp.hand):
            return []
        card = opp.hand.pop(slot)
        # Add to caster's hand (burn if over cap).
        from src.chess.rooms import HAND_CAP
        if len(player.hand) >= HAND_CAP:
            return [{"kind": "card_burned", "by": player.seat, "card_id": card.defn.id}]
        player.hand.append(card)
        return [{"kind": "card_stolen", "by": player.seat, "card_id": card.defn.id}]
    if key == "spell_free_pieces":
        room.draw_cards(player, 5)
        player.pieces_free_this_turn = True
        return [{"kind": "card_drawn", "by": player.seat, "count": 5}]

    raise NotImplementedError(key)


def _place_piece(room: "Room", player: "Player", pending: "PendingPlay", kind: str) -> list[dict]:
    target = pending.targets_collected[0]
    sq = target["square"]
    room.board.place(sq, Piece(player.seat, kind, placed_this_turn=True))  # type: ignore[arg-type]
    return [{
        "kind": "piece_placed", "color": player.seat, "piece_kind": kind, "sq": sq,
    }]


def _place_many(room: "Room", player: "Player", pending: "PendingPlay", kinds: list[str]) -> list[dict]:
    events = []
    for k, t in zip(kinds, pending.targets_collected):
        sq = t["square"]
        room.board.place(sq, Piece(player.seat, k, placed_this_turn=True))  # type: ignore[arg-type]
        events.append({"kind": "piece_placed", "color": player.seat, "piece_kind": k, "sq": sq})
    return events


def _pawn_back_or_two(room: "Room", player: "Player", pending: "PendingPlay") -> list[dict]:
    choice = (pending.modal_choice or "Backward").lower()
    if choice.startswith("back"):
        # Targets list is empty per the card def — the pawn is picked separately.
        # We accept either: nothing collected (UI arms a generic flag) or a
        # square. Without a square in the schema we just arm the flag for any
        # of the player's pawns; rooms.make_move handles the backward step.
        if pending.targets_collected:
            player.pawn_back_pawn_sq = pending.targets_collected[0]["square"]
        else:
            player.pawn_back_pawn_sq = "*"   # wildcard: any of your pawns
        return []
    player.pawn_two_moves_armed = True
    player.moves_remaining = max(player.moves_remaining, 2)
    return []


def _combine_pawns(room: "Room", player: "Player", pending: "PendingPlay") -> list[dict]:
    sqs = [t["square"] for t in pending.targets_collected[:3]]
    place_sq = pending.targets_collected[3]["square"]
    events = []
    for sq in sqs:
        room.board.remove(sq)
        events.append({"kind": "piece_captured", "sq": sq,
                       "victim_kind": "pawn", "victim_color": player.seat})
    kind = (pending.modal_choice or "Knight").lower()
    room.board.place(place_sq, Piece(player.seat, kind, placed_this_turn=True))  # type: ignore[arg-type]
    events.append({"kind": "piece_placed", "color": player.seat,
                   "piece_kind": kind, "sq": place_sq})
    return events


def _adjacent_ep(room: "Room", player: "Player", pending: "PendingPlay") -> list[dict]:
    # Pawn must be adjacent to the captured enemy piece (8-neighbour). Already
    # validated in rooms._validate_targets.
    enemy_sq = pending.targets_collected[1]["square"]
    victim = room.board.remove(enemy_sq)
    if victim is None:
        return []
    return [{"kind": "piece_captured", "sq": enemy_sq,
             "victim_kind": victim.kind, "victim_color": victim.color}]


def _teleport(room: "Room", player: "Player", pending: "PendingPlay") -> list[dict]:
    src = pending.targets_collected[0]["square"]
    dst = pending.targets_collected[1]["square"]
    pc = room.board.remove(src)
    if pc is None:
        return []
    room.board.place(dst, pc)
    return [{"kind": "piece_moved", "from": src, "to": dst, "captured": None}]


def _king_to_center(room: "Room", player: "Player", pending: "PendingPlay") -> list[dict]:
    src = pending.targets_collected[0]["square"]
    dst = pending.targets_collected[1]["square"]
    king = room.board.remove(src)
    if king is None:
        return []
    room.board.place(dst, king)
    return [{"kind": "piece_moved", "from": src, "to": dst, "captured": None}]


def _two_minors_opp_picks(room: "Room", player: "Player", pending: "PendingPlay") -> list[dict]:
    # Opponent's choice is asynchronous in protocol; for the engine we accept
    # `modal_choice` (the caster has carried over what the opp picked, OR the
    # client default-resolved with Knight). 30s timeout default in protocol.
    kind = (pending.modal_choice or "Knight").lower()
    return _place_many(room, player, pending, [kind, kind])


def _discard_draw(room: "Room", player: "Player", pending: "PendingPlay") -> list[dict]:
    # The card schema declares a single `discard_subset` target. The slots are
    # in pending.targets_collected[0] as `{slots: [..]}`. The played card has
    # already been popped from hand, so slot indices refer to the post-play
    # hand layout.
    slots = pending.targets_collected[0].get("slots") if pending.targets_collected else None
    slots = sorted(set(slots or []), reverse=True)
    n = 0
    for s in slots:
        if 0 <= s < len(player.hand):
            player.hand.pop(s)
            n += 1
    drew = room.draw_cards(player, n)
    return [{"kind": "card_drawn", "by": player.seat, "count": drew}]


def _pawns_to_queen(room: "Room", player: "Player", pending: "PendingPlay") -> list[dict]:
    pawns = [sq for sq, p in room.board.pieces_of(player.seat) if p.kind == "pawn"]  # type: ignore[arg-type]
    place_sq = pending.targets_collected[0]["square"]
    events = []
    for sq in pawns:
        room.board.remove(sq)
        events.append({"kind": "piece_captured", "sq": sq,
                       "victim_kind": "pawn", "victim_color": player.seat})
    room.board.place(place_sq, Piece(player.seat, "queen", placed_this_turn=True))
    events.append({"kind": "piece_placed", "color": player.seat,
                   "piece_kind": "queen", "sq": place_sq})
    return events


def _material_to_queen(room: "Room", player: "Player", pending: "PendingPlay") -> list[dict]:
    # Squares to sacrifice are pre-collected in target dicts as `sac_squares`
    # on the FIRST target (the placement). Server has already verified the sum
    # equals 7.
    sac = pending.targets_collected[0].get("sac_squares") or []
    place_sq = pending.targets_collected[0]["square"]
    events = []
    for sq in sac:
        pc = room.board.remove(sq)
        if pc is not None:
            events.append({"kind": "piece_captured", "sq": sq,
                           "victim_kind": pc.kind, "victim_color": pc.color})
    room.board.place(place_sq, Piece(player.seat, "queen", placed_this_turn=True))
    events.append({"kind": "piece_placed", "color": player.seat,
                   "piece_kind": "queen", "sq": place_sq})
    return events


def _rook_and_minor(room: "Room", player: "Player", pending: "PendingPlay") -> list[dict]:
    minor = (pending.modal_choice or "Knight").lower()
    return _place_many(room, player, pending, ["rook", minor])


def _eight_or_eight(room: "Room", player: "Player", pending: "PendingPlay") -> list[dict]:
    mode = (pending.modal_choice or "Pawns").lower()
    if mode.startswith("pawn"):
        # Variable target count 1..8 of empty_square_back2. Validated up-front.
        kinds = ["pawn"] * len(pending.targets_collected)
        return _place_many(room, player, pending, kinds)
    # Material mode: each target carries `piece_kind`. Sum already validated.
    events = []
    for t in pending.targets_collected:
        kind = t["piece_kind"]
        sq = t["square"]
        room.board.place(sq, Piece(player.seat, kind, placed_this_turn=True))  # type: ignore[arg-type]
        events.append({"kind": "piece_placed", "color": player.seat,
                       "piece_kind": kind, "sq": sq})
    return events


def _wipe_type(room: "Room", player: "Player", pending: "PendingPlay") -> list[dict]:
    kind = (pending.modal_choice or "pawn").lower()
    if kind == "king":
        return []
    victims = [sq for sq, p in list(room.board.squares.items()) if p.kind == kind]
    events = []
    for sq in victims:
        p = room.board.remove(sq)
        if p is not None:
            events.append({"kind": "piece_captured", "sq": sq,
                           "victim_kind": kind, "victim_color": p.color})
    return events


def _queen_and_strip(room: "Room", player: "Player", pending: "PendingPlay") -> list[dict]:
    place_sq = pending.targets_collected[0]["square"]
    strip = pending.targets_collected[0].get("strip_squares") or []
    events = []
    room.board.place(place_sq, Piece(player.seat, "queen", placed_this_turn=True))
    events.append({"kind": "piece_placed", "color": player.seat,
                   "piece_kind": "queen", "sq": place_sq})
    for sq in strip:
        pc = room.board.remove(sq)
        if pc is not None:
            events.append({"kind": "piece_captured", "sq": sq,
                           "victim_kind": pc.kind, "victim_color": pc.color})
    return events


def cost_for(player: "Player", card_def, modal_choice: str | None = None) -> int:
    """Effective gold cost considering Any-Piece modal + tax + free-pieces."""
    base = card_def.cost
    if card_def.id == "piece_any":
        base = PIECE_COSTS[(modal_choice or "Pawn").lower()]
    if card_def.type == "piece" and player.pieces_free_this_turn:
        return 0
    if card_def.type == "spell":
        base += player.spell_tax
    return base
