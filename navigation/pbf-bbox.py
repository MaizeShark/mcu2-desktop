#!/usr/bin/env python3
"""Print the bounding box of an .osm.pbf extract: "min_lon max_lon min_lat max_lat", whole
degrees, rounded outwards (for valhalla_build_elevation). From the file's OSMHeader block
(HeaderBBox, in nanodegrees), which Geofabrik extracts carry."""
import struct
import sys
import zlib


def fields(data):
    """protobuf wire format: yields (field number, wire type, value)."""
    i = 0
    while i < len(data):
        key, i = varint(data, i)
        number, wire = key >> 3, key & 7
        if wire == 0:
            value, i = varint(data, i)
        elif wire == 2:
            size, i = varint(data, i)
            value, i = data[i:i + size], i + size
        elif wire == 1:
            value, i = data[i:i + 8], i + 8
        elif wire == 5:
            value, i = data[i:i + 4], i + 4
        else:
            raise ValueError("unsupported wire type %d" % wire)
        yield number, wire, value


def varint(data, i):
    shift = result = 0
    while True:
        b = data[i]
        i += 1
        result |= (b & 0x7f) << shift
        if not b & 0x80:
            return result, i
        shift += 7


def zigzag(n):
    return (n >> 1) ^ -(n & 1)


def main(path):
    with open(path, "rb") as f:
        size = struct.unpack(">I", f.read(4))[0]
        header = dict((n, v) for n, _, v in fields(f.read(size)))
        if header.get(1) != b"OSMHeader":
            sys.exit("%s: no OSMHeader block first" % path)
        blob = dict((n, v) for n, _, v in fields(f.read(header[3])))
    block = blob[1] if 1 in blob else zlib.decompress(blob[3])
    bbox = dict((n, v) for n, _, v in fields(block)).get(1)
    if bbox is None:
        sys.exit("%s: the header has no bounding box; give it by hand" % path)
    b = dict((n, zigzag(v) / 1e9) for n, _, v in fields(bbox))   # 1 left 2 right 3 top 4 bottom
    floor = lambda x: int(x // 1)
    print(floor(b[1]), -floor(-b[2]), floor(b[4]), -floor(-b[3]))


if __name__ == "__main__":
    main(sys.argv[1])
