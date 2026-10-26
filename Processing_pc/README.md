# AuraCNC: Design Extraction Pipeline

Turns a phone scan of a physical object or drawing into machine-ready G-code, with real-world dimensions and no manual measuring or hand-built CAD model.

Built in 24 hours at Qualcomm's Snapdragon Multiverse Hackathon (Noida), where AuraCNC was selected among the Top 60 projects across India.

This README covers the AI and vectorization pipeline that runs on the Arduino UNO Q.

## Pipeline

![AuraCNC pipeline flow](docs/pipeline_flow.svg)

| Stage | What it does |
|---|---|
| Capture | The Android app uses ARCore to capture the object along with depth and a mm-per-pixel scale, so dimensions are known from the start. |
| Transport | Metadata and the JPEG (base64) are pushed to Firebase Realtime Database. |
| Listener | The UNO Q watches for new nodes, decodes the image and hands it to the pipeline. |
| Segmentation | FastSAM-s produces a mask of the object. |
| Vectorization | Contour extraction for 3D objects with a clean mask; skeleton extraction for flat drawings and ink strokes on paper. |
| Path tracing | An MST + DFS script turns the extracted pixels into ordered, connected paths a machine can follow. |
| Scaling | Paths are scaled to millimetres using the ARCore scale and written out as SVG. |
| G-code | The SVG is converted to G-code toolpaths for the machine. |

## Why two vectorization modes

A 3D object has a true boundary, so the silhouette contour is the right shape to cut. A drawing on paper is coplanar with its background, so the useful geometry is the stroke centreline rather than an outline. The pipeline picks the method based on the object type.

## Why MST + DFS

Extracted pixels form a graph. The minimum spanning tree removes redundant connections and noise spurs so each stroke stays a clean tree, and DFS walks that tree to produce an ordered path the machine can follow in one continuous motion.

## Results

- Sub-millimetre precision on extracted dimensions using ARCore scale, carried through to the final G-code.
- Scan to G-code in seconds of on-device processing, replacing hours of manual CAD work for a scaled design.

## Hardware and models

- Arduino UNO Q with the Qualcomm Dragonwing processor (runs the full pipeline on-device)
- FastSAM-s pretrained segmentation model

## Team

- [Luvya Lamba](https://www.linkedin.com/in/luvya-lamba/): team lead, firmware, edge interfacing, hardware, pitch
- [Dhriti Dhall](https://www.linkedin.com/in/dhriti-dhall/): AI modelling, AR interfacing, data handling, concept refinement
- [Rishab Kumar Jha](https://www.linkedin.com/in/rishab-kumar-jha-aa08b6330/): system architecture, app development
