"""t0 로그로 학습해 hestia/model/kde 를 발행한다 (RPi4, 주기 실행용).

    python3 run_learning.py [--t0-jsonl PATH]

t0 가 아직 없으면 발행하지 않는다. mqtt_publisher 를 그대로 쓰면 입력이 없을 때
기본값(Aruba 데이터)으로 학습해 버려서, 실제 사용자에게 남의 패턴이 나간다.
발행하지 않으면 RPi5 는 직전 모델을 그대로 쓴다 (retained).
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from baseline import build_model, samples_from_args, add_input_arguments
from model_payload import build_kde_payload
from mqtt_publisher import MQTT_HOST, MQTT_PORT, PublishError, publish_kde_model
from t0_logger import DEFAULT_PATH

log = logging.getLogger(__name__)


def run(args, publish=publish_kde_model) -> int:
    """0 이면 정상(발행했거나 발행할 게 없음). 1 이면 실패 — systemd 에 실패로 남는다."""
    if args.t0_jsonl is None:
        args.t0_jsonl = DEFAULT_PATH

    if not Path(args.t0_jsonl).exists():
        log.info("t0 로그 없음(%s) — 발행하지 않음", args.t0_jsonl)
        return 0

    samples = samples_from_args(args)
    if not samples:
        log.info("학습할 표본이 없음 — 발행하지 않음")
        return 0

    model = build_model(samples)
    if not model["distributions"]:
        log.info("학습 가능한 분포가 없음 %s — 발행하지 않음", model.get("skipped"))
        return 0

    try:
        sent = publish(
            build_kde_payload(model),
            host=args.host,
            port=args.port,
        )
    except (PublishError, OSError) as exc:
        log.error("모델 발행 실패: %s", exc)
        return 1

    log.info("모델 발행 sample_days=%s 분포=%s", sent["sample_days"], sorted(sent["distributions"]))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="t0 로그 학습 → hestia/model/kde 발행")
    add_input_arguments(parser)
    parser.add_argument("--host", default=MQTT_HOST)
    parser.add_argument("--port", type=int, default=MQTT_PORT)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
