package au.com.homemedia.model

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class DefaultTopologyTest {
    @Test
    fun loungeUsesBs3WithLoungeMiniSecondary() {
        val settings = AppSettings()
        val lounge = settings.rooms.first { it.id == "lounge" }
        assertEquals("media_player.bs3_2", lounge.primaryPlayerEntity)
        assertEquals("media_player.lounge_mini_ma", lounge.secondaryPlayers.single().haEntity)
        assertTrue(lounge.tiles.any { it.actionType == TileActionType.OPEN_KODI })
    }

    @Test
    fun diningUsesBv10WithDiningSecondaryAndBc9500Toggle() {
        val dining = AppSettings().rooms.first { it.id == "dining" }
        assertEquals("media_player.bv10_32_2", dining.primaryPlayerEntity)
        val secondary = dining.secondaryPlayers.single()
        assertEquals("media_player.dining", secondary.haEntity)
        assertEquals("switch.bc9500", secondary.toggleEntity)
    }

    @Test
    fun videoTilesExistForRequestedRooms() {
        val settings = AppSettings()
        listOf("lounge", "dining", "bedroom", "kitchen").forEach { id ->
            val room = settings.rooms.first { it.id == id }
            assertTrue(room.tiles.any { it.actionType == TileActionType.OPEN_KODI })
        }
    }
}
