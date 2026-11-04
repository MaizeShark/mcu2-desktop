# Voice commands and hands-free calls: survey (2026-11-04)

Static analysis of both firmwares, nothing run yet. Status today: `qtcar-speechrecognizer` and
`qtcar-nuanceserver` start (`./tesla start --services`), but no microphone reaches them, and
`start.sh` holds `SPEECH_voiceRecGUIState` at Inactive so that volume changes work. Calls:
pairing, HFP connect, phonebook and call lists work; call audio was never tried.

## The audio paths (both firmwares, same layout)

```
mics --A2B--> audiod (auc:2 capture) --> AudioWeaver --> tplug "voice" --> app_in_voice --> speech recognizer
                                              |   ^
                                    bthfp out |   | bthfp in        (AEC, noise suppression, beamforming:
                                              v   |                  AWE's telephoneProcess)
                          auc:3 (I2S SSP2) <--> BCM4349's PCM pins <--SCO over the air--> phone
```

- AWE merges its inputs into one 22-channel wire (`AWE_CmdLine.ini` order): A2B 0-7, media
  8-9, a2dp 10-11, tuner 12-13, chimes 14-19, **bthfp 20**, ecallrx 21. `MicRouter` and
  `HFPMicRouter` take channels **0, 1, 2** (`model3.aws`): the three mics are A2B capture
  channels 0-2 (S32, 48 kHz, 8 channels). `HFPRXRouter` takes 20 (the phone's voice).
- Model 3 `asound.conf`: `awe_in_a2b = auc:2,8`, `awe_in/out_bthfp = auc:3,1` (48 kHz S32 mono
  in AWE; the SSP2 blobs `ssp2[cp]-hfp-{8,16}kHz.blob` resample in hardware),
  `app_in_voice = app_tplug:voice,1`. Model S: `awe_in_mic = auc:2,2`, bthfp also `auc:3,1`,
  `pcm.speechrec = app_in_voice`, plus a USB mic path (`usb-mic-control`, `alsaloop-usb-mic`,
  `/dev/snd/TeslaMic` → `GUI_usbMicrophoneEnabled`).
- The kit's `auc` override sends `auc:N` to snd-aloop card `model3` device 0 subdevice N.
  `start.sh` loads `pcm_substreams=4,8`, so subdevices 2 and 3 already exist; nothing plays into
  or records from them yet. `audiod-no-a2b` keeps the A2B PCM pumping (only the I2C stack is
  skipped), so audiod should already capture (silence) from subdevice 2.
- `audiod-hfp-config` (run by audiod on a call): `escalator-ctl set-nhlt-ssp RATE` and
  `amixer sset 'HFP Rate'`. Both fail on a PC (no such control on snd-aloop); what audiod does
  then is unknown. Probably a kit stub that exits 0 is needed.

**Mic in (both features):** `pw-record` (PC mic, mono) → upmix into channels 0-2 of an 8-channel
S32 48 kHz stream → `aplay -D hw:CARD=model3,DEV=1,SUBDEV=2`. Same pattern as the existing
output path (arecord on DEV=1 SUBDEV=0 → pw-play), reversed. Must use audiod's 192-frame period
(snd-aloop rule, see start.sh's hrclock comment).

## Hands-free calls: SCO audio

On the car, SCO goes over the BCM4349's PCM/I2S pins, never over HCI. BSA is built that way:
`bsa_sco_set_route() : HCI routing not supported`; it configures the chip with Broadcom VSCs
(`Write SCO PCM`, `Write I2SPCM INTF`, `Enable WBS`), which the bridge answers locally. A PC's
USB adapter (Intel AX210, 8087:0032) sends SCO over HCI instead, as type-3 packets on the
user channel. So `hci-bridge.py` would have to carry the audio itself:

1. Watch the HCI events for `Synchronous Connection Complete` (0x2C): handle, air mode, link
   type. Read `Write Voice Setting` (0x0C26) / `Enhanced Setup Synchronous Connection` to
   know the codec.
2. Switch the USB isoc altsetting: the kernel doesn't track connections on a user channel.
   Linux 6.15+ has the **HCI Drv** packets (H4 type 0xF1) for this; this PC's 7.2 kernel has
   `btusb_hci_drv_switch_altsetting` (checked in `/proc/kallsyms`). The opcode values are in
   `include/net/bluetooth/hci_drv.h` / btusb.c (look up, don't guess). Debian 13's 6.12 (the stick)
   probably lacks it: calls would need a newer kernel there.
3. SCO from the controller (CVSD: 8 kHz S16 linear after the controller's transcoding) → resample
   to 48 kHz S32 → `hw:model3,1,3` playback (audiod reads it as `awe_in_bthfp`). The other
   direction: capture `hw:model3,1,3` → 8 kHz S16 → SCO packets at the controller's SCO MTU
   (`Read Buffer Size`), paced by incoming packets. Don't forward SCO data to BSA.
4. Wideband (mSBC, 16 kHz): the Broadcom chip encodes on chip after `Enable WBS`; Intel won't.
   Either keep the call narrowband (CVSD; stop BSA from offering codec negotiation, or answer
   AT+BAC without mSBC) or do mSBC in the bridge (libsbc via ctypes, transparent air mode,
   H2 sync header). Start with CVSD.
5. The 2019 stack gets "Unknown HCI Command" for vendor commands. Whether SCO setup survives a
   failed `Write SCO PCM` is unknown: maybe it needs the S profile's `--emulate-vendor-success` for
   those opcodes (a per-opcode allowlist in the bridge).

Test: `bsa_server -all=5 -b /tmp/bsa.snoop` (bluetooth/README.md) + `btmon` is unavailable while
the user channel is held, so the bridge's `-v` log is the HCI trace.

## Voice commands: Model 3 2019.20.4.2

`QtCarSpeechRecognizer` has three engines: `GoogleVoiceRecognizer` (Google's old
full-duplex speech API: `.../speech-api/full-duplex/v1/up?lang=..&client=..&pair=..&key=..&interim`
and `.../down?pair=..`), `BaiduVoiceRecognizer` (China) and `DialogflowRecognizer` (through
`/usr/bin/tdialogflow` to Tesla's media server, needs car credentials). Capture: `alsasrc
device=pcm.app_in_voice ! audioconvert ! audioresample ! 16 kHz mono ! speechdetector !
opusenc ! oggmux ! teslahttpclientsink url=...`. The command parsing is local: QtCar matches
the transcript (`assets/voice_command_list_v2.json`, contact finders against the phone's
phonebook), so any recognizer that returns text gives working "navigate to", "call", ...
Triggers: the right steering wheel button (`voiceRecLongPressTimeout`; CAN `VCLEFT_swcRightPressed`,
on the panel already) and the keyboard's mic (`keyboardVoiceRecStarted`: dictation).
Without a mic: `speech_rec/simulated_input_filename` (settings.conf) feeds a file.

The firmware carries a Google API key for this. **Don't use it**, and don't put it in the repo.
Google shut that API down long ago anyway. Plan: a local stand-in server that speaks the
full-duplex protocol (POST up stream of Ogg/Opus chunks, GET down stream of JSON lines
`{"result":[{"alternative":[{"transcript":..,"confidence":..}],"final":true}],"result_index":0}`)
and runs a local recognizer (whisper.cpp or Vosk) on the host or in podman. The up/down URLs are
fixed strings with `https://www.google.com`; a kit string patch to `http://127.0.0.1:PORT`
(shorter, NUL-padded) avoids TLS and leaves www.google.com alone for the browser. Check first
whether `teslahttpclientsink` accepts plain http and how `speech_rec/google_url` (v2) and
`default_voice_recognizer` pick the engine.

`NuanceServer` is TTS (links `libve`, Vocalizer Expressive): the spoken answers/navigation voice.

## Voice commands: Model S/X 2026.8.3

Only cloud: gRPC `/tesla.media.api.v1.services.media.Media/Speech` to Tesla's server
(`speech_rec/grpc_server`, `speech_rec/use_ssl`); the server returns structured actions
(`PhoneCallStartAction`, `NavigationStopAction`, `UIAdjustVolumeAction`, ...), no local NLU.
Gated by `/var/etc/features/speechrec` (the run script exits without it). The proto
descriptors are embedded in the binary (`tesla-media-api/v1/rpcs/speech/speech.proto` and the
action protos), so a local stand-in server is possible: extract the FileDescriptorProtos,
generate stubs, recognize locally, map text → actions. Larger job than the Model 3's: the NLU
is ours to write. `Media/Auth` may also have to be answered.

## Order

1. Mic into AWE (both): pw-record → subdevice 2; check `input-meter ?` on audiod and the
   `voice` tplug. Small, and both features need it.
2. Model 3 voice: stand-in full-duplex server + whisper/Vosk, URL patch; test with
   `simulated_input_filename` in the sandbox first, then the mic.
3. Calls: bridge SCO (CVSD) + HCI Drv altsetting, `audiod-hfp-config` stub; test on the T480 /
   PC with the Pixel 7a. Needs `--bluetooth --audio`, real hardware, the user's phone.
4. Model S voice (gRPC stand-in), if wanted.

## Model 3 voice: done 2026-11-04 (sandbox)

- `qtcar-speechrecognizer` picks the `google` engine and streams (`googleStreamAudio: true`):
  `filesrc/alsasrc ! ... 8 kHz ! speechdetector ! amrnbenc ! teslahttpclientsink url=.../up?...`
  plus a `POST .../down?pair=` for the results. The up/down URLs are fixed strings and ignore
  `speech_rec/google_url` (that's only the non-streaming v2 one).
- **Incident:** the first sandbox test (simulated input = an espeak WAV, "navigate to the Golden
  Gate Bridge") went to the real `https://www.google.com/speech-api/full-duplex/` with the
  firmware's key, and Google answered with the transcript: the endpoint is still alive. Only that
  synthetic clip was sent. Since then the kit's `speech-local-up/-down/-v2` patches point all three
  URLs at 127.0.0.1:8097 (`voice/voice-server.py`); nothing goes to Google, `--voice` or not.
- `VRIdle::startRecognition` refuses without `GUI_canUseInternet` ("VRNotConnected because cannot
  use internet"); the Model 3 run sets no CONN_/LINK_ values without --wifi/--cellular.
  `speech-offline` (0x334186 jne -> jmp) starts anyway.
- Trigger: the recognizer's service call `curl --http1.0 http://127.0.0.1:4280/startCapturing`
  (`stopCapturing`, `cancelCapturing`) works; QtCar's NLPManager processed the transcript
  ("processing command"), the map showed the search result (SFO) with Navigate/Call.
  `./tesla voice` does this.
- The steering wheel: QtCar's voice button is `STW_rightTop` (`ClusterUI::steeringWheelControlsChanged`
  -> `VoiceControlHandler::processButtonAction`, not while `STW_rightBottom`/`STW_leftTop` are pressed).
  qtcar-vehicle sets it from `VCLEFT_switchStatus` byte 1 bits 0-1 == 2 = `VCLEFT_swcRightTiltLeft=ON`
  (LeftSwitchStatusMessage::processMessage). Through the panel (sim on :1235 behind tesla-can.py,
  as ./tesla start does) STW_rightTop does go true/false in QtCar, but a gdb breakpoint on
  ClusterUI::steeringWheelControlsChanged (QtCar's own copy, 0x2ed7d0 in the executable) never
  fired: the slot isn't connected in the sandbox. Open: where createViews connects it (maybe only
  with the cluster service, "could not resolve host ic for service ClusterDisplay"). The left
  card's microphone icon (480,860) didn't start anything either (tap may not have registered).
- Setting a vehicle-owned value (STW_*) on QtCar's :4220 only pushes it to VehicleService
  ("Pushing data value"); it changes nothing locally.
- whisper.cpp v1.9.5 on the i7-6700K: `-t 8` is 5x slower than `-t 4` (hyperthreads); the server
  defaults to the physical cores, at most 4. A prompt with example commands made it hear "Call Mom
  Smith"; off by default. The PC has an RTX 4060 + CUDA 13.4: build-whisper.sh builds with CUDA.
- Not tested: the microphone pipeline (needs snd-aloop, i.e. a real `./tesla start --voice --audio`).
- Also seen: QtCar in the sandbox exited once on an X `BadWindow` error (X_ChangeWindowAttributes)
  and was restarted; unrelated to voice.
- First real run (logs/20261104-212355, RTX 4060, large-v3-turbo CUDA): whisper 0.1-0.4 s, but
  every command recorded ~18 s: the firmware's speechdetector reported speech every 0.4 s
  (`ClusterNLPSpeaking TVOICE: speechDetected: 1..22`, the PC's background noise) and never
  ended it. Short recordings came back as "Thank you" (whisper on silence). The mic pipeline
  underran (aplay buffer 64 ms; now 200 ms). The listening UI (ClusterNLPSM) appeared.
  Fix: voice-server.py decodes while the upload streams and ends the command itself
  (Endpointer: 100 ms level vs. the 20th-percentile noise floor, retrospective so immediate
  speech counts; the decoder's first 200 ms are near silence and left out). Answering the down
  request ends the car's recording ("VRCapturing -> VRProcessingRecognizerResponse"). Streaming
  tests: answer ~0.9 s after the speech; noise only -> "try again" after 6 s.
