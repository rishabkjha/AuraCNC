"""
svg_to_gcode.py - converts a vectorized SVG (from extract_silhouette.py or
extract_best_centerlines()) into G-code.

IMPORTANT UNIT NOTE: extract_silhouette_svg() already bakes mm_per_pixel
into every coordinate before writing the SVG - the path data in that file
is ALREADY real millimeters, not pixels. This function defaults to reading
those coordinates as-is (already_scaled=True). Passing mm_per_pixel again
AND also multiplying would double-scale the output silently - a real,
hard-to-notice bug (mm_per_pixel is typically < 1, so squaring it SHRINKS
everything). Only set already_scaled=False if you're feeding this a raw
pixel-space SVG that hasn't been through extract_silhouette_svg's scaling.
"""

import re
import xml.etree.ElementTree as ET


def parse_path(path):
    tokens = re.findall(r"[MLZmlz]|-?\d+\.?\d*", path)

    i = 0
    cmd = None
    pts = []

    while i < len(tokens):
        t = tokens[i]

        if t in ['M', 'L', 'Z', 'm', 'l', 'z']:
            cmd = t
            i += 1

            if cmd.upper() == 'Z':
                pts.append(("Z",))
                continue

        if cmd is not None and cmd.upper() in ['M', 'L']:
            x = float(tokens[i])
            y = float(tokens[i + 1])
            pts.append((cmd.upper(), x, y))
            i += 2
        else:
            i += 1  # safety: skip anything unexpected rather than looping forever

    return pts


def convert(svg_path, gcode_path, mm_per_pixel=None, feed_rate=500,
            laser_power=255, already_scaled=True):
    """
    svg_path, gcode_path: input/output file paths.
    mm_per_pixel: only applied as a multiplier if already_scaled=False.
                  Ignored (safely) when already_scaled=True, which is the
                  correct setting for SVGs from extract_silhouette_svg().
    feed_rate: engrave/cut feed rate (mm/min).
    laser_power: M3 S<power> value.
    already_scaled: True if the SVG's path coordinates are already real mm
                     (extract_silhouette_svg's output). False if they're
                     still raw pixels and need mm_per_pixel applied here.
    """
    scale = 1.0 if already_scaled else (mm_per_pixel if mm_per_pixel else 1.0)
    if not already_scaled and mm_per_pixel is None:
        raise ValueError("already_scaled=False requires a real mm_per_pixel value")

    tree = ET.parse(svg_path)
    root = tree.getroot()

    paths = root.findall(".//{http://www.w3.org/2000/svg}path")
    if not paths:
        raise ValueError(f"No <path> elements found in {svg_path} - "
                          f"check the SVG isn't empty or using an unexpected namespace")

    gcode = []
    gcode.append("G21")  # mm
    gcode.append("G90")  # Absolute positioning
    gcode.append("G28")  # Home
    gcode.append(f"G1 F{feed_rate}")

    min_x, max_x, min_y, max_y = float('inf'), float('-inf'), float('inf'), float('-inf')

    for path in paths:
        commands = parse_path(path.attrib["d"])
        start = None
        laser_on = False

        for c in commands:
            if c[0] == "M":
                x = c[1] * scale
                y = c[2] * scale
                start = (x, y)
                min_x, max_x = min(min_x, x), max(max_x, x)
                min_y, max_y = min(min_y, y), max(max_y, y)

                if laser_on:
                    gcode.append("M5")
                    laser_on = False
                gcode.append(f"G0 X{x:.3f} Y{y:.3f}")

            elif c[0] == "L":
                x = c[1] * scale
                y = c[2] * scale
                min_x, max_x = min(min_x, x), max(max_x, x)
                min_y, max_y = min(min_y, y), max(max_y, y)

                if not laser_on:
                    gcode.append(f"M3 S{laser_power}")
                    laser_on = True
                gcode.append(f"G1 X{x:.3f} Y{y:.3f}")

            elif c[0] == "Z":
                if start:
                    if not laser_on:
                        gcode.append(f"M3 S{laser_power}")
                        laser_on = True
                    gcode.append(f"G1 X{start[0]:.3f} Y{start[1]:.3f}")

        if laser_on:
            gcode.append("M5")

    gcode.append("G28")
    gcode.append("M2")

    with open(gcode_path, "w") as f:
        f.write("\n".join(gcode))

    real_width = max_x - min_x if max_x > min_x else 0
    real_height = max_y - min_y if max_y > min_y else 0
    print(f"G-code written: {gcode_path} "
          f"(bounds: {real_width:.1f}mm x {real_height:.1f}mm, "
          f"already_scaled={already_scaled})")
    return gcode_path


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Convert an SVG to G-code")
    ap.add_argument("svg_path")
    ap.add_argument("gcode_path")
    ap.add_argument("--mm-per-pixel", type=float, default=None)
    ap.add_argument("--feed-rate", type=int, default=500)
    ap.add_argument("--laser-power", type=int, default=255)
    ap.add_argument("--raw-pixels", action="store_true",
                     help="Set if the SVG is NOT already mm-scaled (raw pixel coords)")
    args = ap.parse_args()

    convert(args.svg_path, args.gcode_path, args.mm_per_pixel,
            args.feed_rate, args.laser_power, already_scaled=not args.raw_pixels)
