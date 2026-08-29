"""Code shared by more than one module image.

Modules run in separate containers and cannot import each other, so anything
two of them need lives here and is copied into each image (see the Dockerfiles
and the `context: ../../shared` build blocks in editions/pro).

What belongs here is code where a second copy would DRIFT rather than merely
duplicate: the Flux query helpers, the loss-unit clamping, the aggregate
queries, and the Grafana deep-link UID maps. All four already existed in two
or three places before this package.
"""
