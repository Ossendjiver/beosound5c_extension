package au.com.homemedia.storage

import android.content.Context
import au.com.homemedia.model.KodiBrowseType
import au.com.homemedia.model.KodiLibraryItem
import au.com.homemedia.model.MassCategory
import au.com.homemedia.model.MassMediaItem
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/** Persistent metadata cache. Coil continues to cache artwork images separately. */
class MediaLibraryCache(context: Context) {
    private val dir = File(context.filesDir, "media_library_cache").apply { mkdirs() }

    fun loadMass(category: MassCategory): List<MassMediaItem> =
        readArray(File(dir, "mass_${category.name.lowercase()}.json")).mapNotNull(::massFromJson)

    fun saveMass(category: MassCategory, items: List<MassMediaItem>) =
        writeArray(File(dir, "mass_${category.name.lowercase()}.json"), items.map(::massToJson))

    fun loadKodi(type: KodiBrowseType): List<KodiLibraryItem> =
        readArray(File(dir, "kodi_${type.name.lowercase()}.json")).mapNotNull(::kodiFromJson)

    fun saveKodi(type: KodiBrowseType, items: List<KodiLibraryItem>) =
        writeArray(File(dir, "kodi_${type.name.lowercase()}.json"), items.map(::kodiToJson))

    private fun readArray(file: File): List<JSONObject> = runCatching {
        if (!file.exists()) return@runCatching emptyList()
        val arr = JSONArray(file.readText())
        (0 until arr.length()).mapNotNull { arr.optJSONObject(it) }
    }.getOrDefault(emptyList())

    private fun writeArray(file: File, items: List<JSONObject>) {
        runCatching {
            val temp = File(file.parentFile, file.name + ".tmp")
            temp.writeText(JSONArray().apply { items.forEach(::put) }.toString())
            if (!temp.renameTo(file)) {
                file.writeText(temp.readText())
                temp.delete()
            }
        }
    }

    private fun massToJson(item: MassMediaItem) = JSONObject()
        .put("mediaType", item.mediaType)
        .put("itemId", item.itemId)
        .put("provider", item.provider)
        .put("uri", item.uri)
        .put("name", item.name)
        .put("subtitle", item.subtitle)
        .put("duration", item.duration)
        .put("imageUrl", item.imageUrl)
        .put("playable", item.playable)

    private fun massFromJson(obj: JSONObject) = runCatching {
        MassMediaItem(
            mediaType = obj.optString("mediaType"),
            itemId = obj.optString("itemId"),
            provider = obj.optString("provider"),
            uri = obj.optString("uri"),
            name = obj.optString("name"),
            subtitle = obj.optString("subtitle"),
            duration = obj.optDouble("duration", 0.0),
            imageUrl = obj.optString("imageUrl"),
            playable = obj.optBoolean("playable", true)
        )
    }.getOrNull()

    private fun kodiToJson(item: KodiLibraryItem) = JSONObject()
        .put("id", item.id)
        .put("label", item.label)
        .put("subtitle", item.subtitle)
        .put("thumbnail", item.thumbnail)
        .put("file", item.file)
        .put("directory", item.directory)
        .put("mediaType", item.mediaType)
        .put("season", item.season)
        .put("episode", item.episode)
        .put("tvShowId", item.tvShowId)
        .put("year", item.year)
        .put("duration", item.duration)

    private fun kodiFromJson(obj: JSONObject) = runCatching {
        KodiLibraryItem(
            id = obj.optInt("id", -1),
            label = obj.optString("label"),
            subtitle = obj.optString("subtitle"),
            thumbnail = obj.optString("thumbnail"),
            file = obj.optString("file"),
            directory = obj.optBoolean("directory", false),
            mediaType = obj.optString("mediaType"),
            season = obj.optInt("season", -1),
            episode = obj.optInt("episode", -1),
            tvShowId = obj.optInt("tvShowId", -1),
            year = obj.optInt("year", 0),
            duration = obj.optInt("duration", 0)
        )
    }.getOrNull()
}
