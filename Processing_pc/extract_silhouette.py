#!/usr/bin/env python3
"""
extract_silhouette.py — extract the object's TRUE boundary outline from a
SAM mask, scaled to real-world mm. This is Outline Mode: accurate overall
dimensions, no internal detail.
"""

import cv2
import numpy as np
import argparse
import json


def extract_silhouette_svg(mask_path, output_svg_path, mm_per_pixel,
                            simplify_epsilon_frac=0.002, min_contour_area=50):
    src = cv2.imread(mask_path, cv2.IMREAD_GRAYSCALE)
    if src is None:
        raise FileNotFoundError(f"Could not load mask: {mask_path}")

    _, binary = cv2.threshold(src, 10, 255, cv2.THRESH_BINARY)
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)

    height_px, width_px = src.shape
    width_mm = width_px * mm_per_pixel
    height_mm = height_px * mm_per_pixel
    paths_written = 0

    with open(output_svg_path, 'w') as svg_file:
        svg_file.write('<?xml version="1.0" encoding="utf-8"?>\n')
        svg_file.write(
            f'<svg xmlns="http://www.w3.org/2000/svg" version="1.1" '
            f'width="{width_mm:.2f}mm" height="{height_mm:.2f}mm" '
            f'viewBox="0 0 {width_mm:.2f} {height_mm:.2f}">\n'
        )

        for contour in contours:
            area = cv2.contourArea(contour)
            if area < min_contour_area:
                continue

            perimeter = cv2.arcLength(contour, closed=True)
            epsilon = simplify_epsilon_frac * perimeter
            simplified = cv2.approxPolyDP(contour, epsilon, closed=True)

            points = simplified.reshape(-1, 2)
            if len(points) < 3:
                continue

            # scale every point from pixels to mm here
            pts_mm = points * mm_per_pixel

            path_str = f"M {pts_mm[0][0]:.3f} {pts_mm[0][1]:.3f} "
            for pt in pts_mm[1:]:
                path_str += f"L {pt[0]:.3f} {pt[1]:.3f} "
            path_str += "Z"

            svg_file.write(f'  <path d="{path_str}" fill="none" stroke="black" stroke-width="0.3" />\n')
            paths_written += 1

        svg_file.write('</svg>\n')

    print(f"{paths_written} boundary contour(s) written, "
          f"true size: {width_mm:.1f}mm x {height_mm:.1f}mm")
    return output_svg_path


def extract_silhouette_from_capture(mask_path, output_svg_path, json_path,
                                     simplify_epsilon_frac=0.002, min_contour_area=50):
    """
    Convenience wrapper: reads mm_per_pixel straight from the capture's JSON
    metadata file
    """
    with open(json_path) as f:
        meta = json.load(f)

    if "mm_per_pixel" not in meta:
        raise KeyError(f"'mm_per_pixel' not found in {json_path} - check the capture metadata")

    mm_per_pixel = meta["mm_per_pixel"]
    return extract_silhouette_svg(mask_path, output_svg_path, mm_per_pixel,
                                   simplify_epsilon_frac, min_contour_area)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Extract dimensioned silhouette boundary from a SAM mask as SVG")
    ap.add_argument("mask_path")
    ap.add_argument("out_svg")
    ap.add_argument("json_path", help="Capture metadata JSON file (must contain mm_per_pixel)")
    ap.add_argument("--epsilon-frac", type=float, default=0.002)
    ap.add_argument("--min-area", type=float, default=50)
    args = ap.parse_args()

    extract_silhouette_from_capture(args.mask_path, args.out_svg, args.json_path,
                                     args.epsilon_frac, args.min_area)
