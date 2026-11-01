"""Small validated row language, rendered as escaped Qt rich text."""
from datetime import datetime
from html import escape
import re
import xml.etree.ElementTree as ET

EXAMPLE = '''<row font="Tenorite" size="15">{clock:HH:mm}</row>
<row font="Tenorite" size="7">Played: {playtime:HH:mm}</row>
<row font="Tenorite" size="7">CPU: {cpu.temp} GPU: {gpu.temp}</row>'''
TOKEN = re.compile(r"\{([^{}]+)\}")


def parse_template(text):
    if len(text) > 16000:
        raise ValueError("Template is too long (maximum 16000 characters).")
    if "<!" in text:
        raise ValueError("XML declarations and entities are not supported.")
    try:
        root = ET.fromstring("<overlay>" + text + "</overlay>")
    except ET.ParseError as exc:
        raise ValueError(f"Invalid row markup: {exc}") from exc
    if (root.text or "").strip() or not len(root):
        raise ValueError("Wrap each line in <row>...</row>.")
    for row in root:
        if row.tag != "row" or len(row) or (row.tail or "").strip():
            raise ValueError("Only plain text inside <row> tags is supported.")
        if set(row.attrib) - {"font", "size", "color", "align"}:
            raise ValueError("Row attributes: font, size, color, align.")
        if "size" in row.attrib:
            try:
                size = float(row.attrib["size"])
            except ValueError:
                size = 0
            if not 4 <= size <= 96:
                raise ValueError("Row size must be 4–96 points.")
        if "color" in row.attrib and not re.fullmatch(r"#[0-9a-fA-F]{6}", row.attrib["color"]):
            raise ValueError("Row color must use #RRGGBB.")
        if row.get("align", "left") not in {"left", "center", "right"}:
            raise ValueError("Row align must be left, center or right.")
        for token in TOKEN.findall(row.text or ""):
            if token in {"cpu.temp", "gpu.temp"}:
                continue
            if token not in {f"{kind}:{fmt}" for kind in ("clock", "playtime") for fmt in ("HH:mm", "HH:mm:ss")}:
                raise ValueError(f"Unknown variable: {{{token}}}")
    return root


def render_template(rows, reading, elapsed=None, now=None, alarm=False):
    now = now or datetime.now()
    result = []
    def substitute(match):
        token = match.group(1)
        if token in {"cpu.temp", "gpu.temp"}:
            value = getattr(reading, token.split('.')[0])
            return "—" if value is None else f"{value:.0f} °C"
        kind, fmt = token.split(":", 1)
        if kind == "clock":
            return now.strftime("%H:%M:%S" if fmt == "HH:mm:ss" else "%H:%M")
        hours, rem = divmod(max(0, int(elapsed or 0)), 3600)
        minutes, seconds = divmod(rem, 60)
        return f"{hours:02d}:{minutes:02d}" + (f":{seconds:02d}" if fmt == "HH:mm:ss" else "")
    for row in rows:
        text = row.text or ""
        if elapsed is None and "{playtime:" in text:
            continue
        content = escape(TOKEN.sub(substitute, text))
        if alarm and not result:
            content += "  ⏰"
        if row.get("font"):
            content = f'<font face="{escape(row.get("font"), quote=True)}">{content}</font>'
        style = "margin:0;"
        if row.get("size"):
            style += f'font-size:{float(row.get("size")):g}pt;'
        if row.get("color"):
            style += f'color:{row.get("color")};'
        result.append(f'<p align="{row.get("align", "left")}" style="{style}">{content}</p>')
    return "".join(result)
