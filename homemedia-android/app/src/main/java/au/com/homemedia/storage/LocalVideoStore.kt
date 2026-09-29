package au.com.homemedia.storage

import android.content.Context
import android.net.Uri
import org.json.JSONArray
import org.json.JSONObject

data class LocalVideoItem(
    val uri: String,
    val name: String
)

class LocalVideoStore(context: Context) {
    private val prefs = context.getSharedPreferences("home_media_local_videos", Context.MODE_PRIVATE)

    fun load(): List<LocalVideoItem> {
        val raw = prefs.getString("items", null) ?: return emptyList()
        return runCatching {
            val arr = JSONArray(raw)
            (0 until arr.length()).mapNotNull { i ->
                arr.optJSONObject(i)?.let { LocalVideoItem(it.optString("uri"), it.optString("name")) }
            }.filter { it.uri.isNotBlank() }
        }.getOrDefault(emptyList())
    }

    fun add(item: LocalVideoItem) {
        val items = (load().filterNot { it.uri == item.uri } + item).takeLast(100)
        save(items)
    }

    fun remove(uri: String) = save(load().filterNot { it.uri == uri })

    private fun save(items: List<LocalVideoItem>) {
        val arr = JSONArray()
        items.forEach { arr.put(JSONObject().put("uri", it.uri).put("name", it.name)) }
        prefs.edit().putString("items", arr.toString()).apply()
    }
}
