@file:OptIn(androidx.compose.foundation.ExperimentalFoundationApi::class)

package au.com.homemedia.ui

import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items as gridItems
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowLeft
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowRight
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import au.com.homemedia.core.AppController
import au.com.homemedia.core.Screen
import au.com.homemedia.model.*
import coil3.compose.AsyncImage
import coil3.network.NetworkHeaders
import coil3.network.httpHeaders
import coil3.request.ImageRequest
import okhttp3.Credentials
import kotlin.math.roundToInt

private val Bg = Color(0xFF090A0B)
private val Panel = Color(0xFF141619)
private val Panel2 = Color(0xFF1B1E22)
private val Panel3 = Color(0xFF25292E)
private val TextMuted = Color(0xFF9DA3AB)
private val Accent = Color(0xFFE9E9E9)
private val Danger = Color(0xFFFF6B6B)
private val ActiveGreen = Color(0xFF35C759)

private val HomeMediaDark = darkColorScheme(
    primary = Accent,
    onPrimary = Color.Black,
    secondary = Color(0xFFBEC4CC),
    background = Bg,
    onBackground = Color.White,
    surface = Panel,
    onSurface = Color.White,
    surfaceVariant = Panel2,
    onSurfaceVariant = Color(0xFFD7DADF),
    error = Danger
)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun HomeMediaApp(controller: AppController) {
    val screen by controller.screen.collectAsState()
    val settings by controller.settings.collectAsState()
    val roomId by controller.selectedRoomId.collectAsState()
    val busy by controller.busy.collectAsState()
    val message by controller.message.collectAsState()
    val room = settings.rooms.firstOrNull { it.id == roomId } ?: settings.rooms.firstOrNull()
    val pendingMass by controller.pendingMassPlayback.collectAsState()
    val pendingYoutube by controller.pendingYoutube.collectAsState()
    var drawerOpen by remember { mutableStateOf(false) }
    val context = LocalContext.current
    val settingsZipLauncher = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        uri?.let(controller::importSettingsZip)
    }
    val permissionLauncher = rememberLauncherForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { }
    LaunchedEffect(Unit) {
        val wanted = if (Build.VERSION.SDK_INT >= 31) {
            arrayOf(Manifest.permission.BLUETOOTH_SCAN, Manifest.permission.BLUETOOTH_CONNECT)
        } else {
            arrayOf(Manifest.permission.ACCESS_FINE_LOCATION)
        }
        val missing = wanted.filter { context.checkSelfPermission(it) != PackageManager.PERMISSION_GRANTED }
        if (missing.isNotEmpty()) permissionLauncher.launch(missing.toTypedArray())
    }

    MaterialTheme(colorScheme = HomeMediaDark) {
        Surface(Modifier.fillMaxSize(), color = Bg) {
            Box {
                Column(Modifier.fillMaxSize()) {
                    AppTopBar(
                        roomName = room?.name ?: "Home Media",
                        screen = screen,
                        onMenu = { drawerOpen = true },
                        onBack = {
                            when (screen) {
                                Screen.ROOM -> drawerOpen = true
                                Screen.MASS_DETAIL -> controller.backFromMassDetail()
                                Screen.MASS_LIST -> controller.backToMassHome()
                                Screen.MEDIA, Screen.MASS_HOME, Screen.QUEUE, Screen.KODI, Screen.YOUTUBE, Screen.SETTINGS -> controller.goRoom()
                                Screen.KODI_LIBRARY -> {
                                    val state = controller.kodiBrowse.value
                                    if (state.type == KodiBrowseType.HOME) controller.openKodi() else controller.kodiBrowseBack()
                                }
                            }
                        }
                    )
                    Box(Modifier.fillMaxSize()) {
                        when (screen) {
                            Screen.ROOM -> RoomScreen(controller, room)
                            Screen.MEDIA -> MediaHubScreen(controller)
                            Screen.MASS_HOME -> MassHomeScreen(controller)
                            Screen.MASS_LIST -> MassListScreen(controller)
                            Screen.MASS_DETAIL -> MassDetailScreen(controller)
                            Screen.QUEUE -> QueueScreen(controller)
                            Screen.KODI -> KodiRemoteScreen(controller, room)
                            Screen.KODI_LIBRARY -> KodiLibraryScreen(controller, room)
                            Screen.YOUTUBE -> YouTubeScreen(controller)
                            Screen.SETTINGS -> SettingsScreen(controller, settings) {
                                settingsZipLauncher.launch(arrayOf("application/zip", "application/octet-stream"))
                            }
                        }
                        if (busy) {
                            Box(
                                Modifier.fillMaxSize().background(Color.Black.copy(alpha = 0.30f)),
                                contentAlignment = Alignment.Center
                            ) { CircularProgressIndicator() }
                        }
                    }
                }

                if (drawerOpen) {
                    RoomDrawer(
                        settings = settings,
                        currentRoomId = roomId,
                        onClose = { drawerOpen = false },
                        onRoom = { controller.selectRoom(it); drawerOpen = false },
                        onMedia = { controller.goMedia(); drawerOpen = false },
                        onLibrary = { controller.openLibrary(); drawerOpen = false },
                        onQueue = { controller.openQueue(); drawerOpen = false },
                        onKodi = { controller.openKodi(); drawerOpen = false },
                        onSettings = { controller.goSettings(); drawerOpen = false }
                    )
                }
            }

            message?.let { text ->
                AlertDialog(
                    onDismissRequest = controller::clearMessage,
                    confirmButton = { TextButton(onClick = controller::clearMessage) { Text("OK") } },
                    title = { Text("Home Media") },
                    text = { Text(text) }
                )
            }

            pendingMass?.let { pending ->
                PlaybackTargetDialog(
                    title = "Play ${pending.first.name}",
                    targets = controller.playbackTargets(),
                    onDismiss = controller::cancelPendingPlayback,
                    onTarget = controller::confirmMassPlayback
                )
            }
            pendingYoutube?.let { item ->
                PlaybackTargetDialog(
                    title = "Play ${item.title}",
                    targets = controller.playbackTargets(includeKodi = true),
                    onDismiss = controller::cancelPendingPlayback,
                    onTarget = controller::confirmYouTubePlayback
                )
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun AppTopBar(roomName: String, screen: Screen, onMenu: () -> Unit, onBack: () -> Unit) {
    TopAppBar(
        title = {
            Column {
                Text(roomName, fontWeight = FontWeight.SemiBold)
                if (screen != Screen.ROOM) Text(screenTitle(screen), color = TextMuted, fontSize = 12.sp)
            }
        },
        navigationIcon = {
            IconButton(onClick = if (screen == Screen.ROOM) onMenu else onBack) {
                Icon(if (screen == Screen.ROOM) Icons.Default.Menu else Icons.AutoMirrored.Filled.ArrowBack, null)
            }
        },
        colors = TopAppBarDefaults.topAppBarColors(containerColor = Bg)
    )
}

private fun screenTitle(screen: Screen) = when (screen) {
    Screen.MEDIA -> "Media"
    Screen.MASS_HOME, Screen.MASS_LIST, Screen.MASS_DETAIL -> "Music Assistant"
    Screen.QUEUE -> "Queue"
    Screen.KODI -> "Kodi remote"
    Screen.KODI_LIBRARY -> "Kodi library"
    Screen.YOUTUBE -> "YouTube"
    Screen.SETTINGS -> "Settings"
    else -> ""
}

@Composable
private fun RoomDrawer(
    settings: AppSettings,
    currentRoomId: String,
    onClose: () -> Unit,
    onRoom: (String) -> Unit,
    onMedia: () -> Unit,
    onLibrary: () -> Unit,
    onQueue: () -> Unit,
    onKodi: () -> Unit,
    onSettings: () -> Unit
) {
    Box(Modifier.fillMaxSize().background(Color.Black.copy(alpha = .55f)).combinedClickable(onClick = onClose, onLongClick = {})) {
        Surface(Modifier.fillMaxHeight().fillMaxWidth(.78f), color = Panel, tonalElevation = 8.dp) {
            LazyColumn(contentPadding = PaddingValues(14.dp)) {
                item {
                    Text("ROOMS", color = TextMuted, fontSize = 12.sp, modifier = Modifier.padding(12.dp, 12.dp, 12.dp, 8.dp))
                }
                items(settings.rooms, key = { it.id }) { room ->
                    NavigationDrawerItem(
                        label = { Text(room.name) },
                        selected = room.id == currentRoomId,
                        icon = { Icon(Icons.Default.Speaker, null) },
                        onClick = { onRoom(room.id) }
                    )
                }
                item { HorizontalDivider(Modifier.padding(vertical = 10.dp)) }
                item { DrawerAction("Media", Icons.Default.PermMedia, onMedia) }
                item { DrawerAction("Music library", Icons.Default.LibraryMusic, onLibrary) }
                item { DrawerAction("Queue", Icons.Default.QueueMusic, onQueue) }
                item { DrawerAction("Kodi", Icons.Default.Tv, onKodi) }
                item { DrawerAction("Settings", Icons.Default.Settings, onSettings) }
            }
        }
    }
}

@Composable
private fun DrawerAction(label: String, icon: androidx.compose.ui.graphics.vector.ImageVector, onClick: () -> Unit) {
    NavigationDrawerItem(label = { Text(label) }, selected = false, icon = { Icon(icon, null) }, onClick = onClick)
}

@Composable
private fun RoomScreen(controller: AppController, room: RoomConfig?) {
    val now by controller.nowPlaying.collectAsState()
    val joinSourceId by controller.joinSourceRoomId.collectAsState()
    val activePlayerKey by controller.activePlayerKey.collectAsState()
    val settings by controller.settings.collectAsState()
    val joinRoom = joinSourceId?.let { id -> settings.rooms.firstOrNull { it.id == id } }
    if (room == null) { EmptyState("No room configured", "Add a room in Settings."); return }

    Column(Modifier.fillMaxSize().padding(horizontal = 16.dp, vertical = 6.dp)) {
        val activeName = if (activePlayerKey == "primary") room.name
        else room.secondaryPlayers.firstOrNull { it.id == activePlayerKey }?.name ?: room.name
        NowPlayingCard(controller, now, settings, activeName)
        Spacer(Modifier.height(16.dp))

        val tiles = remember(room.tiles, joinSourceId) {
            val base = room.tiles.take(4).toMutableList()
            if (joinSourceId != null) {
                while (base.size < 4) base += TileConfig(title = "Tile ${base.size + 1}")
                base[3] = TileConfig(id = "__JOIN__", title = "Join", icon = "join")
            }
            base
        }
        LazyVerticalGrid(
            columns = GridCells.Fixed(2),
            horizontalArrangement = Arrangement.spacedBy(12.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
            modifier = Modifier.weight(1f)
        ) {
            gridItems(room.secondaryPlayers, key = { "secondary:${it.id}" }) { secondary ->
                SecondaryPlayerTile(
                    secondary = secondary,
                    active = activePlayerKey == secondary.id,
                    modifier = Modifier.fillMaxWidth().aspectRatio(1.12f),
                    onToggle = { controller.toggleSecondaryPlayer(secondary.id) },
                    onPause = { controller.secondaryPause(secondary.id) },
                    onStop = { controller.secondaryStop(secondary.id) },
                    onAuxToggle = { controller.toggleSecondaryAux(secondary.id) }
                )
            }
            gridItems(tiles, key = { it.id }) { tile ->
                if (tile.id == "__JOIN__" && joinRoom != null) {
                    JoinTile(
                        source = joinRoom,
                        modifier = Modifier.fillMaxWidth().aspectRatio(1.12f),
                        onJoin = controller::joinActiveRoom,
                        onTransfer = { controller.transferFromRoom(joinRoom.id) },
                        onPause = { controller.pauseRoom(joinRoom.id) },
                        onOff = { controller.turnOffRoom(joinRoom.id) }
                    )
                } else {
                    RoomTile(tile, Modifier.fillMaxWidth().aspectRatio(1.12f)) { controller.executeTile(tile) }
                }
            }
        }
    }
}

@Composable
private fun NowPlayingCard(controller: AppController, now: NowPlaying, settings: AppSettings, activeName: String) {
    Box {
        Card(colors = CardDefaults.cardColors(containerColor = Panel), shape = RoundedCornerShape(24.dp)) {
            Row(Modifier.fillMaxWidth().padding(14.dp), verticalAlignment = Alignment.CenterVertically) {
            Surface(Modifier.size(88.dp), color = Panel2, shape = RoundedCornerShape(16.dp)) {
                if (now.imageUrl.isNotBlank()) AuthImage(now.imageUrl, settings.homeAssistantUrl, settings.homeAssistantToken, Modifier.fillMaxSize())
                else Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) { Icon(Icons.Default.MusicNote, null, tint = TextMuted, modifier = Modifier.size(36.dp)) }
            }
            Spacer(Modifier.width(14.dp))
            Column(Modifier.weight(1f)) {
                Text(now.title, fontWeight = FontWeight.Bold, fontSize = 18.sp, maxLines = 1, overflow = TextOverflow.Ellipsis)
                if (now.artist.isNotBlank()) Text(now.artist, color = TextMuted, maxLines = 1, overflow = TextOverflow.Ellipsis)
                val secondary = listOf(now.album, now.source).filter { it.isNotBlank() }.joinToString(" • ")
                if (secondary.isNotBlank()) Text(secondary, color = TextMuted, fontSize = 12.sp, maxLines = 1, overflow = TextOverflow.Ellipsis)
                Spacer(Modifier.height(8.dp))
                Row(verticalAlignment = Alignment.CenterVertically) {
                    IconButton(onClick = controller::previous) { Icon(Icons.Default.SkipPrevious, "Previous") }
                    FilledIconButton(onClick = controller::togglePlayPause) { Icon(if (now.isPlaying) Icons.Default.Pause else Icons.Default.PlayArrow, "Play/Pause") }
                    IconButton(onClick = controller::next) { Icon(Icons.Default.SkipNext, "Next") }
                    Spacer(Modifier.weight(1f))
                    IconButton(onClick = controller::volumeDown) { Icon(Icons.Default.VolumeDown, "Volume down") }
                    IconButton(onClick = controller::volumeUp) { Icon(Icons.Default.VolumeUp, "Volume up") }
                }
            }
        }
        Surface(
            modifier = Modifier.align(Alignment.TopEnd).padding(10.dp).size(26.dp),
            shape = RoundedCornerShape(13.dp),
            color = ActiveGreen
        ) {
            Box(contentAlignment = Alignment.Center) {
                Icon(Icons.Default.Speaker, activeName, tint = Color.Black, modifier = Modifier.size(16.dp))
            }
        }
    }
}

@Composable
private fun RoomTile(tile: TileConfig, modifier: Modifier, onClick: () -> Unit) {
    Card(
        modifier = modifier.combinedClickable(onClick = onClick, onLongClick = {}),
        colors = CardDefaults.cardColors(containerColor = Panel2),
        shape = RoundedCornerShape(22.dp)
    ) {
        Column(Modifier.fillMaxSize().padding(18.dp), verticalArrangement = Arrangement.SpaceBetween) {
            Icon(tileIcon(tile.icon), null, modifier = Modifier.size(30.dp))
            Text(tile.title, fontSize = 17.sp, fontWeight = FontWeight.SemiBold)
        }
    }
}


@Composable
private fun SecondaryPlayerTile(
    secondary: SecondaryPlayerConfig,
    active: Boolean,
    modifier: Modifier,
    onToggle: () -> Unit,
    onPause: () -> Unit,
    onStop: () -> Unit,
    onAuxToggle: () -> Unit
) {
    var menu by remember { mutableStateOf(false) }
    Box {
        Card(
            modifier = modifier.combinedClickable(
                onClick = onToggle,
                onDoubleClick = { menu = true },
                onLongClick = { menu = true }
            ),
            colors = CardDefaults.cardColors(containerColor = if (active) Color(0xFF153822) else Panel2),
            shape = RoundedCornerShape(22.dp)
        ) {
            Column(Modifier.fillMaxSize().padding(18.dp), verticalArrangement = Arrangement.SpaceBetween) {
                Icon(Icons.Default.Speaker, null, tint = if (active) ActiveGreen else LocalContentColor.current, modifier = Modifier.size(31.dp))
                Column {
                    Text(secondary.name, fontSize = 17.sp, fontWeight = FontWeight.SemiBold)
                    Text(if (active) "Active player" else "Secondary player", color = if (active) ActiveGreen else TextMuted, fontSize = 12.sp)
                }
            }
        }
        DropdownMenu(expanded = menu, onDismissRequest = { menu = false }) {
            DropdownMenuItem(
                text = { Text(if (active) "Transfer queue back to main player" else "Transfer active queue here") },
                leadingIcon = { Icon(Icons.Default.SwapHoriz, null) },
                onClick = { menu = false; onToggle() }
            )
            DropdownMenuItem(
                text = { Text("Pause ${secondary.name}") },
                leadingIcon = { Icon(Icons.Default.Pause, null) },
                onClick = { menu = false; onPause() }
            )
            DropdownMenuItem(
                text = { Text("Stop ${secondary.name}") },
                leadingIcon = { Icon(Icons.Default.Stop, null) },
                onClick = { menu = false; onStop() }
            )
            if (secondary.toggleEntity.isNotBlank()) {
                DropdownMenuItem(
                    text = { Text("Toggle BC9500") },
                    leadingIcon = { Icon(Icons.Default.PowerSettingsNew, null) },
                    onClick = { menu = false; onAuxToggle() }
                )
            }
        }
    }
}

@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun JoinTile(
    source: RoomConfig,
    modifier: Modifier,
    onJoin: () -> Unit,
    onTransfer: () -> Unit,
    onPause: () -> Unit,
    onOff: () -> Unit
) {
    var menu by remember { mutableStateOf(false) }
    Box {
        Card(
            modifier = modifier.combinedClickable(onClick = onJoin, onDoubleClick = { menu = true }, onLongClick = { menu = true }),
            colors = CardDefaults.cardColors(containerColor = Panel3),
            shape = RoundedCornerShape(22.dp)
        ) {
            Column(Modifier.fillMaxSize().padding(18.dp), verticalArrangement = Arrangement.SpaceBetween) {
                Icon(Icons.Default.SpeakerGroup, null, modifier = Modifier.size(32.dp))
                Column {
                    Text("Join", fontSize = 18.sp, fontWeight = FontWeight.Bold)
                    Text(source.name, color = TextMuted, maxLines = 1, overflow = TextOverflow.Ellipsis)
                }
            }
        }
        DropdownMenu(expanded = menu, onDismissRequest = { menu = false }) {
            DropdownMenuItem(
                text = { Text("Transfer queue from ${source.name}") },
                leadingIcon = { Icon(Icons.Default.SwapHoriz, null) },
                onClick = { menu = false; onTransfer() }
            )
            DropdownMenuItem(
                text = { Text("Pause ${source.name}") },
                leadingIcon = { Icon(Icons.Default.Pause, null) },
                onClick = { menu = false; onPause() }
            )
            DropdownMenuItem(
                text = { Text("Turn off ${source.name}") },
                leadingIcon = { Icon(Icons.Default.PowerSettingsNew, null) },
                onClick = { menu = false; onOff() }
            )
        }
    }
}

private fun tileIcon(name: String) = when (name.lowercase()) {
    "disc", "cd" -> Icons.Default.Album
    "news" -> Icons.Default.Newspaper
    "library" -> Icons.Default.LibraryMusic
    "queue" -> Icons.Default.QueueMusic
    "tv", "kodi" -> Icons.Default.Tv
    "radio" -> Icons.Default.Radio
    "join" -> Icons.Default.SpeakerGroup
    else -> Icons.Default.MusicNote
}

// ---------------- Unified media hub ----------------

@Composable
private fun MediaHubScreen(controller: AppController) {
    val settings by controller.settings.collectAsState()
    LazyVerticalGrid(
        columns = GridCells.Adaptive(150.dp),
        contentPadding = PaddingValues(16.dp),
        horizontalArrangement = Arrangement.spacedBy(12.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp)
    ) {
        item {
            Card(
                Modifier.fillMaxWidth().aspectRatio(1.15f).combinedClickable(onClick = controller::openLibrary, onLongClick = {}),
                colors = CardDefaults.cardColors(containerColor = Panel2)
            ) {
                Column(Modifier.fillMaxSize().padding(16.dp), verticalArrangement = Arrangement.SpaceBetween) {
                    Icon(Icons.Default.LibraryMusic, null, modifier = Modifier.size(32.dp))
                    Column {
                        Text("Music", fontWeight = FontWeight.Bold, fontSize = 18.sp)
                        Text("Music Assistant", color = TextMuted, fontSize = 12.sp)
                    }
                }
            }
        }
        item {
            Card(
                Modifier.fillMaxWidth().aspectRatio(1.15f).combinedClickable(onClick = controller::openYouTube, onLongClick = {}),
                colors = CardDefaults.cardColors(containerColor = Panel2)
            ) {
                Column(Modifier.fillMaxSize().padding(16.dp), verticalArrangement = Arrangement.SpaceBetween) {
                    Icon(Icons.Default.SmartDisplay, null, modifier = Modifier.size(32.dp))
                    Column {
                        Text("YouTube", fontWeight = FontWeight.Bold, fontSize = 18.sp)
                        Text("Search and play", color = TextMuted, fontSize = 12.sp)
                    }
                }
            }
        }
        gridItems(settings.rooms.filter { it.kodi.baseUrl.isNotBlank() }, key = { "kodi:${it.id}" }) { room ->
            Card(
                Modifier.fillMaxWidth().aspectRatio(1.15f).combinedClickable(
                    onClick = { controller.selectRoom(room.id); controller.openKodiLibrary() },
                    onLongClick = {}
                ),
                colors = CardDefaults.cardColors(containerColor = Panel2)
            ) {
                Column(Modifier.fillMaxSize().padding(16.dp), verticalArrangement = Arrangement.SpaceBetween) {
                    Icon(Icons.Default.VideoLibrary, null, modifier = Modifier.size(32.dp))
                    Column {
                        Text(room.name, fontWeight = FontWeight.Bold, fontSize = 18.sp)
                        Text("Video library", color = TextMuted, fontSize = 12.sp)
                    }
                }
            }
        }
    }
}

@Composable
private fun YouTubeScreen(controller: AppController) {
    val results by controller.youtubeResults.collectAsState()
    var query by remember { mutableStateOf("") }
    Column(Modifier.fillMaxSize().padding(horizontal = 14.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            OutlinedTextField(
                value = query,
                onValueChange = { query = it },
                placeholder = { Text("Search YouTube") },
                singleLine = true,
                modifier = Modifier.weight(1f)
            )
            Spacer(Modifier.width(8.dp))
            FilledIconButton(onClick = { controller.searchYouTube(query) }) { Icon(Icons.Default.Search, "Search") }
        }
        Spacer(Modifier.height(10.dp))
        if (results.isEmpty()) {
            EmptyState("YouTube", "Search for a video. Playback asks which room/player to use.")
        } else {
            LazyColumn(contentPadding = PaddingValues(bottom = 28.dp)) {
                items(results, key = { it.videoId }) { item ->
                    Row(
                        Modifier.fillMaxWidth().combinedClickable(
                            onClick = { controller.requestYouTubePlayback(item) },
                            onLongClick = { controller.requestYouTubePlayback(item) }
                        ).padding(vertical = 8.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Surface(Modifier.size(84.dp, 48.dp), color = Panel2, shape = RoundedCornerShape(9.dp)) {
                            if (item.thumbnail.isNotBlank()) AsyncImage(
                                model = item.thumbnail,
                                contentDescription = null,
                                modifier = Modifier.fillMaxSize(),
                                contentScale = ContentScale.Crop
                            )
                            else Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) { Icon(Icons.Default.SmartDisplay, null) }
                        }
                        Spacer(Modifier.width(12.dp))
                        Column(Modifier.weight(1f)) {
                            Text(item.title, maxLines = 2, overflow = TextOverflow.Ellipsis, fontWeight = FontWeight.Medium)
                            if (item.channel.isNotBlank()) Text(item.channel, color = TextMuted, fontSize = 12.sp)
                        }
                        Icon(Icons.Default.PlayArrow, null)
                    }
                }
            }
        }
    }
}

@Composable
private fun PlaybackTargetDialog(
    title: String,
    targets: List<PlaybackTarget>,
    onDismiss: () -> Unit,
    onTarget: (String) -> Unit
) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(title, maxLines = 2, overflow = TextOverflow.Ellipsis) },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text("Choose playback device", color = TextMuted, fontSize = 13.sp)
                targets.forEach { target ->
                    TextButton(
                        onClick = { onTarget(target.id) },
                        modifier = Modifier.fillMaxWidth()
                    ) {
                        Icon(if (target.kodi) Icons.Default.Tv else Icons.Default.Speaker, null)
                        Spacer(Modifier.width(8.dp))
                        Text(target.label, modifier = Modifier.weight(1f))
                    }
                }
            }
        },
        confirmButton = {},
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } }
    )
}

// ---------------- Music Assistant ----------------

@Composable
private fun MassHomeScreen(controller: AppController) {
    LazyVerticalGrid(
        columns = GridCells.Adaptive(145.dp),
        contentPadding = PaddingValues(16.dp),
        horizontalArrangement = Arrangement.spacedBy(12.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp)
    ) {
        gridItems(MassCategory.entries, key = { it.name }) { category ->
            Card(
                modifier = Modifier.fillMaxWidth().aspectRatio(1.25f).combinedClickable(onClick = { controller.openMassCategory(category) }, onLongClick = {}),
                colors = CardDefaults.cardColors(containerColor = Panel2)
            ) {
                Column(Modifier.fillMaxSize().padding(16.dp), verticalArrangement = Arrangement.SpaceBetween) {
                    Icon(massIcon(category), null, modifier = Modifier.size(30.dp))
                    Text(category.label, fontWeight = FontWeight.SemiBold, fontSize = 16.sp)
                }
            }
        }
    }
}

private fun massIcon(category: MassCategory) = when (category) {
    MassCategory.ARTISTS -> Icons.Default.Person
    MassCategory.ALBUMS -> Icons.Default.Album
    MassCategory.TRACKS -> Icons.Default.MusicNote
    MassCategory.PLAYLISTS -> Icons.Default.QueueMusic
    MassCategory.RADIOS -> Icons.Default.Radio
    MassCategory.PODCASTS -> Icons.Default.Podcasts
    MassCategory.AUDIOBOOKS -> Icons.Default.MenuBook
    MassCategory.GENRES -> Icons.Default.Category
}

@Composable
private fun MassListScreen(controller: AppController) {
    val items by controller.massItems.collectAsState()
    val category by controller.massCategory.collectAsState()
    val settings by controller.settings.collectAsState()
    var search by remember(category) { mutableStateOf("") }
    Column(Modifier.fillMaxSize().padding(horizontal = 14.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            OutlinedTextField(
                value = search,
                onValueChange = { search = it },
                singleLine = true,
                placeholder = { Text("Search ${category?.label?.lowercase() ?: "library"}") },
                modifier = Modifier.weight(1f)
            )
            Spacer(Modifier.width(8.dp))
            FilledIconButton(onClick = { controller.searchMass(search) }) { Icon(Icons.Default.Search, "Search") }
        }
        Spacer(Modifier.height(10.dp))
        if (items.isEmpty()) EmptyState("Nothing found", "Check Music Assistant or change the search.")
        else LazyColumn(contentPadding = PaddingValues(bottom = 30.dp)) {
            items(items, key = { it.uri.ifBlank { "${it.mediaType}:${it.itemId}:${it.provider}" } }) { item ->
                val browsable = item.mediaType.lowercase() in setOf("artist", "album", "playlist", "podcast", "genre")
                MassItemRow(
                    item, settings,
                    onOpen = { if (browsable) controller.openMassItem(item) else controller.playMassItem(item, "play") },
                    onPlay = { controller.playMassItem(item, "replace") },
                    onQueueOption = { option -> controller.playMassItem(item, option) }
                )
            }
        }
    }
}

@Composable
private fun MassItemRow(
    item: MassMediaItem,
    settings: AppSettings,
    onOpen: () -> Unit,
    onPlay: () -> Unit,
    onQueueOption: (String) -> Unit
) {
    var queueMenu by remember { mutableStateOf(false) }
    Row(
        Modifier.fillMaxWidth().combinedClickable(onClick = onOpen, onLongClick = onPlay).padding(vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Surface(Modifier.size(58.dp), shape = RoundedCornerShape(10.dp), color = Panel2) {
            if (item.imageUrl.isNotBlank()) AuthImage(item.imageUrl, settings.musicAssistantUrl, settings.musicAssistantToken, Modifier.fillMaxSize())
            else Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) { Icon(Icons.Default.MusicNote, null, tint = TextMuted) }
        }
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f)) {
            Text(item.name, maxLines = 1, overflow = TextOverflow.Ellipsis, fontWeight = FontWeight.Medium)
            if (item.subtitle.isNotBlank()) Text(item.subtitle, maxLines = 1, overflow = TextOverflow.Ellipsis, color = TextMuted, fontSize = 13.sp)
        }
        if (item.playable) {
            IconButton(onClick = onPlay) { Icon(Icons.Default.PlayArrow, "Replace queue and play") }
            Box {
                IconButton(onClick = { queueMenu = true }) { Icon(Icons.Default.MoreVert, "Queue options") }
                DropdownMenu(expanded = queueMenu, onDismissRequest = { queueMenu = false }) {
                    listOf(
                        "play" to "Play now",
                        "replace" to "Replace queue",
                        "next" to "Play next",
                        "replace_next" to "Replace upcoming",
                        "add" to "Add to end"
                    ).forEach { (option, label) ->
                        DropdownMenuItem(text = { Text(label) }, onClick = { queueMenu = false; onQueueOption(option) })
                    }
                }
            }
        }
        if (!item.playable || item.mediaType.lowercase() in setOf("artist", "album", "playlist", "podcast", "genre")) {
            Icon(Icons.Default.ChevronRight, null, tint = TextMuted)
        }
    }
}

@Composable
private fun MassDetailScreen(controller: AppController) {
    val item by controller.selectedMassItem.collectAsState()
    val children by controller.massChildren.collectAsState()
    val settings by controller.settings.collectAsState()
    val current = item ?: run { EmptyState("Item unavailable", "Return to the library."); return }
    LazyColumn(contentPadding = PaddingValues(bottom = 30.dp)) {
        item {
            Row(Modifier.padding(18.dp), verticalAlignment = Alignment.Bottom) {
                Surface(Modifier.size(145.dp), shape = RoundedCornerShape(18.dp), color = Panel2) {
                    if (current.imageUrl.isNotBlank()) AuthImage(current.imageUrl, settings.musicAssistantUrl, settings.musicAssistantToken, Modifier.fillMaxSize())
                    else Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) { Icon(Icons.Default.Album, null, modifier = Modifier.size(48.dp)) }
                }
                Spacer(Modifier.width(18.dp))
                Column(Modifier.weight(1f)) {
                    Text(current.name, fontWeight = FontWeight.Bold, fontSize = 24.sp, lineHeight = 28.sp)
                    if (current.subtitle.isNotBlank()) Text(current.subtitle, color = TextMuted, modifier = Modifier.padding(top = 4.dp))
                    if (current.playable) {
                        Spacer(Modifier.height(14.dp))
                        Button(onClick = { controller.playMassItem(current) }) { Icon(Icons.Default.PlayArrow, null); Spacer(Modifier.width(6.dp)); Text("Play") }
                    }
                }
            }
            HorizontalDivider()
        }
        items(children, key = { it.uri.ifBlank { "${it.mediaType}:${it.itemId}:${it.provider}" } }) { child ->
            val browsable = child.mediaType.lowercase() in setOf("artist", "album", "playlist", "podcast", "genre")
            MassItemRow(
                child, settings,
                onOpen = { if (browsable) controller.openMassItem(child) else controller.playMassItem(child, "play") },
                onPlay = { controller.playMassItem(child, "replace") },
                onQueueOption = { option -> controller.playMassItem(child, option) }
            )
        }
    }
}

// ---------------- MASS queue ----------------

@Composable
private fun QueueScreen(controller: AppController) {
    val info by controller.queueInfo.collectAsState()
    val items by controller.queueItems.collectAsState()
    var saveDialog by remember { mutableStateOf(false) }
    var overlayDialog by remember { mutableStateOf(false) }
    val q = info

    Column(Modifier.fillMaxSize()) {
        if (q != null) {
            QueueControlPanel(
                q = q,
                onRefresh = controller::refreshQueue,
                onPrevious = controller::queuePrevious,
                onPlayPause = controller::queuePlayPause,
                onNext = controller::queueNext,
                onStop = controller::queueStop,
                onSkip = controller::queueSkip,
                onSeek = controller::queueSeek,
                onShuffle = controller::queueShuffle,
                onRepeat = controller::queueRepeat,
                onCrossfade = controller::queueCrossfade,
                onAutoplay = controller::queueAutoplay,
                onClear = controller::queueClear,
                onSave = { saveDialog = true },
                onOverlay = { overlayDialog = true }
            )
        }
        if (items.isEmpty()) EmptyState("Queue is empty", "Choose something from Music Assistant.")
        else LazyColumn(Modifier.weight(1f), contentPadding = PaddingValues(horizontal = 12.dp, vertical = 8.dp)) {
            items(items, key = { it.queueItemId }) { item -> QueueItemRow(controller, item, item.index == q?.currentIndex) }
        }
    }

    if (saveDialog) TextInputDialog("Save queue as playlist", "Playlist name", onDismiss = { saveDialog = false }) { name ->
        saveDialog = false
        if (name.isNotBlank()) controller.queueSaveAsPlaylist(name)
    }
    if (overlayDialog) OverlayDialog(onDismiss = { overlayDialog = false }) { enabled, source, volume ->
        overlayDialog = false
        controller.queueOverlay(enabled, source, volume)
    }
}

@Composable
private fun QueueControlPanel(
    q: MassQueueInfo,
    onRefresh: () -> Unit,
    onPrevious: () -> Unit,
    onPlayPause: () -> Unit,
    onNext: () -> Unit,
    onStop: () -> Unit,
    onSkip: (Int) -> Unit,
    onSeek: (Double) -> Unit,
    onShuffle: (Boolean) -> Unit,
    onRepeat: (String) -> Unit,
    onCrossfade: (Boolean) -> Unit,
    onAutoplay: (Boolean) -> Unit,
    onClear: () -> Unit,
    onSave: () -> Unit,
    onOverlay: () -> Unit
) {
    var seek by remember(q.queueId, q.elapsedTime) { mutableFloatStateOf(q.elapsedTime.toFloat()) }
    Card(Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 6.dp), colors = CardDefaults.cardColors(containerColor = Panel)) {
        Column(Modifier.padding(14.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text(q.displayName.ifBlank { q.queueId }, fontWeight = FontWeight.Bold)
                    Text(q.state, color = TextMuted, fontSize = 12.sp)
                }
                IconButton(onClick = onRefresh) { Icon(Icons.Default.Refresh, "Refresh") }
            }
            if (q.duration > 0) {
                Slider(
                    value = seek.coerceIn(0f, q.duration.toFloat()),
                    onValueChange = { seek = it },
                    onValueChangeFinished = { onSeek(seek.toDouble()) },
                    valueRange = 0f..q.duration.toFloat()
                )
                Row(Modifier.fillMaxWidth()) {
                    Text(formatDuration(seek.toDouble()), color = TextMuted, fontSize = 12.sp)
                    Spacer(Modifier.weight(1f))
                    Text(formatDuration(q.duration), color = TextMuted, fontSize = 12.sp)
                }
            }
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceAround, verticalAlignment = Alignment.CenterVertically) {
                IconButton(onClick = onPrevious) { Icon(Icons.Default.SkipPrevious, "Previous") }
                IconButton(onClick = { onSkip(-10) }) { Icon(Icons.Default.Replay10, "Back 10") }
                FilledIconButton(onClick = onPlayPause) { Icon(Icons.Default.PlayArrow, "Play/Pause") }
                IconButton(onClick = { onSkip(10) }) { Icon(Icons.Default.Forward10, "Forward 10") }
                IconButton(onClick = onNext) { Icon(Icons.Default.SkipNext, "Next") }
                IconButton(onClick = onStop) { Icon(Icons.Default.Stop, "Stop") }
            }
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceAround) {
                IconToggleButton(checked = q.shuffleEnabled, onCheckedChange = onShuffle) { Icon(Icons.Default.Shuffle, "Shuffle") }
                IconButton(onClick = { onRepeat(nextRepeat(q.repeatMode)) }) { Icon(repeatIcon(q.repeatMode), "Repeat") }
                IconToggleButton(checked = q.crossfadeEnabled, onCheckedChange = onCrossfade) { Icon(Icons.Default.GraphicEq, "Crossfade") }
                IconToggleButton(checked = q.autoplayEnabled, onCheckedChange = onAutoplay) { Icon(Icons.Default.AutoAwesome, "Autoplay") }
            }
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                AssistChip(onClick = onSave, label = { Text("Save") }, leadingIcon = { Icon(Icons.Default.PlaylistAdd, null) })
                AssistChip(onClick = onOverlay, label = { Text("Overlay") }, leadingIcon = { Icon(Icons.Default.Layers, null) })
                AssistChip(onClick = onClear, label = { Text("Clear") }, leadingIcon = { Icon(Icons.Default.DeleteSweep, null) })
            }
        }
    }
}

private fun nextRepeat(current: String): String = when (current.lowercase()) { "off" -> "all"; "all" -> "one"; else -> "off" }
private fun repeatIcon(mode: String) = if (mode.equals("one", true)) Icons.Default.RepeatOne else Icons.Default.Repeat

@Composable
private fun QueueItemRow(controller: AppController, item: MassQueueItem, current: Boolean) {
    var speedMenu by remember { mutableStateOf(false) }
    Card(
        Modifier.fillMaxWidth().padding(vertical = 4.dp),
        colors = CardDefaults.cardColors(containerColor = if (current) Panel3 else Panel2)
    ) {
        Row(Modifier.fillMaxWidth().padding(8.dp), verticalAlignment = Alignment.CenterVertically) {
            FilledTonalIconButton(onClick = { controller.queuePlay(item) }) { Icon(Icons.Default.PlayArrow, "Play") }
            Spacer(Modifier.width(8.dp))
            Column(Modifier.weight(1f)) {
                Text(item.name, maxLines = 1, overflow = TextOverflow.Ellipsis, fontWeight = if (current) FontWeight.Bold else FontWeight.Normal)
                if (item.subtitle.isNotBlank()) Text(item.subtitle, color = TextMuted, fontSize = 12.sp, maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
            Box {
                IconButton(onClick = { speedMenu = true }) { Icon(Icons.Default.Speed, "Playback speed") }
                DropdownMenu(expanded = speedMenu, onDismissRequest = { speedMenu = false }) {
                    listOf(.75, 1.0, 1.25, 1.5, 2.0).forEach { speed ->
                        DropdownMenuItem(text = { Text("${speed}×") }, onClick = { speedMenu = false; controller.queuePlaybackSpeed(item, speed) })
                    }
                }
            }
            IconButton(onClick = { controller.queueMoveUp(item) }) { Icon(Icons.Default.KeyboardArrowUp, "Move up") }
            IconButton(onClick = { controller.queueMoveDown(item) }) { Icon(Icons.Default.KeyboardArrowDown, "Move down") }
            IconButton(onClick = { controller.queueMoveNext(item) }) { Icon(Icons.Default.VerticalAlignTop, "Move next") }
            IconButton(onClick = { controller.queueMoveEnd(item) }) { Icon(Icons.Default.VerticalAlignBottom, "Move to end") }
            IconButton(onClick = { controller.queueDelete(item) }) { Icon(Icons.Default.Close, "Remove") }
        }
    }
}

@Composable
private fun TextInputDialog(title: String, label: String, onDismiss: () -> Unit, onSubmit: (String) -> Unit) {
    var value by remember { mutableStateOf("") }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(title) },
        text = { OutlinedTextField(value = value, onValueChange = { value = it }, label = { Text(label) }, singleLine = true) },
        confirmButton = { TextButton(onClick = { onSubmit(value) }) { Text("Save") } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } }
    )
}

@Composable
private fun OverlayDialog(onDismiss: () -> Unit, onSubmit: (Boolean, String, Int?) -> Unit) {
    var enabled by remember { mutableStateOf(true) }
    var source by remember { mutableStateOf("") }
    var volume by remember { mutableStateOf("") }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Queue overlay") },
        text = {
            Column {
                SettingSwitch("Enable overlay", enabled) { enabled = it }
                OutlinedTextField(source, { source = it }, label = { Text("Overlay URI/source") })
                OutlinedTextField(volume, { volume = it.filter(Char::isDigit) }, label = { Text("Volume (optional)") }, keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number))
            }
        },
        confirmButton = { TextButton(onClick = { onSubmit(enabled, source, volume.toIntOrNull()) }) { Text("Apply") } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } }
    )
}

// ---------------- Kodi ----------------

@Composable
private fun KodiRemoteScreen(controller: AppController, room: RoomConfig?) {
    val now by controller.kodiNow.collectAsState()
    Column(Modifier.fillMaxSize().padding(18.dp), horizontalAlignment = Alignment.CenterHorizontally) {
        Card(colors = CardDefaults.cardColors(containerColor = Panel), shape = RoundedCornerShape(22.dp), modifier = Modifier.fillMaxWidth()) {
            Column(Modifier.padding(18.dp)) {
                Text(now.title, fontSize = 20.sp, fontWeight = FontWeight.SemiBold, maxLines = 1, overflow = TextOverflow.Ellipsis)
                if (now.subtitle.isNotBlank()) Text(now.subtitle, color = TextMuted, maxLines = 1, overflow = TextOverflow.Ellipsis)
                Spacer(Modifier.height(10.dp))
                Row {
                    IconButton(onClick = { controller.kodiPlayer("Player.Stop") }) { Icon(Icons.Default.Stop, "Stop") }
                    FilledIconButton(onClick = { controller.kodiPlayer("Player.PlayPause") }) { Icon(if (now.playing) Icons.Default.Pause else Icons.Default.PlayArrow, "Play/Pause") }
                    Spacer(Modifier.weight(1f))
                    IconButton(onClick = controller::refreshKodi) { Icon(Icons.Default.Refresh, "Refresh") }
                    FilledTonalButton(onClick = controller::openKodiLibrary) { Icon(Icons.Default.VideoLibrary, null); Spacer(Modifier.width(6.dp)); Text("Library") }
                }
            }
        }
        Spacer(Modifier.height(28.dp))
        KodiDpad(controller::kodiInput)
        Spacer(Modifier.height(20.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            AssistChip(onClick = { controller.kodiInput("Input.Back") }, label = { Text("Back") }, leadingIcon = { Icon(Icons.AutoMirrored.Filled.ArrowBack, null) })
            AssistChip(onClick = { controller.kodiInput("Input.Home") }, label = { Text("Home") }, leadingIcon = { Icon(Icons.Default.Home, null) })
            AssistChip(onClick = { controller.kodiInput("Input.ContextMenu") }, label = { Text("Menu") }, leadingIcon = { Icon(Icons.Default.MoreVert, null) })
        }
        if (room?.kodi?.baseUrl.isNullOrBlank()) Text("Kodi is not configured for this room.", color = TextMuted, modifier = Modifier.padding(top = 24.dp))
    }
}

@Composable
private fun KodiDpad(onInput: (String) -> Unit) {
    Column(horizontalAlignment = Alignment.CenterHorizontally) {
        FilledTonalIconButton(onClick = { onInput("Input.Up") }, modifier = Modifier.size(64.dp)) { Icon(Icons.Default.KeyboardArrowUp, "Up") }
        Row(verticalAlignment = Alignment.CenterVertically) {
            FilledTonalIconButton(onClick = { onInput("Input.Left") }, modifier = Modifier.size(64.dp)) { Icon(Icons.AutoMirrored.Filled.KeyboardArrowLeft, "Left") }
            Spacer(Modifier.width(8.dp))
            FilledIconButton(onClick = { onInput("Input.Select") }, modifier = Modifier.size(70.dp)) { Icon(Icons.Default.Check, "Select") }
            Spacer(Modifier.width(8.dp))
            FilledTonalIconButton(onClick = { onInput("Input.Right") }, modifier = Modifier.size(64.dp)) { Icon(Icons.AutoMirrored.Filled.KeyboardArrowRight, "Right") }
        }
        FilledTonalIconButton(onClick = { onInput("Input.Down") }, modifier = Modifier.size(64.dp)) { Icon(Icons.Default.KeyboardArrowDown, "Down") }
    }
}

@Composable
private fun KodiLibraryScreen(controller: AppController, room: RoomConfig?) {
    val state by controller.kodiBrowse.collectAsState()
    if (state.type == KodiBrowseType.HOME) {
        val categories = listOf(
            KodiBrowseType.MOVIES to "Movies",
            KodiBrowseType.TV_SHOWS to "TV shows",
            KodiBrowseType.MUSIC_VIDEOS to "Music videos",
            KodiBrowseType.ARTISTS to "Artists",
            KodiBrowseType.ALBUMS to "Albums",
            KodiBrowseType.SONGS to "Songs",
            KodiBrowseType.VIDEO_SOURCES to "Video files",
            KodiBrowseType.MUSIC_SOURCES to "Music files"
        )
        LazyVerticalGrid(columns = GridCells.Adaptive(145.dp), contentPadding = PaddingValues(16.dp), horizontalArrangement = Arrangement.spacedBy(12.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            gridItems(categories, key = { it.first.name }) { (type, title) ->
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
                KodiItemRow(item, room) { controller.kodiSelectItem(item) }
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
                    SettingsField("YouTube Data API key", draft.youtubeApiKey, secret = true) { draft = draft.copy(youtubeApiKey = it) }
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
                    }
                }
                item {
                    SettingsSection("${room.name} tiles") {
                        SourceTileAdder(room) { updatedRoom ->
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
private fun SourceTileAdder(room: RoomConfig, onChange: (RoomConfig) -> Unit) {
    if (room.sourceOptions.isEmpty()) return
    Text("Add source tile", color = TextMuted, fontSize = 12.sp)
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        room.sourceOptions.take(4).forEach { source ->
            val exists = room.tiles.any { it.actionType == TileActionType.SELECT_SOURCE && it.source.equals(source, true) }
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