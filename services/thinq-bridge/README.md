# HESTIA ThinQ 브리지

LG ThinQ Connect Open API 의 기기 상태를 `hestia/device/{vid}/state` 로,
`hestia/device/{vid}/cmd` 를 ThinQ 제어 요청으로 옮긴다.

엔진은 이 기기가 ESP32 인지 Tasmota 인지 ThinQ 인지 모른다 — 세 경로가
같은 토픽 계약을 쓴다. `messages.py` 의
`SOURCE = {"thinq", "mock", "esp32", "tasmota"}` 가 그 계약이다.

## 무엇이 검증됐고 무엇이 안 됐는가

|                           |                                                      |
| ------------------------- | ---------------------------------------------------- |
| 변환기 (상태·제어 양방향) | 공식 문서의 응답으로 테스트 검증                     |
| 공식 SDK 호출 서명        | `tests/test_sdk_contract.py` 가 실제 SDK에 대해 확인 |
| MQTT 발행 → 엔진 수신     | `--replay` 로 로컬 확인                              |
| **실물 LG 가전**          | **없다. `--live` 는 가전으로 검증하지 못했다**       |

과장하지 않기 위해 적어둔다. 이 브리지는 **붙일 자리가 이미 있었다**는
것을 보이는 코드이고, 실제 가전 연동은 PAT 와 가전을 꽂는 일만 남았다.

## replay — 가전 없이 돌려보기

```bash
python -m hestia_thinq.runner --replay samples/air_purifier_status.json --vid vd-12
```

`hestia/device/vd-12/state` 로 한 건 발행하고 끝난다. 모니터 입력창에
`[DEVICE] 공기청정기 ... ON  PM2.5 9` 가 찍힌다.

주의: 시뮬레이터 기본값이 `--owned-by mock,thinq` 라 vd-12 를 둘이
발행한다. 같이 돌릴 때는 시뮬레이터를 `--owned-by mock` 으로 띄우거나,
브리지를 따로 한 번만 돌린다. `--owned-by mock` 은 쿡탑(vd-13)을
유령으로 만들어 COOKING 이 죽으므로 시연 대본과 같이 쓰지 않는다.

## live

```bash
export THINQ_PAT=...          # connect-pat.lgthinq.com 에서 발급
export THINQ_CLIENT_ID=...    # uuid4 하나를 고정해 재사용
python -m hestia_thinq.runner --live --device-id <deviceId> --vid vd-12
pip install -e '.[live]'      # thinqconnect, aiohttp
```

가전이 없어도 `--live` 를 `--device-id` 없이 돌리면 `GET /devices` 응답을
찍는다. 등록된 기기가 없으면 0대로 나오고, 그래도 인증·네트워크·응답
파싱이 살아 있다는 증거가 된다.

## 다음 단계

상태를 폴링하고 있다. ThinQ 는 AWS IoT Core MQTT 로 이벤트를 밀어주므로
푸시로 바꿀 수 있다. SDK 에 경로가 다 있다.

쿡탑(vd-13)도 같은 방법으로 붙는다 — SDK 에 `devices/cooktop.py` 가 있다.
그러면 `_cooktop_on()` 이 실물을 보게 되고 COOKING 판정이 ThinQ 를 탄다.

## 참고

- [Device API](https://thinq.developer.lge.com/en/cloud/docs/thinq-connect/api-reference/device-api/)
- [Air Purifier 기기 프로필](https://thinq.developer.lge.com/en/cloud/docs/thinq-connect/device-profile/air-purifier/)
- [PAT 발급](https://thinq.developer.lge.com/en/cloud/docs/thinq-connect/PAT-en/)
- [thinqconnect 공식 SDK](https://github.com/thinq-connect/pythinqconnect) — 1.0.14 로 확인
