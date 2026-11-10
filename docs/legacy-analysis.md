# Legacy repo analysis (legacy_scripts/IA-in-Robotica)

Only the parts relevant to the swarm project. The rest is course material (lab_1_studenti, lab_CNN, Romanian lab docs) and is not needed.

## Reusable pieces

### 1. Flight stack: crazyflie_progressive_square_v2 (cleanest, Windows-tested)
- mocap.py: PoseBuffer (thread-safe latest pose per body), per-drone axis map, yaw from quaternion, extpos/extpose sender (50 Hz), Kalman setup (stabilizer.estimator=2, kalman.initialYaw, resetEstimation), wait_until_tracking (estimate vs OptiTrack within 0.20 m / 15 deg), position-setpoint ramps (20 Hz).
- motive.py: own NatNet frame parser, no SDK. Multicast 239.255.42.99, UDP 1511. MotiveListener(client_ip, on_rigid_body(rb_id, pos, rot)). One listener per machine (it binds port 1511).
- calibrate_axes.py: hand-slide calibration (up, nose, left) -> axis_calibration_<body_id>.json {matrix 3x3, yaw_offset_deg}. One file per drone. v2 has ids 2-9.
- fly_square.py + runner.py + step1..4: takeoff, waypoint following with velocity/yaw/accel limits, fence, speed caps (0.5 m/s, 60 deg/s), Ctrl+C = land + disarm, arm/disarm calls.
- gains.py: KP 0.8, V_MAX 0.40, V_CRUISE 0.35, A_MAX 0.6, etc. Good conservative starting values for the swarm.

### 2. AI-deck capture: aideck_capture/capture.py
- Standard-library TCP client for Bitcraze wifi-img-streamer. Default 192.168.4.1:5000 (deck in access-point mode).
- Frame: CPX header, magic 0xBC, width, height, depth, format (0 = raw Bayer/gray -> PNG, else JPEG), size, then chunked payload.
- Timestamp = PC receive time in ms (not camera time). Fine for hover captures.
- Auto-reconnect, FPS print every 2 s.

### 3. Depth: person_follower/depth_yolo_person_tracking
- Model: yolo26n-depth.pt (Ultralytics, task=depth), imgsz 768, output = metric depth in meters per pixel (claimed by the script; unverified on AI-deck images).
- webcam_pointcloud.py: depth_to_points() = pinhole unprojection, camera frame X right, Y up, Z forward. Intrinsics ASSUMED (70 deg horizontal FOV), stride and z-min/z-max filtering. Frames are not fused.
- Runs on GPU (device 0), threaded inference loop with "latest frame wins" queue.
- person_track.py / follow_me.py: YOLO person detection + depth; follow_me uses AI-deck frames. Useful for the detection route.

## Facts you gave
- Body ID is 1-9, set by the drone firmware. Radio address is E7E7E7E7E<id>, the last hex digit is the body ID (e.g. 0xE7E7E7E7E5 = body 5).
- Default URI in the code: radio://0/80/2M/E7E7E7E7E7 (channel 80, 2M).

## Things that must change for a 5-drone swarm
1. One process, one drone today. Need a swarm manager: one Motive listener fanning out to 5 PoseBuffers, 5 Crazyflie links, per-drone state machines.
2. mocap.motive_to_crazyflie() uses a process-wide axis matrix by default. Must pass each drone's own matrix (PoseBuffer already supports axis_matrix=).
3. URIs all use radio://0/... Multiple Crazyflies per Crazyradio and several radios need explicit radio indices; verify cflib shared-radio behaviour at the real pose rate.
4. Calibration and takeoff are interactive (hold drone still, press Enter, face the wall). Needs an automated per-drone start.
5. The AI deck is in access-point mode at 192.168.4.1. A laptop joins one AP at a time, and 5 decks cannot share that address. Decks must run in station mode on one router, each with its own IP (firmware/config change, and the laptop streams from 5 IPs).
6. Depth intrinsics are assumed. Need real Himax intrinsics and a check that yolo26n-depth works on 324x244 grayscale (it expects normal camera images).
7. Safety: only a per-drone fence exists. Needs the cross-drone separation monitor.

## Decision to make: ROS 2 or Python-first
Your Windows stack already flies a drone with cflib and Motive multicast. ROS 2 would add Docker, USB, multicast and Pi work before any mapping exists. Python-first (extend the current code to a swarm manager, add depth, merge and diff in Python, view in Open3D or Rerun) gets to a demo faster. ROS 2 can wrap the same modules later.
