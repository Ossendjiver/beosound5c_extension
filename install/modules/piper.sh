#!/bin/bash
# =============================================================================
# BeoSound 5c Installer — Piper TTS (offline text-to-speech)
# =============================================================================

PIPER_VERSION="2023.11.14-2"
PIPER_INSTALL_DIR="/opt/piper"
PIPER_BINARY="$PIPER_INSTALL_DIR/piper/piper"
PIPER_VOICE_DIR="$PIPER_INSTALL_DIR/voices"
PIPER_VOICE="en_US-lessac-medium"

install_piper() {
    log_section "Installing Piper TTS"

    # Detect architecture
    local ARCH
    ARCH=$(uname -m)
    case "$ARCH" in
        aarch64) ARCH="aarch64" ;;
        x86_64)  ARCH="x86_64" ;;
        armv7l)  ARCH="armv7l" ;;  # 32-bit ARM (limited support)
        *)
            log_warn "Unsupported architecture for Piper: $ARCH — skipping"
            return
            ;;
    esac

    # Download if not installed or wrong version
    local NEEDS_INSTALL=true
    if [ -x "$PIPER_BINARY" ]; then
        local CURRENT_VERSION
        CURRENT_VERSION=$("$PIPER_BINARY" --version 2>&1 | grep -oP '[0-9]+\.[0-9]+\.[0-9]+-[0-9]+' || echo "")
        if [ "$CURRENT_VERSION" = "$PIPER_VERSION" ]; then
            log_info "Piper $PIPER_VERSION already installed"
            NEEDS_INSTALL=false
        else
            log_info "Upgrading Piper from $CURRENT_VERSION to $PIPER_VERSION"
        fi
    fi

    if [ "$NEEDS_INSTALL" = true ]; then
        local URL="https://github.com/rhasspy/piper/releases/download/${PIPER_VERSION}/piper_linux_${ARCH}.tar.gz"
        log_info "Downloading Piper $PIPER_VERSION ($ARCH)..."
        local TMP_DIR
        TMP_DIR=$(mktemp -d)
        if curl -fsSL "$URL" -o "$TMP_DIR/piper.tar.gz"; then
            mkdir -p "$PIPER_INSTALL_DIR"
            tar -xzf "$TMP_DIR/piper.tar.gz" -C "$PIPER_INSTALL_DIR"
            rm -rf "$TMP_DIR"
            log_success "Piper $PIPER_VERSION installed to $PIPER_INSTALL_DIR"
        else
            log_warn "Failed to download Piper — skipping"
            rm -rf "$TMP_DIR"
            return
        fi
    fi

    # Download voice model if not present
    mkdir -p "$PIPER_VOICE_DIR"
    if [ ! -f "$PIPER_VOICE_DIR/${PIPER_VOICE}.onnx" ]; then
        local VOICE_BASE="https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/lessac/medium"
        log_info "Downloading voice model: $PIPER_VOICE..."
        if curl -fsSL "$VOICE_BASE/en_US-lessac-medium.onnx" -o "$PIPER_VOICE_DIR/${PIPER_VOICE}.onnx" && \
           curl -fsSL "$VOICE_BASE/en_US-lessac-medium.onnx.json" -o "$PIPER_VOICE_DIR/${PIPER_VOICE}.onnx.json"; then
            log_success "Voice model downloaded"
        else
            log_warn "Failed to download voice model — TTS will not work until model is installed"
        fi
    else
        log_info "Voice model already present"
    fi
}
