#!/bin/sh
# Proof, in the built smokeping image, that dig-usec reaches a resolver and
# prints a time SmokePing's DNS probe parses -- against a throwaway fake DNS
# server on loopback (host network), no external traffic.
#   packaging/tests/dig-usec-image.sh <image>
set -eu
image="$1"
here="$(cd "$(dirname "$0")" && pwd)"
python3 "$here/fake-dns.py" 5533 &
srv=$!
trap 'kill $srv 2>/dev/null || true' EXIT
sleep 1

out="$(docker run --rm --network host --entrypoint /usr/local/bin/dig-usec "$image" \
    @127.0.0.1 -p 5533 localhost +tries=1 +time=3)"
echo "$out"
line="$(printf '%s\n' "$out" | grep -i '^;; Query time:')"
# The DNS probe's own regex (Smokeping::probes::DNS), with a decimal part.
printf '%s\n' "$line" | perl -ne 'exit(!(/query time:\s+([0-9.]+)\smsec.*/i && $1 =~ /\./))'

# Exit code and stderr pass through: a server that is not there.
if docker run --rm --network host --entrypoint /usr/local/bin/dig-usec "$image" \
    @127.0.0.1 -p 5399 localhost +tries=1 +time=1 >/dev/null; then
    echo "expected a non-zero exit with no server" >&2; exit 1
fi

# SmokePing's DNS probe parses exactly this form: show its regex in the
# image's own DNS.pm, and run it against what the wrapper printed.
pm="$(docker run --rm --entrypoint /bin/sh "$image" -c \
    "find / -path '*Smokeping/probes/DNS.pm' 2>/dev/null | head -1")"
test -n "$pm"
docker run --rm --entrypoint /bin/sh "$image" -c "grep -in 'query time' $pm"
echo "dig-usec image proof: ok"
