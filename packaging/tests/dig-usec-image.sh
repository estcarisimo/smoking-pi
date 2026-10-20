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

# SmokePing's DNS probe parses exactly this form: take the probe's own
# regex and pingfactor from the image's DNS.pm and apply them to what the
# wrapper printed. A sample of 0.144 msec must come out as 0.000144 s.
pm="$(docker run --rm --entrypoint /bin/sh "$image" -c \
    "find / -path '*Smokeping/probes/DNS.pm' 2>/dev/null | head -1")"
test -n "$pm"
docker run --rm --entrypoint /bin/sh "$image" -c "grep -inE 'query time|pingfactor' $pm"
re="$(docker run --rm --entrypoint /bin/sh "$image" -c "grep -i 'query time' $pm" |
    sed -n 's#.*qr/\(.*\)/i;.*#\1#p' | head -1)"
echo "probe regex: $re"
test -n "$re"
printf '%s\n' "$line" | RE="$re" perl -ne 'exit(1) unless /$ENV{RE}/i; $v = $1; exit(!($v =~ /\./ && $v > 0))'
echo "dig-usec image proof: ok"
