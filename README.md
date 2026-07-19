# AuraCNC

AuraCNC is an AI-powered rapid prototyping platform that transforms images taken on the fly into CNC-ready G-code toolpaths within a click of a button. By combining intelligent object segmentation, automatic scaling, and automated G-code generation, AuraCNC enables users to convert real-world objects into manufacturable 2D prototypes quickly, accurately, and with minimal user interaction.

## Workflow

1. Connect to the AuraCNC device.
2. Authenticate with the device.
3. Scan or capture the target object.
4. Transfer the image and measurement metadata to the IoT hardware.
5. Perform AI-accelerated object segmentation and G-code generation on the IoT hardware.
6. Forward the generated G-code to the CNC edge device.
7. Begin engraving or cutting automatically.

## Hardware Stack

- AR-enabled Android smartphone
- IoT hardware (AI processing unit)
- Two-axis gyro-mirror laser engraver/cutter

## Software Stack

- Android Studio
- Kotlin
- ARCore

## AI Stack

- Segment Anything Model (SAM) / FastSAM
- Qualcomm AI Hub

## Future Improvements

- Extend support to 3-axis and 5-axis CNC systems
- 3D object reconstruction and machining
- Native iOS application
- Support for additional AI segmentation models

## Authors

- Luvya Lamba
- Rishab Kumar Jha
- Dhriti Dhall
