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
# #!/usr/bin/env python3
# """
# svg_to_gcode.py — Convert SVG path outlines into G-code for a 2.5-axis CNC
# (pen plotter / laser / small router).

# APPROACH
# --------
# 1. PARSE     : Use svgpathtools to read every <path> element's `d` string.
#                svgpathtools already understands M/L/C/Q/A/Z etc. and gives
#                back a `Path` object made of typed Segment objects
#                (Line, CubicBezier, QuadraticBezier, Arc).
# 2. FLATTEN   : CNC controllers only move in straight lines (G1) or rapids
#                (G0) — there's no native "draw a bezier" G-code command
#                (arcs G2/G3 exist but fitting curves to arcs is its own
#                can of worms). So every curved segment is sampled at N
#                points and converted into a polyline. Straight Line
#                segments are already exact and need no sampling.
# 3. TRANSFORM : SVG's coordinate system has Y increasing DOWNWARD and units
#                in abstract "user units" (often pixels). CNC machines use
#                Y increasing UPWARD in real-world mm/inches. So every point
#                needs: (a) scale from SVG units -> mm, (b) Y-flip.
# 4. PATH/PEN STATE : Each `M` (moveto) starts a new disconnected subpath —
#                that's a "pen up, rapid move, pen down" event. Every
#                subsequent point in that subpath is a "pen down, feed move".
# 5. EMIT      : Walk every subpath's point list and emit:
#                  G0 Z<safe_height>          (retract before travel)
#                  G0 X.. Y..                 (rapid to start of subpath)
#                  G1 Z<work_height> F<plunge_feed>   (plunge / pen down)
#                  G1 X.. Y.. F<feed_rate>    (cut/draw moves)
#                then after the whole file: retract Z and M2 (end program).

# This keeps the mapping 1:1 and dumb on purpose — reliability over
# cleverness. Two optional refinements are stubbed in (see comments):
#   - path re-ordering to minimize rapid travel (nearest-neighbor TSP-ish)
#   - arc-fitting curves into G2/G3 instead of dense polylines
# Skip both for a first working pipeline; add them once the naive version
# is proven on your machine.
# """

# import argparse
# import json
# from svgpathtools import svg2paths2
# from svgpathtools.path import Line


# def flatten_segment(seg, samples_per_curve=20):
#     """Return a list of complex points approximating this segment.

#     Line segments are exact (2 points). Everything else (Bezier/Arc) is
#     sampled parametrically via .point(t) for t in [0,1] — this is the
#     general trick for turning any parametric curve into a polyline: more
#     samples = smoother curve = more G-code lines. 20 is a reasonable
#     default for small decorative/engraving work; raise it for large
#     sweeping curves, lower it for tiny detail where line count matters.
#     """
#     if isinstance(seg, Line):
#         return [seg.start, seg.end]
#     n = samples_per_curve
#     return [seg.point(t / n) for t in range(n + 1)]


# def svg_to_polylines(svg_file, samples_per_curve=20):
#     """Return a list of subpaths; each subpath is a list of (x, y) tuples
#     in raw SVG coordinate space (unflipped, unscaled)."""
#     paths, attributes, svg_attributes = svg2paths2(svg_file)

#     polylines = []
#     for path in paths:
#         if len(path) == 0:
#             continue
#         # A single svgpathtools Path can itself contain multiple subpaths
#         # (M...Z M...Z...). continuous_subpaths() splits on discontinuities
#         # exactly where a new M would have restarted the pen.
#         for subpath in path.continuous_subpaths():
#             pts = []
#             for seg in subpath:
#                 seg_pts = flatten_segment(seg, samples_per_curve)
#                 # avoid duplicating the shared point between consecutive
#                 # segments (end of seg[i] == start of seg[i+1])
#                 if pts and seg_pts and pts[-1] == seg_pts[0]:
#                     seg_pts = seg_pts[1:]
#                 pts.extend(seg_pts)
#             if len(pts) >= 2:
#                 polylines.append([(p.real, p.imag) for p in pts])
#     return polylines, svg_attributes


# def transform_points(polylines, svg_attributes, scale_mm_per_unit, flip_y=True):
#     """Convert raw SVG-space polylines into mm-space, Y-up polylines."""
#     # svg height, needed to flip Y correctly (y_new = height - y_old)
#     height = float(svg_attributes.get('height', 0) or 0)
#     if not height:
#         # fall back: derive from viewBox if height attr missing/percentage
#         vb = svg_attributes.get('viewBox')
#         if vb:
#             _, _, _, vb_h = [float(v) for v in vb.split()]
#             height = vb_h

#     out = []
#     for line in polylines:
#         transformed = []
#         for x, y in line:
#             y2 = (height - y) if flip_y else y
#             transformed.append((x * scale_mm_per_unit, y2 * scale_mm_per_unit))
#         out.append(transformed)
#     return out


# def polylines_to_gcode(polylines,
#                         safe_z=5.0,
#                         work_z=0.0,
#                         travel_feed=3000,
#                         plunge_feed=300,
#                         draw_feed=800,
#                         units_mm=True):
#     """The actual G-code emission. Straightforward state machine:
#     for each subpath -> retract -> rapid to start -> plunge -> feed
#     through remaining points -> (loop) -> final retract + program end.
#     """
#     lines = []
#     lines.append("; Generated by svg_to_gcode.py")
#     lines.append("G21" if units_mm else "G20")   # mm or inch mode
#     lines.append("G90")                          # absolute positioning
#     lines.append(f"G0 Z{safe_z:.3f}")             # start retracted

#     for path in polylines:
#         if len(path) < 2:
#             continue
#         x0, y0 = path[0]
#         lines.append(f"G0 Z{safe_z:.3f}")
#         lines.append(f"G0 X{x0:.3f} Y{y0:.3f} F{travel_feed}")
#         lines.append(f"G1 Z{work_z:.3f} F{plunge_feed}")  # pen/tool down
#         for x, y in path[1:]:
#             lines.append(f"G1 X{x:.3f} Y{y:.3f} F{draw_feed}")

#     lines.append(f"G0 Z{safe_z:.3f}")
#     lines.append("M2")  # end of program
#     return "\n".join(lines)


# def convert(svg_file, out_file, scale_mm_per_unit=1.0, samples_per_curve=20,
#             safe_z=5.0, work_z=0.0, travel_feed=3000, plunge_feed=300,
#             draw_feed=800):
#     polylines, svg_attrs = svg_to_polylines(svg_file, samples_per_curve)
#     polylines = transform_points(polylines, svg_attrs, scale_mm_per_unit)
#     gcode = polylines_to_gcode(polylines, safe_z, work_z,
#                                 travel_feed, plunge_feed, draw_feed)
#     with open(out_file, "w") as f:
#         f.write(gcode)
#     n_moves = sum(len(p) for p in polylines)
#     print(f"{len(polylines)} subpaths, {n_moves} points -> {out_file}")


# if __name__ == "__main__":
#     ap = argparse.ArgumentParser(description="Convert SVG outlines to G-code")
#     ap.add_argument("svg_file")
#     ap.add_argument("out_file")
#     scale_group = ap.add_mutually_exclusive_group()
#     scale_group.add_argument("--scale", type=float, default=None,
#                      help="mm per SVG unit, given directly (e.g. 0.264583 if units are px at 96dpi)")
#     scale_group.add_argument("--scale-json", type=str, default=None,
#                      help="path to a capture_XXXX.json containing 'mm_per_pixel' — "
#                           "reads that field and uses it as the scale")
#     ap.add_argument("--samples", type=int, default=20,
#                      help="points used to approximate each curve segment")
#     ap.add_argument("--safe-z", type=float, default=5.0)
#     ap.add_argument("--work-z", type=float, default=0.0)
#     ap.add_argument("--travel-feed", type=int, default=3000)
#     ap.add_argument("--plunge-feed", type=int, default=300)
#     ap.add_argument("--draw-feed", type=int, default=800)
#     args = ap.parse_args()

#     if args.scale_json:
#         with open(args.scale_json) as f:
#             meta = json.load(f)
#         scale = meta["mm_per_pixel"]
#     elif args.scale is not None:
#         scale = args.scale
#     else:
#         scale = 1.0  # neither given — fall back to 1:1, likely wrong, but don't crash

#     convert(args.svg_file, args.out_file, scale, args.samples,
#             args.safe_z, args.work_z, args.travel_feed,
#             args.plunge_feed, args.draw_feed)