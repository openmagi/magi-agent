"""Official GAIA answer scorer (normalized exact match).

Ported from the GAIA benchmark reference scorer so local scores match the
leaderboard's grading. Pure: no I/O, no model calls.
"""
from __future__ import annotations

import re
import string


def _is_float(element: object) -> bool:
    try:
        float(element)  # type: ignore[arg-type]
        return True
    except (ValueError, TypeError):
        return False


def normalize_number_str(number_str: str) -> float:
    for char in ("$", "%", ","):
        number_str = number_str.replace(char, "")
    try:
        return float(number_str)
    except ValueError:
        return float("inf")


def split_string(s: str, char_list: tuple[str, ...] = (",", ";")) -> list[str]:
    pattern = f"[{''.join(char_list)}]"
    return re.split(pattern, s)


def normalize_str(input_str: str, *, remove_punct: bool = True) -> str:
    no_spaces = re.sub(r"\s", "", input_str)
    if remove_punct:
        translator = str.maketrans("", "", string.punctuation)
        return no_spaces.lower().translate(translator)
    return no_spaces.lower()


def question_scorer(model_answer: str, ground_truth: str) -> bool:
    model_answer = "" if model_answer is None else str(model_answer)
    if _is_float(ground_truth):
        return normalize_number_str(model_answer) == float(ground_truth)
    if any(c in ground_truth for c in (",", ";")):
        gt_elems = split_string(ground_truth)
        ma_elems = split_string(model_answer)
        if len(gt_elems) != len(ma_elems):
            return False
        out: list[bool] = []
        for ma_elem, gt_elem in zip(ma_elems, gt_elems):
            if _is_float(gt_elem):
                out.append(normalize_number_str(ma_elem) == float(gt_elem))
            else:
                out.append(
                    normalize_str(ma_elem, remove_punct=False)
                    == normalize_str(gt_elem, remove_punct=False)
                )
        return all(out)
    return normalize_str(model_answer) == normalize_str(ground_truth)


__all__ = ["question_scorer", "normalize_number_str", "normalize_str", "split_string"]
