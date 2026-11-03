import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PIL import Image

from explore_cards import render_explore_card


class ExploreCardTests(unittest.TestCase):
    def test_renders_arabic_card_to_jpeg(self):
        root = Path(__file__).resolve().parent
        regular = root / "assets" / "NotoSansArabic-Regular.ttf"
        bold = root / "assets" / "NotoSansArabic-SemiBold.ttf"
        with TemporaryDirectory() as temp_dir:
            path = render_explore_card(
                "منشور تجريبي لاختبار دعم اللغة العربية وإعادة النشر",
                "مستخدم تجريبي",
                temp_dir,
                regular_font=regular,
                bold_font=bold,
            )
            self.assertTrue(path.is_file())
            self.assertEqual(path.suffix, ".jpg")
            with Image.open(path) as image:
                self.assertEqual(image.format, "JPEG")
                self.assertEqual(image.size, (1080, 1350))

    def test_rejects_missing_pillow_only_by_clear_error(self):
        with patch("explore_cards.Image", None):
            with self.assertRaisesRegex(RuntimeError, "Pillow is required"):
                render_explore_card("نص", "مستخدم", "/tmp/explore-test")


if __name__ == "__main__":
    unittest.main()
