package au.com.homemedia.network

import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities

class WifiStatus(context: Context) {
    private val cm = context.applicationContext.getSystemService(ConnectivityManager::class.java)

    fun isConnectedToWifi(): Boolean {
        val network = cm?.activeNetwork ?: return false
        val caps = cm.getNetworkCapabilities(network) ?: return false
        return caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) &&
            caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
    }
}
