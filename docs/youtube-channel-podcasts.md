# YouTube channels as MA podcasts

Search results with an uploader URL can offer **Save channel as podcast**. The NewPipe bridge resolves the canonical channel ID and serves a stable podcast feed. BS5c checks existing MA podcast-provider configurations before creating a subscription. Repeating the action does not create another provider for the same feed.

Saving is intercepted before room targeting: it does not activate a source, change a queue or start playback. The bridge needs channel/podcast endpoints reachable from MA, and provider creation requires an MA admin connection. This is a feed of finite uploads rather than a full channel archive; MA controls subsequent feed refreshes.
