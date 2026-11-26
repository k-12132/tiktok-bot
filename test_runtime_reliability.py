import ast
import unittest
from pathlib import Path

class RuntimeTests(unittest.TestCase):
    def test_photo_guard_and_polling(self):
        source = Path("bot.py").read_text()
        ast.parse(source)
        self.assertIn('/photo/', source)
        self.assertIn('drop_pending_updates=False', source)
        self.assertIn('isinstance(error, NetworkError)', source)
