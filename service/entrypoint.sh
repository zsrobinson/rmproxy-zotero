#!/bin/sh
# Run rmzotero as the owner of the mounted /data folder (or PUID/PGID if set), so the
# config and data written on the NAS by your own account are readable and writable.
set -e
if [ "$(id -u)" = "0" ]; then
  uid="${PUID:-$(stat -c %u /data)}"
  gid="${PGID:-$(stat -c %g /data)}"
  if [ "$uid" != "0" ]; then
    exec setpriv --reuid="$uid" --regid="$gid" --clear-groups env HOME=/data rmzotero "$@"
  fi
fi
exec rmzotero "$@"
