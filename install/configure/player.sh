#!/bin/bash
# =============================================================================
# BeoSound 5c Installer — Player configuration
# =============================================================================

configure_player() {
    echo ""
    log_section "Player Configuration"

    local current_type current_ip current_mass_playback_mode
    current_type=$(cfg_read '.player.type')
    current_ip=$(cfg_read '.player.ip')
    current_mass_playback_mode=$(cfg_read '.mass.playback_mode')
    case "$current_mass_playback_mode" in
        auto|remote|local) ;;
        *) current_mass_playback_mode="auto" ;;
    esac

    if [ -n "$current_type" ] && [ "$current_type" != "" ]; then
        log_info "Current player: $current_type${current_ip:+ @ $current_ip}"
        echo ""
    fi

    echo "Select the network player type:"
    echo ""
    echo "  1) Sonos      - Sonos speaker (most common)"
    echo "  2) BlueSound  - BlueSound player"
    echo "  3) Music Assistant - MASS player / transport bridge"
    echo "  4) Local      - Local playback on the BeoSound 5c"
    echo "  5) None       - No player service"
    echo ""

    # Determine default based on current config
    local default_choice="1"
    case "$current_type" in
        sonos)     default_choice="1" ;;
        bluesound) default_choice="2" ;;
        mass)      default_choice="3" ;;
        local)     default_choice="4" ;;
        none)      default_choice="5" ;;
    esac

    local PLAYER_TYPE=""
    local PLAYER_IP=""
    local MASS_PLAYBACK_MODE="$current_mass_playback_mode"

    while true; do
        read -p "Select player type [1-5, default $default_choice]: " PLAYER_CHOICE
        PLAYER_CHOICE="${PLAYER_CHOICE:-$default_choice}"
        case "$PLAYER_CHOICE" in
            1) PLAYER_TYPE="sonos"; break ;;
            2) PLAYER_TYPE="bluesound"; break ;;
            3) PLAYER_TYPE="mass"; break ;;
            4) PLAYER_TYPE="local"; break ;;
            5) PLAYER_TYPE="none"; break ;;
            *) echo "Invalid selection. Please enter 1, 2, 3, 4, or 5." ;;
        esac
    done

    if [[ "$PLAYER_TYPE" == "sonos" ]]; then
        # Scan for Sonos devices
        mapfile -t sonos_results < <(scan_sonos_devices)

        if [ ${#sonos_results[@]} -gt 0 ]; then
            local sonos_display=()
            for result in "${sonos_results[@]}"; do
                local ip name
                ip=$(echo "$result" | cut -d'|' -f1)
                name=$(echo "$result" | cut -d'|' -f2)
                sonos_display+=("$name ($ip)")
            done

            log_success "Found ${#sonos_results[@]} Sonos device(s)!"

            if selection=$(select_from_list "Select Sonos speaker to control:" "${sonos_display[@]}"); then
                PLAYER_IP=$(echo "$selection" | grep -oP '\(([0-9.]+)\)' | tr -d '()')
            else
                local default_ip="${current_ip:-192.168.1.100}"
                read -p "Enter Sonos speaker IP address [$default_ip]: " PLAYER_IP
                PLAYER_IP="${PLAYER_IP:-$default_ip}"
            fi
        else
            log_warn "No Sonos devices found on the network"
            log_info "Make sure your Sonos speaker is powered on and connected to the same network"
            local default_ip="${current_ip:-192.168.1.100}"
            read -p "Enter Sonos speaker IP address [$default_ip]: " PLAYER_IP
            PLAYER_IP="${PLAYER_IP:-$default_ip}"
        fi
    elif [[ "$PLAYER_TYPE" == "bluesound" ]]; then
        # Scan for Bluesound devices
        mapfile -t bluesound_results < <(scan_bluesound_devices)

        if [ ${#bluesound_results[@]} -gt 0 ]; then
            local bluesound_display=()
            for result in "${bluesound_results[@]}"; do
                local ip name
                ip=$(echo "$result" | cut -d'|' -f1)
                name=$(echo "$result" | cut -d'|' -f2)
                bluesound_display+=("$name ($ip)")
            done

            log_success "Found ${#bluesound_results[@]} Bluesound device(s)!"

            if selection=$(select_from_list "Select Bluesound player to control:" "${bluesound_display[@]}"); then
                PLAYER_IP=$(echo "$selection" | grep -oP '\(([0-9.]+)\)' | tr -d '()')
            else
                local default_ip="${current_ip:-192.168.1.100}"
                read -p "Enter Bluesound player IP address [$default_ip]: " PLAYER_IP
                PLAYER_IP="${PLAYER_IP:-$default_ip}"
            fi
        else
            log_warn "No Bluesound devices found on the network"
            log_info "Make sure your Bluesound player is powered on and connected to the same network"
            local default_ip="${current_ip:-192.168.1.100}"
            read -p "Enter Bluesound player IP address [$default_ip]: " PLAYER_IP
            PLAYER_IP="${PLAYER_IP:-$default_ip}"
        fi
    elif [[ "$PLAYER_TYPE" == "mass" ]]; then
        local default_ip="${current_ip:-192.168.1.100}"
        read -p "Enter Music Assistant host/IP [$default_ip]: " PLAYER_IP
        PLAYER_IP="${PLAYER_IP:-$default_ip}"
    fi

    if [[ "$PLAYER_TYPE" == "mass" || "$PLAYER_TYPE" == "local" ]]; then
        echo ""
        echo "MASS source playback mode:"
        echo ""
        echo "  1) Auto       - Prefer local playback on BS5c-local outputs, remote playback on HASS/network outputs (Recommended)"
        echo "  2) Remote     - MASS controls a Music Assistant queue/player remotely"
        echo "  3) Local      - MASS resolves stream URLs and plays them on the BeoSound 5c"
        echo ""

        local default_mass_choice="1"
        case "$MASS_PLAYBACK_MODE" in
            remote) default_mass_choice="2" ;;
            local)  default_mass_choice="3" ;;
        esac

        while true; do
            read -p "Select MASS playback mode [1-3, default $default_mass_choice]: " MASS_CHOICE
            MASS_CHOICE="${MASS_CHOICE:-$default_mass_choice}"
            case "$MASS_CHOICE" in
                1) MASS_PLAYBACK_MODE="auto"; break ;;
                2) MASS_PLAYBACK_MODE="remote"; break ;;
                3) MASS_PLAYBACK_MODE="local"; break ;;
                *) echo "Invalid selection. Please enter 1, 2, or 3." ;;
            esac
        done

        if [[ "$PLAYER_TYPE" != "local" && "$MASS_PLAYBACK_MODE" == "local" ]]; then
            log_warn "MASS local playback requires Player = Local. This mode will stay unavailable until the local player is selected."
        fi
    fi

    local tmp
    tmp=$(mktemp)
    if jq --arg t "$PLAYER_TYPE" --arg ip "$PLAYER_IP" --arg mass_mode "$MASS_PLAYBACK_MODE" \
        '.player.type = $t | .player.ip = $ip | .mass.playback_mode = $mass_mode' "$CONFIG_FILE" > "$tmp"; then
        mv "$tmp" "$CONFIG_FILE"; chmod 644 "$CONFIG_FILE"
    else
        rm -f "$tmp"; log_error "Failed to update config.json"
    fi
    log_success "Player: $PLAYER_TYPE${PLAYER_IP:+ @ $PLAYER_IP}"
    if [[ "$PLAYER_TYPE" == "mass" || "$PLAYER_TYPE" == "local" ]]; then
        log_success "MASS playback mode: $MASS_PLAYBACK_MODE"
    fi

    # Export for use by other configure steps in full-wizard mode
    _PLAYER_TYPE="$PLAYER_TYPE"
    _PLAYER_IP="$PLAYER_IP"
    _MASS_PLAYBACK_MODE="$MASS_PLAYBACK_MODE"
}
