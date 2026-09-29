package au.com.homemedia.network

import au.com.homemedia.model.YouTubeItem
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaTypeOrNull
import okhttp3.OkHttpClient
import okhttp3.Request as OkRequest
import okhttp3.RequestBody.Companion.toRequestBody
import org.schabi.newpipe.extractor.NewPipe
import org.schabi.newpipe.extractor.downloader.Downloader
import org.schabi.newpipe.extractor.downloader.Request
import org.schabi.newpipe.extractor.downloader.Response
import org.schabi.newpipe.extractor.search.SearchInfo
import org.schabi.newpipe.extractor.stream.StreamInfo
import org.schabi.newpipe.extractor.stream.StreamInfoItem
import java.net.URI
import java.util.concurrent.TimeUnit

class YouTubeClient {
    private val http = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(25, TimeUnit.SECONDS)
        .followRedirects(true)
        .followSslRedirects(true)
        .build()

    @Volatile private var initialized = false

    private fun ensureNewPipe() {
        if (initialized) return
        synchronized(this) {
            if (initialized) return
            NewPipe.init(object : Downloader() {
                override fun execute(request: Request): Response {
                    val builder = OkRequest.Builder().url(request.url())
                    request.headers().forEach { (name, values) ->
                        values.forEach { value -> builder.addHeader(name, value) }
                    }
                    val method = request.httpMethod().uppercase()
                    val bodyBytes = request.dataToSend()
                    val body = bodyBytes?.toRequestBody(
                        request.headers()["Content-Type"]?.firstOrNull()?.toMediaTypeOrNull()
                    )
                    when (method) {
                        "GET" -> builder.get()
                        "HEAD" -> builder.head()
                        "POST" -> builder.post(body ?: ByteArray(0).toRequestBody(null))
                        else -> builder.method(method, body)
                    }
                    http.newCall(builder.build()).execute().use { res ->
                        val headers = res.headers.names().associateWith { name -> res.headers.values(name) }
                        return Response(
                            res.code,
                            res.message,
                            headers,
                            res.body?.string().orEmpty(),
                            res.request.url.toString()
                        )
                    }
                }
            })
            initialized = true
        }
    }

    suspend fun search(query: String, limit: Int = 25): List<YouTubeItem> = withContext(Dispatchers.IO) {
        require(query.isNotBlank()) { "Enter a YouTube search" }
        ensureNewPipe()
        val service = NewPipe.getService("YouTube")
        val info = SearchInfo.getInfo(service.getSearchExtractor(query))
        info.relatedItems
            .asSequence()
            .filterIsInstance<StreamInfoItem>()
            .take(limit.coerceIn(1, 50))
            .mapNotNull { item ->
                val id = videoIdFromUrl(item.url)
                if (id.isBlank()) null else YouTubeItem(
                    videoId = id,
                    title = item.name,
                    channel = item.uploaderName,
                    thumbnail = item.thumbnails.firstOrNull()?.url.orEmpty()
                )
            }
            .toList()
    }

    suspend fun directPlaybackUrl(videoId: String): String = withContext(Dispatchers.IO) {
        require(videoId.isNotBlank()) { "YouTube video ID is blank" }
        ensureNewPipe()
        val info = StreamInfo.getInfo("https://www.youtube.com/watch?v=$videoId")
        val progressive = info.videoStreams
            .asSequence()
            .filter { it.isUrl && !it.isVideoOnly }
            .sortedWith(
                compareByDescending<org.schabi.newpipe.extractor.stream.VideoStream> {
                    val h = it.height
                    if (h in 1..1080) h + 10_000 else h
                }.thenByDescending { it.bitrate }
            )
            .firstOrNull()
        progressive?.content
            ?: info.hlsUrl.takeIf { it.isNotBlank() }
            ?: error("No castable YouTube stream was found")
    }

    private fun videoIdFromUrl(url: String): String {
        return runCatching {
            val uri = URI(url)
            when {
                uri.host?.contains("youtu.be", true) == true -> uri.path.trim('/').substringBefore('/')
                else -> uri.rawQuery.orEmpty().split('&')
                    .firstOrNull { it.startsWith("v=") }
                    ?.substringAfter("v=")
                    .orEmpty()
            }
        }.getOrDefault("")
    }
}
