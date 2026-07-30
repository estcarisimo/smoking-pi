#!/usr/bin/with-contenv bash

# Link generated SmokePing config into /config.
#
# config-manager writes Targets/Probes atomically (temp file + os.replace),
# which swaps the file's inode. A single-file bind mount would keep pointing
# at the OLD inode until the container restarts, so instead the whole output
# directory is mounted read-only at /config/generated and we symlink the
# files SmokePing actually reads. Symlinks resolve through the directory
# mount, so they always see the newest inode.
#
# /config is a named volume (writable). Idempotent: safe to run on every
# container start. Pre-existing real files (from the old single-file bind
# mounts or stale volume contents) are replaced with symlinks.

set -u

GENERATED_DIR="/config/generated"

for name in Targets Probes; do
    src="${GENERATED_DIR}/${name}"
    dst="/config/${name}"

    if [ ! -e "$src" ]; then
        echo "[link-generated-config] WARNING: ${src} does not exist yet" \
             "(config-manager has not generated config); leaving ${dst} alone"
        continue
    fi

    # Already the correct symlink -> nothing to do
    if [ -L "$dst" ] && [ "$(readlink "$dst")" = "$src" ]; then
        echo "[link-generated-config] ${dst} already links to ${src}"
        continue
    fi

    # Replace whatever is there (real file, wrong symlink) with the symlink
    rm -f "$dst"
    ln -s "$src" "$dst"
    echo "[link-generated-config] Linked ${dst} -> ${src}"
done
