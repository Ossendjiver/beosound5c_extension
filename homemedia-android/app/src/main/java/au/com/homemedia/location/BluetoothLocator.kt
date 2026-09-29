package au.com.homemedia.location

import android.annotation.SuppressLint
import android.bluetooth.BluetoothManager
import android.bluetooth.le.ScanCallback
import android.bluetooth.le.ScanResult
import android.content.Context
import au.com.homemedia.model.BluetoothFingerprintSample
import au.com.homemedia.model.RoomConfig
import kotlinx.coroutines.delay
import kotlin.math.abs
import kotlin.math.sqrt

data class BluetoothScanSummary(
    val samples: List<BluetoothFingerprintSample>,
    val durationMs: Long
)

data class BluetoothRoomScore(
    val roomId: String,
    val score: Double,
    val overlap: Int,
    val expectedDevices: Int,
    val stableDevices: Int
)

data class BluetoothRoomMatch(
    val roomId: String,
    val score: Double,
    val runnerUpScore: Double?,
    val confidenceMargin: Double,
    val overlap: Int = 0,
    val expectedDevices: Int = 0,
    val stableDevices: Int = 0,
    val candidates: List<BluetoothRoomScore> = emptyList()
)

private data class FingerprintDevice(
    val address: String,
    val medianRssi: Double,
    val stdDev: Double,
    val seenPoints: Int,
    val name: String
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
        minConfidenceMargin: Double = 8.0,
        maxAcceptableScore: Double = 32.0
    ): BluetoothRoomMatch? =
        resolveRoomFromSamples(
            rooms = rooms,
            live = scanFingerprint(scanMs).samples,
            minConfidenceMargin = minConfidenceMargin,
            maxAcceptableScore = maxAcceptableScore
        )

    fun resolveRoomFromSamples(
        rooms: List<RoomConfig>,
        live: List<BluetoothFingerprintSample>,
        minConfidenceMargin: Double = 8.0,
        maxAcceptableScore: Double = 32.0
    ): BluetoothRoomMatch? {
        val calibrated = rooms.filter { it.bluetoothCalibrationPoints.size >= 3 }
        val uniqueQuiet = calibrated.filter { it.bluetoothQuietRoom }.singleOrNull()

        if (live.isEmpty()) {
            return uniqueQuiet?.let {
                BluetoothRoomMatch(
                    roomId = it.id,
                    score = 30.0,
                    runnerUpScore = null,
                    confidenceMargin = Double.POSITIVE_INFINITY
                )
            }
        }

        if (calibrated.isEmpty()) return resolveLegacyAnchorRoom(rooms, live)

        val scored = scoreRooms(calibrated, live)
        val best = scored.firstOrNull()
        val runnerUp = scored.getOrNull(1)

        if (best != null) {
            val margin = if (runnerUp == null) Double.POSITIVE_INFINITY else runnerUp.score - best.score
            if (best.score <= maxAcceptableScore && (runnerUp == null || margin >= minConfidenceMargin)) {
                return BluetoothRoomMatch(
                    roomId = best.roomId,
                    score = best.score,
                    runnerUpScore = runnerUp?.score,
                    confidenceMargin = margin,
                    overlap = best.overlap,
                    expectedDevices = best.expectedDevices,
                    stableDevices = best.stableDevices,
                    candidates = scored
                )
            }
        }

        val quietRooms = calibrated.filter { it.bluetoothQuietRoom }
        val strongestLive = live.maxOfOrNull { it.rssi } ?: -127
        val nearestNormal = scored
            .filterNot { score -> quietRooms.any { it.id == score.roomId } }
            .minByOrNull { it.score }
        if (quietRooms.size == 1 && strongestLive < -72 && (nearestNormal?.score ?: Double.POSITIVE_INFINITY) > 24.0) {
            return BluetoothRoomMatch(
                roomId = quietRooms.first().id,
                score = 30.0,
                runnerUpScore = nearestNormal?.score,
                confidenceMargin = nearestNormal?.score?.minus(30.0) ?: Double.POSITIVE_INFINITY,
                candidates = scored
            )
        }

        return null
    }

    fun scoreRooms(rooms: List<RoomConfig>, live: List<BluetoothFingerprintSample>): List<BluetoothRoomScore> {
        val fingerprints = rooms.associate { it.id to aggregateFingerprint(it) }
        val allRoomDevices = fingerprints.values.flatten().groupBy { it.address }
        val liveByAddress = live.associateBy { it.address.lowercase() }

        return rooms.mapNotNull { room ->
            val fp = fingerprints[room.id].orEmpty()
            if (fp.isEmpty()) return@mapNotNull null

            val expected = fp.take(10)
            var overlap = 0
            var weightedError = 0.0
            var weightTotal = 0.0
            var stableOverlap = 0

            expected.forEach { device ->
                val actual = liveByAddress[device.address] ?: return@forEach
                overlap++

                val stable = device.seenPoints >= 2 && device.stdDev <= 8.0
                if (stable) stableOverlap++

                val roomCount = allRoomDevices[device.address].orEmpty().count()
                val distinctiveness = when (roomCount) {
                    0, 1 -> 1.55
                    2 -> 1.25
                    else -> 1.0
                }
                val strengthWeight = when {
                    device.medianRssi >= -60 -> 1.6
                    device.medianRssi >= -72 -> 1.3
                    else -> 1.0
                }
                val stabilityWeight = when {
                    device.stdDev <= 4.0 -> 1.35
                    device.stdDev <= 8.0 -> 1.15
                    device.stdDev >= 14.0 -> 0.75
                    else -> 1.0
                }
                val weight = distinctiveness * strengthWeight * stabilityWeight
                weightedError += abs(actual.rssi - device.medianRssi) * weight
                weightTotal += weight
            }

            val minOverlap = if (expected.size >= 5) 3 else 2
            if (overlap < minOverlap || weightTotal == 0.0) return@mapNotNull null

            val missing = (expected.size - overlap).coerceAtLeast(0)
            val missingPenalty = missing * 3.0
            val weakOverlapPenalty = if (stableOverlap < 2 && expected.size >= 4) 5.0 else 0.0
            val score = (weightedError / weightTotal) + missingPenalty + weakOverlapPenalty

            BluetoothRoomScore(
                roomId = room.id,
                score = score,
                overlap = overlap,
                expectedDevices = expected.size,
                stableDevices = stableOverlap
            )
        }.sortedBy { it.score }
    }

    fun calibrationQuality(room: RoomConfig): String {
        val fp = aggregateFingerprint(room)
        if (room.bluetoothCalibrationPoints.size < 3) return "not calibrated"
        if (fp.isEmpty()) return "poor · no repeatable devices"
        val stable = fp.count { it.seenPoints >= 2 && it.stdDev <= 8.0 }
        val strong = fp.count { it.medianRssi >= -70 }
        return when {
            room.bluetoothQuietRoom -> "quiet profile · ${fp.size} repeatable devices"
            stable >= 5 && strong >= 2 -> "good · $stable stable / ${fp.size} repeatable"
            stable >= 3 -> "fair · $stable stable / ${fp.size} repeatable"
            else -> "weak · $stable stable / ${fp.size} repeatable"
        }
    }

    private fun aggregateFingerprint(room: RoomConfig): List<FingerprintDevice> {
        val points = room.bluetoothCalibrationPoints.take(3)
        if (points.size < 3) return emptyList()

        val byAddress = linkedMapOf<String, MutableList<BluetoothFingerprintSample>>()
        points.forEach { point ->
            point.samples.forEach { sample ->
                byAddress.getOrPut(sample.address.lowercase()) { mutableListOf() }.add(sample)
            }
        }

        return byAddress.mapNotNull { (address, samples) ->
            if (samples.size < 2) return@mapNotNull null
            val rssis = samples.map { it.rssi.toDouble() }.sorted()
            val median = rssis[rssis.size / 2]
            val mean = rssis.average()
            val variance = rssis.map { (it - mean) * (it - mean) }.average()
            FingerprintDevice(
                address = address,
                medianRssi = median,
                stdDev = sqrt(variance),
                seenPoints = samples.size,
                name = samples.firstNotNullOfOrNull { it.name.takeIf(String::isNotBlank) }.orEmpty()
            )
        }.sortedWith(
            compareByDescending<FingerprintDevice> { it.seenPoints }
                .thenByDescending { it.medianRssi }
        )
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

        if (second != null && margin < 8.0) return null
        return BluetoothRoomMatch(best.first, best.second, second?.second, margin)
    }
}
