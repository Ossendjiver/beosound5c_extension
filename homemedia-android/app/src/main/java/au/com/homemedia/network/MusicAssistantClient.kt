package au.com.homemedia.network

import au.com.homemedia.model.MassCategory
import au.com.homemedia.model.MassMediaItem
import au.com.homemedia.model.MassQueueInfo
import au.com.homemedia.model.MassPlayerInfo
import au.com.homemedia.model.MassQueueItem
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeout
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONArray
import org.json.JSONObject
import java.net.URLEncoder
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicLong

class MusicAssistantClient {
    private val client = OkHttpClient.Builder()
        .connectTimeout(8, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        .build()
    private val ids = AtomicLong(0)
    private var baseUrl = ""
    private var token = ""
    private var debugLog: ((String, String) -> Unit)? = null

    fun setDebugLogger(logger: ((String, String) -> Unit)?) {
        debugLog = logger
    }

    fun configure(url: String, accessToken: String) {
        baseUrl = url.trim().trimEnd('/').removeSuffix("/api")
        token = accessToken.trim()
        debugLog?.invoke("MA", "Configured base URL=$baseUrl token=${if (token.isBlank()) "missing" else "present"}")
    }

    fun imageBaseUrl(): String = baseUrl
    fun authToken(): String = token

    suspend fun test(): Boolean = runCatching {
        command("player_queues/all")
        true
    }.getOrDefault(false)

    suspend fun library(category: MassCategory, search: String = "", limit: Int = 0, offset: Int = 0): List<MassMediaItem> {
        // limit > 0 is useful for targeted lookups (for example current-album resolution).
        // limit == 0 means fetch the whole library in pages so the UI is not silently truncated.
        val pageSize = if (limit > 0) limit.coerceAtMost(500) else 250
        val out = mutableListOf<MassMediaItem>()
        var pageOffset = offset
        while (true) {
            val args = JSONObject().apply {
                put("limit", pageSize)
                put("offset", pageOffset)
                put("order_by", "sort_name")
                if (search.isNotBlank()) put("search", search)
                if (category == MassCategory.GENRES) put("hide_empty", true)
            }
            val result = command("music/${category.apiName}/library_items", args) as? JSONArray ?: JSONArray()
            for (i in 0 until result.length()) result.optJSONObject(i)?.let(::parseMediaItem)?.let(out::add)
            if (limit > 0 || result.length() < pageSize) break
            pageOffset += result.length()
        }
        return out
    }

    suspend fun itemChildren(item: MassMediaItem): List<MassMediaItem> = when (item.mediaType.lowercase()) {
        "album" -> mediaArrayCommand(
            "music/albums/album_tracks",
            JSONObject().put("item_id", item.itemId).put("provider_instance_id_or_domain", provider(item)).put("in_library_only", false)
        )
        "artist" -> {
            val albums = mediaArrayCommand(
                "music/artists/artist_albums",
                JSONObject().put("item_id", item.itemId).put("provider_instance_id_or_domain", provider(item))
            )
            val tracks = mediaArrayCommand(
                "music/artists/artist_tracks",
                JSONObject().put("item_id", item.itemId).put("provider_instance_id_or_domain", provider(item))
            )
            albums + tracks
        }
        "playlist" -> mediaArrayCommand(
            "music/playlists/playlist_tracks",
            JSONObject().put("item_id", item.itemId).put("provider_instance_id_or_domain", provider(item))
        )
        "podcast" -> mediaArrayCommand(
            "music/podcasts/podcast_episodes",
            JSONObject().put("item_id", item.itemId).put("provider_instance_id_or_domain", provider(item))
        )
        "radio" -> mediaArrayCommand(
            "music/radios/radio_tracks",
            JSONObject().put("item_id", item.itemId).put("provider_instance_id_or_domain", provider(item))
        )
        "genre" -> mediaArrayCommand(
            "music/genres/tracks",
            JSONObject().put("item_id", item.itemId).put("limit", 250).put("offset", 0).put("order_by", "sort_name")
        )
        else -> emptyList()
    }

    suspend fun findAlbum(name: String, artist: String = ""): MassMediaItem? {
        if (name.isBlank()) return null
        val candidates = library(MassCategory.ALBUMS, search = name, limit = 25)
        return candidates.firstOrNull { it.name.equals(name, true) && (artist.isBlank() || it.subtitle.contains(artist, true)) }
            ?: candidates.firstOrNull { it.name.equals(name, true) }
            ?: candidates.firstOrNull()
    }

    suspend fun play(queueId: String, uri: String, option: String = "replace", startItem: String? = null) {
        require(queueId.isNotBlank()) { "Music Assistant queue is not configured" }
        require(uri.isNotBlank()) { "This library item does not have a playable URI" }
        val args = JSONObject().put("queue_id", queueId).put("media", uri).put("option", option)
        if (!startItem.isNullOrBlank()) args.put("start_item", startItem)
        command("player_queues/play_media", args)
    }

    suspend fun players(): List<MassPlayerInfo> {
        val result = command("players/all") as? JSONArray ?: JSONArray()
        return (0 until result.length()).mapNotNull { i ->
            val obj = result.optJSONObject(i) ?: return@mapNotNull null
            val id = obj.optString("player_id")
            if (id.isBlank()) return@mapNotNull null
            MassPlayerInfo(
                playerId = id,
                displayName = obj.optString("display_name").ifBlank { obj.optString("name").ifBlank { id } },
                activeSource = obj.optString("active_source")
            )
        }
    }

    /** Resolve the queue currently owned/used by a native MA player, mirroring MA frontend behaviour. */
    suspend fun resolvePlayerId(explicitId: String, nameHint: String): String {
        if (explicitId.isNotBlank()) return explicitId
        require(nameHint.isNotBlank()) { "Music Assistant player is not configured" }
        val all = players()
        return all.firstOrNull { it.displayName.equals(nameHint, ignoreCase = true) }?.playerId
            ?: all.firstOrNull { it.playerId.equals(nameHint, ignoreCase = true) }?.playerId
            ?: error("Music Assistant player '$nameHint' was not found")
    }

    suspend fun resolvePlayerQueueId(playerId: String): String {
        require(playerId.isNotBlank()) { "Music Assistant player ID is not configured" }
        val activeSource = players().firstOrNull { it.playerId == playerId }?.activeSource.orEmpty()
        if (activeSource.isBlank()) return playerId
        val queueIds = queues().map { it.queueId }.toSet()
        return activeSource.takeIf { it in queueIds } ?: playerId
    }

    suspend fun queues(): List<MassQueueInfo> {
        val result = command("player_queues/all") as? JSONArray ?: JSONArray()
        return (0 until result.length()).mapNotNull { i -> result.optJSONObject(i)?.let(::parseQueue) }
    }

    suspend fun resolveQueueId(explicitId: String, nameHint: String): String {
        if (explicitId.isNotBlank()) return explicitId
        val all = queues()
        return all.firstOrNull { it.displayName.equals(nameHint, ignoreCase = true) }?.queueId
            ?: all.firstOrNull { it.queueId.equals(nameHint, ignoreCase = true) }?.queueId
            ?: error("Music Assistant queue '$nameHint' was not found. Set its queue ID in Settings.")
    }

    suspend fun queue(queueId: String): MassQueueInfo? = queues().firstOrNull { it.queueId == queueId }

    suspend fun queueItems(queueId: String, limit: Int = 0, offset: Int = 0): List<MassQueueItem> {
        val pageSize = if (limit > 0) limit.coerceAtMost(500) else 500
        val out = mutableListOf<MassQueueItem>()
        var pageOffset = offset
        while (true) {
            val result = command(
                "player_queues/items",
                JSONObject().put("queue_id", queueId).put("limit", pageSize).put("offset", pageOffset)
            ) as? JSONArray ?: JSONArray()
            for (i in 0 until result.length()) result.optJSONObject(i)?.let { parseQueueItem(it, i + pageOffset) }?.let(out::add)
            if (limit > 0 || result.length() < pageSize) break
            pageOffset += result.length()
        }
        return out
    }

    suspend fun queueClear(queueId: String) = queueCommand("clear", queueId)
    suspend fun queuePlayIndex(queueId: String, indexOrId: Any) = queueCommand("play_index", queueId, JSONObject().put("index", indexOrId))
    suspend fun queueMove(queueId: String, queueItemId: String, shift: Int) = queueCommand("move_item", queueId, JSONObject().put("queue_item_id", queueItemId).put("pos_shift", shift))
    suspend fun queueMoveEnd(queueId: String, queueItemId: String) = queueCommand("move_item_end", queueId, JSONObject().put("queue_item_id", queueItemId))
    suspend fun queueDelete(queueId: String, itemIdOrIndex: Any) = queueCommand("delete_item", queueId, JSONObject().put("item_id_or_index", itemIdOrIndex))
    suspend fun queueSeek(queueId: String, seconds: Double) = queueCommand("seek", queueId, JSONObject().put("position", seconds.coerceAtLeast(0.0)))
    suspend fun queueSkip(queueId: String, seconds: Int) = queueCommand("skip", queueId, JSONObject().put("seconds", seconds))
    suspend fun queueShuffle(queueId: String, enabled: Boolean) = queueCommand("shuffle", queueId, JSONObject().put("shuffle_enabled", enabled))
    suspend fun queueRepeat(queueId: String, mode: String) = queueCommand("repeat", queueId, JSONObject().put("repeat_mode", mode))
    suspend fun queueCrossfade(queueId: String, enabled: Boolean) = queueCommand("crossfade", queueId, JSONObject().put("crossfade_enabled", enabled))
    suspend fun queueAutoplay(queueId: String, enabled: Boolean) = queueCommand("autoplay", queueId, JSONObject().put("autoplay_enabled", enabled))
    suspend fun queueSetPlaybackSpeed(queueId: String, queueItemId: String, speed: Double) = queueCommand(
        "set_playback_speed", queueId, JSONObject().put("queue_item_id", queueItemId).put("speed", speed)
    )
    suspend fun queueOverlay(queueId: String, enabled: Boolean, source: String = "", volume: Int? = null) {
        val args = JSONObject().put("enabled", enabled)
        if (source.isNotBlank()) args.put("source", source)
        if (volume != null) args.put("volume", volume.coerceIn(0, 200))
        queueCommand("overlay", queueId, args)
    }
    suspend fun queueSaveAsPlaylist(queueId: String, name: String) {
        require(name.isNotBlank()) { "Playlist name is required" }
        command("player_queues/save_as_playlist", JSONObject().put("queue_id", queueId).put("name", name))
    }

    suspend fun transferQueue(sourceQueueId: String, targetQueueId: String, autoPlay: Boolean = true) {
        require(sourceQueueId.isNotBlank() && targetQueueId.isNotBlank()) { "Both MA queue IDs are required for transfer" }
        if (sourceQueueId == targetQueueId) return
        command(
            "player_queues/transfer",
            JSONObject().put("source_queue_id", sourceQueueId).put("target_queue_id", targetQueueId).put("auto_play", autoPlay)
        )
    }

    suspend fun playerGroup(playerId: String, targetPlayer: String) {
        require(playerId.isNotBlank() && targetPlayer.isNotBlank()) { "Both MA player IDs are required for grouping" }
        if (playerId == targetPlayer) return
        command("players/cmd/group", JSONObject().put("player_id", playerId).put("target_player", targetPlayer))
    }

    suspend fun playerUngroup(playerId: String) {
        if (playerId.isBlank()) return
        command("players/cmd/ungroup", JSONObject().put("player_id", playerId))
    }

    suspend fun playerPause(playerId: String) {
        if (playerId.isBlank()) return
        command("players/cmd/pause", JSONObject().put("player_id", playerId))
    }

    suspend fun playerStop(playerId: String) {
        if (playerId.isBlank()) return
        command("players/cmd/stop", JSONObject().put("player_id", playerId))
    }

    suspend fun playerPlayPause(playerId: String) {
        if (playerId.isBlank()) return
        command("players/cmd/play_pause", JSONObject().put("player_id", playerId))
    }

    suspend fun playerNext(playerId: String) {
        if (playerId.isBlank()) return
        command("players/cmd/next", JSONObject().put("player_id", playerId))
    }

    suspend fun playerPrevious(playerId: String) {
        if (playerId.isBlank()) return
        command("players/cmd/previous", JSONObject().put("player_id", playerId))
    }

    private suspend fun queueCommand(commandName: String, queueId: String, extra: JSONObject = JSONObject()) {
        require(queueId.isNotBlank()) { "Music Assistant queue is not configured" }
        val args = JSONObject(extra.toString()).put("queue_id", queueId)
        command("player_queues/$commandName", args)
    }

    private suspend fun mediaArrayCommand(commandName: String, args: JSONObject): List<MassMediaItem> {
        val result = command(commandName, args) as? JSONArray ?: JSONArray()
        return (0 until result.length()).mapNotNull { i -> result.optJSONObject(i)?.let(::parseMediaItem) }
    }

    private suspend fun command(command: String, args: JSONObject = JSONObject()): Any? = withContext(Dispatchers.IO) {
        require(baseUrl.isNotBlank()) { "Music Assistant URL is not configured" }
        require(token.isNotBlank()) { "Music Assistant token is not configured" }

        val wsUrl = when {
            baseUrl.startsWith("https://") -> "wss://" + baseUrl.removePrefix("https://") + "/ws"
            baseUrl.startsWith("http://") -> "ws://" + baseUrl.removePrefix("http://") + "/ws"
            baseUrl.startsWith("wss://") || baseUrl.startsWith("ws://") -> baseUrl.trimEnd('/') + "/ws"
            else -> "ws://" + baseUrl.trimEnd('/') + "/ws"
        }

        val authId = "auth-" + ids.incrementAndGet()
        val commandId = ids.incrementAndGet().toString()
        val result = CompletableDeferred<Any?>()
        val authDone = CompletableDeferred<Unit>()
        val partial = mutableListOf<Any?>()

        val request = Request.Builder().url(wsUrl).build()
        val safeArgs = args.toString().take(700)
        debugLog?.invoke(
            "MA",
            "WS connect " + wsUrl.replace(Regex("(?<=://)[^/]+"), "<host>") +
                " command=" + command + " args=" + safeArgs
        )

        val socket = client.newWebSocket(request, object : okhttp3.WebSocketListener() {
            override fun onOpen(webSocket: okhttp3.WebSocket, response: okhttp3.Response) {
                val auth = JSONObject()
                    .put("message_id", authId)
                    .put("command", "auth")
                    .put("args", JSONObject().put("token", token))
                webSocket.send(auth.toString())
            }

            override fun onMessage(webSocket: okhttp3.WebSocket, text: String) {
                val obj = runCatching { JSONObject(text) }.getOrElse { error ->
                    debugLog?.invoke(
                        "MA",
                        "invalid websocket JSON command=" + command +
                            " error=" + (error.message ?: error.javaClass.simpleName) +
                            " payload=" + text.replace("\n", " ").take(500)
                    )
                    return
                }
                val messageId = obj.opt("message_id")?.toString().orEmpty()
                if (messageId == authId) {
                    if (obj.has("error_code") || obj.has("error")) {
                        val msg = obj.optString("details").ifBlank { obj.optString("error") }.ifBlank { "Music Assistant authentication failed" }
                        if (!authDone.isCompleted) authDone.completeExceptionally(IllegalStateException(msg))
                        if (!result.isCompleted) result.completeExceptionally(IllegalStateException(msg))
                        return
                    }
                    if (!authDone.isCompleted) authDone.complete(Unit)
                    val payload = JSONObject()
                        .put("message_id", commandId)
                        .put("command", command)
                        .put("args", args)
                    webSocket.send(payload.toString())
                    debugLog?.invoke("MA", "command sent=" + command)
                    return
                }
                if (messageId != commandId) return

                if (obj.has("error_code") || obj.has("error")) {
                    val msg = obj.optString("details")
                        .ifBlank { obj.optString("error") }
                        .ifBlank { "Music Assistant command failed" }
                    debugLog?.invoke("MA", "command error=" + command + " msg=" + msg)
                    if (!result.isCompleted) result.completeExceptionally(IllegalStateException(msg))
                    return
                }

                val raw = obj.opt("result")
                val isPartial = obj.optBoolean("partial", false)
                if (isPartial) {
                    when (raw) {
                        is JSONArray -> for (i in 0 until raw.length()) partial.add(raw.opt(i))
                        null, JSONObject.NULL -> Unit
                        else -> partial.add(raw)
                    }
                    debugLog?.invoke("MA", "partial command=" + command + " accumulated=" + partial.size)
                    return
                }

                val finalResult: Any? = if (partial.isNotEmpty()) {
                    when (raw) {
                        is JSONArray -> for (i in 0 until raw.length()) partial.add(raw.opt(i))
                        null, JSONObject.NULL -> Unit
                        else -> partial.add(raw)
                    }
                    JSONArray(partial)
                } else raw.takeUnless { it === JSONObject.NULL }

                val resultSummary = when (finalResult) {
                    is JSONArray -> "JSONArray count=" + finalResult.length()
                    is JSONObject -> "JSONObject keys=" + finalResult.length()
                    null -> "null"
                    else -> finalResult.javaClass.simpleName
                }
                debugLog?.invoke("MA", "command result=" + command + " " + resultSummary)
                if (!result.isCompleted) result.complete(finalResult)
            }

            override fun onFailure(webSocket: okhttp3.WebSocket, t: Throwable, response: okhttp3.Response?) {
                debugLog?.invoke("MA", "WS failure command=" + command + " error=" + (t.message ?: t.javaClass.simpleName))
                if (!authDone.isCompleted) authDone.completeExceptionally(t)
                if (!result.isCompleted) result.completeExceptionally(t)
            }
        })

        try {
            withTimeout(15_000) {
                authDone.await()
                result.await()
            }
        } finally {
            socket.close(1000, "done")
        }
    }

    private fun provider(item: MassMediaItem): String = item.provider.ifBlank { "library" }

    private fun parseMediaItem(obj: JSONObject): MassMediaItem? {
        val name = obj.optString("name").ifBlank { obj.optString("label") }
        if (name.isBlank()) return null
        val mediaType = obj.optString("media_type").ifBlank {
            when {
                obj.has("track_number") || obj.has("album") -> "track"
                obj.has("publisher") && obj.has("total_episodes") -> "podcast"
                else -> ""
            }
        }
        val subtitle = when {
            artistsText(obj).isNotBlank() -> artistsText(obj)
            obj.optString("album").isNotBlank() -> obj.optString("album")
            obj.optString("publisher").isNotBlank() -> obj.optString("publisher")
            obj.optString("owner").isNotBlank() -> obj.optString("owner")
            obj.optInt("year", 0) > 0 -> obj.optInt("year").toString()
            else -> ""
        }
        return MassMediaItem(
            mediaType = mediaType,
            itemId = obj.opt("item_id")?.toString().orEmpty(),
            provider = obj.optString("provider", "library"),
            uri = obj.optString("uri"),
            name = name,
            subtitle = subtitle,
            duration = obj.optDouble("duration", 0.0),
            imageUrl = imageUrl(obj),
            playable = obj.optBoolean("is_playable", mediaType !in setOf("artist", "genre"))
        )
    }

    private fun parseQueue(obj: JSONObject): MassQueueInfo? {
        val id = obj.optString("queue_id").ifBlank { obj.optString("player_id") }
        if (id.isBlank()) return null
        return MassQueueInfo(
            queueId = id,
            displayName = obj.optString("display_name").ifBlank { obj.optString("name").ifBlank { id } },
            state = obj.optString("state", "idle"),
            currentIndex = if (obj.isNull("current_index")) -1 else obj.optInt("current_index", -1),
            elapsedTime = obj.optDouble("elapsed_time", 0.0),
            duration = obj.optDouble("duration", 0.0),
            shuffleEnabled = obj.optBoolean("shuffle_enabled", false),
            repeatMode = obj.optString("repeat_mode", "off"),
            crossfadeEnabled = obj.optBoolean("crossfade_enabled", false),
            autoplayEnabled = obj.optBoolean("autoplay", obj.optBoolean("autoplay_enabled", false))
        )
    }

    private fun parseQueueItem(obj: JSONObject, fallbackIndex: Int): MassQueueItem? {
        val media = obj.optJSONObject("media_item") ?: obj.optJSONObject("media") ?: obj
        val name = obj.optString("name").ifBlank { media.optString("name").ifBlank { obj.optString("label") } }
        if (name.isBlank()) return null
        val artists = artistsText(media).ifBlank { artistsText(obj) }
        val album = when (val albumObj = media.opt("album")) {
            is JSONObject -> albumObj.optString("name")
            is String -> albumObj
            else -> ""
        }
        return MassQueueItem(
            queueItemId = obj.optString("queue_item_id").ifBlank { obj.optString("item_id").ifBlank { fallbackIndex.toString() } },
            index = obj.optInt("index", fallbackIndex),
            uri = obj.optString("uri").ifBlank { media.optString("uri") },
            name = name,
            subtitle = listOf(artists, album).filter { it.isNotBlank() }.distinct().joinToString(" · "),
            duration = obj.optDouble("duration", media.optDouble("duration", 0.0)),
            imageUrl = imageUrl(media).ifBlank { imageUrl(obj) },
            mediaType = media.optString("media_type", "track")
        )
    }

    private fun artistsText(obj: JSONObject): String {
        val artists = obj.optJSONArray("artists")
        if (artists != null) {
            return (0 until artists.length()).mapNotNull { i ->
                when (val item = artists.opt(i)) {
                    is JSONObject -> item.optString("name").takeIf { it.isNotBlank() }
                    is String -> item
                    else -> null
                }
            }.joinToString(", ")
        }
        return when (val artist = obj.opt("artist")) {
            is JSONObject -> artist.optString("name")
            is String -> artist
            else -> ""
        }
    }

    private fun imageUrl(obj: JSONObject): String {
        val metadata = obj.optJSONObject("metadata")
        val images = metadata?.optJSONArray("images") ?: obj.optJSONArray("images") ?: return ""
        val image = (0 until images.length()).mapNotNull { images.optJSONObject(it) }.firstOrNull() ?: return ""
        val proxyId = image.optString("proxy_id")
        if (proxyId.isNotBlank()) return "${baseUrl.trimEnd('/')}/imageproxy/$proxyId?size=512"
        val path = image.optString("path")
        if (path.startsWith("http") && image.optBoolean("remotely_accessible", false)) return path
        if (path.isBlank()) return ""
        val provider = URLEncoder.encode(image.optString("provider"), "UTF-8")
        val encodedPath = URLEncoder.encode(path, "UTF-8")
        return "${baseUrl.trimEnd('/')}/imageproxy?path=$encodedPath&provider=$provider&size=512"
    }
}