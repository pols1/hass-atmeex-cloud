"""Куда отправлять команды: в облако или напрямую устройству.

Интерфейс повторяет сеттеры AtmeexApi, поэтому сущности вызывают его так же,
как раньше вызывали облако, и ничего не знают о том, каким путём ушла команда.

Политика по умолчанию — облако первично. Причина не в надёжности канала, а в
согласованности: приложение вендора и облачные сценарии читают состояние из
облака, и если писать мимо него, они разъедутся с Home Assistant. Локальная
запись включается там, где облако недоступно, — тогда бризер слушается дома,
даже когда у вендора авария.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

_LOGGER = logging.getLogger(__name__)

WRITE_CLOUD_FIRST = "cloud_first"
WRITE_CLOUD_ONLY = "cloud_only"
WRITE_LOCAL_FIRST = "local_first"
WRITE_MODES = [WRITE_CLOUD_FIRST, WRITE_CLOUD_ONLY, WRITE_LOCAL_FIRST]


class AtmeexCommander:
    """Отправка уставок с выбором пути."""

    def __init__(
        self,
        api: Any,
        *,
        channel_getter: Callable[[], Any | None],
        mac_getter: Callable[[int | str], str | None],
        mode: str = WRITE_CLOUD_FIRST,
    ) -> None:
        self._api = api
        self._channel = channel_getter
        self._mac = mac_getter
        self._mode = mode

    # ------------------------------------------------------------------
    # Ядро
    # ------------------------------------------------------------------

    async def set_device_params(self, device_id: int | str, **params: Any) -> Any:
        clean = {k: v for k, v in params.items() if v is not None}
        if not clean:
            return None

        if self._mode == WRITE_LOCAL_FIRST and await self._send_local(device_id, clean):
            return None

        if self._mode in (WRITE_CLOUD_FIRST, WRITE_CLOUD_ONLY, WRITE_LOCAL_FIRST):
            try:
                return await self._api.set_device_params(device_id, **clean)
            except Exception as err:  # noqa: BLE001
                # Тип ошибки здесь не важен: любая означает «облако команду не
                # приняло». Разбираться, что именно случилось, — дело api.py,
                # а нам нужно решить, идти ли локальным путём. Заодно модуль
                # остаётся без зависимостей и проверяется тестами.
                if self._mode == WRITE_CLOUD_ONLY:
                    raise
                _LOGGER.warning(
                    "Atmeex: облако не приняло команду для %s (%s) — пробую "
                    "локальный канал",
                    device_id,
                    err,
                )
                if await self._send_local(device_id, clean):
                    return None
                raise

        return None

    async def _send_local(self, device_id: int | str, params: dict[str, Any]) -> bool:
        channel = self._channel()
        if channel is None:
            return False
        mac = self._mac(device_id)
        if not mac or not channel.is_connected(mac):
            return False
        ok = await channel.async_send_params(mac, params)
        if ok:
            _LOGGER.info(
                "Atmeex: команда для %s отправлена локальным каналом: %s",
                device_id,
                ", ".join(sorted(params)),
            )
        return ok

    # ------------------------------------------------------------------
    # Те же обёртки, что у облачного клиента
    # ------------------------------------------------------------------

    async def set_power(self, device_id: int | str, on: bool) -> Any:
        return await self.set_device_params(device_id, u_pwr_on=bool(on))

    async def set_fan_speed(self, device_id: int | str, speed: int) -> Any:
        speed = max(1, min(7, int(speed)))
        return await self.set_device_params(device_id, u_fan_speed=speed)

    async def set_brizer_mode(self, device_id: int | str, mode_idx: int) -> Any:
        mode_idx = max(0, min(3, int(mode_idx)))
        return await self.set_device_params(device_id, u_damp_pos=mode_idx)

    async def set_target_temperature(
        self, device_id: int | str, temperature_c: float
    ) -> Any:
        # Устройство и облако считают в десятых градуса: 205 = 20.5 °C.
        return await self.set_device_params(
            device_id, u_temp_room=int(round(float(temperature_c) * 10))
        )

    async def set_humid_stage(self, device_id: int | str, stage: int) -> Any:
        stage = max(0, min(3, int(stage)))
        return await self.set_device_params(device_id, u_hum_stg=stage)

    async def set_auto_mode(self, device_id: int | str, auto: bool) -> Any:
        return await self.set_device_params(device_id, u_auto=bool(auto))

    async def set_night_mode(self, device_id: int | str, night: bool) -> Any:
        return await self.set_device_params(device_id, u_night=bool(night))

    async def set_cool_mode(self, device_id: int | str, cool: bool) -> Any:
        return await self.set_device_params(device_id, u_cool_mode=bool(cool))

    def __getattr__(self, item: str) -> Any:
        """Всё остальное — как у облачного клиента (get_devices и прочее)."""
        return getattr(self._api, item)
