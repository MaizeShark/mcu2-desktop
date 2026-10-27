#!/bin/bash
# Build Valhalla 2.4.3 routing tiles from an OpenStreetMap extract, for Tesla's valhalla_server.
#
#   navigation/build-tiles.sh <extract.osm.pbf> [region]      region: 2 letters, default US
#
# Needs the valhalla:2.4.3 image: podman build -t valhalla:2.4.3 -f navigation/Containerfile navigation
# Result: navigation/work/tiles-<region>/valhalla/ (tiles) + admin.sqlite. Install into the rootfs
# with navigation/install-maps.sh.
set -euo pipefail
PBF=$(realpath "${1:?usage: $0 <extract.osm.pbf> [region]}")   # before cd, so relative paths work
cd "$(dirname "$(realpath "$0")")"

REGION=${2:-US}
OUT=$PWD/work/tiles-$REGION
mkdir -p "$OUT"

# Elevation: the SRTM tiles (1 degree, the "skadi" set of the public AWS terrain tiles, as
# valhalla_build_elevation fetches them) for the extract's bounding box. With them the tiles get
# each edge's elevation; the 2026 S/X firmware's TMServer needs that for a route's energy info
# ("missing elevation data": no route line on the map). Kept in work/elevation for the next build.
ELEV=$PWD/work/elevation
read -r X0 X1 Y0 Y1 < <(python3 pbf-bbox.py "$PBF") || { echo "no bounding box in $PBF"; exit 1; }
echo "== elevation for lon $X0..$X1, lat $Y0..$Y1 ($(( (X1 - X0) * (Y1 - Y0) )) tiles, 25 MB each)"
for ((x = X0; x < X1; x++)); do
    for ((y = Y0; y < Y1; y++)); do
        ns=N; [ $y -lt 0 ] && ns=S; ew=E; [ $x -lt 0 ] && ew=W
        dir=$(printf '%s%02d' $ns ${y#-}); file=$(printf '%s%s%03d.hgt' "$dir" $ew ${x#-})
        [ "$(stat -c %s "$ELEV/$dir/$file" 2>/dev/null)" = $((3601 * 3601 * 2)) ] && continue
        mkdir -p "$ELEV/$dir"
        # (tiles over the sea don't exist: 403/404, skipped)
        curl -fsS --retry 3 "https://s3.amazonaws.com/elevation-tiles-prod/skadi/$dir/$file.gz" 2>/dev/null |
            gunzip >"$ELEV/$dir/$file.part" 2>/dev/null && mv "$ELEV/$dir/$file.part" "$ELEV/$dir/$file" ||
            rm -f "$ELEV/$dir/$file.part"
    done
done
echo "   $(find "$ELEV" -name '*.hgt' | wc -l) elevation tiles in $ELEV"

# (the elevation tiles writable: valhalla 2.4.3 opens them read-write)
podman run --rm -v "$OUT":/out -v "$PBF":/input.osm.pbf:ro -v "$ELEV":/elevation valhalla:2.4.3 bash -c '
    set -e
    valhalla_build_config --mjolnir-tile-dir /out/valhalla \
        --mjolnir-admin /out/admin.sqlite --mjolnir-timezone /out/tz_world.sqlite \
        --additional-data-elevation /elevation/ > /out/build.json
    echo "== admins (country/state borders, driving side)"
    valhalla_build_admins -c /out/build.json /input.osm.pbf
    echo "== tiles"
    valhalla_build_tiles -c /out/build.json /input.osm.pbf
' 2>&1 | tee "$OUT/build.log"

echo "Tiles: $OUT/valhalla ($(du -sh "$OUT/valhalla" | cut -f1))"
