import unittest

from app.integrations import magiis_web
from app.integrations.magiis_crm import MagiisCrmReader


class DisabledExternalIntegrationTest(unittest.TestCase):
    def test_web_operations_are_disabled(self):
        for operation in (magiis_web.login, magiis_web.create_travel, magiis_web.get_place, magiis_web.cancel_travel):
            with self.assertRaisesRegex(RuntimeError, "deshabilitada"):
                operation()

    def test_crm_reader_is_disabled(self):
        with self.assertRaisesRegex(RuntimeError, "deshabilitado"):
            MagiisCrmReader()


if __name__ == "__main__":
    unittest.main()
