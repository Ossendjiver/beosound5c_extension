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
    private var debugLog: ((String, String) -> Unit)? = null
    private var reconnectJob: kotlinx.coroutines.Job? = null
    private val reconnectScope = kotlinx.coroutines.CoroutineScope(kotlinx.coroutines.SupervisorJob() + kotlinx.coroutines.Dispatchers.IO)
    @Volatile private var manualDisconnect = false

    fun setDebugLogger(logger: ((String, String) -> Unit)?) {
        debugLog = logger
    }

    private val _states = MutableStateFlow<Map<String, HaEntityState>>(emptyMap())
    val states: StateFlow<Map<String, HaEntityState>> = _states

    private val _connected = MutableStateFlow(false)
    val connected: StateFlow<Boolean> = _connected

    fun connect(url: String, accessToken: String) {
        manualDisconnect = false
        reconnectJob?.cancel()
        reconnectJob = null
        disconnectInternal("reconnect")
        if (url.isBlank() || accessToken.isBlank()) return
        baseUrl = url.trimEnd('/')
        token = accessToken.trim()
        val wsUrl = when {
            baseUrl.startsWith("https://") -> "wss://${baseUrl.removePrefix("https://")}/api/websocket"
            baseUrl.startsWith("http://") -> "ws://${baseUrl.removePrefix("http://")}/api/websocket"
            else -> "ws://$baseUrl/api/websocket"
        }
        debugLog?.invoke("HA", "Connecting websocket $wsUrl token=${if (token.isBlank()) "missing" else "present"}")
        debugLog?.invoke("HA", "connect websocket configured")
        val request = Request.Builder().url(wsUrl).build()
        socket = http.newWebSocket(request, object : WebSocketListener() {
            override fun onMessage(webSocket: WebSocket, text: String) {
                handleMessage(webSocket, text)
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                debugLog?.invoke("HA", "WebSocket closed code=$code reason=$reason")
                _connected.value = false
                if (!manualDisconnect) scheduleReconnect()
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                debugLog?.invoke("HA", "WebSocket failure ${t.javaClass.simpleName}: ${t.message}; http=${response?.code}")
                _connected.value = false
                if (!manualDisconnect) scheduleReconnect()
            }
        })
    }

    fun disconnect() {
        manualDisconnect = true
        reconnectJob?.cancel()
        reconnectJob = null
        disconnectInternal("disconnect")
    }

    private fun disconnectInternal(reason: String) {
        socket?.close(1000, reason)
        socket = null
        _connected.value = false
    }

    private fun scheduleReconnect() {
        if (baseUrl.isBlank() || token.isBlank()) return
        if (reconnectJob?.isActive == true) return
        reconnectJob = reconnectScope.launch {
            var delayMs = 1500L
            while (!manualDisconnect && !_connected.value) {
                kotlinx.coroutines.delay(delayMs)
                if (manualDisconnect || _connected.value) break
                debugLog?.invoke("HA", "Reconnect attempt after ${delayMs}ms")
                val url = baseUrl
                val wsUrl = when {
                    url.startsWith("https://") -> "wss://${url.removePrefix("https://")}/api/websocket"
                    url.startsWith("http://") -> "ws://${url.removePrefix("http://")}/api/websocket"
                    else -> "ws://$url/api/websocket"
                }
                debugLog?.invoke("HA", "Connecting websocket $wsUrl token=present")
                val request = Request.Builder().url(wsUrl).build()
                socket = http.newWebSocket(request, object : WebSocketListener() {
                    override fun onMessage(webSocket: WebSocket, text: String) {
                        handleMessage(webSocket, text)
                    }
                    override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                        debugLog?.invoke("HA", "WebSocket closed code=$code reason=$reason")
                        _connected.value = false
                    }
                    override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                        debugLog?.invoke("HA", "Reconnect failed ${t.javaClass.simpleName}: ${t.message}; http=${response?.code}")
                        _connected.value = false
                    }
                })
                kotlinx.coroutines.delay(3000)
                if (_connected.value) {
                    debugLog?.invoke("HA", "Reconnect succeeded")
                    break
                }
                delayMs = (delayMs * 2).coerceAtMost(30000L)
            }
        }
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
    fun stop(entityId: String) = callService("media_player", "media_stop", entityId)
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
                debugLog?.invoke("HA", "Authenticated with Home Assistant")
                _connected.value = true
                ws.send(JSONObject().put("id", 1).put("type", "get_states").toString())
                ws.send(
                    JSONObject().put("id", 2).put("type", "subscribe_events")
                        .put("event_type", "state_changed").toString()
                )
            }
            "auth_invalid" -> {
                debugLog?.invoke("HA", "Home Assistant authentication failed")
                _connected.value = false
            }
            "result" -> if (obj.optInt("id") == 1 && obj.optBoolean("success")) {
                val result = obj.optJSONArray("result") ?: JSONArray()
                val map = mutableMapOf<String, HaEntityState>()
                for (i in 0 until result.length()) {
                    result.optJSONObject(i)?.let(::parseState)?.let { map[it.entityId] = it }
                }
                _states.value = map
                debugLog?.invoke("HA", "initial states=" + map.size)
                debugLog?.invoke("HA", "Loaded ${map.size} Home Assistant states")
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
