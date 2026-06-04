from magi_agent.coding.read_format import (
    apply_caps,
    binary_file_message,
    did_you_mean,
    did_you_mean_message,
    is_binary,
    number_lines,
)


def test_number_lines_is_one_indexed():
    out = number_lines("alpha\nbeta\ngamma")
    assert out == "1: alpha\n2: beta\n3: gamma"


def test_number_lines_respects_offset():
    out = number_lines("beta\ngamma", offset=2)
    assert out == "2: beta\n3: gamma"


def test_number_lines_empty_and_coerces_bad_offset():
    assert number_lines("") == ""
    assert number_lines("solo", offset=0) == "1: solo"
    assert number_lines("solo", offset=-5) == "1: solo"


def test_number_lines_trailing_newline_no_phantom_line():
    out = number_lines("only\n")
    assert out == "1: only"


def test_apply_caps_no_truncation():
    text = "a\nb\nc"
    body, truncated, next_offset = apply_caps(text, max_lines=10, max_bytes=1000)
    assert body == text
    assert truncated is False
    assert next_offset is None


def test_apply_caps_line_cap_footer_and_next_offset():
    text = "\n".join(f"line{i}" for i in range(1, 11))
    body, truncated, next_offset = apply_caps(text, max_lines=3, max_bytes=100000)
    assert truncated is True
    assert next_offset == 4
    assert body.startswith("line1\nline2\nline3")
    assert "(truncated at line 4; use offset=4 to continue)" in body


def test_apply_caps_offset_makes_next_offset_absolute():
    text = "\n".join(f"line{i}" for i in range(1, 11))
    body, truncated, next_offset = apply_caps(
        text, max_lines=3, max_bytes=100000, offset=4
    )
    assert truncated is True
    assert next_offset == 7
    assert "use offset=7 to continue" in body


def test_apply_caps_byte_cap():
    text = "\n".join("x" * 50 for _ in range(20))
    body, truncated, next_offset = apply_caps(text, max_lines=10000, max_bytes=120)
    assert truncated is True
    assert next_offset is not None
    assert "use offset=" in body


def test_is_binary_plain_text_false():
    assert is_binary(b"hello world\nthis is text\n") is False


def test_is_binary_null_byte_true():
    assert is_binary(b"hello\x00world") is True


def test_is_binary_high_nonprintable_true():
    assert is_binary(bytes(range(1, 32)) * 4) is True


def test_is_binary_empty_false():
    assert is_binary(b"") is False


def test_did_you_mean_finds_similar():
    entries = ["readme.txt", "README.md", "config.yaml", "main.py"]
    out = did_you_mean(entries, "redme.txt")
    assert "readme.txt" in out
    assert len(out) <= 3


def test_did_you_mean_limit_three():
    entries = ["foo1.py", "foo2.py", "foo3.py", "foo4.py", "foo5.py"]
    out = did_you_mean(entries, "foo.py", limit=3)
    assert len(out) <= 3


def test_did_you_mean_no_match_returns_empty():
    out = did_you_mean(["alpha.py", "beta.py"], "zzzzzzz.bin")
    assert out == []


def test_did_you_mean_does_not_invent_or_leak_unfiltered():
    # The caller is responsible for filtering sealed/secret names; this proves
    # did_you_mean only returns from the (already-filtered) entries it is given.
    safe_entries = ["notes.txt", "main.py"]
    out = did_you_mean(safe_entries, "note.txt")
    assert set(out).issubset(set(safe_entries))
    assert ".env" not in out
    assert "secret.key" not in out


def test_binary_file_message():
    assert binary_file_message() == "Cannot read binary file"
    assert binary_file_message("a/b.bin") == "Cannot read binary file: a/b.bin"


def test_did_you_mean_message():
    assert did_you_mean_message("x.py", []) == "File not found: x.py"
    msg = did_you_mean_message("x.py", ["xx.py", "xy.py"])
    assert msg == "File not found: x.py. Did you mean? xx.py, xy.py"
