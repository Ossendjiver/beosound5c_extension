package au.com.homemedia.storage

import android.content.Context
import au.com.homemedia.model.YouTubeItem
import au.com.homemedia.model.YouTubePlaylist
import au.com.homemedia.model.YouTubeSavedChannel
import org.json.JSONArray
import org.json.JSONObject

class YouTubeLibraryStore(context: Context) {
    private val prefs = context.getSharedPreferences("home_media_youtube", Context.MODE_PRIVATE)

    data class State(
        val channels: List<YouTubeSavedChannel> = emptyList(),
        val history: List<YouTubeItem> = emptyList(),
        val playlists: List<YouTubePlaylist> = emptyList()
    )

    fun load(): State {
        val raw = prefs.getString("library", null) ?: return State()
        return runCatching {
            val root = JSONObject(raw)
            State(
                channels = root.optJSONArray("channels").toSavedChannels(),
                history = root.optJSONArray("history").toYouTubeItems(),
                playlists = root.optJSONArray("playlists").toPlaylists()
            )
        }.getOrDefault(State())
    }

    fun save(state: State) {
        val root = JSONObject()
            .put("channels", JSONArray().apply { state.channels.forEach { put(JSONObject().put("name", it.name).put("url", it.url)) } })
            .put("history", JSONArray().apply { state.history.forEach { put(it.toJson()) } })
            .put("playlists", JSONArray().apply {
                state.playlists.forEach { p ->
                    put(JSONObject().put("id", p.id).put("name", p.name).put("videos", JSONArray().apply { p.videos.forEach { put(it.toJson()) } }))
                }
            })
        prefs.edit().putString("library", root.toString()).apply()
    }

    private fun YouTubeItem.toJson() = JSONObject()
        .put("videoId", videoId)
        .put("title", title)
        .put("channel", channel)
        .put("channelUrl", channelUrl)
        .put("thumbnail", thumbnail)

    private fun JSONArray?.toYouTubeItems(): List<YouTubeItem> {
        if (this == null) return emptyList()
        return (0 until length()).mapNotNull { i -> optJSONObject(i)?.let { o ->
            YouTubeItem(
                videoId = o.optString("videoId"),
                title = o.optString("title"),
                channel = o.optString("channel"),
                channelUrl = o.optString("channelUrl"),
                thumbnail = o.optString("thumbnail")
            )
        }}
    }

    private fun JSONArray?.toSavedChannels(): List<YouTubeSavedChannel> {
        if (this == null) return emptyList()
        return (0 until length()).mapNotNull { i -> optJSONObject(i)?.let { o ->
            val url = o.optString("url")
            if (url.isBlank()) null else YouTubeSavedChannel(o.optString("name"), url)
        }}
    }

    private fun JSONArray?.toPlaylists(): List<YouTubePlaylist> {
        if (this == null) return emptyList()
        return (0 until length()).mapNotNull { i -> optJSONObject(i)?.let { o ->
            YouTubePlaylist(
                id = o.optString("id"),
                name = o.optString("name"),
                videos = o.optJSONArray("videos").toYouTubeItems()
            )
        }}
    }
}
