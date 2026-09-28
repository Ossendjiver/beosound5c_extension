package au.com.homemedia

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import au.com.homemedia.core.AppController
import au.com.homemedia.ui.HomeMediaApp

class MainActivity : ComponentActivity() {
    private lateinit var controller: AppController

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        controller = AppController(this)
        setContent { HomeMediaApp(controller) }
    }

    override fun onStart() {
        super.onStart()
        controller.onForeground()
    }

    override fun onDestroy() {
        controller.close()
        super.onDestroy()
    }
}
