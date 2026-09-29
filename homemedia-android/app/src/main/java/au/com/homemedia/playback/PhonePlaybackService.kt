package au.com.homemedia.playback

import android.content.Context
import android.content.Intent
import androidx.core.content.ContextCompat
import androidx.media3.common.MediaItem
import androidx.media3.common.MediaMetadata
import androidx.media3.common.Player
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.session.MediaSession
import androidx.media3.session.MediaSessionService
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow

class PhonePlaybackService : MediaSessionService() {
    private lateinit var exoPlayer: ExoPlayer
    private lateinit var mediaSession: MediaSession

    override fun onCreate() {
        super.onCreate()
        exoPlayer = ExoPlayer.Builder(this).build()
        mediaSession = MediaSession.Builder(this, exoPlayer).build()
        _player.value = exoPlayer
    }

    override fun onGetSession(controllerInfo: MediaSession.ControllerInfo): MediaSession = mediaSession

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_PLAY -> {
                val url = intent.getStringExtra(EXTRA_URL).orEmpty()
                val title = intent.getStringExtra(EXTRA_TITLE).orEmpty()
                if (url.isNotBlank()) {
                    val metadata = MediaMetadata.Builder().setTitle(title).build()
                    exoPlayer.setMediaItem(MediaItem.Builder().setUri(url).setMediaMetadata(metadata).build())
                    exoPlayer.prepare()
                    exoPlayer.playWhenReady = true
                }
            }
            ACTION_STOP -> {
                exoPlayer.stop()
                stopSelf()
            }
        }
        return START_STICKY
    }

    override fun onTaskRemoved(rootIntent: Intent?) {
        if (!exoPlayer.playWhenReady || exoPlayer.playbackState == Player.STATE_ENDED) stopSelf()
    }

    override fun onDestroy() {
        _player.value = null
        mediaSession.release()
        exoPlayer.release()
        super.onDestroy()
    }

    companion object {
        private const val ACTION_PLAY = "au.com.homemedia.PLAY_PHONE_VIDEO"
        private const val ACTION_STOP = "au.com.homemedia.STOP_PHONE_VIDEO"
        private const val EXTRA_URL = "url"
        private const val EXTRA_TITLE = "title"

        private val _player = MutableStateFlow<Player?>(null)
        val player: StateFlow<Player?> = _player

        fun play(context: Context, url: String, title: String) {
            val intent = Intent(context, PhonePlaybackService::class.java)
                .setAction(ACTION_PLAY)
                .putExtra(EXTRA_URL, url)
                .putExtra(EXTRA_TITLE, title)
            ContextCompat.startForegroundService(context, intent)
        }

        fun stop(context: Context) {
            context.startService(Intent(context, PhonePlaybackService::class.java).setAction(ACTION_STOP))
        }
    }
}
