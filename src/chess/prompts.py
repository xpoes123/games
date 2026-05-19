"""Prompt + PendingPlay state machine for sequential card resolution.

A card with N targets emits N prompts. The client responds to each in order;
once the schema is satisfied, effects.py resolves the card. Phase 1 only
exercises single-step prompts (piece placements), but the machinery is in
place so Phase 2 can wire chains without protocol churn.
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from typing import Any

from src.chess.cards import Card


@dataclass
class Prompt:
    prompt_id: str
    seat: str                            # "white" | "black"
    kind: str                            # protocol prompt kind
    label: str
    options: list[str] = field(default_factory=list)
    min: int = 0
    max: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_json(self, deadline_ms: int | None = None) -> dict:
        out: dict = {
            "type": "prompt",
            "prompt_id": self.prompt_id,
            "kind": self.kind,
            "label": self.label,
        }
        if self.options:
            out["options"] = self.options
        if self.min or self.max is not None:
            out["min"] = self.min
            out["max"] = self.max
        if deadline_ms is not None:
            out["deadline_ms"] = deadline_ms
        out.update(self.extra)
        return out


@dataclass
class PendingPlay:
    """A card mid-resolution. `targets_collected` are the answers the client
    has given so far, in the order of the card's `targets` schema."""
    seat: str
    card: Card
    slot: int
    paid_gold: int
    targets_collected: list[dict] = field(default_factory=list)
    modal_choice: str | None = None
    prompt: Prompt | None = None


def new_prompt_id() -> str:
    return "p-" + secrets.token_hex(4)


# Map target-schema entry → protocol prompt kind + human label.
TARGET_PROMPTS: dict[str, tuple[str, str]] = {
    "empty_square_back2": ("select_square_back2", "Pick a square in your back two ranks"),
    "empty_square_back2_opp": ("select_enemy_rank_empty", "Pick an empty square in opponent's rank 2/7"),
    "empty_square_central4": ("select_central_empty", "Pick d4/d5/e4/e5"),
    "any_empty_square": ("select_any_empty", "Pick any empty square"),
    "friendly_pawn": ("select_friendly_pawn", "Pick a friendly pawn"),
    "friendly_piece": ("select_friendly_piece", "Pick a friendly piece"),
    "enemy_piece": ("select_enemy_piece", "Pick an enemy piece"),
    "any_knight_or_bishop": ("select_any_minor", "Pick a knight or bishop"),
    "any_piece_kind": ("select_any_piece_kind", "Pick a piece kind"),
    "discard_subset": ("discard_subset", "Pick cards to discard"),
}


def build_choose_opp_move_prompt(seat: str, moves: list[dict]) -> Prompt:
    return Prompt(
        prompt_id=new_prompt_id(),
        seat=seat,
        kind="choose_opp_move",
        label="Choose your opponent's move",
        extra={"moves": moves},
    )


def build_target_prompt(seat: str, target: str) -> Prompt:
    kind, label = TARGET_PROMPTS[target]
    return Prompt(prompt_id=new_prompt_id(), seat=seat, kind=kind, label=label)


def build_modal_prompt(seat: str, options: list[str]) -> Prompt:
    return Prompt(
        prompt_id=new_prompt_id(),
        seat=seat,
        kind="select_modal",
        label="Choose one",
        options=list(options),
    )


def build_mulligan_prompt(seat: str, hand_size: int) -> Prompt:
    return Prompt(
        prompt_id=new_prompt_id(),
        seat=seat,
        kind="mulligan",
        label="Pick cards to replace",
        min=0,
        max=hand_size,
    )
