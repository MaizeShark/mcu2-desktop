# voice

Voice commands for the Model 3 firmware (2019.20.4.2), recognized on this PC:
`./tesla start --voice --audio`, then `./tesla voice` (docs/features.md, "Voice commands").

```
mic -> pw-record -> loopback subdevice 2 (A2B mics 1-3) -> audiod -> AudioWeaver -> tplug "voice"
    -> QtCarSpeechRecognizer --AMR 8 kHz, HTTP--> voice-server.py :8097 -> ffmpeg -> whisper-server :8098
    <- {"result":[{"alternative":[{"transcript":...}]...}]} <- QtCar: NLPManager runs the command
```

- `build-whisper.sh [MODEL]`: whisper.cpp v1.9.5 (static; CUDA when `nvcc` is found, `GPU=cpu`
  or `GPU=vulkan` to choose) into `work/whisper.cpp`, the model into `work/models/ggml-MODEL.bin`
  (base by default; on a GPU large-v3-turbo). Untracked, like navigation/work.
- `voice-server.py`: the stand-in for Google's full-duplex speech API that the firmware's
  recognizer streams to (the kit's `speech-local-*` patches). `--fake TEXT` answers every
  command with TEXT (tests without whisper), `--save DIR` keeps each command's audio,
  `--prompt` primes whisper (off by default: example commands leaked into what it heard).

The protocol (seen 2026-11-04): `POST /speech-api/full-duplex/v1/up?lang=en-US&client=..&pair=ID&key=..&interim`
with `Transfer-Encoding: chunked`, `Content-Type: audio/amr; rate=8000` (amrnbenc frames, no
`#!AMR` header), ending when the speech detector hears the end; and `POST .../down?pair=ID&interim`
(empty body), answered with JSON lines. The two share the pair ID and can come in either order.
An answer without a transcript is the recognizer's "try again". The firmware's `speech_rec/*`
settings (settings.conf): `simulated_input_filename` (a WAV instead of the mic, for tests),
`save_recordings`, `debug_level`.

Timing on an i7-6700K (CPU, base model, a 2 s command): 1.5 s with 4 threads; 8 threads (the
hyperthreads) took 7 s. Accuracy with base on espeak's voice through AMR is mediocre (names,
"Bohemian Rhapsody"); a larger model on the GPU is the fix.
