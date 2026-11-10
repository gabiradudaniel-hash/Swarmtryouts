# Bring-up checklist
1. Collect hardware models (AI deck version, Crazyflie model, Motive version, router, Pi).
2. Port existing flight scripts: 1 drone, then 2, then 5 (compare with legacy_scripts/).
3. WiFi stream test: 1 deck, then 2, then 5. Log FPS, latency, drops.
4. Camera calibration (intrinsics + camera-to-body), one YAML per drone.
5. Depth node on one drone's frames, point cloud in Foxglove.
6. Simulation (Gazebo): scripted trajectories and separation checks.
7. Scripted 5-drone scan with hover captures, rosbag per run.
8. Merge + change detection, run 1 vs run 2.
9. Safety tests: pose loss, low battery, kill switch.
