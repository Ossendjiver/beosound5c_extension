package au.com.homemedia.network

import au.com.homemedia.model.YouTubeItem
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONObject
import java.net.URLEncoder
import java.util.concurrent.TimeUnit

class YouTubeClient {
    private val client = OkHttpClient.Builder()
        .connectTimeout(8, TimeUnit.SECONDS)
        .readTimeout(20, TimeUnit.SECONDS)
        .build()

    suspend fun search(apiKey: String, query: String, limit: Int = 25): List<YouTubeItem> = withContext(Dispatchers.IO) {
        require(apiKey.isNotBlank()) { "Add a YouTube Data API key in Settings" }
        require(query.isNotBlank()) { "Enter a YouTube search" }
        val url = "https://www.googleapis.com/youtube/v3/search?part=snippet&type=video&maxResults=${limit.coerceIn(1,50)}&q=${URLEncoder.encode(query,"UTF-8")}&key=${URLEncoder.encode(apiKey,"UTF-8")}"
        val req = Request.Builder().url(url).get().build()
        client.newCall(req).execute().use { res ->
            if (!res.isSuccessful) error("YouTube HTTP ${res.code}")
            val root = JSONObject(res.body?.string().orEmpty())
            val arr = root.optJSONArray("items") ?: return@withContext emptyList()
            (0 until arr.length()).mapNotNull { i ->
                val obj = arr.optJSONObject(i) ?: return@mapNotNull null
                val id = obj.optJSONObject("id")?.optString("videoId").orEmpty()
                val sn = obj.optJSONObject("snippet") ?: return@mapNotNull null
                if (id.isBlank()) return@mapNotNull null
                YouTubeItem(
                    videoId = id,
                    title = sn.optString("title"),
                    channel = sn.optString("channelTitle"),
                    thumbnail = sn.optJSONObject("thumbnails")?.optJSONObject("medium")?.optString("url").orEmpty()
                )
            }
        }
    }
}
