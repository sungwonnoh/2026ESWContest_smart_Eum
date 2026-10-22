# 시연 초기화·진행 (p2 = 실제 사용자로 간주)

시연 기간에는 실측을 끄고, 합성 p2(78세 독거, 점심 11시·저녁 17시, 야간뇨 없음) 91일치를
"그동안 쌓인 실제 기록"으로 쓴다. 매번 같은 결과가 나오도록 **상태가 남는 곳을 전부 비우고
p2 모델 하나만 넣은 뒤** 시작한다.

## 상태가 남는 곳

| 위치 | 무엇 | 초기화 |
|---|---|---|
| RPi5 Mosquitto retained `hestia/#` | 모델, 복약 목록, 센서·가전 상태, context, 프로파일, **`hestia/sim/clock`** | `rpi5_reset.sh` 가 전부 지운다 |
| RPi5 Backend SQLite (`HESTIA_DB_PATH`) | 공간·가전·복약·알림·생활 기록 | 백업 폴더로 옮긴다 |
| RPi5 엔진 `/data/hestia/t0_log.jsonl` | 엔진이 쓴 식사·수면 t0 | 백업 폴더로 옮긴다 |
| RPi5 엔진 메모리 | 오늘 식사 종료, 보낸 알림 | 재시작하면 비워진다 |
| RPi4 `hestia-learning.timer` | 부팅 직후(Persistent)·매일 03:00 **실데이터 모델로 p2 모델을 덮어씀** | 시연 기간 끈다 |
| RPi4 `hestia-t0-logger` | 시연 이벤트를 실데이터 로그에 쌓음 | 시연 기간 끈다 |
| RPi4 날씨 서비스 | 폭염 시연 모드 (1시간 뒤 자동 해제) | 재시작하면 꺼진다 |

`hestia/sim/clock` 은 시계 점프 오프셋이고 retained 다. 남아 있으면 `--sim-clock` 으로 켠 엔진이
기동 직후 지난 시연의 가상 시각으로 넘어간다. `simulate.py` 의 retained 정리(`_clear_retained`)는 이 토픽을
일부러 남기므로 이 스크립트로 지운다. (`Retained_Debug_Request.md` 의 "시각이 과거로 바뀜" 증상과
관련 있는지는 확인하지 않았다)

## 순서

```text
RPi5  ./rpi5_reset.sh      서비스 정지 → retained 전부 삭제 → DB·t0 로그 백업
RPi4  ./rpi4_prepare.sh    실측 끄기 → p2 모델 발행 → 끼니 피크 확인 (11:00 · 16:45)
RPi5  ./rpi5_start.sh      모델 확인 → sim/clock 정리 → 엔진 → Backend
앱    최초 설정             공간 6개 · 가전 (demo.toml 과 같은 공간에만 둘 수 있다)
앱    복약 등록             식후 약은 여기서 — 등록 시각 전에 끝난 오늘 식사는 건너뛴다
RPi5  ./check.sh           모델 91일·피크 2개 / 복약 목록 / sim/clock 없음 / API 200
```

경로·이름이 다르면 환경변수로 넘긴다.

```bash
BACKEND_DB=/data/hestia.db BACKEND_NAME=hestia-api ./rpi5_reset.sh
BROKER=rpi5.local APP_DIR=/data/hestia-learning ./rpi4_prepare.sh
```

## 시연 중 지킬 것

- **식사 장면 전에 약을 등록하고, 식사 뒤에는 약을 추가·수정하지 않는다.** 엔진은 복약 목록 전체의
  `sent_ts` 하나로 "등록 전에 끝난 식사"를 거른다. 식사 뒤에 다른 약을 고치면 그 식사의 식후 알림이 전부 빠진다.
  Backend 재시작도 같은 효과다.
- **식사가 끝난 뒤 30분 안에 엔진을 재시작하지 않는다.** 식사 종료는 메모리에만 있다.
- 식후 약은 EATING 이 한 번은 찍혀야 끼니로 친다. 조리만 하고 나가면 복약 알림이 없다.
- 폭염 특보는 앱 설정 > 시스템 정보 > 폭염 특보 시연 (1시간 뒤 자동 해제).

## p2 기준 알림 시각

| 장면 | 조건 | 알림 |
|---|---|---|
| 식후 복약 | 주방을 떠난 시각 + 30분 | 점심 보통 12시 전후, 저녁 18시 전후 |
| 식사 알림 | 끼니 구간에 식사가 없을 때 | 점심 13:15, 저녁 19:00 (피크 11:00·16:45 의 꼬리 5%) |
| 정해진 시각 복약 | 등록한 시각 | KDE 와 무관 |

## Mosquitto persistence

retained 는 브로커가 디스크에 저장해야 RPi5 재부팅 뒤에도 남는다. `/etc/mosquitto/mosquitto.conf`
(또는 `conf.d/`)에 `persistence true` 가 없으면 RPi5 를 켤 때마다 `rpi4_prepare.sh` 의 모델 발행을 다시 해야 한다.

```bash
grep -rn persistence /etc/mosquitto/
```

## 시연이 끝나면

```bash
# RPi5 — 시연 상태 정리
./rpi5_reset.sh
# RPi4 — 실측 다시 켜기 (모델은 실데이터가 쌓여 학습될 때까지 없다 = 식후 알림 없음)
sudo systemctl enable --now hestia-learning.timer hestia-t0-logger
```
