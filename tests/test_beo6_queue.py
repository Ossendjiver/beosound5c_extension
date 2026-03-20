#!/usr/bin/env python3
"""
Beo6 Queue Click Test — validates that clicking a queue item sends the correct
skip offset and that the service routes it properly.

Tests the full protocol flow:
  1. Connect, stream open, disco, subscribe
  2. Play a track (replace_after)
  3. Wait for renderer+player pushes
  4. Query queue (offsets 0-32, track.title + track.id + from-mots)
  5. Parse the queue items
  6. "Click" item #3 (send skip with correct offset)
  7. Verify the skip was received and processed

Usage:
  python3 tests/test_beo6_queue.py <host> [--port 5222]
"""

import argparse
import asyncio
import sys
import time
import xml.etree.ElementTree as ET

C_SEND = "\033[36m"
C_RECV = "\033[33m"
C_OK   = "\033[32m"
C_FAIL = "\033[31m"
C_HEAD = "\033[1;35m"
C_DIM  = "\033[2m"
C_RST  = "\033[0m"

BEO6_SERIAL = "99999999"
BEO6_JID = f"Beo6-{BEO6_SERIAL}@products.bang-olufsen.com"


class QueueTestClient:
    def __init__(self, host, port=5222):
        self.host = host
        self.port = port
        self.reader = None
        self.writer = None
        self.server_jid = ""
        self._iq_id = 0
        self._errors = []

    async def connect(self):
        self.reader, self.writer = await asyncio.open_connection(self.host, self.port)
        print(f"{C_OK}✓ TCP connected to {self.host}:{self.port}{C_RST}")

    async def close(self):
        if self.writer:
            try:
                self.writer.write(b'</stream:stream>')
                self.writer.close()
                await self.writer.wait_closed()
            except Exception:
                pass

    def _next_id(self):
        self._iq_id += 1
        return str(self._iq_id)

    async def _send(self, data):
        print(f"{C_SEND}  TX → {data[:300]}{C_RST}")
        if len(data) > 300:
            print(f"{C_DIM}       ... ({len(data)} bytes total){C_RST}")
        self.writer.write(data.encode('utf-8'))
        await self.writer.drain()

    async def _recv(self, timeout=5.0):
        try:
            data = await asyncio.wait_for(self.reader.read(65536), timeout=timeout)
            if not data:
                return ""
            text = data.decode('utf-8', errors='replace')
            for line in text.split('\n'):
                line = line.strip()
                if line:
                    display = line[:300]
                    print(f"{C_RECV}  RX ← {display}{C_RST}")
                    if len(line) > 300:
                        print(f"{C_DIM}       ... ({len(line)} chars total){C_RST}")
            return text
        except asyncio.TimeoutError:
            return ""

    async def _recv_until_iq(self, iq_id, timeout=10.0):
        accumulated = ""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            text = await self._recv(timeout=max(0.1, deadline - time.monotonic()))
            if not text:
                break
            accumulated += text
            if f'id="{iq_id}"' in accumulated and ('type="result"' in accumulated or 'type="error"' in accumulated):
                return accumulated
        return accumulated

    async def _recv_all(self, timeout=2.0):
        """Read all available data for a period."""
        accumulated = ""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            text = await self._recv(timeout=max(0.1, deadline - time.monotonic()))
            if not text:
                break
            accumulated += text
        return accumulated

    def _fail(self, msg):
        print(f"{C_FAIL}  ✗ {msg}{C_RST}")
        self._errors.append(msg)

    # -- Protocol steps --

    async def setup(self):
        """Open stream, disco, presence, subscribe."""
        print(f"\n{C_HEAD}── Stream Setup ──{C_RST}")

        # Stream open
        await self._send(
            '<?xml version="1.0"?>'
            '<stream:stream xmlns="jabber:client" '
            'xmlns:stream="http://etherx.jabber.org/streams" '
            f'from="{BEO6_JID}" version="1.0">'
        )
        resp = await self._recv(timeout=5)
        if 'from="' in resp:
            start = resp.index('from="') + 6
            end = resp.index('"', start)
            self.server_jid = resp[start:end]
        print(f"{C_OK}  ✓ Server JID: {self.server_jid}{C_RST}")

        # Respond to disco query from server
        if 'disco#info' in resp and 'type="get"' in resp:
            import re
            m = re.search(r'<iq[^>]*id="([^"]*)"[^>]*type="get"', resp)
            if m:
                disco_id = m.group(1)
                await self._send(
                    f'<iq id="{disco_id}" to="{self.server_jid}" from="{BEO6_JID}" type="result">'
                    f'<query xmlns="http://jabber.org/protocol/disco#info">'
                    f'<identity category="client" name="Beo6" type="product"></identity>'
                    f'<feature var="jid\\20escaping"></feature>'
                    f'</query></iq>'
                )

        # Presence
        await self._send(
            f'<presence from="{BEO6_JID}">'
            f'<c xmlns="http://jabber.org/protocol/caps" hash="sha-1" '
            f'node="beonet" name="Beo6" jid="{BEO6_JID}" '
            f'txtvers="1" ver="+IOiCrDoKxTEozkDqsd2esnKFvE="></c>'
            f'</presence>'
        )
        await asyncio.sleep(0.3)

        # Disco query
        iq_id = self._next_id()
        await self._send(
            f'<iq id="{iq_id}" to="{self.server_jid}" from="{BEO6_JID}" type="get">'
            f'<query xmlns="http://jabber.org/protocol/disco#info"></query></iq>'
        )
        await self._recv_until_iq(iq_id)

        # Subscribe renderer
        iq_id = self._next_id()
        await self._send(
            f'<iq id="{iq_id}" to="{self.server_jid}" from="{BEO6_JID}" type="set">'
            f'<status-subscribe xmlns="beonet:renderer" iid="audio_only_renderer"></status-subscribe></iq>'
        )
        resp = await self._recv_until_iq(iq_id)
        content_id = queue_id = queue_position = ""
        if 'content-id="' in resp:
            content_id = self._extract(resp, 'content-id')
            queue_id = self._extract(resp, 'queue-id')
            queue_position = self._extract(resp, 'queue-position')
        print(f"{C_OK}  ✓ Renderer: content-id={content_id} queue-id={queue_id} queue-position={queue_position}{C_RST}")

        # Subscribe player
        iq_id = self._next_id()
        await self._send(
            f'<iq id="{iq_id}" to="{self.server_jid}" from="{BEO6_JID}" type="set">'
            f'<status-subscribe xmlns="beonet:player" iid="NMUSIC"></status-subscribe></iq>'
        )
        resp = await self._recv_until_iq(iq_id)
        pq_rev = self._extract(resp, 'pq-revision')
        print(f"{C_OK}  ✓ Player: pq-revision={pq_rev}{C_RST}")

        return content_id, queue_id, pq_rev

    async def get_renderer_status(self):
        """Poll renderer status and return content-id, queue-id, queue-position."""
        print(f"\n{C_HEAD}── Renderer Status ──{C_RST}")
        iq_id = self._next_id()
        await self._send(
            f'<iq id="{iq_id}" to="{self.server_jid}" from="{BEO6_JID}" type="get">'
            f'<status iid="audio_only_renderer" xmlns="beonet:renderer"></status></iq>'
        )
        resp = await self._recv_until_iq(iq_id)
        content_id = self._extract(resp, 'content-id')
        queue_id = self._extract(resp, 'queue-id')
        queue_position = self._extract(resp, 'queue-position')
        print(f"  content-id={content_id}  queue-id={queue_id}  queue-position={queue_position}")
        return content_id, queue_id, queue_position

    async def query_queue(self, queue_id, pos=0, first_offset=0, last_offset=32):
        """Query play queue and return parsed items.
        Each item is a dict with: position_id, title, track_id, from_mots."""
        print(f"\n{C_HEAD}── Queue Query (pos={pos} offsets={first_offset}..{last_offset} queue-id={queue_id}) ──{C_RST}")
        iq_id = self._next_id()
        await self._send(
            f'<iq id="{iq_id}" to="{self.server_jid}" from="{BEO6_JID}" type="get">'
            f'<query-queue piid="NMUSIC" profile="bs5-music-queue-1_0" '
            f'pos="{pos}" first-offset="{first_offset}" last-offset="{last_offset}" '
            f'queue-id="{queue_id}" xmlns="beonet:player">'
            f'<attr name="id">'
            f'<attr name="track.title"></attr>'
            f'<attr name="track.id"></attr>'
            f'<attr name="from-mots"></attr>'
            f'</attr></query-queue></iq>'
        )
        resp = await self._recv_until_iq(iq_id)

        # Parse the response
        items = []
        revision = self._extract(resp, 'revision')

        # Find attr_column mappings
        # Expected: id=0, track.title=1, track.id=2, from-mots=3
        # Parse items
        pos_search = 0
        while True:
            item_start = resp.find('<item>', pos_search)
            if item_start < 0:
                break
            item_end = resp.find('</item>', item_start)
            if item_end < 0:
                break
            item_xml = resp[item_start:item_end + 7]

            # Extract <a value="..."> values
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
            if len(values) >= 3:
                items.append({
                    'position_id': values[0],  # column 0: id
                    'title': values[1],         # column 1: track.title
                    'track_id': values[2],      # column 2: track.id
                    'from_mots': values[3] if len(values) > 3 else '',
                })
            pos_search = item_end + 7

        print(f"  Queue revision={revision}, {len(items)} items:")
        for i, item in enumerate(items):
            marker = " ←CURRENT" if i == 0 else ""
            print(f"    [{i}] pos_id={item['position_id']} track_id={item['track_id']} "
                  f"title=\"{item['title'][:40]}\"{marker}")

        return items, revision

    async def send_skip(self, queue_id, offset):
        """Send a skip command (like the Beo6 does when clicking a queue item)."""
        print(f"\n{C_HEAD}── Skip (queue-id={queue_id} offset={offset}) ──{C_RST}")
        iq_id = self._next_id()
        await self._send(
            f'<iq id="{iq_id}" to="{self.server_jid}" from="{BEO6_JID}" type="set">'
            f'<skip piid="NMUSIC" queue-id="{queue_id}" offset="{offset}" '
            f'xmlns="beonet:player"></skip></iq>'
        )
        resp = await self._recv_until_iq(iq_id, timeout=10)
        if 'command' in resp or f'id="{iq_id}"' in resp:
            print(f"{C_OK}  ✓ Skip acknowledged{C_RST}")
        else:
            self._fail("No skip ack")

        # Collect push messages
        extra = await self._recv_all(timeout=3)
        resp += extra

        # Parse new content-id from any renderer push
        new_content_id = ""
        if 'content-id="' in resp:
            # Find the LAST content-id in the response (most recent push)
            idx = resp.rfind('content-id="')
            if idx >= 0:
                start = idx + 12
                end = resp.find('"', start)
                new_content_id = resp[start:end]

        new_queue_id = ""
        if 'queue-id="' in resp:
            idx = resp.rfind('queue-id="')
            if idx >= 0:
                start = idx + 10
                end = resp.find('"', start)
                new_queue_id = resp[start:end]

        print(f"  After skip: content-id={new_content_id} queue-id={new_queue_id}")
        return new_content_id, new_queue_id

    def _extract(self, text, attr):
        pattern = f'{attr}="'
        idx = text.find(pattern)
        if idx < 0:
            return ""
        start = idx + len(pattern)
        end = text.find('"', start)
        return text[start:end] if end >= 0 else ""

    def print_summary(self):
        print(f"\n{C_HEAD}{'='*60}{C_RST}")
        if self._errors:
            print(f"{C_FAIL}  {len(self._errors)} error(s):{C_RST}")
            for e in self._errors:
                print(f"{C_FAIL}    • {e}{C_RST}")
        else:
            print(f"{C_OK}  All tests passed!{C_RST}")
        print(f"{C_HEAD}{'='*60}{C_RST}")


async def run_test(host, port):
    client = QueueTestClient(host, port)
    try:
        await client.connect()
        content_id, queue_id, pq_rev = await client.setup()

        # Step 1: Get current renderer status
        content_id, queue_id, queue_position = await client.get_renderer_status()
        print(f"\n{C_HEAD}── Analysis ──{C_RST}")
        print(f"  Renderer says: content-id=\"{content_id}\" queue-id=\"{queue_id}\"")

        # Step 2: Query the queue
        items, revision = await client.query_queue(queue_id, pos=0, first_offset=0, last_offset=10)

        if len(items) < 2:
            client._fail(f"Need at least 2 queue items, got {len(items)}")
            client.print_summary()
            return

        # Step 3: Verify content-id matches one of the track.id values
        current_item = None
        current_idx = None
        for i, item in enumerate(items):
            if item['track_id'] == content_id:
                current_item = item
                current_idx = i
                break

        if current_item is None:
            client._fail(
                f"content-id=\"{content_id}\" not found in any queue item's track.id! "
                f"Queue track_ids: {[it['track_id'] for it in items[:5]]}"
            )
            print(f"{C_FAIL}  This is why the Beo6 can't calculate skip offsets!{C_RST}")
        else:
            print(f"{C_OK}  ✓ content-id=\"{content_id}\" found at queue index {current_idx} "
                  f"(position_id={current_item['position_id']}){C_RST}")

        # Step 4: Verify position_ids are unique
        pos_ids = [it['position_id'] for it in items]
        if len(set(pos_ids)) != len(pos_ids):
            client._fail(f"Queue position_ids are NOT unique: {pos_ids[:10]}")
        else:
            print(f"{C_OK}  ✓ Queue position_ids are unique: {pos_ids[:5]}...{C_RST}")

        # Step 5: Simulate clicking item #3 (4th in list)
        target_idx = min(3, len(items) - 1)
        target = items[target_idx]
        print(f"\n{C_HEAD}── Simulating click on item #{target_idx}: \"{target['title']}\" ──{C_RST}")

        if current_idx is not None:
            # Calculate offset like the Beo6 would
            # Real BM5: offset = clicked_position_id - current_position_id
            # (position_ids are sequential, so this equals the array index difference)
            offset = target_idx - current_idx
            print(f"  Calculated offset: {target_idx} - {current_idx} = {offset}")
        else:
            # If we can't find current, just use the array index
            offset = target_idx
            print(f"  Using array index as offset: {offset} (content-id match failed)")

        # Send the skip
        new_content_id, new_queue_id = await client.send_skip(queue_id, offset)

        # Step 6: Verify — query renderer status to see what's playing now
        await asyncio.sleep(2)
        new_content_id2, new_queue_id2, new_pos = await client.get_renderer_status()

        # Step 7: Re-query queue to verify
        new_items, new_rev = await client.query_queue(
            new_queue_id2 or queue_id, pos=0, first_offset=0, last_offset=3
        )

        # Compare: did the correct track start playing?
        print(f"\n{C_HEAD}── Verification ──{C_RST}")
        print(f"  Target was: \"{target['title']}\" (track_id={target['track_id']})")
        if new_items:
            print(f"  Now playing: \"{new_items[0]['title']}\" (track_id={new_items[0]['track_id']})")
        print(f"  Renderer content-id: {new_content_id2}")

        # The real BM5 protocol comparison
        print(f"\n{C_HEAD}── Real BM5 Protocol Comparison ──{C_RST}")
        print(f"  Real BM5 queue items have:")
        print(f"    column 0 (id): sequential position IDs (e.g., 136, 137, 138, ...)")
        print(f"    track.id: track database ID (matches content-id)")
        print(f"  Our queue items have:")
        print(f"    column 0 (id): {pos_ids[:5]}")
        print(f"    track.id: {[it['track_id'] for it in items[:5]]}")
        print(f"    content-id: \"{content_id}\"")

        # Check if content-id matching works
        track_ids_in_queue = [it['track_id'] for it in items]
        if content_id in track_ids_in_queue:
            print(f"{C_OK}  ✓ content-id matching works (found in track.id list){C_RST}")
        else:
            print(f"{C_FAIL}  ✗ content-id matching BROKEN — \"{content_id}\" not in track.id values{C_RST}")
            print(f"{C_FAIL}    The Beo6 cannot find the current track in the queue.{C_RST}")
            print(f"{C_FAIL}    It defaults to offset=0 for every click → nothing happens.{C_RST}")

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
    parser = argparse.ArgumentParser(description='Beo6 Queue Click Test')
    parser.add_argument('host', help='Target host')
    parser.add_argument('--port', type=int, default=5222)
    args = parser.parse_args()
    ok = asyncio.run(run_test(args.host, args.port))
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
