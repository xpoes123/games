"""Math 24: deal four numbers solvable to 24; validate players' expressions.

A submission is valid iff it uses each dealt number exactly once with only
+ - * / and parentheses, and evaluates (exactly, via Fraction) to 24.
"""
from __future__ import annotations

import ast
import random
from fractions import Fraction

TARGET = 24


def _combine(nums: list[Fraction]) -> bool:
    """True if some sequence of + - * / over these numbers makes TARGET."""
    if len(nums) == 1:
        return nums[0] == TARGET
    for i in range(len(nums)):
        for j in range(len(nums)):
            if i == j:
                continue
            rest = [nums[k] for k in range(len(nums)) if k != i and k != j]
            a, b = nums[i], nums[j]
            cands = [a + b, a - b, a * b]
            if b != 0:
                cands.append(a / b)
            for c in cands:
                if _combine(rest + [c]):
                    return True
    return False


def solvable(numbers: list[int]) -> bool:
    return _combine([Fraction(n) for n in numbers])


def _solve(items: list[tuple[Fraction, str]]) -> str | None:
    if len(items) == 1:
        return items[0][1] if items[0][0] == TARGET else None
    for i in range(len(items)):
        for j in range(len(items)):
            if i == j:
                continue
            rest = [items[k] for k in range(len(items)) if k != i and k != j]
            (a, ea), (b, eb) = items[i], items[j]
            cands = [(a + b, f"({ea}+{eb})"), (a - b, f"({ea}-{eb})"), (a * b, f"({ea}*{eb})")]
            if b != 0:
                cands.append((a / b, f"({ea}/{eb})"))
            for val, exp in cands:
                r = _solve(rest + [(val, exp)])
                if r:
                    return r
    return None


def solve_expr(numbers: list[int]) -> str | None:
    """A worked expression making 24, or None."""
    return _solve([(Fraction(n), str(n)) for n in numbers])


def deal(lo: int = 1, hi: int = 9) -> list[int]:
    """Four numbers in [lo, hi] guaranteed to have a 24 solution."""
    while True:
        nums = [random.randint(lo, hi) for _ in range(4)]
        if solvable(nums):
            return nums


_ALLOWED = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.USub,
            ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Constant)


def _walk(node, consts: list[int]) -> Fraction:
    if isinstance(node, ast.Expression):
        return _walk(node.body, consts)
    if isinstance(node, ast.BinOp):
        a, b = _walk(node.left, consts), _walk(node.right, consts)
        if isinstance(node.op, ast.Add):
            return a + b
        if isinstance(node.op, ast.Sub):
            return a - b
        if isinstance(node.op, ast.Mult):
            return a * b
        if isinstance(node.op, ast.Div):
            if b == 0:
                raise ValueError("divide by zero")
            return a / b
        raise ValueError("operator not allowed")
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return -_walk(node.operand, consts)
    if isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(node.value, bool):
        consts.append(node.value)
        return Fraction(node.value)
    raise ValueError("only numbers, + - * / and parentheses allowed")


def check(expr: str, numbers: list[int]) -> tuple[bool, str]:
    """Validate a submission against the dealt numbers. Returns (ok, reason)."""
    if len(expr) > 200:
        return False, "expression too long"
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return False, "can't parse that"
    for n in ast.walk(tree):
        if not isinstance(n, (_ALLOWED, ast.Load)):
            return False, "only numbers, + - * / and parentheses allowed"
    consts: list[int] = []
    try:
        value = _walk(tree, consts)
    except ValueError as e:
        return False, str(e)
    if sorted(consts) != sorted(numbers):
        return False, "use each of the four numbers exactly once"
    if value != TARGET:
        return False, f"that makes {value}, not 24"
    return True, "ok"


def _demo() -> None:
    assert solvable([4, 6, 1, 1])         # 4*6*1*1
    assert check("4*6*1*1", [4, 6, 1, 1])[0]
    assert check("6/(1-3/4)", [6, 1, 3, 4])[0]      # classic 24
    assert not check("4*6", [4, 6, 1, 1])[0]        # missing numbers
    assert not check("4*6*1+1", [4, 6, 1, 1])[0]    # = 25
    assert not check("4*6*1*2", [4, 6, 1, 1])[0]    # wrong numbers
    assert not check("__import__('os')", [4, 6, 1, 1])[0]  # rejected
    nums = deal()
    assert solvable(nums) and len(nums) == 4
    print("ok")


if __name__ == "__main__":
    _demo()
