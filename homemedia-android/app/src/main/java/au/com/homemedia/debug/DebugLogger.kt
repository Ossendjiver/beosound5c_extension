package au.com.homemedia.debug

import android.content.Context
import android.net.Uri
import java.io.File
import java.time.Instant

class DebugLogger(private val context: Context) {
    private val file = File(context.filesDir, "homemedia-debug.log")
    @Volatile private var enabled = false

    fun setEnabled(value: Boolean) {
        enabled = value
        if (value) log("DEBUG", "Debug logging enabled")
    }

    fun isEnabled(): Boolean = enabled

    @Synchronized
    fun log(tag: String, message: String) {
        if (!enabled) return
        runCatching {
            rotateIfNeeded()
            file.appendText("${Instant.now()} [$tag] ${message.replace("\n", " ")}\n")
        }
    }

    @Synchronized
    fun clear() {
        runCatching { file.writeText("") }
        if (enabled) log("DEBUG", "Debug log cleared")
    }

    @Synchronized
    fun export(uri: Uri, header: String = "") {
        context.contentResolver.openOutputStream(uri, "w")?.bufferedWriter()?.use { out ->
            if (header.isNotBlank()) {
                out.write(header)
                if (!header.endsWith("\n")) out.newLine()
                out.newLine()
            }
            if (file.exists()) out.write(file.readText())
        } ?: error("Could not open debug log destination")
    }

    private fun rotateIfNeeded() {
        if (!file.exists() || file.length() < 1_500_000) return
        val text = file.readText()
        file.writeText(text.takeLast(750_000))
    }
}
