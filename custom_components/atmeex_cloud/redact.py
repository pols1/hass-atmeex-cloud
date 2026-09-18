"""Вымарывание учётных данных из того, что уходит в лог и в тексты ошибок.

Ответ /auth/signin несёт access_token и refresh_token. Отладочный лог включают
ровно тогда, когда заводят баг, и кусок home-assistant.log потом вставляют
в issue — значит, ни одно значение токена или пароля туда попадать не должно.
Ключи оставляем: по ним видно, что сервер вообще прислал.

Диагностика (diagnostics.py) закрывает свой набор TO_REDACT штатным
async_redact_data — он шире: там ещё MAC, owner_id и имя Wi-Fi сети.

Модуль не зависит ни от homeassistant, ни от aiohttp, поэтому тестируется
на голом Python.
"""

from __future__ import annotations

import json
import re
from typing import Any

# Совпадает с homeassistant.components.diagnostics.REDACTED.
REDACTED = "**REDACTED**"

SENSITIVE_KEYS = frozenset(
    {
        "access_token",
        "refresh_token",
        "id_token",
        "token",
        "password",
        "email",
        "authorization",
    }
)

# Для тел, которые не разбираются как JSON (обрезанные, битые, текст с
# вкраплениями JSON): "ключ": "значение" — значение может быть без
# закрывающей кавычки, если строку обрезали посреди токена.
_JSON_PAIR_RE = re.compile(
    r'("(?:' + "|".join(sorted(SENSITIVE_KEYS)) + r')"\s*:\s*)"[^"]*"?',
    re.IGNORECASE,
)
_BEARER_RE = re.compile(r"(Bearer\s+)[^\s\"',;]+", re.IGNORECASE)


def redact(data: Any) -> Any:
    """Копия структуры, где значения чувствительных ключей заменены на REDACTED."""
    if isinstance(data, dict):
        return {
            k: REDACTED
            if isinstance(k, str) and k.lower() in SENSITIVE_KEYS
            else redact(v)
            for k, v in data.items()
        }
    if isinstance(data, list):
        return [redact(v) for v in data]
    return data


def redact_body(text: str, limit: int | None = None) -> str:
    """Тело HTTP-ответа, пригодное для лога: без токенов, паролей и email.

    Обрезаем только после вымарывания — иначе обрезанный JSON не разберётся
    и хвост токена проскочит.
    """
    try:
        parsed = json.loads(text)
    except ValueError:
        out = _JSON_PAIR_RE.sub(r'\1"' + REDACTED + '"', text)
    else:
        out = json.dumps(redact(parsed), ensure_ascii=False)
    out = _BEARER_RE.sub(r"\1" + REDACTED, out)
    return out if limit is None else out[:limit]
