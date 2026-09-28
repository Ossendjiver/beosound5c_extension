package au.com.homemedia.model

import org.json.JSONArray
import org.json.JSONObject
import java.util.UUID

enum class TileActionType { HA_SERVICE, OPEN_LIBRARY, OPEN_KODI, OPEN_QUEUE }
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
    val service: HaServiceSpec = HaServiceSpec()
)

data class KodiConfig(
    val baseUrl: String = "",
    val username: String = "",
    val password: String = ""
)

data class RoomConfig(
    val id: String = UUID.randomUUID().toString(),
    val name: String = "Room",
    /** HA entity used for room metadata/transport. */
    val primaryPlayerEntity: String = "",
    /** MLGW-facing HA media_player. When blank primaryPlayerEntity is used. */
    val mlgwEntity: String = "",
    /** Native Music Assistant player/queue ID. Blank means use the shared MA target. */
    val maPlayerId: String = "",
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
            TileConfig(
                title = "CD",
                icon = "disc",
                actionType = TileActionType.HA_SERVICE,
                service = HaServiceSpec(
                    domain = "media_player",
                    service = "select_source",
                    dataJson = "{\"source\":\"CD\"}"
                )
            ),
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

private fun presetRooms(): List<RoomConfig> = listOf(
    RoomConfig(id = "bathroom", name = "Bathroom", primaryPlayerEntity = "media_player.bl3500_2"),
    RoomConfig(id = "lounge_mini", name = "Lounge Mini", primaryPlayerEntity = "media_player.lounge_mini_ma"),
    RoomConfig(id = "dining", name = "Dining", primaryPlayerEntity = "media_player.dining"),
    RoomConfig(id = "cuisine", name = "Cuisine", primaryPlayerEntity = "media_player.cuisine"),
    RoomConfig(id = "bv10_40", name = "BV10-40", primaryPlayerEntity = "media_player.bv10_40"),
    RoomConfig(id = "bv10_32", name = "BV10-32", primaryPlayerEntity = "media_player.bv10_32_2"),
    RoomConfig(id = "bs3", name = "BS3", primaryPlayerEntity = "media_player.bs3_2")
)

data class AppSettings(
    val homeAssistantUrl: String = "",
    val homeAssistantToken: String = "",
    val musicAssistantUrl: String = "",
    val musicAssistantToken: String = "",
    /** Shared MA output used by Master Link rooms. The known player name is Link; ID is resolved at runtime if blank. */
    val sharedMaQueueId: String = "",
    val sharedMaQueueName: String = "Link",
    val globalMediaEntity: String = "media_player.global_media",
    val linkMediaPlayerEntity: String = "media_player.link",
    val automaticRoom: Boolean = true,
    /** Do not treat the only active room as the user's physical location unless explicitly enabled. */
    val activePlayerLocationFallback: Boolean = false,
    val lastRoomId: String = "bathroom",
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
    put("homeAssistantUrl", homeAssistantUrl)
    put("homeAssistantToken", homeAssistantToken)
    put("musicAssistantUrl", musicAssistantUrl)
    put("musicAssistantToken", musicAssistantToken)
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
    put("mlgwEntity", mlgwEntity); put("maPlayerId", maPlayerId)
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

fun TileConfig.toJson(): JSONObject = JSONObject().apply {
    put("id", id); put("title", title); put("icon", icon); put("actionType", actionType.name)
    put("service", service.toJson())
}

fun HaServiceSpec.toJson(): JSONObject = JSONObject().apply {
    put("domain", domain); put("service", service); put("targetEntity", targetEntity); put("dataJson", dataJson)
}

fun appSettingsFromJson(obj: JSONObject): AppSettings {
    val parsedRooms = obj.optJSONArray("rooms")?.let { arr ->
        (0 until arr.length()).mapNotNull { i -> arr.optJSONObject(i)?.let(::roomFromJson) }
    }
    return AppSettings(
        homeAssistantUrl = obj.optString("homeAssistantUrl"),
        homeAssistantToken = obj.optString("homeAssistantToken"),
        musicAssistantUrl = obj.optString("musicAssistantUrl"),
        musicAssistantToken = obj.optString("musicAssistantToken"),
        sharedMaQueueId = obj.optString("sharedMaQueueId"),
        sharedMaQueueName = obj.optString("sharedMaQueueName", "Link"),
        globalMediaEntity = obj.optString("globalMediaEntity", "media_player.global_media"),
        linkMediaPlayerEntity = obj.optString("linkMediaPlayerEntity", "media_player.link"),
        automaticRoom = obj.optBoolean("automaticRoom", true),
        activePlayerLocationFallback = obj.optBoolean("activePlayerLocationFallback", false),
        lastRoomId = obj.optString("lastRoomId", "bathroom"),
        rooms = parsedRooms?.takeIf { it.isNotEmpty() } ?: presetRooms()
    )
}

fun roomFromJson(obj: JSONObject): RoomConfig = RoomConfig(
    id = obj.optString("id").ifBlank { UUID.randomUUID().toString() },
    name = obj.optString("name", "Room"),
    primaryPlayerEntity = obj.optString("primaryPlayerEntity"),
    mlgwEntity = obj.optString("mlgwEntity"),
    maPlayerId = obj.optString("maPlayerId"),
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
    }?.takeIf { it.isNotEmpty() } ?: RoomConfig.defaultTiles()
)

fun kodiFromJson(obj: JSONObject) = KodiConfig(
    baseUrl = obj.optString("baseUrl"), username = obj.optString("username"), password = obj.optString("password")
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
        service = obj.optJSONObject("service")?.let(::serviceFromJson) ?: HaServiceSpec()
    )
}

fun serviceFromJson(obj: JSONObject) = HaServiceSpec(
    domain = obj.optString("domain"), service = obj.optString("service"),
    targetEntity = obj.optString("targetEntity"), dataJson = obj.optString("dataJson", "{}")
)