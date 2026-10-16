# Google Cast tuning — October 15, 2026

CarbonCast now shares one source clock between audio and video, reads live input off the network feedback thread, and skips decoded frames more than 100 ms late before encoding. This prevents blocking feedback on HTTP reads and sending expired startup history to the receiver.

The installed settings are 720p at 30 fps, VP8/Opus, a 400 ms receiver playback target, a 300 ms acknowledgment allowance, and a 6 Mbps maximum native media bitrate. The source H.264 stream remains local. Fire Stick and Apple TV transports were not changed during this repair.

## Camera result

Cursor-triggered TV color transitions measured **562.3 ms median**, **539.1–588.4 ms**, across seven samples with the laptop camera at 30 fps. The original Google Cast baseline measured about 996 ms. The timing measurement used the diagnostic 1.2 Mbps bitrate. Camera sampling and target polling limit precision. This is close to the tuned Fire Stick result (~610 ms), but above the Apple TV benchmark (~470–490 ms).

The user confirmed “Motion smooth and tone clear” with normal video visible and a quiet tone mixed into its audio. The test window was then closed. Camera exposure controls were restored and camera capture stopped.

## Rejected trials

The earlier 300 ms target with a 100 ms acknowledgment allowance measured ~472 ms cursor response, but the user subsequently reported glitchy audio and slow video. It was rejected. Color transitions and an isolated tone were insufficient playback verification.

Asynchronous reads and a larger acknowledgment allowance alone did not resolve the failure. Skipping expired startup frames produced the decisive improvement. The individual contribution of the lower bitrate was not isolated after that change. The user confirmed improved motion and clear audio after a normal reconnect, but noted softer video at 1.2 Mbps. At 3 Mbps the user still found the picture soft, although audio and motion remained smooth. A 1080p trial changed the desktop sizing and later stalled capture, so it was rejected. The final trial restores the preferred 720p layout and raises the native bitrate cap to 6 Mbps; the user confirmed the picture and playback are on point, cursor movement is smooth, and audio is great.

## Verification

The native helper compiled successfully. Thirteen Google Cast Python tests and 23 recorder lifecycle tests passed, covering source timestamp parsing, receiver identity, transport behavior and recorder cleanup. Stop terminated the live helper cleanly, and a normal session launched using the installed binary and defaults. The user confirmed much better motion and on-point audio after the normal reconnect.

The native patch is the full source patch against Chromium Open Screen revision `8978b74badf042795c07fcf99d0a53dccbd3dcf6`, including pre-existing host build adaptations. The Python patch is relative to the worker before this tuning session. Original installed files remain backed up in the task work directory. The native binary depends on this Arch host’s FFmpeg/libvpx/libopus ABIs.

## Final state

The user confirmed the installed 720p / 6 Mbps stream has the desired picture quality, smooth playback and cursor motion, and clear audio. The working session is left running. The 400 ms playback target and 300 ms acknowledgment allowance persist across normal reconnects; automatic Google Cast quality retains the preferred 720p desktop size. The final 6 Mbps setting was not separately camera-timed; the 562.3 ms timing measurement used the same timing logic at 1.2 Mbps.
