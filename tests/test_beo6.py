#!/usr/bin/env python3
"""
Beo6 XMPP test client — emulates a Beo6 remote connecting to the beo-beo6 service.

Exercises the full protocol flow:
  1. XMPP stream open
  2. Disco info exchange
  3. Presence announcement
  4. Subscribe to renderer + player status
  5. Content queries (artists, albums, tracks)
  6. Play a track
  7. Skip next
  8. Queue query
  9. Ping keep-alive

Usage:
  python3 tests/test_beo6.py <host> [--port 5222] [--art-port 8080]

Runs all tests sequentially, printing detailed protocol logs.
"""

import argparse
import asyncio
import socket
import sys
import time
import xml.etree.ElementTree as ET


BEO6_SERIAL = "50277416"
BEO6_JID = f"Beo6-{BEO6_SERIAL}@products.bang-olufsen.com"

# ANSI colors
C_SEND = "\033[36m"   # cyan
C_RECV = "\033[33m"   # yellow
C_OK   = "\033[32m"   # green
C_FAIL = "\033[31m"   # red
C_HEAD = "\033[1;35m" # bold magenta
C_DIM  = "\033[2m"    # dim
C_RST  = "\033[0m"    # reset


class Beo6Client:
    def __init__(self, host, port=5222):
        self.host = host
        self.port = port
        self.reader = None
        self.writer = None
        self.server_jid = ""
        self._iq_id = 0
        self._buf = b""
        self._capabilities = []
        self._renderer_state = {}
        self._tracks = []
        self._artists = []
        self._albums = []
        self._errors = []

    async def connect(self):
        print(f"{C_HEAD}{'='*60}{C_RST}")
        print(f"{C_HEAD}  Beo6 Test Client → {self.host}:{self.port}{C_RST}")
        print(f"{C_HEAD}{'='*60}{C_RST}")
        print()

        self.reader, self.writer = await asyncio.open_connection(self.host, self.port)
        print(f"{C_OK}✓ TCP connected{C_RST}")

    async def close(self):
        if self.writer:
            try:
                self._send_raw('</stream:stream>')
                self.writer.close()
                await self.writer.wait_closed()
            except Exception:
                pass

    def _next_id(self):
        self._iq_id += 1
        return str(self._iq_id)

    def _send_raw(self, data):
        print(f"{C_SEND}  → {data[:200]}{C_RST}")
        if len(data) > 200:
            print(f"{C_DIM}    ... ({len(data)} bytes total){C_RST}")
        self.writer.write(data.encode('utf-8'))

    async def _send_and_drain(self, data):
        self._send_raw(data)
        await self.writer.drain()

    async def _recv(self, timeout=5.0):
        """Read data with timeout. Returns decoded string."""
        try:
            data = await asyncio.wait_for(self.reader.read(16384), timeout=timeout)
            if not data:
                print(f"{C_FAIL}  ✗ Connection closed by server{C_RST}")
                return ""
            text = data.decode('utf-8', errors='replace')
            # Print each line
            for line in text.split('\n'):
                line = line.strip()
                if line:
                    print(f"{C_RECV}  ← {line[:200]}{C_RST}")
                    if len(line) > 200:
                        print(f"{C_DIM}    ... ({len(line)} chars){C_RST}")
            return text
        except asyncio.TimeoutError:
            print(f"{C_FAIL}  ✗ Timeout waiting for response{C_RST}")
            return ""

    async def _recv_until_iq(self, iq_id, timeout=5.0):
        """Read until we get an IQ result with the given ID."""
        accumulated = ""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            text = await self._recv(timeout=remaining)
            if not text:
                break
            accumulated += text
            # Check if we have our IQ result
            if f'id="{iq_id}"' in accumulated and 'type="result"' in accumulated:
                return accumulated
        return accumulated

    def _parse_stanzas(self, text):
        """Parse XML stanzas from text. Returns list of ET elements."""
        stanzas = []
        # Wrap in root for parsing
        wrapped = f'<root xmlns:stream="http://etherx.jabber.org/streams">{text}</root>'
        try:
            root = ET.fromstring(wrapped)
            for child in root:
                stanzas.append(child)
        except ET.ParseError:
            # Try to extract individual stanzas
            for tag in ('iq', 'message', 'presence'):
                start = 0
                while True:
                    idx = text.find(f'<{tag}', start)
                    if idx < 0:
                        break
                    end = text.find(f'</{tag}>', idx)
                    if end < 0:
                        break
                    end += len(f'</{tag}>')
                    try:
                        el = ET.fromstring(text[idx:end])
                        stanzas.append(el)
                    except ET.ParseError:
                        pass
                    start = end
        return stanzas

    # -- Protocol steps --

    async def test_stream_open(self):
        """Step 1: Open XMPP stream."""
        self._section("Stream Open")

        stream = (
            '<?xml version="1.0"?>'
            '<stream:stream xmlns="jabber:client" '
            'xmlns:stream="http://etherx.jabber.org/streams" '
            f'from="{BEO6_JID}" version="1.0">'
        )
        await self._send_and_drain(stream)

        # Expect stream response + disco query from server
        resp = await self._recv(timeout=5)
        if not resp:
            self._fail("No stream response")
            return False

        if '<stream:stream' in resp:
            # Extract server JID
            if 'from="' in resp:
                start = resp.index('from="') + 6
                end = resp.index('"', start)
                self.server_jid = resp[start:end]
            print(f"{C_OK}  ✓ Stream opened — server JID: {self.server_jid}{C_RST}")
        else:
            self._fail("No stream:stream in response")
            return False

        # Server should send disco#info query
        if 'disco#info' in resp:
            print(f"{C_OK}  ✓ Server sent disco#info query{C_RST}")
            # Extract the IQ id to respond to
            disco_id = self._extract_attr(resp, 'iq', 'id')
            if disco_id:
                await self._respond_disco(disco_id)
        else:
            print(f"{C_DIM}  (no disco query from server — may come later){C_RST}")

        return True

    async def _respond_disco(self, iq_id):
        """Respond to server's disco#info query with our capabilities."""
        resp = (
            f'<iq id="{iq_id}" to="{self.server_jid}" '
            f'from="{BEO6_JID}" type="result">'
            f'<query xmlns="http://jabber.org/protocol/disco#info">'
            f'<identity category="client" name="Beo6" type="product"></identity>'
            f'<feature var="jid\\20escaping"></feature>'
            f'</query></iq>'
        )
        await self._send_and_drain(resp)
        print(f"{C_OK}  ✓ Sent disco result{C_RST}")

    async def test_presence(self):
        """Step 2: Send presence announcement."""
        self._section("Presence")

        presence = (
            f'<presence from="{BEO6_JID}">'
            f'<c xmlns="http://jabber.org/protocol/caps" hash="sha-1" '
            f'node="beonet" name="Beo6" jid="{BEO6_JID}" '
            f'txtvers="1" ver="+IOiCrDoKxTEozkDqsd2esnKFvE="></c>'
            f'</presence>'
        )
        await self._send_and_drain(presence)
        print(f"{C_OK}  ✓ Presence sent{C_RST}")
        # Small delay for server to process
        await asyncio.sleep(0.2)

    async def test_disco_query(self):
        """Step 3: Query server capabilities."""
        self._section("Disco Query")

        iq_id = self._next_id()
        query = (
            f'<iq id="{iq_id}" to="{self.server_jid}" '
            f'from="{BEO6_JID}" type="get">'
            f'<query xmlns="http://jabber.org/protocol/disco#info"></query></iq>'
        )
        await self._send_and_drain(query)
        resp = await self._recv_until_iq(iq_id)

        if 'beonet:content' in resp:
            print(f"{C_OK}  ✓ Server supports beonet:content{C_RST}")
            self._capabilities.append('content')
        if 'beonet:player' in resp:
            print(f"{C_OK}  ✓ Server supports beonet:player{C_RST}")
            self._capabilities.append('player')
        if 'beonet:renderer' in resp:
            print(f"{C_OK}  ✓ Server supports beonet:renderer{C_RST}")
            self._capabilities.append('renderer')
        if 'beonet:power' in resp:
            print(f"{C_OK}  ✓ Server supports beonet:power{C_RST}")
            self._capabilities.append('power')

        if not self._capabilities:
            self._fail("No BeoNet capabilities found in disco response")

        return bool(self._capabilities)

    async def test_subscribe_renderer(self):
        """Step 4: Subscribe to renderer status."""
        self._section("Subscribe Renderer")

        iq_id = self._next_id()
        sub = (
            f'<iq id="{iq_id}" to="{self.server_jid}" '
            f'from="{BEO6_JID}" type="set">'
            f'<status-subscribe xmlns="beonet:renderer" '
            f'iid="audio_only_renderer"></status-subscribe></iq>'
        )
        await self._send_and_drain(sub)
        resp = await self._recv_until_iq(iq_id)

        if 'beonet:renderer' in resp:
            print(f"{C_OK}  ✓ Renderer subscription confirmed{C_RST}")
            # Extract state
            if 'state="' in resp:
                state = self._extract_attr(resp, 'status', 'state')
                volume = self._extract_attr(resp, 'status', 'volume')
                content_id = self._extract_attr(resp, 'status', 'content-id')
                print(f"     State: {state}, Volume: {volume}, Content-ID: {content_id}")
                self._renderer_state = {
                    'state': state, 'volume': volume, 'content_id': content_id}
            return True
        else:
            self._fail("No renderer status in response")
            return False

    async def test_subscribe_player(self):
        """Step 5: Subscribe to player status."""
        self._section("Subscribe Player")

        iq_id = self._next_id()
        sub = (
            f'<iq id="{iq_id}" to="{self.server_jid}" '
            f'from="{BEO6_JID}" type="set">'
            f'<status-subscribe xmlns="beonet:player" '
            f'iid="NMUSIC"></status-subscribe></iq>'
        )
        await self._send_and_drain(sub)
        resp = await self._recv_until_iq(iq_id)

        if 'beonet:player' in resp:
            print(f"{C_OK}  ✓ Player subscription confirmed{C_RST}")
            if 'pq-revision' in resp:
                rev = self._extract_attr(resp, 'status', 'pq-revision')
                print(f"     PQ Revision: {rev}")
            return True
        else:
            self._fail("No player status in response")
            return False

    async def test_content_tracks(self):
        """Step 6: Query tracks (recent, first page)."""
        self._section("Content Query — Tracks (last played)")

        iq_id = self._next_id()
        query = (
            f'<iq id="{iq_id}" to="{self.server_jid}" '
            f'from="{BEO6_JID}" type="get">'
            f'<query instance_id="NMUSIC" profile="bs5-music-1_0" '
            f'type="track" first="0" last="5" iid="NMUSIC" '
            f'xmlns="beonet:content">'
            f'<attr name="album.extra-small-cover-url"></attr>'
            f'<order_by attr="last-played-time" sort="desc"></order_by>'
            f'<attr name="title"></attr>'
            f'<attr name="id"></attr>'
            f'<attr name="album.title"></attr>'
            f'</query></iq>'
        )
        await self._send_and_drain(query)
        resp = await self._recv_until_iq(iq_id)

        if 'query_result' in resp:
            items = resp.count('<item>')
            print(f"{C_OK}  ✓ Got query_result with {items} tracks{C_RST}")
            # Extract track titles
            self._tracks = self._extract_items(resp)
            for i, t in enumerate(self._tracks[:5]):
                print(f"     [{i}] {t}")
            return items > 0
        else:
            self._fail("No query_result in response")
            return False

    async def test_content_artists(self):
        """Step 7: Query artists."""
        self._section("Content Query — Artists")

        iq_id = self._next_id()
        query = (
            f'<iq id="{iq_id}" to="{self.server_jid}" '
            f'from="{BEO6_JID}" type="get">'
            f'<query instance_id="NMUSIC" profile="bs5-music-1_0" '
            f'type="album-artist" first="0" last="10" iid="NMUSIC" '
            f'xmlns="beonet:content">'
            f'<attr name="name"></attr>'
            f'<order_by attr="name" sort="asc"></order_by>'
            f'<attr name="id"></attr>'
            f'</query></iq>'
        )
        await self._send_and_drain(query)
        resp = await self._recv_until_iq(iq_id)

        if 'query_result' in resp:
            items = resp.count('<item>')
            print(f"{C_OK}  ✓ Got {items} artists{C_RST}")
            self._artists = self._extract_items(resp)
            for a in self._artists[:5]:
                print(f"     {a}")
            return True
        else:
            self._fail("No artist query_result")
            return False

    async def test_content_albums(self):
        """Step 8: Query albums (all or by artist)."""
        self._section("Content Query — Albums")

        iq_id = self._next_id()
        query = (
            f'<iq id="{iq_id}" to="{self.server_jid}" '
            f'from="{BEO6_JID}" type="get">'
            f'<query instance_id="NMUSIC" profile="bs5-music-1_0" '
            f'type="album" first="0" last="10" iid="NMUSIC" '
            f'xmlns="beonet:content">'
            f'<attr name="title"></attr>'
            f'<order_by attr="title" sort="asc"></order_by>'
            f'<attr name="id"></attr>'
            f'<attr name="album-artist.id"></attr>'
            f'</query></iq>'
        )
        await self._send_and_drain(query)
        resp = await self._recv_until_iq(iq_id)

        if 'query_result' in resp:
            items = resp.count('<item>')
            print(f"{C_OK}  ✓ Got {items} albums{C_RST}")
            self._albums = self._extract_items(resp)
            for a in self._albums[:5]:
                print(f"     {a}")
            return True
        else:
            self._fail("No album query_result")
            return False

    async def test_play_track(self):
        """Step 9: Play first track from catalog."""
        self._section("Play Track")

        if not self._tracks:
            print(f"{C_DIM}  (skipping — no tracks in catalog){C_RST}")
            return True

        # Extract the ID from first track (format: "cover_url | title | id | album.title")
        first = self._tracks[0]
        parts = [p.strip() for p in first.split('|')]
        track_id = parts[2] if len(parts) > 2 else "1"
        print(f"     Playing track ID: {track_id} ({parts[1] if len(parts) > 1 else '?'})")

        iq_id = self._next_id()
        play = (
            f'<iq id="{iq_id}" to="{self.server_jid}" '
            f'from="{BEO6_JID}" type="set">'
            f'<replace_after piid="NMUSIC" '
            f'content-server-jid="{self.server_jid}" '
            f'profile="bs5-music-1_0" type="track" random="false" '
            f'queue-id="" xmlns="beonet:player">'
            f'<filters><static_filter attr="id" value="{track_id}" opr="eq">'
            f'</static_filter></filters>'
            f'<seed key="" value=""></seed></replace_after></iq>'
        )
        await self._send_and_drain(play)

        # Should get: IQ result + possibly message pushes
        resp = await self._recv_until_iq(iq_id, timeout=10)

        if 'command' in resp or f'id="{iq_id}"' in resp:
            print(f"{C_OK}  ✓ Play command acknowledged{C_RST}")

            # Check for push messages
            if '<message' in resp:
                msg_count = resp.count('<message')
                print(f"{C_OK}  ✓ Received {msg_count} push message(s){C_RST}")
                if 'beonet:renderer' in resp:
                    print(f"     → Renderer status update")
                if 'beonet:player' in resp:
                    print(f"     → Player status update")

            # Wait a moment for more pushes
            await asyncio.sleep(1)
            try:
                extra = await self._recv(timeout=2)
                if extra and '<message' in extra:
                    print(f"{C_OK}  ✓ Additional push messages received{C_RST}")
            except Exception:
                pass

            return True
        else:
            self._fail("No play ack received")
            return False

    async def test_skip(self):
        """Step 10: Skip to next track."""
        self._section("Skip Next")

        iq_id = self._next_id()
        skip = (
            f'<iq id="{iq_id}" to="{self.server_jid}" '
            f'from="{BEO6_JID}" type="set">'
            f'<skip piid="NMUSIC" queue-id="1" offset="0" '
            f'xmlns="beonet:player"></skip></iq>'
        )
        await self._send_and_drain(skip)
        resp = await self._recv_until_iq(iq_id, timeout=10)

        if 'command' in resp or f'id="{iq_id}"' in resp:
            print(f"{C_OK}  ✓ Skip acknowledged{C_RST}")
            return True
        else:
            self._fail("No skip ack")
            return False

    async def test_queue_query(self):
        """Step 11: Query play queue."""
        self._section("Queue Query")

        iq_id = self._next_id()
        query = (
            f'<iq id="{iq_id}" to="{self.server_jid}" '
            f'from="{BEO6_JID}" type="get">'
            f'<query-queue piid="NMUSIC" profile="bs5-music-queue-1_0" '
            f'pos="0" first-offset="0" last-offset="3" '
            f'queue-id="1" xmlns="beonet:player"></query-queue></iq>'
        )
        await self._send_and_drain(query)
        resp = await self._recv_until_iq(iq_id)

        if 'query_result' in resp:
            items = resp.count('<item>')
            print(f"{C_OK}  ✓ Queue has {items} items{C_RST}")
            return True
        else:
            self._fail("No queue query_result")
            return False

    async def test_renderer_poll(self):
        """Step 12: Poll renderer status."""
        self._section("Renderer Status Poll")

        iq_id = self._next_id()
        poll = (
            f'<iq id="{iq_id}" to="{self.server_jid}" '
            f'from="{BEO6_JID}" type="get">'
            f'<status iid="audio_only_renderer" '
            f'xmlns="beonet:renderer"></status></iq>'
        )
        await self._send_and_drain(poll)
        resp = await self._recv_until_iq(iq_id)

        if 'beonet:renderer' in resp:
            state = self._extract_attr(resp, 'status', 'state')
            volume = self._extract_attr(resp, 'status', 'volume')
            content_id = self._extract_attr(resp, 'status', 'content-id')
            print(f"{C_OK}  ✓ Renderer: state={state} volume={volume} content-id={content_id}{C_RST}")
            return True
        else:
            self._fail("No renderer status")
            return False

    async def test_ping(self):
        """Step 13: XMPP ping."""
        self._section("Ping")

        iq_id = self._next_id()
        ping = (
            f'<iq id="{iq_id}" to="{self.server_jid}" '
            f'from="{BEO6_JID}" type="get">'
            f'<ping xmlns="urn:xmpp:ping"></ping></iq>'
        )
        await self._send_and_drain(ping)
        resp = await self._recv_until_iq(iq_id)

        if f'id="{iq_id}"' in resp:
            print(f"{C_OK}  ✓ Pong received{C_RST}")
            return True
        else:
            self._fail("No pong")
            return False

    async def test_cover_art(self, art_host, art_port):
        """Step 14: Fetch cover art via HTTP."""
        self._section("Cover Art (HTTP)")

        if not self._tracks:
            print(f"{C_DIM}  (skipping — no tracks){C_RST}")
            return True

        # Extract artwork path from first track (first field in pipe-separated values)
        first = self._tracks[0]
        parts = [p.strip() for p in first.split('|')]
        art_path = parts[0] if parts else ''

        import aiohttp
        ok = True

        # Test with real artwork path from catalog
        if art_path and art_path.startswith('E:'):
            try:
                from urllib.parse import quote
                url = f"http://{art_host}:{art_port}/?path={quote(art_path, safe='')}"
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                        if resp.status == 200:
                            data = await resp.read()
                            ct = resp.headers.get('Content-Type', '')
                            print(f"{C_OK}  ✓ Cover art: {len(data)} bytes ({ct}){C_RST}")
                        else:
                            print(f"{C_FAIL}  ✗ Cover art HTTP {resp.status}{C_RST}")
                            ok = False
            except Exception as e:
                self._fail(f"Art fetch error: {e}")
                ok = False
        else:
            print(f"{C_DIM}  (no artwork path in track data){C_RST}")

        # Also test server reachability with dummy path
        try:
            url = f"http://{art_host}:{art_port}/?path=E%3A%5CCache%5CCovers%5Ctest"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status in (200, 404, 502):
                        print(f"{C_OK}  ✓ Cover art server reachable (dummy: HTTP {resp.status}){C_RST}")
                    else:
                        self._fail(f"Unexpected status: {resp.status}")
                        ok = False
        except Exception as e:
            self._fail(f"Art server error: {e}")
            ok = False

        return ok

    # -- Helpers --

    def _section(self, title):
        print()
        print(f"{C_HEAD}── {title} ──{C_RST}")

    def _fail(self, msg):
        print(f"{C_FAIL}  ✗ {msg}{C_RST}")
        self._errors.append(msg)

    def _extract_attr(self, text, tag, attr):
        """Extract an attribute value from XML text."""
        pattern = f'{attr}="'
        # Find within the tag context
        tag_start = text.find(f'<{tag} ')
        if tag_start < 0:
            tag_start = text.find(f'<{tag}>')
        if tag_start < 0:
            return ""
        search_from = tag_start
        idx = text.find(pattern, search_from)
        if idx < 0:
            return ""
        start = idx + len(pattern)
        end = text.find('"', start)
        if end < 0:
            return ""
        return text[start:end]

    def _extract_items(self, text):
        """Extract <item><a value="..."> sequences from query_result."""
        items = []
        pos = 0
        while True:
            item_start = text.find('<item>', pos)
            if item_start < 0:
                break
            item_end = text.find('</item>', item_start)
            if item_end < 0:
                break
            item_xml = text[item_start:item_end + 7]
            # Extract values
            values = []
            vpos = 0
            while True:
                vidx = item_xml.find('value="', vpos)
                if vidx < 0:
                    break
                vstart = vidx + 7
                vend = item_xml.find('"', vstart)
                if vend < 0:
                    break
                values.append(item_xml[vstart:vend])
                vpos = vend + 1
            items.append(' | '.join(values))
            pos = item_end + 7
        return items

    def print_summary(self):
        print()
        print(f"{C_HEAD}{'='*60}{C_RST}")
        if self._errors:
            print(f"{C_FAIL}  {len(self._errors)} error(s):{C_RST}")
            for e in self._errors:
                print(f"{C_FAIL}    • {e}{C_RST}")
        else:
            print(f"{C_OK}  All tests passed!{C_RST}")
        print(f"{C_HEAD}{'='*60}{C_RST}")


async def run_tests(host, port, art_port):
    client = Beo6Client(host, port)
    try:
        await client.connect()

        await client.test_stream_open()
        await client.test_presence()
        await client.test_disco_query()
        await client.test_subscribe_renderer()
        await client.test_subscribe_player()
        await client.test_content_tracks()
        await client.test_content_artists()
        await client.test_content_albums()
        await client.test_play_track()
        await asyncio.sleep(2)  # let playback start
        await client.test_skip()
        await client.test_queue_query()
        await client.test_renderer_poll()
        await client.test_ping()
        await client.test_cover_art(host, art_port)

        client.print_summary()
    except KeyboardInterrupt:
        print(f"\n{C_DIM}Interrupted{C_RST}")
    except Exception as e:
        print(f"{C_FAIL}Fatal: {e}{C_RST}")
        import traceback
        traceback.print_exc()
    finally:
        await client.close()

    return len(client._errors) == 0


def main():
    parser = argparse.ArgumentParser(description='Beo6 XMPP test client')
    parser.add_argument('host', help='Target host (BS5c with beo-beo6 service)')
    parser.add_argument('--port', type=int, default=5222, help='XMPP port')
    parser.add_argument('--art-port', type=int, default=8080, help='Cover art HTTP port')
    args = parser.parse_args()

    ok = asyncio.run(run_tests(args.host, args.port, args.art_port))
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
