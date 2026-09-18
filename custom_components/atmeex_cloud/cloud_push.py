"""Push-канал облака Atmeex: wss://ws.iot.atmeex.com.

Вебсокет, которым пользуется приложение вендора. В Swagger его нет; всё
ниже снято трёхчасовой записью 18.09.2026 (заметки протокола, раздел
«Вебсокет приложения»):

* Авторизация — только заголовок Authorization при рукопожатии. Токен в
  query, в подпротоколе или первым сообщением сервер молча отвергает.
* Токен проверяется только при подключении: открытое соединение истечение
  JWT переживает. С просроченным токеном сервер отвечает 101, присылает
  {"type":"unauthorized","data":null} и закрывает.
* После подключения сервер сам присылает снимок condition и settings по всем
  устройствам аккаунта, дальше condition от каждого бризера раз в ~5 с и
  settings на каждое изменение уставок — из приложения или через REST.
* Сервер сам ping не шлёт; соединение может повиснуть на пару минут или
  оборваться без close-кадра. Живость проверяет клиент: heartbeat aiohttp.

Модуль не зависит от Home Assistant и от aiohttp при импорте — aiohttp
нужен только готовому соединителю, — поэтому проверяется тестами на голом
Python. Кадры приходят в тех же терминах, что у локального канала: state
ложится в condition, setp — в settings.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import random
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine
from dataclasses import dataclass
from typing import Any

_LOGGER = logging.getLogger(__name__)

PUSH_URL = "wss://ws.iot.atmeex.com/"

# Ping после 30 с без входящих кадров, pong ждётся 15 с (heartbeat / 2 в
# aiohttp). Провал вроде записанного 153-секундного обнаружится за ~45 с.
HEARTBEAT = 30.0

BACKOFF_MIN = 5.0
BACKOFF_MAX = 300.0

# Сколько неудачных подключений подряд терпеть молча, прежде чем сказать в лог.
QUIET_FAILURES = 3

# В снимке при подключении эти поля приходят числами 0/1, в живых кадрах —
# true/false. Приводим к одному виду.
BOOL_FIELDS = ("pwr_on", "no_water")

# Кадр от соединителя: ("text", str) | ("error", исключение) | ("closed", код).
Frame = tuple[str, Any]
Connector = Callable[[str, str, float], contextlib.AbstractAsyncContextManager[AsyncIterator[Frame]]]
TokenGetter = Callable[[str | None], Awaitable[str]]


class Unauthorized(Exception):
    """Сервер отверг токен: кадр {"type":"unauthorized"}."""


@dataclass(frozen=True)
class PushUpdate:
    kind: str  # "state" | "setp" — как у локального канала
    device_id: str | None
    mac: str  # как пришёл, с суффиксом ":0"; нормализует вызывающий
    payload: dict[str, Any]


_KINDS = {"condition": ("condition", "state"), "settings": ("settings", "setp")}


def parse_message(raw: str | dict[str, Any]) -> list[PushUpdate]:
    """Разобрать сообщение сервера в обновления по устройствам.

    Неизвестные типы дают пустой список, «unauthorized» — исключение.
    Битый JSON — ValueError.
    """
    msg = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(msg, dict):
        return []

    kind = msg.get("type")
    if kind == "unauthorized":
        raise Unauthorized
    if kind not in _KINDS:
        return []

    field, update_kind = _KINDS[kind]
    items = msg.get("data")
    if not isinstance(items, list):
        return []

    updates: list[PushUpdate] = []
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get(field), dict):
            continue
        payload = dict(item[field])
        for name in BOOL_FIELDS:
            value = payload.get(name)
            if isinstance(value, int):  # bool тоже int — bool(True) остаётся True
                payload[name] = bool(value)
        did = item.get("id", payload.get("device_id"))
        updates.append(
            PushUpdate(
                kind=update_kind,
                device_id=None if did is None else str(did),
                mac=str(item.get("mac") or ""),
                payload=payload,
            )
        )
    return updates


def aiohttp_connector(session: Any) -> Connector:
    """Соединитель на aiohttp — тот, что работает внутри Home Assistant."""

    @contextlib.asynccontextmanager
    async def connect(url: str, token: str, heartbeat: float) -> AsyncIterator[AsyncIterator[Frame]]:
        import aiohttp  # здесь, а не наверху: тесты идут без aiohttp

        async with session.ws_connect(
            url,
            headers={"Authorization": f"Bearer {token}"},
            heartbeat=heartbeat,
        ) as ws:

            async def frames() -> AsyncIterator[Frame]:
                # Итерация по ws заканчивается на CLOSE/CLOSING/CLOSED, в том
                # числе при обрыве TCP без close-кадра. Пропущенный pong
                # приходит как ERROR с ServerTimeoutError.
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        yield "text", msg.data
                    elif msg.type == aiohttp.WSMsgType.ERROR:
                        yield "error", ws.exception()
                        return
                yield "closed", ws.close_code

            yield frames()

    return connect


class AtmeexCloudPush:
    """Держит соединение с вебсокетом и переподключается сам."""

    def __init__(
        self,
        *,
        token_getter: TokenGetter,
        on_update: Callable[[PushUpdate], None],
        connector: Connector,
        url: str = PUSH_URL,
        heartbeat: float = HEARTBEAT,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        jitter: Callable[[float, float], float] = random.uniform,
    ) -> None:
        self._token_getter = token_getter
        self._on_update = on_update
        self._connector = connector
        self._url = url
        self._heartbeat = heartbeat
        self._sleep = sleep
        self._now = monotonic
        self._jitter = jitter

        self._task: asyncio.Task[None] | None = None
        self._stopping = False
        self._failures = 0
        # Последнее состояние каждого устройства и когда оно пришло.
        self._states: dict[str, tuple[float, dict[str, Any]]] = {}

        self.connected = False
        self.connections = 0
        self.last_message_at: float | None = None
        self.last_error: str | None = None

    # ---------- жизненный цикл ----------

    def start(
        self,
        create_task: Callable[[Coroutine[Any, Any, None]], asyncio.Task[None]] = asyncio.create_task,
    ) -> None:
        """Запустить. В Home Assistant передаётся задача, привязанная к записи."""
        if self._task is None:
            self._task = create_task(self._run())

    async def async_stop(self) -> None:
        self._stopping = True
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    # ---------- что знаем об устройствах ----------

    def fresh_state(self, device_id: str, max_age: float) -> dict[str, Any] | None:
        """Состояние устройства, если оно пришло не раньше max_age секунд назад."""
        entry = self._states.get(str(device_id))
        if entry is None or self._now() - entry[0] > max_age:
            return None
        return entry[1]

    def status(self) -> dict[str, Any]:
        """Для диагностики: без токенов и адресов устройств."""
        now = self._now()
        return {
            "connected": self.connected,
            "connections": self.connections,
            "seconds_since_last_message": (
                None if self.last_message_at is None else round(now - self.last_message_at, 1)
            ),
            "last_error": self.last_error,
            "devices_seen": sorted(self._states),
        }

    # ---------- основной цикл ----------

    async def _run(self) -> None:
        backoff = BACKOFF_MIN
        rejected: str | None = None
        while not self._stopping:
            got_data = False
            try:
                token = await self._token_getter(rejected)
                rejected = None
                async with self._connector(self._url, token, self._heartbeat) as frames:
                    self.connected = True
                    self.connections += 1
                    async for kind, value in frames:
                        if kind == "text":
                            try:
                                updates = parse_message(value)
                            except Unauthorized:
                                # Токен истёк или отозван. В следующий раз
                                # попросим новый, а не тот же из кэша.
                                rejected = token
                                self.last_error = "сервер отверг токен"
                                break
                            except ValueError:
                                _LOGGER.debug("Atmeex push: не JSON: %.200s", value)
                                continue
                            if updates:
                                got_data = True
                                self._deliver(updates)
                        elif kind == "error":
                            self.last_error = f"{type(value).__name__}: {value}"
                            break
                        elif kind == "closed":
                            self.last_error = f"соединение закрыто (код {value})"
                            break
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001 — любая ошибка = переподключение
                self.last_error = f"{type(err).__name__}: {err}"
            finally:
                self.connected = False

            if self._stopping:
                break

            if got_data:
                if self._failures >= QUIET_FAILURES:
                    _LOGGER.info("Atmeex push: соединение восстановлено")
                self._failures = 0
                backoff = BACKOFF_MIN
            else:
                self._failures += 1
                if self._failures == QUIET_FAILURES:
                    _LOGGER.warning(
                        "Atmeex push: не удаётся получить данные уже %s раз подряд (%s). "
                        "Интеграция продолжает опрашивать облако и будет пробовать дальше",
                        self._failures,
                        self.last_error,
                    )

            delay = backoff + self._jitter(0, backoff / 5)
            if not got_data:
                backoff = min(backoff * 2, BACKOFF_MAX)
            await self._sleep(delay)

    def _deliver(self, updates: list[PushUpdate]) -> None:
        now = self._now()
        self.last_message_at = now
        for update in updates:
            if update.kind == "state" and update.device_id:
                previous = self._states.get(update.device_id, (now, {}))[1]
                self._states[update.device_id] = (now, {**previous, **update.payload})
            try:
                self._on_update(update)
            except Exception:  # noqa: BLE001 — сбой обработчика не должен рвать соединение
                _LOGGER.exception("Atmeex push: ошибка при обработке кадра")
