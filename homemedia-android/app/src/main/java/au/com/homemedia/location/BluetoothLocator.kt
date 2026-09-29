package au.com.homemedia.location

import android.annotation.SuppressLint
import android.bluetooth.BluetoothManager
import android.bluetooth.le.ScanCallback
import android.bluetooth.le.ScanResult
import android.content.Context
import au.com.homemedia.model.BluetoothCalibrationPoint
import au.com.homemedia.model.BluetoothFingerprintSample
import au.com.homemedia.model.RoomConfig
import kotlinx.coroutines.delay
import kotlin.math.abs

data class BluetoothScanSummary(
    val samples: List<BluetoothFingerprintSample>,
    val durationMs: Long
)

data class BluetoothRoomMatch(
    val roomId: String,
    val score: Double,
    val runnerUpScore: Double?,
    val confidenceMargin: Double
)

class BluetoothLocator(context: Context) {
    private val manager = context.applicationContext.getSystemService(BluetoothManager::class.java)

    @SuppressLint("MissingPermission")
    suspend fun scanFingerprint(scanMs: Long = 5000): BluetoothScanSummary {
        val scanner = manager?.adapter?.bluetoothLeScanner ?: return BluetoothScanSummary(emptyList(), scanMs)
        val readings = linkedMapOf<String, MutableList<Int>>()
        val names = mutableMapOf<String, String>()

        val cb = object : ScanCallback() {
            override fun onScanResult(callbackType: Int, result: ScanResult) {
                val address = result.device?.address.orEmpty()
                if (address.isBlank()) return
                val name = runCatching { result.device?.name.orEmpty() }.getOrDefault("")
                readings.getOrPut(address) { mutableListOf() }.add(result.rssi)
                if (name.isNotBlank()) names[address] = name
            }

            override fun onBatchScanResults(results: MutableList<ScanResult>) {
                results.forEach { onScanResult(0, it) }
            }
        }

        return runCatching {
            scanner.startScan(cb)
            delay(scanMs)
            scanner.stopScan(cb)
            val samples = readings.mapNotNull { (address, values) ->
                if (values.isEmpty()) null else {
                    val sorted = values.sorted()
                    val median = sorted[sorted.size / 2]
                    BluetoothFingerprintSample(address, names[address].orEmpty(), median)
                }
            }.sortedByDescending { it.rssi }
            BluetoothScanSummary(samples, scanMs)
        }.getOrElse {
            runCatching { scanner.stopScan(cb) }
            BluetoothScanSummary(emptyList(), scanMs)
        }
    }

    @SuppressLint("MissingPermission")
    suspend fun resolveRoom(
        rooms: List<RoomConfig>,
        scanMs: Long = 1800,
        minConfidenceMargin: Double = 9.0,
        maxAcceptableScore: Double = 34.0
    ): BluetoothRoomMatch? {
        val live = scanFingerprint(scanMs).samples
        val quietRooms = rooms.filter { it.bluetoothQuietRoom && it.bluetoothCalibrationPoints.size >= 3 }
        val hasStrongLocalSignal = live.any { it.rssi >= -62 }

        // A uniquely calibrated quiet room (e.g. Bathroom) may be identified by the
        // absence of a strong local BLE device. This prevents weak Bedroom bleed-through
        // from becoming the default simply because Bedroom has visible devices.
        if (!hasStrongLocalSignal && quietRooms.size == 1) {
            return BluetoothRoomMatch(
                roomId = quietRooms.first().id,
                score = 0.0,
                runnerUpScore = null,
                confidenceMargin = Double.POSITIVE_INFINITY
            )
        }
        if (live.isEmpty()) return null

        val calibrated = rooms.filter { it.bluetoothCalibrationPoints.size >= 3 }
        if (calibrated.isEmpty()) {
            return resolveLegacyAnchorRoom(rooms, live)
        }

        val scored = calibrated.mapNotNull { room ->
            val score = roomScore(room, live) ?: return@mapNotNull null
            room.id to score
        }.sortedBy { it.second }

        val best = scored.firstOrNull()
        val runnerUp = scored.getOrNull(1)

        if (best != null) {
            val margin = if (runnerUp == null) Double.POSITIVE_INFINITY else runnerUp.second - best.second
            if (best.second <= maxAcceptableScore && (runnerUp == null || margin >= minConfidenceMargin)) {
                return BluetoothRoomMatch(best.first, best.second, runnerUp?.second, margin)
            }
        }

        // Negative/quiet-room fingerprint: useful for a room such as Bathroom with no local BLE.
        // Only use it when exactly one calibrated room is explicitly quiet, no strong device is
        // visible, and no normal room is even a moderately plausible match.
        val quietRooms = calibrated.filter { it.bluetoothQuietRoom }
        val strongestLive = live.maxOfOrNull { it.rssi } ?: -127
        val nearestNormal = scored
            .filterNot { pair -> quietRooms.any { it.id == pair.first } }
            .minOfOrNull { it.second } ?: Double.POSITIVE_INFINITY
        if (quietRooms.size == 1 && strongestLive < -72 && nearestNormal > 24.0) {
            return BluetoothRoomMatch(
                roomId = quietRooms.first().id,
                score = 30.0,
                runnerUpScore = nearestNormal.takeIf { it.isFinite() },
                confidenceMargin = if (nearestNormal.isFinite()) nearestNormal - 30.0 else Double.POSITIVE_INFINITY
            )
        }

        return null
    }

    private fun roomScore(room: RoomConfig, live: List<BluetoothFingerprintSample>): Double? {
        val points = room.bluetoothCalibrationPoints.take(3)
        if (points.size < 3) return null

        val liveByAddress = live.associateBy { it.address.lowercase() }
        val pointScores = points.mapNotNull { point -> fingerprintDistance(point, liveByAddress) }
        if (pointScores.size < 2) return null

        // A room match is the nearest of the three calibration points.
        // Requiring overlap and keeping an absolute ceiling prevents distant bleed-through.
        return pointScores.minOrNull()
    }

    private fun fingerprintDistance(
        point: BluetoothCalibrationPoint,
        liveByAddress: Map<String, BluetoothFingerprintSample>
    ): Double? {
        if (point.samples.isEmpty()) return null
        var overlap = 0
        var weightedError = 0.0
        var weightTotal = 0.0

        point.samples.take(12).forEach { expected ->
            val live = liveByAddress[expected.address.lowercase()] ?: return@forEach
            overlap++
            val weight = when {
                expected.rssi >= -60 -> 1.8
                expected.rssi >= -72 -> 1.35
                else -> 1.0
            }
            weightedError += abs(live.rssi - expected.rssi) * weight
            weightTotal += weight
        }

        if (overlap < 2 || weightTotal == 0.0) return null

        val missingPenalty = (point.samples.take(8).size - overlap).coerceAtLeast(0) * 4.0
        return (weightedError / weightTotal) + missingPenalty
    }

    private fun resolveLegacyAnchorRoom(
        rooms: List<RoomConfig>,
        live: List<BluetoothFingerprintSample>
    ): BluetoothRoomMatch? {
        val bestByRoom = rooms.mapNotNull { room ->
            val score = room.bluetoothAnchors.mapNotNull { anchor ->
                val match = live.firstOrNull { sample ->
                    (anchor.address.isNotBlank() && sample.address.equals(anchor.address, true)) ||
                        (anchor.nameContains.isNotBlank() && sample.name.contains(anchor.nameContains, true))
                } ?: return@mapNotNull null
                if (match.rssi < anchor.minRssi) return@mapNotNull null
                -match.rssi.toDouble()
            }.minOrNull() ?: return@mapNotNull null
            room.id to score
        }.sortedBy { it.second }

        val best = bestByRoom.firstOrNull() ?: return null
        val second = bestByRoom.getOrNull(1)
        val margin = if (second == null) Double.POSITIVE_INFINITY else second.second - best.second

        // Legacy anchors are deliberately conservative: if two rooms are close,
        // do not move the user at all.
        if (second != null && margin < 8.0) return null
        return BluetoothRoomMatch(best.first, best.second, second?.second, margin)
    }
}
