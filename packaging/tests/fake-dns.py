"""A throwaway DNS server on loopback: answers every query with an empty
NOERROR response. For packaging/tests/dig-usec-image.sh only."""
import socket
import sys

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(("127.0.0.1", int(sys.argv[1])))
while True:
    data, peer = sock.recvfrom(512)
    # Same id and question; QR=1, RD copied, RA=1, no answers.
    flags = 0x8000 | (data[2] & 0x01) << 8 | 0x0080
    sock.sendto(data[:2] + flags.to_bytes(2, "big") + data[4:6] + b"\0\0\0\0\0\0"
                + data[12:], peer)
