from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

import aiohttp

_LOGGER = logging.getLogger(__name__)


class ApiError(Exception):
    """Base API error."""


class ApiAuthError(ApiError):
    """Auth/permission error (needs reauth)."""


@dataclass
class Tokens:
    access_token: str
    refresh_token: str


class AtmeexApi:
    """
    Atmeex Cloud API wrapper with:
    - JWT access token
    - refresh_token re-auth flow (grant_type=refresh_token)
    - auto refresh before requests (based on JWT exp)
    - retry once on 401/403
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        email: str,
        password: str,
        base_url: str = "https://api.iot.atmeex.com",
        *,
        access_token: str | None = None,
        refresh_token: str | None = None,
        token_update_cb: Callable[[Tokens], None] | None = None,
    ) -> None:
        self._session = session
        self._email = email
        self._password = password
        self._base_url = base_url.rstrip("/")
        self._access_token = access_token
        self._refresh_token = refresh_token
        self._token_update_cb = token_update_cb

        # чтобы не было гонок при параллельных запросах
        self._auth_lock = asyncio.Lock()

    @property
    def base_url(self) -> str:
        """Адрес облака — нужен для внятных сообщений об ошибках."""
        return self._base_url

    # ---------------------------
    # JWT helpers
    # ---------------------------

    def _jwt_expired(self, leeway_sec: int = 30) -> bool:
        """
        True если токен истёк или скоро истечёт.
        exp берём из payload JWT без верификации подписи (это ОК для exp).
        """
        if not self._access_token:
            return True
        try:
            # jwt может отсутствовать как dependency — но в HA обычно есть PyJWT
            import jwt  # type: ignore

            payload = jwt.decode(self._access_token, options={"verify_signature": False})
            exp = int(payload.get("exp", 0))
            return exp <= int(time.time()) + leeway_sec
        except Exception as e:
            _LOGGER.debug("JWT exp decode failed, treating as expired: %s", e)
            return True

    def _set_tokens(self, access_token: str, refresh_token: str) -> None:
        self._access_token = access_token
        self._refresh_token = refresh_token
        if self._token_update_cb:
            try:
                self._token_update_cb(Tokens(access_token=access_token, refresh_token=refresh_token))
            except Exception as e:
                _LOGGER.warning("token_update_cb failed: %s", e)

    # ---------------------------
    # Auth flows
    # ---------------------------

    async def _signin_basic(self) -> None:
        """
        Первичная аутентификация (email/password).
        По swagger: grant_type=basic.
        """
        url = f"{self._base_url}/auth/signin"
        payload = {
            "grant_type": "basic",
            "email": self._email,
            "password": self._password,
        }
        _LOGGER.info("Atmeex: requesting new token via %s", url)

        async with self._session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            text = await resp.text()
            _LOGGER.debug("Atmeex auth response: status=%s, body=%s", resp.status, text[:1000])

            if resp.status != 200:
                raise ApiAuthError(f"signin failed {resp.status}: {text[:500]}")

            data = json.loads(text)
            access = data.get("access_token")
            refresh = data.get("refresh_token")
            if not access or not refresh:
                raise ApiAuthError(f"signin: missing tokens: {data}")

            self._set_tokens(access, refresh)
            _LOGGER.info("Atmeex: authenticated, token_type=Bearer, expires_in=None (JWT exp is handled by server)")

    async def _signin_refresh(self) -> None:
        """
        Реаутентификация по refresh_token.
        По swagger: POST /auth/signin с grant_type=refresh_token.
        """
        if not self._refresh_token:
            raise ApiAuthError("refresh_token is missing")

        url = f"{self._base_url}/auth/signin"
        payload = {
            "grant_type": "refresh_token",
            "refresh_token": self._refresh_token,
        }
        _LOGGER.info("Atmeex: refreshing token via %s", url)

        async with self._session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            text = await resp.text()
            _LOGGER.debug("Atmeex refresh response: status=%s, body=%s", resp.status, text[:1000])

            if resp.status != 200:
                # refresh умер → нужна реавторизация руками
                raise ApiAuthError(f"refresh failed {resp.status}: {text[:500]}")

            data = json.loads(text)
            access = data.get("access_token")
            refresh = data.get("refresh_token") or self._refresh_token
            if not access or not refresh:
                raise ApiAuthError(f"refresh: missing tokens: {data}")

            self._set_tokens(access, refresh)

    async def ensure_token(self) -> None:
        """
        Гарантирует валидный access_token.
        - если нет токенов → basic signin
        - если access истёк → refresh_token flow
        """
        async with self._auth_lock:
            # пока ждали lock, другой корутин мог обновить
            if self._access_token and not self._jwt_expired():
                return

            if self._refresh_token:
                try:
                    await self._signin_refresh()
                    return
                except ApiAuthError as e:
                    _LOGGER.warning("Atmeex: refresh failed, will try basic signin: %s", e)

            await self._signin_basic()

    # ---------------------------
    # Request wrapper
    # ---------------------------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_data: Any | None = None,
        _retry: bool = True,
    ) -> Any:
        await self.ensure_token()

        url = f"{self._base_url}{path}"
        headers = {"Authorization": f"Bearer {self._access_token}"}

        _LOGGER.debug("Atmeex API request: %s %s params=%s json=%s", method, url, params, json_data)

        async with self._session.request(
            method,
            url,
            params=params,
            json=json_data,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            text = await resp.text()
            _LOGGER.debug("Atmeex API response: %s %s -> %s, body=%s", method, path, resp.status, text[:1000])

            # если токен протух/отозван — пробуем 1 раз обновить и повторить
            if resp.status in (401, 403):
                if _retry:
                    async with self._auth_lock:
                        # обновим токен (refresh → basic fallback внутри ensure_token)
                        await self.ensure_token()
                    return await self._request(
                        method,
                        path,
                        params=params,
                        json_data=json_data,
                        _retry=False,
                    )
                raise ApiAuthError(f"{method} {path} unauthorized {resp.status}: {text[:200]}")

            if resp.status >= 400:
                raise ApiError(f"{method} {path} failed {resp.status}: {text[:500]}")

            if not text:
                return None

            try:
                return json.loads(text)
            except Exception:
                return text

    # ---------------------------
    # Public API methods
    # ---------------------------

    async def get_devices(self, with_condition: bool = True) -> list[dict[str, Any]]:
        params = {"with_condition": 1} if with_condition else None
        data = await self._request("GET", "/devices", params=params)
        if isinstance(data, list):
            return data
        # иногда сервер может вернуть {"devices":[...]} — подстрахуемся
        if isinstance(data, dict) and isinstance(data.get("devices"), list):
            return data["devices"]
        raise ApiError(f"Unexpected devices payload: {type(data)} {data}")

    async def set_device_params(self, device_id: int | str, **params: Any) -> Any:
        """
        По swagger: PUT /devices/{id}/params
        Тело = SetDeviceParamsRequest (u_*)
        """
        did = int(device_id)
        clean = {k: v for k, v in params.items() if v is not None}
        return await self._request("PUT", f"/devices/{did}/params", json_data=clean)

    # Удобные обёртки под climate.py
    async def set_power(self, device_id: int | str, on: bool) -> Any:
        return await self.set_device_params(device_id, u_pwr_on=bool(on))

    async def set_fan_speed(self, device_id: int | str, speed: int) -> Any:
        # speed должен быть 1..7
        speed = max(1, min(7, int(speed)))
        return await self.set_device_params(device_id, u_fan_speed=speed)

    async def set_brizer_mode(self, device_id: int | str, mode_idx: int) -> Any:
        mode_idx = max(0, min(3, int(mode_idx)))
        return await self.set_device_params(device_id, u_damp_pos=mode_idx)

    async def set_target_temperature(self, device_id: int | str, temperature_c: float) -> Any:
        # API ждёт десятые градуса, судя по твоим данным u_temp_room=105 => 10.5
        value = int(round(float(temperature_c) * 10))
        return await self.set_device_params(device_id, u_temp_room=value)

    async def set_humid_stage(self, device_id: int | str, stage: int) -> Any:
        stage = max(0, min(3, int(stage)))
        return await self.set_device_params(device_id, u_hum_stg=stage)

    async def set_auto_mode(self, device_id: int | str, auto: bool) -> Any:
        return await self.set_device_params(device_id, u_auto=bool(auto))

    async def set_night_mode(self, device_id: int | str, night: bool) -> Any:
        return await self.set_device_params(device_id, u_night=bool(night))

    async def set_cool_mode(self, device_id: int | str, cool: bool) -> Any:
        return await self.set_device_params(device_id, u_cool_mode=bool(cool))
