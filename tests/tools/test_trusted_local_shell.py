from magi_agent.tools.safety import _decompose_shell_segments, _segment_is_read_safe


def test_splits_on_pipe_and_semicolon_and_and():
    assert _decompose_shell_segments("grep -n foo bar.py | head -30") == [
        "grep -n foo bar.py",
        "head -30",
    ]
    assert _decompose_shell_segments("cat a.py; grep b a.py") == ["cat a.py", "grep b a.py"]
    assert _decompose_shell_segments("ls && pwd || echo x") == ["ls", "pwd", "echo x"]


def test_operators_inside_quotes_are_not_split():
    assert _decompose_shell_segments("grep -n 'union\\|combinator' f.py | head") == [
        "grep -n 'union\\|combinator' f.py",
        "head",
    ]
    assert _decompose_shell_segments('echo "a | b ; c"') == ['echo "a | b ; c"']


def test_returns_none_on_command_substitution_or_redirect():
    assert _decompose_shell_segments("grep x $(cat list)") is None
    assert _decompose_shell_segments("echo hi > /etc/passwd") is None
    assert _decompose_shell_segments("cat `whoami`") is None


def test_read_safe_segments():
    assert _segment_is_read_safe("grep -n foo bar.py") is True
    assert _segment_is_read_safe("head -30") is True
    assert _segment_is_read_safe("cat django/db/models/sql/query.py") is True
    assert _segment_is_read_safe("find django -name '*.py'") is True


def test_unsafe_segments():
    assert _segment_is_read_safe("rm -rf /") is False
    assert _segment_is_read_safe("sed -i s/a/b/ f.py") is False
    assert _segment_is_read_safe("curl http://x") is False
    assert _segment_is_read_safe("python -c 'import os'") is False
    assert _segment_is_read_safe("") is False
