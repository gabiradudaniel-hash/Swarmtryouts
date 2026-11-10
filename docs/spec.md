# Crazyflie Swarm + OptiTrack + AI Deck: Mapping and Change Detection Spec

Status: draft from interview, 2026-11-04. Items marked **OPEN** need an answer before implementation.

## 1. Goal

Five Crazyflie 2.x drones, each with an AI deck, fly pre-planned trajectories inside an OptiTrack volume. Frames from each drone, combined with its mocap pose, are turned into a merged 3D point map in the OptiTrack frame. A second run over the same space is compared with the first, and changed objects are highlighted.

**Demo success criteria (live demo):**
1. 5 drones fly a deconflicted scripted scan and stream frames.
2. A primitive merged 3D map appears live in Foxglove/RViz2 from live inputs (a sparse, noisy map is acceptable).
3. After a second run, at least one object that was moved, added or removed is detected and highlighted.

Stretch: geometry diff plus labeled objects (detection-based semantics).

## 2. Decisions captured

| Area | Decision |
|---|---|
| Depth source | Existing monocular-depth Python script (MiDaS / Depth Anything class) running on the PC. Relative depth is acceptable; scale is recovered using mocap. Fallback: do detections instead of dense mapping. |
| Compute | Offboard on PC/GPU. GAP8 only streams (no onboard inference in v1). |
| Camera path | Stock AI deck WiFi streamer example (GAP8 + ESP32), grayscale JPEG, about 324x244. |
| Frame rate | 1-2 FPS per drone is acceptable. |
| Time sync | Hover at waypoint, capture while stationary. Match frame to latest mocap pose (latency mostly irrelevant). |
| Pose and control | ROS 2 + mocap4ros2 + crazyswarm2. Current setup is Python (cflib) driven; this is an upgrade. |
| Pi role | Crazyradio host + crazyswarm2 server. mocap4ros2 / NatNet bridge on the Pi is a **future direction** if latency proves acceptable; for the demo the bridge may run on the mocap PC or main PC. |
| Radios | 2-3 Crazyradios, drones split across them. |
| Volume | Large OptiTrack volume (8 m+). |
| Swarm tasking | Pre-planned scripted trajectories per drone, partitioned sweep areas, repeatable across runs. |
| Safety | Deconflicted trajectories + mocap-based min-separation monitor + auto-land on pose loss / low battery. |
| Battery | Short runs (about 3-4 min) with a landing reserve; swap batteries between runs. |
| Change detection | Combination: voxel/occupancy diff in mocap frame plus object detection diff. |
| Visualization | RViz2 / Foxglove via ROS 2 topics. |
| Hardware state | Swarm already flies in OptiTrack via Python; hardware mostly set up. |

## 3. Architecture

```
OptiTrack/Motive --NatNet--> mocap4ros2 --> /rigid_bodies, /poses
                                               |
                              Raspberry Pi: crazyswarm2 server + 2-3 Crazyradios
                                               |
                         extpos/extpose to cf1..cf5, waypoint commands
AI deck x5 --WiFi (ESP32)--> AP/router --> GPU PC
                                               |
              per-drone ingest node (JPEG -> image + stamp)
                                               |
         depth node (existing script wrapped) + pose-sync node
                                               |
                 /droneN/depth_points (PointCloud2, map frame)
                                               |
                 map_merge node -> /map/run_K (voxel grid)
                                               |
                 diff node (run K vs K-1) + detection diff
                                               |
                              Foxglove / RViz2 display
```

Notes:
- Keep the video network separate from the Crazyradio links (2.4 GHz contention). Choose radio channels and the AP WiFi channel so they do not overlap.
- Use one dedicated router/AP for the 5 AI decks. Expect 1-2 FPS per drone and verify with all 5 streaming at once.

## 4. Components

### 4.1 Flight and mocap
- Migrate from the Python cflib scripts to crazyswarm2 with mocap4ros2. Keep the current Python scripts working until parity is shown.
- Mocap rigid bodies named `cf1`..`cf5`; consistent marker patterns, all distinct.
- Pose rate to each drone: target at least 50-100 Hz via extpos/extpose; assign drones to radios to meet this.
- Trajectories: one per drone, generated offline, checked for pairwise clearance, with hover waypoints at capture points (dwell about 1-2 s for a sharp frame).

### 4.2 Capture and sync
- At each hover waypoint: capture a frame, tag it with the drone ID, run ID, waypoint ID, and the latest mocap pose (position and orientation).
- Reject frames taken while the drone is moving (speed threshold from mocap).
- Log everything to a rosbag per run for replay and diffing.

### 4.3 Camera calibration (not yet done; in scope)
- Per drone: intrinsics via checkerboard (Himax lens, grayscale, 324x244). Distortion is likely significant.
- Camera-to-body extrinsics via hand-eye using mocap (or a CAD value refined by a validation target).
- Store as one YAML per drone; version it with the run.

### 4.4 Depth to points
- Wrap the existing script as a ROS 2 node: input image + pose + intrinsics, output PointCloud2 in the map frame.
- Scale: fit a per-frame scale (and shift if needed) from mocap. Candidate anchors: known floor plane height from mocap, and known fixed objects. Document which anchor is used.
- **OPEN:** the script's actual inputs and outputs (to be provided by the user). Integration plan is wrapped ROS 2 node unless the format makes this costly.

### 4.5 Map merge and change detection
- Merge: fuse all drones' point clouds into a voxel grid in the mocap frame (voxel size chosen after measuring depth noise; start at 5-10 cm).
- Geometry diff: compare run K against run K-1 voxel-wise, with a persistence threshold so single noisy voxels are ignored; report added / removed voxel clusters.
- Detection diff: run an object detector on the PC for each frame, project detections into the map frame using depth + pose, compare object sets between runs by class and location.
- Output: changed clusters as a MarkerArray (colored added/removed) plus a labeled list.

### 4.6 Visualization
- Foxglove or RViz2: per-drone clouds, merged map, drone poses and planned paths, change markers, min-separation and battery status.

## 5. Safety requirements
- Separation monitor: warn below X m and trigger hold/land below Y m (values TBD; start conservative for drones this size).
- Auto-land on mocap pose loss longer than a set time, or battery below the reserve threshold.
- Geofence inside the capture volume with margin from the cage/volume edge.
- Operator kill switch (single command lands all).
- Flight-test progression: 1 drone, then 2, then 5; dry-run trajectories in simulation first.

## 6. Risks and mitigations
| Risk | Mitigation |
|---|---|
| Himax image is low-res grayscale; monocular depth will be noisy and scale-ambiguous | Per-frame scale from mocap; hover captures; keep map "primitive"; fallback to detections-only route |
| 5 AI decks on one WiFi AP | Dedicated AP, 1-2 FPS cap, lower JPEG quality; stagger capture if needed |
| 2.4 GHz interference between Crazyradio and WiFi | Channel planning; separate AP channel from radio channels |
| Payload and battery life with AI deck | Short runs, battery swaps, landing reserve; check thrust margin |
| Camera-to-mocap extrinsic error corrupts the map | Dedicated calibration procedure and a validation target with known mocap positions |
| NatNet bridge on Pi adds latency | Run bridge on the PC for the demo; evaluate on the Pi later |
| Frame/pose mismatch if drone is moving | Capture only when stationary |

## 7. Phases
1. **Port and parity:** crazyswarm2 + mocap4ros2 reproduce current flights with 1 drone, then 5. Radio assignment tuned.
2. **AI deck bring-up:** stock streamer working on all 5; measure FPS and loss with 5 concurrent.
3. **Calibration:** intrinsics and extrinsics for each drone; validation target.
4. **Depth node:** wrap script, add mocap scale fit, single-drone point cloud in Foxglove.
5. **Scripted swarm scan:** deconflicted trajectories, hover capture, rosbag logging.
6. **Merge and viz:** merged voxel map live.
7. **Change detection:** voxel diff, then detection diff; run 1 vs run 2 demo with one moved object.
8. **Hardening:** safety monitor, auto-land tests, demo rehearsal.

## 8. Open questions
- **OPEN:** Existing depth script inputs/outputs and runtime/FPS on the target GPU (user will provide).
- Which object detector for the detection route, and which object classes matter for the demo.
- Exact radio-to-drone assignment and measured pose rate per drone.
- Mocap volume dimensions and marker layout per drone given the AI deck.
- Voxel size and persistence thresholds (decide after measuring depth noise).
- Whether to move the NatNet/mocap4ros2 bridge to the Pi after the demo.

## Update 2026-11-09
- Python-first, no ROS/crazyswarm2: cflib Swarm-style manager on the Windows laptop (see docs/legacy-analysis.md, docs/tasks.md).
- Crazyradios on the laptop (2 radios, 3 + 2 drones). The Pi is only the WiFi access point for the AI decks, connected to the laptop by Ethernet.
- Volume is about 20 m2 x 3 m (not 8 m+): lane widths about 1 m, staggered altitudes, 0.5 m minimum separation.
