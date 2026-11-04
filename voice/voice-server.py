#!/usr/bin/env python3
"""Local speech recognition for the Model 3's voice commands (./tesla start --voice).

The firmware's QtCarSpeechRecognizer streams a voice command to Google's old full-duplex speech
API; the patch kit points those URLs here (speech-local-* in the Model 3 profile):

  POST /speech-api/full-duplex/v1/up?lang=en-US&client=..&pair=ID&key=..&interim
       chunked AMR-NB, 8 kHz ("audio/amr; rate=8000"), ends when the speech detector hears
       the end of the command (or the user lets go)
  POST /speech-api/full-duplex/v1/down?pair=ID&interim
       the answer, JSON lines as Google sent them:
       {"result":[]}
       {"result":[{"alternative":[{"transcript":"...","confidence":0.9}],"final":true}],"result_index":0}
  POST /speech-api/v2/recognize?...  (the non-streaming engine: the whole file at once)

The two requests of one command share the pair ID and may arrive in either order. QtCar turns
the transcript into the command itself ("navigate to ...", "call ...", "play ..."); no answer
(empty result) is its "try again". The recognizer is whisper.cpp (voice/build-whisper.sh): its
whisper-server, started here on PORT+1 and kept running (the model stays loaded, on the GPU
when built with CUDA), gets the decoded audio (ffmpeg); without it whisper-cli per command. The key in the URL is
the firmware's; it is neither used nor logged here.
"""

import argparse
import array
import collections
import http.server
import json
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import wave

HERE = os.path.dirname(os.path.realpath(__file__))
# whisper's usual inventions for noise and silence (YouTube subtitles)
HALLUCINATIONS = {"thank you", "thanks for watching", "thank you for watching", "you", "bye", "okay"}
NO_SPEECH = re.compile(r"^\W*$|^\s*[\[(].*[\])]\s*$")  # "", "[BLANK_AUDIO]", "(wind blowing)"


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


class Session:
    def __init__(self):
        self.cond = threading.Condition()
        self.done = False
        self.text = None
        self.created = time.monotonic()

    def finish(self, text):
        with self.cond:
            self.text, self.done = text, True
            self.cond.notify_all()

    def wait(self, timeout):
        with self.cond:
            self.cond.wait_for(lambda: self.done, timeout)
            return self.done, self.text


SESSIONS = {}
LOCK = threading.Lock()


def session(pair):
    with LOCK:
        now = time.monotonic()
        for k in [k for k, s in SESSIONS.items() if now - s.created > 120]:
            del SESSIONS[k]
        return SESSIONS.setdefault(pair, Session())


def result_lines(text):
    lines = [{"result": []}]
    if text:
        lines.append({"result": [{"alternative": [{"transcript": text, "confidence": 0.9}], "final": True}],
                      "result_index": 0})
    return "".join(json.dumps(x) + "\n" for x in lines).encode()


def multipart(fields, boundary="voiceserverboundary"):
    """{name: str | (filename, bytes)} -> (body, content type)"""
    out = bytearray()
    for k, v in fields.items():
        out += f"--{boundary}\r\n".encode()
        if isinstance(v, tuple):
            out += f'Content-Disposition: form-data; name="{k}"; filename="{v[0]}"\r\n'.encode()
            out += b"Content-Type: application/octet-stream\r\n\r\n" + v[1] + b"\r\n"
        else:
            out += f'Content-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
    out += f"--{boundary}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={boundary}"


class Whisper:
    """whisper-server, kept running with the model loaded (on the GPU if whisper.cpp was built
    with CUDA/Vulkan), or else whisper-cli once per command."""

    def __init__(self, a):
        self.a, self.proc, self.lock = a, None, threading.Lock()
        self.url = f"http://127.0.0.1:{a.port + 1}"

    def server(self):
        if self.proc and self.proc.poll() is None:
            return True
        if not self.a.whisper_server:
            return False
        log(f"starting {self.a.whisper_server} on {self.url}")
        self.proc = subprocess.Popen([self.a.whisper_server, "-m", self.a.model, "-t", str(self.a.threads),
                                      "--host", "127.0.0.1", "--port", str(self.a.port + 1)],
                                     stdout=sys.stdout, stderr=subprocess.STDOUT)
        for _ in range(600):            # loading a large model onto the GPU takes a while
            if self.proc.poll() is not None:
                log("whisper-server exited:", self.proc.returncode)
                return False
            try:
                urllib.request.urlopen(self.url + "/", timeout=1).close()
                return True
            except OSError:
                time.sleep(0.1)
        return False

    def __call__(self, wav, lang):
        prompt = self.a.prompt or ""
        with self.lock:
            if self.server():
                body, ctype = multipart({"file": ("in.wav", open(wav, "rb").read()), "language": lang,
                                         "prompt": prompt, "response_format": "json", "temperature": "0.0"})
                req = urllib.request.Request(self.url + "/inference", body, {"Content-Type": ctype})
                with urllib.request.urlopen(req, timeout=60) as r:
                    return json.load(r).get("text", "")
            cmd = [self.a.whisper, "-m", self.a.model, "-f", wav, "-l", lang, "-t", str(self.a.threads), "-nt", "-np"]
            if prompt:
                cmd += ["--prompt", prompt]
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode:
                raise RuntimeError("whisper-cli: " + r.stderr.strip()[-300:])
            return r.stdout

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()


class Endpointer:
    """End-of-speech detection on 20 ms frames of 16 kHz audio, for the car's speech detector,
    which took a PC's background noise for speech and recorded until its limit (18 s). The level
    (100 ms average) against the noise floor: the lowest 20th percentile of 1.5 s windows so far
    (rising 2 dB/s, noise that gets louder; not from the first 200 ms, the decoder's warm-up). Looked at again each frame, so speech that started
    right away (before there was a floor) is found once the quiet after it comes: speech = vad_db
    above the floor for 100 ms, the end = vad_end_ms below vad_db - 6 after it."""
    FRAME = 640                 # bytes: 20 ms of 16 kHz S16

    def __init__(self, a):
        self.a = a
        self.recent = collections.deque(maxlen=5)     # frame energies, for the 100 ms average
        self.levels = []        # the averages (dB), one per frame
        self.floor = None
        self.start = None       # first frame of the speech
        self.end = None         # its last loud frame
        self.speech = 0         # loud frames

    def feed(self, frame):
        """one frame; True once the end of speech is found"""
        x = array.array("h", frame)
        self.recent.append(sum(v * v for v in x) / max(len(x), 1))
        lv = self.levels
        lv.append(10 * math.log10(sum(self.recent) / len(self.recent) / 32768 ** 2 + 1e-12))
        n = len(lv)
        if n <= 10:             # the decoder's first frames are near silence: no floor from them
            return False
        w = sorted(lv[max(10, n - 75):])
        p20 = w[len(w) // 5]
        self.floor = p20 if self.floor is None else min(self.floor + 0.04, p20)
        if n < 25:
            return False
        loud = self.floor + self.a.vad_db
        quiet = loud - 6
        tail = self.a.vad_end_ms // 20
        if any(v > quiet for v in lv[-tail:]):
            return n * 20 >= 15000 and self.found(loud, n)     # still talking (or noise)
        return self.found(loud, n - tail)

    def found(self, loud, upto):
        """speech (5 loud frames in a row) before frame upto? sets start, end, speech"""
        run, start, end, count = 0, None, None, 0
        for i, v in enumerate(self.levels[:upto]):
            if i < 10:
                continue
            if v > loud:
                count += 1
                run += 1
                if run >= 5:
                    start = i - 4 if start is None else start
                    end = i
            else:
                run = 0
        if start is None:
            return False
        self.start, self.end, self.speech = start, end, count
        return True

    def secs(self, frames):
        return frames * 0.02


class Recognizer:
    def __init__(self, a):
        self.a = a
        self.whisper = None if a.fake is not None else Whisper(a)

    def decoder(self, ctype):
        """ffmpeg: the car's audio on stdin -> 16 kHz S16 mono on stdout, as it arrives"""
        fmt = ["-f", "amr"] if "amr" in ctype else []
        return subprocess.Popen(["ffmpeg", "-hide_banner", "-loglevel", "error", "-probesize", "32",
                                 "-analyzeduration", "0", "-fflags", "nobuffer", *fmt, "-i", "pipe:0",
                                 "-f", "s16le", "-ac", "1", "-ar", "16000", "-flush_packets", "1", "pipe:1"],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    def pcm(self, pcm, lang, ep=None, why=""):
        """16 kHz S16 audio -> transcript or None"""
        lang = (lang or "en").split("-")[0].lower()
        if ep is not None:
            if ep.start is None or ep.speech < 12:          # under a quarter second: no command
                log(f"{len(pcm) / 32000:.1f} s of audio, no speech heard{why}")
                return None
            a = max(0, ep.start - 15) * Endpointer.FRAME     # from 0.3 s before the speech
            b = (ep.end + 15) * Endpointer.FRAME if ep.end is not None else len(pcm)
            pcm = pcm[a:b]
        with tempfile.TemporaryDirectory(prefix="voice-") as d:
            wav = os.path.join(d, "in.wav")
            with wave.open(wav, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(16000)
                w.writeframes(pcm)
            if self.a.save:
                os.makedirs(self.a.save, exist_ok=True)
                shutil.copy(wav, os.path.join(self.a.save, time.strftime("voice-%Y%m%d-%H%M%S.wav")))
            t0 = time.monotonic()
            text = self.a.fake if self.whisper is None else self.whisper(wav, lang)
        text = " ".join(text.split()).strip().rstrip(".!?。").strip()
        if NO_SPEECH.match(text) or (text.lower() in HALLUCINATIONS and (ep is None or ep.speech < 50)):
            text = None
        log(f"{len(pcm) / 32000:.1f} s of speech ({lang}){why} -> {text!r} in {time.monotonic() - t0:.1f} s")
        return text

    def __call__(self, audio, ctype, lang):
        """a whole recording (v2, tests) -> transcript or None"""
        if "amr" in ctype and not audio.startswith(b"#!AMR\n"):
            audio = b"#!AMR\n" + audio
        dec = self.decoder(ctype)
        out, _ = dec.communicate(audio)
        return self.pcm(out, lang)


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    recognize = None

    def body(self):
        if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
            out = bytearray()
            while True:
                line = self.rfile.readline()
                if not line:
                    break
                n = int(line.split(b";")[0].strip() or b"0", 16)
                if n == 0:
                    while self.rfile.readline() not in (b"\r\n", b"\n", b""):
                        pass                    # trailers
                    break
                out += self.rfile.read(n)
                self.rfile.readline()
            return bytes(out)
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def chunks(self):
        """the request body as it arrives"""
        if self.headers.get("Transfer-Encoding", "").lower() != "chunked":
            yield self.body()
            return
        while True:
            line = self.rfile.readline()
            if not line:
                return
            n = int(line.split(b";")[0].strip() or b"0", 16)
            if n == 0:
                while self.rfile.readline() not in (b"\r\n", b"\n", b""):
                    pass
                return
            yield self.rfile.read(n)
            self.rfile.readline()

    def stream_up(self, s, ctype, lang):
        """Decode while the car uploads; answer as soon as the speech has ended (the answer on
        the down request ends the car's recording), else when the upload ends."""
        rec = self.recognize
        dec = rec.decoder(ctype)
        ep = Endpointer(self.server.args)
        pcm = bytearray()
        t0 = time.monotonic()

        def finish(why):
            try:
                s.finish(rec.pcm(bytes(pcm), lang, ep, why))
            except Exception as e:      # never leave the down request hanging
                log("recognition failed:", e)
                s.finish(None)

        def read():
            ended = False
            while True:
                frame = dec.stdout.read(Endpointer.FRAME)
                if not frame:
                    return
                if ended:
                    continue            # keep ffmpeg's pipe drained until the upload stops
                pcm.extend(frame)
                if ep.feed(frame):
                    ended = True
                    threading.Thread(target=finish, args=(f", end after {ep.secs(ep.end - ep.start):.1f} s",),
                                     daemon=True).start()
                elif (len(pcm) >= self.server.args.vad_wait * 32000 and ep.start is None and
                      not ep.found(ep.floor + self.server.args.vad_db, len(ep.levels))):
                    ended = True        # nobody spoke: "try again" now, not after the car's limit
                    threading.Thread(target=finish, args=(", nothing in the first seconds",), daemon=True).start()

        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        first = True
        try:
            for c in self.chunks():
                if first and "amr" in ctype and not c.startswith(b"#!AMR\n"):
                    c = b"#!AMR\n" + c         # amrnbenc's frames, without the file header
                first = False
                try:
                    dec.stdin.write(c)
                    dec.stdin.flush()
                except BrokenPipeError:
                    break
        finally:
            try:
                dec.stdin.close()
            except BrokenPipeError:
                pass
            reader.join(5)
            dec.kill()
        if not s.done:                  # the upload ended first (the button let go, the car's limit)
            if ep.start is None and ep.levels:
                ep.found(ep.floor + self.server.args.vad_db, len(ep.levels))
            finish(f", upload ended after {time.monotonic() - t0:.1f} s")

    def reply(self, data, ctype="application/json; charset=utf-8", code=200):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        u = urllib.parse.urlsplit(self.path)
        q = dict(urllib.parse.parse_qsl(u.query, keep_blank_values=True))
        ctype = self.headers.get("Content-Type", "")
        if u.path.endswith("/full-duplex/v1/up"):
            self.stream_up(session(q.get("pair", "")), ctype, q.get("lang"))
            self.reply(b'{"result":[]}\n')
        elif u.path.endswith("/full-duplex/v1/down"):
            self.body()
            done, text = session(q.get("pair", "")).wait(self.server.timeout_s)
            if not done:
                log("no audio arrived for this command")
            self.reply(result_lines(text))
        elif u.path.endswith("/v2/recognize"):
            text = self.recognize(self.body(), ctype, q.get("lang"))
            self.reply(result_lines(text))
        else:
            self.body()
            self.reply(b"not found\n", "text/plain", 404)

    def do_GET(self):
        if self.path in ("/", "/status"):
            self.reply(json.dumps({"ok": True, "engine": "fake" if self.server.args.fake is not None
                                   else os.path.basename(self.server.args.model)}).encode() + b"\n")
        else:
            self.do_POST()

    def log_message(self, *a):
        pass


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--port", type=int, default=int(os.environ.get("VOICE_PORT", 8097)))
    ap.add_argument("--whisper", help="whisper-cli (default: voice/work/whisper.cpp/build/bin/whisper-cli, "
                                      "else whisper-cli on PATH)")
    ap.add_argument("--whisper-server", help="whisper-server, kept running on PORT+1 (default: the one next to "
                                             "whisper-cli); --no-server: whisper-cli once per command")
    ap.add_argument("--no-server", action="store_true")
    ap.add_argument("--model", default=os.environ.get("VOICE_MODEL", "base"),
                    help="ggml model file, or a name in voice/work/models (base, small, base.en, ...)")
    ap.add_argument("--prompt", default=os.environ.get("VOICE_PROMPT"),
                    help="text that primes whisper (names, words); none by default: example commands made it "
                         "put their words into what it heard")
    ap.add_argument("--threads", type=int, default=max(1, min(4, (os.cpu_count() or 2) // 2)),
                    help="CPU threads (default: the physical cores, at most 4; more made it slower)")
    ap.add_argument("--save", metavar="DIR", help="keep every command's audio as a WAV file here")
    ap.add_argument("--vad-db", type=float, default=10, help="speech: this many dB above the noise floor")
    ap.add_argument("--vad-end-ms", type=int, default=800, help="this long back near the floor ends a command")
    ap.add_argument("--vad-wait", type=float, default=6, help="no speech in this many seconds: \"try again\"")
    ap.add_argument("--timeout", type=float, default=40, help="how long an answer waits for its audio (s)")
    ap.add_argument("--fake", metavar="TEXT", help="answer every command with TEXT (testing without whisper)")
    a = ap.parse_args()
    if not shutil.which("ffmpeg"):
        sys.exit("voice-server: ffmpeg is needed (it decodes the car's AMR audio)")
    if a.fake is None:
        built = os.path.join(HERE, "work/whisper.cpp/build/bin/whisper-cli")
        a.whisper = a.whisper or (built if os.access(built, os.X_OK) else shutil.which("whisper-cli"))
        if not a.whisper:
            sys.exit("voice-server: no whisper-cli: run voice/build-whisper.sh (or install whisper.cpp)")
        ws = a.whisper_server or os.path.join(os.path.dirname(a.whisper), "whisper-server")
        a.whisper_server = None if a.no_server or not os.access(ws, os.X_OK) else ws
        if not os.path.exists(a.model):
            a.model = os.path.join(HERE, "work/models", f"ggml-{a.model}.bin")
        if not os.path.exists(a.model):
            sys.exit(f"voice-server: no model {a.model}: run voice/build-whisper.sh")
    Handler.recognize = Recognizer(a)
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    srv.daemon_threads = True
    srv.timeout_s, srv.args = a.timeout, a
    log(f"voice server on 127.0.0.1:{a.port}, " +
        ("fake answers" if a.fake is not None else f"{a.whisper_server or a.whisper} with {a.model}"))
    rec = Handler.recognize
    if rec.whisper:
        threading.Thread(target=rec.whisper.server, daemon=True).start()   # load the model now
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        srv.serve_forever()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        if rec.whisper:
            rec.whisper.stop()


if __name__ == "__main__":
    main()
