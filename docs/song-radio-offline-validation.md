# Song radio offline validation — 10 October 2026

Read-only evaluation used the installed 15,777-entry library, persisted listening
history and cached provider/local audio profiles. Ranking was executed without
player, queue or service commands. No live deployment or queue refresh was made.

Initial seed checks covered Grieg/Maisky, Erykah Badu, Fred again.., Oklou,
Sufjan Stevens, Kendrick Lamar, John Coltrane, Radiohead, Brian Eno and a
Finnebassen Essential Mix. Seeds were selected by track/artist metadata; Eno's
Help Me Somebody is a funk/crossover example, not an ambient reference.
The final corrective comparison repeated the following cases:

| Seed | Eligible candidates | Distinct ranked picks | Outcome |
| --- | ---: | ---: | --- |
| Solveig's Song | 347 | 18 | Chamber/classical; Beethoven slow movements and Chopin nocturnes |
| Next Lifetime | 985 | 20 | Soul/R&B; Beyoncé, Ashanti, Sabrina Claudio, Xavier Omär |
| Silent Hill | 1,919 | 20 | Hip-hop; Sylvan LaCue, GoldLink, A Tribe Called Quest, Kendrick |
| Moment's Notice | 69 | 20 | Jazz; James Brandon Lewis, Jimmy Haslip, Ben Williams, Ornette Coleman |
| Creep | 638 | 20 | Rock; David Gilmour, RHCP, Augie March, Smashing Pumpkins |
| BBC Radio 1 Essential Mix (Finnebassen) | 0 | 0 | Insufficient genre/audio evidence; no unrelated filler |

Candidate numbers describe the inspected snapshot, not guaranteed fixed counts.
All six final cases had zero duration-rule or defined genre-rule violations.
This is offline structural validation, not a listening-quality assessment.

## Gaps found and addressed

- Generic Experimental/Abstract tags were treated as specific genre matches.
  Only recognised genre/subgenre labels now establish genre fallback.
- Broad mixed album tags admitted pop/hip-hop into pure jazz radio. Dominant
  genre evidence and specific corroboration now constrain these crossovers.
- R&B spellings, folktronica and rap substyles were not consistently mapped.
  Normalisation now preserves these families; rap-specific evidence prevents
  neo-soul album tags from overriding a hip-hop seed.
- Standalone Symphony/Sonata titles were treated as classical. Explicit work
  numbers or repertoire construction are now required.
- Artist arrays and metadata genres could be discarded by the library loader.
  They are retained, along with supplied ISRC/external identifiers. Existing
  artist data is retained when duplicate rows omit it.
- Genre-derived mood coordinates marked tracks metadata-ready and suppressed
  sampling. Only validated audio descriptors or explicit manual mood profiles
  now avoid sampling. Old tag-only metadata-ready entries are eligible for the
  pending sampler and included in pending counts; their genre tags are retained.

## Remaining limits

Nine of the 18 selected classical entries still have no performer identity in
the available catalogue. Preserving artist arrays fixes loss when supplied, but
cannot reconstruct missing metadata; artist pacing and version detection remain
weaker for those records. Some differently named performances of a classical
work therefore need better identifiers/performer metadata.

For Fred again.. and Oklou, close acoustic matches with no genre tags include
crossovers. Current mood/embedding features cannot guarantee semantic genre
identity without track-specific metadata or a validated genre classifier.
Sampling tag-only records improves evidence but does not by itself constitute a
new genre classifier. Artist-name-only SoundCloud mix tags are not reliable genre
labels; the Finnebassen seed needs profiling/enrichment before radio can fill.

## Validation

934 Python tests passed, 2 skipped; 2 Provider-page JavaScript tests passed.
Eight pre-existing router coroutine warnings remain unrelated to this change.
Regression tests cover generic tags, mixed album genres, R&B aliases, classical
false positives, artist arrays, genre-hint sampling and pending recovery.
