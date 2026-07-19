# """
# PC-side receiver.
# Protocol:
# [4B big-endian int: JPEG length][JPEG bytes]
# [4B big-endian float: mm_per_pixel]
# [4B big-endian int: tapX (crop-relative)]
# [4B big-endian int: tapY (crop-relative)]

# Flow: receive + save depth-isolated crop -> ACK the phone -> save
# capture_<ts>.jpg / capture_<ts>.json -> hand both straight to
# pipeline.process_capture(), which does SAM segmentation (tap-prompted)
# -> centerline extraction -> scaled G-code, in one call.

# NOTE: this intentionally does NOT use pipeline_stages_2_3_4 or
# refine_with_fastsam from the original version — those are swapped out
# for pipeline.process_capture for now. Re-wire back in later if the
# edges/vectorize/clean stages still need to sit in front of or behind
# this new path.
# """

# import socket
# import struct
# import os
# import json
# import time

# from pipeline3px import process_capture

# HOST = "0.0.0.0"
# PORT = 5001
# SAVE_DIR = "captures"
# SAM_CHECKPOINT = "sam_vit_b_01ec64.pth"

# os.makedirs(SAVE_DIR, exist_ok=True)


# def recv_exact(conn, n):
#     buf = b""
#     while len(buf) < n:
#         chunk = conn.recv(n - len(buf))
#         if not chunk:
#             raise ConnectionError("Connection closed before full payload received")
#         buf += chunk
#     return buf


# def main():
#     server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
#     server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
#     server.bind((HOST, PORT))
#     server.listen(1)
#     print(f"Listening on {HOST}:{PORT} - waiting for phone capture...")

#     while True:
#         conn, addr = server.accept()
#         print(f"Connection from {addr}")
#         try:
#             with conn:
#                 length_bytes = recv_exact(conn, 4)
#                 jpeg_length = struct.unpack(">I", length_bytes)[0]
#                 print(f"Receiving {jpeg_length} bytes...")

#                 jpeg_bytes = recv_exact(conn, jpeg_length)

#                 mm_per_pixel_bytes = recv_exact(conn, 4)
#                 mm_per_pixel = struct.unpack(">f", mm_per_pixel_bytes)[0]

#                 tap_x_bytes = recv_exact(conn, 4)
#                 tap_x = struct.unpack(">i", tap_x_bytes)[0]

#                 tap_y_bytes = recv_exact(conn, 4)
#                 tap_y = struct.unpack(">i", tap_y_bytes)[0]

#                 # ACK immediately - the phone is blocked waiting for this, don't
#                 # make it wait through SAM + the centerline/G-code pipeline too.
#                 conn.sendall(b"\x01")

#                 timestamp = int(time.time())
#                 filepath = os.path.join(SAVE_DIR, f"capture_{timestamp}.jpg")
#                 with open(filepath, "wb") as f:
#                     f.write(jpeg_bytes)

#                 meta_path = os.path.join(SAVE_DIR, f"capture_{timestamp}.json")
#                 with open(meta_path, "w") as f:
#                     json.dump({"mm_per_pixel": mm_per_pixel, "tap_x": tap_x, "tap_y": tap_y}, f)

#                 print(f"Saved: {filepath} (mm_per_pixel={mm_per_pixel:.4f}, tap=({tap_x},{tap_y}))")

#                 out_dir = os.path.join(SAVE_DIR, f"result_{timestamp}")
#                 os.makedirs(out_dir, exist_ok=True)

#                 gcode_path = process_capture(
#                     jpg_path=filepath,
#                     json_path=meta_path,
#                     checkpoint_path=SAM_CHECKPOINT,
#                     out_dir=out_dir,
#                 )
#                 print(f"Pipeline result: {gcode_path}")

#         except Exception as e:
#             print(f"Error handling connection: {e}")


# if __name__ == "__main__":
#     main()

#!/usr/bin/env python3
"""
PC-side receiver.
Protocol:
[4B big-endian int: JPEG length][JPEG bytes]
[4B big-endian float: mm_per_pixel]
[4B big-endian int: tapX (crop-relative)]
[4B big-endian int: tapY (crop-relative)]
[1B byte: mode flag (0x00 = outline, 0x01 = detailed)]

Flow: receive + save capture -> ACK the phone -> save
capture_<ts>.jpg / capture_<ts>.json -> hand both to
pipeline.process_capture(), which routes to outline or detailed mode
based on the JSON's "mode" key.
"""

import socket
import struct
import os
import json
import time

from pipeline import process_capture 

HOST = "0.0.0.0"
PORT = 5001
SAVE_DIR = "captures"
SAM_CHECKPOINT = "FastSAM-s.pt"

os.makedirs(SAVE_DIR, exist_ok=True)


def recv_exact(conn, n):
    buf = b""
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("Connection closed before full payload received")
        buf += chunk
    return buf


def main():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((HOST, PORT))
    server.listen(1)
    print(f"Listening on {HOST}:{PORT} - waiting for phone capture...")

    while True:
        conn, addr = server.accept()
        print(f"Connection from {addr}")
        try:
            with conn:
                # 1. Read image data
                length_bytes = recv_exact(conn, 4)
                jpeg_length = struct.unpack(">I", length_bytes)[0]
                print(f"Receiving {jpeg_length} bytes...")
                jpeg_bytes = recv_exact(conn, jpeg_length)

                # 2. Read physical properties
                mm_per_pixel_bytes = recv_exact(conn, 4)
                mm_per_pixel = struct.unpack(">f", mm_per_pixel_bytes)[0]

                # 3. Read tap locations
                tap_x_bytes = recv_exact(conn, 4)
                tap_x = struct.unpack(">i", tap_x_bytes)[0]

                tap_y_bytes = recv_exact(conn, 4)
                tap_y = struct.unpack(">i", tap_y_bytes)[0]

                # 4. Read mode flag sent from the phone app (1 byte)
                mode_byte = recv_exact(conn, 1)
                mode_val = struct.unpack(">B", mode_byte)[0]
                
                # Route values: 0 maps to outline, anything else (like 1) to detailed
                mode = "outline" if mode_val == 0 else "detailed"

                # ACK immediately - don't block the phone app UI during processing
                conn.sendall(b"\x01")

                timestamp = int(time.time())
                filepath = os.path.join(SAVE_DIR, f"capture_{timestamp}.jpg")
                with open(filepath, "wb") as f:
                    f.write(jpeg_bytes)

                meta_path = os.path.join(SAVE_DIR, f"capture_{timestamp}.json")
                with open(meta_path, "w") as f:
                    json.dump({
                        "mm_per_pixel": mm_per_pixel, 
                        "tap_x": tap_x, 
                        "tap_y": tap_y,
                        "mode": mode
                    }, f)

                print(f"Saved: {filepath} (mm_per_pixel={mm_per_pixel:.4f}, tap=({tap_x},{tap_y}), mode={mode})")

                out_dir = os.path.join(SAVE_DIR, f"result_{timestamp}")
                os.makedirs(out_dir, exist_ok=True)

                result_path = process_capture(
                    jpg_path=filepath,
                    json_path=meta_path,
                    checkpoint_path=SAM_CHECKPOINT,
                    method="fastsam",
                    out_dir=out_dir,
                )
                print(f"Pipeline result: {result_path}")

        except Exception as e:
            print(f"Error handling connection: {e}")


if __name__ == "__main__":
    main()
