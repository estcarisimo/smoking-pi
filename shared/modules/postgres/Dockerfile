FROM postgres:18-alpine

# SmokePing PostgreSQL Image
# Based on PostgreSQL 15 Alpine with SmokePing-specific configuration

LABEL maintainer="SmokePing Project"
LABEL description="PostgreSQL database for SmokePing target management"
LABEL version="15-alpine"

# Copy initialization scripts
COPY init/ /docker-entrypoint-initdb.d/

# Set default environment variables
ENV POSTGRES_DB=smokeping_targets
ENV POSTGRES_USER=smokeping