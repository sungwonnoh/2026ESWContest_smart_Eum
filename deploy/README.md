# Deploy

| 장치 | 서비스 | 실행 방식 |
|---|---|---|
| RPi5 | Mosquitto, Context Engine, FastAPI Backend(`hestia-api`), TTS, Flutter 앱 | Mosquitto·엔진·Backend 는 docker 컨테이너 (엔진은 `hestia-engine.service` 로도 실행 가능), 앱은 IPK |
| RPi4 | t0 로거, Learning Engine, 날씨 서비스 | systemd (`hestia-t0-logger`, `hestia-learning.timer`, `hestia-weather`) |

시연 초기화·진행 순서는 [`demo/README.md`](demo/README.md)를 참고한다.

## RPi5 — Context Engine

- `hestia-engine.service`: `python3 -m hestia_engine.runner` 상시 실행
- `mosquitto.conf`: 브로커 설정
- `backend.env.sample`: FastAPI Backend 환경변수 예시

## RPi4 — t0 수신과 학습 (Learning Engine)

```text
RPi5 Context Engine ─ hestia/log/t0 ─▶ hestia-t0-logger ─▶ /data/hestia-learning/data/t0_log.jsonl
                                                                  │
                                         hestia-learning.timer (03:00)
                                                                  ▼
RPi5 ModelStore ◀─ hestia/model/kde (retained) ◀─ run_learning.py
```

- `hestia-t0-logger.service` 는 상시 실행한다. 세션을 유지해서(`clean_session=False`) 꺼져 있던 동안의 t0 를 브로커가 모아 두었다가 돌려준다.
- `hestia-learning.timer` 가 하루 한 번 `run_learning.py` 를 실행한다. t0 가 없으면 발행하지 않는다(직전 모델 유지).

```bash
# 코드(services/learning-engine 의 내용)를 /data/hestia-learning 에 둔다
sudo mkdir -p /etc/hestia /data/hestia-learning/data
sudo cp deploy/learning.env.example /etc/hestia/learning.env && sudo chmod 600 /etc/hestia/learning.env
sudo cp deploy/hestia-t0-logger.service deploy/hestia-learning.service deploy/hestia-learning.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now hestia-t0-logger.service hestia-learning.timer

# 확인
systemctl status hestia-t0-logger
systemctl list-timers hestia-learning
journalctl -u hestia-learning -n 20            # 마지막 학습 결과
sudo systemctl start hestia-learning           # 지금 바로 한 번 실행
```

## RPi4 — 날씨 (Weather)

- `hestia-weather.service`: `python3 -m hestia_weather.runner` 상시 실행, `hestia/external/weather` 발행
- 환경변수는 `weather.env.example` 참고. 자세한 내용은 [`services/weather/README.md`](../services/weather/README.md)
