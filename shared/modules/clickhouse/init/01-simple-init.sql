-- Comprehensive ClickHouse initialization for SmokePing
CREATE DATABASE IF NOT EXISTS smokeping;

-- Main latency table for SmokePing data
CREATE TABLE IF NOT EXISTS smokeping.latency (
    timestamp DateTime64(3) CODEC(DoubleDelta, LZ4),
    target String CODEC(ZSTD(1)),
    category String CODEC(ZSTD(1)),
    host String CODEC(ZSTD(1)),
    measurement_type String DEFAULT 'latency' CODEC(ZSTD(1)),
    
    -- Latency metrics (in milliseconds)
    min_latency Nullable(Float64) CODEC(DoubleDelta, LZ4),
    max_latency Nullable(Float64) CODEC(DoubleDelta, LZ4),
    avg_latency Nullable(Float64) CODEC(DoubleDelta, LZ4),
    median_latency Nullable(Float64) CODEC(DoubleDelta, LZ4),
    
    -- Percentile metrics
    p10_latency Nullable(Float64) CODEC(DoubleDelta, LZ4),
    p20_latency Nullable(Float64) CODEC(DoubleDelta, LZ4),
    p80_latency Nullable(Float64) CODEC(DoubleDelta, LZ4),
    p90_latency Nullable(Float64) CODEC(DoubleDelta, LZ4),
    p95_latency Nullable(Float64) CODEC(DoubleDelta, LZ4),
    p99_latency Nullable(Float64) CODEC(DoubleDelta, LZ4),
    
    -- Loss metrics
    packet_loss Nullable(Float64) CODEC(DoubleDelta, LZ4),
    packets_sent Nullable(UInt32) CODEC(DoubleDelta, LZ4),
    packets_received Nullable(UInt32) CODEC(DoubleDelta, LZ4)
)
ENGINE = MergeTree()
PARTITION BY toYYYYMM(timestamp)
ORDER BY (target, category, timestamp)
TTL timestamp + INTERVAL 2 YEAR DELETE
SETTINGS index_granularity = 8192;

-- Create indexes for better query performance
CREATE INDEX IF NOT EXISTS idx_latency_measurement_type ON smokeping.latency (measurement_type) TYPE bloom_filter GRANULARITY 1;
CREATE INDEX IF NOT EXISTS idx_latency_category ON smokeping.latency (category) TYPE bloom_filter GRANULARITY 1;