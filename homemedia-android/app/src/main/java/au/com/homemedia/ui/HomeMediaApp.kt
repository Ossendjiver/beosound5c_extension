@Composable
private fun KodiLibraryScreen(controller: AppController, room: RoomConfig?) {
    val state by controller.kodiBrowse.collectAsState()
    val artworkRoom = controller.kodiLibraryHostRoom() ?: room
    if (state.type == KodiBrowseType.HOME) {
        val youtubeLibrary by controller.youtubeLibrary.collectAsState()
        val combinedYoutube = remember(youtubeLibrary) {
            (youtubeLibrary.history + youtubeLibrary.playlists.flatMap { it.videos })
                .distinctBy { it.videoId }
        }
        val categories = listOf(
            KodiBrowseType.MOVIES to "Movies",
            KodiBrowseType.TV_SHOWS to "TV shows",
            KodiBrowseType.MUSIC_VIDEOS to "Music videos",
            KodiBrowseType.VIDEO_SOURCES to "Video files"
        )
        LazyVerticalGrid(
            columns = GridCells.Adaptive(145.dp),
            contentPadding = PaddingValues(16.dp),
            horizontalArrangement = Arrangement.spacedBy(12.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp)
        ) {
            item(span = { GridItemSpan(maxLineSpan) }) {
                Text("Kodi", fontWeight = FontWeight.Bold, fontSize = 20.sp, modifier = Modifier.padding(top = 2.dp, bottom = 2.dp))
            }
            gridItems(categories, key = { "kodi-" + it.first.name }) { (type, title) ->
                Card(
                    Modifier.fillMaxWidth().aspectRatio(1.2f).combinedClickable(onClick = { controller.openKodiCategory(type) }, onLongClick = {}),
                    colors = CardDefaults.cardColors(containerColor = Panel2)
                ) {
                    Column(Modifier.fillMaxSize().padding(16.dp), verticalArrangement = Arrangement.SpaceBetween) {
                        Icon(kodiCategoryIcon(type), null, modifier = Modifier.size(30.dp))
                        Text(title, fontWeight = FontWeight.SemiBold)
                    }
                }
            }

            item(span = { GridItemSpan(maxLineSpan) }) {
                Row(Modifier.fillMaxWidth().padding(top = 10.dp), verticalAlignment = Alignment.CenterVertically) {
                    Text("YouTube library", fontWeight = FontWeight.Bold, fontSize = 20.sp, modifier = Modifier.weight(1f))
                    TextButton(onClick = { controller.openYouTube("History") }) { Text("Open YouTube") }
                }
            }
            if (combinedYoutube.isEmpty()) {
                item(span = { GridItemSpan(maxLineSpan) }) {
                    Text("Watched and playlist videos will appear here.", color = TextMuted, fontSize = 13.sp)
                }
            } else {
                gridItems(combinedYoutube, key = { "yt-" + it.videoId }) { video ->
                    Card(
                        Modifier.fillMaxWidth().combinedClickable(
                            onClick = { controller.requestYouTubePlayback(video) },
                            onLongClick = { controller.openYouTube("History") }
                        ),
                        colors = CardDefaults.cardColors(containerColor = Panel2)
                    ) {
                        Column {
                            Surface(Modifier.fillMaxWidth().aspectRatio(16f / 9f), color = Panel3) {
                                if (video.thumbnail.isNotBlank()) {
                                    AsyncImage(video.thumbnail, null, Modifier.fillMaxSize(), contentScale = ContentScale.Crop)
                                } else {
                                    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) { Icon(Icons.Default.SmartDisplay, null) }
                                }
                            }
                            Column(Modifier.padding(10.dp)) {
                                Text(video.title, maxLines = 2, overflow = TextOverflow.Ellipsis, fontWeight = FontWeight.Medium)
                                if (video.channel.isNotBlank()) Text(video.channel, color = TextMuted, fontSize = 11.sp, maxLines = 1)
                            }
                        }
                    }
                }
            }

            if (youtubeLibrary.channels.isNotEmpty()) {
                item(span = { GridItemSpan(maxLineSpan) }) {
                    Text("Saved YouTube channels", fontWeight = FontWeight.Bold, fontSize = 18.sp, modifier = Modifier.padding(top = 10.dp))
                }
                gridItems(youtubeLibrary.channels, key = { "channel-" + it.url }) { channel ->
                    Card(
                        Modifier.fillMaxWidth().aspectRatio(1.2f).combinedClickable(
                            onClick = { controller.openSavedYouTubeChannel(channel) },
                            onLongClick = { controller.openYouTube("Channels") }
                        ),
                        colors = CardDefaults.cardColors(containerColor = Panel2)
                    ) {
                        Column(Modifier.fillMaxSize().padding(14.dp), verticalArrangement = Arrangement.SpaceBetween) {
                            Icon(Icons.Default.Subscriptions, null, modifier = Modifier.size(30.dp))
                            Text(channel.name, fontWeight = FontWeight.SemiBold, maxLines = 2, overflow = TextOverflow.Ellipsis)
                        }
                    }
                }
            }

            item(span = { GridItemSpan(maxLineSpan) }) {
                Text("Stremio", fontWeight = FontWeight.Bold, fontSize = 20.sp, modifier = Modifier.padding(top = 10.dp))
            }
            item {
                MediaHubTile("Search Stremio", "Movies, series & streams", Icons.Default.MovieFilter, controller::openStremio)
            }
        }
        return
    }

    Column(Modifier.fillMaxSize()) {
        Row(Modifier.fillMaxWidth().padding(horizontal = 12.dp), verticalAlignment = Alignment.CenterVertically) {
            IconButton(onClick = controller::kodiBrowseBack) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Back") }
            Text(state.title, fontWeight = FontWeight.Bold, fontSize = 20.sp, maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
        if (state.items.isEmpty()) EmptyState("Nothing here", "Kodi returned no items.")
        else LazyColumn(contentPadding = PaddingValues(horizontal = 12.dp, vertical = 4.dp)) {
            items(state.items, key = { "${state.type}:${it.id}:${it.file}:${it.label}" }) { item ->
                KodiItemRow(item, artworkRoom) { controller.kodiSelectItem(item) }
            }
        }
    }
}

private fun kodiCategoryIcon(type: KodiBrowseType) = when (type) {
    KodiBrowseType.MOVIES -> Icons.Default.Movie
    KodiBrowseType.TV_SHOWS -> Icons.Default.LiveTv
    KodiBrowseType.MUSIC_VIDEOS -> Icons.Default.MusicVideo
    KodiBrowseType.ARTISTS -> Icons.Default.Person
    KodiBrowseType.ALBUMS -> Icons.Default.Album
    KodiBrowseType.SONGS -> Icons.Default.MusicNote
    KodiBrowseType.VIDEO_SOURCES, KodiBrowseType.MUSIC_SOURCES -> Icons.Default.Folder
    else -> Icons.Default.VideoLibrary
}

@Composable
private fun KodiItemRow(item: KodiLibraryItem, room: RoomConfig?, onClick: () -> Unit) {
    Row(Modifier.fillMaxWidth().combinedClickable(onClick = onClick, onLongClick = {}).padding(vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
        Surface(Modifier.size(58.dp), shape = RoundedCornerShape(10.dp), color = Panel2) {
            val imageUrl = room?.kodi?.let { kodiImageUrl(it, item.thumbnail) }.orEmpty()
            if (imageUrl.isNotBlank() && room != null) KodiImage(imageUrl, room.kodi, Modifier.fillMaxSize())
            else Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) { Icon(if (item.directory) Icons.Default.Folder else Icons.Default.PlayCircle, null, tint = TextMuted) }
        }
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f)) {
            Text(item.label, fontWeight = FontWeight.Medium, maxLines = 1, overflow = TextOverflow.Ellipsis)
            if (item.subtitle.isNotBlank()) Text(item.subtitle, color = TextMuted, fontSize = 12.sp, maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
        Icon(if (item.directory || item.mediaType in listOf("tvshow", "season", "artist", "album")) Icons.Default.ChevronRight else Icons.Default.PlayArrow, null, tint = TextMuted)
    }
}

private fun kodiImageUrl(config: KodiConfig, raw: String): String {
    if (raw.isBlank()) return ""
    if (raw.startsWith("http://") || raw.startsWith("https://")) return raw
    val base = config.baseUrl.trimEnd('/')
    if (base.isBlank()) return ""
    return "$base/image/${java.net.URLEncoder.encode(raw, "UTF-8")}" 
}

// ---------------- Settings ----------------

@Composable
private fun SettingsScreen(controller: AppController, initial: AppSettings, onImportSettings: () -> Unit) {
    var draft by remember(initial) { mutableStateOf(initial) }
    var selectedRoomId by remember(initial.rooms) { mutableStateOf(initial.rooms.firstOrNull()?.id.orEmpty()) }
    val roomIndex = draft.rooms.indexOfFirst { it.id == selectedRoomId }.takeIf { it >= 0 } ?: 0
    val room = draft.rooms.getOrNull(roomIndex)

    Column(Modifier.fillMaxSize()) {
        LazyColumn(Modifier.weight(1f), contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            item {
                SettingsSection("Connections") {
                    SettingsField("Home Assistant URL", draft.homeAssistantUrl) { draft = draft.copy(homeAssistantUrl = it) }
                    SettingsField("Home Assistant token", draft.homeAssistantToken, secret = true) { draft = draft.copy(homeAssistantToken = it) }
                    SettingsField("Music Assistant URL", draft.musicAssistantUrl) { draft = draft.copy(musicAssistantUrl = it) }
                    SettingsField("Music Assistant token", draft.musicAssistantToken, secret = true) { draft = draft.copy(musicAssistantToken = it) }
                    Text("YouTube search uses NewPipeExtractor — no API key required.", color = TextMuted, fontSize = 12.sp, modifier = Modifier.padding(vertical = 4.dp))
                    SettingSwitch("Bluetooth room detection", draft.bluetoothLocationEnabled) { draft = draft.copy(bluetoothLocationEnabled = it) }
                    OutlinedButton(onClick = onImportSettings, modifier = Modifier.fillMaxWidth().padding(vertical = 6.dp)) {
                        Icon(Icons.Default.FolderZip, null)
                        Spacer(Modifier.width(8.dp))
                        Text("Import settings ZIP")
                    }
                    SettingsField("Shared MA queue/player ID", draft.sharedMaQueueId) { draft = draft.copy(sharedMaQueueId = it) }
                    SettingsField("Shared MA queue/player name", draft.sharedMaQueueName) { draft = draft.copy(sharedMaQueueName = it) }
                    SettingsField("Global media entity", draft.globalMediaEntity) { draft = draft.copy(globalMediaEntity = it) }
                    SettingsField("Link metadata entity", draft.linkMediaPlayerEntity) { draft = draft.copy(linkMediaPlayerEntity = it) }
                    SettingSwitch("Automatic room from presence", draft.automaticRoom) { draft = draft.copy(automaticRoom = it) }
                    SettingSwitch("Use sole active player as room fallback", draft.activePlayerLocationFallback) { draft = draft.copy(activePlayerLocationFallback = it) }
                }
            }
            item {
                SettingsSection("Rooms") {
                    ScrollableTabRow(selectedTabIndex = roomIndex.coerceAtMost((draft.rooms.size - 1).coerceAtLeast(0)), edgePadding = 0.dp) {
                        draft.rooms.forEach { r -> Tab(selected = r.id == selectedRoomId, onClick = { selectedRoomId = r.id }, text = { Text(r.name) }) }
                    }
                }
            }
            if (room != null) {
                item {
                    SettingsSection("${room.name} routing") {
                        RoomEditor(room) { updated ->
                            draft = draft.copy(rooms = draft.rooms.map { if (it.id == updated.id) updated else it })
                        }
                        Spacer(Modifier.height(12.dp))
                        val calibrated = room.bluetoothCalibrationPoints.size >= 3
                        val calibrationLabel = when {
                            room.bluetoothQuietRoom && calibrated -> "3-point calibration complete · Bluetooth-quiet room"
                            calibrated -> "3-point calibration complete"
                            else -> "Not calibrated"
                        }
                        Text(calibrationLabel, color = if (calibrated) ActiveGreen else TextMuted, fontSize = 12.sp)
                        Spacer(Modifier.height(6.dp))
                        OutlinedButton(
                            onClick = { controller.beginBluetoothCalibration(room.id) },
                            modifier = Modifier.fillMaxWidth()
                        ) {
                            Icon(Icons.Default.BluetoothSearching, null)
                            Spacer(Modifier.width(8.dp))
                            Text(if (calibrated) "Recalibrate Bluetooth (3 points)" else "Calibrate Bluetooth (3 points)")
                        }
                    }
                }
                item {
                    SettingsSection("${room.name} tiles") {
                        SourceTileAdder(room, controller.availableSources(room)) { updatedRoom ->
                            draft = draft.copy(rooms = draft.rooms.map { if (it.id == room.id) updatedRoom else it })
                        }
                        if (room.sourceOptions.isNotEmpty()) HorizontalDivider(Modifier.padding(vertical = 8.dp))
                        room.tiles.forEachIndexed { idx, tile ->
                            TileEditor(idx + 1, tile) { updated ->
                                val newTiles = room.tiles.toMutableList().apply { this[idx] = updated }
                                val updatedRoom = room.copy(tiles = newTiles)
                                draft = draft.copy(rooms = draft.rooms.map { if (it.id == room.id) updatedRoom else it })
                            }
                            if (idx < room.tiles.lastIndex) HorizontalDivider(Modifier.padding(vertical = 8.dp))
                        }
                    }
                }
            }
        }
        Surface(color = Panel, tonalElevation = 6.dp) {
            Row(Modifier.fillMaxWidth().padding(14.dp), horizontalArrangement = Arrangement.End) {
                Button(onClick = { controller.saveSettings(draft); controller.goRoom() }) { Icon(Icons.Default.Save, null); Spacer(Modifier.width(6.dp)); Text("Save settings") }
            }
        }
    }
}

@Composable
private fun SettingsSection(title: String, content: @Composable ColumnScope.() -> Unit) {
    Card(colors = CardDefaults.cardColors(containerColor = Panel), shape = RoundedCornerShape(18.dp)) {
        Column(Modifier.fillMaxWidth().padding(16.dp)) {
            Text(title, fontWeight = FontWeight.Bold, fontSize = 18.sp)
            Spacer(Modifier.height(10.dp))
            content()
        }
    }
}

@Composable
private fun RoomEditor(room: RoomConfig, onChange: (RoomConfig) -> Unit) {
    SettingsField("Room name", room.name) { onChange(room.copy(name = it)) }
    SettingsField("Primary HA media_player", room.primaryPlayerEntity) { onChange(room.copy(primaryPlayerEntity = it)) }
    SettingsField("MLGW HA entity (blank = primary)", room.mlgwEntity) { onChange(room.copy(mlgwEntity = it)) }
    SettingsField("Native Music Assistant player/queue ID", room.maPlayerId) { onChange(room.copy(maPlayerId = it)) }
    SettingsField("Native Music Assistant player name", room.maPlayerName) { onChange(room.copy(maPlayerName = it)) }
    SettingsField("YouTube Cast HA entity (optional; Bedroom auto-detects)", room.youtubeCastEntity) { onChange(room.copy(youtubeCastEntity = it)) }
    SettingsField("Source options (comma separated)", room.sourceOptions.joinToString(", ")) {
        onChange(room.copy(sourceOptions = it.split(",").map(String::trim).filter(String::isNotBlank)))
    }
    room.secondaryPlayers.forEachIndexed { index, secondary ->
        Spacer(Modifier.height(8.dp))
        Text("Secondary player ${index + 1}", fontWeight = FontWeight.SemiBold)
        SecondaryPlayerEditor(secondary) { updated ->
            onChange(room.copy(secondaryPlayers = room.secondaryPlayers.toMutableList().apply { this[index] = updated }))
        }
    }
    val anchor = room.bluetoothAnchors.firstOrNull() ?: BluetoothAnchorConfig()
    Spacer(Modifier.height(8.dp))
    Text("Bluetooth room anchor", fontWeight = FontWeight.SemiBold)
    SettingsField("BLE address / MAC", anchor.address) { value ->
        onChange(room.copy(bluetoothAnchors = listOf(anchor.copy(address = value))))
    }
    SettingsField("BLE device name contains", anchor.nameContains) { value ->
        onChange(room.copy(bluetoothAnchors = listOf(anchor.copy(nameContains = value))))
    }
    SettingsField("Minimum RSSI", anchor.minRssi.toString(), numeric = true) { value ->
        onChange(room.copy(bluetoothAnchors = listOf(anchor.copy(minRssi = value.toIntOrNull() ?: anchor.minRssi))))
    }
    SettingsField("Presence entity", room.presenceEntity) { onChange(room.copy(presenceEntity = it)) }
    SettingsField("Presence value", room.presenceValue) { onChange(room.copy(presenceValue = it)) }
    SettingsField("Link source label", room.linkSourceName) { onChange(room.copy(linkSourceName = it)) }
    SettingsField("A.AUX source label", room.auxSourceName) { onChange(room.copy(auxSourceName = it)) }
    SourceSelector(room.sharedPlaybackSource) { onChange(room.copy(sharedPlaybackSource = it)) }
    SettingsField("Power-on delay (ms)", room.powerOnDelayMs.toString(), numeric = true) { onChange(room.copy(powerOnDelayMs = it.toLongOrNull() ?: room.powerOnDelayMs)) }
    SettingsField("Source-confirm timeout (ms)", room.sourceConfirmTimeoutMs.toString(), numeric = true) { onChange(room.copy(sourceConfirmTimeoutMs = it.toLongOrNull() ?: room.sourceConfirmTimeoutMs)) }
    Spacer(Modifier.height(8.dp))
    Text("Kodi", fontWeight = FontWeight.SemiBold)
    SettingsField("Kodi base URL", room.kodi.baseUrl) { onChange(room.copy(kodi = room.kodi.copy(baseUrl = it))) }
    SettingsField("Kodi username", room.kodi.username) { onChange(room.copy(kodi = room.kodi.copy(username = it))) }
    SettingsField("Kodi password", room.kodi.password, secret = true) { onChange(room.copy(kodi = room.kodi.copy(password = it))) }
    SettingsField("Kodi source label", room.kodiSourceName) { onChange(room.copy(kodiSourceName = it)) }
}

@Composable
private fun SecondaryPlayerEditor(player: SecondaryPlayerConfig, onChange: (SecondaryPlayerConfig) -> Unit) {
    SettingsField("Name", player.name) { onChange(player.copy(name = it)) }
    SettingsField("HA media_player", player.haEntity) { onChange(player.copy(haEntity = it)) }
    SettingsField("Music Assistant player ID", player.maPlayerId) { onChange(player.copy(maPlayerId = it)) }
    SettingsField("Music Assistant player name", player.maPlayerName) { onChange(player.copy(maPlayerName = it)) }
    SettingsField("Optional toggle entity", player.toggleEntity) { onChange(player.copy(toggleEntity = it)) }
}

@Composable
private fun SourceTileAdder(
    room: RoomConfig,
    availableSources: List<String>,
    onChange: (RoomConfig) -> Unit
) {
    if (availableSources.isEmpty()) return
    Text("Add source tile", color = TextMuted, fontSize = 12.sp)
    Row(
        Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()),
        horizontalArrangement = Arrangement.spacedBy(6.dp)
    ) {
        availableSources.forEach { source ->
            val exists = room.tiles.any {
                it.actionType == TileActionType.SELECT_SOURCE && it.source.equals(source, true)
            }
            AssistChip(
                onClick = {
                    if (!exists) onChange(room.copy(tiles = room.tiles + TileConfig(
                        title = source,
                        icon = if (source.equals("CD", true)) "disc" else "radio",
                        actionType = TileActionType.SELECT_SOURCE,
                        source = source
                    )))
                },
                enabled = !exists,
                label = { Text(source) }
            )
        }
    }
}

@Composable
private fun SourceSelector(value: SharedSource, onChange: (SharedSource) -> Unit) {
    var expanded by remember { mutableStateOf(false) }
    Box(Modifier.fillMaxWidth().padding(vertical = 4.dp)) {
        OutlinedButton(onClick = { expanded = true }, modifier = Modifier.fillMaxWidth()) { Text("Shared playback source: ${if (value == SharedSource.LINK) "Link" else "A.AUX"}") }
        DropdownMenu(expanded = expanded, onDismissRequest = { expanded = false }) {
            SharedSource.entries.forEach { source ->
                DropdownMenuItem(text = { Text(if (source == SharedSource.LINK) "Link" else "A.AUX") }, onClick = { expanded = false; onChange(source) })
            }
        }
    }
}

@Composable
private fun TileEditor(number: Int, tile: TileConfig, onChange: (TileConfig) -> Unit) {
    Text("Tile $number", color = TextMuted, fontSize = 12.sp)
    SettingsField("Title", tile.title) { onChange(tile.copy(title = it)) }
    SettingsField("Icon", tile.icon) { onChange(tile.copy(icon = it)) }
    TileActionSelector(tile.actionType) { onChange(tile.copy(actionType = it)) }
    if (tile.actionType == TileActionType.SELECT_SOURCE) {
        SettingsField("Source", tile.source) { onChange(tile.copy(source = it)) }
    }
    if (tile.actionType == TileActionType.HA_SERVICE) {
        SettingsField("HA domain", tile.service.domain) { onChange(tile.copy(service = tile.service.copy(domain = it))) }
        SettingsField("HA service", tile.service.service) { onChange(tile.copy(service = tile.service.copy(service = it))) }
        SettingsField("Target entity (blank = room player)", tile.service.targetEntity) { onChange(tile.copy(service = tile.service.copy(targetEntity = it))) }
        SettingsField("Service data JSON", tile.service.dataJson) { onChange(tile.copy(service = tile.service.copy(dataJson = it))) }
    }
}

@Composable
private fun TileActionSelector(value: TileActionType, onChange: (TileActionType) -> Unit) {
    var expanded by remember { mutableStateOf(false) }
    Box(Modifier.fillMaxWidth().padding(vertical = 4.dp)) {
        OutlinedButton(onClick = { expanded = true }, modifier = Modifier.fillMaxWidth()) { Text("Action: ${value.name.replace('_', ' ')}") }
        DropdownMenu(expanded = expanded, onDismissRequest = { expanded = false }) {
            TileActionType.entries.forEach { action -> DropdownMenuItem(text = { Text(action.name.replace('_', ' ')) }, onClick = { expanded = false; onChange(action) }) }
        }
    }
}

@Composable
private fun SettingsField(label: String, value: String, secret: Boolean = false, numeric: Boolean = false, onChange: (String) -> Unit) {
    OutlinedTextField(
        value = value,
        onValueChange = onChange,
        label = { Text(label) },
        singleLine = true,
        modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp),
        visualTransformation = if (secret) PasswordVisualTransformation() else androidx.compose.ui.text.input.VisualTransformation.None,
        keyboardOptions = if (numeric) KeyboardOptions(keyboardType = KeyboardType.Number) else KeyboardOptions.Default
    )
}

@Composable
private fun SettingSwitch(label: String, checked: Boolean, onChange: (Boolean) -> Unit) {
    Row(Modifier.fillMaxWidth().padding(vertical = 4.dp), verticalAlignment = Alignment.CenterVertically) {
        Text(label, Modifier.weight(1f))
        Switch(checked = checked, onCheckedChange = onChange)
    }
}

// ---------------- Images / helpers ----------------

@Composable
private fun AuthImage(url: String, authBase: String, token: String, modifier: Modifier = Modifier) {
    val context = LocalContext.current
    val model = remember(url, authBase, token) {
        val builder = ImageRequest.Builder(context).data(url)
        if (token.isNotBlank() && authBase.isNotBlank() && url.startsWith(authBase.trimEnd('/'))) {
            builder.httpHeaders(NetworkHeaders.Builder().set("Authorization", "Bearer $token").build())
        }
        builder.build()
    }
    AsyncImage(model = model, contentDescription = null, modifier = modifier, contentScale = ContentScale.Crop)
}

@Composable
private fun KodiImage(url: String, config: KodiConfig, modifier: Modifier = Modifier) {
    val context = LocalContext.current
    val model = remember(url, config.username, config.password) {
        val builder = ImageRequest.Builder(context).data(url)
        if (config.username.isNotBlank()) {
            builder.httpHeaders(NetworkHeaders.Builder().set("Authorization", Credentials.basic(config.username, config.password)).build())
        }
        builder.build()
    }
    AsyncImage(model = model, contentDescription = null, modifier = modifier, contentScale = ContentScale.Crop)
}

@Composable
private fun EmptyState(title: String, subtitle: String) {
    Box(Modifier.fillMaxSize().padding(28.dp), contentAlignment = Alignment.Center) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            Icon(Icons.Default.Speaker, null, tint = TextMuted, modifier = Modifier.size(42.dp))
            Spacer(Modifier.height(12.dp))
            Text(title, fontWeight = FontWeight.SemiBold, fontSize = 18.sp)
            Text(subtitle, color = TextMuted, modifier = Modifier.padding(top = 5.dp), lineHeight = 19.sp)
        }
    }
}

private fun formatDuration(seconds: Double): String {
    val s = seconds.roundToInt().coerceAtLeast(0)
    return "%d:%02d".format(s / 60, s % 60)
}