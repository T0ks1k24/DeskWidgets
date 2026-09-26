#!/usr/bin/env bash
# LAN device watch for the Network widget.
# Ping-scans the subnet of the default route with nmap and flags every device whose MAC is
# not listed in KNOWN ("mac|friendly name" per line). Notifies once per new unknown device.
# Usage: netwatch.sh CACHE KNOWN SEEN LOG [uk|en]
CACHE=$1 KNOWN=$2 SEEN=$3 LOG=$4 LANG_UI=${5:-uk}
MACDB=/usr/share/nmap/nmap-mac-prefixes

tr_() { [ "$LANG_UI" = en ] && printf '%s' "$2" || printf '%s' "$1"; }

if ! command -v nmap >/dev/null; then
    printf 'NONMAP\n' > "$CACHE"
    exit 0
fi
touch "$KNOWN" "$SEEN" "$LOG"

read -r DEV GATEWAY < <(ip route show default | awk '{for (i = 1; i <= NF; i++) {if ($i == "dev") d = $(i + 1); if ($i == "via") g = $(i + 1)} print d, g; exit}')
SUBNET=$(ip -o -f inet addr show "$DEV" 2>/dev/null | awk '{print $4; exit}')
[ -z "$SUBNET" ] && { printf 'OFFLINE\n' > "$CACHE"; exit 0; }

# unprivileged ping scan; it also fills the kernel neighbour table with the MACs of everyone who replied
nmap -sn "$SUBNET" >/dev/null 2>&1

vendor_for() {
    local oui
    oui=$(tr -d ':' <<<"$1" | tr 'a-f' 'A-F')
    awk -v oui="${oui:0:6}" '$1 == oui {sub($1 FS, ""); print; exit}' "$MACDB" 2>/dev/null
}

declare -A seen_mac
total=0
unknown=()
while read -r ip mac state; do
    [ "$ip" = "$GATEWAY" ] && continue
    [[ "$mac" =~ ^([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$ ]] || continue
    [ "$state" = "FAILED" ] && continue
    mac=${mac,,}
    [ -n "${seen_mac[$mac]}" ] && continue
    seen_mac[$mac]=1
    total=$((total + 1))
    grep -qi "^$mac|" "$KNOWN" || unknown+=("$ip|$mac|$(vendor_for "$mac")")
done < <(ip neigh show dev "$DEV" | awk '/lladdr/ {print $1, $3, $NF}')

for entry in "${unknown[@]}"; do
    IFS='|' read -r ip mac vendor <<<"$entry"
    if ! grep -qi "^$mac$" "$SEEN"; then
        echo "$mac" >> "$SEEN"
        echo "$(date '+%Y-%m-%d %H:%M:%S') $(tr_ 'НОВИЙ пристрій' 'NEW device'): $ip · $mac · ${vendor:-?}" >> "$LOG"
        notify-send -u critical -i network-wireless "$(tr_ 'Новий пристрій у мережі' 'New device on your network')" \
            "$ip · ${vendor:-$(tr_ 'невідомий виробник' 'unknown vendor')}\nMAC: $mac" 2>/dev/null
    fi
done

if [ ${#unknown[@]} -eq 0 ]; then
    printf 'OK\n%s\n\n\n' "$total" > "$CACHE.tmp"
else
    IFS='|' read -r ip _mac vendor <<<"${unknown[0]}"
    printf 'ALERT\n%s\n%s\n%s\n' "$total" "${#unknown[@]}" "$ip" > "$CACHE.tmp"
fi
mv "$CACHE.tmp" "$CACHE"
