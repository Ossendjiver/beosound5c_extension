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
    fun bathroomKeepsMorningNewsAndCd() {
        val bathroom = AppSettings().rooms.first { it.id == "bathroom" }
        assertTrue(bathroom.tiles.any { it.title == "Morning news" })
        assertTrue(bathroom.tiles.any { it.actionType == TileActionType.SELECT_SOURCE && it.source == "CD" })
        assertTrue("CD" in bathroom.sourceOptions)
    }

    @Test
    fun kitchenDoesNotExposeCdByDefault() {
        val kitchen = AppSettings().rooms.first { it.id == "kitchen" }
        assertTrue(kitchen.tiles.none { it.actionType == TileActionType.SELECT_SOURCE && it.source == "CD" })
    }

    @Test
    fun allRoomsUseUniversalSourceTile() {
        AppSettings().rooms.forEach { room ->
            val source = room.tiles.firstOrNull { it.id == "__SOURCE__" }
            assertTrue("Missing Source tile in ${room.name}", source != null)
            assertEquals("Source", source?.title)
        }
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
