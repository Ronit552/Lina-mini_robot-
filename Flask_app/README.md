# LINA - Web Interface

LINA is a web-based dashboard and control interface for a robotics project.

## Recent Updates
- Fixed layout issues across the Cockpit and Debug pages where flex containers (like the system logs and console output) would expand infinitely and break the layout boundary grid rows. This was resolved by properly implementing `min-height: 0` on flex items and grid containers to permit the content to scroll.
- **Major UI Redesign:** Replaced the default camera video feed interface with a purely sensor-driven dashboard featuring a rotating algorithmic radar UI (simulating the rotating ultrasonic head), a frontal proximity HUD array, and visual 3-state IR indicators. All sensor simulations natively operate on the frontend via Canvas APIs and Javascript timed loops for immediate UI verification.
- **Drive & Calibrate UI Update:** Replaced the complex Nipple.js 360-degree joystick with a discrete, 5-button D-pad interface (Up, Down, Left, Right, Stop) aligned with the custom cyberpunk styling. Additionally, replaced the manual Head Pan slider with fixed-value preset buttons (0°, 45°, 90°, 135°, 180°).
- **Backend Rewrite (`app.py`):** Completely restructured with a structured `robot_state` dict, a `broadcast_telemetry()` helper, safety-guarded D-pad control handler, clearly labelled `TODO` UDP stubs for ESP32 integration, and extensive inline documentation covering the hardware architecture, JSON packet formats, and motor command table.
