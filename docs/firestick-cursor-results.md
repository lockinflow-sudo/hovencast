# Fire Stick cursor calibration — October 11, 2026

Installed and physically tested on Sean’s 2nd Fire TV.

| Test | Median cursor-triggered display delay |
|---|---:|
| Original Fire Stick path | 1.090 seconds |
| Direct transport, first pass | 0.616 seconds |
| Direct transport, repeat | 0.614 seconds |
| Continuous-motion trajectory fit | 0.613 seconds |
| Apple TV, previous session | approximately 0.48 seconds |

Fire Stick delay decreased about 44%. The user confirmed clear, continuous
TV audio and a smooth, noticeably better cursor. The faster cast was left
running; test windows were closed and the camera stopped.

The change feeds existing H.264/AAC MPEG-TS directly into the native P2P
sender, eliminating the second FIFO and FFmpeg remux. The encoder, frame
rate, audio configuration and native pacing are retained. MICE retains its
original path; Apple TV and Chromecast code are unaffected.

Two independent synthetic direct-stream trials decoded cleanly. Five native
WFD lifecycle tests, 23 recorder tests, 18 protocol tests and session tests
passed. The 40-second motion sample tracked all 1,183 camera frames and
showed consistent motion.

Measurements include camera capture/exposure and display scanout uncertainty.
Other receiver models and platforms still require their own live calibration;
this result does not establish Apple TV latency parity. Initial Wi-Fi Direct
reconnect failures occurred before video started and remain a separate issue.

Source saved in the Fire TV feature branch and unified Apple TV development
tree. Installed as a local beta.8 tuning change; nothing was published.
