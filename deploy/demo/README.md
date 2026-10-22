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

WSL 에서 한 번에 (원격은 ATLAS OS `/bin/sh`, 스크립트는 ssh 표준 입력으로 보낸다):

```sh
cd deploy/demo
DRY_RUN=1 ./run_all.sh     # 계획만 — 지울 retained · 옮길 파일 · 백업 경로 · 끌 서비스
./run_all.sh               # 실제 실행. 하나라도 실패하면 그 자리에서 멈춘다
```

`run_all.sh` 가 하는 순서:

```text
RPi5 root@172.20.10.4  rpi5_reset.sh    계획 출력 → 엔진·Backend 정지 → hestia/# retained 삭제 → DB·t0 로그 백업
RPi4 root@172.20.10.2  rpi4_prepare.sh  실측 끄기 → p2 로그 확인 → p2 모델 발행 → 끼니 피크 확인 (11:00 · 16:45)
RPi5                   rpi5_start.sh    브로커·모델 확인 → sim/clock 정리 → 엔진 재연결 검사 → 엔진 → Backend
(앱)                                    최초 설정 · 복약 등록 — 터미널이면 Enter 를 기다린다 (SKIP_PAUSE=1 로 건너뜀)
RPi5                   check.sh         컨테이너 · 엔진 CPU · 모델 · 복약 · sim/clock · API
```

- MQTT CLI 는 RPi5 의 `mosquitto` 컨테이너 안의 것을, JSON 해석은 Backend 컨테이너(RPi5)·학습 이미지(RPi4)의
  python 을 쓴다. 호스트에 bash · python · mosquitto 클라이언트가 없어도 된다.
- 이름·경로가 다르면 로컬 환경변수로 넘긴다. `run_all.sh` 가 원격 스크립트 앞에 붙여 보낸다.

```sh
ENGINE_NAME=ce BACKEND_NAME=api BACKEND_DB=/data/hestia.db ./run_all.sh
RPI5=root@172.20.10.5 RPI4=root@172.20.10.3 ./run_all.sh
```

하나씩 돌릴 때:

```sh
ssh -T -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null root@172.20.10.4 sh -s < rpi5_reset.sh
```

### 엔진 재연결 버그 대비

현재 엔진(`runner.run`)은 기동 때 브로커에 못 붙으면 다시 붙지 않고 CPU 100% 로 돈다. `rpi5_start.sh` 는

1. 엔진 이미지의 `Runner` 소스에 `reconnect(` 가 있는지 본다 — 없으면 경고 (`STRICT_ENGINE_FIX=1` 이면 중단)
2. 브로커가 연결을 받는 것을 확인한 뒤에만 엔진을 켠다
3. `CONNECT_WAIT`(기본 20)초 안에 엔진 로그에 `연결됨` 이 없으면 엔진을 다시 멈추고 중단한다

수정된 이미지가 아니면 **시연 중 mosquitto 를 재시작하지 않는다.** 했다면 엔진도 재시작한다.

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

```sh
docker exec mosquitto sh -c 'grep -rn persistence /mosquitto/config/'
```

## 시연이 끝나면

```sh
# RPi5 — 시연 상태 정리
ssh -T -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null root@172.20.10.4 sh -s < rpi5_reset.sh
# RPi4 — rpi4_prepare.sh 가 마지막에 출력한 "다시 켤 것" 을 실행한다
# (모델은 실데이터가 쌓여 학습될 때까지 없다 = 식후 알림 없음)
```
