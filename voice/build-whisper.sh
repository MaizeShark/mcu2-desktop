#!/bin/bash
# Build whisper.cpp (local speech recognition for voice commands) and download a model.
#
#   voice/build-whisper.sh [MODEL]      MODEL: base (default), small, large-v3-turbo, base.en, ...
#
#   GPU=cuda|vulkan|cpu   default: cuda when nvcc is there (/usr/local/cuda/bin too), else cpu
#
# Result: voice/work/whisper.cpp/build/bin/whisper-server (+ whisper-cli) and
# voice/work/models/ggml-MODEL.bin, which voice/voice-server.py uses (VOICE_MODEL in tesla.conf
# picks another model). On a GPU large-v3-turbo is the most accurate and still fast; on a CPU
# base or small. Needs git, cmake, a C++ compiler (CUDA: the CUDA toolkit); no sudo.
set -euo pipefail
cd "$(dirname "$(realpath "$0")")"

VERSION=v1.9.5
MODEL=${1:-base}
[ -x /usr/local/cuda/bin/nvcc ] && PATH=$PATH:/usr/local/cuda/bin
GPU=${GPU:-$(command -v nvcc >/dev/null && echo cuda || echo cpu)}
case $GPU in
    cuda) gpu_opts=(-DGGML_CUDA=ON) ;;
    vulkan) gpu_opts=(-DGGML_VULKAN=ON) ;;
    cpu) gpu_opts=() ;;
    *) echo "GPU=$GPU: cuda, vulkan or cpu"; exit 1 ;;
esac
mkdir -p work/models
B=work/whisper.cpp/build

if [ ! -x $B/bin/whisper-server ] || [ "$(git -C work/whisper.cpp describe --tags 2>/dev/null)" != $VERSION ] ||
   [ "$(cat $B/gpu 2>/dev/null)" != "$GPU" ]; then
    echo "== whisper.cpp $VERSION ($GPU)"
    [ -d work/whisper.cpp ] || git clone --quiet --depth 1 --branch $VERSION https://github.com/ggml-org/whisper.cpp work/whisper.cpp
    git -C work/whisper.cpp fetch --quiet --depth 1 origin tag $VERSION && git -C work/whisper.cpp checkout --quiet $VERSION
    rm -f $B/CMakeCache.txt
    # static: the binaries need no libwhisper.so at run time
    cmake -S work/whisper.cpp -B $B -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF \
        -DWHISPER_BUILD_TESTS=OFF "${gpu_opts[@]}" >/dev/null
    cmake --build $B -j"$(nproc)" --target whisper-cli whisper-server >/dev/null
    echo "$GPU" >$B/gpu
fi
echo "   $PWD/$B/bin/whisper-server ($GPU)"

f=work/models/ggml-$MODEL.bin
if [ ! -s "$f" ]; then
    echo "== model $MODEL"
    curl -fL --retry 3 -o "$f.part" "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-$MODEL.bin"
    mv "$f.part" "$f"
fi
echo "   $PWD/$f ($(du -h "$f" | cut -f1))"
