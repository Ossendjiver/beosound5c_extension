package au.com.homemedia.location

import android.annotation.SuppressLint
import android.bluetooth.BluetoothManager
import android.bluetooth.le.ScanCallback
import android.bluetooth.le.ScanResult
import android.content.Context
import au.com.homemedia.model.RoomConfig
import kotlinx.coroutines.delay

class BluetoothLocator(context: Context) {
    private val manager = context.applicationContext.getSystemService(BluetoothManager::class.java)

    @SuppressLint("MissingPermission")
    suspend fun resolveRoom(rooms: List<RoomConfig>, scanMs: Long = 1600): String? {
        val scanner = manager?.adapter?.bluetoothLeScanner ?: return null
        val best = mutableMapOf<String, Int>()
        val cb = object : ScanCallback() {
            override fun onScanResult(callbackType: Int, result: ScanResult) {
                val address = result.device?.address.orEmpty()
                val name = runCatching { result.device?.name.orEmpty() }.getOrDefault("")
                rooms.forEach { room ->
                    room.bluetoothAnchors.forEach { a ->
                        val addressMatch = a.address.isNotBlank() && a.address.equals(address, true)
                        val nameMatch = a.nameContains.isNotBlank() && name.contains(a.nameContains, true)
                        if ((addressMatch || nameMatch) && result.rssi >= a.minRssi) {
                            best[room.id] = maxOf(best[room.id] ?: Int.MIN_VALUE, result.rssi)
                        }
                    }
                }
            }
        }
        return runCatching {
            scanner.startScan(cb)
            delay(scanMs)
            scanner.stopScan(cb)
            best.maxByOrNull { it.value }?.key
        }.getOrNull().also { runCatching { scanner.stopScan(cb) } }
    }
}
