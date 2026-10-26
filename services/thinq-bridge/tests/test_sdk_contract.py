"""공식 SDK 와의 계약. SDK 가 없으면 건너뛴다.

증명하는 것과 못 하는 것을 분명히 해둔다.
  증명: 우리가 부르는 메서드가 thinqconnect 에 그 서명으로 존재하고,
        우리가 만든 제어 본문이 SDK 를 그대로 통과한다
  못 함: 실물 가전이 그 본문을 받아들이는지. 가전이 없다
"""

import inspect

import pytest

from hestia_thinq.device import air_purifier_to_thinq

thinq_api = pytest.importorskip("thinqconnect.thinq_api")


def test_methods_we_call_exist():
    api = thinq_api.ThinQApi
    for name, params in (
        ("async_get_device_list", ()),
        ("async_get_device_status", ("device_id",)),
        ("async_post_device_control", ("device_id", "payload")),
    ):
        fn = getattr(api, name, None)
        assert fn is not None, f"SDK 에 {name} 이 없다"
        sig = inspect.signature(fn).parameters
        for p in params:
            assert p in sig, f"{name} 에 {p} 인자가 없다"


def test_constructor_takes_pat_and_country():
    sig = inspect.signature(thinq_api.ThinQApi.__init__).parameters
    for p in ("session", "access_token", "country_code", "client_id"):
        assert p in sig


def test_control_body_passes_through_sdk():
    """mock_response 는 보낸 요청을 돌려준다(thinq_api.py:325).
    네트워크 없이 우리 본문이 SDK 를 통과하는 것만 확인한다."""
    import asyncio

    from aiohttp import ClientSession

    body = air_purifier_to_thinq({"power": "ON", "mode": "SILENT"})

    async def call():
        async with ClientSession() as session:
            api = thinq_api.ThinQApi(
                session=session,
                access_token="not-a-real-token",
                country_code="KR",
                client_id="hestia-contract-test",
                mock_response=True,
            )
            return await api.async_post_device_control("device-id", body)

    got = asyncio.run(call())
    assert got["body"] == body