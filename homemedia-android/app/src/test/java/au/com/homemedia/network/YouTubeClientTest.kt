package au.com.homemedia.network

import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertTrue
import org.junit.Test

class YouTubeClientTest {
    @Test
    fun newPipeSearchReturnsVideoResults() = runBlocking {
        val results = YouTubeClient().search("OpenAI")
        assertTrue("NewPipe returned no YouTube results", results.isNotEmpty())
        assertTrue("First YouTube result has no video ID", results.first().videoId.isNotBlank())
    }
}
