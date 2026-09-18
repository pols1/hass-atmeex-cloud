"""Тесты вымарывания учётных данных из логов.

Модуль redact не зависит ни от homeassistant, ни от aiohttp:

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import unittest

_MODULE_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "custom_components"
    / "atmeex_cloud"
    / "redact.py"
)
_spec = importlib.util.spec_from_file_location("atmeex_redact", _MODULE_PATH)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

REDACTED = _mod.REDACTED
redact = _mod.redact
redact_body = _mod.redact_body

ACCESS = "eyJhbGciOiJIUzI1NiJ9.eyJleHAiOjE3NTg3MDAwMDB9.c2lnbmF0dXJlLWFjY2Vzcw"
REFRESH = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJyZWZyZXNoIn0.c2lnbmF0dXJlLXJlZnJlc2g"

SIGNIN_OK = json.dumps(
    {"access_token": ACCESS, "refresh_token": REFRESH, "token_type": "Bearer"}
)


class RedactBodyTest(unittest.TestCase):
    def test_signin_response_keeps_keys_drops_values(self) -> None:
        out = redact_body(SIGNIN_OK, 1000)
        self.assertNotIn(ACCESS, out)
        self.assertNotIn(REFRESH, out)
        self.assertEqual(
            json.loads(out),
            {"access_token": REDACTED, "refresh_token": REDACTED, "token_type": "Bearer"},
        )

    def test_truncated_body_leaks_no_token_prefix(self) -> None:
        # Тело обрезано посреди токена: JSON не разбирается, срабатывает запасной путь.
        cut = SIGNIN_OK[: SIGNIN_OK.index(REFRESH) + 20]
        out = redact_body(cut)
        self.assertNotIn(ACCESS[:20], out)
        self.assertNotIn(REFRESH[:20], out)
        self.assertIn('"refresh_token"', out)

    def test_limit_applies_after_redaction(self) -> None:
        # Раньше обрезали сырой текст; обрезка до разбора пропустила бы хвост токена.
        out = redact_body(SIGNIN_OK, 40)
        self.assertEqual(len(out), 40)
        self.assertNotIn(ACCESS[:10], out)

    def test_error_message_survives(self) -> None:
        body = '{"message": "Неверный пароль", "code": 401}'
        self.assertEqual(json.loads(redact_body(body)), json.loads(body))

    def test_plain_text_is_untouched(self) -> None:
        self.assertEqual(redact_body("Bad Gateway", 500), "Bad Gateway")

    def test_bearer_value_redacted(self) -> None:
        out = redact_body(f"invalid header Authorization: Bearer {ACCESS}")
        self.assertNotIn(ACCESS, out)
        self.assertIn("Bearer " + REDACTED, out)

    def test_key_match_is_case_insensitive(self) -> None:
        out = redact_body('{"Access_Token": "abc", "Password": "hunter2"}')
        self.assertNotIn("abc", out)
        self.assertNotIn("hunter2", out)


class RedactTest(unittest.TestCase):
    def test_nested_and_list(self) -> None:
        data = {"user": {"email": "a@b.c", "id": 7}, "items": [{"token": "t"}]}
        self.assertEqual(
            redact(data),
            {"user": {"email": REDACTED, "id": 7}, "items": [{"token": REDACTED}]},
        )

    def test_does_not_mutate_input(self) -> None:
        data = {"access_token": ACCESS}
        redact(data)
        self.assertEqual(data["access_token"], ACCESS)

    def test_one_token_missing_message(self) -> None:
        # Сценарий "signin: missing tokens": пришёл только один из двух токенов.
        msg = f"signin: missing tokens: {redact({'access_token': ACCESS})}"
        self.assertNotIn(ACCESS, msg)
        self.assertIn("access_token", msg)


if __name__ == "__main__":
    unittest.main()
