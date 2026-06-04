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


def test_number_lines_crlf_consistent_with_apply_caps():
    # apply_caps and number_lines both use split("\n") so their line counts stay
    # in sync for CRLF and mixed-EOL content.
    # split("\n") on "alpha\r\nbeta\r\ngamma\r\n" → ["alpha\r", "beta\r", "gamma\r", ""]
    # apply_caps keeps all 4 tokens (line cap not hit), joins with "\n" → body with
    # trailing "\n".  number_lines drops the trailing empty token → 3 numbered lines.
    # The important invariant: the NUMBER of non-empty lines seen by number_lines
    # equals the line-cap count tracked by apply_caps (both count split("\n") items,
    # both ignore the trailing empty from a trailing newline).
    crlf_text = "alpha\r\nbeta\r\ngamma\r\n"
    capped, truncated, _ = apply_caps(crlf_text, max_lines=10, max_bytes=10000)
    assert not truncated
    numbered = number_lines(capped)
    # 3 real lines → 3 numbered lines.
    assert numbered.count("\n") == 2  # "1: alpha\r\n2: beta\r\n3: gamma\r"
    assert numbered.startswith("1: alpha\r")
    assert "2: beta\r" in numbered
    assert "3: gamma\r" in numbered


def test_apply_caps_crlf_line_cap_consistent():
    # Verify apply_caps counts CRLF lines (split on "\n") consistently with
    # number_lines so that the offset footer is accurate when truncation occurs.
    crlf_text = "line1\r\nline2\r\nline3\r\nline4\r\nline5\r\n"
    capped, truncated, next_offset = apply_caps(crlf_text, max_lines=3, max_bytes=100000)
    assert truncated is True
    assert next_offset == 4
    assert "use offset=4 to continue" in capped


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


def test_is_binary_multibyte_utf8_korean_not_binary():
    # Multibyte UTF-8 characters must NOT be treated as binary — every byte of
    # a valid UTF-8 sequence decodes to a printable codepoint.
    assert is_binary("안녕하세요".encode("utf-8")) is False


def test_is_binary_emoji_utf8_not_binary():
    # 4-byte emoji sequences are valid UTF-8 and must not trigger the heuristic.
    assert is_binary("hello 😀".encode("utf-8")) is False


def test_is_binary_control_char_heavy_is_flagged():
    # A sample dominated by raw control-character bytes (SOH–US, i.e. 0x01–0x1f
    # excluding whitespace) IS treated as binary.  The heuristic flags > 30 %
    # non-printable bytes.  Document the behaviour explicitly so future changes
    # to the threshold are intentional.
    # bytes(range(1, 32)) contains 31 control chars; repeated 4× = 124 bytes,
    # ~84 % non-printable — well above the 0.3 threshold.
    control_heavy = bytes(range(1, 32)) * 4
    assert is_binary(control_heavy) is True


def test_is_binary_ansi_escape_not_flagged():
    # NOTE: sparse ANSI sequences (e.g. \x1b[31m — only 1/5 bytes non-printable)
    # do NOT cross the 0.3 non-printable threshold and are therefore NOT flagged
    # as binary.  This is intentional: coloured terminal output is valid text.
    # The threshold is designed to catch densely packed binary data, not log files
    # with occasional colour codes.
    sparse_ansi = b"\x1b[31m" * 40  # 20 % non-printable — below threshold
    assert is_binary(sparse_ansi) is False


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
