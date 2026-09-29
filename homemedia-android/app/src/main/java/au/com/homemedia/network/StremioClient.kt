package au.com.homemedia.network

import android.content.Context
import android.content.Intent
import android.net.Uri
import au.com.homemedia.model.StremioMetaItem
import au.com.homemedia.model.StremioStreamItem
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONObject
import java.net.URLEncoder
import java.util.concurrent.TimeUnit

class StremioClient(private val context: Context) {
    private val http = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(20, TimeUnit.SECONDS)
        .build()

    private val cinemetaBase = "https://v3-cinemeta.strem.io"

    suspend fun search(query: String, limit: Int = 40): List<StremioMetaItem> = coroutineScope {
        require(query.isNotBlank()) { "Enter a Stremio search" }
        listOf("movie", "series").map { type ->
            async(Dispatchers.IO) { searchType(type, query, limit / 2) }
        }.awaitAll().flatten().distinctBy { "${it.type}:${it.id}" }.take(limit)
    }

    private fun searchType(type: String, query: String, limit: Int): List<StremioMetaItem> {
        val extra = "search=" + URLEncoder.encode(query, "UTF-8").replace("+", "%20")
        val url = "$cinemetaBase/catalog/$type/top/$extra.json"
        val json = getJson(url)
        val metas = json.optJSONArray("metas") ?: return emptyList()
        return (0 until metas.length()).mapNotNull { i ->
            metas.optJSONObject(i)?.let { obj ->
                StremioMetaItem(
                    id = obj.optString("id"),
                    type = obj.optString("type", type),
                    name = obj.optString("name"),
                    poster = obj.optString("poster"),
                    description = obj.optString("description"),
                    releaseInfo = obj.optString("releaseInfo")
                ).takeIf { it.id.isNotBlank() && it.name.isNotBlank() }
            }
        }.take(limit)
    }

    suspend fun streams(
        item: StremioMetaItem,
        addonManifestUrls: List<String>
    ): List<StremioStreamItem> = coroutineScope {
        addonManifestUrls.distinct().filter { it.isNotBlank() }.map { manifest ->
            async(Dispatchers.IO) { runCatching { streamsFromAddon(item, manifest) }.getOrDefault(emptyList()) }
        }.awaitAll().flatten()
    }

    private fun streamsFromAddon(item: StremioMetaItem, manifestUrl: String): List<StremioStreamItem> {
        val base = manifestUrl.substringBeforeLast("/manifest.json", manifestUrl.trimEnd('/'))
        val url = "$base/stream/${Uri.encode(item.type)}/${Uri.encode(item.id)}.json"
        val json = getJson(url)
        val streams = json.optJSONArray("streams") ?: return emptyList()
        return (0 until streams.length()).mapNotNull { i ->
            streams.optJSONObject(i)?.let { obj ->
                StremioStreamItem(
                    name = obj.optString("name"),
                    title = obj.optString("title"),
                    url = obj.optString("url"),
                    externalUrl = obj.optString("externalUrl"),
                    infoHash = obj.optString("infoHash")
                ).takeIf { it.url.isNotBlank() || it.externalUrl.isNotBlank() || it.infoHash.isNotBlank() }
            }
        }
    }

    fun openDetail(item: StremioMetaItem) {
        open("stremio:///detail/${Uri.encode(item.type)}/${Uri.encode(item.id)}")
    }

    fun openExternal(url: String) {
        if (url.isBlank()) return
        context.startActivity(
            Intent(Intent.ACTION_VIEW, Uri.parse(url)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        )
    }

    fun openBoard() = open("stremio:///board")
    fun openLibrary() = open("stremio:///library")
    fun openSearch(query: String) = open("stremio:///search?search=${Uri.encode(query)}")

    private fun open(uri: String) {
        context.startActivity(
            Intent(Intent.ACTION_VIEW, Uri.parse(uri)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        )
    }

    private fun getJson(url: String): JSONObject {
        val request = Request.Builder().url(url).header("User-Agent", "HomeMedia/0.5.1").build()
        http.newCall(request).execute().use { response ->
            if (!response.isSuccessful) error("Stremio HTTP ${response.code}")
            return JSONObject(response.body?.string().orEmpty())
        }
    }
}
