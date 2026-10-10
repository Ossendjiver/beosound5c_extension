# Performer and long SoundCloud mix enrichment

These changes are prepared for deployment; no live services, MA database or queues are changed by the offline validation.

## Diagnosis and recovery

MA can return an empty `artists` list for a canonical library track even when its artist-browser export carries the performer. The Songs/Playlists catalogue omitted those artist-browser credits. Recovery now indexes explicit leaf credits across the export by exact track URI; duplicate/conflicting credits are withheld. No title matching, composer-folder guessing or album-artist inheritance is used for repair.

Provider performer credits are preserved. A separate metadata worker can read embedded file tags through a bounded ffprobe call, restricted to available filesystem mappings inside the configured music root. PERFORMER is preferred. Classical ARTIST without a distinct composer credit, or ARTIST equal to COMPOSER, is left unresolved rather than advertised as a recovered performer.

The read-only library test recovered Beaux Arts Trio, Alfred Brendel and others. The original Solveig radio test had nine missing artist strings; the revised test had two among nineteen selections. Those remaining local files supplied Beethoven as ARTIST rather than a usable performer credit, so they require better source tags. Existing provider composer credits remain as supplied and are not relabelled as performers.

## SoundCloud mix genres

SoundCloud's genre/style fields can contain uploader names and quoted artist lists rather than genres. Recognised genre labels are parsed conservatively; arbitrary artist-name tags are not radio genre evidence. For mixes at least twenty minutes long, explicit artist tags can support inferred genres only when at least three distinct exact library artist matches agree with at least 75% support. Each artist gets one vote, independent of play counts and favourites. Ambiguous broad-family ties are withheld. As a weaker fallback, at least three distinct recordings by the uploader must agree on a specific style with 75% support; aliases of the same title count once. This inference has separate uploader/recording provenance. Inferred labels retain source, confidence and artist evidence separately from supplied genres. They never count as a measured mood profile or bypass pending audio analysis.

Finnebassen's BBC Radio 1 Essential Mix has no explicit genre beyond `Finnebassen`. Its artist-tag references currently have mixed electronic/pop library tags, insufficient for an unambiguous classification under this rule. The artist-tag inference is withheld. A separate uploader reference fallback can identify deep house from three distinct Finnebassen recordings out of four with known library genres; this remains an inferred genre, not a measured house mood. Genre-matched mixes still obey the root-duration limits already implemented.

## Deployment workflow

Run the metadata-only worker with the installed MA environment, on SSD, after installing these sources:

```
python tools/enrich_library_metadata.py --max-files 10000
```

It writes `/media/local/cache/track_enrichment.json` atomically with exact provider aliases; it never writes MA's database or commands playback. The library reads it alongside audio/provider sidecars. The provider profiler also applies mix enrichment during future checks, using the canonical library's artist genres. Fingerprints include relevant metadata so older poorly tagged records are revisited. Audio analysis remains responsible for measured mood, not popularity or inferred genres.
