"""Локальный канал до бризеров Atmeex.

Бризер — чистый исходящий клиент: он не слушает ни одного порта (проверено
полным сканом TCP 1-10000 и UDP-зондированием), а сам звонит на
ws.iot.atmeex.com:3001 и всю жизнь разговаривает через этот один сокет.
Поэтому единственный способ получить локальный доступ — оказаться на
приёмном конце его соединения: трафик заворачивается на Home Assistant
(статическая DNS-запись или dst-nat на роутере), а мы принимаем.

Чтобы не отбирать у пользователя приложение вендора, канал работает
ПРОЗРАЧНО: устройство ↔ Home Assistant ↔ облако Atmeex. Мы читаем поток,
но не вмешиваемся в него. Если облако недоступно, отвечаем устройству сами —
тогда локальные данные продолжают идти даже при аварии у вендора.

Протокол (снят перехватом 15-16.08.2026): голый JSON по TCP, без TLS,
объекты идут вплотную без разделителя и без длины.

Устройство → сервер:
    {"id":"<MAC>:0","hello":{"fw_ver":…,"time":…,"model":"A7",…}}
    {"id":"<MAC>:0","setp":{"u_pwr_on":…,"u_fan_speed":…,…}}
    {"id":"<MAC>:0","state":{"pwr_on":…,"co2_ppm":…,"temp_room":…,…}}

Сервер → устройство:
    {"hello":true,"id":"<MAC>:0","time":"YYYY-MM-DD HH:MM:SS","time_zone":"-00:00"}
    {"id":"<MAC>:0","cmd":{"get_state":true}}

Ключевое: пока сервер не ответит на hello синхронизацией времени,
устройство молчит и телеметрию не присылает.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import socket
from datetime import datetime, timezone
from typing import Any, Callable

_LOGGER = logging.getLogger(__name__)

DEFAULT_LOCAL_PORT = 3001
UPSTREAM_HOST = "ws.iot.atmeex.com"
UPSTREAM_PORT = 3001
# Имя канала устройств заворачивается на Home Assistant — и оверрайд DNS
# действует на всех клиентов, включая сам Home Assistant. Поэтому адрес
# облака берём по имени REST-API: тот же сервер вендора, но не подменён.
UPSTREAM_FALLBACK_HOST = "api.iot.atmeex.com"

UPSTREAM_CONNECT_TIMEOUT = 10
STOP_TIMEOUT = 5
# Как часто проверять, не поднялось ли облако, пока мы обслуживаем
# устройство автономно. Бризер держит соединение сутками, поэтому без
# этой проверки авария у вендора превращалась бы в вечную изоляцию.
UPSTREAM_RETRY_INTERVAL = 60
# Опора для ресинхронизации потока: кадры устройства всегда начинаются с
# {"id", ответы облака — с {"hello" (синхронизация времени) либо тоже с {"id".
FRAME_PREFIXES = ('{"id"', '{"hello"')

# Уставка из облачного API -> команда канала. Наблюдались в перехвате
# set_pwr_on, set_fan_speed и set_cool_mode; остальные выведены по симметрии
# имён и подтверждаются первой же удачной локальной записью.
CMD_BY_PARAM = {
    "u_pwr_on": "set_pwr_on",
    "u_fan_speed": "set_fan_speed",
    "u_damp_pos": "set_damp_pos",
    "u_temp_room": "set_temp_room",
    "u_hum_stg": "set_hum_stg",
    "u_auto": "set_auto",
    "u_night": "set_night",
    "u_cool_mode": "set_cool_mode",
}
READ_CHUNK = 65536
# Дальше этого размера буфер не растёт: если мы не смогли собрать объект,
# значит поток рассинхронизирован и копить бесполезно.
MAX_BUFFER = 1 << 20


def split_json_objects(buf: str) -> tuple[list[str], str]:
    """Разрезать склеенные JSON-объекты по балансу скобок.

    Разделителя в потоке нет, поэтому считаем фигурные скобки, игнорируя
    те, что внутри строк. Возвращает готовые объекты и неразобранный хвост.
    """
    # Сор в начале отрезаем ДО разбора: иначе лишняя открывающая скобка
    # проглотит следующий настоящий кадр как вложенный объект.
    buf = resync(buf)

    out: list[str] = []
    depth = 0
    start: int | None = None
    in_str = False
    esc = False

    for i, ch in enumerate(buf):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth:
                depth -= 1
                if depth == 0 and start is not None:
                    out.append(buf[start : i + 1])
                    start = None

    rest = buf[start:] if start is not None else ""
    return out, resync(rest)


def resync(tail: str, prefixes: tuple[str, ...] = FRAME_PREFIXES) -> str:
    """Отбросить мусор в начале потока или незавершённого хвоста.

    Кадр всегда начинается с одного из `prefixes`. Если начало этому не
    соответствует, значит в поток попал сор или обрезок, и его надо
    выбросить: одна лишняя открывающая скобка иначе травит разбор
    навсегда — всё последующее вкладывается внутрь недописанного объекта
    и ни один кадр больше не выходит.

    Обрезанное начало настоящего кадра сохраняется: сравнение идёт по той
    части префикса, которая уже пришла.
    """
    while tail:
        if any(tail[: min(len(tail), len(p))] == p[: min(len(tail), len(p))] for p in prefixes):
            return tail
        nxt = tail.find("{", 1)
        if nxt < 0:
            return ""
        tail = tail[nxt:]
    return tail


def normalize_mac(device_id: str) -> str:
    """'84:1F:E8:A5:D7:A0:0' -> '84:1f:e8:a5:d7:a0'.

    Устройство представляется MAC-адресом с суффиксом ':0'; в облачном
    /devices тот же адрес лежит в поле mac без суффикса.
    """
    parts = [p for p in str(device_id).split(":") if p != ""]
    if len(parts) > 6:
        parts = parts[:6]
    return ":".join(p.lower().rjust(2, "0") for p in parts)


class AtmeexLocalChannel:
    """TCP-сервер, принимающий соединения бризеров и читающий их поток."""

    def __init__(
        self,
        port: int = DEFAULT_LOCAL_PORT,
        *,
        on_state: Callable[[str, dict[str, Any]], None] | None = None,
        on_setp: Callable[[str, dict[str, Any]], None] | None = None,
        upstream: tuple[str, int] | None = (UPSTREAM_HOST, UPSTREAM_PORT),
    ) -> None:
        self._port = port
        self._on_state = on_state
        self._on_setp = on_setp
        self._upstream = upstream
        self._server: asyncio.AbstractServer | None = None
        self._tasks: set[asyncio.Task] = set()
        # Активные соединения устройств. Их надо закрывать руками при остановке:
        # с Python 3.12 Server.wait_closed() ждёт завершения ВСЕХ обработчиков,
        # а бризер держит соединение бесконечно — выгрузка интеграции повисла бы.
        self._writers: set[asyncio.StreamWriter] = set()
        # Адреса наших собственных исходящих сокетов к облаку: по ним узнаём
        # себя, если имя облака заворачивается на нас же.
        self._own_endpoints: set[tuple[str, int]] = set()
        # Кому писать команды: mac -> (соединение, id устройства с суффиксом ':0')
        self._sessions: dict[str, tuple[asyncio.StreamWriter, str]] = {}

        # последнее увиденное по каждому устройству, ключ — нормализованный MAC
        self.states: dict[str, dict[str, Any]] = {}
        self.setpoints: dict[str, dict[str, Any]] = {}
        self.connected: dict[str, bool] = {}

    @property
    def port(self) -> int:
        return self._port

    async def async_start(self) -> None:
        self._server = await asyncio.start_server(
            self._handle_device, host="0.0.0.0", port=self._port
        )
        _LOGGER.info(
            "Atmeex: локальный канал слушает 0.0.0.0:%s, апстрим %s",
            self._port,
            f"{self._upstream[0]}:{self._upstream[1]}" if self._upstream else "выключен",
        )

    async def async_stop(self) -> None:
        if self._server is not None:
            self._server.close()

        # Сначала рвём соединения устройств, иначе wait_closed() не вернётся.
        for writer in list(self._writers):
            writer.close()
        self._writers.clear()

        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

        if self._server is not None:
            with contextlib.suppress(Exception, asyncio.TimeoutError):
                async with asyncio.timeout(STOP_TIMEOUT):
                    await self._server.wait_closed()
            self._server = None

        self.connected.clear()
        self._sessions.clear()
        _LOGGER.info("Atmeex: локальный канал остановлен")

    # ------------------------------------------------------------------
    # Обработка соединения устройства
    # ------------------------------------------------------------------

    async def _handle_device(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        peer = writer.get_extra_info("peername")
        if isinstance(peer, tuple) and tuple(peer[:2]) in self._own_endpoints:
            # Наше же встречное соединение вернулось к нам: имя облака ведёт
            # на Home Assistant. Обслуживать его нельзя — будет рекурсия.
            _LOGGER.error(
                "Atmeex: локальный канал соединился сам с собой (%s). Адрес "
                "облака указывает на Home Assistant — проверьте подмену DNS",
                peer,
            )
            writer.close()
            return
        _LOGGER.debug("Atmeex: подключилось устройство %s", peer)
        self._writers.add(writer)

        up_reader, up_writer = await self._connect_upstream()
        relay: asyncio.Task | None = None
        mac: str | None = None

        if up_writer is not None:
            relay = asyncio.create_task(self._relay_upstream(up_reader, writer))
            self._tasks.add(relay)
            relay.add_done_callback(self._tasks.discard)
        elif self._upstream is not None:
            # Обслуживаем устройство сами, но ждём возвращения облака.
            relay = asyncio.create_task(self._await_upstream_return(writer))
            self._tasks.add(relay)
            relay.add_done_callback(self._tasks.discard)

        buf = ""
        try:
            while True:
                chunk = await reader.read(READ_CHUNK)
                if not chunk:
                    break

                # Облако — источник истины, пока оно на связи: отдаём байты
                # дальше нетронутыми, чтобы приложение вендора работало.
                if up_writer is not None:
                    up_writer.write(chunk)
                    with contextlib.suppress(Exception):
                        await up_writer.drain()

                buf += chunk.decode("utf-8", "replace")
                if len(buf) > MAX_BUFFER:
                    _LOGGER.warning(
                        "Atmeex: буфер локального канала переполнен, сбрасываю хвост"
                    )
                    buf = ""
                    continue

                frames, buf = split_json_objects(buf)
                for raw in frames:
                    got = self._process_frame(raw, writer, answer=up_writer is None)
                    if got:
                        mac = got
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        except Exception:  # noqa: BLE001 — сервер не должен падать из-за одного клиента
            _LOGGER.exception("Atmeex: ошибка в локальном канале")
        finally:
            if mac:
                self.connected[mac] = False
                if self._sessions.get(mac, (None, None))[0] is writer:
                    self._sessions.pop(mac, None)
            self._writers.discard(writer)
            if relay is not None:
                relay.cancel()
            if up_writer is not None:
                sock = up_writer.get_extra_info("sockname")
                if isinstance(sock, tuple) and len(sock) >= 2:
                    self._own_endpoints.discard((sock[0], sock[1]))
            for w in (up_writer, writer):
                if w is not None:
                    w.close()
                    with contextlib.suppress(Exception):
                        await w.wait_closed()
            _LOGGER.debug("Atmeex: устройство %s отключилось", peer)

    async def _connect_upstream(
        self,
    ) -> tuple[asyncio.StreamReader | None, asyncio.StreamWriter | None]:
        """Встречное соединение к облаку. None — работаем автономно."""
        if self._upstream is None:
            return None, None

        host, port = await self._resolve_upstream()
        try:
            async with asyncio.timeout(UPSTREAM_CONNECT_TIMEOUT):
                reader, writer = await asyncio.open_connection(host, port)
        except (OSError, TimeoutError) as err:
            _LOGGER.warning(
                "Atmeex: облако %s:%s недоступно (%s) — локальный канал "
                "отвечает устройству сам",
                host,
                port,
                err,
            )
            return None, None

        # Запоминаем свой конец: если это соединение вернётся к нам входящим,
        # значит имя облака указывает на нас же.
        sock = writer.get_extra_info("sockname")
        if isinstance(sock, tuple) and len(sock) >= 2:
            self._own_endpoints.add((sock[0], sock[1]))
        return reader, writer

    async def _resolve_upstream(self) -> tuple[str, int]:
        """Куда идти за облаком.

        Если резолвить имя канала устройств в лоб, канал соединится сам с
        собой: подмена DNS действует и на Home Assistant. Каждое такое
        соединение выглядит как новое устройство, для которого снова
        открывается встречное — рекурсия без дна.
        """
        host, port = self._upstream
        if host != UPSTREAM_HOST:
            return host, port  # адрес задан явно — доверяем

        loop = asyncio.get_running_loop()
        try:
            infos = await loop.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
            addrs = {info[4][0] for info in infos}
        except OSError:
            return host, port

        if not addrs & self._local_addresses():
            return host, port

        _LOGGER.debug(
            "Atmeex: %s указывает на нас самих (%s) — беру адрес облака по %s",
            host,
            ", ".join(sorted(addrs)),
            UPSTREAM_FALLBACK_HOST,
        )
        return UPSTREAM_FALLBACK_HOST, port

    @staticmethod
    def _local_addresses() -> set[str]:
        """Собственные адреса хоста, включая смотрящий в сеть."""
        addrs = {"127.0.0.1", "::1"}
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # Пакеты не уходят: ядро только выбирает исходящий адрес.
            probe.connect(("192.0.2.1", 9))
            addrs.add(probe.getsockname()[0])
        except OSError:
            pass
        finally:
            probe.close()
        with contextlib.suppress(OSError):
            addrs.update(
                info[4][0] for info in socket.getaddrinfo(socket.gethostname(), None)
            )
        return addrs

    async def _await_upstream_return(self, writer: asyncio.StreamWriter) -> None:
        """Ждать, пока облако оживёт, и разорвать автономную сессию.

        Подхватить облако на середине разговора нельзя: оно не видело hello
        и не знает, чьё это соединение. Поэтому, когда вендор возвращается,
        мы закрываем сессию — бризер переподключается за секунды и получает
        уже нормальный сквозной канал.
        """
        while True:
            await asyncio.sleep(UPSTREAM_RETRY_INTERVAL)
            if writer.is_closing():
                return
            try:
                async with asyncio.timeout(UPSTREAM_CONNECT_TIMEOUT):
                    _, probe = await asyncio.open_connection(*self._upstream)
            except (OSError, TimeoutError):
                continue

            probe.close()
            with contextlib.suppress(Exception):
                await probe.wait_closed()
            _LOGGER.info(
                "Atmeex: облако снова доступно — разрываю автономную сессию, "
                "устройство переподключится уже через облако"
            )
            writer.close()
            return

    async def _relay_upstream(
        self, up_reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Ответы облака — устройству, байт в байт."""
        try:
            while True:
                chunk = await up_reader.read(READ_CHUNK)
                if not chunk:
                    break
                writer.write(chunk)
                await writer.drain()
        except (ConnectionError, asyncio.CancelledError):
            pass
        except Exception:  # noqa: BLE001
            _LOGGER.debug("Atmeex: обрыв ответного потока от облака", exc_info=True)

    # ------------------------------------------------------------------
    # Отправка команд
    # ------------------------------------------------------------------

    def is_connected(self, mac: str) -> bool:
        """Держит ли устройство прямо сейчас соединение с нами."""
        return mac in self._sessions and bool(self.connected.get(mac))

    async def async_send_params(self, mac: str, params: dict[str, Any]) -> bool:
        """Отправить уставки устройству напрямую.

        Принимает те же ключи `u_*`, что и облачный PUT /devices/{id}/params,
        и переводит их в команды канала. Возвращает False, если устройство
        сейчас не на связи — вызывающий решает, идти ли в облако.
        """
        session = self._sessions.get(mac)
        if session is None or not self.connected.get(mac):
            return False
        writer, device_id = session

        unknown = [key for key in params if key not in CMD_BY_PARAM]
        if unknown:
            _LOGGER.warning(
                "Atmeex: нет локальной команды для %s — эти параметры "
                "останутся необработанными",
                ", ".join(sorted(unknown)),
            )

        sent = False
        for key, value in params.items():
            cmd = CMD_BY_PARAM.get(key)
            if cmd is None or value is None:
                continue
            self._send(writer, {"id": device_id, "cmd": {cmd: value}})
            sent = True
            _LOGGER.debug("Atmeex: локально -> %s = %r (%s)", cmd, value, mac)

        if not sent:
            return False

        # Просим устройство отчитаться, чтобы не ждать очередной телеметрии.
        self._send(writer, {"id": device_id, "cmd": {"get_setp": True}})
        self._send(writer, {"id": device_id, "cmd": {"get_state": True}})
        try:
            await writer.drain()
        except OSError as err:
            _LOGGER.warning("Atmeex: не удалось отправить команду локально: %s", err)
            return False
        return True

    # ------------------------------------------------------------------
    # Разбор кадров
    # ------------------------------------------------------------------

    def _process_frame(
        self, raw: str, writer: asyncio.StreamWriter, *, answer: bool
    ) -> str | None:
        try:
            obj = json.loads(raw)
        except ValueError:
            _LOGGER.debug("Atmeex: нераспознанный кадр: %s", raw[:200])
            return None
        if not isinstance(obj, dict):
            return None

        device_id = obj.get("id")
        if not device_id:
            return None
        mac = normalize_mac(device_id)

        if isinstance(obj.get("hello"), dict):
            self.connected[mac] = True
            self._sessions[mac] = (writer, str(device_id))
            _LOGGER.info(
                "Atmeex: локальный канал — устройство %s (%s, прошивка %s)",
                mac,
                obj["hello"].get("model", "?"),
                obj["hello"].get("fw_ver", "?"),
            )
            if answer:
                # Облака нет — синхронизацию времени берём на себя, иначе
                # устройство не начнёт присылать телеметрию.
                self._send(writer, self._hello_reply(device_id))
                self._send(writer, {"id": device_id, "cmd": {"get_setp": True}})
                self._send(writer, {"id": device_id, "cmd": {"get_state": True}})
            return mac

        if isinstance(obj.get("state"), dict):
            self.states[mac] = obj["state"]
            self.connected[mac] = True
            self._sessions.setdefault(mac, (writer, str(device_id)))
            if self._on_state:
                self._on_state(mac, obj["state"])
            return mac

        if isinstance(obj.get("setp"), dict):
            self.setpoints[mac] = obj["setp"]
            self.connected[mac] = True
            if self._on_setp:
                self._on_setp(mac, obj["setp"])
            return mac

        return mac

    @staticmethod
    def _hello_reply(device_id: str) -> dict[str, Any]:
        """Ответ на hello — ровно в том виде, в каком его шлёт облако."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        return {
            "hello": True,
            "id": device_id,
            "time": now,
            "time_zone": "-00:00",
        }

    @staticmethod
    def _send(writer: asyncio.StreamWriter, payload: dict[str, Any]) -> None:
        writer.write(json.dumps(payload, separators=(",", ":")).encode())
