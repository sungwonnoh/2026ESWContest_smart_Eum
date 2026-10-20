from fastapi import APIRouter, Depends, HTTPException, status

from ..dependencies import Container, get_container
from ..schemas.weather import Weather, WeatherDemoIn

router = APIRouter(tags=["weather"])


@router.get("/weather", response_model=Weather)
def current_weather(c: Container = Depends(get_container)) -> Weather:
    """RPi4 가 보낸 최신 외부 날씨. 아직 받지 않았으면 404."""
    weather = c.weather.latest()
    if weather is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "아직 날씨를 받지 못했습니다.")
    return weather


@router.put("/weather/demo", status_code=status.HTTP_202_ACCEPTED)
def set_weather_demo(body: WeatherDemoIn, c: Container = Depends(get_container)) -> dict:
    """시연 모드: 폭염 특보와 여름 기온·습도를 RPi4 가 덧씌워 보낸다 (1시간 뒤 자동 해제).

    바뀐 날씨는 hestia/external/weather 로 다시 와서 GET /weather 에 demo=true 로 보인다.
    """
    if not c.weather.set_demo(body.heat):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "MQTT 연결이 없어 보내지 못했습니다.")
    return {"heat": body.heat}
