package au.com.homemedia.core

import android.content.Context
import android.net.Uri
import au.com.homemedia.location.BluetoothLocator
import au.com.homemedia.model.*
import au.com.homemedia.network.HomeAssistantClient
import au.com.homemedia.network.KodiClient
import au.com.homemedia.network.MusicAssistantClient
import au.com.homemedia.network.NewsClient
import au.com.homemedia.network.YouTubeClient
import au.com.homemedia.network.WifiStatus
import au.com.homemedia.network.StremioClient
import au.com.homemedia.storage.SettingsStore
import au.com.homemedia.storage.YouTubeLibraryStore
import au.com.homemedia.storage.LocalVideoStore
import au.com.homemedia.storage.LocalVideoItem
import au.com.homemedia.playback.PhonePlaybackService
import au.com.homemedia.debug.DebugLogger
import android.provider.OpenableColumns
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.*
import org.json.JSONObject
import java.util.zip.ZipEntry
import java.util.zip.ZipInputStream
import java.util.zip.ZipOutputStream

enum class Screen { ROOM, MEDIA, MASS_HOME, MASS_LIST, MASS_DETAIL, QUEUE, KODI, KODI_LIBRARY, NEWS, YOUTUBE, PHONE_VIDEO, STREMIO, SETTINGS }

data class BluetoothCalibrationState(
    val roomId: String = "",
    val roomName: String = "",
    val point: Int = 1,
    val running: Boolean = false,
    val captured: List<BluetoothCalibrationPoint> = emptyList(),
    val lastSummary: String = "",
    val complete: Boolean = false
)

class AppController(context: Context) {
    private val appContext = context.applicationContext
    private val store = SettingsStore(appContext)
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    val ha = HomeAssistantClient()
    val ma = MusicAssistantClient()
    val kodi = KodiClient()
    val youtube = YouTubeClient()
    val stremio = StremioClient(appContext)
    val news = NewsClient()
    private val bluetoothLocator = BluetoothLocator(appContext)
    private val wifiStatus = WifiStatus(appContext)
    private val youtubeStore = YouTubeLibraryStore(appContext)
    private val localVideoStore = LocalVideoStore(appContext)
    private val debugLogger = DebugLogger(appContext)

    private val _settings = MutableStateFlow(store.load())
    val settings: StateFlow<AppSettings> = _settings

    private val _screen = MutableStateFlow(Screen.ROOM)
    val screen: StateFlow<Screen> = _screen

    private val _selectedRoomId = MutableStateFlow(resolveInitialRoomId(_settings.value))
    val selectedRoomId: StateFlow<String> = _selectedRoomId

    private val _nowPlaying = MutableStateFlow(NowPlaying())
    val nowPlaying: StateFlow<NowPlaying> = _nowPlaying

    private val _joinSourceRoomId = MutableStateFlow<String?>(null)
    val joinSourceRoomId: StateFlow<String?> = _joinSourceRoomId

    private val _massCategory = MutableStateFlow<MassCategory?>(null)
    val massCategory: StateFlow<MassCategory?> = _massCategory

    private val _massItems = MutableStateFlow<List<MassMediaItem>>(emptyList())
    val massItems: StateFlow<List<MassMediaItem>> = _massItems

    private val _selectedMassItem = MutableStateFlow<MassMediaItem?>(null)
    val selectedMassItem: StateFlow<MassMediaItem?> = _selectedMassItem

    private val _massChildren = MutableStateFlow<List<MassMediaItem>>(emptyList())
    val massChildren: StateFlow<List<MassMediaItem>> = _massChildren

    private val _queueInfo = MutableStateFlow<MassQueueInfo?>(null)
    val queueInfo: StateFlow<MassQueueInfo?> = _queueInfo

    private val _queueItems = MutableStateFlow<List<MassQueueItem>>(emptyList())
    val queueItems: StateFlow<List<MassQueueItem>> = _queueItems

    private val _kodiNow = MutableStateFlow(KodiNowPlaying())
    val kodiNow: StateFlow<KodiNowPlaying> = _kodiNow

    private val _kodiBrowse = MutableStateFlow(KodiBrowseState())
    val kodiBrowse: StateFlow<KodiBrowseState> = _kodiBrowse
    private val kodiBackStack = mutableListOf<KodiBrowseState>()

    private val _busy = MutableStateFlow(false)
    val busy: StateFlow<Boolean> = _busy

    private val _message = MutableStateFlow<String?>(null)
    val message: StateFlow<String?> = _message

    private val _activePlayerKey = MutableStateFlow(
        _settings.value.rooms.firstOrNull { it.id == _selectedRoomId.value }?.activePlayerKey ?: "primary"
    )
    val activePlayerKey: StateFlow<String> = _activePlayerKey

    private val _newsSections = MutableStateFlow<List<NewsSection>>(emptyList())
    val newsSections: StateFlow<List<NewsSection>> = _newsSections

    private val _pendingMassPlayback = MutableStateFlow<Pair<MassMediaItem, String>?>(null)
    val pendingMassPlayback: StateFlow<Pair<MassMediaItem, String>?> = _pendingMassPlayback

    private val _youtubeResults = MutableStateFlow<List<YouTubeItem>>(emptyList())
    val youtubeResults: StateFlow<List<YouTubeItem>> = _youtubeResults

    private val _youtubeLibrary = MutableStateFlow(youtubeStore.load())
    val youtubeLibrary: StateFlow<YouTubeLibraryStore.State> = _youtubeLibrary

    private val _youtubeSection = MutableStateFlow("Search")
    val youtubeSection: StateFlow<String> = _youtubeSection

    private val _localVideos = MutableStateFlow(localVideoStore.load())
    val localVideos: StateFlow<List<LocalVideoItem>> = _localVideos

    private val _stremioResults = MutableStateFlow<List<StremioMetaItem>>(emptyList())
    val stremioResults: StateFlow<List<StremioMetaItem>> = _stremioResults

    private val _selectedStremio = MutableStateFlow<StremioMetaItem?>(null)
    val selectedStremio: StateFlow<StremioMetaItem?> = _selectedStremio

    private val _stremioStreams = MutableStateFlow<List<StremioStreamItem>>(emptyList())
    val stremioStreams: StateFlow<List<StremioStreamItem>> = _stremioStreams

    private val _pendingStremio = MutableStateFlow<Pair<StremioMetaItem, StremioStreamItem>?>(null)
    val pendingStremio: StateFlow<Pair<StremioMetaItem, StremioStreamItem>?> = _pendingStremio

    private val _wifiConnected = MutableStateFlow(wifiStatus.isConnectedToWifi())
    val wifiConnected: StateFlow<Boolean> = _wifiConnected

    private val _phoneVideo = MutableStateFlow<PhoneVideo?>(null)
    val phoneVideo: StateFlow<PhoneVideo?> = _phoneVideo

    private val _phoneFullscreen = MutableStateFlow(false)
    val phoneFullscreen: StateFlow<Boolean> = _phoneFullscreen

    private val _pendingYoutube = MutableStateFlow<YouTubeItem?>(null)
    val pendingYoutube: StateFlow<YouTubeItem?> = _pendingYoutube

    private val _pendingKodiItem = MutableStateFlow<KodiLibraryItem?>(null)
    val pendingKodiItem: StateFlow<KodiLibraryItem?> = _pendingKodiItem

    private val _bluetoothCalibration = MutableStateFlow<BluetoothCalibrationState?>(null)
    val bluetoothCalibration: StateFlow<BluetoothCalibrationState?> = _bluetoothCalibration
    private var kodiLibraryHostRoomId: String? = null
    private var kodiLibraryShared: Boolean = false
    private var pendingBleRoomId: String? = null
    private var pendingBleConfirmations: Int = 0
    private var mediaEntryRoomId: String? = null
    private var mediaOriginScreen: Screen = Screen.ROOM
    private var lastPresenceStateSignature: String? = null
    private var secondaryDiscoveryJob: Job? = null

    init {
        debugLogger.setEnabled(_settings.value.debugEnabled)
        ma.setDebugLogger { tag, message -> debugLogger.log(tag, message) }
        ha.setDebugLogger { tag, message -> debugLogger.log(tag, message) }
        configureConnections(_settings.value)
        scope.launch {
            ha.states.collect { states ->
                if (_settings.value.automaticRoom && _wifiConnected.value) resolveAutomaticRoom(states)
                if (secondaryDiscoveryJob?.isActive != true && states.isNotEmpty() &&
                    _settings.value.rooms.any { room -> room.secondaryPlayers.any { it.haEntity.isBlank() || !states.containsKey(it.haEntity) || it.maPlayerId.isBlank() } }
                ) {
                    secondaryDiscoveryJob = scope.launch { discoverSecondaryPlayers() }
                }
                val activePlayerChanged = syncActivePlayerForCurrentRoom(states)
                updateNowPlaying(states)
                updateJoinCandidate(states)
                if (activePlayerChanged && _screen.value == Screen.QUEUE) {
                    currentRoom()?.let { room ->
                        scope.launch {
                            runCatching { refreshQueueInternal(resolveActiveQueueForRoom(room)) }
                                .onFailure { debugLogger.log("MA", "Could not refresh queue after active player change: ${it.message}") }
                        }
                    }
                }
            }
        }
    }

    fun close() {
        scope.cancel()
        ha.disconnect()
    }

    fun currentRoom(): RoomConfig? = _settings.value.rooms.firstOrNull { it.id == _selectedRoomId.value }
        ?: _settings.value.rooms.firstOrNull()

    fun activePlayerDisplayName(): String {
        val room = currentRoom() ?: return ""
        val key = _activePlayerKey.value
        return if (key == "primary") {
            room.maPlayerName.ifBlank { room.name }
        } else {
            room.secondaryPlayers.firstOrNull { it.id == key }?.name ?: room.name
        }
    }

    fun bluetoothCalibrationQuality(room: RoomConfig): String = bluetoothLocator.calibrationQuality(room)

    fun roomMediaKind(room: RoomConfig): String? {
        val states = ha.states.value
        if (!isRoomActive(room, states)) return null

        val primary = states[room.primaryPlayerEntity]
        val routed = states[room.routeEntity]
        val source = (routed?.attributes?.optString("source").orEmpty() + " " +
            primary?.attributes?.optString("source").orEmpty()).lowercase()
        val mediaType = listOfNotNull(primary, routed)
            .map { it.attributes.optString("media_content_type").lowercase() }
            .firstOrNull { it.isNotBlank() }
            .orEmpty()
        val title = listOfNotNull(primary, routed)
            .joinToString(" ") { it.attributes.optString("media_title") }
            .lowercase()

        val video = source.contains("kodi") || source.contains("youtube") ||
            source.contains("video") || source.contains("cast") || source.contains("tv") ||
            mediaType in setOf("video", "movie", "episode", "tvshow") ||
            title.contains("youtube")

        return if (video) "video" else "music"
    }

    fun joinSourceRoom(): RoomConfig? = _joinSourceRoomId.value?.let { id -> _settings.value.rooms.firstOrNull { it.id == id } }

    fun clearMessage() { _message.value = null }

    fun setDebugRuntimeEnabled(enabled: Boolean) {
        debugLogger.setEnabled(enabled)
        if (_settings.value.debugEnabled != enabled) {
            persist(_settings.value.copy(debugEnabled = enabled))
        }
        debugLogger.log("DEBUG", "Runtime debug mode=$enabled")
        if (enabled) {
            val settings = _settings.value
            debugLogger.log(
                "SNAPSHOT",
                "wifi=${wifiStatus.isConnectedToWifi()} haConnected=${ha.connected.value} " +
                    "haStates=${ha.states.value.size} selected=${_selectedRoomId.value} " +
                    "automaticRoom=${settings.automaticRoom} bluetooth=${settings.bluetoothLocationEnabled}"
            )
            settings.rooms.forEach { room ->
                val state = room.presenceEntity.takeIf(String::isNotBlank)?.let { ha.states.value[it] }
                debugLogger.log(
                    "SNAPSHOT",
                    "room=${room.name} presence=${room.presenceEntity.ifBlank { "<none>" }} " +
                        "actual=${state?.state ?: "<missing>"} expected=${room.presenceValue.ifBlank { "<auto>" }} " +
                        "ble=${bluetoothLocator.calibrationQuality(room)}"
                )
            }
        }
    }

    fun runHomeAssistantDiagnostic() {
        scope.launch {
            _busy.value = true
            try {
                val settings = _settings.value
                val states = ha.states.value
                debugLogger.log(
                    "HA",
                    "Manual Home Assistant diagnostic started connected=${ha.connected.value} states=${states.size}"
                )
                val lines = settings.rooms.map { room ->
                    val state = states[room.presenceEntity]
                    val matches = presenceMatchesRoom(room, state)
                    val line =
                        "${room.name}: ${room.presenceEntity.ifBlank { "<not configured>" }} " +
                            "state=${state?.state ?: "<missing>"} expected=${room.presenceValue.ifBlank { "<auto>" }} match=$matches"
                    debugLogger.log("HA", line)
                    line
                }
                val missing = settings.rooms.count { it.presenceEntity.isBlank() || states[it.presenceEntity] == null }
                _message.value =
                    "Home Assistant · connected=${ha.connected.value} · ${states.size} states · " +
                        "${settings.rooms.size - missing}/${settings.rooms.size} presence sensors received\n" +
                        lines.joinToString("\n")
            } catch (e: Exception) {
                debugLogger.log("HA", "Diagnostic failed ${e.javaClass.simpleName}: ${e.message}")
                _message.value = "Home Assistant failed: ${e.message ?: e.javaClass.simpleName}"
            } finally {
                _busy.value = false
            }
        }
    }

    fun runMusicAssistantDiagnostic() {
        scope.launch {
            _busy.value = true
            try {
                debugLogger.log("MA", "Manual Music Assistant diagnostic started")
                val queues = ma.queues()
                debugLogger.log("MA", "Queue listing OK count=${queues.size}")

                val categoryCounts = linkedMapOf<MassCategory, Int>()
                MassCategory.entries.forEach { category ->
                    val sample = ma.library(category, limit = 3)
                    categoryCounts[category] = sample.size
                    debugLogger.log(
                        "MA",
                        "Library ${category.label} OK count=${sample.size} sample=${sample.joinToString { it.name }.take(300)}"
                    )
                }

                val normalPage = ma.library(MassCategory.ALBUMS, limit = 250)
                debugLogger.log("MA", "Normal 250-item album page OK count=${normalPage.size}")
                _message.value =
                    "Music Assistant OK · ${queues.size} queues · " +
                        categoryCounts.entries.joinToString { "${it.key.label}=${it.value}" } +
                        " · album page=${normalPage.size}"
            } catch (e: Exception) {
                debugLogger.log("MA", "Diagnostic failed ${e.javaClass.simpleName}: ${e.message}")
                _message.value = "Music Assistant failed: ${e.message ?: e.javaClass.simpleName}"
            } finally {
                _busy.value = false
            }
        }
    }

    fun clearDebugLog() {
        debugLogger.clear()
        _message.value = "Debug log cleared"
    }

    fun exportDebugLog(uri: Uri) {
        scope.launch(Dispatchers.IO) {
            runCatching {
                val settings = _settings.value
                val states = ha.states.value
                val header = buildString {
                    appendLine("Home Media diagnostic log")
                    appendLine("App settings schema: ${settings.schemaVersion}")
                    appendLine("Wi-Fi connected: ${wifiStatus.isConnectedToWifi()}")
                    appendLine("HA connected: ${ha.connected.value}")
                    appendLine("HA states loaded: ${states.size}")
                    appendLine("HA URL: ${settings.homeAssistantUrl}")
                    appendLine("MA URL: ${settings.musicAssistantUrl}")
                    appendLine("Selected room: ${_selectedRoomId.value}")
                    appendLine("Automatic room: ${settings.automaticRoom}")
                    appendLine("Bluetooth room detection: ${settings.bluetoothLocationEnabled}")
                    settings.rooms.forEach { room ->
                        val presenceState = states[room.presenceEntity]
                        appendLine(
                            "Room ${room.name}: presence=${room.presenceEntity} " +
                                "actual=${presenceState?.state ?: "<missing>"} expected=${room.presenceValue}; " +
                                "BLE points=${room.bluetoothCalibrationPoints.size}; quiet=${room.bluetoothQuietRoom}; " +
                                "quality=${bluetoothLocator.calibrationQuality(room)}"
                        )
                    }
                    appendLine("Tokens: REDACTED")
                }
                debugLogger.export(uri, header)
            }.onSuccess {
                withContext(Dispatchers.Main) { _message.value = "Debug log exported" }
            }.onFailure { e ->
                withContext(Dispatchers.Main) { _message.value = e.message ?: "Could not export debug log" }
            }
        }
    }

    fun runLocationDiagnostic() {
        scope.launch {
            _busy.value = true
            try {
                val settings = _settings.value
                val wifi = wifiStatus.isConnectedToWifi()
                debugLogger.log("LOC", "Manual diagnostic started wifi=$wifi selected=${_selectedRoomId.value}")
                if (!wifi) {
                    _message.value = "Location diagnostics are disabled off Wi-Fi"
                    return@launch
                }

                val presenceLines = settings.rooms.map { room ->
                    val st = room.presenceEntity.takeIf(String::isNotBlank)?.let { ha.states.value[it] }
                    val line =
                        "${room.name}: ${room.presenceEntity.ifBlank { "<not configured>" }} " +
                            "state=${st?.state ?: "<missing>"} expected=${room.presenceValue.ifBlank { "<auto>" }}"
                    debugLogger.log("PRESENCE", line)
                    line
                }

                val scan = bluetoothLocator.scanFingerprint(3500)
                val top = scan.samples.take(10).joinToString { "${it.name.ifBlank { it.address }}=${it.rssi}" }
                debugLogger.log("BLE", "Scan devices=${scan.samples.size} duration=${scan.durationMs}ms top=[$top]")

                settings.rooms.forEach { room ->
                    debugLogger.log(
                        "BLE",
                        "Calibration room=${room.name} points=${room.bluetoothCalibrationPoints.size} " +
                            "quiet=${room.bluetoothQuietRoom} quality=${bluetoothLocator.calibrationQuality(room)}"
                    )
                }

                val candidates = bluetoothLocator.scoreRooms(settings.rooms, scan.samples)
                candidates.forEach { score ->
                    val name = settings.rooms.firstOrNull { it.id == score.roomId }?.name ?: score.roomId
                    debugLogger.log(
                        "BLE",
                        "Candidate room=$name score=${"%.1f".format(score.score)} " +
                            "overlap=${score.overlap}/${score.expectedDevices} stable=${score.stableDevices}"
                    )
                }

                val match = bluetoothLocator.resolveRoomFromSamples(settings.rooms, scan.samples)
                val matchText = match?.let {
                    "${settings.rooms.firstOrNull { r -> r.id == it.roomId }?.name ?: it.roomId} " +
                        "score=${"%.1f".format(it.score)} margin=${"%.1f".format(it.confidenceMargin)} " +
                        "overlap=${it.overlap}/${it.expectedDevices}"
                } ?: "no confident BLE match"
                debugLogger.log("BLE", "Prediction from same scan: $matchText")

                _message.value = buildString {
                    append("Presence: ")
                    append(presenceLines.joinToString(" | "))
                    append("\nBLE: ${scan.samples.size} devices; $matchText")
                }
            } catch (e: Exception) {
                debugLogger.log("LOC", "Diagnostic failure: ${e.javaClass.simpleName}: ${e.message}")
                _message.value = e.message ?: "Location diagnostic failed"
            } finally {
                _busy.value = false
            }
        }
    }

    fun goRoom() {
        mediaEntryRoomId = null
        mediaOriginScreen = Screen.ROOM
        _screen.value = Screen.ROOM
    }

    fun goMedia() {
        mediaEntryRoomId = null
        mediaOriginScreen = Screen.MEDIA
        _screen.value = Screen.MEDIA
    }

    fun goSettings() { _screen.value = Screen.SETTINGS }

    private fun beginMediaNavigation(roomScoped: Boolean) {
        mediaOriginScreen = if (roomScoped) Screen.ROOM else when (_screen.value) {
            Screen.MEDIA -> Screen.MEDIA
            else -> Screen.ROOM
        }
        mediaEntryRoomId = if (roomScoped) _selectedRoomId.value else null
    }

    fun openYouTube(section: String = "Search", roomScoped: Boolean = false) {
        beginMediaNavigation(roomScoped)
        _youtubeSection.value = section
        _screen.value = Screen.YOUTUBE
    }

    fun openStremio(roomScoped: Boolean = false) {
        beginMediaNavigation(roomScoped)
        _screen.value = Screen.STREMIO
    }

    fun navigateBack() {
        when (_screen.value) {
            Screen.ROOM -> Unit
            Screen.MEDIA -> goRoom()
            Screen.MASS_DETAIL -> backFromMassDetail()
            Screen.MASS_LIST -> backToMassHome()
            Screen.MASS_HOME, Screen.QUEUE, Screen.NEWS, Screen.YOUTUBE, Screen.STREMIO ->
                if (mediaOriginScreen == Screen.MEDIA) goMedia() else goRoom()
            Screen.KODI_LIBRARY -> {
                if (_kodiBrowse.value.type != KodiBrowseType.HOME || kodiBackStack.isNotEmpty()) {
                    kodiBrowseBack()
                } else if (mediaOriginScreen == Screen.MEDIA) {
                    goMedia()
                } else {
                    goRoom()
                }
            }
            Screen.KODI -> if (mediaOriginScreen == Screen.MEDIA) goMedia() else goRoom()
            Screen.PHONE_VIDEO -> stopPhoneVideo()
            Screen.SETTINGS -> goRoom()
        }
    }
    fun setPhoneFullscreen(enabled: Boolean) { _phoneFullscreen.value = enabled }
    fun stremioBoard() { runCatching { stremio.openBoard() }.onFailure { _message.value = it.message ?: "Stremio is not installed" } }
    fun stremioLibrary() { runCatching { stremio.openLibrary() }.onFailure { _message.value = it.message ?: "Stremio is not installed" } }

    fun stremioSearch(query: String) {
        if (query.isBlank()) return
        scope.launch {
            busyRun("Stremio search failed") {
                _selectedStremio.value = null
                _stremioStreams.value = emptyList()
                _stremioResults.value = stremio.search(query)
                _screen.value = Screen.STREMIO
            }
        }
    }

    fun selectStremioItem(item: StremioMetaItem) {
        _selectedStremio.value = item
        _stremioStreams.value = emptyList()
        if (item.type == "series") return
        scope.launch {
            busyRun("Could not load Stremio streams") {
                _stremioStreams.value = stremio.streams(item, _settings.value.stremioStreamAddonManifests)
            }
        }
    }

    fun openStremioItemInApp(item: StremioMetaItem) {
        runCatching { stremio.openDetail(item) }.onFailure { _message.value = it.message ?: "Stremio is not installed" }
    }

    fun requestStremioPlayback(item: StremioMetaItem, stream: StremioStreamItem) {
        if (!stream.directlyPlayable) {
            if (stream.externalUrl.isNotBlank()) runCatching { stremio.openExternal(stream.externalUrl) }
            else openStremioItemInApp(item)
            return
        }
        _wifiConnected.value = wifiStatus.isConnectedToWifi()
        if (_wifiConnected.value) {
            _pendingStremio.value = item to stream
        } else {
            playStremioOnPhone(item, stream)
        }
    }

    private fun playStremioOnPhone(item: StremioMetaItem, stream: StremioStreamItem) {
        _phoneVideo.value = PhoneVideo(item.name, stream.url)
        PhonePlaybackService.play(appContext, stream.url, item.name)
        _screen.value = Screen.PHONE_VIDEO
    }

    fun confirmStremioPlayback(targetId: String) {
        val pending = _pendingStremio.value ?: return
        _pendingStremio.value = null
        val item = pending.first
        val stream = pending.second
        if (targetId == "phone") {
            playStremioOnPhone(item, stream)
            return
        }
        scope.launch {
            busyRun("Could not start Stremio stream") {
                val target = youtubePlaybackTargets().firstOrNull { it.id == targetId }
                    ?: error("Playback target not found")
                val room = _settings.value.rooms.first { it.id == target.roomId }
                resetRoomToPrimary(room.id)
                when {
                    target.kodi -> kodi.openUrl(room.kodi, stream.url)
                    target.cast -> {
                        val entity = resolveYouTubeCastEntity(room)
                        val data = JSONObject().put("media_content_id", stream.url).put("media_content_type", "video")
                        if (!ha.callService("media_player", "play_media", entity, data)) error("Home Assistant is not connected")
                    }
                }
            }
        }
    }

    fun cancelPendingStremio() { _pendingStremio.value = null }
    fun stopPhoneVideo() { _phoneVideo.value = null; _screen.value = Screen.YOUTUBE }

    fun beginBluetoothCalibration(roomId: String) {
        _wifiConnected.value = wifiStatus.isConnectedToWifi()
        if (!_wifiConnected.value) {
            _message.value = "Bluetooth room calibration is disabled when not connected to Wi‑Fi"
            return
        }
        val room = _settings.value.rooms.firstOrNull { it.id == roomId } ?: return
        _bluetoothCalibration.value = BluetoothCalibrationState(roomId = room.id, roomName = room.name)
    }

    fun cancelBluetoothCalibration() {
        _bluetoothCalibration.value = null
    }

    fun captureBluetoothCalibrationPoint() {
        _wifiConnected.value = wifiStatus.isConnectedToWifi()
        if (!_wifiConnected.value) {
            _message.value = "Bluetooth room calibration is disabled when not connected to Wi‑Fi"
            return
        }
        val state = _bluetoothCalibration.value ?: return
        if (state.running || state.complete) return
        scope.launch {
            _bluetoothCalibration.value = state.copy(running = true, lastSummary = "Scanning…")
            val scan = bluetoothLocator.scanFingerprint(5500)
            val useful = scan.samples.filter { it.rssi >= -92 }.take(12)
            val strong = useful.count { it.rssi >= -62 }
            val point = BluetoothCalibrationPoint(point = state.point, samples = useful)
            val captured = state.captured.filterNot { it.point == state.point } + point
            val summary = when {
                useful.isEmpty() -> "No BLE devices detected"
                strong == 0 -> "${useful.size} weak devices detected; no strong local beacon"
                else -> "${useful.size} devices detected, $strong strong"
            }

            if (state.point < 3) {
                _bluetoothCalibration.value = state.copy(
                    point = state.point + 1,
                    running = false,
                    captured = captured.sortedBy { it.point },
                    lastSummary = summary
                )
            } else {
                val finalPoints = captured.sortedBy { it.point }.take(3)
                val strongAcross = finalPoints.flatMap { it.samples }.count { it.rssi >= -62 }
                val quiet = strongAcross == 0
                val settings = _settings.value
                val updatedRooms = settings.rooms.map { room ->
                    if (room.id == state.roomId) room.copy(
                        bluetoothCalibrationPoints = finalPoints,
                        bluetoothQuietRoom = quiet
                    ) else room
                }
                persist(settings.copy(rooms = updatedRooms))
                _bluetoothCalibration.value = state.copy(
                    point = 3,
                    running = false,
                    captured = finalPoints,
                    lastSummary = if (quiet) "$summary · marked as Bluetooth-quiet" else summary,
                    complete = true
                )
            }
        }
    }

    fun selectRoom(id: String) {
        val room = _settings.value.rooms.firstOrNull { it.id == id } ?: return
        _selectedRoomId.value = id
        _activePlayerKey.value = room.activePlayerKey.takeIf { key ->
            key == "primary" || room.secondaryPlayers.any { it.id == key }
        } ?: "primary"
        persist(_settings.value.copy(lastRoomId = id))
        mediaEntryRoomId = null
        mediaOriginScreen = Screen.ROOM
        _screen.value = Screen.ROOM
        syncActivePlayerForCurrentRoom(ha.states.value)
        updateNowPlaying(ha.states.value)
        updateJoinCandidate(ha.states.value)
    }

    fun selectActivePlayer(key: String) {
        val room = currentRoom() ?: return
        if (key != "primary" && room.secondaryPlayers.none { it.id == key }) return
        _activePlayerKey.value = key
        persistActivePlayer(room.id, key)
        updateNowPlaying(ha.states.value)
        if (_screen.value == Screen.QUEUE) refreshQueue()
    }

    private fun persistActivePlayer(roomId: String, key: String) {
        val settings = _settings.value
        val updated = settings.rooms.map { room ->
            if (room.id == roomId) room.copy(activePlayerKey = key) else room
        }
        if (updated != settings.rooms) persist(settings.copy(rooms = updated))
    }

    private fun resetRoomToPrimary(roomId: String) {
        if (_selectedRoomId.value == roomId) _activePlayerKey.value = "primary"
        persistActivePlayer(roomId, "primary")
    }

    fun saveSettings(newSettings: AppSettings) {
        val old = _settings.value
        debugLogger.setEnabled(newSettings.debugEnabled)
        persist(newSettings)
        debugLogger.log("SETTINGS", "Saved settings debug=${newSettings.debugEnabled} automaticRoom=${newSettings.automaticRoom} bluetooth=${newSettings.bluetoothLocationEnabled}")
        if (newSettings.automaticRoom) {
            val missingPresence = newSettings.rooms.filter { it.presenceEntity.isBlank() }
            if (missingPresence.isNotEmpty()) {
                debugLogger.log("PRESENCE", "Automatic room enabled but presence is not configured for: " + missingPresence.joinToString { it.name })
            }
        }
        if (_selectedRoomId.value.isBlank() || newSettings.rooms.none { it.id == _selectedRoomId.value }) {
            _selectedRoomId.value = resolveInitialRoomId(newSettings)
        }
        if (old.homeAssistantUrl != newSettings.homeAssistantUrl || old.homeAssistantToken != newSettings.homeAssistantToken ||
            old.musicAssistantUrl != newSettings.musicAssistantUrl || old.musicAssistantToken != newSettings.musicAssistantToken
        ) configureConnections(newSettings)
        if (newSettings.automaticRoom && wifiStatus.isConnectedToWifi()) {
            resolveAutomaticRoom(ha.states.value)
        }
        updateNowPlaying(ha.states.value)
        updateJoinCandidate(ha.states.value)
    }

    fun executeTile(tile: TileConfig) {
        val room = currentRoom() ?: return
        when (tile.actionType) {
            TileActionType.OPEN_LIBRARY -> openLibrary(roomScoped = true)
            TileActionType.OPEN_KODI -> {
                beginMediaNavigation(roomScoped = true)
                openKodiLibrary(shared = false)
            }
            TileActionType.OPEN_QUEUE -> openQueue(roomScoped = true)
            TileActionType.OPEN_YOUTUBE -> openYouTube("Search", roomScoped = true)
            TileActionType.SELECT_SOURCE -> if (tile.source.isNotBlank()) selectRoomSource(tile.source)
            TileActionType.HA_SERVICE -> scope.launch {
                busyRun("Home Assistant action failed") {
                    if (tile.service.domain == "media_player" && tile.service.service == "select_source") {
                        ensureRoomOn(room)
                    }
                    callServiceSpec(room, tile.service)
                }
            }
        }
    }

    fun togglePlayPause() { activeTransportEntity()?.takeIf(String::isNotBlank)?.let(ha::playPause) }
    fun next() { activeTransportEntity()?.takeIf(String::isNotBlank)?.let(ha::next) }
    fun previous() { activeTransportEntity()?.takeIf(String::isNotBlank)?.let(ha::previous) }
    fun stop() { activeTransportEntity()?.takeIf(String::isNotBlank)?.let(ha::stop) }
    fun volumeUp() { activeVolumeEntity()?.takeIf(String::isNotBlank)?.let(ha::volumeUp) }
    fun volumeDown() { activeVolumeEntity()?.takeIf(String::isNotBlank)?.let(ha::volumeDown) }

    fun hardwareVolumeUp() { hardwareVolumeEntity()?.let(ha::volumeUp) }
    fun hardwareVolumeDown() { hardwareVolumeEntity()?.let(ha::volumeDown) }

    fun allOff() {
        scope.launch {
            busyRun("All Off failed") {
                val states = ha.states.value
                val script = states.values.firstOrNull { state ->
                    if (!state.entityId.startsWith("script.")) return@firstOrNull false
                    val id = state.entityId.lowercase()
                    val name = state.attributes.optString("friendly_name").lowercase()
                    id.contains("b_o_all_off") || id.contains("bo_all_off") ||
                        name.replace("&", "and").contains("b and o all off") ||
                        name.contains("b&o all off")
                }?.entityId
                if (script != null) {
                    if (!ha.callService("script", "turn_on", script)) error("Home Assistant is not connected")
                } else {
                    debugLogger.log("HA", "All Off script not found")
                }

                val configuredTargets = buildSet {
                    add(_settings.value.linkMediaPlayerEntity)
                    add("media_player.cuisine")
                    add("media_player.bedroomcast")
                    _settings.value.rooms.flatMap { it.secondaryPlayers }.forEach { secondary ->
                        if (secondary.name.equals("Lounge Mini", true) || secondary.name.equals("Bedroom Mini", true)) {
                            add(secondary.haEntity)
                        }
                    }
                    states.values
                        .filter { it.entityId.startsWith("media_player.") }
                        .filter { state ->
                            val id = state.entityId.lowercase().replace("_", " ")
                            val name = state.attributes.optString("friendly_name").lowercase()
                            id.contains("lounge mini") || name.contains("lounge mini") ||
                                id.contains("bedroom mini") || name.contains("bedroom mini") ||
                                id.contains("bedroomcast") || id.contains("bedroom cast") ||
                                name.contains("bedroomcast") || name.contains("bedroom cast")
                        }
                        .forEach { add(it.entityId) }
                }.filter(String::isNotBlank)

                configuredTargets.forEach { entity ->
                    if (states.containsKey(entity)) {
                        ha.stop(entity)
                        debugLogger.log("HA", "All Off media_stop target=$entity")
                    } else {
                        debugLogger.log("HA", "All Off target missing from HA states=$entity")
                    }
                }
                _message.value = if (script != null) "All Off sent" else "Media stopped; B&O All Off script was not found"
            }
        }
    }

    fun roomSourceOptions(room: RoomConfig): List<String> {
        val dynamic = ha.states.value[room.routeEntity]?.attributes?.optJSONArray("source_list")
        val fromHa = if (dynamic != null) {
            (0 until dynamic.length()).mapNotNull { i -> dynamic.optString(i).takeIf(String::isNotBlank) }
        } else emptyList()
        return (fromHa + room.sourceOptions).distinctBy { it.lowercase() }
    }

    fun selectRoomSource(source: String) {
        val room = currentRoom() ?: return
        if (source.isBlank()) return
        scope.launch {
            busyRun("Could not select $source") {
                resetRoomToPrimary(room.id)
                ensureRoomOn(room)
                if (!ha.selectSource(room.routeEntity, source)) error("Home Assistant is not connected")
            }
        }
    }

    // ----- Context-sensitive room Join / Transfer -----

    fun joinActiveRoom() {
        val source = joinSourceRoom() ?: return
        val target = currentRoom() ?: return
        scope.launch {
            busyRun("Could not join ${source.name}") {
                if (source.maPlayerId.isNotBlank() && target.maPlayerId.isNotBlank()) {
                    ensureRoomOn(target)
                    ma.playerGroup(target.maPlayerId, source.maPlayerId)
                } else {
                    val sourceKind = sharedSourceForRoom(source, ha.states.value)
                    prepareMlgwSource(target, sourceKind)
                }
            }
        }
    }

    /**
     * Move playback from source room to current room.
     * Native MA -> real MA queue transfer.
     * Anything else -> MLGW handover: target ON + same Link/A.AUX confirmed, then source OFF.
     */
    fun transferFromRoom(sourceRoomId: String) {
        val source = _settings.value.rooms.firstOrNull { it.id == sourceRoomId } ?: return
        val target = currentRoom() ?: return
        if (source.id == target.id) return
        scope.launch {
            busyRun("Transfer from ${source.name} failed") {
                if (source.maPlayerId.isNotBlank() && target.maPlayerId.isNotBlank() && source.maPlayerId != target.maPlayerId) {
                    ensureRoomOn(target)
                    val sourceQueue = ma.resolvePlayerQueueId(source.maPlayerId)
                    val targetQueue = ma.resolvePlayerQueueId(target.maPlayerId)
                    ma.transferQueue(sourceQueue, targetQueue, autoPlay = true)
                } else {
                    val sourceKind = sharedSourceForRoom(source, ha.states.value)
                    prepareMlgwSource(target, sourceKind)
                    val sourceEntity = source.routeEntity
                    if (sourceEntity.isBlank()) error("${source.name} has no MLGW/HA room entity configured")
                    if (!ha.turnOff(sourceEntity)) error("Home Assistant is not connected")
                }
            }
        }
    }

    fun pauseRoom(roomId: String) {
        val room = _settings.value.rooms.firstOrNull { it.id == roomId } ?: return
        scope.launch {
            runCatching {
                if (room.maPlayerId.isNotBlank()) ma.playerPause(room.maPlayerId)
                else transportEntity(room).takeIf { it.isNotBlank() }?.let { if (!ha.pause(it)) error("Home Assistant is not connected") }
            }.onFailure { _message.value = it.message ?: "Could not pause ${room.name}" }
        }
    }

    fun turnOffRoom(roomId: String) {
        val room = _settings.value.rooms.firstOrNull { it.id == roomId } ?: return
        val entity = room.routeEntity
        if (entity.isBlank()) { _message.value = "${room.name} has no MLGW/HA entity configured"; return }
        if (!ha.turnOff(entity)) _message.value = "Home Assistant is not connected"
    }

    // ----- In-room secondary players -----

    fun availableSources(room: RoomConfig): List<String> {
        val discovered = ha.states.value[room.routeEntity]?.attributes?.optJSONArray("source_list")?.let { arr ->
            (0 until arr.length()).mapNotNull { i -> arr.optString(i).takeIf(String::isNotBlank) }
        }.orEmpty()
        return (room.sourceOptions + discovered).distinctBy { it.lowercase() }
    }

    fun secondaryPlayer(id: String): SecondaryPlayerConfig? = currentRoom()?.secondaryPlayers?.firstOrNull { it.id == id }

    fun toggleSecondaryPlayer(id: String) {
        val room = currentRoom() ?: return
        val secondary = room.secondaryPlayers.firstOrNull { it.id == id } ?: return
        scope.launch {
            busyRun("Could not switch to ${secondary.name}") {
                refreshBluetoothLocation()
                if (_activePlayerKey.value == secondary.id) {
                    val secondaryPlayerId = ma.resolvePlayerId(secondary.maPlayerId, secondary.maPlayerName)
                    val sourceQueue = ma.resolvePlayerQueueId(secondaryPlayerId)
                    val hasQueue = ma.queueItems(sourceQueue, limit = 1).isNotEmpty()
                    val primaryPlayerId = if (room.maPlayerId.isNotBlank() || room.maPlayerName.isNotBlank()) {
                        ma.resolvePlayerId(room.maPlayerId, room.maPlayerName)
                    } else ""
                    if (hasQueue) {
                        val targetQueue = if (primaryPlayerId.isNotBlank()) {
                            ma.resolvePlayerQueueId(primaryPlayerId)
                        } else {
                            prepareMlgwSource(room, room.sharedPlaybackSource)
                            ma.resolveQueueId(_settings.value.sharedMaQueueId, _settings.value.sharedMaQueueName)
                        }
                        ma.transferQueue(sourceQueue, targetQueue, autoPlay = true)
                    } else {
                        ma.playerStop(secondaryPlayerId)
                    }
                    _activePlayerKey.value = "primary"
                    persistActivePlayer(room.id, "primary")
                } else {
                    val targetPlayerId = ma.resolvePlayerId(secondary.maPlayerId, secondary.maPlayerName)
                    val targetQueue = ma.resolvePlayerQueueId(targetPlayerId)
                    val sourceQueue = resolveActiveQueueForRoom(room)
                    if (sourceQueue.isNotBlank() && sourceQueue != targetQueue) {
                        ma.transferQueue(sourceQueue, targetQueue, autoPlay = true)
                    }
                    _activePlayerKey.value = secondary.id
                    persistActivePlayer(room.id, secondary.id)
                }
                updateNowPlaying(ha.states.value)
            }
        }
    }

    fun secondaryPause(id: String) {
        val s = secondaryPlayer(id) ?: return
        scope.launch {
            runCatching { ma.playerPause(ma.resolvePlayerId(s.maPlayerId, s.maPlayerName)) }
                .onFailure { _message.value = it.message }
        }
    }

    fun secondaryStop(id: String) {
        val s = secondaryPlayer(id) ?: return
        scope.launch {
            runCatching { ma.playerStop(ma.resolvePlayerId(s.maPlayerId, s.maPlayerName)) }
                .onFailure { _message.value = it.message }
        }
    }

    fun toggleSecondaryAux(id: String) {
        val s = secondaryPlayer(id) ?: return
        val configured = s.toggleEntity
        val resolved = configured.takeIf { it.isNotBlank() && ha.states.value.containsKey(it) }
            ?: ha.states.value.values.firstOrNull { state ->
                if (!state.entityId.startsWith("switch.")) return@firstOrNull false
                val idText = state.entityId.lowercase()
                val name = state.attributes.optString("friendly_name").lowercase()
                idText.contains("bc9500") || name.contains("bc9500")
            }?.entityId
            ?: configured
        if (resolved.isBlank()) {
            _message.value = "BC9500 switch was not found. Set its entity in Dining settings."
            return
        }
        if (!ha.callService("homeassistant", "toggle", resolved)) _message.value = "Home Assistant is not connected"
    }

    // ----- News + dynamic play -----

    fun openNews() {
        mediaOriginScreen = Screen.ROOM
        mediaEntryRoomId = _selectedRoomId.value
        scope.launch {
            busyRun("Could not load BS5c news") {
                _newsSections.value = news.sections(_settings.value.beosound5cUrl)
                _screen.value = Screen.NEWS
            }
        }
    }

    fun readNewsArticle(article: NewsArticle) {
        val room = currentRoom() ?: return
        scope.launch {
            busyRun("Could not read news article") {
                val mediaEntity = activeVolumeEntity().orEmpty()
                val ttsEntity = ha.states.value.values.firstOrNull { it.entityId.startsWith("tts.") }?.entityId.orEmpty()
                val message = listOf(article.title, article.body).filter { it.isNotBlank() }.joinToString(". ").take(12000)
                val sentToRoom = ttsEntity.isNotBlank() && mediaEntity.isNotBlank() &&
                    ha.callService(
                        "tts", "speak", ttsEntity,
                        JSONObject().put("media_player_entity_id", mediaEntity).put("message", message)
                    )
                if (!sentToRoom) {
                    debugLogger.log("NEWS", "HA TTS unavailable; using BS5c local fallback")
                    news.readOnBs5c(_settings.value.beosound5cUrl, article, room.name)
                }
            }
        }
    }

    fun playDynamicTile() {
        val room = currentRoom() ?: return
        scope.launch {
            busyRun("Could not start dynamic play") {
                val hour = java.time.LocalTime.now().hour
                if (hour < 10) {
                    val scriptEntity = "script.play_the_morning_news"
                    val ok = ha.states.value.containsKey(scriptEntity) &&
                        ha.callService("script", "turn_on", scriptEntity)
                    if (!ok) openNews()
                    return@busyRun
                }

                val weather = ha.states.value.values.firstOrNull { it.entityId.startsWith("weather.") }
                val condition = weather?.state.orEmpty().lowercase()
                val temperature = weather?.attributes?.optDouble("temperature", Double.NaN) ?: Double.NaN
                val moodTerms = when {
                    condition.contains("rain") || condition.contains("storm") -> listOf("chill", "acoustic", "downtempo")
                    condition.contains("sun") || condition.contains("clear") || (!temperature.isNaN() && temperature >= 24) ->
                        listOf("summer", "upbeat", "feel good")
                    !temperature.isNaN() && temperature <= 13 -> listOf("warm", "soul", "jazz")
                    hour >= 18 -> listOf("evening", "dinner", "chill")
                    else -> listOf("mix", "indie", "discover")
                }
                val recentQueue = runCatching {
                    val currentQueue = resolveActiveQueueForRoom(room)
                    ma.queueItems(currentQueue, limit = 12)
                }.getOrDefault(emptyList())
                val recentNames = (recentQueue.map { it.name.lowercase() } + _nowPlaying.value.title.lowercase())
                    .filter { it.isNotBlank() }
                val recentHint = recentQueue.asReversed()
                    .map { it.subtitle.substringBefore(" · ").substringBefore(" - ").trim() }
                    .firstOrNull { it.length >= 3 }
                    .orEmpty()

                var candidates = emptyList<MassMediaItem>()
                for (term in moodTerms) {
                    candidates = ma.library(MassCategory.PLAYLISTS, search = term, limit = 30)
                    if (candidates.isNotEmpty()) break
                }
                if (candidates.isEmpty() && recentHint.isNotBlank()) {
                    candidates = ma.library(MassCategory.PLAYLISTS, search = recentHint, limit = 30)
                }
                if (candidates.isEmpty()) candidates = ma.library(MassCategory.PLAYLISTS, limit = 50)
                if (candidates.isEmpty()) candidates = ma.library(MassCategory.ALBUMS, limit = 50)
                if (candidates.isEmpty()) candidates = ma.library(MassCategory.TRACKS, limit = 50)
                val pick = candidates.firstOrNull { item ->
                    item.playable && recentNames.none { recent -> recent.isNotBlank() && item.name.lowercase().contains(recent) }
                } ?: candidates.firstOrNull { it.playable }
                    ?: error("No playable Music Assistant fallback was found")
                val queue = prepareActiveQueueForRoom(room)
                ma.play(queue, pick.uri, "replace")
                debugLogger.log("MA", "Dynamic play weather=$condition temp=$temperature mood=${moodTerms.first()} recentHint=$recentHint picked=${pick.name}")
                delay(250)
                refreshQueueInternal(queue)
            }
        }
    }

    // ----- Music Assistant library -----

    fun openLibrary(roomScoped: Boolean = false) {
        beginMediaNavigation(roomScoped)
        val np = _nowPlaying.value
        scope.launch {
            _busy.value = true
            try {
                if (np.isActive && np.album.isNotBlank()) {
                    val current = ma.findAlbum(np.album, np.artist)
                    if (current != null) {
                        openMassDetailInternal(current)
                        return@launch
                    }
                }
                _massCategory.value = null
                _massItems.value = emptyList()
                _selectedMassItem.value = null
                _massChildren.value = emptyList()
                _screen.value = Screen.MASS_HOME
            } catch (e: Exception) {
                _message.value = e.message ?: "Could not load Music Assistant"
                _screen.value = Screen.MASS_HOME
            } finally { _busy.value = false }
        }
    }

    fun openMassCategory(category: MassCategory, search: String = "") {
        scope.launch {
            busyRun("Could not load ${category.label}") {
                _massCategory.value = category
                _massItems.value = ma.library(category, search)
                _screen.value = Screen.MASS_LIST
            }
        }
    }

    fun searchMass(query: String) {
        val category = _massCategory.value ?: return
        openMassCategory(category, query)
    }

    fun openMassItem(item: MassMediaItem) {
        scope.launch {
            busyRun("Could not open ${item.name}") { openMassDetailInternal(item) }
        }
    }

    fun backFromMassDetail() {
        _selectedMassItem.value = null
        _massChildren.value = emptyList()
        _screen.value = Screen.MASS_LIST
    }

    fun backToMassHome() {
        _massCategory.value = null
        _massItems.value = emptyList()
        _screen.value = Screen.MASS_HOME
    }

    fun playMassItem(item: MassMediaItem, option: String = "replace") {
        val roomId = mediaEntryRoomId
        if (roomId == null) {
            _pendingMassPlayback.value = item to option
            return
        }
        val room = _settings.value.rooms.firstOrNull { it.id == roomId } ?: return
        val activeSecondary = room.secondaryPlayers.firstOrNull { it.id == _activePlayerKey.value }
        val targetId = if (activeSecondary != null) {
            "secondary:${room.id}:${activeSecondary.id}"
        } else {
            "room:${room.id}"
        }
        _pendingMassPlayback.value = item to option
        confirmMassPlayback(targetId)
    }

    fun cancelPendingPlayback() {
        _pendingMassPlayback.value = null
        _pendingYoutube.value = null
        _pendingKodiItem.value = null
    }

    fun playbackTargets(includeKodi: Boolean = false): List<PlaybackTarget> = _settings.value.rooms.flatMap { room ->
        buildList {
            add(PlaybackTarget("room:${room.id}", room.name, room.id))
            room.secondaryPlayers.forEach { s ->
                add(PlaybackTarget("secondary:${room.id}:${s.id}", "${room.name} · ${s.name}", room.id, s.id))
            }
            if (includeKodi && room.kodi.baseUrl.isNotBlank()) {
                add(PlaybackTarget("kodi:${room.id}", "${room.name} · Kodi", room.id, kodi = true))
            }
        }
    }

    fun youtubePlaybackTargets(): List<PlaybackTarget> = buildList {
        add(PlaybackTarget("phone", "This phone", phone = true))
        _settings.value.rooms.forEach { room ->
            if (room.kodi.baseUrl.isNotBlank()) add(PlaybackTarget("kodi:${room.id}", "${room.name} · Kodi", room.id, kodi = true))
            resolveYouTubeCastEntityOrNull(room)?.let { entity ->
                val friendly = ha.states.value[entity]?.attributes?.optString("friendly_name").orEmpty()
                add(PlaybackTarget("cast:${room.id}", friendly.ifBlank { "${room.name} · Cast" }, room.id, cast = true))
            }
        }
    }

    fun confirmMassPlayback(targetId: String) {
        val pending = _pendingMassPlayback.value ?: return
        _pendingMassPlayback.value = null
        scope.launch {
            busyRun("Could not start ${pending.first.name}") {
                refreshBluetoothLocation()
                val q = queueForTarget(targetId)
                ma.play(q, pending.first.uri, pending.second)
                delay(250)
                refreshQueueInternal(q)
            }
        }
    }

    // ----- Music Assistant queue -----

    fun openQueue(roomScoped: Boolean = false) {
        beginMediaNavigation(roomScoped)
        val room = currentRoom() ?: return
        scope.launch {
            busyRun("Could not load Music Assistant queue") {
                val queueId = resolveActiveQueueForRoom(room)
                refreshQueueInternal(queueId)
                _screen.value = Screen.QUEUE
            }
        }
    }

    fun refreshQueue() {
        val room = currentRoom() ?: return
        scope.launch {
            busyRun("Could not refresh queue") { refreshQueueInternal(resolveActiveQueueForRoom(room)) }
        }
    }

    fun queuePlay(item: MassQueueItem) = queueMutation(preparePlayback = true) { q -> ma.queuePlayIndex(q, item.index) }
    fun queueMoveUp(item: MassQueueItem) = queueMutation { q -> ma.queueMove(q, item.queueItemId, -1) }
    fun queueMoveDown(item: MassQueueItem) = queueMutation { q -> ma.queueMove(q, item.queueItemId, 1) }
    fun queueMoveNext(item: MassQueueItem) = queueMutation { q -> ma.queueMove(q, item.queueItemId, 0) }
    fun queueMoveEnd(item: MassQueueItem) = queueMutation { q -> ma.queueMoveEnd(q, item.queueItemId) }
    fun queueDelete(item: MassQueueItem) = queueMutation { q -> ma.queueDelete(q, item.queueItemId) }
    fun queueClear() = queueMutation { q -> ma.queueClear(q) }
    fun queueSkip(seconds: Int) = queueMutation(refresh = false) { q -> ma.queueSkip(q, seconds) }
    fun queueSeek(seconds: Double) = queueMutation(refresh = false) { q -> ma.queueSeek(q, seconds) }
    fun queueShuffle(enabled: Boolean) = queueMutation { q -> ma.queueShuffle(q, enabled) }
    fun queueCrossfade(enabled: Boolean) = queueMutation { q -> ma.queueCrossfade(q, enabled) }
    fun queueAutoplay(enabled: Boolean) = queueMutation { q -> ma.queueAutoplay(q, enabled) }
    fun queueRepeat(mode: String) = queueMutation { q -> ma.queueRepeat(q, mode) }
    fun queuePlaybackSpeed(item: MassQueueItem, speed: Double) = queueMutation { q -> ma.queueSetPlaybackSpeed(q, item.queueItemId, speed) }
    fun queueOverlay(enabled: Boolean, source: String, volume: Int?) = queueMutation { q -> ma.queueOverlay(q, enabled, source, volume) }
    fun queueSaveAsPlaylist(name: String) = queueMutation(refresh = false) { q -> ma.queueSaveAsPlaylist(q, name) }
    fun queuePlayPause() = queuePlayerMutation { q -> ma.playerPlayPause(q) }
    fun queueNext() = queuePlayerMutation { q -> ma.playerNext(q) }
    fun queuePrevious() = queuePlayerMutation { q -> ma.playerPrevious(q) }
    fun queueStop() = queuePlayerMutation { q -> ma.playerStop(q) }

    private fun queuePlayerMutation(command: suspend (String) -> Unit) {
        val room = currentRoom() ?: return
        scope.launch {
            busyRun("Queue playback command failed") {
                val q = resolveActiveQueueForRoom(room)
                command(q)
                delay(120)
                refreshQueueInternal(q)
            }
        }
    }

    private fun queueMutation(
        refresh: Boolean = true,
        preparePlayback: Boolean = false,
        command: suspend (String) -> Unit
    ) {
        val room = currentRoom() ?: return
        scope.launch {
            busyRun("Queue command failed") {
                val q = if (preparePlayback) prepareActiveQueueForRoom(room) else resolveActiveQueueForRoom(room)
                command(q)
                if (refresh) { delay(120); refreshQueueInternal(q) }
            }
        }
    }

    // ----- Kodi remote + complete library -----

    fun openKodi() {
        if (_screen.value == Screen.ROOM) {
            mediaOriginScreen = Screen.ROOM
            mediaEntryRoomId = _selectedRoomId.value
        }
        _screen.value = Screen.KODI
        refreshKodi()
    }

    fun addLocalVideo(uri: Uri) {
        runCatching {
            appContext.contentResolver.takePersistableUriPermission(
                uri,
                android.content.Intent.FLAG_GRANT_READ_URI_PERMISSION
            )
        }
        val name = runCatching {
            appContext.contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use { cursor ->
                if (cursor.moveToFirst()) cursor.getString(0) else null
            }
        }.getOrNull().orEmpty().ifBlank { uri.lastPathSegment ?: "Local video" }
        val item = LocalVideoItem(uri.toString(), name)
        localVideoStore.add(item)
        _localVideos.value = localVideoStore.load()
    }

    fun removeLocalVideo(uri: String) {
        localVideoStore.remove(uri)
        _localVideos.value = localVideoStore.load()
    }

    fun playLocalVideo(item: LocalVideoItem) {
        _phoneVideo.value = PhoneVideo(item.name, item.uri)
        PhonePlaybackService.play(appContext, item.uri, item.name)
        _screen.value = Screen.PHONE_VIDEO
    }

    fun searchYouTube(query: String) {
        scope.launch {
            busyRun("YouTube search failed") {
                _youtubeResults.value = youtube.search(query)
                _screen.value = Screen.YOUTUBE
            }
        }
    }

    fun openSavedYouTubeChannel(channel: YouTubeSavedChannel) {
        scope.launch {
            busyRun("Could not load ${channel.name}") {
                _youtubeResults.value = youtube.channelVideos(channel.url)
                _screen.value = Screen.YOUTUBE
            }
        }
    }

    fun saveYouTubeChannel(item: YouTubeItem) {
        if (item.channelUrl.isBlank()) { _message.value = "This result did not expose a channel URL"; return }
        val state = _youtubeLibrary.value
        val channel = YouTubeSavedChannel(item.channel, item.channelUrl)
        if (state.channels.none { it.url == channel.url }) {
            val updated = state.copy(channels = state.channels + channel)
            _youtubeLibrary.value = updated
            youtubeStore.save(updated)
        }
    }

    fun removeYouTubeChannel(url: String) {
        val state = _youtubeLibrary.value
        val updated = state.copy(channels = state.channels.filterNot { it.url == url })
        _youtubeLibrary.value = updated
        youtubeStore.save(updated)
    }

    fun createYouTubePlaylist(name: String) {
        if (name.isBlank()) return
        val state = _youtubeLibrary.value
        val updated = state.copy(playlists = state.playlists + YouTubePlaylist(name = name.trim()))
        _youtubeLibrary.value = updated
        youtubeStore.save(updated)
    }

    fun addYouTubeToPlaylist(playlistId: String, item: YouTubeItem) {
        val state = _youtubeLibrary.value
        val updated = state.copy(playlists = state.playlists.map { p ->
            if (p.id == playlistId && p.videos.none { it.videoId == item.videoId }) p.copy(videos = p.videos + item) else p
        })
        _youtubeLibrary.value = updated
        youtubeStore.save(updated)
    }

    fun removeYouTubeFromPlaylist(playlistId: String, videoId: String) {
        val state = _youtubeLibrary.value
        val updated = state.copy(playlists = state.playlists.map { p ->
            if (p.id == playlistId) p.copy(videos = p.videos.filterNot { it.videoId == videoId }) else p
        })
        _youtubeLibrary.value = updated
        youtubeStore.save(updated)
    }

    fun clearYouTubeHistory() {
        val state = _youtubeLibrary.value.copy(history = emptyList())
        _youtubeLibrary.value = state
        youtubeStore.save(state)
    }

    private fun markYouTubeWatched(item: YouTubeItem) {
        val state = _youtubeLibrary.value
        val updatedHistory = (listOf(item) + state.history.filterNot { it.videoId == item.videoId }).take(200)
        val updated = state.copy(history = updatedHistory)
        _youtubeLibrary.value = updated
        youtubeStore.save(updated)
    }

    fun requestYouTubePlayback(item: YouTubeItem) {
        _wifiConnected.value = wifiStatus.isConnectedToWifi()
        if (!_wifiConnected.value) {
            startPhoneYouTube(item)
            return
        }

        val roomId = mediaEntryRoomId
        if (roomId == null) {
            _pendingYoutube.value = item
            return
        }

        val room = _settings.value.rooms.firstOrNull { it.id == roomId }
        if (room == null) {
            _pendingYoutube.value = item
            return
        }

        val targetId = resolveYouTubeCastEntityOrNull(room)?.let { "cast:${room.id}" }
            ?: room.kodi.baseUrl.takeIf(String::isNotBlank)?.let { "kodi:${room.id}" }

        if (targetId == null) {
            _message.value = "No YouTube playback target is configured for ${room.name}"
            return
        }

        _pendingYoutube.value = item
        confirmYouTubePlayback(targetId)
    }

    private fun startPhoneYouTube(item: YouTubeItem) {
        scope.launch {
            busyRun("Could not play YouTube on phone") {
                val stream = youtube.directPlaybackUrl(item.videoId)
                markYouTubeWatched(item)
                _phoneVideo.value = PhoneVideo(item.title, stream, item)
                PhonePlaybackService.play(appContext, stream, item.title)
                _screen.value = Screen.PHONE_VIDEO
            }
        }
    }

    fun confirmYouTubePlayback(targetId: String) {
        val item = _pendingYoutube.value ?: return
        _pendingYoutube.value = null
        if (targetId == "phone") {
            startPhoneYouTube(item)
            return
        }
        scope.launch {
            busyRun("Could not start YouTube") {
                _wifiConnected.value = wifiStatus.isConnectedToWifi()
                if (!_wifiConnected.value) {
                    val stream = youtube.directPlaybackUrl(item.videoId)
                    markYouTubeWatched(item)
                    _phoneVideo.value = PhoneVideo(item.title, stream, item)
                    PhonePlaybackService.play(appContext, stream, item.title)
                    _screen.value = Screen.PHONE_VIDEO
                    return@busyRun
                }
                refreshBluetoothLocation()
                val target = youtubePlaybackTargets().firstOrNull { it.id == targetId }
                    ?: error("Playback target not found")
                val room = _settings.value.rooms.first { it.id == target.roomId }
                resetRoomToPrimary(room.id)
                when {
                    target.kodi -> kodi.openUrl(room.kodi, "plugin://plugin.video.youtube/play/?video_id=${item.videoId}")
                    target.cast -> {
                        val entity = resolveYouTubeCastEntity(room)
                        if (isSamsungEntity(entity)) {
                            val data = JSONObject()
                                .put("media_content_id", "https://www.youtube.com/watch?v=${item.videoId}")
                                .put("media_content_type", "video")
                            if (!ha.callService("media_extractor", "play_media", entity, data)) error("Home Assistant is not connected")
                        } else {
                            val streamUrl = youtube.directPlaybackUrl(item.videoId)
                            val data = JSONObject().put("media_content_id", streamUrl).put("media_content_type", "video")
                            if (!ha.callService("media_player", "play_media", entity, data)) error("Home Assistant is not connected")
                        }
                    }
                }
                markYouTubeWatched(item)
            }
        }
    }

    fun openKodiLibrary(shared: Boolean = false) {
        if (shared) beginMediaNavigation(roomScoped = false)
        val host = if (shared) _settings.value.rooms.firstOrNull { it.kodi.baseUrl.isNotBlank() } else currentRoom()
        if (host == null || host.kodi.baseUrl.isBlank()) {
            _message.value = "No Kodi library host is configured"
            return
        }
        kodiLibraryHostRoomId = host.id
        kodiLibraryShared = shared
        kodiBackStack.clear()
        _kodiBrowse.value = KodiBrowseState()
        _screen.value = Screen.KODI_LIBRARY
    }

    fun openSharedKodiLibrary() = openKodiLibrary(shared = true)

    fun exitKodiLibrary() {
        if (kodiLibraryShared) goMedia() else openKodi()
    }

    private fun kodiBrowseRoom(): RoomConfig? =
        kodiLibraryHostRoomId?.let { id -> _settings.value.rooms.firstOrNull { it.id == id } } ?: currentRoom()

    fun kodiLibraryHostRoom(): RoomConfig? = kodiBrowseRoom()

    fun kodiPlaybackTargets(): List<PlaybackTarget> = _settings.value.rooms
        .filter { it.kodi.baseUrl.isNotBlank() }
        .map { PlaybackTarget("kodi:${it.id}", "${it.name} · Kodi", it.id, kodi = true) }

    fun confirmKodiPlayback(targetId: String) {
        val item = _pendingKodiItem.value ?: return
        _pendingKodiItem.value = null
        val target = kodiPlaybackTargets().firstOrNull { it.id == targetId } ?: return
        val room = _settings.value.rooms.firstOrNull { it.id == target.roomId } ?: return
        scope.launch {
            busyRun("Kodi playback failed") {
                resetRoomToPrimary(room.id)
                kodi.open(room.kodi, item)
                _selectedRoomId.value = room.id
                _screen.value = Screen.KODI
                delay(150)
                refreshKodi()
            }
        }
    }

    fun refreshKodi() {
        val room = currentRoom() ?: return
        scope.launch {
            _kodiNow.value = runCatching { kodi.nowPlaying(room.kodi) }.getOrElse {
                _message.value = it.message ?: "Kodi connection failed"
                KodiNowPlaying()
            }
        }
    }

    fun kodiInput(method: String) {
        val room = currentRoom() ?: return
        scope.launch { runCatching { kodi.input(room.kodi, method) }.onFailure { _message.value = it.message } }
    }

    fun kodiPlayer(method: String) {
        val room = currentRoom() ?: return
        val playerId = _kodiNow.value.playerId
        scope.launch {
            runCatching { kodi.player(room.kodi, playerId, method) }
                .onSuccess { delay(150); refreshKodi() }
                .onFailure { _message.value = it.message }
        }
    }

    fun openKodiCategory(type: KodiBrowseType) {
        val room = kodiBrowseRoom() ?: return
        scope.launch {
            busyRun("Could not load Kodi library") {
                pushKodiState()
                _kodiBrowse.value = when (type) {
                    KodiBrowseType.MOVIES -> KodiBrowseState(type, "Movies", kodi.movies(room.kodi))
                    KodiBrowseType.TV_SHOWS -> KodiBrowseState(type, "TV shows", kodi.tvShows(room.kodi))
                    KodiBrowseType.MUSIC_VIDEOS -> KodiBrowseState(type, "Music videos", kodi.musicVideos(room.kodi))
                    KodiBrowseType.ARTISTS -> KodiBrowseState(type, "Artists", kodi.artists(room.kodi))
                    KodiBrowseType.ALBUMS -> KodiBrowseState(type, "Albums", kodi.albums(room.kodi))
                    KodiBrowseType.SONGS -> KodiBrowseState(type, "Songs", kodi.songs(room.kodi))
                    KodiBrowseType.VIDEO_SOURCES -> KodiBrowseState(type, "Video files", kodi.sources(room.kodi, "video"), fileMedia = "video")
                    KodiBrowseType.MUSIC_SOURCES -> KodiBrowseState(type, "Music files", kodi.sources(room.kodi, "music"), fileMedia = "music")
                    else -> KodiBrowseState()
                }
                _screen.value = Screen.KODI_LIBRARY
            }
        }
    }

    fun kodiSelectItem(item: KodiLibraryItem) {
        val room = kodiBrowseRoom() ?: return
        val state = _kodiBrowse.value
        scope.launch {
            busyRun("Kodi action failed") {
                when (state.type) {
                    KodiBrowseType.TV_SHOWS -> {
                        pushKodiState()
                        _kodiBrowse.value = KodiBrowseState(
                            KodiBrowseType.SEASONS, item.label,
                            kodi.seasons(room.kodi, item.id), parentTvShowId = item.id
                        )
                    }
                    KodiBrowseType.SEASONS -> {
                        pushKodiState()
                        _kodiBrowse.value = KodiBrowseState(
                            KodiBrowseType.EPISODES, item.label,
                            kodi.episodes(room.kodi, state.parentTvShowId, item.season),
                            parentTvShowId = state.parentTvShowId, parentSeason = item.season
                        )
                    }
                    KodiBrowseType.ARTISTS -> {
                        pushKodiState()
                        _kodiBrowse.value = KodiBrowseState(
                            KodiBrowseType.ALBUMS, item.label,
                            kodi.albums(room.kodi, artistId = item.id), parentArtistId = item.id
                        )
                    }
                    KodiBrowseType.ALBUMS -> {
                        pushKodiState()
                        _kodiBrowse.value = KodiBrowseState(
                            KodiBrowseType.SONGS, item.label,
                            kodi.songs(room.kodi, albumId = item.id, artistId = state.parentArtistId),
                            parentArtistId = state.parentArtistId
                        )
                    }
                    KodiBrowseType.VIDEO_SOURCES, KodiBrowseType.MUSIC_SOURCES, KodiBrowseType.FILES -> {
                        if (item.directory) {
                            pushKodiState()
                            val media = state.fileMedia.ifBlank { item.mediaType.ifBlank { "files" } }
                            _kodiBrowse.value = KodiBrowseState(
                                KodiBrowseType.FILES, item.label,
                                kodi.directory(room.kodi, item.file, media), directory = item.file, fileMedia = media
                            )
                        } else {
                            if (kodiLibraryShared) {
                                _pendingKodiItem.value = item
                            } else {
                                resetRoomToPrimary(room.id)
                                kodi.open(room.kodi, item)
                                delay(150); refreshKodi(); _screen.value = Screen.KODI
                            }
                        }
                    }
                    KodiBrowseType.MOVIES, KodiBrowseType.EPISODES, KodiBrowseType.MUSIC_VIDEOS, KodiBrowseType.SONGS -> {
                        if (kodiLibraryShared) {
                            _pendingKodiItem.value = item
                        } else {
                            resetRoomToPrimary(room.id)
                            kodi.open(room.kodi, item)
                            delay(150); refreshKodi(); _screen.value = Screen.KODI
                        }
                    }
                    else -> Unit
                }
            }
        }
    }

    fun kodiBrowseBack() {
        _kodiBrowse.value = if (kodiBackStack.isNotEmpty()) kodiBackStack.removeAt(kodiBackStack.lastIndex) else KodiBrowseState()
    }

    fun onForeground() {
        _wifiConnected.value = wifiStatus.isConnectedToWifi()
        scope.launch { discoverSecondaryPlayers() }
        val s = _settings.value
        if (!ha.connected.value && s.homeAssistantUrl.isNotBlank() && s.homeAssistantToken.isNotBlank()) {
            ha.connect(s.homeAssistantUrl, s.homeAssistantToken)
        }
        if (_wifiConnected.value) scope.launch { refreshBluetoothLocation() }
    }

    fun exportSettingsZip(uri: Uri) {
        scope.launch(Dispatchers.IO) {
            runCatching {
                val output = appContext.contentResolver.openOutputStream(uri)
                    ?: error("Could not create settings backup")
                output.use { raw ->
                    ZipOutputStream(raw).use { zip ->
                        zip.putNextEntry(ZipEntry("settings.json"))
                        zip.write(_settings.value.toJson().toString(2).toByteArray(Charsets.UTF_8))
                        zip.closeEntry()

                        zip.putNextEntry(ZipEntry("README.txt"))
                        zip.write(
                            (
                                "Home Media settings backup\n" +
                                    "WARNING: this backup contains authentication tokens, passwords, IP addresses and local network configuration.\n" +
                                    "Keep it private. Import it from Home Media Settings to restore the configuration.\n"
                                ).toByteArray(Charsets.UTF_8)
                        )
                        zip.closeEntry()
                    }
                }
            }.onSuccess {
                withContext(Dispatchers.Main) { _message.value = "Full settings backup exported" }
            }.onFailure { e ->
                withContext(Dispatchers.Main) {
                    _message.value = e.message ?: "Could not export settings backup"
                }
            }
        }
    }

    fun importSettingsZip(uri: Uri) {
        scope.launch(Dispatchers.IO) {
            runCatching {
                val input = appContext.contentResolver.openInputStream(uri) ?: error("Could not open settings ZIP")
                input.use {
                    ZipInputStream(it).use { zip ->
                        var entry = zip.nextEntry
                        while (entry != null) {
                            if (!entry.isDirectory && entry.name.substringAfterLast('/').equals("settings.json", true)) {
                                val json = zip.bufferedReader().readText()
                                val imported = appSettingsFromJson(JSONObject(json))
                                val current = _settings.value
                                val merged = imported.copy(
                                    homeAssistantUrl = imported.homeAssistantUrl.ifBlank { current.homeAssistantUrl },
                                    homeAssistantToken = imported.homeAssistantToken.ifBlank { current.homeAssistantToken },
                                    musicAssistantUrl = imported.musicAssistantUrl.ifBlank { current.musicAssistantUrl },
                                    musicAssistantToken = imported.musicAssistantToken.ifBlank { current.musicAssistantToken },
                                    youtubeApiKey = imported.youtubeApiKey.ifBlank { current.youtubeApiKey },
                                    sharedMaQueueId = imported.sharedMaQueueId.ifBlank { current.sharedMaQueueId },
                                    sharedMaQueueName = imported.sharedMaQueueName.ifBlank { current.sharedMaQueueName }
                                )
                                withContext(Dispatchers.Main) {
                                    saveSettings(merged)
                                    _message.value = "Settings imported"
                                }
                                return@use
                            }
                            zip.closeEntry()
                            entry = zip.nextEntry
                        }
                        error("settings.json was not found in the ZIP")
                    }
                }
            }.onFailure { e ->
                withContext(Dispatchers.Main) { _message.value = e.message ?: "Could not import settings ZIP" }
            }
        }
    }

    private fun resolveYouTubeCastEntity(room: RoomConfig): String =
        resolveYouTubeCastEntityOrNull(room)
            ?: error("${room.name} Cast player was not found. Set its HA entity in room settings.")

    private fun resolveYouTubeCastEntityOrNull(room: RoomConfig): String? {
        if (room.youtubeCastEntity.isNotBlank()) return room.youtubeCastEntity
        val candidates = ha.states.value.values.filter { it.entityId.startsWith("media_player.") }
        val bedroomCandidates = candidates.filter { state ->
            val id = state.entityId.lowercase()
            val name = state.attributes.optString("friendly_name").lowercase()
            id.contains(room.id.lowercase()) || id.contains(room.name.lowercase()) ||
                name.contains(room.name.lowercase())
        }
        val explicitCast = bedroomCandidates.firstOrNull { state ->
            val id = state.entityId.lowercase()
            val name = state.attributes.optString("friendly_name").lowercase()
            id.contains("cast") || id.contains("chromecast") || name.contains("cast") || name.contains("chromecast")
        }
        if (explicitCast != null) return explicitCast.entityId

        val excluded = buildSet {
            add(room.primaryPlayerEntity)
            add(room.routeEntity)
            room.secondaryPlayers.mapTo(this) { it.haEntity }
        }
        val remaining = bedroomCandidates.filter { it.entityId !in excluded }
        if (remaining.size == 1) return remaining.first().entityId

        if (room.id == "lounge") {
            val samsung = candidates.filter { state ->
                val id = state.entityId.lowercase()
                val name = state.attributes.optString("friendly_name").lowercase()
                (id.contains("samsung") || name.contains("samsung")) && state.entityId !in excluded
            }
            if (samsung.size == 1) return samsung.first().entityId
        }
        return null
    }

    private fun isSamsungEntity(entityId: String): Boolean {
        val state = ha.states.value[entityId]
        val name = state?.attributes?.optString("friendly_name").orEmpty()
        return entityId.contains("samsung", true) || name.contains("samsung", true)
    }

    private suspend fun refreshBluetoothLocation() {
        _wifiConnected.value = wifiStatus.isConnectedToWifi()
        if (!_wifiConnected.value) return
        val settings = _settings.value
        val activePresenceMatches = settings.rooms.filter { room ->
            presenceMatchesRoom(room, ha.states.value[room.presenceEntity])
        }
        val activePresence = when {
            activePresenceMatches.size == 1 -> activePresenceMatches.first()
            activePresenceMatches.any { it.id == _selectedRoomId.value } ->
                activePresenceMatches.first { it.id == _selectedRoomId.value }
            else -> null
        }
        if (activePresenceMatches.size > 1 && activePresence == null) {
            debugLogger.log(
                "PRESENCE",
                "Ambiguous active presence rooms=" + activePresenceMatches.joinToString { it.name } + "; allowing BLE disambiguation"
            )
        }
        if (activePresence != null) {
            pendingBleRoomId = null
            pendingBleConfirmations = 0
            debugLogger.log("BLE", "Skip BLE override because HA presence matches room=${activePresence.name}")
            if (activePresence.id != _selectedRoomId.value) {
                _selectedRoomId.value = activePresence.id
                persist(settings.copy(lastRoomId = activePresence.id))
            }
            return
        }

        val anyBluetoothData = settings.rooms.any {
            it.bluetoothAnchors.isNotEmpty() || it.bluetoothCalibrationPoints.size >= 3
        }
        if (!settings.bluetoothLocationEnabled || !anyBluetoothData) {
            debugLogger.log("BLE", "Skip automatic BLE: enabled=${settings.bluetoothLocationEnabled} anyData=$anyBluetoothData")
            return
        }

        val scan = bluetoothLocator.scanFingerprint(2200)
        val candidates = bluetoothLocator.scoreRooms(settings.rooms, scan.samples)
        candidates.take(5).forEach { score ->
            debugLogger.log(
                "BLE",
                "Auto candidate room=${score.roomId} score=${"%.1f".format(score.score)} " +
                    "overlap=${score.overlap}/${score.expectedDevices} stable=${score.stableDevices}"
            )
        }
        val match = bluetoothLocator.resolveRoomFromSamples(settings.rooms, scan.samples)
        if (match == null) {
            pendingBleRoomId = null
            pendingBleConfirmations = 0
            debugLogger.log("BLE", "No confident automatic BLE room match")
            return
        }

        val roomId = match.roomId
        if (roomId == _selectedRoomId.value) {
            pendingBleRoomId = null
            pendingBleConfirmations = 0
            debugLogger.log(
                "BLE",
                "Automatic match confirms current room=$roomId score=${match.score} margin=${match.confidenceMargin}"
            )
            return
        }

        if (pendingBleRoomId == roomId) {
            pendingBleConfirmations += 1
        } else {
            pendingBleRoomId = roomId
            pendingBleConfirmations = 1
        }

        debugLogger.log(
            "BLE",
            "Automatic candidate room=$roomId score=${"%.1f".format(match.score)} " +
                "margin=${"%.1f".format(match.confidenceMargin)} confirmation=$pendingBleConfirmations/2"
        )
        if (pendingBleConfirmations < 2) return

        val current = settings.rooms.firstOrNull { it.id == _selectedRoomId.value }
        val leavingQuietRoom = current?.bluetoothQuietRoom == true && roomId != current.id
        if (leavingQuietRoom) {
            val veryStrong = match.score <= 18.0 && match.confidenceMargin >= 14.0
            if (!veryStrong) {
                debugLogger.log("BLE", "Rejected exit from quiet room; candidate not strong enough")
                return
            }
        }

        if (settings.rooms.any { it.id == roomId }) {
            _selectedRoomId.value = roomId
            persist(settings.copy(lastRoomId = roomId))
            debugLogger.log("BLE", "Automatic room changed to $roomId after 2 confirmations")
        }
        pendingBleRoomId = null
        pendingBleConfirmations = 0
    }

    private fun activeTransportEntity(): String? {
        val room = currentRoom() ?: return null
        val key = _activePlayerKey.value
        if (key != "primary") return room.secondaryPlayers.firstOrNull { it.id == key }?.let { secondaryHaEntity(it) }
        return transportEntity(room)
    }

    private fun activeVolumeEntity(): String? {
        val room = currentRoom() ?: return null
        val key = _activePlayerKey.value
        if (key != "primary") {
            return room.secondaryPlayers.firstOrNull { it.id == key }?.let { secondaryHaEntity(it) }?.takeIf(String::isNotBlank)
                ?: room.primaryPlayerEntity.takeIf(String::isNotBlank)
                ?: room.routeEntity.takeIf(String::isNotBlank)
        }
        return room.primaryPlayerEntity.takeIf(String::isNotBlank)
            ?: room.routeEntity.takeIf(String::isNotBlank)
    }

    private fun hardwareVolumeEntity(): String? {
        if (_screen.value == Screen.ROOM) return activeVolumeEntity()

        val states = ha.states.value
        val configured = buildList {
            _settings.value.rooms.forEach { room ->
                if (room.primaryPlayerEntity.isNotBlank()) add(room.primaryPlayerEntity)
                room.secondaryPlayers.mapNotNullTo(this) { it.haEntity.takeIf(String::isNotBlank) }
                resolveYouTubeCastEntityOrNull(room)?.let(::add)
            }
        }.distinct()

        val active = configured.filter { entity ->
            states[entity]?.state in setOf("playing", "paused", "buffering")
        }
        if (active.size == 1) return active.first()

        return activeVolumeEntity()
    }

    private suspend fun prepareActiveQueueForRoom(room: RoomConfig): String {
        val key = _activePlayerKey.value
        if (key != "primary") {
            val secondary = room.secondaryPlayers.firstOrNull { it.id == key }
            if (secondary != null) {
                val playerId = ma.resolvePlayerId(secondary.maPlayerId, secondary.maPlayerName.ifBlank { secondary.name })
                return ma.resolvePlayerQueueId(playerId)
            }
        }
        return prepareMassPlaybackTarget(room)
    }

    private suspend fun resolveActiveQueueForRoom(room: RoomConfig): String {
        val key = _activePlayerKey.value
        if (key != "primary") {
            val s = room.secondaryPlayers.firstOrNull { it.id == key }
            if (s != null) return ma.resolvePlayerQueueId(ma.resolvePlayerId(s.maPlayerId, s.maPlayerName.ifBlank { s.name }))
        }
        return resolveQueueIdForRoom(room)
    }

    private suspend fun queueForTarget(targetId: String): String {
        val target = playbackTargets().firstOrNull { it.id == targetId } ?: error("Playback target not found")
        val room = _settings.value.rooms.first { it.id == target.roomId }
        return if (target.secondaryId.isNotBlank()) {
            val s = room.secondaryPlayers.first { it.id == target.secondaryId }
            _selectedRoomId.value = room.id
            _activePlayerKey.value = s.id
            ma.resolvePlayerQueueId(ma.resolvePlayerId(s.maPlayerId, s.maPlayerName))
        } else {
            _selectedRoomId.value = room.id
            _activePlayerKey.value = "primary"
            prepareMassPlaybackTarget(room)
        }
    }

    // ----- internals -----

    private fun persist(settings: AppSettings) {
        _settings.value = settings
        store.save(settings)
    }

    private fun configureConnections(settings: AppSettings) {
        debugLogger.log("CONNECT", "Configuring HA=${settings.homeAssistantUrl} MA=${settings.musicAssistantUrl}")
        ma.configure(settings.musicAssistantUrl, settings.musicAssistantToken)
        ha.connect(settings.homeAssistantUrl, settings.homeAssistantToken)
    }

    private fun resolveAutomaticRoom(states: Map<String, HaEntityState>) {
        _wifiConnected.value = wifiStatus.isConnectedToWifi()
        if (!_wifiConnected.value) return
        val settings = _settings.value
        val signature = settings.rooms.joinToString("|") { room ->
            room.presenceEntity + "=" + (states[room.presenceEntity]?.state ?: "<missing>")
        }
        if (signature == lastPresenceStateSignature) return
        lastPresenceStateSignature = signature

        val presenceMatches = settings.rooms.filter { room -> presenceMatchesRoom(room, states[room.presenceEntity]) }
        if (settings.debugEnabled && settings.rooms.any { it.presenceEntity.isNotBlank() }) {
            settings.rooms.filter { it.presenceEntity.isNotBlank() }.forEach { room ->
                val st = states[room.presenceEntity]
                debugLogger.log(
                    "PRESENCE",
                    "room=${room.name} entity=${room.presenceEntity} actual=${st?.state ?: "<missing>"} expected=${room.presenceValue.ifBlank { "<auto>" }} match=${room in presenceMatches}"
                )
            }
        }

        val presenceMatch = when {
            presenceMatches.size == 1 -> presenceMatches.first()
            presenceMatches.any { it.id == _selectedRoomId.value } -> presenceMatches.first { it.id == _selectedRoomId.value }
            presenceMatches.size > 1 -> {
                debugLogger.log("PRESENCE", "Ambiguous presence matches=${presenceMatches.joinToString { it.name }}; retaining current room")
                null
            }
            else -> null
        }

        val activeFallback = if (presenceMatch == null && settings.activePlayerLocationFallback) {
            settings.rooms.filter { room -> states[room.primaryPlayerEntity]?.state in setOf("playing", "paused", "buffering") }.singleOrNull()
        } else null

        val resolved = presenceMatch ?: activeFallback
        if (resolved != null && resolved.id != _selectedRoomId.value) {
            debugLogger.log("LOCATION", "HA location changed ${_selectedRoomId.value} -> ${resolved.id}")
            _selectedRoomId.value = resolved.id
            _activePlayerKey.value = resolved.activePlayerKey.takeIf { key ->
                key == "primary" || resolved.secondaryPlayers.any { it.id == key }
            } ?: "primary"
            persist(settings.copy(lastRoomId = resolved.id))
        }
    }

    private fun presenceMatchesRoom(room: RoomConfig, state: HaEntityState?): Boolean {
        if (room.presenceEntity.isBlank() || state == null) return false
        val actual = state.state.trim()
        val expectedRaw = room.presenceValue.trim()

        fun eq(a: String, b: String) = a.trim().equals(b.trim(), ignoreCase = true)
        val attrs = listOf(
            "room", "room_name", "location", "area", "area_name",
            "zone", "presence", "occupancy"
        ).mapNotNull { key ->
            state.attributes.optString(key).trim().takeIf(String::isNotBlank)
        }

        if (expectedRaw.isNotBlank()) {
            val expectedValues = expectedRaw
                .split(',', '|', ';')
                .map(String::trim)
                .filter(String::isNotBlank)
            return expectedValues.any { expected ->
                eq(actual, expected) || attrs.any { eq(it, expected) }
            }
        }

        val roomNames = listOf(room.id, room.name)
        if (roomNames.any { eq(actual, it) } || attrs.any { attr -> roomNames.any { eq(attr, it) } }) return true

        return actual.lowercase() in setOf(
            "on", "home", "present", "occupied", "detected", "true", "yes"
        )
    }

    private fun updateJoinCandidate(states: Map<String, HaEntityState>) {
        val currentId = _selectedRoomId.value
        val rooms = _settings.value.rooms
        val currentActive = rooms.firstOrNull { it.id == currentId }?.let { isRoomActive(it, states) } == true
        if (currentActive) {
            _joinSourceRoomId.value = null
            return
        }
        val direct = rooms.firstOrNull { it.id != currentId && states[it.primaryPlayerEntity]?.state == "playing" }
        val linked = rooms.firstOrNull { it.id != currentId && isRoomActive(it, states) }
        _joinSourceRoomId.value = (direct ?: linked)?.id
    }

    private fun isRoomActive(room: RoomConfig, states: Map<String, HaEntityState>): Boolean {
        val activeStates = setOf("playing", "paused", "buffering")
        val primary = states[room.primaryPlayerEntity]
        if (primary?.state in activeStates) return true
        if (room.secondaryPlayers.any { secondary ->
                secondaryHaEntity(secondary, states).takeIf(String::isNotBlank)?.let { states[it]?.state in activeStates } == true
            }) return true
        val routed = states[room.routeEntity] ?: return false
        if (routed.state in setOf("off", "unavailable", "unknown")) return false
        val source = routed.attributes.optString("source")
        return source.equals(room.linkSourceName, true) || source.equals(room.auxSourceName, true)
    }

    private fun syncActivePlayerForCurrentRoom(states: Map<String, HaEntityState>): Boolean {
        val room = currentRoom() ?: return false
        val activeStates = setOf("playing", "paused", "buffering")
        val currentKey = _activePlayerKey.value
        val primaryActive = states[room.primaryPlayerEntity]?.state in activeStates
        val activeSecondaries = room.secondaryPlayers.filter { secondary ->
            secondary.haEntity.isNotBlank() && states[secondary.haEntity]?.state in activeStates
        }

        val resolvedKey = when {
            currentKey != "primary" && room.activePlayerKey == currentKey -> currentKey
            currentKey != "primary" && activeSecondaries.any { it.id == currentKey } -> currentKey
            currentKey == "primary" && !primaryActive && activeSecondaries.size == 1 -> activeSecondaries.first().id
            else -> currentKey
        }

        if (resolvedKey == currentKey) return false
        debugLogger.log(
            "PLAYER",
            "Active player changed room=${room.name} $currentKey -> $resolvedKey " +
                "primaryActive=$primaryActive secondaries=${activeSecondaries.joinToString { it.name }}"
        )
        _activePlayerKey.value = resolvedKey
        persistActivePlayer(room.id, resolvedKey)
        return true
    }

    private fun secondaryHaEntity(secondary: SecondaryPlayerConfig, states: Map<String, HaEntityState> = ha.states.value): String {
        if (secondary.haEntity.isNotBlank() && states.containsKey(secondary.haEntity)) return secondary.haEntity
        val needles = listOf(secondary.name, secondary.maPlayerName)
            .map { it.lowercase().replace(Regex("[^a-z0-9]"), "") }
            .filter { it.length >= 3 }
        return states.values.firstOrNull { state ->
            if (!state.entityId.startsWith("media_player.")) return@firstOrNull false
            val hay = (state.entityId + " " + state.attributes.optString("friendly_name"))
                .lowercase().replace(Regex("[^a-z0-9]"), "")
            needles.any { it in hay }
        }?.entityId.orEmpty()
    }

    private suspend fun discoverSecondaryPlayers() {
        val states = ha.states.value
        val massPlayers = runCatching { ma.players() }.getOrDefault(emptyList())
        val settings = _settings.value
        var changed = false
        val updatedRooms = settings.rooms.map { room ->
            val secondaries = room.secondaryPlayers.map { secondary ->
                val haId = secondaryHaEntity(secondary, states).ifBlank { secondary.haEntity }
                val maId = secondary.maPlayerId.ifBlank {
                    val hints = listOf(secondary.maPlayerName, secondary.name).filter { it.isNotBlank() }
                    massPlayers.firstOrNull { p -> hints.any { it.equals(p.displayName, true) } }?.playerId.orEmpty()
                }
                if (haId != secondary.haEntity || maId != secondary.maPlayerId) {
                    changed = true
                    debugLogger.log("PLAYER", "Discovered secondary room=${room.name} name=${secondary.name} ha=${haId.ifBlank { "<missing>" }} ma=${maId.ifBlank { "<missing>" }}")
                    secondary.copy(haEntity = haId, maPlayerId = maId)
                } else secondary
            }
            room.copy(secondaryPlayers = secondaries)
        }
        if (changed) persist(_settings.value.copy(rooms = updatedRooms))
    }

    private fun updateNowPlaying(states: Map<String, HaEntityState>) {
        val room = currentRoom()
        if (room == null) { _nowPlaying.value = NowPlaying(); return }
        val activeSecondary = room.secondaryPlayers.firstOrNull { it.id == _activePlayerKey.value }
        val activeEntity = activeSecondary?.let { secondaryHaEntity(it) }?.takeIf(String::isNotBlank) ?: room.primaryPlayerEntity
        val roomState = states[activeEntity]
        val routeState = if (activeSecondary == null) states[room.routeEntity] else roomState
        val source = routeState?.attributes?.optString("source").orEmpty()
        val settings = _settings.value
        val fallbackState = when {
            source.equals(room.linkSourceName, true) -> states[settings.linkMediaPlayerEntity] ?: states[settings.globalMediaEntity]
            else -> states[settings.globalMediaEntity]
        }
        val st = when {
            roomState != null && hasMediaMetadata(roomState) -> roomState
            activeSecondary != null -> roomState
            fallbackState != null && isRoomActive(room, states) -> fallbackState
            else -> roomState ?: routeState
        }
        if (st == null) {
            _nowPlaying.value = NowPlaying(entityId = activeEntity, source = source)
            return
        }
        val a = st.attributes
        _nowPlaying.value = NowPlaying(
            entityId = st.entityId,
            state = st.state,
            title = a.optString("media_title").ifBlank { if ((routeState?.state ?: st.state) == "off") "Off" else room.name },
            artist = a.optString("media_artist"),
            album = a.optString("media_album_name").ifBlank { a.optString("media_album") },
            imageUrl = ha.imageUrl(a.optString("entity_picture")),
            position = a.optDouble("media_position", 0.0),
            duration = a.optDouble("media_duration", 0.0),
            volume = (roomState ?: routeState)?.attributes?.optDouble("volume_level", 0.0) ?: 0.0,
            source = source.ifBlank { a.optString("source") }
        )
    }

    private fun hasMediaMetadata(state: HaEntityState): Boolean = state.attributes.optString("media_title").isNotBlank() ||
        state.attributes.optString("media_artist").isNotBlank() || state.attributes.optString("media_album_name").isNotBlank()

    private fun transportEntity(room: RoomConfig): String {
        val source = ha.states.value[room.routeEntity]?.attributes?.optString("source").orEmpty()
        return if (source.equals(room.linkSourceName, ignoreCase = true)) {
            _settings.value.linkMediaPlayerEntity.ifBlank { room.primaryPlayerEntity }
        } else room.primaryPlayerEntity
    }

    private fun callServiceSpec(room: RoomConfig, spec: HaServiceSpec) {
        if (spec.domain.isBlank() || spec.service.isBlank()) error("This tile is not configured yet")
        val target = spec.targetEntity.ifBlank { if (spec.domain == "media_player") room.routeEntity else "" }
        val data = runCatching { JSONObject(spec.dataJson.ifBlank { "{}" }) }.getOrElse { error("Tile service data is not valid JSON") }
        if (!ha.callService(spec.domain, spec.service, target, data)) error("Home Assistant is not connected")
    }

    private suspend fun ensureRoomOn(room: RoomConfig) {
        val entity = room.routeEntity
        if (entity.isBlank()) error("${room.name} media player/MLGW entity is not configured")
        val current = ha.states.value[entity]?.state
        if (current !in setOf("off", "unavailable", "unknown", null)) return
        if (!ha.turnOn(entity)) error("Home Assistant is not connected")
        val ok = withTimeoutOrNull(room.sourceConfirmTimeoutMs.coerceAtLeast(room.powerOnDelayMs)) {
            ha.states.map { it[entity]?.state }.first { it !in setOf("off", "unavailable", "unknown", null) }
        } != null
        if (!ok) error("${room.name} did not turn on")
        delay(room.powerOnDelayMs.coerceAtLeast(100))
    }

    private suspend fun prepareMlgwSource(room: RoomConfig, sourceKind: SharedSource) {
        val entity = room.routeEntity
        if (entity.isBlank()) error("${room.name} MLGW/HA entity is not configured")
        ensureRoomOn(room)
        val source = room.sourceName(sourceKind)
        if (!ha.selectSource(entity, source)) error("Home Assistant is not connected")
        val confirmed = withTimeoutOrNull(room.sourceConfirmTimeoutMs.coerceAtLeast(1000)) {
            ha.states.map { it[entity]?.attributes?.optString("source") }
                .first { it.equals(source, ignoreCase = true) }
        } != null
        if (!confirmed) error("${room.name} did not confirm source $source; playback was not moved")
    }

    private fun sharedSourceForRoom(room: RoomConfig, states: Map<String, HaEntityState>): SharedSource {
        val source = states[room.routeEntity]?.attributes?.optString("source").orEmpty()
        return when {
            source.equals(room.auxSourceName, true) -> SharedSource.A_AUX
            source.equals(room.linkSourceName, true) -> SharedSource.LINK
            else -> room.sharedPlaybackSource
        }
    }

    private suspend fun resolveQueueIdForRoom(room: RoomConfig): String {
        return if (room.maPlayerId.isNotBlank()) ma.resolvePlayerQueueId(room.maPlayerId)
        else ma.resolveQueueId(_settings.value.sharedMaQueueId, _settings.value.sharedMaQueueName)
    }

    private suspend fun prepareMassPlaybackTarget(room: RoomConfig): String {
        return if (room.maPlayerId.isNotBlank()) {
            ensureRoomOn(room)
            ma.resolvePlayerQueueId(room.maPlayerId)
        } else {
            prepareMlgwSource(room, room.sharedPlaybackSource)
            ma.resolveQueueId(_settings.value.sharedMaQueueId, _settings.value.sharedMaQueueName)
        }
    }

    private suspend fun refreshQueueInternal(queueId: String) {
        _queueInfo.value = ma.queue(queueId) ?: MassQueueInfo(queueId = queueId, displayName = queueId)
        _queueItems.value = ma.queueItems(queueId)
    }

    private suspend fun openMassDetailInternal(item: MassMediaItem) {
        _selectedMassItem.value = item
        _massChildren.value = runCatching { ma.itemChildren(item) }.getOrDefault(emptyList())
        _screen.value = Screen.MASS_DETAIL
    }

    private fun pushKodiState() {
        val current = _kodiBrowse.value
        if (current.type != KodiBrowseType.HOME || current.items.isNotEmpty()) kodiBackStack += current
    }

    private suspend fun busyRun(defaultError: String, block: suspend () -> Unit) {
        _busy.value = true
        try { block() }
        catch (e: Exception) {
            debugLogger.log("ERROR", "$defaultError: ${e.javaClass.simpleName}: ${e.message}")
            _message.value = e.message ?: defaultError
        }
        finally { _busy.value = false }
    }

    private fun resolveInitialRoomId(settings: AppSettings): String = settings.lastRoomId.takeIf { id -> settings.rooms.any { it.id == id } }
        ?: settings.rooms.firstOrNull()?.id.orEmpty()
}