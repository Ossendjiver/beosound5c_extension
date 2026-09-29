package au.com.homemedia.network

import au.com.homemedia.model.KodiBrowseType
import au.com.homemedia.model.KodiConfig
import au.com.homemedia.model.KodiLibraryItem
import au.com.homemedia.model.KodiNowPlaying
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.Credentials
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.net.URLEncoder
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicLong

class KodiClient {
    private val client = OkHttpClient.Builder().connectTimeout(6, TimeUnit.SECONDS).readTimeout(20, TimeUnit.SECONDS).build()
    private val ids = AtomicLong(0)

    suspend fun nowPlaying(config: KodiConfig): KodiNowPlaying {
        if (config.baseUrl.isBlank()) return KodiNowPlaying()
        val active = rpc(config, "Player.GetActivePlayers") as? JSONArray ?: return KodiNowPlaying()
        val player = active.optJSONObject(0) ?: return KodiNowPlaying()
        val id = player.optInt("playerid", -1)
        if (id < 0) return KodiNowPlaying()
        val item = rpc(
            config,
            "Player.GetItem",
            JSONObject().put("playerid", id).put(
                "properties", JSONArray(listOf("title", "album", "artist", "showtitle", "season", "episode", "thumbnail", "file"))
            )
        ) as? JSONObject ?: JSONObject()
        val current = item.optJSONObject("item") ?: JSONObject()
        val props = rpc(config, "Player.GetProperties", JSONObject().put("playerid", id).put("properties", JSONArray(listOf("speed")))) as? JSONObject
        val title = current.optString("label").ifBlank { current.optString("title", "Kodi") }
        val subtitle = when {
            current.optString("showtitle").isNotBlank() -> "${current.optString("showtitle")} · S${current.optInt("season")}E${current.optInt("episode")}"
            current.optString("album").isNotBlank() -> current.optString("album")
            else -> current.optJSONArray("artist")?.optString(0).orEmpty()
        }
        return KodiNowPlaying(
            playerId = id,
            type = player.optString("type"),
            title = title,
            subtitle = subtitle,
            thumbnail = imageUrl(config, current.optString("thumbnail")),
            playing = (props?.optInt("speed", 0) ?: 0) > 0
        )
    }

    suspend fun input(config: KodiConfig, method: String) { rpc(config, method) }

    suspend fun player(config: KodiConfig, playerId: Int, method: String, extra: JSONObject = JSONObject()) {
        if (playerId < 0) return
        val params = JSONObject(extra.toString()).put("playerid", playerId)
        rpc(config, method, params)
    }

    suspend fun setVolume(config: KodiConfig, volume: Int) {
        rpc(config, "Application.SetVolume", JSONObject().put("volume", volume.coerceIn(0, 100)))
    }

    suspend fun movies(config: KodiConfig): List<KodiLibraryItem> = paged(
        config, "VideoLibrary.GetMovies", "movies",
        JSONArray(listOf("title", "thumbnail", "year", "runtime", "file", "playcount", "plot")),
        sortMethod = "title"
    ) { parseVideo(it, "movie", "movieid") }

    suspend fun tvShows(config: KodiConfig): List<KodiLibraryItem> = paged(
        config, "VideoLibrary.GetTVShows", "tvshows",
        JSONArray(listOf("title", "thumbnail", "year", "file", "plot")),
        sortMethod = "title"
    ) { parseVideo(it, "tvshow", "tvshowid") }

    suspend fun seasons(config: KodiConfig, tvShowId: Int): List<KodiLibraryItem> = paged(
        config, "VideoLibrary.GetSeasons", "seasons",
        JSONArray(listOf("season", "showtitle", "thumbnail", "episode")),
        baseParams = JSONObject().put("tvshowid", tvShowId),
        sortMethod = "season"
    ) { obj ->
        KodiLibraryItem(
            id = obj.optInt("seasonid", obj.optInt("season", -1)),
            label = obj.optString("label").ifBlank { "Season ${obj.optInt("season")}" },
            subtitle = obj.optString("showtitle"),
            thumbnail = imageUrl(config, obj.optString("thumbnail")),
            mediaType = "season",
            season = obj.optInt("season", -1),
            tvShowId = tvShowId
        )
    }

    suspend fun episodes(config: KodiConfig, tvShowId: Int, season: Int): List<KodiLibraryItem> = paged(
        config, "VideoLibrary.GetEpisodes", "episodes",
        JSONArray(listOf("title", "showtitle", "season", "episode", "thumbnail", "runtime", "file", "playcount")),
        baseParams = JSONObject().put("tvshowid", tvShowId).put("season", season),
        sortMethod = "episode"
    ) { parseVideo(it, "episode", "episodeid") }

    suspend fun musicVideos(config: KodiConfig): List<KodiLibraryItem> = paged(
        config, "VideoLibrary.GetMusicVideos", "musicvideos",
        JSONArray(listOf("title", "thumbnail", "artist", "album", "year", "runtime", "file")),
        sortMethod = "artist"
    ) { obj ->
        val artist = arrayText(obj.optJSONArray("artist"))
        KodiLibraryItem(
            id = obj.optInt("musicvideoid", -1),
            label = obj.optString("label").ifBlank { obj.optString("title") },
            subtitle = listOf(artist, obj.optString("album"), obj.optInt("year", 0).takeIf { it > 0 }?.toString().orEmpty()).filter { it.isNotBlank() }.joinToString(" · "),
            thumbnail = imageUrl(config, obj.optString("thumbnail")),
            file = obj.optString("file"),
            mediaType = "musicvideo",
            year = obj.optInt("year", 0),
            duration = obj.optInt("runtime", 0)
        )
    }

    suspend fun artists(config: KodiConfig): List<KodiLibraryItem> = paged(
        config, "AudioLibrary.GetArtists", "artists",
        JSONArray(listOf("thumbnail", "genre")),
        baseParams = JSONObject().put("albumartistsonly", false),
        sortMethod = "artist"
    ) { obj ->
        KodiLibraryItem(
            id = obj.optInt("artistid", -1),
            label = obj.optString("label").ifBlank { obj.optString("artist") },
            subtitle = arrayText(obj.optJSONArray("genre")),
            thumbnail = imageUrl(config, obj.optString("thumbnail")),
            mediaType = "artist"
        )
    }

    suspend fun albums(config: KodiConfig, artistId: Int = -1): List<KodiLibraryItem> {
        val base = JSONObject()
        if (artistId >= 0) base.put("artistid", artistId)
        return paged(
            config, "AudioLibrary.GetAlbums", "albums",
            JSONArray(listOf("title", "artist", "thumbnail", "year", "genre")),
            baseParams = base,
            sortMethod = "album"
        ) { obj ->
            KodiLibraryItem(
                id = obj.optInt("albumid", -1),
                label = obj.optString("label").ifBlank { obj.optString("title") },
                subtitle = listOf(arrayText(obj.optJSONArray("artist")), obj.optInt("year", 0).takeIf { it > 0 }?.toString().orEmpty()).filter { it.isNotBlank() }.joinToString(" · "),
                thumbnail = imageUrl(config, obj.optString("thumbnail")),
                mediaType = "album",
                year = obj.optInt("year", 0)
            )
        }
    }

    suspend fun songs(config: KodiConfig, albumId: Int = -1, artistId: Int = -1): List<KodiLibraryItem> {
        val base = JSONObject()
        if (albumId >= 0) base.put("albumid", albumId)
        if (artistId >= 0) base.put("artistid", artistId)
        return paged(
            config, "AudioLibrary.GetSongs", "songs",
            JSONArray(listOf("title", "artist", "album", "thumbnail", "duration", "track", "file")),
            baseParams = base,
            sortMethod = if (albumId >= 0) "track" else "title"
        ) { obj ->
            KodiLibraryItem(
                id = obj.optInt("songid", -1),
                label = obj.optString("label").ifBlank { obj.optString("title") },
                subtitle = listOf(arrayText(obj.optJSONArray("artist")), obj.optString("album")).filter { it.isNotBlank() }.joinToString(" · "),
                thumbnail = imageUrl(config, obj.optString("thumbnail")),
                file = obj.optString("file"),
                mediaType = "song",
                duration = obj.optInt("duration", 0)
            )
        }
    }

    suspend fun sources(config: KodiConfig, media: String): List<KodiLibraryItem> {
        val result = rpc(config, "Files.GetSources", JSONObject().put("media", media)) as? JSONObject ?: JSONObject()
        val arr = result.optJSONArray("sources") ?: JSONArray()
        return (0 until arr.length()).mapNotNull { i ->
            val obj = arr.optJSONObject(i) ?: return@mapNotNull null
            KodiLibraryItem(
                label = obj.optString("label").ifBlank { obj.optString("file") },
                file = obj.optString("file"),
                directory = true,
                mediaType = media
            )
        }
    }

    suspend fun directory(config: KodiConfig, directory: String, media: String): List<KodiLibraryItem> {
        val result = rpc(
            config,
            "Files.GetDirectory",
            JSONObject().put("directory", directory).put("media", media).put(
                "properties", JSONArray(listOf("title", "artist", "album", "thumbnail", "duration", "file"))
            ).put("sort", JSONObject().put("method", "label").put("order", "ascending").put("ignorearticle", true))
        ) as? JSONObject ?: JSONObject()
        val arr = result.optJSONArray("files") ?: JSONArray()
        return (0 until arr.length()).mapNotNull { i ->
            val obj = arr.optJSONObject(i) ?: return@mapNotNull null
            KodiLibraryItem(
                id = obj.optInt("id", -1),
                label = obj.optString("label").ifBlank { obj.optString("title").ifBlank { obj.optString("file") } },
                subtitle = listOf(arrayText(obj.optJSONArray("artist")), obj.optString("album")).filter { it.isNotBlank() }.joinToString(" · "),
                thumbnail = imageUrl(config, obj.optString("thumbnail")),
                file = obj.optString("file"),
                directory = obj.optString("filetype").equals("directory", true),
                mediaType = obj.optString("type").ifBlank { media },
                duration = obj.optInt("duration", 0)
            )
        }
    }

    suspend fun openUrl(config: KodiConfig, url: String) {
        require(url.isNotBlank()) { "Kodi URL is blank" }
        rpc(config, "Player.Open", JSONObject().put("item", JSONObject().put("file", url)))
    }

    suspend fun open(config: KodiConfig, item: KodiLibraryItem) {
        val target = JSONObject()
        when (item.mediaType.lowercase()) {
            "movie" -> target.put("movieid", item.id)
            "episode" -> target.put("episodeid", item.id)
            "musicvideo" -> target.put("musicvideoid", item.id)
            "song" -> target.put("songid", item.id)
            else -> if (item.file.isNotBlank()) target.put("file", item.file) else return
        }
        rpc(config, "Player.Open", JSONObject().put("item", target))
    }

    fun imageUrl(config: KodiConfig, raw: String): String {
        if (raw.isBlank()) return ""
        if (raw.startsWith("http://") || raw.startsWith("https://")) return raw
        val base = config.baseUrl.trimEnd('/')
        if (raw.startsWith("image://")) return "$base/image/${URLEncoder.encode(raw, "UTF-8")}"
        return raw
    }

    private suspend fun <T> paged(
        config: KodiConfig,
        method: String,
        resultKey: String,
        properties: JSONArray,
        baseParams: JSONObject = JSONObject(),
        sortMethod: String = "label",
        parser: (JSONObject) -> T
    ): List<T> {
        val out = mutableListOf<T>()
        var start = 0
        val pageSize = 250
        while (true) {
            val params = JSONObject(baseParams.toString())
                .put("properties", properties)
                .put("limits", JSONObject().put("start", start).put("end", start + pageSize))
                .put("sort", JSONObject().put("method", sortMethod).put("order", "ascending").put("ignorearticle", true))
            val result = rpc(config, method, params) as? JSONObject ?: break
            val arr = result.optJSONArray(resultKey) ?: JSONArray()
            for (i in 0 until arr.length()) arr.optJSONObject(i)?.let { out += parser(it) }
            val total = result.optJSONObject("limits")?.optInt("total", start + arr.length()) ?: (start + arr.length())
            start += arr.length()
            if (arr.length() == 0 || start >= total) break
        }
        return out
    }

    private fun parseVideo(obj: JSONObject, mediaType: String, idKey: String): KodiLibraryItem {
        val show = obj.optString("showtitle")
        val se = if (mediaType == "episode") "S${obj.optInt("season")}E${obj.optInt("episode")}" else ""
        return KodiLibraryItem(
            id = obj.optInt(idKey, -1),
            label = obj.optString("label").ifBlank { obj.optString("title") },
            subtitle = listOf(show, se, obj.optInt("year", 0).takeIf { it > 0 }?.toString().orEmpty()).filter { it.isNotBlank() }.joinToString(" · "),
            thumbnail = obj.optString("thumbnail"),
            file = obj.optString("file"),
            mediaType = mediaType,
            season = obj.optInt("season", -1),
            episode = obj.optInt("episode", -1),
            tvShowId = obj.optInt("tvshowid", -1),
            year = obj.optInt("year", 0),
            duration = obj.optInt("runtime", 0)
        )
    }

    private fun arrayText(arr: JSONArray?): String {
        if (arr == null) return ""
        return (0 until arr.length()).mapNotNull { arr.optString(it).takeIf(String::isNotBlank) }.joinToString(", ")
    }

    private suspend fun rpc(config: KodiConfig, method: String, params: JSONObject? = null): Any? = withContext(Dispatchers.IO) {
        require(config.baseUrl.isNotBlank()) { "Kodi is not configured for this room" }
        val payload = JSONObject().put("jsonrpc", "2.0").put("id", ids.incrementAndGet()).put("method", method)
        if (params != null) payload.put("params", params)
        val base = config.baseUrl.trimEnd('/')
        val requestBuilder = Request.Builder()
            .url("$base/jsonrpc")
            .post(payload.toString().toRequestBody("application/json; charset=utf-8".toMediaType()))
        if (config.username.isNotBlank()) requestBuilder.header("Authorization", Credentials.basic(config.username, config.password))
        client.newCall(requestBuilder.build()).execute().use { response ->
            if (!response.isSuccessful) error("Kodi HTTP ${response.code}")
            val obj = JSONObject(response.body?.string().orEmpty())
            if (obj.has("error")) {
                val err = obj.optJSONObject("error")
                error(err?.optString("message")?.ifBlank { err.toString() } ?: "Kodi error")
            }
            obj.opt("result")
        }
    }
}
