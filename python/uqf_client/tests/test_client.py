from uqf_client import UqfClient


def test_import_and_construct_without_connecting():
    client = UqfClient(port=1)
    assert client is not None


def test_call_uqf_function_over_ipc(q_port):
    with UqfClient(port=q_port) as client:
        fwd = client.call("qfwd", "fwd_simple", 1.10, 0.05, 0.02, 1)
        assert abs(fwd - 1.132353) < 1e-5

        rate = client.sync(".qfwd.invert_rate", 2.0)
        assert abs(rate - 0.5) < 1e-9
