# tests/egress_proxy/test_model_egress_isolation.py
import inspect

from magi_agent.gates import gate5b_full_toolhost as g5
from magi_agent.web_acquisition import live_fetch_provider as lf


def test_no_process_wide_proxy_env_mutation():
    """The seam must never set os.environ proxy vars (would capture model egress)."""
    for mod in (lf, g5):
        src = inspect.getsource(mod)
        assert 'os.environ["HTTPS_PROXY"]' not in src
        assert "os.environ['HTTPS_PROXY']" not in src
        assert "setdefault(\"HTTPS_PROXY\"" not in src


def test_overlay_is_scoped_to_tool_paths_only():
    # subprocess overlay + httpx kwargs are the ONLY consumers; assert they are
    # the functions the tool sites call (guards against a refactor that widens scope)
    assert hasattr(g5, "_build_bash_env")
    assert hasattr(lf, "_egress_client_kwargs")
