# 🎨 Excalidraw Architecture Guide

## How to Create the Architecture Diagram in Excalidraw

Visit [excalidraw.com](https://excalidraw.com) and create the following elements:

### 1. **Network Targets Cloud** (Top)
- **Shape**: Rectangle with rounded corners
- **Color**: Light blue (#E3F2FD)
- **Text**: 
  ```
  🌐 Network Targets
  • Google, Facebook, Amazon, NYT
  • Apple, UBA, Netflix OCAs  
  • DNS: 8.8.8.8, 1.1.1.1, 9.9.9.9
  ```
- **Arrow**: Downward arrow labeled "probes every 5min"

### 2. **SmokePing Container** (Middle-Upper)
- **Shape**: Large rectangle with rounded corners
- **Color**: Light orange (#FFF3E0)
- **Title**: "📊 SmokePing"
- **Sub-components** (3 smaller rectangles inside):
  - **FPing Probe** (left)
  - **DNS Probe** (center) 
  - **Lighttpd + fcgiwrap** (right)
- **RRD Files box** (bottom of container):
  - Shape: Rectangle
  - Color: Light gray (#F5F5F5)
  - Text: "📁 RRD Files"
  - Bullet points: 
    - `/var/lib/smokeping/`
    - `/var/lib/smokeping/resolvers/`
    - `median, loss, ping1-20 values`

### 3. **RRD Exporter** (Middle)
- **Shape**: Rectangle
- **Color**: Light green (#E8F5E8)
- **Title**: "🚀 RRD Exporter (Python)"
- **Text**:
  ```
  • Watches RRD files for changes
  • Classifies targets by directory:
    - /resolvers/ → dns_latency measurement
    - /other/ → latency measurement
  • Converts RRD → InfluxDB line protocol
  ```
- **Arrow**: Downward arrow labeled "HTTP POST"

### 4. **InfluxDB Container** (Middle-Lower)
- **Shape**: Large rectangle
- **Color**: Light purple (#F3E5F5)
- **Title**: "💾 InfluxDB 2.x"
- **Sub-container**: "📊 Bucket: 'latency'"
- **Two measurement boxes side-by-side**:
  - **Left box**: "Measurement: 'latency'"
    - Amazon, Apple, etc.
    - Google (ping)
    - Facebook, NYT, UBA
    - Netflix OCAs
    - Fields: median, loss, ping1-20
  - **Right box**: "Measurement: 'dns_latency'"
    - Google (DNS)
    - Cloudflare  
    - Quad9
    - Fields: median, loss, ping1-20

### 5. **Grafana Container** (Bottom)
- **Shape**: Large rectangle
- **Color**: Light yellow (#FFFDE7)
- **Title**: "📈 Grafana"
- **Sub-container**: "📊 Dashboards"
- **Three dashboard boxes**:
  - **Left**: "Latency & Loss (Percentiles)" - Filters: latency targets
  - **Center**: "Side-by-Side Compare (Mosaic)" - Filters: latency targets
  - **Right**: "DNS Resolvers" - Filters: DNS only

### 6. **Connections & Arrows**
- **SmokePing → RRD Exporter**: "monitors & exports" + ":8080" label
- **RRD Exporter → InfluxDB**: "HTTP POST"
- **InfluxDB → Grafana**: "Flux queries" + ":8086" label
- **Grafana**: ":3000" label

### 7. **Color Scheme**
- **Network/External**: Light blue (#E3F2FD)
- **SmokePing**: Light orange (#FFF3E0)
- **Processing**: Light green (#E8F5E8)
- **Storage**: Light purple (#F3E5F5)
- **Visualization**: Light yellow (#FFFDE7)
- **Arrows**: Dark gray (#424242)

### 8. **Typography**
- **Titles**: Bold, larger size
- **Emojis**: Use throughout for visual appeal
- **Code/Paths**: Monospace font where possible
- **Bullet points**: Use • character

### 9. **Layout Tips**
- **Vertical flow**: Top to bottom data flow
- **Consistent spacing**: Equal gaps between major components
- **Alignment**: Center-align all major components
- **Grouping**: Use background rectangles to group related elements

### 10. **Export Settings**
- **Format**: PNG or SVG
- **Quality**: High resolution for documentation
- **Background**: White or transparent

This creates a professional, visually appealing architecture diagram that clearly shows the data flow and component relationships in your SmokePing-InfluxDB-Grafana stack!