# tests/egress_proxy/test_evidence.py
from magi_agent.egress_proxy.evidence import egress_proxy_record


def test_record_is_redacted_and_tagged():
    rec = egress_proxy_record(call_class="bash_subprocess")
    assert rec["evidence_source"] == "gate5b_egress_proxy"
    assert rec["call_class"] == "bash_subprocess"
    # no raw secrets / urls / auth in the record
    serialized = str(rec)
    assert "Proxy-Authorization" not in serialized
    assert "http://" not in serialized


def test_record_emit_never_raises():
    # best-effort: a broken sink must not break the tool call
    egress_proxy_record(
        call_class="web_fetch",
        sink=lambda _: (_ for _ in ()).throw(RuntimeError()),
    )
