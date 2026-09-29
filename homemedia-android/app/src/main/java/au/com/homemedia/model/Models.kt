package au.com.homemedia.model

import org.json.JSONArray
import org.json.JSONObject
import java.util.UUID

enum class TileActionType { HA_SERVICE, SELECT_SOURCE, OPEN_LIBRARY, OPEN_KODI, OPEN_QUEUE, OPEN_YOUTUBE }
enum class SharedSource { LINK, A_AUX }
enum class MassCategory(val apiName: String, val label: String) {
    ARTISTS("artists", "Artists"),
    ALBUMS("albums", "Albums"),
    TRACKS("tracks", "Tracks"),
    PLAYLISTS("playlists", "Playlists"),
    RADIOS("radios", "Radio"),
    PODCASTS("podcasts", "Podcasts"),
    AUDIOBOOKS("audiobooks", "Audiobooks"),
    GENRES("genres", "Genres")
}
enum class KodiBrowseType {
    HOME, MOVIES, TV_SHOWS, SEASONS, EPISODES, MUSIC_VIDEOS,
    ARTISTS, ALBUMS, SONGS, VIDEO_SOURCES, MUSIC_SOURCES, FILES
}

data class HaServiceSpec(
    val domain: String = "",
    val service: String = "",
    val targetEntity: String = "",
    val dataJson: String = "{}"
)

data class TileConfig(
    val id: String = UUID.randomUUID().toString(),
    val title: String = "Tile",
    val icon: String = "music",
    val actionType: TileActionType = TileActionType.HA_SERVICE,
    val source: String = "",
    val service: HaServiceSpec = HaServiceSpec()
)

data class KodiConfig(
    val baseUrl: String = "",
    val username: String = "",
    val password: String = ""
)

data class SecondaryPlayerConfig(
    val id: String = UUID.randomUUID().toString(),
    val name: String = "Secondary player",
    val haEntity: String = "",
    val maPlayerId: String = "",
    val maPlayerName: String = "",
    val toggleEntity: String = ""
)

data class BluetoothAnchorConfig(
    val address: String = "",
    val nameContains: String = "",
    val minRssi: Int = -92
)

data class BluetoothFingerprintSample(
    val address: String = "",
    val name: String = "",
    val rssi: Int = -100
)

data class BluetoothCalibrationPoint(
    val point: Int = 1,
    val samples: List<BluetoothFingerprintSample> = emptyList()
)

data class PlaybackTarget(
    val id: String,
    val label: String,
    val roomId: String = "",
    val secondaryId: String = "",
    val kodi: Boolean = false,
    val cast: Boolean = false,
    val phone: Boolean = false
)

data class YouTubeItem(
    val videoId: String,
    val title: String,
    val channel: String = "",
    val channelUrl: String = "",
    val thumbnail: String = ""
)

data class YouTubeSavedChannel(
    val name: String,
    val url: String
)

data class YouTubePlaylist(
    val id: String = UUID.randomUUID().toString(),
    val name: String,
    val videos: List<YouTubeItem> = emptyList()
)

data class PhoneVideo(
    val title: String = "",
    val streamUrl: String = "",
    val youtubeItem: YouTubeItem? = null
)

data class StremioMetaItem(
    val id: String,
    val type: String,
    val name: String,
    val poster: String = "",
    val description: String = "",
    val releaseInfo: String = ""
)

data class StremioStreamItem(
    val name: String = "",
    val title: String = "",
    val url: String = "",
    val externalUrl: String = "",
    val infoHash: String = ""
) {
    val directlyPlayable: Boolean get() = url.startsWith("http://") || url.startsWith("https://")
}

data class RoomConfig(
    val id: String = UUID.randomUUID().toString(),
    val name: String = "Room",
    /** HA entity used for room metadata/transport. */
    val primaryPlayerEntity: String = "",
    /** MLGW-facing HA media_player. When blank primaryPlayerEntity is used. */
    val mlgwEntity: String = "",
    /** Native Music Assistant player/queue ID. Blank means use the shared MA target. */
    val maPlayerId: String = "",
    val maPlayerName: String = "",
    val secondaryPlayers: List<SecondaryPlayerConfig> = emptyList(),
    val sourceOptions: List<String> = emptyList(),
    val bluetoothAnchors: List<BluetoothAnchorConfig> = emptyList(),
    /** Three spatial fingerprints captured in this room. */
    val bluetoothCalibrationPoints: List<BluetoothCalibrationPoint> = emptyList(),
    /** True when calibration found no strong local BLE device; retain this room unless another match is very strong. */
    val bluetoothQuietRoom: Boolean = false,
    val youtubeCastEntity: String = "",
    val kodiSourceName: String = "Kodi",
    val presenceEntity: String = "",
    val presenceValue: String = "",
    val powerOnDelayMs: Long = 900,
    val sourceConfirmTimeoutMs: Long = 4500,
    val linkSourceName: String = "Link",
    val auxSourceName: String = "A.AUX",
    val sharedPlaybackSource: SharedSource = SharedSource.LINK,
    val kodi: KodiConfig = KodiConfig(),
    val tiles: List<TileConfig> = defaultTiles()
) {
    val routeEntity: String get() = mlgwEntity.ifBlank { primaryPlayerEntity }

    fun sourceName(kind: SharedSource): String = when (kind) {
        SharedSource.LINK -> linkSourceName
        SharedSource.A_AUX -> auxSourceName
    }

    companion object {
        fun defaultTiles(): List<TileConfig> = listOf(
            TileConfig(id = "__SOURCE__", title = "Source", icon = "source", actionType = TileActionType.SELECT_SOURCE),
            TileConfig(
                title = "Morning news",
                icon = "news",
                actionType = TileActionType.HA_SERVICE,
                service = HaServiceSpec(
                    domain = "script",
                    service = "turn_on",
                    targetEntity = "script.play_the_morning_news"
                )
            ),
            TileConfig(title = "Music library", icon = "library", actionType = TileActionType.OPEN_LIBRARY),
            TileConfig(title = "Queue", icon = "queue", actionType = TileActionType.OPEN_QUEUE)
        )
    }
}

private fun mediaTiles(
    video: Boolean = false,
    includeCd: Boolean = true,
    includeNews: Boolean = true
): List<TileConfig> = buildList {
    add(TileConfig(id = "__SOURCE__", title = "Source", icon = "source", actionType = TileActionType.SELECT_SOURCE))
    if (includeNews) {
        add(
            TileConfig(
                title = "Morning news",
                icon = "news",
                actionType = TileActionType.HA_SERVICE,
                service = HaServiceSpec(
                    domain = "script",
                    service = "turn_on",
                    targetEntity = "script.play_the_morning_news"
                )
            )
        )
    }
    add(TileConfig(title = "Music library", icon = "library", actionType = TileActionType.OPEN_LIBRARY))
    if (video) add(TileConfig(title = "Video library", icon = "video", actionType = TileActionType.OPEN_KODI))
    add(TileConfig(title = "Queue", icon = "queue", actionType = TileActionType.OPEN_QUEUE))
}

private fun presetRooms(): List<RoomConfig> = listOf(
    RoomConfig(
        id = "lounge", name = "Lounge",
        primaryPlayerEntity = "media_player.bs3_2",
        mlgwEntity = "media_player.bs3_2",
        secondaryPlayers = listOf(
            SecondaryPlayerConfig(
                id = "lounge_mini", name = "Lounge Mini",
                haEntity = "media_player.lounge_mini_ma",
                maPlayerName = "Lounge Mini"
            )
        ),
        sourceOptions = listOf("CD", "Link", "A.AUX", "Kodi"),
        youtubeCastEntity = "",
        tiles = mediaTiles(video = true) + TileConfig(title = "YouTube", icon = "youtube", actionType = TileActionType.OPEN_YOUTUBE)
    ),
    RoomConfig(
        id = "dining", name = "Dining",
        primaryPlayerEntity = "media_player.bv10_32_2",
        mlgwEntity = "media_player.bv10_32_2",
        secondaryPlayers = listOf(
            SecondaryPlayerConfig(
                id = "dining_secondary", name = "Dining",
                haEntity = "media_player.dining",
                maPlayerName = "Dining",
                toggleEntity = "switch.bc9500"
            )
        ),
        sourceOptions = listOf("CD", "Link", "A.AUX", "Kodi"),
        tiles = mediaTiles(video = true) + TileConfig(title = "YouTube", icon = "youtube", actionType = TileActionType.OPEN_YOUTUBE)
    ),
    RoomConfig(
        id = "bedroom", name = "Bedroom",
        primaryPlayerEntity = "media_player.bv10_40",
        mlgwEntity = "media_player.bv10_40",
        sourceOptions = listOf("CD", "Link", "A.AUX", "Kodi"),
        tiles = mediaTiles(video = true) + TileConfig(title = "YouTube", icon = "youtube", actionType = TileActionType.OPEN_YOUTUBE)
    ),
    RoomConfig(
        id = "kitchen", name = "Kitchen",
        primaryPlayerEntity = "media_player.cuisine",
        mlgwEntity = "media_player.cuisine",
        sourceOptions = listOf("Link", "A.AUX", "Kodi"),
        tiles = mediaTiles(video = true, includeCd = false) + TileConfig(title = "YouTube", icon = "youtube", actionType = TileActionType.OPEN_YOUTUBE)
    ),
    RoomConfig(
        id = "bathroom", name = "Bathroom",
        primaryPlayerEntity = "media_player.bl3500_2",
        mlgwEntity = "media_player.bl3500_2",
        sourceOptions = listOf("CD", "Link", "A.AUX"),
        tiles = mediaTiles(video = false, includeCd = true)
    )
)

private fun migrateRoomId(id: String): String = when (id) {
    "bs3", "lounge_mini" -> "lounge"
    "bv10_32", "dining" -> "dining"
    "bv10_40" -> "bedroom"
    "cuisine" -> "kitchen"
    else -> id
}

data class AppSettings(
    val schemaVersion: Int = 3,
    val homeAssistantUrl: String = "",
    val homeAssistantToken: String = "",
    val musicAssistantUrl: String = "",
    val musicAssistantToken: String = "",
    val youtubeApiKey: String = "",
    val stremioStreamAddonManifests: List<String> = emptyList(),
    val bluetoothLocationEnabled: Boolean = true,
    /** Shared MA output used by Master Link rooms. The known player name is Link; ID is resolved at runtime if blank. */
    val sharedMaQueueId: String = "",
    val sharedMaQueueName: String = "Link",
    val globalMediaEntity: String = "media_player.global_media",
    val linkMediaPlayerEntity: String = "media_player.link",
    val automaticRoom: Boolean = true,
    /** Do not treat the only active room as the user's physical location unless explicitly enabled. */
    val activePlayerLocationFallback: Boolean = false,
    val lastRoomId: String = "lounge",
    val rooms: List<RoomConfig> = presetRooms()
)

data class HaEntityState(
    val entityId: String,
    val state: String,
    val attributes: JSONObject = JSONObject()
)

data class NowPlaying(
    val entityId: String = "",
    val state: String = "off",
    val title: String = "Nothing playing",
    val artist: String = "",
    val album: String = "",
    val imageUrl: String = "",
    val position: Double = 0.0,
    val duration: Double = 0.0,
    val volume: Double = 0.0,
    val source: String = ""
) {
    val isPlaying: Boolean get() = state == "playing"
    val isActive: Boolean get() = state in setOf("playing", "paused", "buffering")
}

data class MassMediaItem(
    val mediaType: String,
    val itemId: String,
    val provider: String,
    val uri: String,
    val name: String,
    val subtitle: String = "",
    val duration: Double = 0.0,
    val imageUrl: String = "",
    val playable: Boolean = true
)

data class MassPlayerInfo(
    val playerId: String,
    val displayName: String = "",
    val activeSource: String = ""
)

data class MassQueueInfo(
    val queueId: String,
    val displayName: String = "",
    val state: String = "idle",
    val currentIndex: Int = -1,
    val elapsedTime: Double = 0.0,
    val duration: Double = 0.0,
    val shuffleEnabled: Boolean = false,
    val repeatMode: String = "off",
    val crossfadeEnabled: Boolean = false,
    val autoplayEnabled: Boolean = false
)

data class MassQueueItem(
    val queueItemId: String,
    val index: Int,
    val uri: String,
    val name: String,
    val subtitle: String = "",
    val duration: Double = 0.0,
    val imageUrl: String = "",
    val mediaType: String = "track"
)

data class KodiNowPlaying(
    val playerId: Int = -1,
    val type: String = "",
    val title: String = "Kodi idle",
    val subtitle: String = "",
    val thumbnail: String = "",
    val playing: Boolean = false
)

data class KodiLibraryItem(
    val id: Int = -1,
    val label: String,
    val subtitle: String = "",
    val thumbnail: String = "",
    val file: String = "",
    val directory: Boolean = false,
    val mediaType: String = "",
    val season: Int = -1,
    val episode: Int = -1,
    val tvShowId: Int = -1,
    val year: Int = 0,
    val duration: Int = 0
)

data class KodiBrowseState(
    val type: KodiBrowseType = KodiBrowseType.HOME,
    val title: String = "Kodi library",
    val items: List<KodiLibraryItem> = emptyList(),
    val parentTvShowId: Int = -1,
    val parentSeason: Int = -1,
    val parentArtistId: Int = -1,
    val directory: String = "",
    val fileMedia: String = "files"
)

fun AppSettings.toJson(): JSONObject = JSONObject().apply {
    put("schemaVersion", schemaVersion)
    put("homeAssistantUrl", homeAssistantUrl)
    put("homeAssistantToken", homeAssistantToken)
    put("musicAssistantUrl", musicAssistantUrl)
    put("musicAssistantToken", musicAssistantToken)
    put("youtubeApiKey", youtubeApiKey)
    put("stremioStreamAddonManifests", JSONArray().apply { stremioStreamAddonManifests.forEach { put(it) } })
    put("bluetoothLocationEnabled", bluetoothLocationEnabled)
    put("sharedMaQueueId", sharedMaQueueId)
    put("sharedMaQueueName", sharedMaQueueName)
    put("globalMediaEntity", globalMediaEntity)
    put("linkMediaPlayerEntity", linkMediaPlayerEntity)
    put("automaticRoom", automaticRoom)
    put("activePlayerLocationFallback", activePlayerLocationFallback)
    put("lastRoomId", lastRoomId)
    put("rooms", JSONArray().apply { rooms.forEach { put(it.toJson()) } })
}

fun RoomConfig.toJson(): JSONObject = JSONObject().apply {
    put("id", id); put("name", name); put("primaryPlayerEntity", primaryPlayerEntity)
    put("mlgwEntity", mlgwEntity); put("maPlayerId", maPlayerId); put("maPlayerName", maPlayerName)
    put("secondaryPlayers", JSONArray().apply { secondaryPlayers.forEach { put(it.toJson()) } })
    put("sourceOptions", JSONArray().apply { sourceOptions.forEach { put(it) } })
    put("bluetoothAnchors", JSONArray().apply { bluetoothAnchors.forEach { put(it.toJson()) } })
    put("bluetoothCalibrationPoints", JSONArray().apply { bluetoothCalibrationPoints.forEach { put(it.toJson()) } })
    put("bluetoothQuietRoom", bluetoothQuietRoom)
    put("youtubeCastEntity", youtubeCastEntity)
    put("kodiSourceName", kodiSourceName)
    put("presenceEntity", presenceEntity); put("presenceValue", presenceValue)
    put("powerOnDelayMs", powerOnDelayMs); put("sourceConfirmTimeoutMs", sourceConfirmTimeoutMs)
    put("linkSourceName", linkSourceName); put("auxSourceName", auxSourceName)
    put("sharedPlaybackSource", sharedPlaybackSource.name)
    put("kodi", kodi.toJson())
    put("tiles", JSONArray().apply { tiles.forEach { put(it.toJson()) } })
}

fun KodiConfig.toJson(): JSONObject = JSONObject().apply {
    put("baseUrl", baseUrl); put("username", username); put("password", password)
}

fun SecondaryPlayerConfig.toJson(): JSONObject = JSONObject().apply {
    put("id", id); put("name", name); put("haEntity", haEntity)
    put("maPlayerId", maPlayerId); put("maPlayerName", maPlayerName); put("toggleEntity", toggleEntity)
}

fun BluetoothAnchorConfig.toJson(): JSONObject = JSONObject().apply {
    put("address", address); put("nameContains", nameContains); put("minRssi", minRssi)
}

fun BluetoothFingerprintSample.toJson(): JSONObject = JSONObject().apply {
    put("address", address); put("name", name); put("rssi", rssi)
}

fun BluetoothCalibrationPoint.toJson(): JSONObject = JSONObject().apply {
    put("point", point)
    put("samples", JSONArray().apply { samples.forEach { put(it.toJson()) } })
}

fun TileConfig.toJson(): JSONObject = JSONObject().apply {
    put("id", id); put("title", title); put("icon", icon); put("actionType", actionType.name)
    put("source", source)
    put("service", service.toJson())
}

fun HaServiceSpec.toJson(): JSONObject = JSONObject().apply {
    put("domain", domain); put("service", service); put("targetEntity", targetEntity); put("dataJson", dataJson)
}

fun appSettingsFromJson(obj: JSONObject): AppSettings {
    val schema = obj.optInt("schemaVersion", 1)
    val parsedRooms = obj.optJSONArray("rooms")?.let { arr ->
        (0 until arr.length()).mapNotNull { i -> arr.optJSONObject(i)?.let(::roomFromJson) }
    }
    val legacyIds = parsedRooms.orEmpty().map { it.id }.toSet()
    val rooms = if (schema < 3 && legacyIds.any { it in setOf("bs3", "lounge_mini", "bv10_32", "bv10_40", "cuisine") }) {
        presetRooms()
    } else parsedRooms?.takeIf { it.isNotEmpty() } ?: presetRooms()
    return AppSettings(
        schemaVersion = 3,
        homeAssistantUrl = obj.optString("homeAssistantUrl"),
        homeAssistantToken = obj.optString("homeAssistantToken"),
        musicAssistantUrl = obj.optString("musicAssistantUrl"),
        musicAssistantToken = obj.optString("musicAssistantToken"),
        youtubeApiKey = obj.optString("youtubeApiKey"),
        stremioStreamAddonManifests = obj.optJSONArray("stremioStreamAddonManifests")?.let { arr ->
            (0 until arr.length()).mapNotNull { i -> arr.optString(i).takeIf(String::isNotBlank) }
        } ?: emptyList(),
        bluetoothLocationEnabled = obj.optBoolean("bluetoothLocationEnabled", true),
        sharedMaQueueId = obj.optString("sharedMaQueueId"),
        sharedMaQueueName = obj.optString("sharedMaQueueName", "Link"),
        globalMediaEntity = obj.optString("globalMediaEntity", "media_player.global_media"),
        linkMediaPlayerEntity = obj.optString("linkMediaPlayerEntity", "media_player.link"),
        automaticRoom = obj.optBoolean("automaticRoom", true),
        activePlayerLocationFallback = obj.optBoolean("activePlayerLocationFallback", false),
        lastRoomId = migrateRoomId(obj.optString("lastRoomId", "lounge")),
        rooms = rooms
    )
}

fun roomFromJson(obj: JSONObject): RoomConfig = RoomConfig(
    id = obj.optString("id").ifBlank { UUID.randomUUID().toString() },
    name = obj.optString("name", "Room"),
    primaryPlayerEntity = obj.optString("primaryPlayerEntity"),
    mlgwEntity = obj.optString("mlgwEntity"),
    maPlayerId = obj.optString("maPlayerId"),
    maPlayerName = obj.optString("maPlayerName"),
    secondaryPlayers = obj.optJSONArray("secondaryPlayers")?.let { arr ->
        (0 until arr.length()).mapNotNull { i -> arr.optJSONObject(i)?.let(::secondaryPlayerFromJson) }
    } ?: emptyList(),
    sourceOptions = obj.optJSONArray("sourceOptions")?.let { arr ->
        (0 until arr.length()).mapNotNull { i -> arr.optString(i).takeIf(String::isNotBlank) }
    } ?: emptyList(),
    bluetoothAnchors = obj.optJSONArray("bluetoothAnchors")?.let { arr ->
        (0 until arr.length()).mapNotNull { i -> arr.optJSONObject(i)?.let(::bluetoothAnchorFromJson) }
    } ?: emptyList(),
    bluetoothCalibrationPoints = obj.optJSONArray("bluetoothCalibrationPoints")?.let { arr ->
        (0 until arr.length()).mapNotNull { i -> arr.optJSONObject(i)?.let(::bluetoothCalibrationPointFromJson) }
    } ?: emptyList(),
    bluetoothQuietRoom = obj.optBoolean("bluetoothQuietRoom", false),
    youtubeCastEntity = obj.optString("youtubeCastEntity"),
    kodiSourceName = obj.optString("kodiSourceName", "Kodi"),
    presenceEntity = obj.optString("presenceEntity"),
    presenceValue = obj.optString("presenceValue"),
    powerOnDelayMs = obj.optLong("powerOnDelayMs", 900),
    sourceConfirmTimeoutMs = obj.optLong("sourceConfirmTimeoutMs", 4500),
    linkSourceName = obj.optString("linkSourceName", "Link"),
    auxSourceName = obj.optString("auxSourceName", "A.AUX"),
    sharedPlaybackSource = runCatching { SharedSource.valueOf(obj.optString("sharedPlaybackSource", SharedSource.LINK.name)) }.getOrDefault(SharedSource.LINK),
    kodi = obj.optJSONObject("kodi")?.let(::kodiFromJson) ?: KodiConfig(),
    tiles = obj.optJSONArray("tiles")?.let { arr ->
        (0 until arr.length()).mapNotNull { i -> arr.optJSONObject(i)?.let(::tileFromJson) }
    }?.takeIf { it.isNotEmpty() }?.let { raw ->
        val roomId = obj.optString("id")
        val withoutLegacySource = raw.filterNot { tile ->
            tile.id == "__SOURCE__" ||
                (tile.actionType == TileActionType.SELECT_SOURCE && tile.source.equals("CD", true)) ||
                tile.title.equals("CD", true)
        }
        buildList {
            add(TileConfig(id = "__SOURCE__", title = "Source", icon = "source", actionType = TileActionType.SELECT_SOURCE))
            addAll(withoutLegacySource)
            if (roomId in setOf("lounge", "dining", "bedroom", "kitchen") && none { it.actionType == TileActionType.OPEN_YOUTUBE }) {
                add(TileConfig(title = "YouTube", icon = "youtube", actionType = TileActionType.OPEN_YOUTUBE))
            }
        }
    } ?: RoomConfig.defaultTiles()
)

fun kodiFromJson(obj: JSONObject) = KodiConfig(
    baseUrl = obj.optString("baseUrl"), username = obj.optString("username"), password = obj.optString("password")
)

fun secondaryPlayerFromJson(obj: JSONObject) = SecondaryPlayerConfig(
    id = obj.optString("id").ifBlank { UUID.randomUUID().toString() },
    name = obj.optString("name", "Secondary player"),
    haEntity = obj.optString("haEntity"),
    maPlayerId = obj.optString("maPlayerId"),
    maPlayerName = obj.optString("maPlayerName"),
    toggleEntity = obj.optString("toggleEntity")
)

fun bluetoothAnchorFromJson(obj: JSONObject) = BluetoothAnchorConfig(
    address = obj.optString("address"),
    nameContains = obj.optString("nameContains"),
    minRssi = obj.optInt("minRssi", -92)
)

fun bluetoothFingerprintSampleFromJson(obj: JSONObject) = BluetoothFingerprintSample(
    address = obj.optString("address"),
    name = obj.optString("name"),
    rssi = obj.optInt("rssi", -100)
)

fun bluetoothCalibrationPointFromJson(obj: JSONObject) = BluetoothCalibrationPoint(
    point = obj.optInt("point", 1),
    samples = obj.optJSONArray("samples")?.let { arr ->
        (0 until arr.length()).mapNotNull { i -> arr.optJSONObject(i)?.let(::bluetoothFingerprintSampleFromJson) }
    } ?: emptyList()
)

fun tileFromJson(obj: JSONObject): TileConfig {
    val rawType = obj.optString("actionType")
    val action = when (rawType) {
        "START_LINK" -> TileActionType.HA_SERVICE // v0.1 migration; Link is no longer a tile concept.
        else -> runCatching { TileActionType.valueOf(rawType) }.getOrDefault(TileActionType.HA_SERVICE)
    }
    return TileConfig(
        id = obj.optString("id").ifBlank { UUID.randomUUID().toString() },
        title = obj.optString("title", "Tile"),
        icon = obj.optString("icon", "music"),
        actionType = action,
        source = obj.optString("source"),
        service = obj.optJSONObject("service")?.let(::serviceFromJson) ?: HaServiceSpec()
    )
}

fun serviceFromJson(obj: JSONObject) = HaServiceSpec(
    domain = obj.optString("domain"), service = obj.optString("service"),
    targetEntity = obj.optString("targetEntity"), dataJson = obj.optString("dataJson", "{}")
)