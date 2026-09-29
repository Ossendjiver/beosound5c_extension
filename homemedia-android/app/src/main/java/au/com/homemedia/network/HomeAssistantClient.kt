package au.com.homemedia.network

import au.com.homemedia.model.HaEntityState
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import okhttp3.*
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger

class HomeAssistantClient {
    private val http = OkHttpClient.Builder()
        .connectTimeout(8, TimeUnit.SECONDS)
        .readTimeout(20, TimeUnit.SECONDS)
        .build()
    private var socket: WebSocket? = null
    private var baseUrl: String = ""
    private var token: String = ""
    private val ids = AtomicInteger(10)
    private var debugSink: ((String) -> Unit)? = null

    private val _states = MutableStateFlow<Map<String, HaEntityState>>(emptyMap())
    val states: StateFlow<Map<String, HaEntityState>> = _states

    private val _connected = MutableStateFlow(false)
    val connected: StateFlow<Boolean> = _connected

    fun setDebugLogger(logger: ((String) -> Unit)?) {
        debugSink = logger
    }

    fun connect(url: String, accessToken: String) {
        disconnect()
        if (url.isBlank() || accessToken.isBlank()) return
        baseUrl = url.trimEnd('/')
        token = accessToken.trim()
        val wsUrl = when {
            baseUrl.startsWith("https://") -> "wss://${baseUrl.removePrefix("https://")}/api/websocket"
            baseUrl.startsWith("http://") -> "ws://${baseUrl.removePrefix("http://")}/api/websocket"
            else -> "ws://$baseUrl/api/websocket"
        }
        debugSink?.invoke("Connecting websocket $wsUrl token=${if (token.isBlank()) "missing" else "present"}")
        val request = Request.Builder().url(wsUrl).build()
        socket = http.newWebSocket(request, object : WebSocketListener() {
            override fun onMessage(webSocket: WebSocket, text: String) {
                handleMessage(webSocket, text)
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                debugSink?.invoke("WebSocket closed code=$code reason=$reason")
                _connected.value = false
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                debugSink?.invoke("WebSocket failure ${t.javaClass.simpleName}: ${t.message}; http=${response?.code}")
                _connected.value = false
            }
        })
    }

    fun disconnect() {
        socket?.close(1000, "reconnect")
        socket = null
        _connected.value = false
    }

    fun imageUrl(relativeOrAbsolute: String): String {
        if (relativeOrAbsolute.isBlank()) return ""
        return if (relativeOrAbsolute.startsWith("http")) relativeOrAbsolute
        else "${baseUrl.trimEnd('/')}/${relativeOrAbsolute.trimStart('/')}"
    }

    fun authToken(): String = token

    fun callService(
        domain: String,
        service: String,
        entityId: String = "",
        serviceData: JSONObject = JSONObject()
    ): Boolean {
        val ws = socket ?: return false
        if (!_connected.value || domain.isBlank() || service.isBlank()) return false
        val obj = JSONObject().apply {
            put("id", ids.incrementAndGet())
            put("type", "call_service")
            put("domain", domain)
            put("service", service)
            if (entityId.isNotBlank()) put("target", JSONObject().put("entity_id", entityId))
            put("service_data", serviceData)
        }
        return ws.send(obj.toString())
    }

    fun turnOn(entityId: String) = callService("media_player", "turn_on", entityId)
    fun turnOff(entityId: String) = callService("media_player", "turn_off", entityId)
    fun pause(entityId: String) = callService("media_player", "media_pause", entityId)
    fun play(entityId: String) = callService("media_player", "media_play", entityId)
    fun selectSource(entityId: String, source: String) = callService(
        "media_player", "select_source", entityId, JSONObject().put("source", source)
    )
    fun playPause(entityId: String) = callService("media_player", "media_play_pause", entityId)
    fun next(entityId: String) = callService("media_player", "media_next_track", entityId)
    fun previous(entityId: String) = callService("media_player", "media_previous_track", entityId)
    fun volumeUp(entityId: String) = callService("media_player", "volume_up", entityId)
    fun volumeDown(entityId: String) = callService("media_player", "volume_down", entityId)

    private fun handleMessage(ws: WebSocket, text: String) {
        val obj = runCatching { JSONObject(text) }.getOrNull() ?: return
        when (obj.optString("type")) {
            "auth_required" -> ws.send(JSONObject().put("type", "auth").put("access_token", token).toString())
            "auth_ok" -> {
                debugSink?.invoke("Authenticated with Home Assistant")
                _connected.value = true
                ws.send(JSONObject().put("id", 1).put("type", "get_states").toString())
                ws.send(
                    JSONObject().put("id", 2).put("type", "subscribe_events")
                        .put("event_type", "state_changed").toString()
                )
            }
            "auth_invalid" -> {
                debugSink?.invoke("Home Assistant authentication failed")
                _connected.value = false
            }
            "result" -> if (obj.optInt("id") == 1 && obj.optBoolean("success")) {
                val result = obj.optJSONArray("result") ?: JSONArray()
                val map = mutableMapOf<String, HaEntityState>()
                for (i in 0 until result.length()) {
                    result.optJSONObject(i)?.let(::parseState)?.let { map[it.entityId] = it }
                }
                _states.value = map
                debugSink?.invoke("Loaded ${map.size} Home Assistant states")
            }
            "event" -> {
                val event = obj.optJSONObject("event") ?: return
                if (event.optString("event_type") != "state_changed") return
                val newState = event.optJSONObject("data")?.optJSONObject("new_state") ?: return
                val parsed = parseState(newState) ?: return
                _states.value = _states.value.toMutableMap().apply { put(parsed.entityId, parsed) }
            }
        }
    }

    private fun parseState(obj: JSONObject): HaEntityState? {
        val id = obj.optString("entity_id")
        if (id.isBlank()) return null
        return HaEntityState(id, obj.optString("state"), obj.optJSONObject("attributes") ?: JSONObject())
    }
}
