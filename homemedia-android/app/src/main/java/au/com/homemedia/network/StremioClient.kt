package au.com.homemedia.network

import android.content.Context
import android.content.Intent
import android.net.Uri

class StremioClient(private val context: Context) {
    fun openBoard() = open("stremio:///board")
    fun openLibrary() = open("stremio:///library")
    fun search(query: String) = open("stremio:///search?search=${Uri.encode(query)}")

    fun openDetail(type: String, id: String, videoId: String = "") {
        val suffix = if (videoId.isBlank()) "" else "/${Uri.encode(videoId)}"
        open("stremio:///detail/${Uri.encode(type)}/${Uri.encode(id)}$suffix")
    }

    fun openAddonManifest(manifestUrl: String) {
        if (manifestUrl.isBlank()) return
        val deep = manifestUrl
            .replaceFirst("https://", "stremio://")
            .replaceFirst("http://", "stremio://")
        open(deep)
    }

    private fun open(uri: String) {
        val intent = Intent(Intent.ACTION_VIEW, Uri.parse(uri)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        context.startActivity(intent)
    }
}
