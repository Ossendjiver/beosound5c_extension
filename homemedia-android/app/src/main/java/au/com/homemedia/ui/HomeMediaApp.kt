@file:OptIn(androidx.compose.foundation.ExperimentalFoundationApi::class)

package au.com.homemedia.ui

import android.Manifest
import android.app.Activity
import android.content.pm.ActivityInfo
import android.content.pm.PackageManager
import android.os.Build
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.GridItemSpan
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
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.media3.ui.PlayerView
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.compose.ui.window.Dialog
import au.com.homemedia.core.AppController
import au.com.homemedia.core.Screen
import au.com.homemedia.model.*
import au.com.homemedia.playback.PhonePlaybackService
import au.com.homemedia.storage.LocalVideoItem
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
    val pendingKodi by controller.pendingKodiItem.collectAsState()
    val pendingStremio by controller.pendingStremio.collectAsState()
    val calibration by controller.bluetoothCalibration.collectAsState()
    val wifiConnected by controller.wifiConnected.collectAsState()
    val phoneFullscreen by controller.phoneFullscreen.collectAsState()
    val activePlayerKey by controller.activePlayerKey.collectAsState()
    val activePlayerName = room?.let { r ->
        if (activePlayerKey == "primary") r.name
        else r.secondaryPlayers.firstOrNull { it.id == activePlayerKey }?.name ?: r.name
    }.orEmpty()
    var drawerOpen by remember { mutableStateOf(false) }
    val context = LocalContext.current
    val settingsZipLauncher = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        uri?.let(controller::importSettingsZip)
    }
    val localVideoLauncher = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        uri?.let(controller::addLocalVideo)
    }
    val localVideoLauncher = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        uri?.let(controller::addLocalVideo)
    }
    val permissionLauncher = rememberLauncherForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { }
    val activity = context as? Activity
    val view = LocalView.current
    LaunchedEffect(phoneFullscreen) {
        activity?.requestedOrientation = if (phoneFullscreen) {
            ActivityInfo.SCREEN_ORIENTATION_SENSOR_LANDSCAPE
        } else {
            ActivityInfo.SCREEN_ORIENTATION_UNSPECIFIED
        }
        activity?.window?.let { window ->
            val insets = WindowCompat.getInsetsController(window, view)
            if (phoneFullscreen) insets.hide(WindowInsetsCompat.Type.systemBars())
            else insets.show(WindowInsetsCompat.Type.systemBars())
        }
    }

    val activity = context as? Activity
    val view = LocalView.current
    LaunchedEffect(phoneFullscreen) {
        activity?.requestedOrientation = if (phoneFullscreen) ActivityInfo.SCREEN_ORIENTATION_SENSOR_LANDSCAPE
        else ActivityInfo.SCREEN_ORIENTATION_UNSPECIFIED
        activity?.window?.let { window ->
            val insets = WindowCompat.getInsetsController(window, view)
            if (phoneFullscreen) insets.hide(WindowInsetsCompat.Type.systemBars())
            else insets.show(WindowInsetsCompat.Type.systemBars())
        }
    }

    LaunchedEffect(wifiConnected) {
        if (!wifiConnected) return@LaunchedEffect
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
                    if (!phoneFullscreen) AppTopBar(
                        roomName = room?.name ?: "Home Media",
                        screen = screen,
                        activePlayerName = activePlayerName,
                        onMenu = { drawerOpen = true },
                        onBack = {
                            when (screen) {
                                Screen.ROOM -> drawerOpen = true
                                Screen.MASS_DETAIL -> controller.backFromMassDetail()
                                Screen.MASS_LIST -> controller.backToMassHome()
                                Screen.MEDIA, Screen.MASS_HOME, Screen.QUEUE, Screen.KODI, Screen.YOUTUBE, Screen.STREMIO, Screen.SETTINGS -> controller.goRoom()
                                Screen.PHONE_VIDEO -> controller.stopPhoneVideo()
                                Screen.KODI_LIBRARY -> {
                                    val state = controller.kodiBrowse.value
                                    if (state.type == KodiBrowseType.HOME) controller.exitKodiLibrary() else controller.kodiBrowseBack()
                                }
                            }
                        }
                    )
                    Box(Modifier.fillMaxSize()) {
                        when (screen) {
                            Screen.ROOM -> RoomScreen(controller, room)
                            Screen.MEDIA -> MediaHubScreen(controller) { localVideoLauncher.launch(arrayOf("video/*")) } { localVideoLauncher.launch(arrayOf("video/*")) }
                            Screen.MASS_HOME -> MassHomeScreen(controller)
                            Screen.MASS_LIST -> MassListScreen(controller)
                            Screen.MASS_DETAIL -> MassDetailScreen(controller)
                            Screen.QUEUE -> QueueScreen(controller)
                            Screen.KODI -> KodiRemoteScreen(controller, room)
                            Screen.KODI_LIBRARY -> KodiLibraryScreen(controller, room)
                            Screen.YOUTUBE -> YouTubeScreen(controller)
                            Screen.PHONE_VIDEO -> PhoneVideoScreen(controller)
                            Screen.STREMIO -> StremioScreen(controller)
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
                    targets = controller.youtubePlaybackTargets(),
                    onDismiss = controller::cancelPendingPlayback,
                    onTarget = controller::confirmYouTubePlayback
                )
            }

            pendingKodi?.let { item ->
                PlaybackTargetDialog(
                    title = "Play ${item.label}",
                    targets = controller.kodiPlaybackTargets(),
                    onDismiss = controller::cancelPendingPlayback,
                    onTarget = controller::confirmKodiPlayback
                )
            }

            pendingStremio?.let { pending ->
                PlaybackTargetDialog(
                    title = "Play ${pending.first.name}",
                    targets = controller.youtubePlaybackTargets(),
                    onDismiss = controller::cancelPendingStremio,
                    onTarget = controller::confirmStremioPlayback
                )
            }

            calibration?.let { state ->
                BluetoothCalibrationDialog(
                    state = state,
                    onCapture = controller::captureBluetoothCalibrationPoint,
                    onDismiss = controller::cancelBluetoothCalibration
                )
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun AppTopBar(
    roomName: String,
    screen: Screen,
    activePlayerName: String,
    onMenu: () -> Unit,
    onBack: () -> Unit
) {
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
        actions = {
            if (screen == Screen.ROOM && activePlayerName.isNotBlank()) {
                Surface(
                    modifier = Modifier.padding(end = 18.dp).size(27.dp),
                    shape = RoundedCornerShape(14.dp),
                    color = ActiveGreen
                ) {
                    Box(contentAlignment = Alignment.Center) {
                        Icon(
                            Icons.Default.Speaker,
                            activePlayerName,
                            tint = Color.Black,
                            modifier = Modifier.size(16.dp)
                        )
                    }
                }
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
    Screen.PHONE_VIDEO -> "Now playing"
    Screen.STREMIO -> "Stremio"
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
        NowPlayingCard(controller, now, settings)
        Spacer(Modifier.height(16.dp))

        val kodiActive = now.source.equals(room.kodiSourceName, ignoreCase = true) ||
            now.source.contains("kodi", ignoreCase = true)
        val tiles = remember(room.tiles, joinSourceId, kodiActive) {
            room.tiles.toMutableList().apply {
                if (kodiActive && room.kodi.baseUrl.isNotBlank()) {
                    add(TileConfig(id = "__KODI_REMOTE__", title = "Kodi remote", icon = "remote"))
                }
                if (joinSourceId != null) add(TileConfig(id = "__JOIN__", title = "Join", icon = "join"))
            }
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
                if (tile.id == "__SOURCE__") {
                    SourceTile(
                        currentSource = now.source,
                        sources = controller.availableSources(room),
                        modifier = Modifier.fillMaxWidth().aspectRatio(1.12f),
                        onSelect = controller::selectRoomSource
                    )
                } else if (tile.id == "__KODI_REMOTE__") {
                    RoomTile(
                        TileConfig(title = "Kodi remote", icon = "remote"),
                        Modifier.fillMaxWidth().aspectRatio(1.12f),
                        selected = true
                    ) { controller.openKodi() }
                } else if (tile.id == "__JOIN__" && joinRoom != null) {
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
private fun NowPlayingCard(controller: AppController, now: NowPlaying, settings: AppSettings) {
    Card(colors = CardDefaults.cardColors(containerColor = Panel), shape = RoundedCornerShape(24.dp)) {
        Row(Modifier.fillMaxWidth().padding(14.dp), verticalAlignment = Alignment.CenterVertically) {
            Surface(Modifier.size(88.dp), color = Panel2, shape = RoundedCornerShape(16.dp)) {
                if (now.imageUrl.isNotBlank()) AuthImage(now.imageUrl, settings.homeAssistantUrl, settings.homeAssistantToken, Modifier.fillMaxSize())
                else Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                    Icon(Icons.Default.MusicNote, null, tint = TextMuted, modifier = Modifier.size(36.dp))
                }
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
    }
}

@Composable
private fun RoomTile(tile: TileConfig, modifier: Modifier, selected: Boolean = false, onClick: () -> Unit) {
    Card(
        modifier = modifier.combinedClickable(onClick = onClick, onLongClick = {}),
        colors = CardDefaults.cardColors(containerColor = if (selected) Color(0xFF153822) else Panel2),
        shape = RoundedCornerShape(22.dp)
    ) {
        Column(Modifier.fillMaxSize().padding(18.dp), verticalArrangement = Arrangement.SpaceBetween) {
            Icon(tileIcon(tile.icon), null, modifier = Modifier.size(30.dp))
            Text(tile.title, fontSize = 17.sp, fontWeight = FontWeight.SemiBold)
        }
    }
}


@Composable
private fun SourceTile(
    currentSource: String,
    sources: List<String>,
    modifier: Modifier,
    onSelect: (String) -> Unit
) {
    var menu by remember { mutableStateOf(false) }
    val active = currentSource.isNotBlank()
    Box {
        Card(
            modifier = modifier.combinedClickable(onClick = { menu = true }, onLongClick = { menu = true }),
            colors = CardDefaults.cardColors(containerColor = if (active) Color(0xFF153822) else Panel2),
            shape = RoundedCornerShape(22.dp)
        ) {
            Column(Modifier.fillMaxSize().padding(18.dp), verticalArrangement = Arrangement.SpaceBetween) {
                Icon(Icons.Default.Input, null, tint = if (active) ActiveGreen else LocalContentColor.current, modifier = Modifier.size(31.dp))
                Column {
                    Text(currentSource.ifBlank { "Source" }, fontSize = 17.sp, fontWeight = FontWeight.SemiBold)
                    Text("Source", color = if (active) ActiveGreen else TextMuted, fontSize = 12.sp)
                }
            }
        }
        DropdownMenu(expanded = menu, onDismissRequest = { menu = false }) {
            if (sources.isEmpty()) {
                DropdownMenuItem(text = { Text("No sources reported") }, enabled = false, onClick = {})
            } else {
                sources.forEach { source ->
                    DropdownMenuItem(
                        text = { Text(source) },
                        leadingIcon = {
                            if (source.equals(currentSource, true)) Icon(Icons.Default.Check, null, tint = ActiveGreen)
                        },
                        onClick = {
                            menu = false
                            onSelect(source)
                        }
                    )
                }
            }
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
    "source" -> Icons.Default.Input
    "remote" -> Icons.Default.SettingsRemote
    "video" -> Icons.Default.VideoLibrary
    "youtube" -> Icons.Default.SmartDisplay
    else -> Icons.Default.MusicNote
}

// ---------------- Unified media hub ----------------

@Composable
private fun MediaHubScreen(controller: AppController, onAddLocal: () -> Unit) {
    val settings by controller.settings.collectAsState()
    val wifi by controller.wifiConnected.collectAsState()
    val localVideos by controller.localVideos.collectAsState()
    var tab by remember(wifi) { mutableStateOf(if (wifi) "Home" else "Local") }

    Column(Modifier.fillMaxSize()) {
        PrimaryTabRow(selectedTabIndex = if (tab == "Home") 0 else 1) {
            Tab(selected = tab == "Home", onClick = { tab = "Home" }, text = { Text("Home") }, icon = { Icon(Icons.Default.Home, null) })
            Tab(selected = tab == "Local", onClick = { tab = "Local" }, text = { Text("Local") }, icon = { Icon(Icons.Default.PhoneAndroid, null) })
        }

        if (tab == "Home") {
            LazyVerticalGrid(
                columns = GridCells.Adaptive(150.dp),
                contentPadding = PaddingValues(16.dp),
                horizontalArrangement = Arrangement.spacedBy(12.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp)
            ) {
                item {
                    MediaHubTile("Music", "Music Assistant", Icons.Default.LibraryMusic, controller::openLibrary)
                }
                item {
                    MediaHubTile("YouTube", "Channels, history & playlists", Icons.Default.SmartDisplay) { controller.openYouTube("Search") }
                }
                item {
                    MediaHubTile("Stremio", "Search, watch & cast", Icons.Default.MovieFilter, controller::openStremio)
                }
                if (settings.rooms.any { it.kodi.baseUrl.isNotBlank() }) {
                    item {
                        MediaHubTile("Video library", "Kodi + YouTube + Stremio", Icons.Default.VideoLibrary, controller::openSharedKodiLibrary)
                    }
                }
            }
        } else {
            Column(Modifier.fillMaxSize()) {
                Row(
                    Modifier.fillMaxWidth().padding(14.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Column(Modifier.weight(1f)) {
                        Text("Local", fontWeight = FontWeight.Bold, fontSize = 20.sp)
                        Text(
                            if (wifi) "On-device videos available without the home network."
                            else "Offline mode · room location and BLE are disabled.",
                            color = TextMuted,
                            fontSize = 12.sp
                        )
                    }
                    Button(onClick = onAddLocal) {
                        Icon(Icons.Default.Add, null)
                        Spacer(Modifier.width(6.dp))
                        Text("Add video")
                    }
                }
                if (localVideos.isEmpty()) {
                    EmptyState("No local videos", "Add videos from your phone for playback without Wi‑Fi.")
                } else {
                    LazyColumn(contentPadding = PaddingValues(horizontal = 14.dp, vertical = 4.dp)) {
                        items(localVideos, key = { it.uri }) { item ->
                            Row(
                                Modifier.fillMaxWidth()
                                    .combinedClickable(onClick = { controller.playLocalVideo(item) }, onLongClick = {})
                                    .padding(vertical = 10.dp),
                                verticalAlignment = Alignment.CenterVertically
                            ) {
                                Surface(Modifier.size(58.dp), color = Panel2, shape = RoundedCornerShape(10.dp)) {
                                    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                                        Icon(Icons.Default.PlayCircle, null, modifier = Modifier.size(30.dp))
                                    }
                                }
                                Spacer(Modifier.width(12.dp))
                                Text(item.name, Modifier.weight(1f), maxLines = 2, overflow = TextOverflow.Ellipsis)
                                IconButton(onClick = { controller.removeLocalVideo(item.uri) }) {
                                    Icon(Icons.Default.DeleteOutline, "Remove")
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun MediaHubTile(
    title: String,
    subtitle: String,
    icon: androidx.compose.ui.graphics.vector.ImageVector,
    onClick: () -> Unit
) {
    Card(
        Modifier.fillMaxWidth().aspectRatio(1.15f).combinedClickable(onClick = onClick, onLongClick = {}),
        colors = CardDefaults.cardColors(containerColor = Panel2)
    ) {
        Column(Modifier.fillMaxSize().padding(16.dp), verticalArrangement = Arrangement.SpaceBetween) {
            Icon(icon, null, modifier = Modifier.size(32.dp))
            Column {
                Text(title, fontWeight = FontWeight.Bold, fontSize = 18.sp)
                Text(subtitle, color = TextMuted, fontSize = 12.sp)
            }
        }
    }
}

@Composable
private fun YouTubeScreen(controller: AppController) {
    val results by controller.youtubeResults.collectAsState()
    val library by controller.youtubeLibrary.collectAsState()
    val wifi by controller.wifiConnected.collectAsState()
    val requestedSection by controller.youtubeSection.collectAsState()
    var query by remember { mutableStateOf("") }
    var mode by remember(requestedSection) { mutableStateOf(requestedSection) }
    var playlistName by remember { mutableStateOf("") }

    Column(Modifier.fillMaxSize().padding(horizontal = 14.dp)) {
        Row(
            Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).padding(bottom = 8.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp)
        ) {
            listOf("Search", "Channels", "History", "Playlists").forEach { label ->
                FilterChip(selected = mode == label, onClick = { mode = label }, label = { Text(label) })
            }
        }

        if (mode == "Search") {
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
            Text(
                if (wifi) "On Wi‑Fi: choose where to play. Off Wi‑Fi: playback stays on this phone."
                else "Not on Wi‑Fi: playback stays on this phone.",
                color = TextMuted,
                fontSize = 12.sp,
                modifier = Modifier.padding(vertical = 8.dp)
            )
            YouTubeVideoList(
                videos = results,
                playlists = library.playlists,
                onPlay = controller::requestYouTubePlayback,
                onSaveChannel = controller::saveYouTubeChannel,
                onAddToPlaylist = controller::addYouTubeToPlaylist
            )
        } else if (mode == "Channels") {
            if (library.channels.isEmpty()) {
                EmptyState("No saved channels", "Save a channel from a YouTube result.")
            } else {
                LazyColumn {
                    items(library.channels, key = { it.url }) { channel ->
                        ListItem(
                            headlineContent = { Text(channel.name) },
                            supportingContent = { Text(channel.url, maxLines = 1, overflow = TextOverflow.Ellipsis) },
                            leadingContent = { Icon(Icons.Default.Subscriptions, null) },
                            trailingContent = {
                                IconButton(onClick = { controller.removeYouTubeChannel(channel.url) }) {
                                    Icon(Icons.Default.DeleteOutline, "Remove")
                                }
                            },
                            modifier = Modifier.combinedClickable(
                                onClick = { controller.openSavedYouTubeChannel(channel); mode = "Search" },
                                onLongClick = {}
                            )
                        )
                    }
                }
            }
        } else if (mode == "History") {
            if (library.history.isEmpty()) {
                EmptyState("No watch history", "Videos you start will appear here.")
            } else {
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
                    TextButton(onClick = controller::clearYouTubeHistory) { Text("Clear history") }
                }
                YouTubeVideoList(
                    videos = library.history,
                    playlists = library.playlists,
                    onPlay = controller::requestYouTubePlayback,
                    onSaveChannel = controller::saveYouTubeChannel,
                    onAddToPlaylist = controller::addYouTubeToPlaylist
                )
            }
        } else {
            Row(verticalAlignment = Alignment.CenterVertically) {
                OutlinedTextField(
                    value = playlistName,
                    onValueChange = { playlistName = it },
                    placeholder = { Text("New playlist name") },
                    singleLine = true,
                    modifier = Modifier.weight(1f)
                )
                Spacer(Modifier.width(8.dp))
                FilledIconButton(onClick = {
                    controller.createYouTubePlaylist(playlistName)
                    playlistName = ""
                }) { Icon(Icons.Default.PlaylistAdd, "Create playlist") }
            }
            Spacer(Modifier.height(8.dp))
            if (library.playlists.isEmpty()) {
                EmptyState("No playlists", "Create a local playlist above.")
            } else {
                LazyColumn {
                    library.playlists.forEach { playlist ->
                        item(key = "header-" + playlist.id) {
                            Text(
                                playlist.name,
                                fontWeight = FontWeight.Bold,
                                fontSize = 18.sp,
                                modifier = Modifier.padding(vertical = 10.dp)
                            )
                        }
                        items(playlist.videos, key = { playlist.id + "-" + it.videoId }) { video ->
                            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                                YouTubeCompactRow(
                                    item = video,
                                    onPlay = { controller.requestYouTubePlayback(video) },
                                    modifier = Modifier.weight(1f)
                                )
                                IconButton(onClick = { controller.removeYouTubeFromPlaylist(playlist.id, video.videoId) }) {
                                    Icon(Icons.Default.RemoveCircleOutline, "Remove")
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun YouTubeVideoList(
    videos: List<YouTubeItem>,
    playlists: List<YouTubePlaylist>,
    onPlay: (YouTubeItem) -> Unit,
    onSaveChannel: (YouTubeItem) -> Unit,
    onAddToPlaylist: (String, YouTubeItem) -> Unit
) {
    if (videos.isEmpty()) {
        EmptyState("YouTube", "Search for a video or open a saved channel.")
        return
    }
    LazyColumn(contentPadding = PaddingValues(bottom = 28.dp)) {
        items(videos, key = { it.videoId }) { item ->
            var menu by remember(item.videoId) { mutableStateOf(false) }
            Row(
                Modifier.fillMaxWidth()
                    .combinedClickable(onClick = { onPlay(item) }, onLongClick = { menu = true })
                    .padding(vertical = 8.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                Surface(Modifier.size(84.dp, 48.dp), color = Panel2, shape = RoundedCornerShape(9.dp)) {
                    if (item.thumbnail.isNotBlank()) {
                        AsyncImage(
                            model = item.thumbnail,
                            contentDescription = null,
                            modifier = Modifier.fillMaxSize(),
                            contentScale = ContentScale.Crop
                        )
                    } else {
                        Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) { Icon(Icons.Default.SmartDisplay, null) }
                    }
                }
                Spacer(Modifier.width(12.dp))
                Column(Modifier.weight(1f)) {
                    Text(item.title, maxLines = 2, overflow = TextOverflow.Ellipsis, fontWeight = FontWeight.Medium)
                    if (item.channel.isNotBlank()) Text(item.channel, color = TextMuted, fontSize = 12.sp)
                }
                Box {
                    IconButton(onClick = { menu = true }) { Icon(Icons.Default.MoreVert, "More") }
                    DropdownMenu(expanded = menu, onDismissRequest = { menu = false }) {
                        DropdownMenuItem(
                            text = { Text("Save channel") },
                            leadingIcon = { Icon(Icons.Default.Subscriptions, null) },
                            enabled = item.channelUrl.isNotBlank(),
                            onClick = { menu = false; onSaveChannel(item) }
                        )
                        playlists.forEach { playlist ->
                            DropdownMenuItem(
                                text = { Text("Add to " + playlist.name) },
                                leadingIcon = { Icon(Icons.Default.PlaylistAdd, null) },
                                onClick = { menu = false; onAddToPlaylist(playlist.id, item) }
                            )
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun YouTubeCompactRow(item: YouTubeItem, onPlay: () -> Unit, modifier: Modifier = Modifier) {
    Row(
        modifier.combinedClickable(onClick = onPlay, onLongClick = {}).padding(vertical = 6.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        if (item.thumbnail.isNotBlank()) {
            AsyncImage(
                model = item.thumbnail,
                contentDescription = null,
                modifier = Modifier.size(72.dp, 42.dp),
                contentScale = ContentScale.Crop
            )
        } else {
            Box(Modifier.size(72.dp, 42.dp), contentAlignment = Alignment.Center) { Icon(Icons.Default.SmartDisplay, null) }
        }
        Spacer(Modifier.width(10.dp))
        Column(Modifier.weight(1f)) {
            Text(item.title, maxLines = 2, overflow = TextOverflow.Ellipsis)
            if (item.channel.isNotBlank()) Text(item.channel, color = TextMuted, fontSize = 12.sp)
        }
    }
}

@Composable
private fun PhoneVideoScreen(controller: AppController) {
    val video by controller.phoneVideo.collectAsState()
    val fullscreen by controller.phoneFullscreen.collectAsState()
    val player by PhonePlaybackService.player.collectAsState()
    val current = video ?: run { EmptyState("Nothing playing", "Return to YouTube."); return }

    Column(Modifier.fillMaxSize().background(Color.Black)) {
        Box(Modifier.fillMaxWidth().weight(1f)) {
            if (player == null) {
                Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) { CircularProgressIndicator() }
            } else {
                AndroidView(
                    factory = { ctx -> PlayerView(ctx).apply { useController = true; this.player = player } },
                    update = { it.player = player },
                    modifier = Modifier.fillMaxSize()
                )
            }
            Row(Modifier.align(Alignment.TopEnd).padding(10.dp), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                FilledTonalIconButton(onClick = { controller.setPhoneFullscreen(!fullscreen) }) {
                    Icon(if (fullscreen) Icons.Default.FullscreenExit else Icons.Default.Fullscreen, if (fullscreen) "Exit fullscreen" else "Fullscreen")
                }
                if (fullscreen) {
                    FilledTonalIconButton(onClick = controller::stopPhoneVideo) { Icon(Icons.Default.Close, "Close") }
                }
            }
        }
        if (!fullscreen) {
            Row(Modifier.fillMaxWidth().padding(12.dp), verticalAlignment = Alignment.CenterVertically) {
                Text(current.title, modifier = Modifier.weight(1f), maxLines = 2, overflow = TextOverflow.Ellipsis)
                TextButton(onClick = controller::stopPhoneVideo) { Text("Close") }
            }
        }
    }
}

@Composable
private fun StremioScreen(controller: AppController) {
    val results by controller.stremioResults.collectAsState()
    val selected by controller.selectedStremio.collectAsState()
    val streams by controller.stremioStreams.collectAsState()
    val settings by controller.settings.collectAsState()
    var query by remember { mutableStateOf("") }

    Column(Modifier.fillMaxSize().padding(horizontal = 14.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            OutlinedTextField(value = query, onValueChange = { query = it }, placeholder = { Text("Search movies & series") }, singleLine = true, modifier = Modifier.weight(1f))
            Spacer(Modifier.width(8.dp))
            FilledIconButton(onClick = { controller.stremioSearch(query) }) { Icon(Icons.Default.Search, "Search") }
        }

        if (selected == null) {
            Row(Modifier.fillMaxWidth().padding(vertical = 8.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                AssistChip(onClick = controller::stremioBoard, label = { Text("Stremio Board") }, leadingIcon = { Icon(Icons.Default.Dashboard, null) })
                AssistChip(onClick = controller::stremioLibrary, label = { Text("Stremio Library") }, leadingIcon = { Icon(Icons.Default.VideoLibrary, null) })
            }
            if (results.isEmpty()) {
                EmptyState("Stremio", "Search is native to Home Media. Compatible direct streams can play here or cast; unsupported streams open in Stremio.")
            } else {
                LazyVerticalGrid(columns = GridCells.Adaptive(145.dp), contentPadding = PaddingValues(vertical = 10.dp), horizontalArrangement = Arrangement.spacedBy(10.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    gridItems(results, key = { it.type + ":" + it.id }) { item ->
                        Card(
                            Modifier.fillMaxWidth().combinedClickable(onClick = { controller.selectStremioItem(item) }, onLongClick = { controller.openStremioItemInApp(item) }),
                            colors = CardDefaults.cardColors(containerColor = Panel2)
                        ) {
                            Column {
                                Surface(Modifier.fillMaxWidth().aspectRatio(2f / 3f), color = Panel3) {
                                    if (item.poster.isNotBlank()) AsyncImage(item.poster, null, Modifier.fillMaxSize(), contentScale = ContentScale.Crop)
                                    else Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) { Icon(Icons.Default.Movie, null, modifier = Modifier.size(42.dp)) }
                                }
                                Column(Modifier.padding(10.dp)) {
                                    Text(item.name, fontWeight = FontWeight.SemiBold, maxLines = 2, overflow = TextOverflow.Ellipsis)
                                    Text(listOf(item.type.replaceFirstChar { it.uppercase() }, item.releaseInfo).filter { it.isNotBlank() }.joinToString(" · "), color = TextMuted, fontSize = 12.sp)
                                }
                            }
                        }
                    }
                }
            }
        } else {
            val item = selected!!
            Row(Modifier.fillMaxWidth().padding(vertical = 10.dp), verticalAlignment = Alignment.CenterVertically) {
                IconButton(onClick = { controller.stremioSearch(query.ifBlank { item.name }) }) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Back") }
                Column(Modifier.weight(1f)) {
                    Text(item.name, fontWeight = FontWeight.Bold, fontSize = 20.sp)
                    Text(item.type.replaceFirstChar { it.uppercase() }, color = TextMuted, fontSize = 12.sp)
                }
                TextButton(onClick = { controller.openStremioItemInApp(item) }) { Text("Open in Stremio") }
            }
            if (item.description.isNotBlank()) {
                Text(item.description, color = TextMuted, fontSize = 13.sp, maxLines = 4, overflow = TextOverflow.Ellipsis)
                Spacer(Modifier.height(10.dp))
            }
            if (item.type == "series") {
                EmptyState("Series episode selection", "Episode selection currently hands off to Stremio. Movies and compatible direct streams are playable inside Home Media.")
            } else if (settings.stremioStreamAddonManifests.isEmpty()) {
                EmptyState("No stream addons configured", "Add Stremio addon manifest URLs in Settings, or open this title in Stremio.")
            } else if (streams.isEmpty()) {
                EmptyState("No compatible streams", "The configured addons returned no direct stream for this title.")
            } else {
                LazyColumn(contentPadding = PaddingValues(vertical = 8.dp)) {
                    items(streams.indices.toList()) { idx ->
                        val stream = streams[idx]
                        ListItem(
                            headlineContent = { Text(stream.title.ifBlank { stream.name.ifBlank { "Stream ${idx + 1}" } }) },
                            supportingContent = { Text(
                                when {
                                    stream.directlyPlayable -> "Direct stream · play here or cast"
                                    stream.externalUrl.isNotBlank() -> "External provider"
                                    stream.infoHash.isNotBlank() -> "Torrent stream · open in Stremio"
                                    else -> "Stremio stream"
                                }, color = TextMuted
                            ) },
                            leadingContent = { Icon(if (stream.directlyPlayable) Icons.Default.PlayCircle else Icons.Default.OpenInNew, null) },
                            modifier = Modifier.combinedClickable(onClick = { controller.requestStremioPlayback(item, stream) }, onLongClick = { controller.openStremioItemInApp(item) })
                        )
                    }
                }
            }
        }
    }
}

@Composable
private fun BluetoothCalibrationDialog(
    state: au.com.homemedia.core.BluetoothCalibrationState,
    onCapture: () -> Unit,
    onDismiss: () -> Unit
) {
    val instruction = when (state.point) {
        1 -> "Stand at one side of the room where you normally use the phone."
        2 -> "Move to the middle of the room, then capture the second point."
        else -> "Move to the opposite side of the room for the final point."
    }
    AlertDialog(
        onDismissRequest = { if (!state.running) onDismiss() },
        title = { Text("Calibrate ${state.roomName}") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Text(
                    if (state.complete) "Calibration complete"
                    else "Point ${state.point} of 3",
                    fontWeight = FontWeight.Bold,
                    color = if (state.complete) ActiveGreen else LocalContentColor.current
                )
                if (!state.complete) Text(instruction)
                Text(
                    "Each point scans Bluetooth for about 5.5 seconds. Three spatial samples are compared when locating the room.",
                    color = TextMuted,
                    fontSize = 12.sp
                )
                if (state.lastSummary.isNotBlank()) {
                    Surface(color = Panel2, shape = RoundedCornerShape(10.dp)) {
                        Text(state.lastSummary, Modifier.padding(10.dp), fontSize = 13.sp)
                    }
                }
                if (state.running) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        CircularProgressIndicator(Modifier.size(22.dp), strokeWidth = 2.dp)
                        Spacer(Modifier.width(10.dp))
                        Text("Scanning… keep the phone roughly still")
                    }
                }
                if (state.complete && state.lastSummary.contains("Bluetooth-quiet", ignoreCase = true)) {
                    Text(
                        "This room has been marked Bluetooth-quiet. Weak signals bleeding in from another room will not pull location away from it.",
                        color = ActiveGreen,
                        fontSize = 12.sp
                    )
                }
            }
        },
        confirmButton = {
            if (state.complete) {
                Button(onClick = onDismiss) { Text("Done") }
            } else {
                Button(onClick = onCapture, enabled = !state.running) {
                    Text("Capture point ${state.point}")
                }
            }
        },
        dismissButton = {
            if (!state.running && !state.complete) TextButton(onClick = onDismiss) { Text("Cancel") }
        }
    )
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
                        Icon(when { target.phone -> Icons.Default.Smartphone; target.kodi -> Icons.Default.Tv; target.cast -> Icons.Default.Cast; else -> Icons.Default.Speaker }, null)
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
    val artworkRoom = controller.kodiLibraryHostRoom() ?: room
    if (state.type == KodiBrowseType.HOME) {
        val youtubeLibrary by controller.youtubeLibrary.collectAsState()
        val combinedYoutube = remember(youtubeLibrary) {
            (youtubeLibrary.history + youtubeLibrary.playlists.flatMap { it.videos }).distinctBy { it.videoId }
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
            item(span = { GridItemSpan(maxLineSpan) }) { Text("Kodi", fontWeight = FontWeight.Bold, fontSize = 20.sp) }
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
                item(span = { GridItemSpan(maxLineSpan) }) { Text("Watched and playlist videos will appear here.", color = TextMuted, fontSize = 13.sp) }
            } else {
                gridItems(combinedYoutube, key = { "yt-" + it.videoId }) { video ->
                    Card(
                        Modifier.fillMaxWidth().combinedClickable(onClick = { controller.requestYouTubePlayback(video) }, onLongClick = { controller.openYouTube("History") }),
                        colors = CardDefaults.cardColors(containerColor = Panel2)
                    ) {
                        Column {
                            Surface(Modifier.fillMaxWidth().aspectRatio(16f / 9f), color = Panel3) {
                                if (video.thumbnail.isNotBlank()) AsyncImage(video.thumbnail, null, Modifier.fillMaxSize(), contentScale = ContentScale.Crop)
                                else Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) { Icon(Icons.Default.SmartDisplay, null) }
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
                item(span = { GridItemSpan(maxLineSpan) }) { Text("Saved YouTube channels", fontWeight = FontWeight.Bold, fontSize = 18.sp, modifier = Modifier.padding(top = 10.dp)) }
                gridItems(youtubeLibrary.channels, key = { "channel-" + it.url }) { channel ->
                    Card(
                        Modifier.fillMaxWidth().aspectRatio(1.2f).combinedClickable(onClick = { controller.openSavedYouTubeChannel(channel) }, onLongClick = { controller.openYouTube("Channels") }),
                        colors = CardDefaults.cardColors(containerColor = Panel2)
                    ) {
                        Column(Modifier.fillMaxSize().padding(14.dp), verticalArrangement = Arrangement.SpaceBetween) {
                            Icon(Icons.Default.Subscriptions, null, modifier = Modifier.size(30.dp))
                            Text(channel.name, fontWeight = FontWeight.SemiBold, maxLines = 2, overflow = TextOverflow.Ellipsis)
                        }
                    }
                }
            }
            item(span = { GridItemSpan(maxLineSpan) }) { Text("Stremio", fontWeight = FontWeight.Bold, fontSize = 20.sp, modifier = Modifier.padding(top = 10.dp)) }
            item { MediaHubTile("Search Stremio", "Movies, series & streams", Icons.Default.MovieFilter, controller::openStremio) }
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
                    SettingsField(
                        "Stremio stream addon manifests (comma separated)",
                        draft.stremioStreamAddonManifests.joinToString(", ")
                    ) {
                        draft = draft.copy(stremioStreamAddonManifests = it.split(",").map(String::trim).filter(String::isNotBlank))
                    }
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
@Composable
private fun MediaHubScreen(controller: AppController, onAddLocal: () -> Unit) {
    val settings by controller.settings.collectAsState()
    val wifi by controller.wifiConnected.collectAsState()
    val localVideos by controller.localVideos.collectAsState()
    var tab by remember(wifi) { mutableStateOf(if (wifi) "Home" else "Local") }

    Column(Modifier.fillMaxSize()) {
        PrimaryTabRow(selectedTabIndex = if (tab == "Home") 0 else 1) {
            Tab(selected = tab == "Home", onClick = { tab = "Home" }, text = { Text("Home") }, icon = { Icon(Icons.Default.Home, null) })
            Tab(selected = tab == "Local", onClick = { tab = "Local" }, text = { Text("Local") }, icon = { Icon(Icons.Default.PhoneAndroid, null) })
        }
        if (tab == "Home") {
            LazyVerticalGrid(
                columns = GridCells.Adaptive(150.dp),
                contentPadding = PaddingValues(16.dp),
                horizontalArrangement = Arrangement.spacedBy(12.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp)
            ) {
                item { MediaHubTile("Music", "Music Assistant", Icons.Default.LibraryMusic, controller::openLibrary) }
                item { MediaHubTile("YouTube", "Channels, history & playlists", Icons.Default.SmartDisplay) { controller.openYouTube("Search") } }
                item { MediaHubTile("Stremio", "Search, watch & cast", Icons.Default.MovieFilter, controller::openStremio) }
                if (settings.rooms.any { it.kodi.baseUrl.isNotBlank() }) {
                    item { MediaHubTile("Video library", "Kodi + YouTube + Stremio", Icons.Default.VideoLibrary, controller::openSharedKodiLibrary) }
                }
            }
        } else {
            Column(Modifier.fillMaxSize()) {
                Row(Modifier.fillMaxWidth().padding(14.dp), verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text("Local", fontWeight = FontWeight.Bold, fontSize = 20.sp)
                        Text(
                            if (wifi) "On-device videos available without the home network."
                            else "Offline mode · room location and BLE are disabled.",
                            color = TextMuted,
                            fontSize = 12.sp
                        )
                    }
                    Button(onClick = onAddLocal) {
                        Icon(Icons.Default.Add, null); Spacer(Modifier.width(6.dp)); Text("Add video")
                    }
                }
                if (localVideos.isEmpty()) {
                    EmptyState("No local videos", "Add videos from your phone for playback without Wi‑Fi.")
                } else {
                    LazyColumn(contentPadding = PaddingValues(horizontal = 14.dp, vertical = 4.dp)) {
                        items(localVideos, key = { it.uri }) { item ->
                            Row(
                                Modifier.fillMaxWidth().combinedClickable(onClick = { controller.playLocalVideo(item) }, onLongClick = {}).padding(vertical = 10.dp),
                                verticalAlignment = Alignment.CenterVertically
                            ) {
                                Surface(Modifier.size(58.dp), color = Panel2, shape = RoundedCornerShape(10.dp)) {
                                    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) { Icon(Icons.Default.PlayCircle, null, modifier = Modifier.size(30.dp)) }
                                }
                                Spacer(Modifier.width(12.dp))
                                Text(item.name, Modifier.weight(1f), maxLines = 2, overflow = TextOverflow.Ellipsis)
                                IconButton(onClick = { controller.removeLocalVideo(item.uri) }) { Icon(Icons.Default.DeleteOutline, "Remove") }
                            }
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun MediaHubTile(
    title: String,
    subtitle: String,
    icon: androidx.compose.ui.graphics.vector.ImageVector,
    onClick: () -> Unit
) {
    Card(
        Modifier.fillMaxWidth().aspectRatio(1.15f).combinedClickable(onClick = onClick, onLongClick = {}),
        colors = CardDefaults.cardColors(containerColor = Panel2)
    ) {
        Column(Modifier.fillMaxSize().padding(16.dp), verticalArrangement = Arrangement.SpaceBetween) {
            Icon(icon, null, modifier = Modifier.size(32.dp))
            Column {
                Text(title, fontWeight = FontWeight.Bold, fontSize = 18.sp)
                Text(subtitle, color = TextMuted, fontSize = 12.sp)
            }
        }
    }
}

@Composable
private fun YouTubeScreen(controller: AppController) {
    val results by controller.youtubeResults.collectAsState()
    val library by controller.youtubeLibrary.collectAsState()
    val wifi by controller.wifiConnected.collectAsState()
    val requestedSection by controller.youtubeSection.collectAsState()
    var query by remember { mutableStateOf("") }
    var mode by remember(requestedSection) { mutableStateOf(requestedSection) }
    var playlistName by remember { mutableStateOf("") }

    Column(Modifier.fillMaxSize().padding(horizontal = 14.dp)) {
        Row(
            Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).padding(bottom = 8.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp)
        ) {
            listOf("Search", "Channels", "History", "Playlists").forEach { label ->
                FilterChip(selected = mode == label, onClick = { mode = label }, label = { Text(label) })
            }
        }

        if (mode == "Search") {
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
            Text(
                if (wifi) "On Wi‑Fi: choose where to play. Off Wi‑Fi: playback stays on this phone."
                else "Not on Wi‑Fi: playback stays on this phone.",
                color = TextMuted,
                fontSize = 12.sp,
                modifier = Modifier.padding(vertical = 8.dp)
            )
            YouTubeVideoList(
                videos = results,
                playlists = library.playlists,
                onPlay = controller::requestYouTubePlayback,
                onSaveChannel = controller::saveYouTubeChannel,
                onAddToPlaylist = controller::addYouTubeToPlaylist
            )
        } else if (mode == "Channels") {
            if (library.channels.isEmpty()) {
                EmptyState("No saved channels", "Save a channel from a YouTube result.")
            } else {
                LazyColumn {
                    items(library.channels, key = { it.url }) { channel ->
                        ListItem(
                            headlineContent = { Text(channel.name) },
                            supportingContent = { Text(channel.url, maxLines = 1, overflow = TextOverflow.Ellipsis) },
                            leadingContent = { Icon(Icons.Default.Subscriptions, null) },
                            trailingContent = {
                                IconButton(onClick = { controller.removeYouTubeChannel(channel.url) }) {
                                    Icon(Icons.Default.DeleteOutline, "Remove")
                                }
                            },
                            modifier = Modifier.combinedClickable(
                                onClick = { controller.openSavedYouTubeChannel(channel); mode = "Search" },
                                onLongClick = {}
                            )
                        )
                    }
                }
            }
        } else if (mode == "History") {
            if (library.history.isEmpty()) {
                EmptyState("No watch history", "Videos you start will appear here.")
            } else {
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
                    TextButton(onClick = controller::clearYouTubeHistory) { Text("Clear history") }
                }
                YouTubeVideoList(
                    videos = library.history,
                    playlists = library.playlists,
                    onPlay = controller::requestYouTubePlayback,
                    onSaveChannel = controller::saveYouTubeChannel,
                    onAddToPlaylist = controller::addYouTubeToPlaylist
                )
            }
        } else {
            Row(verticalAlignment = Alignment.CenterVertically) {
                OutlinedTextField(
                    value = playlistName,
                    onValueChange = { playlistName = it },
                    placeholder = { Text("New playlist name") },
                    singleLine = true,
                    modifier = Modifier.weight(1f)
                )
                Spacer(Modifier.width(8.dp))
                FilledIconButton(onClick = {
                    controller.createYouTubePlaylist(playlistName)
                    playlistName = ""
                }) { Icon(Icons.Default.PlaylistAdd, "Create playlist") }
            }
            Spacer(Modifier.height(8.dp))
            if (library.playlists.isEmpty()) {
                EmptyState("No playlists", "Create a local playlist above.")
            } else {
                LazyColumn {
                    library.playlists.forEach { playlist ->
                        item(key = "header-" + playlist.id) {
                            Text(
                                playlist.name,
                                fontWeight = FontWeight.Bold,
                                fontSize = 18.sp,
                                modifier = Modifier.padding(vertical = 10.dp)
                            )
                        }
                        items(playlist.videos, key = { playlist.id + "-" + it.videoId }) { video ->
                            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                                YouTubeCompactRow(
                                    item = video,
                                    onPlay = { controller.requestYouTubePlayback(video) },
                                    modifier = Modifier.weight(1f)
                                )
                                IconButton(onClick = { controller.removeYouTubeFromPlaylist(playlist.id, video.videoId) }) {
                                    Icon(Icons.Default.RemoveCircleOutline, "Remove")
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun YouTubeVideoList(
    videos: List<YouTubeItem>,
    playlists: List<YouTubePlaylist>,
    onPlay: (YouTubeItem) -> Unit,
    onSaveChannel: (YouTubeItem) -> Unit,
    onAddToPlaylist: (String, YouTubeItem) -> Unit
) {
    if (videos.isEmpty()) {
        EmptyState("YouTube", "Search for a video or open a saved channel.")
        return
    }
    LazyColumn(contentPadding = PaddingValues(bottom = 28.dp)) {
        items(videos, key = { it.videoId }) { item ->
            var menu by remember(item.videoId) { mutableStateOf(false) }
            Row(
                Modifier.fillMaxWidth()
                    .combinedClickable(onClick = { onPlay(item) }, onLongClick = { menu = true })
                    .padding(vertical = 8.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                Surface(Modifier.size(84.dp, 48.dp), color = Panel2, shape = RoundedCornerShape(9.dp)) {
                    if (item.thumbnail.isNotBlank()) {
                        AsyncImage(
                            model = item.thumbnail,
                            contentDescription = null,
                            modifier = Modifier.fillMaxSize(),
                            contentScale = ContentScale.Crop
                        )
                    } else {
                        Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) { Icon(Icons.Default.SmartDisplay, null) }
                    }
                }
                Spacer(Modifier.width(12.dp))
                Column(Modifier.weight(1f)) {
                    Text(item.title, maxLines = 2, overflow = TextOverflow.Ellipsis, fontWeight = FontWeight.Medium)
                    if (item.channel.isNotBlank()) Text(item.channel, color = TextMuted, fontSize = 12.sp)
                }
                Box {
                    IconButton(onClick = { menu = true }) { Icon(Icons.Default.MoreVert, "More") }
                    DropdownMenu(expanded = menu, onDismissRequest = { menu = false }) {
                        DropdownMenuItem(
                            text = { Text("Save channel") },
                            leadingIcon = { Icon(Icons.Default.Subscriptions, null) },
                            enabled = item.channelUrl.isNotBlank(),
                            onClick = { menu = false; onSaveChannel(item) }
                        )
                        playlists.forEach { playlist ->
                            DropdownMenuItem(
                                text = { Text("Add to " + playlist.name) },
                                leadingIcon = { Icon(Icons.Default.PlaylistAdd, null) },
                                onClick = { menu = false; onAddToPlaylist(playlist.id, item) }
                            )
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun YouTubeCompactRow(item: YouTubeItem, onPlay: () -> Unit, modifier: Modifier = Modifier) {
    Row(
        modifier.combinedClickable(onClick = onPlay, onLongClick = {}).padding(vertical = 6.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        if (item.thumbnail.isNotBlank()) {
            AsyncImage(
                model = item.thumbnail,
                contentDescription = null,
                modifier = Modifier.size(72.dp, 42.dp),
                contentScale = ContentScale.Crop
            )
        } else {
            Box(Modifier.size(72.dp, 42.dp), contentAlignment = Alignment.Center) { Icon(Icons.Default.SmartDisplay, null) }
        }
        Spacer(Modifier.width(10.dp))
        Column(Modifier.weight(1f)) {
            Text(item.title, maxLines = 2, overflow = TextOverflow.Ellipsis)
            if (item.channel.isNotBlank()) Text(item.channel, color = TextMuted, fontSize = 12.sp)
        }
    }
}

@Composable
private fun PhoneVideoScreen(controller: AppController) {
    val video by controller.phoneVideo.collectAsState()
    val fullscreen by controller.phoneFullscreen.collectAsState()
    val player by PhonePlaybackService.player.collectAsState()
    val current = video ?: run {
        EmptyState("Nothing playing", "Return to YouTube.")
        return
    }

    Column(Modifier.fillMaxSize().background(Color.Black)) {
        Box(Modifier.fillMaxWidth().weight(1f)) {
            if (player == null) {
                Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                    CircularProgressIndicator()
                }
            } else {
                AndroidView(
                    factory = { ctx ->
                        PlayerView(ctx).apply {
                            useController = true
                            this.player = player
                        }
                    },
                    update = { it.player = player },
                    modifier = Modifier.fillMaxSize()
                )
            }
            Row(
                Modifier.align(Alignment.TopEnd).padding(10.dp),
                horizontalArrangement = Arrangement.spacedBy(6.dp)
            ) {
                FilledTonalIconButton(
                    onClick = { controller.setPhoneFullscreen(!fullscreen) }
                ) {
                    Icon(
                        if (fullscreen) Icons.Default.FullscreenExit else Icons.Default.Fullscreen,
                        if (fullscreen) "Exit fullscreen" else "Fullscreen"
                    )
                }
                if (fullscreen) {
                    FilledTonalIconButton(onClick = controller::stopPhoneVideo) {
                        Icon(Icons.Default.Close, "Close")
                    }
                }
            }
        }
        if (!fullscreen) {
            Row(Modifier.fillMaxWidth().padding(12.dp), verticalAlignment = Alignment.CenterVertically) {
                Text(current.title, modifier = Modifier.weight(1f), maxLines = 2, overflow = TextOverflow.Ellipsis)
                TextButton(onClick = controller::stopPhoneVideo) { Text("Close") }
            }
        }
    }
}

@Composable
private fun StremioScreen(controller: AppController) {
    val results by controller.stremioResults.collectAsState()
    val selected by controller.selectedStremio.collectAsState()
    val streams by controller.stremioStreams.collectAsState()
    val settings by controller.settings.collectAsState()
    var query by remember { mutableStateOf("") }

    Column(Modifier.fillMaxSize().padding(horizontal = 14.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            OutlinedTextField(
                value = query,
                onValueChange = { query = it },
                placeholder = { Text("Search movies & series") },
                singleLine = true,
                modifier = Modifier.weight(1f)
            )
            Spacer(Modifier.width(8.dp))
            FilledIconButton(onClick = { controller.stremioSearch(query) }) {
                Icon(Icons.Default.Search, "Search")
            }
        }

        if (selected == null) {
            Row(
                Modifier.fillMaxWidth().padding(vertical = 8.dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                AssistChip(onClick = controller::stremioBoard, label = { Text("Stremio Board") }, leadingIcon = { Icon(Icons.Default.Dashboard, null) })
                AssistChip(onClick = controller::stremioLibrary, label = { Text("Stremio Library") }, leadingIcon = { Icon(Icons.Default.VideoLibrary, null) })
            }
            if (results.isEmpty()) {
                EmptyState("Stremio", "Search is native to Home Media. Compatible direct streams can play here or cast; unsupported streams open in Stremio.")
            } else {
                LazyVerticalGrid(
                    columns = GridCells.Adaptive(145.dp),
                    contentPadding = PaddingValues(vertical = 10.dp),
                    horizontalArrangement = Arrangement.spacedBy(10.dp),
                    verticalArrangement = Arrangement.spacedBy(12.dp)
                ) {
                    gridItems(results, key = { it.type + ":" + it.id }) { item ->
                        Card(
                            Modifier.fillMaxWidth().combinedClickable(
                                onClick = { controller.selectStremioItem(item) },
                                onLongClick = { controller.openStremioItemInApp(item) }
                            ),
                            colors = CardDefaults.cardColors(containerColor = Panel2)
                        ) {
                            Column {
                                Surface(Modifier.fillMaxWidth().aspectRatio(2f / 3f), color = Panel3) {
                                    if (item.poster.isNotBlank()) {
                                        AsyncImage(
                                            model = item.poster,
                                            contentDescription = null,
                                            modifier = Modifier.fillMaxSize(),
                                            contentScale = ContentScale.Crop
                                        )
                                    } else {
                                        Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                                            Icon(Icons.Default.Movie, null, modifier = Modifier.size(42.dp))
                                        }
                                    }
                                }
                                Column(Modifier.padding(10.dp)) {
                                    Text(item.name, fontWeight = FontWeight.SemiBold, maxLines = 2, overflow = TextOverflow.Ellipsis)
                                    Text(
                                        listOf(item.type.replaceFirstChar { it.uppercase() }, item.releaseInfo).filter { it.isNotBlank() }.joinToString(" · "),
                                        color = TextMuted,
                                        fontSize = 12.sp
                                    )
                                }
                            }
                        }
                    }
                }
            }
        } else {
            val item = selected!!
            Row(Modifier.fillMaxWidth().padding(vertical = 10.dp), verticalAlignment = Alignment.CenterVertically) {
                IconButton(onClick = { controller.stremioSearch(query.ifBlank { item.name }) }) {
                    Icon(Icons.AutoMirrored.Filled.ArrowBack, "Back")
                }
                Column(Modifier.weight(1f)) {
                    Text(item.name, fontWeight = FontWeight.Bold, fontSize = 20.sp)
                    Text(item.type.replaceFirstChar { it.uppercase() }, color = TextMuted, fontSize = 12.sp)
                }
                TextButton(onClick = { controller.openStremioItemInApp(item) }) { Text("Open in Stremio") }
            }

            if (item.description.isNotBlank()) {
                Text(item.description, color = TextMuted, fontSize = 13.sp, maxLines = 4, overflow = TextOverflow.Ellipsis)
                Spacer(Modifier.height(10.dp))
            }

            if (item.type == "series") {
                EmptyState(
                    "Series episode selection",
                    "Episode selection currently hands off to Stremio. Movie search and compatible direct streams are playable inside Home Media."
                )
            } else if (settings.stremioStreamAddonManifests.isEmpty()) {
                EmptyState(
                    "No stream addons configured",
                    "Add one or more Stremio addon manifest URLs in Settings, or open this title in Stremio."
                )
            } else if (streams.isEmpty()) {
                EmptyState("No compatible streams", "The configured addons returned no direct stream for this title.")
            } else {
                LazyColumn(contentPadding = PaddingValues(vertical = 8.dp)) {
                    items(streams.indices.toList()) { idx ->
                        val stream = streams[idx]
                        ListItem(
                            headlineContent = { Text(stream.title.ifBlank { stream.name.ifBlank { "Stream ${idx + 1}" } }) },
                            supportingContent = {
                                Text(
                                    when {
                                        stream.directlyPlayable -> "Direct stream · play here or cast"
                                        stream.externalUrl.isNotBlank() -> "External provider"
                                        stream.infoHash.isNotBlank() -> "Torrent stream · open in Stremio"
                                        else -> "Stremio stream"
                                    },
                                    color = TextMuted
                                )
                            },
                            leadingContent = {
                                Icon(if (stream.directlyPlayable) Icons.Default.PlayCircle else Icons.Default.OpenInNew, null)
                            },
                            modifier = Modifier.combinedClickable(
                                onClick = { controller.requestStremioPlayback(item, stream) },
                                onLongClick = { controller.openStremioItemInApp(item) }
                            )
                        )
                    }
                }
            }
        }
    }
}

@Composable
private fun BluetoothCalibrationDialog(
    state: au.com.homemedia.core.BluetoothCalibrationState,
    onCapture: () -> Unit,
    onDismiss: () -> Unit
) {
    val instruction = when (state.point) {
        1 -> "Stand at one side of the room where you normally use the phone."
        2 -> "Move to the middle of the room, then capture the second point."
        else -> "Move to the opposite side of the room for the final point."
    }
    AlertDialog(
        onDismissRequest = { if (!state.running) onDismiss() },
        title = { Text("Calibrate ${state.roomName}") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Text(
                    if (state.complete) "Calibration complete"
                    else "Point ${state.point} of 3",
                    fontWeight = FontWeight.Bold,
                    color = if (state.complete) ActiveGreen else LocalContentColor.current
                )
                if (!state.complete) Text(instruction)
                Text(
                    "Each point scans Bluetooth for about 5.5 seconds. Three spatial samples are compared when locating the room.",
                    color = TextMuted,
                    fontSize = 12.sp
                )
                if (state.lastSummary.isNotBlank()) {
                    Surface(color = Panel2, shape = RoundedCornerShape(10.dp)) {
                        Text(state.lastSummary, Modifier.padding(10.dp), fontSize = 13.sp)
                    }
                }
                if (state.running) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        CircularProgressIndicator(Modifier.size(22.dp), strokeWidth = 2.dp)
                        Spacer(Modifier.width(10.dp))
                        Text("Scanning… keep the phone roughly still")
                    }
                }
                if (state.complete && state.lastSummary.contains("Bluetooth-quiet", ignoreCase = true)) {
                    Text(
                        "This room has been marked Bluetooth-quiet. Weak signals bleeding in from another room will not pull location away from it.",
                        color = ActiveGreen,
                        fontSize = 12.sp
                    )
                }
            }
        },
        confirmButton = {
            if (state.complete) {
                Button(onClick = onDismiss) { Text("Done") }
            } else {
                Button(onClick = onCapture, enabled = !state.running) {
                    Text("Capture point ${state.point}")
                }
            }
        },
        dismissButton = {
            if (!state.running && !state.complete) TextButton(onClick = onDismiss) { Text("Cancel") }
        }
    )
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
                        Icon(when { target.phone -> Icons.Default.Smartphone; target.kodi -> Icons.Default.Tv; target.cast -> Icons.Default.Cast; else -> Icons.Default.Speaker }, null)
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
    val artworkRoom = controller.kodiLibraryHostRoom() ?: room
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