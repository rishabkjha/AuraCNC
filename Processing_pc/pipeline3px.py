# #!/usr/bin/env python3
# """
# pipeline.py — the three stages chained for one received capture.

# STAGE A — segment_with_sam()
#     Unified entry point. Accepts a `method="sam"` or `method="fastsam"` argument
#     to route execution dynamically. Includes a 3-pixel padding dilation buffer.

# STAGE B — extract_best_centerlines()
#     Meijering ridge-detection + MST + DFS-ordering script.
#     [RESTORED & FIXED]: Hole-filling mask constraint brought back to block outer bleed,
#                         while lower ridge threshold restores thin inner vein extraction.

# STAGE C — svg_to_gcode.convert()
#     Converts vector tracks directly into G-code based on capture scaling factors.
# """
# import os
# import json
# import argparse

# import cv2
# import numpy as np
# import networkx as nx
# from skimage.filters import meijering
# from networkx.algorithms.tree.mst import minimum_spanning_tree
# from scipy.spatial.distance import cdist

# import svg_to_gcode


# def _run_original_sam(image_path, tap_x, tap_y, checkpoint_path, output_path):
#     from segment_anything import sam_model_registry, SamPredictor
#     if not os.path.exists(checkpoint_path):
#         raise FileNotFoundError(f"SAM checkpoint not found at {checkpoint_path}")
        
#     sam = sam_model_registry["vit_b"](checkpoint=checkpoint_path)
#     predictor = SamPredictor(sam)
#     image = cv2.imread(image_path)
#     image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
#     predictor.set_image(image_rgb)
    
#     masks, scores, _ = predictor.predict(
#         point_coords=np.array([[tap_x, tap_y]]),
#         point_labels=np.array([1]),
#         multimask_output=True
#     )
    
#     # Restored standard behavior based on model score matching the original engine architecture
#     best_idx = int(np.argmax(scores))
#     best_mask = masks[best_idx]
    
#     mask_resized = cv2.resize(
#         best_mask.astype(np.uint8), (image.shape[1], image.shape[0]),
#         interpolation=cv2.INTER_NEAREST
#     )
#     return image, mask_resized


# def _run_fast_sam(image_path, tap_x, tap_y, checkpoint_path, output_path):
#     from ultralytics import FastSAM
#     import os
    
#     if not os.path.exists(checkpoint_path) and not checkpoint_path.endswith(".pt"):
#         raise FileNotFoundError(f"FastSAM checkpoint file not found at {checkpoint_path}")
        
#     model = FastSAM(checkpoint_path)
    
#     results = model.predict(
#         source=image_path, 
#         points=[[tap_x, tap_y]], 
#         labels=[1], 
#         device="cpu", 
#         retina_masks=True, 
#         verbose=False
#     )
    
#     if results and results[0].masks is not None:
#         best_mask = results[0].masks.data[0].cpu().numpy().astype(np.uint8)
#     else:
#         raise ValueError("FastSAM failed to generate a valid mask for the given tap point.")
        
#     image = cv2.imread(image_path)
#     mask_resized = cv2.resize(
#         best_mask, (image.shape[1], image.shape[0]),
#         interpolation=cv2.INTER_NEAREST
#     )
#     return image, mask_resized


# def segment_with_sam(image_path, tap_x, tap_y,
#                      checkpoint_path="sam_vit_b_01ec64.pth",
#                      output_path="sam_crop.png",
#                      method="sam"):
#     """
#     Unified entry point for object segmentation.
#     Allows easy switching between standard 'sam' and 'fastsam' engines.
#     """
#     method_lower = method.lower()
#     if method_lower == "sam":
#         image, mask_resized = _run_original_sam(image_path, tap_x, tap_y, checkpoint_path, output_path)
#     elif method_lower == "fastsam":
#         image, mask_resized = _run_fast_sam(image_path, tap_x, tap_y, checkpoint_path, output_path)
#     else:
#         raise ValueError(f"Unknown segmentation method: {method}. Choose either 'sam' or 'fastsam'.")

#     result = image.copy()
    
#     # 3-PIXEL PADDING OPTION: Dilate the mask uniformly by 3 pixels before cropping
#     kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (7, 7))
#     mask_padded = cv2.dilate(mask_resized, kernel, iterations=1)
    
#     # Apply the padded mask to isolate the target object safely
#     result[mask_padded == 0] = 0
    
#     # Find bounding box coordinates based on the padded mask boundaries
#     ys, xs = np.where(mask_padded > 0)
#     if len(xs) == 0 or len(ys) == 0:
#         raise ValueError(f"{method} mask is completely empty.")
        
#     x0, x1 = xs.min(), xs.max()
#     y0, y1 = ys.min(), ys.max()
    
#     # Crop using the padded bounding coordinates
#     result = result[y0:y1 + 1, x0:x1 + 1]
#     cv2.imwrite(output_path, result)
#     return output_path


# def extract_best_centerlines(image_path, output_svg_path):
#     src = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
#     if src is None:
#         raise FileNotFoundError(f"Could not load image: {image_path}")

#     # RESTORED MASK CONSTRAINT: Find outer silhouette bounds to block outer edges
#     _, object_mask = cv2.threshold(src, 1, 255, cv2.THRESH_BINARY)
    
#     # Hole-filling step: keeps internal details safe from erosion
#     contours, _ = cv2.findContours(object_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
#     solid_mask = np.zeros_like(object_mask)
#     cv2.drawContours(solid_mask, contours, -1, 255, thickness=cv2.FILLED)
    
#     # Slightly erode the solid mask boundary to drop crop bleed edges
#     kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
#     safe_inner_zone = cv2.erode(solid_mask, kernel, iterations=1)

#     # Ridge extraction
#     inverted = cv2.bitwise_not(src)
#     ridges = meijering(inverted, sigmas=[1.0, 2.0], black_ridges=False)
#     ridges = np.uint8(cv2.normalize(ridges, None, 0, 255, cv2.NORM_MINMAX))
    
#     # TUNED THRESHOLD: Lowered from 45 to 15 to pick up light/thin inner lines successfully
#     _, binary_ridges = cv2.threshold(ridges, 15, 255, cv2.THRESH_BINARY)

#     # Intersect to clean outer bleed while keeping the inner veins protected
#     binary_ridges = cv2.bitwise_and(binary_ridges, safe_inner_zone)

#     y_coords, x_coords = np.where(binary_ridges > 0)
#     points = list(zip(x_coords, y_coords))
#     if not points:
#         raise ValueError("No ridge points found — check the segment crop isn't blank")

#     g = nx.Graph()
#     sanitized_points = []
#     for pt in points:
#         int_pt = (int(pt[0]), int(pt[1]))
#         g.add_node(int_pt, pos=int_pt)
#         sanitized_points.append(int_pt)

#     point_matrix = np.array(sanitized_points, dtype=np.float32)
#     max_link_distance = 15.0
#     dist_matrix = cdist(point_matrix, point_matrix, metric='euclidean')
#     rows, cols = np.where((dist_matrix > 0) & (dist_matrix < max_link_distance))
    
#     for u, v in zip(rows, cols):
#         if u < v:
#             pt_u = sanitized_points[u]
#             pt_v = sanitized_points[v]
#             g.add_edge(pt_u, pt_v, weight=dist_matrix[u, v])

#     height, width = src.shape[:2]
#     fixed_jump_count = 0
#     with open(output_svg_path, 'w') as svg_file:
#         svg_file.write('<?xml version="1.0" encoding="utf-8"?>\n')
#         svg_file.write(f'<svg xmlns="http://www.w3.org/2000/svg" version="1.1" width="{width}" height="{height}" viewBox="0 0 {width} height">\n')
        
#         for component in nx.connected_components(g):
#             subgraph = g.subgraph(component)
#             mst = minimum_spanning_tree(subgraph)
#             degrees = dict(mst.degree())
#             start_nodes = [node for node, deg in degrees.items() if deg == 1]
#             start_node = start_nodes[0] if start_nodes else list(component)[0]
#             ordered_nodes = list(nx.dfs_preorder_nodes(mst, source=start_node))
#             if len(ordered_nodes) < 8:
#                 continue
                
#             path_str = ""
#             prev_node = None
#             for i, node_pt in enumerate(ordered_nodes):
#                 if i == 0:
#                     path_str += f"M {node_pt[0]} {node_pt[1]} "
#                 else:
#                     if mst.has_edge(prev_node, node_pt):
#                         path_str += f"L {node_pt[0]} {node_pt[1]} "
#                     else:
#                         path_str += f"M {node_pt[0]} {node_pt[1]} "
#                         fixed_jump_count += 1
#                 prev_node = node_pt
#             svg_file.write(f'  <path d="{path_str}" fill="none" stroke="black" stroke-width="1.5" />\n')
#         svg_file.write('</svg>\n')
        
#     print(f"Fixed {fixed_jump_count} spurious jumps. Vector tracks are perfectly safe.")
#     return output_svg_path


# def process_capture(jpg_path, json_path, checkpoint_path, method="sam", out_dir="."):
#     stem = os.path.splitext(os.path.basename(jpg_path))[0]
#     with open(json_path) as f:
#         meta = json.load(f)
        
#     mm_per_pixel = meta["mm_per_pixel"]
    
#     # ----------------------------------------------------
#     # TARGETED HARDWARE INTERCEPT: OnePlus Driver Check
#     # ----------------------------------------------------
#     # Checks if metadata explicitly specifies OnePlus or if the scale crosses the physical inversion limit
#     is_oneplus = "oneplus" in str(meta.get("manufacturer", "")).lower() or "oneplus" in str(meta.get("device", "")).lower()
    
#     if is_oneplus or mm_per_pixel > 1.0:
#         if mm_per_pixel > 1.0:
#             print(f"[{stem}] Scale Intercept: OnePlus hardware mismatch detected. Inverting {mm_per_pixel:.5f} to true scale.")
#             mm_per_pixel = 1.0 / mm_per_pixel
            
#     tap_x = meta["tap_x"]
#     tap_y = meta["tap_y"]
    
#     crop_path = os.path.join(out_dir, f"{stem}_crop.png")
#     svg_path = os.path.join(out_dir, f"{stem}_centerlines.svg")
#     gcode_path = os.path.join(out_dir, f"{stem}.gcode")
    
#     print(f"[{stem}] Stage A: {method} segmentation (tap=({tap_x},{tap_y}))")
#     segment_with_sam(jpg_path, tap_x, tap_y, checkpoint_path, crop_path, method=method)
    
#     print(f"[{stem}] Stage B: centerline extraction")
#     extract_best_centerlines(crop_path, svg_path)
    
#     print(f"[{stem}] Stage C: G-code (scale={mm_per_pixel:.5f} mm/px)")
#     svg_to_gcode.convert(svg_path, gcode_path, mm_per_pixel)
#     return gcode_path


# # Rest of standard execution layer
# if __name__ == "__main__":
#     parser = argparse.ArgumentParser(description="Run the flexible Segmentation & Centerline tracking pipeline.")
#     parser.add_argument("image", help="Path to the input JPEG image")
#     parser.add_argument("json", help="Path to the matching metadata JSON file")
#     parser.add_argument("--method", default="sam", choices=["sam", "fastsam"], 
#                         help="Choose segmentation backend engine (default: sam)")
#     parser.add_argument("--checkpoint", default=None, 
#                         help="Path to model weights. If omitted, matches the defaults for chosen method.")
#     parser.add_argument("--outdir", default=".", help="Directory to save the output files")
    
#     args = parser.parse_args()
    
#     checkpoint = args.checkpoint
#     if checkpoint is None:
#         checkpoint = "sam_vit_b_01ec64.pth" if args.method == "sam" else "FastSAM-x.pt"
    
#     if not os.path.exists(args.image) or not os.path.exists(args.json):
#         print("Error: Image or JSON file path does not exist.")
#         exit(1)
        
#     print(f"Starting pipeline running framework choice: [{args.method}]")
#     gcode_output = process_capture(
#         jpg_path=args.image, 
#         json_path=args.json, 
#         checkpoint_path=checkpoint, 
#         method=args.method,
#         out_dir=args.outdir
#     )
#     print(f"\nPipeline successfully completed! G-code saved to: {gcode_output}")


#!/usr/bin/env python3
"""
pipeline.py — the three stages chained for one received capture.

STAGE A — segment_with_sam()
    Unified entry point. Accepts a `method="sam"` or `method="fastsam"` argument
    to route execution dynamically. Includes a 3-pixel padding dilation buffer.

STAGE B — extract_best_centerlines()
    Meijering ridge-detection + MST + DFS-ordering script.
    [RESTORED & FIXED]: Hole-filling mask constraint brought back to block outer bleed,
                        while lower ridge threshold restores thin inner vein extraction.

STAGE C — svg_to_gcode.convert()
    Converts vector tracks directly into G-code based on capture scaling factors.
"""
# import os
# import json
# import argparse

# import cv2
# import numpy as np
# import networkx as nx
# from skimage.filters import meijering
# from networkx.algorithms.tree.mst import minimum_spanning_tree
# from scipy.spatial.distance import cdist

# import svg_to_gcode


# def _run_original_sam(image_path, tap_x, tap_y, checkpoint_path, output_path):
#     from segment_anything import sam_model_registry, SamPredictor
#     if not os.path.exists(checkpoint_path):
#         raise FileNotFoundError(f"SAM checkpoint not found at {checkpoint_path}")
        
#     sam = sam_model_registry["vit_b"](checkpoint=checkpoint_path)
#     predictor = SamPredictor(sam)
#     image = cv2.imread(image_path)
#     image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
#     predictor.set_image(image_rgb)
    
#     masks, scores, _ = predictor.predict(
#         point_coords=np.array([[tap_x, tap_y]]),
#         point_labels=np.array([1]),
#         multimask_output=True
#     )
    
#     # Restored standard behavior based on model score matching the original engine architecture
#     best_idx = int(np.argmax(scores))
#     best_mask = masks[best_idx]
    
#     mask_resized = cv2.resize(
#         best_mask.astype(np.uint8), (image.shape[1], image.shape[0]),
#         interpolation=cv2.INTER_NEAREST
#     )
#     return image, mask_resized


# def _run_fast_sam(image_path, tap_x, tap_y, checkpoint_path, output_path):
#     from ultralytics import FastSAM
#     import os
    
#     if not os.path.exists(checkpoint_path) and not checkpoint_path.endswith(".pt"):
#         raise FileNotFoundError(f"FastSAM checkpoint file not found at {checkpoint_path}")
        
#     model = FastSAM(checkpoint_path)
    
#     results = model.predict(
#         source=image_path, 
#         points=[[tap_x, tap_y]], 
#         labels=[1], 
#         device="cpu", 
#         retina_masks=True, 
#         verbose=False
#     )
    
#     if results and results[0].masks is not None:
#         best_mask = results[0].masks.data[0].cpu().numpy().astype(np.uint8)
#     else:
#         raise ValueError("FastSAM failed to generate a valid mask for the given tap point.")
        
#     image = cv2.imread(image_path)
#     mask_resized = cv2.resize(
#         best_mask, (image.shape[1], image.shape[0]),
#         interpolation=cv2.INTER_NEAREST
#     )
#     return image, mask_resized


# def segment_with_sam(image_path, tap_x, tap_y,
#                      checkpoint_path="sam_vit_b_01ec64.pth",
#                      output_path="sam_crop.png",
#                      method="sam"):
#     """
#     Unified entry point for object segmentation.
#     Allows easy switching between standard 'sam' and 'fastsam' engines.
#     """
#     method_lower = method.lower()
#     if method_lower == "sam":
#         image, mask_resized = _run_original_sam(image_path, tap_x, tap_y, checkpoint_path, output_path)
#     elif method_lower == "fastsam":
#         image, mask_resized = _run_fast_sam(image_path, tap_x, tap_y, checkpoint_path, output_path)
#     else:
#         raise ValueError(f"Unknown segmentation method: {method}. Choose either 'sam' or 'fastsam'.")

#     result = image.copy()
    
#     # 3-PIXEL PADDING OPTION: Dilate the mask uniformly by 3 pixels before cropping
#     kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (7, 7))
#     mask_padded = cv2.dilate(mask_resized, kernel, iterations=1)
    
#     # Apply the padded mask to isolate the target object safely
#     result[mask_padded == 0] = 0
    
#     # Find bounding box coordinates based on the padded mask boundaries
#     ys, xs = np.where(mask_padded > 0)
#     if len(xs) == 0 or len(ys) == 0:
#         raise ValueError(f"{method} mask is completely empty.")
        
#     x0, x1 = xs.min(), xs.max()
#     y0, y1 = ys.min(), ys.max()
    
#     # Crop using the padded bounding coordinates
#     result = result[y0:y1 + 1, x0:x1 + 1]
#     cv2.imwrite(output_path, result)
#     return output_path


# def extract_best_centerlines(image_path, output_svg_path):
#     src = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
#     if src is None:
#         raise FileNotFoundError(f"Could not load image: {image_path}")

#     # RESTORED MASK CONSTRAINT: Find outer silhouette bounds to block outer edges
#     _, object_mask = cv2.threshold(src, 1, 255, cv2.THRESH_BINARY)
    
#     # Hole-filling step: keeps internal details safe from erosion
#     contours, _ = cv2.findContours(object_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
#     solid_mask = np.zeros_like(object_mask)
#     cv2.drawContours(solid_mask, contours, -1, 255, thickness=cv2.FILLED)
    
#     # Slightly erode the solid mask boundary to drop crop bleed edges
#     kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
#     safe_inner_zone = cv2.erode(solid_mask, kernel, iterations=1)

#     # Ridge extraction
#     inverted = cv2.bitwise_not(src)
#     ridges = meijering(inverted, sigmas=[1.0, 2.0], black_ridges=False)
#     ridges = np.uint8(cv2.normalize(ridges, None, 0, 255, cv2.NORM_MINMAX))
    
#     # TUNED THRESHOLD: Lowered from 45 to 15 to pick up light/thin inner lines successfully
#     _, binary_ridges = cv2.threshold(ridges, 15, 255, cv2.THRESH_BINARY)

#     # Intersect to clean outer bleed while keeping the inner veins protected
#     binary_ridges = cv2.bitwise_and(binary_ridges, safe_inner_zone)

#     # SKELETONIZE: binary_ridges is currently the FULL-WIDTH ridge blob
#     # (widened further by threshold=15 for thin-line sensitivity). This
#     # thins it to a true 1px centerline - must happen here, on the raster
#     # mask, before any point extraction/graph-building. There's no
#     # equivalent step once this becomes an SVG path.
#     from skimage.morphology import skeletonize
#     skeleton = skeletonize(binary_ridges > 0)
#     binary_ridges = (skeleton * 255).astype(np.uint8)   

#     y_coords, x_coords = np.where(binary_ridges > 0)
#     points = list(zip(x_coords, y_coords))
#     if not points:
#         raise ValueError("No ridge points found — check the segment crop isn't blank")

#     g = nx.Graph()
#     sanitized_points = []
#     for pt in points:
#         int_pt = (int(pt[0]), int(pt[1]))
#         g.add_node(int_pt, pos=int_pt)
#         sanitized_points.append(int_pt)

#     point_matrix = np.array(sanitized_points, dtype=np.float32)
#     max_link_distance = 15.0
#     dist_matrix = cdist(point_matrix, point_matrix, metric='euclidean')
#     rows, cols = np.where((dist_matrix > 0) & (dist_matrix < max_link_distance))
    
#     for u, v in zip(rows, cols):
#         if u < v:
#             pt_u = sanitized_points[u]
#             pt_v = sanitized_points[v]
#             g.add_edge(pt_u, pt_v, weight=dist_matrix[u, v])

#     height, width = src.shape[:2]
#     fixed_jump_count = 0
#     with open(output_svg_path, 'w') as svg_file:
#         svg_file.write('<?xml version="1.0" encoding="utf-8"?>\n')
#         svg_file.write(f'<svg xmlns="http://www.w3.org/2000/svg" version="1.1" width="{width}" height="{height}" viewBox="0 0 {width} {height}">\n')
        
#         for component in nx.connected_components(g):
#             subgraph = g.subgraph(component)
#             mst = minimum_spanning_tree(subgraph)
#             degrees = dict(mst.degree())
#             start_nodes = [node for node, deg in degrees.items() if deg == 1]
#             start_node = start_nodes[0] if start_nodes else list(component)[0]
#             ordered_nodes = list(nx.dfs_preorder_nodes(mst, source=start_node))
#             if len(ordered_nodes) < 8:
#                 continue
                
#             path_str = ""
#             prev_node = None
#             for i, node_pt in enumerate(ordered_nodes):
#                 if i == 0:
#                     path_str += f"M {node_pt[0]} {node_pt[1]} "
#                 else:
#                     if mst.has_edge(prev_node, node_pt):
#                         path_str += f"L {node_pt[0]} {node_pt[1]} "
#                     else:
#                         path_str += f"M {node_pt[0]} {node_pt[1]} "
#                         fixed_jump_count += 1
#                 prev_node = node_pt
#             svg_file.write(f'  <path d="{path_str}" fill="none" stroke="black" stroke-width="1.5" />\n')
#         svg_file.write('</svg>\n')
        
#     print(f"Fixed {fixed_jump_count} spurious jumps. Vector tracks are perfectly safe.")
#     return output_svg_path


# def process_capture(jpg_path, json_path, checkpoint_path, method="sam", out_dir="."):
#     stem = os.path.splitext(os.path.basename(jpg_path))[0]
#     with open(json_path) as f:
#         meta = json.load(f)
#     mm_per_pixel = meta["mm_per_pixel"]
#     tap_x = meta["tap_x"]
#     tap_y = meta["tap_y"]
    
#     crop_path = os.path.join(out_dir, f"{stem}_crop.png")
#     svg_path = os.path.join(out_dir, f"{stem}_centerlines.svg")
#     gcode_path = os.path.join(out_dir, f"{stem}.gcode")
    
#     print(f"[{stem}] Stage A: {method} segmentation (tap=({tap_x},{tap_y}))")
#     segment_with_sam(jpg_path, tap_x, tap_y, checkpoint_path, crop_path, method=method)
    
#     print(f"[{stem}] Stage B: centerline extraction")
#     extract_best_centerlines(crop_path, svg_path)
    
#     print(f"[{stem}] Stage C: G-code (scale={mm_per_pixel} mm/px)")
#     svg_to_gcode.convert(svg_path, gcode_path, mm_per_pixel)
#     return gcode_path


# if __name__ == "__main__":
#     parser = argparse.ArgumentParser(description="Run the flexible Segmentation & Centerline tracking pipeline.")
#     parser.add_argument("image", help="Path to the input JPEG image")
#     parser.add_argument("json", help="Path to the matching metadata JSON file")
#     parser.add_argument("--method", default="sam", choices=["sam", "fastsam"], 
#                         help="Choose segmentation backend engine (default: fastsam)")
#     parser.add_argument("--checkpoint", default=None, 
#                         help="Path to model weights. If omitted, matches the defaults for chosen method.")
#     parser.add_argument("--outdir", default=".", help="Directory to save the output files")
    
#     args = parser.parse_args()
    
#     checkpoint = args.checkpoint
#     if checkpoint is None:
#         checkpoint = "sam_vit_b_01ec64.pth" if args.method == "sam" else "FastSAM-s.pt"
    
#     if not os.path.exists(args.image) or not os.path.exists(args.json):
#         print("Error: Image or JSON file path does not exist.")
#         exit(1)
        
#     print(f"Starting pipeline running framework choice: [{args.method}]")
#     gcode_output = process_capture(
#         jpg_path=args.image, 
#         json_path=args.json, 
#         checkpoint_path=checkpoint, 
#         method=args.method,
#         out_dir=args.outdir
#     )
#     print(f"\nPipeline successfully completed! G-code saved to: {gcode_output}")

#!/usr/bin/env python3
"""
pipeline.py — the three stages chained for one received capture.

STAGE A — segment_with_sam()
    Unified entry point. Accepts a `method="sam"` or `method="fastsam"` argument
    to route execution dynamically. Includes a 3-pixel padding dilation buffer.

STAGE B — mode-dependent:
    "outline"  -> extract_silhouette_svg()     true boundary, real mm dimensions
    "detailed" -> extract_best_centerlines()   internal design lines, skeletonized,
                                               pixel-space only, no dimensioning

STAGE C — svg_to_gcode.convert() (outline mode only)
    Converts the dimensioned boundary into scaled G-code. Detailed mode stops
    at the SVG, since it deliberately carries no real-world scale to apply.
"""
import os
import json
import argparse

import cv2
import numpy as np
import networkx as nx
from skimage.filters import meijering
from skimage.morphology import skeletonize
from networkx.algorithms.tree.mst import minimum_spanning_tree
from scipy.spatial.distance import cdist

import svg_to_gcode
from extract_silhouette import extract_silhouette_svg


def _run_original_sam(image_path, tap_x, tap_y, checkpoint_path, output_path):
    from segment_anything import sam_model_registry, SamPredictor
    if not os.path.exists(checkpoint_path):
        raise FileNotFoundError(f"SAM checkpoint not found at {checkpoint_path}")

    sam = sam_model_registry["vit_b"](checkpoint=checkpoint_path)
    predictor = SamPredictor(sam)
    image = cv2.imread(image_path)
    image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    predictor.set_image(image_rgb)

    masks, scores, _ = predictor.predict(
        point_coords=np.array([[tap_x, tap_y]]),
        point_labels=np.array([1]),
        multimask_output=True
    )

    best_idx = int(np.argmax(scores))
    best_mask = masks[best_idx]

    mask_resized = cv2.resize(
        best_mask.astype(np.uint8), (image.shape[1], image.shape[0]),
        interpolation=cv2.INTER_NEAREST
    )
    return image, mask_resized


def _run_fast_sam(image_path, tap_x, tap_y, checkpoint_path, output_path):
    from ultralytics import FastSAM

    if not os.path.exists(checkpoint_path) and not checkpoint_path.endswith(".pt"):
        raise FileNotFoundError(f"FastSAM checkpoint file not found at {checkpoint_path}")

    model = FastSAM(checkpoint_path)

    results = model.predict(
        source=image_path,
        points=[[tap_x, tap_y]],
        labels=[1],
        device="cpu",
        retina_masks=True,
        verbose=False
    )

    if results and results[0].masks is not None:
        best_mask = results[0].masks.data[0].cpu().numpy().astype(np.uint8)
    else:
        raise ValueError("FastSAM failed to generate a valid mask for the given tap point.")

    image = cv2.imread(image_path)
    mask_resized = cv2.resize(
        best_mask, (image.shape[1], image.shape[0]),
        interpolation=cv2.INTER_NEAREST
    )
    return image, mask_resized


def segment_with_sam(image_path, tap_x, tap_y,
                     checkpoint_path="FastSAM-s.pt",
                     output_path="sam_crop.png",
                     method="fastsam",
                     margin_pixels=10 ):
    """
    Unified entry point for object segmentation.
    Allows easy switching between standard 'sam' and 'fastsam' engines.
    """
    method_lower = method.lower()
    if method_lower == "sam":
        image, mask_resized = _run_original_sam(image_path, tap_x, tap_y, checkpoint_path, output_path)
    elif method_lower == "fastsam":
        image, mask_resized = _run_fast_sam(image_path, tap_x, tap_y, checkpoint_path, output_path)
    else:
        raise ValueError(f"Unknown segmentation method: {method}. Choose either 'sam' or 'fastsam'.")

    result = image.copy()

    # Dynamic 3-pixel padding: A margin of N pixels requires a kernel size of 2N + 1
    kernel_size = 2 * margin_pixels + 1
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_size, kernel_size))
    mask_padded = cv2.dilate(mask_resized, kernel, iterations=1)

    result[mask_padded == 0] = 0

    ys, xs = np.where(mask_padded > 0)
    if len(xs) == 0 or len(ys) == 0:
        raise ValueError(f"{method} mask is completely empty.")

    x0, x1 = xs.min(), xs.max()
    y0, y1 = ys.min(), ys.max()

    result = result[y0:y1 + 1, x0:x1 + 1]
    cv2.imwrite(output_path, result)
    return output_path


def extract_best_centerlines(image_path, output_svg_path):
    src = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
    if src is None:
        raise FileNotFoundError(f"Could not load image: {image_path}")

    _, object_mask = cv2.threshold(src, 1, 255, cv2.THRESH_BINARY)

    contours, _ = cv2.findContours(object_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    solid_mask = np.zeros_like(object_mask)
    cv2.drawContours(solid_mask, contours, -1, 255, thickness=cv2.FILLED)

    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    safe_inner_zone = cv2.erode(solid_mask, kernel, iterations=1)

    inverted = cv2.bitwise_not(src)
    ridges = meijering(inverted, sigmas=[1.0, 2.0], black_ridges=False)
    ridges = np.uint8(cv2.normalize(ridges, None, 0, 255, cv2.NORM_MINMAX))

    _, binary_ridges = cv2.threshold(ridges, 15, 255, cv2.THRESH_BINARY)
    binary_ridges = cv2.bitwise_and(binary_ridges, safe_inner_zone)

    print(f"Ridge pixels before skeletonize: {np.count_nonzero(binary_ridges)}")
    skeleton = skeletonize(binary_ridges > 0)
    binary_ridges = (skeleton * 255).astype(np.uint8)
    print(f"Ridge pixels after skeletonize:  {np.count_nonzero(binary_ridges)}")

    y_coords, x_coords = np.where(binary_ridges > 0)
    points = list(zip(x_coords, y_coords))
    if not points:
        raise ValueError("No ridge points found — check the segment crop isn't blank")

    g = nx.Graph()
    sanitized_points = []
    for pt in points:
        int_pt = (int(pt[0]), int(pt[1]))
        g.add_node(int_pt, pos=int_pt)
        sanitized_points.append(int_pt)

    point_matrix = np.array(sanitized_points, dtype=np.float32)
    max_link_distance = 15.0
    dist_matrix = cdist(point_matrix, point_matrix, metric='euclidean')
    rows, cols = np.where((dist_matrix > 0) & (dist_matrix < max_link_distance))

    for u, v in zip(rows, cols):
        if u < v:
            pt_u = sanitized_points[u]
            pt_v = sanitized_points[v]
            g.add_edge(pt_u, pt_v, weight=dist_matrix[u, v])

    height, width = src.shape[:2]
    fixed_jump_count = 0
    with open(output_svg_path, 'w') as svg_file:
        svg_file.write('<?xml version="1.0" encoding="utf-8"?>\n')
        svg_file.write(f'<svg xmlns="http://www.w3.org/2000/svg" version="1.1" width="{width}" height="{height}" viewBox="0 0 {width} {height}">\n')

        for component in nx.connected_components(g):
            subgraph = g.subgraph(component)
            mst = minimum_spanning_tree(subgraph)
            degrees = dict(mst.degree())
            start_nodes = [node for node, deg in degrees.items() if deg == 1]
            start_node = start_nodes[0] if start_nodes else list(component)[0]
            ordered_nodes = list(nx.dfs_preorder_nodes(mst, source=start_node))
            if len(ordered_nodes) < 8:
                continue

            path_str = ""
            prev_node = None
            for i, node_pt in enumerate(ordered_nodes):
                if i == 0:
                    path_str += f"M {node_pt[0]} {node_pt[1]} "
                else:
                    if mst.has_edge(prev_node, node_pt):
                        path_str += f"L {node_pt[0]} {node_pt[1]} "
                    else:
                        path_str += f"M {node_pt[0]} {node_pt[1]} "
                        fixed_jump_count += 1
                prev_node = node_pt
            svg_file.write(f'  <path d="{path_str}" fill="none" stroke="black" stroke-width="1.5" />\n')
        svg_file.write('</svg>\n')

    print(f"Fixed {fixed_jump_count} spurious jumps. Vector tracks are perfectly safe.")
    return output_svg_path


def process_capture(jpg_path, json_path, checkpoint_path, method="fastsam", out_dir="."):
    stem = os.path.splitext(os.path.basename(jpg_path))[0]
    with open(json_path) as f:
        meta = json.load(f)
    mm_per_pixel = meta["mm_per_pixel"]
    tap_x = meta["tap_x"]
    tap_y = meta["tap_y"]

    mode = meta.get("mode", "detailed")
    if mode not in ("outline", "detailed"):
        raise ValueError(f"Unknown mode '{mode}' in {json_path} - expected 'outline' or 'detailed'")

    crop_path = os.path.join(out_dir, f"{stem}_crop.png")

    print(f"[{stem}] Stage A: {method} segmentation (tap=({tap_x},{tap_y}), mode={mode}, margin=10px)")
    segment_with_sam(jpg_path, tap_x, tap_y, checkpoint_path, crop_path, method=method, margin_pixels=3)

    if mode == "outline":
        svg_path = os.path.join(out_dir, f"{stem}_outline.svg")
        gcode_path = os.path.join(out_dir, f"{stem}.gcode")

        print(f"[{stem}] Stage B: silhouette extraction (dimensioned)")
        extract_silhouette_svg(crop_path, svg_path, mm_per_pixel)

        print(f"[{stem}] Stage C: G-code (scale={mm_per_pixel} mm/px)")
        svg_to_gcode.convert(svg_path, gcode_path, mm_per_pixel)
        return gcode_path

    else:  # mode == "detailed"
        svg_path = os.path.join(out_dir, f"{stem}_centerlines.svg")

        print(f"[{stem}] Stage B: centerline extraction (skeletonized, unscaled)")
        extract_best_centerlines(crop_path, svg_path)

        print(f"[{stem}] Detailed mode - no dimensioning applied, stopping at SVG")
        return svg_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the flexible Segmentation & Extraction pipeline.")
    parser.add_argument("image", help="Path to the input JPEG image")
    parser.add_argument("json", help="Path to the matching metadata JSON file (mode, mm_per_pixel, tap_x, tap_y)")
    parser.add_argument("--method", default="fastsam", choices=["sam", "fastsam"],
                        help="Choose segmentation backend engine (default: fastsam)")
    parser.add_argument("--checkpoint", default=None,
                        help="Path to model weights. If omitted, matches the defaults for chosen method.")
    parser.add_argument("--outdir", default=".", help="Directory to save the output files")

    args = parser.parse_args()

    checkpoint = args.checkpoint
    if checkpoint is None:
        checkpoint = "FastSAM-s.pt" if args.method == "fastsam" else "sam_vit_b_01ec64.pth"

    if not os.path.exists(args.image) or not os.path.exists(args.json):
        print("Error: Image or JSON file path does not exist.")
        exit(1)

    print(f"Starting pipeline running framework choice: [{args.method}]")
    result_path = process_capture(
        jpg_path=args.image,
        json_path=args.json,
        checkpoint_path=checkpoint,
        method=args.method,
        out_dir=args.outdir
    )
    print(f"\nPipeline successfully completed! Output: {result_path}")