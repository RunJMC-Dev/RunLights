import unittest
from PIL import Image, ImageDraw
from runlights.health_bar import read_health_bar

class HealthBarTests(unittest.TestCase):
    def frame(self, percent):
        im = Image.new("RGB", (296, 26), (20, 20, 20))
        d = ImageDraw.Draw(im)
        for x in (2, 292):
            d.point((x, 2), fill=(120, 120, 120))
        width = round(284 * percent / 100)
        if width:
            d.rectangle((6, 9, 5 + width, 17), fill=(185, 190, 196))
        return im
    def test_fill(self):
        for pct in (0, 10, 20, 50, 90, 100):
            self.assertAlmostEqual(read_health_bar(self.frame(pct)), pct, delta=.3)
    def test_invalid(self):
        for color in ('black', 'white', 'gray'):
            self.assertIsNone(read_health_bar(Image.new('RGB', (296, 26), color)))
        self.assertIsNone(read_health_bar(Image.new('RGB', (592, 52))))
    def test_disconnected(self):
        im = self.frame(20)
        ImageDraw.Draw(im).rectangle((200, 9, 220, 17), fill='white')
        self.assertIsNone(read_health_bar(im))
    def test_sanity(self):
        im = self.frame(50)
        ImageDraw.Draw(im).rectangle((148, 9, 289, 17), fill=(55, 65, 90))
        self.assertAlmostEqual(read_health_bar(im), 50, delta=.3)
