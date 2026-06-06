from benchmarks.swebench.container import InferenceResult, instance_image
from benchmarks.swebench.dataset import Instance


def _inst(iid: str) -> Instance:
    return Instance(iid, "r/p", "abc", "fix", "1.0")


def test_instance_image_slug():
    img = instance_image(_inst("astropy__astropy-12907"))
    assert img == "swebench/sweb.eval.x86_64.astropy_1776_astropy-12907:latest"


def test_inference_result_fields():
    r = InferenceResult("a__b-1", "PATCH", "log")
    assert (r.instance_id, r.patch, r.log) == ("a__b-1", "PATCH", "log")
