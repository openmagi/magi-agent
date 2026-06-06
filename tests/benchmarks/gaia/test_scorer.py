from __future__ import annotations

from magi_agent.benchmarks.gaia.scorer import question_scorer


def test_number_with_units_and_commas() -> None:
    assert question_scorer("17,000", "17000") is True
    assert question_scorer("$5.50", "5.5") is True
    assert question_scorer("18", "17") is False


def test_string_is_case_and_punct_insensitive() -> None:
    assert question_scorer("Egalitarian.", "egalitarian") is True
    assert question_scorer("FunkyMonkey", "funky monkey") is True


def test_comma_list_elementwise() -> None:
    assert question_scorer("apple, banana, pear", "apple,banana,pear") is True
    assert question_scorer("1, 2, 3", "1,2,3") is True
    assert question_scorer("apple, pear", "apple, banana, pear") is False


def test_list_numbers_compared_numerically() -> None:
    # GAIA's official split_string splits on both ',' and ';' in one pass, and
    # the GAIA prompt forbids commas inside numbers, so list answers are clean.
    assert question_scorer("1000; 2000", "1000;2000") is True
    assert question_scorer("1000, 2000", "1000,2000") is True
    assert question_scorer("1000;2000", "1000;2001") is False
