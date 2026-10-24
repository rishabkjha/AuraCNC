The `AuraCNC.zip` file contains the development package for the custom Android application.

### 🛠️ Core Foundation & Architecture

AuraCNC leverages the Google ARCore "Hello AR" sample application as its structural foundation. 

Instead of building a spatial computing pipeline from scratch, AuraCNC utilises the core capabilities of the Hello AR boilerplate to manage the physical camera view and spatial telemetry. This allows the system to focus directly on edge AI processing, image acquisition, and G-code generation.

By inheriting this framework, AuraCNC utilizes ARCore and Firebase for three critical steps in the data pipeline:
* **Camera Telemetry & Image Capture:** Reuses the optimised `GLSurfaceView` and camera preview pipeline to capture clean, high-resolution snapshots of target objects on the fly.
* **Spatial Measurement Metadata:** Leverages ARCore's underlying depth estimation and plane detection to calculate accurate, real-world metric dimensions of the scanned object. This spatial metadata is bundled with the captured image to ensure the generated G-code tools are automatically scaled to exact real-world dimensions.
* **Firebase Realtime Database Bridge:** Acts as a low-latency data stream. Images and real-world scale metadata are pushed to a synchronised database node, instantly triggering the AI segmentation pipeline on the listening IoT hardware.

---

### 🔄 Data & Execution Workflow

1. **Local Connection & Auth:** The Android application discovers and authenticates directly with the local IoT hardware using application-side device discovery.
2. **Data Acquisition:** The user scans the target object using the ARCore camera framework.
3. **Data Transit:** The app pushes the captured image payload and extracted scale metadata directly to a dedicated node in the Firebase Realtime Database.
4. **Edge AI Segmentation:** The IoT hardware, constantly listening to that same Firebase node, detects the new entry, pulls the data, and runs the Fast Segment Anything Model (Fast-SAM) optimised via the Qualcomm AI Hub.
5. **Fabrication:** The IoT hardware compiles the segment boundaries through a pattern extraction pipeline to produce a scaled G-code file and forwards it to the two-axis gyro-mirror laser engraver.
