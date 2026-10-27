#!/usr/bin/env python3
"""Write a device-mapper snapshot's changes into the image it was taken of (IMAGE_RAM).

./tesla start --image-ram runs the chroot on a dm snapshot whose copy-on-write store (persistent
format) is a file in /dev/shm. dm's own snapshot-merge copies one chunk at a time and flushes the
origin after each: on a USB stick 0.3 s per 4 KiB chunk, minutes for a few MB. This reads the
store's exception tables and writes every changed chunk into the image with one fsync at the end.
Run it after the file system is unmounted and the dm device removed.

  cow-apply.py COW_FILE IMAGE

The store (drivers/md/dm-snap-persistent.c): chunk 0 is the header (magic "SnAp", valid, version,
chunk size in sectors); then metadata areas, each one chunk of {le64 old_chunk, le64 new_chunk}
entries followed by the data chunks of those entries; an entry with new_chunk 0 ends the list.
"""
import os, struct, sys

cow_path, image_path = sys.argv[1], sys.argv[2]
with open(cow_path, "rb") as cow:
    magic, valid, version, chunk_sectors = struct.unpack("<IIII", cow.read(16))
    if magic != 0x70416E53:
        sys.exit("cow-apply: %s isn't a persistent snapshot store" % cow_path)
    if not valid:
        sys.exit("cow-apply: the snapshot was invalidated (full?); nothing written")
    chunk = chunk_sectors * 512
    per_area = chunk // 16
    image = os.open(image_path, os.O_WRONLY)
    n, area, done = 0, 0, False
    while not done:
        cow.seek((1 + area * (per_area + 1)) * chunk)
        meta = cow.read(chunk)
        if len(meta) < chunk:
            break
        for i in range(per_area):
            old, new = struct.unpack_from("<QQ", meta, i * 16)
            if new == 0:
                done = True
                break
            cow.seek(new * chunk)
            os.pwrite(image, cow.read(chunk), old * chunk)
            n += 1
        area += 1
    os.fsync(image)
    os.close(image)
print("%d chunks, %.1f MB" % (n, n * chunk / 1048576))
