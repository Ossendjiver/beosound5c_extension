#!/bin/bash
# =============================================================================
# BeoSound 5c Installer — User group membership
# =============================================================================

configure_user_groups() {
    log_section "Configuring User Groups"

    log_info "Adding $INSTALL_USER to required groups..."
    usermod -aG video,input,bluetooth,dialout,tty "$INSTALL_USER"

    log_success "User added to groups: video, input, bluetooth, dialout, tty"

    # Passwordless sudo for kiosk commands and config management
    local SUDOERS_FILE="/etc/sudoers.d/beosound5c"
    log_info "Configuring passwordless sudo..."
    cat > "$SUDOERS_FILE" << SUDOEOF
# BeoSound 5c — UI kiosk and config management
$INSTALL_USER ALL=(ALL) NOPASSWD: /usr/bin/pkill, /usr/bin/fbi, /usr/bin/plymouth, /sbin/reboot, /usr/sbin/reboot
$INSTALL_USER ALL=(ALL) NOPASSWD: /usr/bin/tee /etc/beosound5c/config.json
$INSTALL_USER ALL=(ALL) NOPASSWD: /bin/systemctl restart beo-*
$INSTALL_USER ALL=(ALL) NOPASSWD: /bin/systemctl stop beo-*
$INSTALL_USER ALL=(ALL) NOPASSWD: /bin/systemctl start beo-*
$INSTALL_USER ALL=(ALL) NOPASSWD: $INSTALL_HOME/beosound5c/install/post-update.sh
SUDOEOF
    chmod 440 "$SUDOERS_FILE"
    log_success "Sudoers configured"
}
