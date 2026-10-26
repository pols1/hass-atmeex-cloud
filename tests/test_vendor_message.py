"""Фраза вендора из текста ошибки — она и должна стоять в диалоге.

26.10.2026 облако отвечало 401 с объяснением («Аккаунт не найден. После
аварии в дата-центре Яндекса сервис восстановлен заново — зарегистрируйтесь
и добавьте прибор снова»), а мастер настройки показывал «не удаётся связаться
с облаком, проверьте, отвечает ли api.iot.atmeex.com». Владелец возразил
ровно по делу: «хотя пинг есть».
"""

from __future__ import annotations

import importlib.util
import pathlib
import unittest

_MODULE_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "custom_components"
    / "atmeex_cloud"
    / "vendor_message.py"
)
_spec = importlib.util.spec_from_file_location("atmeex_vendor_message", _MODULE_PATH)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

vendor_message = _mod.vendor_message
MAX_LENGTH = _mod.MAX_LENGTH


class VendorMessageTest(unittest.TestCase):
    def test_real_answer_from_the_cloud(self) -> None:
        err = Exception(
            'signin failed 401: {"message": "Аккаунт не найден. После аварии '
            'в дата-центре Яндекса сервис восстановлен заново — '
            'зарегистрируйтесь и добавьте прибор снова."}'
        )
        self.assertEqual(
            vendor_message(err),
            "Аккаунт не найден. После аварии в дата-центре Яндекса сервис "
            "восстановлен заново — зарегистрируйтесь и добавьте прибор снова.",
        )

    def test_earlier_refusal(self) -> None:
        err = Exception('signin failed 403: {"message": "Аккаунт не допущен к тестированию"}')
        self.assertEqual(vendor_message(err), "Аккаунт не допущен к тестированию")

    def test_other_field_names(self) -> None:
        self.assertEqual(vendor_message('500: {"error": "boom"}'), "boom")
        self.assertEqual(vendor_message('422: {"detail": "bad email"}'), "bad email")

    def test_without_json_the_text_itself_is_shown(self) -> None:
        # Таймаут и сетевые ошибки объяснения от вендора не несут.
        self.assertEqual(
            vendor_message("Облако Atmeex не ответило за отведённое время"),
            "Облако Atmeex не ответило за отведённое время",
        )

    def test_broken_json_does_not_break_us(self) -> None:
        self.assertEqual(vendor_message('401: {"message": "обрыв'), '401: {"message": "обрыв')

    def test_empty_and_blank(self) -> None:
        self.assertEqual(vendor_message(""), "")
        self.assertEqual(vendor_message(Exception()), "")
        # Пустое значение поля не должно выдаваться за объяснение.
        self.assertEqual(vendor_message('401: {"message": "   "}'), '401: {"message": "   "}')

    def test_long_message_is_trimmed(self) -> None:
        long = "я" * 1000
        self.assertEqual(len(vendor_message(f'401: {{"message": "{long}"}}')), MAX_LENGTH)
        self.assertEqual(len(vendor_message("ы" * 1000)), MAX_LENGTH)


if __name__ == "__main__":
    unittest.main()
