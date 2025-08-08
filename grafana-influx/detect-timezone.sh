#!/bin/bash
# Timezone Detection Script
# Automatically detects and sets the appropriate timezone for the stack

set -e

echo "Detecting system timezone..."

# Method 1: Try timedatectl (systemd)
if command -v timedatectl >/dev/null 2>&1; then
    DETECTED_TZ=$(timedatectl show --property=Timezone --value 2>/dev/null || echo "")
    if [ -n "$DETECTED_TZ" ] && [ "$DETECTED_TZ" != "n/a" ]; then
        echo "Detected timezone via timedatectl: $DETECTED_TZ"
        TZ="$DETECTED_TZ"
    fi
fi

# Method 2: Try /etc/timezone file
if [ -z "$TZ" ] && [ -f /etc/timezone ]; then
    DETECTED_TZ=$(cat /etc/timezone 2>/dev/null | tr -d '\n' || echo "")
    if [ -n "$DETECTED_TZ" ]; then
        echo "Detected timezone via /etc/timezone: $DETECTED_TZ"
        TZ="$DETECTED_TZ"
    fi
fi

# Method 3: Try readlink on /etc/localtime
if [ -z "$TZ" ] && [ -L /etc/localtime ]; then
    DETECTED_TZ=$(readlink /etc/localtime 2>/dev/null | sed 's|.*/zoneinfo/||' || echo "")
    if [ -n "$DETECTED_TZ" ]; then
        echo "Detected timezone via /etc/localtime: $DETECTED_TZ"
        TZ="$DETECTED_TZ"
    fi
fi

# Method 4: Check TZ environment variable
if [ -z "$TZ" ] && [ -n "${TZ:-}" ]; then
    echo "Using TZ environment variable: $TZ"
fi

# Fallback to UTC
if [ -z "$TZ" ]; then
    echo "Could not detect timezone, defaulting to UTC"
    TZ="UTC"
fi

# Validate timezone
if ! python3 -c "import zoneinfo; zoneinfo.ZoneInfo('$TZ')" 2>/dev/null; then
    echo "Warning: Invalid timezone '$TZ', falling back to UTC"
    TZ="UTC"
fi

echo "Using timezone: $TZ"

# Update .env file
ENV_FILE=".env"
if [ -f "$ENV_FILE" ]; then
    # Check if TZ line exists
    if grep -q "^TZ=" "$ENV_FILE"; then
        # Update existing TZ line
        if [[ "$OSTYPE" == "darwin"* ]]; then
            # macOS sed
            sed -i '' "s|^TZ=.*|TZ=$TZ|" "$ENV_FILE"
        else
            # Linux sed
            sed -i "s|^TZ=.*|TZ=$TZ|" "$ENV_FILE"
        fi
        echo "Updated TZ in $ENV_FILE"
    else
        # Add TZ line
        echo "TZ=$TZ" >> "$ENV_FILE"
        echo "Added TZ to $ENV_FILE"
    fi
else
    echo "Warning: .env file not found"
fi

echo "Timezone configuration complete!"
echo ""
echo "To manually set a different timezone, edit .env:"
echo "  TZ=America/New_York      # Eastern Time"
echo "  TZ=America/Los_Angeles   # Pacific Time"
echo "  TZ=Europe/London         # UK Time"
echo "  TZ=Europe/Berlin         # Central European Time"
echo "  TZ=Asia/Tokyo           # Japan Time"
echo "  TZ=America/Argentina/Buenos_Aires  # Argentina Time"