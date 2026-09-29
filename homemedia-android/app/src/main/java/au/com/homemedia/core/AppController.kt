package au.com.homemedia.core

import android.content.Context
import android.net.Uri
import au.com.homemedia.location.BluetoothLocator
import au.com.homemedia.model.*
import au.com.homemedia.network.HomeAssistantClient
import au.com.homemedia.network.KodiClient
import au.com.homemedia.network.MusicAssistantClient
import au.com.homemedia.network.YouTubeClient
import au.com.homemedia.storage.SettingsStore
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.*
import org.json.JSONObject
import java.util.zip.ZipInputStream

enum class Screen { ROOM, MEDIA, MASS_HOME, MASS_LIST, MASS_DETAIL, QUEUE, KODI, KODI_LIBRARY, YOUTUBE, SETTINGS }

class AppController(context: Context) {
    private val appContext = context.applicationContext
    private val store = SettingsStore(appContext)
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    val ha = HomeAssistantClient()
    val ma = MusicAssistantClient()
    val kodi = KodiClient()
    val youtube = YouTubeClient()
    private val bluetoothLocator = BluetoothLocator(appContext)

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

    private val _activePlayerKey = MutableStateFlow("primary")
    val activePlayerKey: StateFlow<String> = _activePlayerKey

    private val _pendingMassPlayback = MutableStateFlow<Pair<MassMediaItem, String>?>(null)
    val pendingMassPlayback: StateFlow<Pair<MassMediaItem, String>?> = _pendingMassPlayback

    private val _youtubeResults = MutableStateFlow<List<YouTubeItem>>(emptyList())
    val youtubeResults: StateFlow<List<YouTubeItem>> = _youtubeResults

    private val _pendingYoutube = MutableStateFlow<YouTubeItem?>(null)
    val pendingYoutube: StateFlow<YouTubeItem?> = _pendingYoutube

    init {
        configureConnections(_settings.value)
        scope.launch {
            ha.states.collect { states ->
                if (_settings.value.automaticRoom) resolveAutomaticRoom(states)
                updateNowPlaying(states)
                updateJoinCandidate(states)
            }
        }
    }

    fun close() {
        scope.cancel()
        ha.disconnect()
    }

    fun currentRoom(): RoomConfig? = _settings.value.rooms.firstOrNull { it.id == _selectedRoomId.value }
        ?: _settings.value.rooms.firstOrNull()

    fun joinSourceRoom(): RoomConfig? = _joinSourceRoomId.value?.let { id -> _settings.value.rooms.firstOrNull { it.id == id } }

    fun clearMessage() { _message.value = null }
    fun goRoom() { _screen.value = Screen.ROOM }
    fun goMedia() { _screen.value = Screen.MEDIA }
    fun goSettings() { _screen.value = Screen.SETTINGS }
    fun openYouTube() { _screen.value = Screen.YOUTUBE }

    fun selectRoom(id: String) {
        if (_settings.value.rooms.none { it.id == id }) return
        _selectedRoomId.value = id
        persist(_settings.value.copy(lastRoomId = id))
        _screen.value = Screen.ROOM
        _activePlayerKey.value = "primary"
        updateNowPlaying(ha.states.value)
        updateJoinCandidate(ha.states.value)
    }

    fun saveSettings(newSettings: AppSettings) {
        val old = _settings.value
        persist(newSettings)
        if (_selectedRoomId.value.isBlank() || newSettings.rooms.none { it.id == _selectedRoomId.value }) {
            _selectedRoomId.value = resolveInitialRoomId(newSettings)
        }
        if (old.homeAssistantUrl != newSettings.homeAssistantUrl || old.homeAssistantToken != newSettings.homeAssistantToken ||
            old.musicAssistantUrl != newSettings.musicAssistantUrl || old.musicAssistantToken != newSettings.musicAssistantToken
        ) configureConnections(newSettings)
        updateNowPlaying(ha.states.value)
        updateJoinCandidate(ha.states.value)
    }

    fun executeTile(tile: TileConfig) {
        val room = currentRoom() ?: return
        when (tile.actionType) {
            TileActionType.OPEN_LIBRARY -> openLibrary()
            TileActionType.OPEN_KODI -> openKodi()
            TileActionType.OPEN_QUEUE -> openQueue()
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

    fun togglePlayPause() { currentRoom()?.let { room -> transportEntity(room).takeIf(String::isNotBlank)?.let(ha::playPause) } }
    fun next() { currentRoom()?.let { room -> transportEntity(room).takeIf(String::isNotBlank)?.let(ha::next) } }
    fun previous() { currentRoom()?.let { room -> transportEntity(room).takeIf(String::isNotBlank)?.let(ha::previous) } }
    // Volume always belongs to the physical room, even when transport is the shared Link player.
    fun volumeUp() { currentRoom()?.primaryPlayerEntity?.takeIf { it.isNotBlank() }?.let(ha::volumeUp) }
    fun volumeDown() { currentRoom()?.primaryPlayerEntity?.takeIf { it.isNotBlank() }?.let(ha::volumeDown) }

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

    // ----- Music Assistant library -----

    fun openLibrary() {
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
        val room = currentRoom() ?: return
        scope.launch {
            busyRun("Could not start ${item.name}") {
                val queueId = prepareMassPlaybackTarget(room)
                ma.play(queueId, item.uri, option)
                delay(250)
                refreshQueueInternal(queueId)
            }
        }
    }

    // ----- Music Assistant queue -----

    fun openQueue() {
        val room = currentRoom() ?: return
        scope.launch {
            busyRun("Could not load Music Assistant queue") {
                val queueId = resolveQueueIdForRoom(room)
                refreshQueueInternal(queueId)
                _screen.value = Screen.QUEUE
            }
        }
    }

    fun refreshQueue() {
        val room = currentRoom() ?: return
        scope.launch {
            busyRun("Could not refresh queue") { refreshQueueInternal(resolveQueueIdForRoom(room)) }
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
                val q = resolveQueueIdForRoom(room)
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
                val q = if (preparePlayback) prepareMassPlaybackTarget(room) else resolveQueueIdForRoom(room)
                command(q)
                if (refresh) { delay(120); refreshQueueInternal(q) }
            }
        }
    }

    // ----- Kodi remote + complete library -----

    fun openKodi() {
        _screen.value = Screen.KODI
        refreshKodi()
    }

    fun openKodiLibrary() {
        kodiBackStack.clear()
        _kodiBrowse.value = KodiBrowseState()
        _screen.value = Screen.KODI_LIBRARY
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
        val room = currentRoom() ?: return
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
        val room = currentRoom() ?: return
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
                            kodi.open(room.kodi, item)
                            delay(150); refreshKodi(); _screen.value = Screen.KODI
                        }
                    }
                    KodiBrowseType.MOVIES, KodiBrowseType.EPISODES, KodiBrowseType.MUSIC_VIDEOS, KodiBrowseType.SONGS -> {
                        kodi.open(room.kodi, item)
                        delay(150); refreshKodi(); _screen.value = Screen.KODI
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
        val s = _settings.value
        if (!ha.connected.value && s.homeAssistantUrl.isNotBlank() && s.homeAssistantToken.isNotBlank()) {
            ha.connect(s.homeAssistantUrl, s.homeAssistantToken)
        }
    }

    // ----- internals -----

    private fun persist(settings: AppSettings) {
        _settings.value = settings
        store.save(settings)
    }

    private fun configureConnections(settings: AppSettings) {
        ma.configure(settings.musicAssistantUrl, settings.musicAssistantToken)
        ha.connect(settings.homeAssistantUrl, settings.homeAssistantToken)
    }

    private fun resolveAutomaticRoom(states: Map<String, HaEntityState>) {
        val settings = _settings.value
        val presenceMatch = settings.rooms.firstOrNull { room ->
            room.presenceEntity.isNotBlank() && room.presenceValue.isNotBlank() &&
                states[room.presenceEntity]?.state.equals(room.presenceValue, ignoreCase = true)
        }
        val resolved = presenceMatch ?: if (settings.activePlayerLocationFallback) {
            settings.rooms.filter { room -> states[room.primaryPlayerEntity]?.state in setOf("playing", "paused", "buffering") }.singleOrNull()
        } else null
        if (resolved != null && resolved.id != _selectedRoomId.value) _selectedRoomId.value = resolved.id
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
        val primary = states[room.primaryPlayerEntity]
        if (primary?.state in setOf("playing", "paused", "buffering")) return true
        val routed = states[room.routeEntity] ?: return false
        if (routed.state in setOf("off", "unavailable", "unknown")) return false
        val source = routed.attributes.optString("source")
        return source.equals(room.linkSourceName, true) || source.equals(room.auxSourceName, true)
    }

    private fun updateNowPlaying(states: Map<String, HaEntityState>) {
        val room = currentRoom()
        if (room == null) { _nowPlaying.value = NowPlaying(); return }
        val roomState = states[room.primaryPlayerEntity]
        val routeState = states[room.routeEntity]
        val source = routeState?.attributes?.optString("source").orEmpty()
        val settings = _settings.value
        val fallbackState = when {
            source.equals(room.linkSourceName, true) -> states[settings.linkMediaPlayerEntity] ?: states[settings.globalMediaEntity]
            else -> states[settings.globalMediaEntity]
        }
        val st = when {
            roomState != null && hasMediaMetadata(roomState) -> roomState
            fallbackState != null && isRoomActive(room, states) -> fallbackState
            else -> roomState ?: routeState
        }
        if (st == null) {
            _nowPlaying.value = NowPlaying(entityId = room.primaryPlayerEntity, source = source)
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
        catch (e: Exception) { _message.value = e.message ?: defaultError }
        finally { _busy.value = false }
    }

    private fun resolveInitialRoomId(settings: AppSettings): String = settings.lastRoomId.takeIf { id -> settings.rooms.any { it.id == id } }
        ?: settings.rooms.firstOrNull()?.id.orEmpty()
}