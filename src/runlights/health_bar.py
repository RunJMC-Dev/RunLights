"""Read the Once Human 1920x1080 HUD crop without OCR."""


def read_health_bar(image):
    """Return percent of the full bar, or None when the HUD is not recognizable.

    Crop: (812, 1030, 296, 26). Sanity-limited capacity counts as unavailable
    health; this deliberately does not infer a separate sanity percentage.
    """
    if image.size != (296, 26):
        return None
    rgb = image.convert("RGB")
    def bright(x, y):
        return min(rgb.getpixel((x, y))) > 100
    # Two light corner ticks plus dark gutters distinguish the HUD from menus.
    if not all(any(bright(x, y) for y in (1, 2, 3)) for x in (2, 292)):
        return None
    for y in (5, 20):
        if sum(max(rgb.getpixel((x, y))) < 100 for x in range(6, 290)) < 250:
            return None
    filled = []
    for x in range(6, 290):
        samples = [rgb.getpixel((x, y)) for y in range(9, 18)]
        filled.append(sum(min(p) > 140 and max(p)-min(p) < 65 for p in samples) >= 6)
    # The fill must be a contiguous left-to-right run (allow tiny render gaps).
    end = max((i+1 for i, on in enumerate(filled) if on), default=0)
    if end and (sum(filled[:end]) / end < .96 or not any(filled[:3])):
        return None
    return round(100 * end / len(filled), 1)
