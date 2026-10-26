"""Человеческая часть ответа облака — чтобы показать её в мастере настройки.

Ошибки API выглядят как `signin failed 401: {"message": "Аккаунт не найден…"}`.
Пользователю нужна именно эта фраза: она объясняет, что делать. Прежде мастер
показывал на любую ошибку одно и то же — «не удаётся связаться с облаком,
проверьте, отвечает ли api.iot.atmeex.com», — и 26.10.2026 это ввело в
заблуждение: облако отвечало (пинг шёл, сервер вернул 401 с объяснением, что
аккаунты потеряны при аварии дата-центра и нужно зарегистрироваться заново),
а мастер уверял, что связи нет.

Модуль не зависит от Home Assistant и проверяется тестами на голом Python.
"""

from __future__ import annotations

import json
import re

# Ограничиваем длину: сообщение попадает в диалог, а не в лог.
MAX_LENGTH = 300

_JSON_PART = re.compile(r"\{.*\}", re.DOTALL)


def vendor_message(error: object) -> str:
    """Фраза вендора из текста ошибки; если её нет — сам текст ошибки."""
    text = str(error).strip()
    if not text:
        return ""

    match = _JSON_PART.search(text)
    if match:
        try:
            payload = json.loads(match.group(0))
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            for key in ("message", "error", "detail"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()[:MAX_LENGTH]

    return text[:MAX_LENGTH]
