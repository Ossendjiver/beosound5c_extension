package au.com.homemedia.network

import au.com.homemedia.model.NewsArticle
import au.com.homemedia.model.NewsSection
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.TimeUnit

class NewsClient {
    private val http = OkHttpClient.Builder()
        .connectTimeout(4, TimeUnit.SECONDS)
        .readTimeout(20, TimeUnit.SECONDS)
        .build()

    private fun base(raw: String): String = raw.trim().trimEnd('/').ifBlank { "http://beosound5c.local" }

    suspend fun sections(baseUrl: String): List<NewsSection> = withContext(Dispatchers.IO) {
        val req = Request.Builder().url(base(baseUrl) + ":8776/articles").get().build()
        http.newCall(req).execute().use { response ->
            if (!response.isSuccessful) error("BS5c news returned HTTP ${response.code}")
            val arr = JSONArray(response.body?.string().orEmpty())
            (0 until arr.length()).mapNotNull { i ->
                val sec = arr.optJSONObject(i) ?: return@mapNotNull null
                val articles = sec.optJSONArray("articles") ?: JSONArray()
                NewsSection(
                    id = sec.optString("id"),
                    name = sec.optString("name", "News"),
                    articles = (0 until articles.length()).mapNotNull { j ->
                        val art = articles.optJSONObject(j) ?: return@mapNotNull null
                        val page = art.optJSONObject("page")
                        val bodyHtml = page?.optString("body").orEmpty()
                        val plain = bodyHtml
                            .replace(Regex("<[^>]+>"), " ")
                            .replace("&amp;", "&")
                            .replace("&quot;", "\"")
                            .replace(Regex("\\s+"), " ")
                            .trim()
                        NewsArticle(
                            id = art.optString("id"),
                            title = page?.optString("title").orEmpty().ifBlank { art.optString("name", "News") },
                            trail = "",
                            body = plain,
                            imageUrl = art.optString("image")
                        )
                    }
                )
            }
        }
    }

    suspend fun readOnBs5c(baseUrl: String, article: NewsArticle, room: String = "") = withContext(Dispatchers.IO) {
        val payload = JSONObject()
            .put("title", article.title)
            .put("text", listOf(article.title, article.body).filter { it.isNotBlank() }.joinToString(". "))
            .put("room", room)
        val req = Request.Builder()
            .url(base(baseUrl) + ":8776/read")
            .post(payload.toString().toRequestBody("application/json".toMediaType()))
            .build()
        http.newCall(req).execute().use { response ->
            if (!response.isSuccessful) error("BS5c read returned HTTP ${response.code}")
        }
    }
}
